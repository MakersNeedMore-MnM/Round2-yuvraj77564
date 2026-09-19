#!/usr/bin/env python3
"""
Smart Helmet - Raspberry Pi 3 Hackathon Prototype
===================================================

Primary computer:
    Raspberry Pi 3

Sensors / peripherals:
    MPU6050 IMU over I2C
    NEO-6M GNSS over UART
    USB/CSI camera through OpenCV
    Physical emergency-cancel button
    Buzzer
    Optional GSM modem

Safety note:
    The accident "probability" shown by this project is intentionally called a
    RISK SCORE. It is a transparent heuristic from sensor evidence, not a
    statistically calibrated probability and not a medical/safety certification.

Risk weights:
    45% impact/deceleration
    25% angular velocity
    20% helmet tilt
    10% post-event inactivity

Synthetic reference profiles are included only for hackathon demonstration.
Do not deliberately crash, drop, or damage a helmet/bike to test this system.
Use demo mode or controlled bench tests.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import queue
import sqlite3
import statistics
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from smbus2 import SMBus
except ImportError:
    SMBus = None

try:
    import serial
except ImportError:
    serial = None

try:
    import cv2
except ImportError:
    cv2 = None

try:
    from gpiozero import Button, Buzzer
except ImportError:
    Button = None
    Buzzer = None

try:
    import pyttsx3
except ImportError:
    pyttsx3 = None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
EVIDENCE_DIR = DATA_DIR / "evidence"
DB_PATH = DATA_DIR / "smart_helmet.db"

I2C_BUS = int(os.getenv("HELMET_I2C_BUS", "1"))
MPU6050_ADDRESS = int(os.getenv("HELMET_MPU6050_ADDRESS", "0x68"), 0)

GPS_PORT = os.getenv("HELMET_GPS_PORT", "/dev/serial0")
GPS_BAUD = int(os.getenv("HELMET_GPS_BAUD", "9600"))

CANCEL_GPIO = int(os.getenv("HELMET_CANCEL_GPIO", "17"))
BUZZER_GPIO = int(os.getenv("HELMET_BUZZER_GPIO", "27"))

CAMERA_INDEX = int(os.getenv("HELMET_CAMERA_INDEX", "0"))

CITY_SPEED_LIMIT_KMH = float(os.getenv("HELMET_CITY_LIMIT", "50"))
HIGHWAY_SPEED_LIMIT_KMH = float(os.getenv("HELMET_HIGHWAY_LIMIT", "80"))
ACTIVE_SPEED_LIMIT_KMH = float(os.getenv("HELMET_SPEED_LIMIT", str(CITY_SPEED_LIMIT_KMH)))
OVERSPEED_MARGIN_KMH = float(os.getenv("HELMET_OVERSPEED_MARGIN", "5"))

LOOP_HZ = float(os.getenv("HELMET_LOOP_HZ", "10"))
CONFIRMATION_SECONDS = float(os.getenv("HELMET_CONFIRMATION_SECONDS", "15"))

# Motion thresholds used by the heuristic risk model.
IMPACT_G_THRESHOLD = float(os.getenv("HELMET_IMPACT_G", "2.5"))
DECELERATION_G_THRESHOLD = float(os.getenv("HELMET_DECELERATION_G", "1.8"))
ANGULAR_RATE_THRESHOLD_DPS = float(os.getenv("HELMET_ANGULAR_DPS", "220"))
TILT_THRESHOLD_DEG = float(os.getenv("HELMET_TILT_DEG", "55"))
INACTIVITY_SECONDS = float(os.getenv("HELMET_INACTIVITY_SECONDS", "5"))

RISK_ALERT_THRESHOLD = float(os.getenv("HELMET_RISK_ALERT", "70"))

# Drowsiness demo thresholds.
EYES_CLOSED_SECONDS = float(os.getenv("HELMET_EYES_CLOSED_SECONDS", "2.0"))
DROWSINESS_COOLDOWN_SECONDS = float(os.getenv("HELMET_DROWSINESS_COOLDOWN", "15"))

# GNSS data is considered fresh for this many seconds.
GPS_STALE_SECONDS = float(os.getenv("HELMET_GPS_STALE_SECONDS", "10"))

# Optional GSM configuration.
GSM_ENABLED = os.getenv("HELMET_GSM_ENABLED", "0") == "1"
GSM_PORT = os.getenv("HELMET_GSM_PORT", "/dev/ttyUSB0")
GSM_BAUD = int(os.getenv("HELMET_GSM_BAUD", "115200"))
EMERGENCY_NUMBER = os.getenv("HELMET_EMERGENCY_NUMBER", "")

# Demo mode never sends an emergency SMS.
DEMO_MODE = os.getenv("HELMET_DEMO_MODE", "0") == "1"

# GPIO can be disabled on a normal laptop for development.
SIMULATION_MODE = os.getenv("HELMET_SIMULATION_MODE", "0") == "1"


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def safe_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def run_shell(command: list[str]) -> str:
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT, timeout=5).strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class GPSFix:
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    speed_kmh: float = 0.0
    satellites: int = 0
    timestamp: str = ""
    valid: bool = False


@dataclass
class MotionSample:
    timestamp: str
    ax_g: float
    ay_g: float
    az_g: float
    acceleration_g: float
    linear_accel_g: float
    gyro_x_dps: float
    gyro_y_dps: float
    gyro_z_dps: float
    angular_velocity_dps: float
    tilt_deg: float


@dataclass
class RiskResult:
    risk_percent: float
    impact_component: float
    rotation_component: float
    tilt_component: float
    inactivity_component: float
    explanation: str


@dataclass
class SystemState:
    mode: str = "NORMAL"
    last_event: str = "boot"
    risk_percent: float = 0.0
    speed_kmh: float = 0.0
    gps_valid: bool = False
    drowsiness: bool = False
    camera_ok: bool = False
    imu_ok: bool = False
    gps_ok: bool = False
    gsm_ok: bool = False
    uptime_seconds: float = 0.0
    updated_at: str = ""


# ---------------------------------------------------------------------------
# SQLite storage
# ---------------------------------------------------------------------------

class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.RLock()
        self._create_tables()

    def _create_tables(self):
        with self.lock:
            self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                risk_percent REAL,
                latitude REAL,
                longitude REAL,
                speed_kmh REAL,
                details TEXT
            );

            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                ax_g REAL,
                ay_g REAL,
                az_g REAL,
                acceleration_g REAL,
                angular_velocity_dps REAL,
                tilt_deg REAL,
                speed_kmh REAL,
                risk_percent REAL
            );
            """)
            self.conn.commit()

    def event(self, event_type: str, severity: str = "INFO",
              risk_percent: Optional[float] = None,
              gps: Optional[GPSFix] = None,
              speed_kmh: float = 0.0,
              details: Optional[dict] = None) -> str:
        event_id = str(uuid.uuid4())
        with self.lock:
            self.conn.execute(
                """INSERT INTO events
                   (id, timestamp, event_type, severity, risk_percent,
                    latitude, longitude, speed_kmh, details)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id, utc_now(), event_type, severity, risk_percent,
                    gps.latitude if gps else None,
                    gps.longitude if gps else None,
                    speed_kmh,
                    json.dumps(details or {}, ensure_ascii=False),
                ),
            )
            self.conn.commit()
        return event_id

    def telemetry(self, motion: MotionSample, speed: float, risk: float):
        with self.lock:
            self.conn.execute(
                """INSERT INTO telemetry
                   (timestamp, ax_g, ay_g, az_g, acceleration_g,
                    angular_velocity_dps, tilt_deg, speed_kmh, risk_percent)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    motion.timestamp, motion.ax_g, motion.ay_g, motion.az_g,
                    motion.acceleration_g, motion.angular_velocity_dps,
                    motion.tilt_deg, speed, risk,
                ),
            )
            self.conn.commit()

    def export_csv(self, output: Path):
        output.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            cur = self.conn.execute(
                """SELECT timestamp, event_type, severity, risk_percent,
                          latitude, longitude, speed_kmh, details
                   FROM events ORDER BY timestamp"""
            )
            rows = cur.fetchall()
        with output.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp", "event_type", "severity", "risk_percent",
                "latitude", "longitude", "speed_kmh", "details"
            ])
            writer.writerows(rows)

    def close(self):
        with self.lock:
            self.conn.close()


# ---------------------------------------------------------------------------
# MPU6050
# ---------------------------------------------------------------------------

class MPU6050:
    PWR_MGMT_1 = 0x6B
    ACCEL_XOUT_H = 0x3B
    GYRO_XOUT_H = 0x43
    WHO_AM_I = 0x75

    def __init__(self, bus_num=1, address=0x68):
        self.address = address
        self.bus = None
        self.ok = False
        self.offset_ax = 0.0
        self.offset_ay = 0.0
        self.offset_az = 0.0
        self.offset_gx = 0.0
        self.offset_gy = 0.0
        self.offset_gz = 0.0

        if SIMULATION_MODE or SMBus is None:
            return

        try:
            self.bus = SMBus(bus_num)
            self.bus.write_byte_data(self.address, self.PWR_MGMT_1, 0x00)
            time.sleep(0.1)
            self.bus.read_byte_data(self.address, self.WHO_AM_I)
            self.ok = True
        except Exception as exc:
            print(f"[IMU] initialization failed: {exc}")
            self.ok = False

    @staticmethod
    def _signed(high: int, low: int) -> int:
        value = (high << 8) | low
        return value - 65536 if value & 0x8000 else value

    def _read_block(self, register: int, length: int):
        return self.bus.read_i2c_block_data(self.address, register, length)

    def calibrate(self, samples: int = 100):
        """Bench calibration. Keep the helmet stationary."""
        if not self.ok:
            return

        ax = ay = az = gx = gy = gz = 0.0
        valid = 0
        for _ in range(samples):
            try:
                data_a = self._read_block(self.ACCEL_XOUT_H, 6)
                data_g = self._read_block(self.GYRO_XOUT_H, 6)

                ax += self._signed(data_a[0], data_a[1]) / 16384.0
                ay += self._signed(data_a[2], data_a[3]) / 16384.0
                az += self._signed(data_a[4], data_a[5]) / 16384.0 - 1.0
                gx += self._signed(data_g[0], data_g[1]) / 131.0
                gy += self._signed(data_g[2], data_g[3]) / 131.0
                gz += self._signed(data_g[4], data_g[5]) / 131.0
                valid += 1
            except Exception:
                pass
            time.sleep(0.01)

        if valid:
            self.offset_ax = ax / valid
            self.offset_ay = ay / valid
            self.offset_az = az / valid
            self.offset_gx = gx / valid
            self.offset_gy = gy / valid
            self.offset_gz = gz / valid

    def read(self) -> MotionSample:
        if not self.ok:
            # Safe deterministic simulation for software testing.
            return MotionSample(
                timestamp=utc_now(),
                ax_g=0.0, ay_g=0.0, az_g=1.0,
                acceleration_g=1.0, linear_accel_g=0.0,
                gyro_x_dps=0.0, gyro_y_dps=0.0, gyro_z_dps=0.0,
                angular_velocity_dps=0.0, tilt_deg=0.0,
            )

        a = self._read_block(self.ACCEL_XOUT_H, 6)
        g = self._read_block(self.GYRO_XOUT_H, 6)

        ax = self._signed(a[0], a[1]) / 16384.0 - self.offset_ax
        ay = self._signed(a[2], a[3]) / 16384.0 - self.offset_ay
        az = self._signed(a[4], a[5]) / 16384.0 - self.offset_az

        gx = self._signed(g[0], g[1]) / 131.0 - self.offset_gx
        gy = self._signed(g[2], g[3]) / 131.0 - self.offset_gy
        gz = self._signed(g[4], g[5]) / 131.0 - self.offset_gz

        magnitude = math.sqrt(ax * ax + ay * ay + az * az)
        linear = abs(magnitude - 1.0)
        tilt = math.degrees(math.acos(clamp(abs(az) / max(magnitude, 1e-6))))

        return MotionSample(
            timestamp=utc_now(),
            ax_g=ax, ay_g=ay, az_g=az,
            acceleration_g=magnitude,
            linear_accel_g=linear,
            gyro_x_dps=gx, gyro_y_dps=gy, gyro_z_dps=gz,
            angular_velocity_dps=math.sqrt(gx * gx + gy * gy + gz * gz),
            tilt_deg=tilt,
        )

    def close(self):
        if self.bus:
            self.bus.close()


# ---------------------------------------------------------------------------
# GPS / NMEA
# ---------------------------------------------------------------------------

def nmea_coordinate(raw: str, hemisphere: str) -> Optional[float]:
    if not raw:
        return None
    try:
        dot = raw.index(".")
        degrees_len = dot - 2
        degrees = float(raw[:degrees_len])
        minutes = float(raw[degrees_len:])
        value = degrees + minutes / 60.0
        if hemisphere in ("S", "W"):
            value *= -1
        return value
    except (ValueError, IndexError):
        return None


class GPSReader:
    def __init__(self, port=GPS_PORT, baud=GPS_BAUD):
        self.port = port
        self.baud = baud
        self.serial = None
        self.fix = GPSFix()
        self.lock = threading.RLock()
        self.running = False
        self.thread = None

        if SIMULATION_MODE or serial is None:
            return

        try:
            self.serial = serial.Serial(self.port, self.baud, timeout=1)
        except Exception as exc:
            print(f"[GPS] initialization failed: {exc}")

    def start(self):
        if not self.serial:
            return
        self.running = True
        self.thread = threading.Thread(target=self._worker, daemon=True, name="gps-reader")
        self.thread.start()

    def _worker(self):
        while self.running:
            try:
                line = self.serial.readline().decode("ascii", errors="ignore").strip()
                if line.startswith("$GPRMC") or line.startswith("$GNRMC"):
                    self._parse_rmc(line)
                elif line.startswith("$GPGGA") or line.startswith("$GNGGA"):
                    self._parse_gga(line)
            except Exception:
                time.sleep(0.2)

    def _parse_rmc(self, line: str):
        p = line.split(",")
        if len(p) < 8:
            return
        valid = p[2] == "A"
        lat = nmea_coordinate(p[3], p[4])
        lon = nmea_coordinate(p[5], p[6])
        speed = safe_float(p[7]) * 1.852  # knots -> km/h

        with self.lock:
            self.fix.valid = valid
            self.fix.latitude = lat
            self.fix.longitude = lon
            self.fix.speed_kmh = speed
            self.fix.timestamp = utc_now()

    def _parse_gga(self, line: str):
        p = line.split(",")
        if len(p) >= 8:
            try:
                sats = int(p[7])
            except ValueError:
                sats = 0
            with self.lock:
                self.fix.satellites = sats

    def get_fix(self) -> GPSFix:
        with self.lock:
            return GPSFix(**asdict(self.fix))

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1)
        if self.serial:
            self.serial.close()


# ---------------------------------------------------------------------------
# Camera / drowsiness
# ---------------------------------------------------------------------------

class DrowsinessDetector:
    def __init__(self, camera_index=0):
        self.camera_index = camera_index
        self.camera = None
        self.face_cascade = None
        self.eye_cascade = None
        self.ok = False
        self.closed_since: Optional[float] = None
        self.last_alert = 0.0
        self.last_status = "FACE NOT DETECTED"

        if cv2 is None:
            return

        try:
            self.camera = cv2.VideoCapture(camera_index)
            self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

            haar = cv2.data.haarcascades
            self.face_cascade = cv2.CascadeClassifier(haar + "haarcascade_frontalface_default.xml")
            self.eye_cascade = cv2.CascadeClassifier(haar + "haarcascade_eye.xml")
            self.ok = bool(self.camera.isOpened())
        except Exception as exc:
            print(f"[CAMERA] initialization failed: {exc}")

    def read(self):
        if not self.ok:
            self.last_status = "CAMERA NOT AVAILABLE"
            return False, self.last_status

        ok, frame = self.camera.read()
        if not ok:
            self.last_status = "CAMERA READ ERROR"
            return False, self.last_status

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self.face_cascade.detectMultiScale(gray, 1.2, 5, minSize=(80, 80))

        if len(faces) == 0:
            self.closed_since = None
            self.last_status = "FACE NOT DETECTED"
            return False, self.last_status

        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
        roi = gray[y:y+h, x:x+w]
        eyes = self.eye_cascade.detectMultiScale(roi, 1.1, 5, minSize=(20, 20))

        now = time.monotonic()
        if len(eyes) == 0:
            if self.closed_since is None:
                self.closed_since = now
            elapsed = now - self.closed_since
            if elapsed >= EYES_CLOSED_SECONDS:
                self.last_status = f"EYES CLOSED ~{elapsed:.1f}s"
                return True, self.last_status
            self.last_status = f"EYES UNCERTAIN ~{elapsed:.1f}s"
            return False, self.last_status

        self.closed_since = None
        self.last_status = "ALERT / EYES DETECTED"
        return False, self.last_status

    def close(self):
        if self.camera:
            self.camera.release()


# ---------------------------------------------------------------------------
# Audio / GPIO
# ---------------------------------------------------------------------------

class AudioAlert:
    def __init__(self):
        self.engine = None
        if pyttsx3:
            try:
                self.engine = pyttsx3.init()
                self.engine.setProperty("rate", 165)
            except Exception:
                self.engine = None

    def say(self, text: str):
        print(f"[VOICE] {text}")
        if self.engine:
            try:
                self.engine.say(text)
                self.engine.runAndWait()
            except Exception:
                pass


class GPIOController:
    def __init__(self):
        self.button = None
        self.buzzer = None

        if SIMULATION_MODE or Button is None:
            return

        try:
            self.button = Button(CANCEL_GPIO, pull_up=True, bounce_time=0.05)
            self.buzzer = Buzzer(BUZZER_GPIO)
        except Exception as exc:
            print(f"[GPIO] initialization failed: {exc}")

    def pressed(self) -> bool:
        return bool(self.button and self.button.is_pressed)

    def beep(self, seconds=0.2):
        if self.buzzer:
            try:
                self.buzzer.on()
                time.sleep(seconds)
                self.buzzer.off()
            except Exception:
                pass

    def close(self):
        if self.buzzer:
            self.buzzer.off()
        if self.button:
            self.button.close()
        if self.buzzer:
            self.buzzer.close()


# ---------------------------------------------------------------------------
# GSM
# ---------------------------------------------------------------------------

class GSMNotifier:
    def __init__(self):
        self.serial = None
        self.ok = False

        if not GSM_ENABLED or DEMO_MODE or SIMULATION_MODE or serial is None:
            return
        if not EMERGENCY_NUMBER:
            print("[GSM] enabled but HELMET_EMERGENCY_NUMBER is empty.")
            return

        try:
            self.serial = serial.Serial(GSM_PORT, GSM_BAUD, timeout=2)
            self.ok = True
            self._command("AT")
        except Exception as exc:
            print(f"[GSM] initialization failed: {exc}")

    def _command(self, command: str, wait=0.5):
        if not self.serial:
            return ""
        self.serial.write((command + "\r").encode())
        time.sleep(wait)
        try:
            return self.serial.read_all().decode(errors="ignore")
        except Exception:
            return ""

    def send_sms(self, message: str) -> bool:
        if not self.ok:
            return False
        try:
            self._command("AT+CMGF=1")
            self._command(f'AT+CMGS="{EMERGENCY_NUMBER}"')
            self.serial.write(message.encode() + b"\x1A")
            time.sleep(3)
            response = self.serial.read_all().decode(errors="ignore")
            return "OK" in response or "+CMGS" in response
        except Exception as exc:
            print(f"[GSM] SMS failed: {exc}")
            return False

    def close(self):
        if self.serial:
            self.serial.close()


# ---------------------------------------------------------------------------
# Risk analysis
# ---------------------------------------------------------------------------

def risk_score(acceleration_g: float,
               angular_velocity_dps: float,
               tilt_deg: float,
               inactivity: bool) -> RiskResult:
    impact = clamp(
        max(acceleration_g, 0.0) / max(IMPACT_G_THRESHOLD, 0.01)
        if acceleration_g >= DECELERATION_G_THRESHOLD else
        max(acceleration_g - 1.0, 0.0) / max(DECELERATION_G_THRESHOLD, 0.01)
    )

    rotation = clamp(
        angular_velocity_dps / max(ANGULAR_RATE_THRESHOLD_DPS, 1.0)
    )

    tilt = clamp(
        max(tilt_deg, 0.0) / max(TILT_THRESHOLD_DEG, 1.0)
    )

    inactivity_component = 1.0 if inactivity else 0.0

    score = (
        0.45 * impact +
        0.25 * rotation +
        0.20 * tilt +
        0.10 * inactivity_component
    ) * 100.0

    score = round(clamp(score, 0.0, 100.0), 1)

    explanation_parts = []
    if impact >= 0.7:
        explanation_parts.append("strong acceleration/deceleration")
    if rotation >= 0.7:
        explanation_parts.append("high angular motion")
    if tilt >= 0.7:
        explanation_parts.append("large tilt")
    if inactivity:
        explanation_parts.append("post-event inactivity")

    explanation = ", ".join(explanation_parts) if explanation_parts else "low combined motion evidence"

    return RiskResult(
        risk_percent=score,
        impact_component=round(impact * 100, 1),
        rotation_component=round(rotation * 100, 1),
        tilt_component=round(tilt * 100, 1),
        inactivity_component=round(inactivity_component * 100, 1),
        explanation=explanation,
    )


REFERENCE = {
    "normal": dict(acceleration_g=1.05, angular_velocity_dps=8, tilt_deg=8, inactivity=False),
    "hard_brake": dict(acceleration_g=1.9, angular_velocity_dps=35, tilt_deg=12, inactivity=False),
    "fall_like": dict(acceleration_g=2.8, angular_velocity_dps=180, tilt_deg=60, inactivity=True),
    "impact_like": dict(acceleration_g=3.4, angular_velocity_dps=260, tilt_deg=70, inactivity=True),
}


def risk_analysis():
    rows = []
    for name, values in REFERENCE.items():
        result = risk_score(**values)
        rows.append((name, result))
    return rows


# ---------------------------------------------------------------------------
# Main safety state machine
# ---------------------------------------------------------------------------

class SafetyController:
    STATES = ("NORMAL", "WARNING", "CONFIRMATION", "EMERGENCY", "CANCELLED")

    def __init__(self, db: Database, audio: AudioAlert, gpio: GPIOController,
                 gsm: GSMNotifier):
        self.db = db
        self.audio = audio
        self.gpio = gpio
        self.gsm = gsm
        self.state = SystemState(updated_at=utc_now())
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.countdown_thread: Optional[threading.Thread] = None
        self.started = time.monotonic()
        self.last_drowsy_alert = 0.0
        self.last_overspeed = 0.0

    def update_state(self, **kwargs):
        with self.lock:
            for key, value in kwargs.items():
                if hasattr(self.state, key):
                    setattr(self.state, key, value)
            self.state.updated_at = utc_now()
            self.state.uptime_seconds = round(time.monotonic() - self.started, 1)

    def possible_accident(self, motion: MotionSample, gps: GPSFix,
                          inactivity: bool):
        result = risk_score(
            acceleration_g=motion.acceleration_g,
            angular_velocity_dps=motion.angular_velocity_dps,
            tilt_deg=motion.tilt_deg,
            inactivity=inactivity,
        )

        self.update_state(
            risk_percent=result.risk_percent,
            mode="WARNING" if result.risk_percent >= RISK_ALERT_THRESHOLD else "NORMAL",
            last_event="risk_analysis",
        )

        self.db.telemetry(motion, gps.speed_kmh, result.risk_percent)

        # Multi-signal gate: high risk plus at least one strong motion indicator.
        strong_motion = (
            motion.acceleration_g >= DECELERATION_G_THRESHOLD or
            motion.angular_velocity_dps >= ANGULAR_RATE_THRESHOLD_DPS or
            motion.tilt_deg >= TILT_THRESHOLD_DEG
        )

        if result.risk_percent >= RISK_ALERT_THRESHOLD and strong_motion:
            self.start_countdown(result, gps)

        return result

    def start_countdown(self, result: RiskResult, gps: GPSFix):
        with self.lock:
            if self.state.mode == "CONFIRMATION":
                return
            self.state.mode = "CONFIRMATION"
            self.state.last_event = "possible_accident"
            self.cancel_event.clear()

        event_id = self.db.event(
            "POSSIBLE_ACCIDENT",
            "HIGH",
            result.risk_percent,
            gps,
            gps.speed_kmh,
            details={
                "risk": asdict(result),
                "countdown_seconds": CONFIRMATION_SECONDS,
            },
        )

        self.audio.say(
            f"Possible accident detected. Risk score {result.risk_percent:.0f} percent. "
            f"Emergency alert in {int(CONFIRMATION_SECONDS)} seconds. Press the cancel button."
        )

        self.countdown_thread = threading.Thread(
            target=self._countdown,
            args=(result, gps, event_id),
            daemon=True,
            name="emergency-countdown",
        )
        self.countdown_thread.start()

    def _countdown(self, result: RiskResult, gps: GPSFix, event_id: str):
        end = time.monotonic() + CONFIRMATION_SECONDS

        while time.monotonic() < end:
            if self.cancel_event.is_set() or self.gpio.pressed():
                self.cancel_event.set()
                self.update_state(mode="CANCELLED", last_event="emergency_cancelled")
                self.db.event(
                    "EMERGENCY_CANCELLED",
                    "INFO",
                    result.risk_percent,
                    gps,
                    gps.speed_kmh,
                    details={"parent_event_id": event_id},
                )
                self.audio.say("Emergency alert cancelled.")
                self.gpio.beep(0.1)
                return

            remaining = max(0, int(math.ceil(end - time.monotonic())))
            if remaining in (10, 5, 3, 2, 1):
                self.audio.say(f"{remaining}")
            time.sleep(0.2)

        if self.cancel_event.is_set():
            return

        self.trigger_emergency(result, gps, event_id)

    def trigger_emergency(self, result: RiskResult, gps: GPSFix, parent_event_id: str):
        self.update_state(mode="EMERGENCY", last_event="emergency_triggered")

        event_id = self.db.event(
            "EMERGENCY_TRIGGERED",
            "CRITICAL",
            result.risk_percent,
            gps,
            gps.speed_kmh,
            details={
                "parent_event_id": parent_event_id,
                "risk_components": asdict(result),
            },
        )

        maps = ""
        if gps.valid and gps.latitude is not None and gps.longitude is not None:
            maps = f"https://maps.google.com/?q={gps.latitude},{gps.longitude}"

        message = (
            "SMART HELMET EMERGENCY ALERT\n"
            f"Event: {event_id}\n"
            f"Risk score: {result.risk_percent:.1f}%\n"
            f"Speed: {gps.speed_kmh:.1f} km/h\n"
            f"Location: {maps or 'GNSS location unavailable'}\n"
            f"UTC: {utc_now()}"
        )

        print("\n" + "=" * 70)
        print(message)
        print("=" * 70 + "\n")

        self.audio.say("Emergency alert triggered. Please seek assistance.")
        self.gpio.beep(0.5)

        if not DEMO_MODE:
            sent = self.gsm.send_sms(message)
            self.update_state(gsm_ok=sent)
            self.db.event(
                "GSM_SMS_RESULT",
                "INFO" if sent else "WARNING",
                result.risk_percent,
                gps,
                gps.speed_kmh,
                details={"sent": sent},
            )
        else:
            print("[DEMO] GSM disabled; no emergency SMS was sent.")

    def cancel(self):
        self.cancel_event.set()


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------

class SmartHelmet:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

        self.db = Database(DB_PATH)
        self.imu = MPU6050(I2C_BUS, MPU6050_ADDRESS)
        self.gps = GPSReader(GPS_PORT, GPS_BAUD)
        self.camera = DrowsinessDetector(CAMERA_INDEX)
        self.audio = AudioAlert()
        self.gpio = GPIOController()
        self.gsm = GSMNotifier()
        self.safety = SafetyController(self.db, self.audio, self.gpio, self.gsm)

        self.running = False

    def start(self):
        print("=" * 70)
        print("SMART HELMET | Raspberry Pi 3 Edge Safety Prototype")
        print("=" * 70)
        print(f"Simulation mode : {SIMULATION_MODE}")
        print(f"Demo mode       : {DEMO_MODE}")
        print(f"Risk threshold  : {RISK_ALERT_THRESHOLD:.0f}%")
        print(f"Emergency wait  : {CONFIRMATION_SECONDS:.0f}s")
        print()

        if self.imu.ok:
            print("[OK] MPU6050")
            self.imu.calibrate()
        else:
            print("[WARN] MPU6050 unavailable -> software simulation values")

        self.gps.start()
        print("[OK] GPS reader started" if self.gps.serial else "[WARN] GPS unavailable")

        print("[OK] Camera" if self.camera.ok else "[WARN] Camera unavailable")

        self.running = True
        last_print = 0.0
        last_drowsy = 0.0

        self.audio.say("Smart Helmet system started.")

        try:
            while self.running:
                loop_start = time.monotonic()

                motion = self.imu.read()
                gps = self.gps.get_fix()

                drowsy, drowsy_text = self.camera.read()
                now = time.monotonic()

                if drowsy and now - last_drowsy >= DROWSINESS_COOLDOWN_SECONDS:
                    last_drowsy = now
                    self.safety.update_state(
                        drowsiness=True,
                        camera_ok=self.camera.ok,
                        last_event="drowsiness_detected",
                    )
                    self.db.event(
                        "DROWSINESS_DETECTED",
                        "WARNING",
                        self.safety.state.risk_percent,
                        gps,
                        gps.speed_kmh,
                        details={"camera_status": drowsy_text},
                    )
                    self.audio.say("Warning. Signs of drowsiness detected. Please stay alert.")
                elif not drowsy:
                    self.safety.update_state(
                        drowsiness=False,
                        camera_ok=self.camera.ok,
                    )

                overspeed = gps.speed_kmh > ACTIVE_SPEED_LIMIT_KMH + OVERSPEED_MARGIN_KMH
                if overspeed and now - self.safety.last_overspeed >= 10:
                    self.safety.last_overspeed = now
                    self.db.event(
                        "OVERSPEED",
                        "WARNING",
                        self.safety.state.risk_percent,
                        gps,
                        gps.speed_kmh,
                        details={"limit_kmh": ACTIVE_SPEED_LIMIT_KMH},
                    )
                    self.audio.say(f"Speed warning. Current speed {gps.speed_kmh:.0f} kilometers per hour.")

                # A simple inactivity proxy: near-static helmet after significant motion.
                inactivity = (
                    motion.linear_accel_g < 0.08 and
                    motion.angular_velocity_dps < 15 and
                    motion.tilt_deg > 40
                )

                result = self.safety.possible_accident(motion, gps, inactivity)

                self.safety.update_state(
                    speed_kmh=gps.speed_kmh,
                    gps_valid=gps.valid,
                    gps_ok=self.gps.serial is not None,
                    imu_ok=self.imu.ok,
                    camera_ok=self.camera.ok,
                    risk_percent=result.risk_percent,
                )

                if now - last_print >= 1.0:
                    last_print = now
                    print(
                        f"[STATUS] state={self.safety.state.mode:<13} "
                        f"risk={result.risk_percent:5.1f}% "
                        f"speed={gps.speed_kmh:5.1f} km/h "
                        f"gps={'OK' if gps.valid else 'NO'} "
                        f"camera={drowsy_text}"
                    )

                elapsed = time.monotonic() - loop_start
                time.sleep(max(0.0, (1.0 / max(LOOP_HZ, 1.0)) - elapsed))

        except KeyboardInterrupt:
            print("\nStopping...")
        finally:
            self.stop()

    def stop(self):
        self.running = False
        self.gps.stop()
        self.camera.close()
        self.imu.close()
        self.gpio.close()
        self.gsm.close()
        self.db.close()
        print("Smart Helmet stopped.")

    def status(self):
        print(json.dumps(asdict(self.safety.state), indent=2))
        print(f"Database: {DB_PATH}")

    def export_data(self):
        output = DATA_DIR / f"events_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        self.db.export_csv(output)
        print(f"Exported: {output}")

    def demo_crash(self):
        print("\nSAFE SOFTWARE DEMO — no physical crash required.")
        print("-" * 60)
        gps = GPSFix(
            latitude=13.0475,
            longitude=80.2089,
            speed_kmh=46.0,
            satellites=8,
            timestamp=utc_now(),
            valid=True,
        )

        # Synthetic values deliberately used only to demonstrate the algorithm.
        motion = MotionSample(
            timestamp=utc_now(),
            ax_g=0.0, ay_g=0.0, az_g=1.0,
            acceleration_g=3.4,
            linear_accel_g=2.4,
            gyro_x_dps=170.0,
            gyro_y_dps=80.0,
            gyro_z_dps=160.0,
            angular_velocity_dps=260.0,
            tilt_deg=70.0,
        )

        result = risk_score(3.4, 260, 70, True)
        print(f"Risk score: {result.risk_percent}%")
        print(f"Components: impact={result.impact_component}% "
              f"rotation={result.rotation_component}% "
              f"tilt={result.tilt_component}% "
              f"inactivity={result.inactivity_component}%")
        print(f"Explanation: {result.explanation}")

        if DEMO_MODE:
            self.safety.start_countdown(result, gps)
            print("DEMO mode: cancel countdown with Ctrl+C or the physical cancel button.")
        else:
            print("Set HELMET_DEMO_MODE=1 before running demo-crash to guarantee no SMS is sent.")

    def demo_speed(self):
        speed = ACTIVE_SPEED_LIMIT_KMH + 15
        print(f"Demo speed: {speed:.1f} km/h")
        print(f"Configured limit: {ACTIVE_SPEED_LIMIT_KMH:.1f} km/h")
        print("Result: OVERSPEED WARNING")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def print_risk_analysis():
    print("\nSMART HELMET — RISK ANALYSIS")
    print("=" * 70)
    print("This is a heuristic risk score, not a calibrated probability.\n")

    for name, result in risk_analysis():
        print(
            f"{name:12} -> {result.risk_percent:5.1f}% | "
            f"impact={result.impact_component:5.1f} "
            f"rotation={result.rotation_component:5.1f} "
            f"tilt={result.tilt_component:5.1f} "
            f"inactivity={result.inactivity_component:5.1f}"
        )


def main():
    parser = argparse.ArgumentParser(description="Raspberry Pi Smart Helmet")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="Run the live system")
    sub.add_parser("status", help="Show current configuration/status")
    sub.add_parser("risk-analysis", help="Show synthetic risk-analysis examples")
    sub.add_parser("export-data", help="Export event database to CSV")
    sub.add_parser("demo-speed", help="Run safe overspeed software demonstration")
    sub.add_parser("demo-crash", help="Run safe synthetic accident-risk demonstration")

    args = parser.parse_args()

    if args.command == "risk-analysis":
        print_risk_analysis()
        return

    if args.command == "demo-speed":
        helmet = SmartHelmet()
        try:
            helmet.demo_speed()
        finally:
            helmet.stop()
        return

    if args.command == "demo-crash":
        helmet = SmartHelmet()
        try:
            helmet.demo_crash()
            if DEMO_MODE:
                time.sleep(CONFIRMATION_SECONDS + 2)
        except KeyboardInterrupt:
            helmet.safety.cancel()
        finally:
            helmet.stop()
        return

    helmet = SmartHelmet()

    if args.command == "status":
        helmet.status()
        helmet.stop()
    elif args.command == "export-data":
        helmet.export_data()
        helmet.stop()
    else:
        helmet.start()


if __name__ == "__main__":
    main()
