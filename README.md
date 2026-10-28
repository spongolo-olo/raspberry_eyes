# 🍓 Raspberry Eyes 👁️

[![Raspberry Pi 5](https://img.shields.io/badge/Platform-Raspberry%20Pi%205-c51a4a?logo=raspberry-pi)](https://www.raspberrypi.com)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776ab?logo=python)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Raspberry Eyes** is a dual-camera streaming, motorized focus control, and telemetry platform built specifically for the **Raspberry Pi 5** equipped with dual **Raspberry Pi Camera Module 3** sensors and the **Sense HAT**.

---

## 🌟 Features

- **Dual Camera Module 3 Streams**:
  - **Camera 0**: Standard IMX708 sensor pointed at the Sense HAT PCB macro view.
  - **Camera 1**: Wide NoIR IMX708 sensor with 180° software rotation.
- **Full Motorized Focus Control**:
  - Continuous full-range autofocus (`AfRange = Full`).
  - One-shot autofocus scan trigger.
  - Sub-millimeter manual focus slider (`0.0 D` infinity to `15.0 D` extreme macro).
  - Quick step micro-tuning (`-1.0D`, `-0.2D`, `+0.2D`, `+1.0D`).
- **Sense HAT Integration**:
  - Real-time environmental telemetry (Temperature in °C/°F, Humidity, Barometric Pressure).
  - Orientation metrics (Pitch, Roll, Yaw).
  - Interactive 8×8 RGB LED matrix controls (scrolling text, rainbow pattern, clear).
- **Dual Display Interface**:
  - **Web Dashboard**: Modern dark-themed dashboard at `http://<pi-ip>:8080` with persistent, low-latency MJPEG video feeds.
  - **Native Pi Monitor Window**: PyQt5 GUI window running directly on `DISPLAY=:0`.
- **Agent Skill Included**:
  - Pre-packaged Antigravity skill (`.agents/skills/pi-camera/`) allowing AI coding assistants and autonomous subagents to safely view streams and capture snapshots via CLI or HTTP API without conflicting over hardware locks.

---

## 🛠️ Architecture

```
                                  ┌───────────────────────────────┐
                                  │      Hardware Layer           │
                                  │  • Camera 0 (CAM/DISP 1)      │
                                  │  • Camera 1 (CAM/DISP 0)      │
                                  │  • Sense HAT (I2C-1, Framebuf)│
                                  └──────────────┬────────────────┘
                                                 │
                                  ┌──────────────▼────────────────┐
                                  │   raspberry_eyes daemon       │
                                  │       (server.py)             │
                                  │  - Picamera2 thread loops     │
                                  │  - Sense HAT telemetry worker │
                                  │  - Threading HTTP Server      │
                                  └───────┬──────────────┬────────┘
                                          │              │
                    ┌─────────────────────▼──┐        ┌──▼────────────────────┐
                    │     Web Dashboard      │        │  Native Desktop GUI   │
                    │   http://0.0.0.0:8080  │        │   PyQt5 on DISPLAY=:0 │
                    │ - Persistent MJPEG     │        │ - Dual camera viewer  │
                    │ - Focus & LED controls │        │ - Sense HAT banner    │
                    │ - Sensor telemetry bar │        └───────────────────────┘
                    └────────────────────────┘
                                          │
                    ┌─────────────────────▼──┐
                    │    Agent / CLI Tools   │
                    │ - capture_snapshot.py  │
                    │ - sense_hat_control.py │
                    └────────────────────────┘
```

---

## 🚀 Quick Start

### 1. Prerequisites (Raspberry Pi OS Bookworm or later)
```bash
sudo apt update
sudo apt install -y python3-picamera2 python3-opencv python3-pyqt5 python3-sense-hat git curl
```

### 2. Clone the Repository
```bash
git clone https://github.com/spongolo-olo/raspberry_eyes.git
cd raspberry_eyes
```

### 3. Run the Server
```bash
./bin/raspberry-eyes
```
Or as a python module:
```bash
python3 -m raspberry_eyes
```

Open **`http://localhost:8080`** (or `http://<your-pi-ip>:8080`) in any browser.

---

## 🤖 CLI & Agent Tools

The project includes CLI tools in `./bin/` and `.agents/skills/pi-camera/scripts/`:

### Capture Live Snapshot
```bash
# Capture Camera 0
./bin/capture-snapshot --cam 0 -o /tmp/sense_hat.jpg

# Capture Camera 1 (wide angle room)
./bin/capture-snapshot --cam 1 -o /tmp/room.jpg

# Adjust focus before capturing
./bin/capture-snapshot --cam 0 --focus manual --lens-pos 8.5 -o /tmp/macro.jpg

# Query camera and Sense HAT telemetry JSON
./bin/capture-snapshot --telemetry
```

### Sense HAT Tools
```bash
# Print sensor telemetry
./bin/sense-hat-tool --status

# Scroll custom text across 8x8 LEDs (watch live on Camera 0)
./bin/sense-hat-tool --message "Raspberry Eyes" --color cyan

# Display rainbow pattern
./bin/sense-hat-tool --rainbow

# Turn off LEDs
./bin/sense-hat-tool --clear
```

---

## 🌐 HTTP API Reference

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/frame/{0,1}` | `GET` | Fetches a single JPEG frame from Camera 0 or 1. |
| `/stream/{0,1}` | `GET` | Persistent `multipart/x-mixed-replace` MJPEG stream. |
| `/api/state` | `GET` | Returns full JSON telemetry (FPS, AF state, lens pos, Sense HAT sensors). |
| `/api/mode?mode={both,cam0,cam1,off}` | `POST` | Switches active camera mode. |
| `/api/focus?cam={0,1}&mode={continuous,trigger,manual}&pos={D}` | `POST` | Adjusts focus mode or manual dioptres. |
| `/api/flip?cam={0,1}` | `POST` | Toggles 180° image rotation. |
| `/api/sense/message?text={msg}&color={col}` | `POST` | Scrolls text across the Sense HAT 8×8 LEDs. |
| `/api/sense/rainbow` | `POST` | Displays rainbow pattern on LEDs. |
| `/api/sense/clear` | `POST` | Clears all LEDs. |

---

## ⚙️ Optional Systemd Autostart Service

To have Raspberry Eyes start automatically on system boot:

```bash
sudo cp systemd/raspberry-eyes.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable raspberry-eyes
sudo systemctl start raspberry-eyes
```

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
