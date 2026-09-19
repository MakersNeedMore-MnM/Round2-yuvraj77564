# Smart Helmet — Raspberry Pi 3 Edge-AI Safety Prototype

A hackathon-ready Raspberry Pi smart helmet prototype that combines motion sensing, GNSS, camera-based drowsiness monitoring, speed monitoring, voice alerts, emergency confirmation, optional GSM communication, and SQLite event logging.

## 1. What this project does

The Raspberry Pi is the primary computer.

### Core functions

- MPU6050 motion sensing
- Multi-signal accident/fall-risk analysis
- NEO-6M GNSS location and speed
- Overspeed warning
- Camera-based drowsiness indication
- Voice alerts
- Physical emergency-cancel button
- 15-second emergency confirmation countdown
- Optional GSM SMS
- SQLite incident/event logging
- CSV data export
- Safe software-only demonstration modes
- Centralized risk scoring
- Configuration through environment variables

## 2. Important risk-score clarification

The project displays a **0–100% heuristic risk score**.

It is NOT a statistically validated accident probability.

The current transparent model uses:

| Signal | Weight |
|---|---:|
| Impact/deceleration | 45% |
| Angular velocity | 25% |
| Helmet tilt | 20% |
| Post-event inactivity | 10% |

A real probability model would require representative labelled accident/non-accident data, independent train/validation/test datasets, calibration, and evaluation of false positives and false negatives.

The included reference profiles are synthetic demonstration values. They are not a real training dataset and must not be presented as measured accuracy.

## 3. Project structure

```text
smart-helmet/
├── smart_helmet.py
├── README.md
├── WIRING.md
├── ARCHITECTURE.md
├── requirements.txt
├── .gitignore
├── docs/
│   └── HACKATHON_DEMO.md
└── data/
    ├── smart_helmet.db
    └── events_*.csv
```

The `data/` directory is generated automatically and should not be committed.

## 4. Hardware

Recommended prototype:

- Raspberry Pi 3
- MPU6050
- NEO-6M GPS/GNSS
- USB or CSI camera
- Push button
- Active buzzer
- Optional GSM module/modem
- Appropriate regulated power supply
- Helmet-mounted enclosure and wiring

## 5. Raspberry Pi setup

Use Raspberry Pi OS and enable I2C/UART as appropriate for your hardware.

Install system packages:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip python3-opencv espeak i2c-tools
```

Create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install Python packages:

```bash
pip install -r requirements.txt
```

Enable I2C:

```bash
sudo raspi-config
```

Then:

```text
Interface Options
    I2C
        Enable
```

Check the MPU6050:

```bash
i2cdetect -y 1
```

You should normally see `68` for a standard MPU6050 address.

## 6. Wiring

See [WIRING.md](WIRING.md).

Quick reference:

```text
MPU6050 SDA -> GPIO2 / physical pin 3
MPU6050 SCL -> GPIO3 / physical pin 5
MPU6050 GND -> GND
MPU6050 VCC -> 3.3V-compatible supply

GPS TX -> Raspberry Pi GPIO15 / physical pin 10
GPS GND -> GND

Cancel button -> GPIO17 / physical pin 11
Button other side -> GND

Buzzer signal -> GPIO27 / physical pin 13
Buzzer GND -> GND
```

Verify the exact voltage and pinout of every module before connecting it.

**Never feed 5 V into Raspberry Pi GPIO pins.**

## 7. Run the project

### Check status

```bash
python3 smart_helmet.py status
```

### Risk analysis

```bash
python3 smart_helmet.py risk-analysis
```

### Safe speed demo

```bash
python3 smart_helmet.py demo-speed
```

### Safe accident-risk demo

First set demo mode:

```bash
export HELMET_DEMO_MODE=1
```

Then:

```bash
python3 smart_helmet.py demo-crash
```

This uses synthetic sensor values. It does not require a real crash.

### Live system

```bash
python3 smart_helmet.py run
```

Stop with:

```text
Ctrl+C
```

## 8. Configuration

You can configure the prototype without editing the main code.

Example:

```bash
export HELMET_SPEED_LIMIT=50
export HELMET_CONFIRMATION_SECONDS=15
export HELMET_RISK_ALERT=70
export HELMET_GPS_PORT=/dev/serial0
export HELMET_CANCEL_GPIO=17
export HELMET_BUZZER_GPIO=27
```

For simulation on a normal computer:

```bash
export HELMET_SIMULATION_MODE=1
python3 smart_helmet.py risk-analysis
```

## 9. Emergency workflow

```text
MPU6050 + GNSS + Camera
          |
          v
   Sensor Processing
          |
          v
   Risk Score 0–100
          |
          v
  Multi-signal accident gate
          |
          v
  15-second confirmation
          |
      +---+---+
      |       |
    Cancel   Timeout
      |       |
      v       v
 CANCELLED  EMERGENCY
              |
       Optional GSM SMS
              |
       SQLite event log
```

## 10. Why the confirmation button matters

A sensor can generate false positives from hard braking, potholes, mounting movement, or unusual rider motion.

The confirmation window gives the rider a chance to cancel the alert before communication is attempted.

## 11. Drowsiness logic

The prototype uses OpenCV Haar cascades.

The system distinguishes:

- `FACE NOT DETECTED`
- `EYES UNCERTAIN`
- `EYES CLOSED`
- `ALERT / EYES DETECTED`

A missing face is not automatically treated as drowsiness.

For a production system, use a validated temporal eye-state model and evaluate it on representative data.

## 12. Data logging

The SQLite database is created at:

```text
data/smart_helmet.db
```

Export events:

```bash
python3 smart_helmet.py export-data
```

This produces a CSV file in `data/`.

Logged information can include:

- UTC timestamp
- event type
- severity
- risk score
- latitude
- longitude
- speed
- risk components
- event details

## 13. Hackathon demo

Recommended sequence:

1. Show the physical helmet.
2. Show Raspberry Pi 3 as the primary computer.
3. Explain MPU6050 + GPS + camera.
4. Run:
   ```bash
   python3 smart_helmet.py risk-analysis
   ```
5. Explain that the percentage is a heuristic risk score.
6. Run:
   ```bash
   export HELMET_DEMO_MODE=1
   python3 smart_helmet.py demo-crash
   ```
7. Demonstrate the emergency countdown.
8. Press the physical cancel button.
9. Show the SQLite event log.
10. Run `export-data`.
11. Explain how real labelled data would be used in a future ML version.

## 14. Limitations

This is a hackathon prototype.

It is not:

- a certified motorcycle safety system
- a medical device
- a statistically validated crash predictor
- a replacement for emergency services
- a guarantee that an accident will be detected

Do not use it as the only safety mechanism on public roads.

## 15. Future development

- Temporal ML accident classifier
- Proper labelled IMU dataset
- Probability calibration
- MediaPipe/TFLite drowsiness model optimized for Pi
- Camera evidence circular buffer
- Local Flask dashboard
- WebSocket live telemetry
- Better GNSS filtering
- IMU sensor fusion
- CAN/vehicle integration where legally and technically appropriate
- Robust GSM modem state handling
- systemd service
- Automated tests and CI
- Hardware enclosure and power-management design
- Independent field validation

## 16. GitHub description

**Smart Helmet — Raspberry Pi 3 Edge-AI Safety Prototype**

A Raspberry Pi 3 smart-helmet prototype combining MPU6050 motion sensing, GNSS location/speed monitoring, camera-based drowsiness detection, heuristic accident-risk scoring, voice alerts, emergency confirmation, optional GSM communication, SQLite logging, and safe hackathon demo modes.

## 17. Suggested GitHub topics

```text
raspberry-pi
raspberry-pi-3
python
opencv
computer-vision
edge-ai
iot
smart-helmet
mpu6050
gps
gnss
drowsiness-detection
accident-detection
embedded-systems
hackathon
safety-system
```
