---
name: pi-camera
description: >-
  Access live camera feeds (Camera 0 Standard / Sense HAT and Camera 1 Wide NoIR), capture snapshots,
  adjust autofocus and manual lens positions, and read or control the Sense HAT (temperature, humidity,
  pressure, orientation, and 8x8 LED matrix) on the Raspberry Pi 5.
---

# Raspberry Pi 5 Dual Camera & Sense HAT Skill

This skill provides procedures, helper scripts, and API endpoints for agents to view live camera feeds, capture snapshots, control focus, and interact with the attached Sense HAT.

> [!IMPORTANT]
> **Hardware Exclusivity Rule**: The physical CSI camera devices (`/dev/video*`, `libcamera`) are already managed by the background streaming service ([server.py](../../../raspberry_eyes/server.py)). Attempting to instantiate `Picamera2()` or run `rpicam-still` directly from a separate process will fail with `Device or resource busy`.
> **Always use the HTTP endpoints or the helper scripts below to capture frames or adjust settings.**

---

## Hardware Reference

| Device | Model / Sensor | Port / Bus | Default View / Direction | Capabilities |
| :--- | :--- | :--- | :--- | :--- |
| **Camera 0** | `imx708` (Standard Module 3) | `CAM/DISP 1` (`i2c-10`) | Points straight down at the **Sense HAT** PCB on the desk | Autofocus, Manual Focus (0.0–15.0D), 120 FPS capable |
| **Camera 1** | `imx708_wide_noir` (Wide NoIR) | `CAM/DISP 0` (`i2c-11`) | Points across the room (bicycles, indoor plants) | Wide-angle, NoIR (IR sensitive), Autofocus |
| **Sense HAT** | Raspberry Pi Sense HAT v1.0 | `i2c-1` + `/dev/fb1` | Sits on breadboard under Camera 0 | 8×8 RGB LED Matrix, Temp, Humidity, Pressure, IMU |

---

## Quick Start for Agents

### 1. Capture a Live Snapshot
Use the bundled helper script [capture_snapshot.py](./scripts/capture_snapshot.py):

```bash
# Capture Camera 0 (pointed at Sense HAT)
python3 .agents/skills/pi-camera/scripts/capture_snapshot.py --cam 0 -o /tmp/cam0.jpg

# Capture Camera 1 (wide angle room view)
python3 .agents/skills/pi-camera/scripts/capture_snapshot.py --cam 1 -o /tmp/cam1.jpg

# Capture with focus adjustment before taking the shot
python3 .agents/skills/pi-camera/scripts/capture_snapshot.py --cam 0 --focus manual --lens-pos 8.5 -o /tmp/cam0_sharp.jpg
```

Then read the image in your conversation context:
`read_file(Path="/tmp/cam0.jpg")`

Or directly via `curl`:
```bash
curl -s http://localhost:8080/frame/0 -o /tmp/cam0.jpg
curl -s http://localhost:8080/frame/1 -o /tmp/cam1.jpg
```

---

### 2. Focus Controls

Both cameras feature motorized voice-coil autofocus (`dw9807` VCM).

Lens positions are measured in **dioptres** ($D = 1 / \text{distance in meters}$):
- `0.0 D` = Infinity
- `1.0 D` = 1 meter
- `5.0 D` = 20 cm
- `8.5 D` = ~11.8 cm (**optimal focus for Sense HAT under Camera 0**)
- `15.0 D` = ~6.7 cm (extreme macro)

#### Set Focus via CLI
```bash
# Set Continuous Autofocus (automatically tracks focus across full range)
python3 .agents/skills/pi-camera/scripts/capture_snapshot.py --cam 0 --focus continuous

# Trigger single one-shot autofocus scan
python3 .agents/skills/pi-camera/scripts/capture_snapshot.py --cam 0 --focus trigger

# Set exact manual dioptres
python3 .agents/skills/pi-camera/scripts/capture_snapshot.py --cam 0 --focus manual --lens-pos 8.5
```

#### Set Focus via HTTP API
```bash
# Continuous AF
curl -X POST "http://localhost:8080/api/focus?cam=0&mode=continuous"

# Single-shot AF
curl -X POST "http://localhost:8080/api/focus?cam=0&mode=trigger"

# Manual Lens Position
curl -X POST "http://localhost:8080/api/focus?cam=0&mode=manual&pos=8.5"
```

---

### 3. Sense HAT Sensors & 8×8 LEDs

Use the bundled helper script [sense_hat_control.py](./scripts/sense_hat_control.py):

```bash
# Read current environmental sensors (Temperature, Humidity, Pressure, Orientation)
python3 .agents/skills/pi-camera/scripts/sense_hat_control.py --status

# Scroll text across the Sense HAT LEDs (watch it live in Camera 0!)
python3 .agents/skills/pi-camera/scripts/sense_hat_control.py --message "Agent Online" --color cyan

# Display test rainbow pattern
python3 .agents/skills/pi-camera/scripts/sense_hat_control.py --rainbow

# Turn off LEDs
python3 .agents/skills/pi-camera/scripts/sense_hat_control.py --clear
```

#### Read Telemetry via HTTP API
```bash
curl -s http://localhost:8080/api/state
```

---

## Stream Server Health & Recovery

If `http://localhost:8080` is ever unreachable:
1. Check if the server is running:
   ```bash
   ps aux | grep -i server.py | grep -v grep
   ```
2. Start the daemon:
   ```bash
   ./bin/raspberry-eyes &
   ```
