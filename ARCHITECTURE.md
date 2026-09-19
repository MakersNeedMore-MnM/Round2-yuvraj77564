# Smart Helmet Architecture

## 1. High-level architecture

```text
                       SMART HELMET
                            |
             +--------------+--------------+
             |              |              |
          MPU6050          GNSS          Camera
             |              |              |
             +--------------+--------------+
                            |
                            v
                    Raspberry Pi 3
                            |
        +-------------------+-------------------+
        |                   |                   |
   Sensor processing    Risk engine       Vision engine
        |                   |                   |
        +-------------------+-------------------+
                            |
                            v
                    Safety state machine
                            |
                 +----------+----------+
                 |                     |
            Normal/Warning       Confirmation
                                       |
                              +--------+--------+
                              |                 |
                           Cancel            Timeout
                              |                 |
                          CANCELLED          EMERGENCY
                                                |
                                  +-------------+-------------+
                                  |             |             |
                                Voice         GSM          SQLite
                                                               |
                                                              CSV
```

## 2. Processing pipeline

### Motion

```text
MPU6050
  -> raw accelerometer/gyroscope
  -> calibration offsets
  -> magnitude
  -> linear acceleration
  -> angular velocity
  -> tilt
```

### GNSS

```text
NEO-6M
  -> NMEA
  -> RMC/GGA parsing
  -> latitude/longitude
  -> speed
  -> satellite count
```

### Vision

```text
Camera
  -> OpenCV frame
  -> grayscale
  -> face detection
  -> eye detection
  -> temporal closed-eye timer
  -> drowsiness warning
```

## 3. Risk engine

The current score is intentionally transparent:

```text
Risk =
    0.45 * Impact component
  + 0.25 * Rotation component
  + 0.20 * Tilt component
  + 0.10 * Inactivity component
```

Each component is normalized to 0–1 and the final value is converted to 0–100.

This makes the model easy to explain to a hackathon jury.

## 4. Safety state machine

```text
NORMAL
  |
  | high combined motion evidence
  v
WARNING
  |
  | risk >= threshold
  v
CONFIRMATION
  |
  +---- cancel ----> CANCELLED
  |
  | timeout
  v
EMERGENCY
```

The confirmation stage is important because sensor-based accident detection can produce false positives.

## 5. Software modules

The project intentionally uses a single main Python file for easy Raspberry Pi 3 deployment:

```text
smart_helmet.py
```

Logical components inside the file:

- configuration
- utility functions
- data models
- SQLite database
- MPU6050 driver
- GPS parser
- OpenCV drowsiness detector
- GPIO controller
- voice alerts
- GSM notifier
- risk engine
- safety controller
- live application
- CLI

This keeps the competition demo simple while preserving clear software boundaries.

## 6. Data storage

SQLite tables:

```text
events
telemetry
```

Events can contain:

```text
event ID
timestamp
event type
severity
risk score
latitude
longitude
speed
details
```

## 7. Failure handling

The prototype is designed to degrade rather than silently claim that hardware is healthy.

Examples:

```text
MPU6050 unavailable
    -> warning + simulation-safe values

GPS unavailable
    -> location marked invalid

Camera unavailable
    -> drowsiness unavailable

GSM unavailable
    -> event remains logged locally
```

## 8. Why Raspberry Pi 3 is the primary computer

The Raspberry Pi performs:

- sensor processing
- GPS parsing
- camera processing
- risk calculation
- state management
- voice output
- data logging
- optional GSM communication

No ESP32 is required as the primary processor.

An ESP32 could be added later as a peripheral controller, but it is intentionally not part of the required architecture.

## 9. Future ML architecture

A future validated ML version could use:

```text
Raw IMU + GPS + optional camera features
                 |
                 v
          Feature extraction
                 |
                 v
       Trained classifier/model
                 |
                 v
        Calibrated probability
                 |
                 v
        Safety decision layer
```

Training and validation must use real labelled datasets. Synthetic reference profiles should not be used to claim accuracy.
