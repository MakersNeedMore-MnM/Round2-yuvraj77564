# Smart Helmet — Raspberry Pi 3 Wiring

## Important electrical warning

Raspberry Pi GPIO pins are 3.3 V logic.

Do not connect a 5 V signal directly to a Raspberry Pi GPIO.

Confirm the voltage requirements of your exact GPS, MPU6050 breakout, buzzer, camera, and GSM module before wiring.

---

## 1. MPU6050

| MPU6050 | Raspberry Pi 3 |
|---|---|
| VCC | 3.3 V |
| GND | GND |
| SDA | GPIO2 / physical pin 3 |
| SCL | GPIO3 / physical pin 5 |

Default I2C address is usually:

```text
0x68
```

Check:

```bash
i2cdetect -y 1
```

Expected example:

```text
68
```

Some modules may use `0x69` depending on AD0 configuration.

---

## 2. NEO-6M GPS

Typical UART connection:

| GPS | Raspberry Pi 3 |
|---|---|
| TX | GPIO15 / physical pin 10 |
| RX | GPIO14 / physical pin 8 |
| GND | GND |
| VCC | Module-specific supply |

For the current code, the important input is GPS TX -> Raspberry Pi RX.

The code defaults to:

```text
/dev/serial0
9600 baud
```

If your serial device is different:

```bash
export HELMET_GPS_PORT=/dev/ttyS0
```

Check the actual device on your Raspberry Pi before running.

---

## 3. Emergency cancel button

The code uses GPIO17.

```text
GPIO17 / physical pin 11 ---- button ---- GND
```

The program uses the GPIO pull-up configuration.

Pressing the button during the confirmation countdown cancels the emergency action.

---

## 4. Buzzer

The code uses GPIO27.

```text
GPIO27 / physical pin 13 -> buzzer input
GND ----------------------> buzzer GND
```

If using a bare/high-current buzzer, use a suitable transistor/MOSFET driver and external supply rather than driving excessive current directly from GPIO.

---

## 5. Camera

USB camera:

```text
USB camera -> Raspberry Pi USB
```

CSI camera:

```text
Camera ribbon -> Raspberry Pi CSI camera connector
```

The program opens:

```text
camera index = 0
```

Change it with:

```bash
export HELMET_CAMERA_INDEX=1
```

---

## 6. Optional GSM

GSM modules can have high current requirements and voltage constraints.

Do not power a cellular modem directly from a GPIO pin.

A typical architecture is:

```text
GSM modem
   |
UART / USB
   |
Raspberry Pi
   |
SIM/network
```

Configure:

```bash
export HELMET_GSM_ENABLED=1
export HELMET_GSM_PORT=/dev/ttyUSB0
export HELMET_GSM_BAUD=115200
export HELMET_EMERGENCY_NUMBER="+91XXXXXXXXXX"
```

For hackathon demonstrations, keep:

```bash
export HELMET_DEMO_MODE=1
```

when you do not want a real SMS to be sent.

---

## 7. Recommended physical layout

```text
             FRONT
        +---------------+
        |    CAMERA     |
        |               |
        |    HELMET     |
        |   Raspberry   |
        |      Pi       |
        |               |
        | MPU6050  GPS  |
        |               |
        | Button Buzzer |
        +---------------+
              BACK
```

Mount the IMU rigidly relative to the helmet.

Avoid loose mounting because movement of the sensor relative to the helmet can distort motion measurements.

---

## 8. Power

Use a properly regulated power solution appropriate for your Raspberry Pi 3 and peripherals.

Do not improvise lithium battery packs or protection circuits for a competition prototype.

For bench demonstration, use an appropriate certified USB power supply.

---

## 9. First hardware test

Do not perform a real crash test.

Use this order:

1. Boot Raspberry Pi.
2. Check I2C.
3. Check GPS.
4. Check camera.
5. Check button.
6. Check buzzer.
7. Run `status`.
8. Run `risk-analysis`.
9. Run `demo-crash`.
10. Only then test individual sensors under safe stationary conditions.

