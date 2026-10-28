#!/usr/bin/env python3
"""
Raspberry Eyes — Dual Camera Streamer, Focus & Sense HAT Controller

Features:
- Dual Camera streaming (Camera 0 Standard Module 3 + Camera 1 Wide NoIR)
- Motorized Voice-Coil Autofocus & Manual Dioptre Controls (0.0D - 15.0D)
- Sense HAT Environmental Telemetry (Temperature, Humidity, Pressure, Orientation)
- Interactive 8x8 RGB LED Matrix Control (Scrolling text, rainbow, clear)
- Web Dashboard on http://0.0.0.0:8080 (Persistent low-latency MJPEG streams)
- Native GUI Monitor window on DISPLAY=:0 (PyQt5)
"""

import json
import os
import sys
import time
import threading
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
from libcamera import controls
from picamera2 import Picamera2

os.environ.setdefault("DISPLAY", ":0")

FALLBACK_IMG_CAM0 = os.path.expanduser("~/.gemini/antigravity-cli/brain/50cc17d8-2ac6-498f-bf65-7ca24ccf02e9/scratch/cam0.jpg")

AF_STATE_NAMES = {
    0: "Idle",
    1: "Scanning",
    2: "Focused",
    3: "Failed",
}


class SenseHatWorker:
    def __init__(self):
        self.lock = threading.Lock()
        self.hat = None
        self.data = {
            "available": False,
            "temp_c": 0.0,
            "temp_f": 0.0,
            "humidity": 0.0,
            "pressure": 0.0,
            "pitch": 0.0,
            "roll": 0.0,
            "yaw": 0.0,
        }
        self.running = True
        self._init_hat()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _init_hat(self):
        try:
            from sense_hat import SenseHat
            try:
                self.hat = SenseHat()
            except Exception:
                import unittest.mock
                with unittest.mock.patch("sense_hat.sense_hat.SenseStick"):
                    self.hat = SenseHat()
            self.data["available"] = True
            print("[SenseHat] Initialized successfully!", flush=True)
        except Exception as e:
            print(f"[SenseHat] Init error: {e}", flush=True)
            self.data["available"] = False

    def _loop(self):
        while self.running:
            if self.hat:
                try:
                    t = self.hat.get_temperature()
                    h = self.hat.get_humidity()
                    p = self.hat.get_pressure()
                    ori = self.hat.get_orientation()
                    with self.lock:
                        self.data["available"] = True
                        self.data["temp_c"] = round(t, 1)
                        self.data["temp_f"] = round(t * 9.0 / 5.0 + 32.0, 1)
                        self.data["humidity"] = round(h, 1)
                        self.data["pressure"] = round(p, 1)
                        self.data["pitch"] = round(ori.get("pitch", 0.0), 1)
                        self.data["roll"] = round(ori.get("roll", 0.0), 1)
                        self.data["yaw"] = round(ori.get("yaw", 0.0), 1)
                except Exception as e:
                    pass
            time.sleep(1.0)

    def get_data(self) -> dict:
        with self.lock:
            return dict(self.data)

    def show_message(self, text: str, color_name: str = "cyan"):
        if not self.hat:
            return
        colors = {
            "cyan": [0, 255, 255],
            "green": [0, 255, 0],
            "red": [255, 0, 0],
            "blue": [0, 120, 255],
            "yellow": [255, 220, 0],
            "white": [255, 255, 255],
        }
        c = colors.get(color_name.lower(), [0, 255, 255])
        threading.Thread(target=lambda: self.hat.show_message(text, scroll_speed=0.07, text_colour=c), daemon=True).start()

    def clear(self):
        if self.hat:
            try:
                self.hat.clear()
            except Exception:
                pass

    def show_rainbow(self):
        if not self.hat:
            return
        R = [255, 0, 0]
        O = [255, 127, 0]
        Y = [255, 255, 0]
        G = [0, 255, 0]
        C = [0, 255, 255]
        B = [0, 0, 255]
        I = [75, 0, 130]
        V = [148, 0, 211]
        rainbow = [
            R, R, R, R, R, R, R, R,
            O, O, O, O, O, O, O, O,
            Y, Y, Y, Y, Y, Y, Y, Y,
            G, G, G, G, G, G, G, G,
            C, C, C, C, C, C, C, C,
            B, B, B, B, B, B, B, B,
            I, I, I, I, I, I, I, I,
            V, V, V, V, V, V, V, V,
        ]
        try:
            self.hat.set_pixels(rainbow)
        except Exception:
            pass


class ControllableCamera:
    def __init__(self, cam_idx: int, name: str, rotate_180: bool = False, fallback_img_path: str = None):
        self.cam_idx = cam_idx
        self.name = name
        self.rotate_180 = rotate_180
        self.fallback_img_path = fallback_img_path

        self.lock = threading.Lock()
        self.ctrl_lock = threading.Lock()
        self.cam_ref: Picamera2 = None
        self.enabled = False
        self.is_live = False
        self.status = "STANDBY (Paused)"
        self.fps = 0.0

        # Focus settings & telemetry
        self.af_mode = "continuous"
        self.manual_lens_pos = 8.5 if cam_idx == 0 else 2.0
        self.current_lens_pos = 0.0
        self.af_state_str = "Idle"
        self.last_cmd_ack = ""

        self.latest_bgr = None
        self.latest_jpeg = None
        self.fallback_bgr = None

        if fallback_img_path and os.path.exists(fallback_img_path):
            img = cv2.imread(fallback_img_path)
            if img is not None:
                self.fallback_bgr = cv2.resize(img, (960, 540))

        self._render_placeholder()
        self._thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._thread.start()

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        if not enabled:
            self.is_live = False
            self.fps = 0.0
            self.status = "PAUSED (Click to make Live)"
            self._render_placeholder()

    def set_focus(self, mode: str, lens_pos: float = None) -> dict:
        with self.ctrl_lock:
            if lens_pos is not None:
                self.manual_lens_pos = round(max(0.0, min(15.0, float(lens_pos))), 2)

            if mode in ("continuous", "trigger", "manual"):
                self.af_mode = "auto" if mode == "trigger" else mode

            cam = self.cam_ref
            if cam is None or not self.is_live:
                return {"ok": False, "msg": "Camera not active"}

            try:
                if mode == "continuous":
                    cam.set_controls({
                        "AfMode": controls.AfModeEnum.Continuous,
                        "AfRange": controls.AfRangeEnum.Full,
                    })
                    self.last_cmd_ack = "Continuous AF Enabled"
                elif mode == "trigger":
                    cam.set_controls({
                        "AfMode": controls.AfModeEnum.Auto,
                        "AfRange": controls.AfRangeEnum.Full,
                        "AfTrigger": controls.AfTriggerEnum.Start,
                    })
                    self.last_cmd_ack = "AF Scan Triggered"
                elif mode == "manual":
                    cam.set_controls({
                        "AfMode": controls.AfModeEnum.Manual,
                        "LensPosition": float(self.manual_lens_pos),
                    })
                    self.last_cmd_ack = f"Manual Focus -> {self.manual_lens_pos:.1f}D"
                print(f"[Cam {self.cam_idx}] {self.last_cmd_ack}", flush=True)
                return {"ok": True, "ack": self.last_cmd_ack}
            except Exception as e:
                err_msg = f"Focus control error: {e}"
                print(f"[Cam {self.cam_idx}] {err_msg}", flush=True)
                return {"ok": False, "msg": err_msg}

    def _apply_initial_focus(self, cam: Picamera2):
        with self.ctrl_lock:
            try:
                if self.af_mode == "manual":
                    cam.set_controls({
                        "AfMode": controls.AfModeEnum.Manual,
                        "LensPosition": float(self.manual_lens_pos),
                    })
                else:
                    cam.set_controls({
                        "AfMode": controls.AfModeEnum.Continuous,
                        "AfRange": controls.AfRangeEnum.Full,
                    })
            except Exception as e:
                print(f"[Cam {self.cam_idx}] Initial focus setup error: {e}", flush=True)

    def _render_placeholder(self, custom_msg: str = None):
        if self.fallback_bgr is not None:
            base = (self.fallback_bgr * 0.55).astype(np.uint8)
        else:
            base = np.zeros((540, 960, 3), dtype=np.uint8)
            base[:] = (24, 28, 36)

        msg = custom_msg or self.status
        self._update_output(base, status_text=msg, is_live=False)

    def _update_output(self, bgr_frame: np.ndarray, status_text: str, is_live: bool):
        display_frame = bgr_frame.copy()
        h, w = display_frame.shape[:2]
        cv2.rectangle(display_frame, (0, 0), (w, 38), (18, 20, 26), -1)
        focus_info = f"AF: {self.af_mode.upper()} ({self.af_state_str}, {self.current_lens_pos:.1f}D)"
        label = f"Cam {self.cam_idx}: {self.name} | {status_text}"
        if is_live:
            label += f" | {focus_info}"
        color = (80, 230, 100) if is_live else (0, 180, 255)
        cv2.putText(display_frame, label, (12, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.54, color, 2, cv2.LINE_AA)

        ok, enc = cv2.imencode(".jpg", display_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        if ok:
            with self.lock:
                self.latest_bgr = display_frame
                self.latest_jpeg = enc.tobytes()

    def get_jpeg(self) -> bytes:
        with self.lock:
            return self.latest_jpeg

    def get_bgr(self) -> np.ndarray:
        with self.lock:
            return self.latest_bgr.copy()

    def _i2c_bus_healthy(self) -> bool:
        if self.cam_idx == 0:
            try:
                res = subprocess.run(["i2cdetect", "-y", "10"], timeout=2.0,
                                     capture_output=True, text=True)
                out = res.stdout
                if " 50 " not in out and " 64 " not in out:
                    return False
            except Exception:
                return False
        return True

    def _worker_loop(self):
        cam = None
        while True:
            if not self.enabled:
                if cam is not None:
                    self.cam_ref = None
                    try:
                        cam.stop()
                        cam.close()
                    except Exception:
                        pass
                    cam = None
                time.sleep(0.1)
                continue

            if cam is None:
                if not self._i2c_bus_healthy():
                    self.is_live = False
                    self.status = "OFFLINE — Check Ribbon Cable on CAM/DISP 1"
                    self._render_placeholder(self.status)
                    time.sleep(1.0)
                    continue

                try:
                    self.status = "Starting camera..."
                    self._render_placeholder(self.status)
                    cam = Picamera2(self.cam_idx)
                    cfg = cam.create_video_configuration(main={"size": (960, 540), "format": "RGB888"})
                    cam.configure(cfg)
                    cam.start()
                    self.cam_ref = cam
                    self.is_live = True
                    self._apply_initial_focus(cam)
                    last_t = time.time()
                    frames = 0
                except Exception as e:
                    self.is_live = False
                    self.cam_ref = None
                    self.status = f"Camera Error: {e}"
                    self._render_placeholder(self.status)
                    if cam is not None:
                        try:
                            cam.close()
                        except Exception:
                            pass
                        cam = None
                    time.sleep(1.5)
                    continue

            try:
                req = cam.capture_request()
                try:
                    arr = req.make_array("main")
                    meta = req.get_metadata()
                finally:
                    req.release()

                if meta:
                    lp = meta.get("LensPosition")
                    if lp is not None:
                        self.current_lens_pos = float(lp)
                    afs = meta.get("AfState")
                    if afs is not None:
                        self.af_state_str = AF_STATE_NAMES.get(int(afs), str(afs))

                bgr = arr
                if self.rotate_180:
                    bgr = cv2.rotate(bgr, cv2.ROTATE_180)
                frames += 1
                now = time.time()
                if now - last_t >= 1.0:
                    self.fps = frames / (now - last_t)
                    frames = 0
                    last_t = now
                self.status = f"LIVE ({self.fps:.1f} FPS)"
                self._update_output(bgr, self.status, is_live=True)
                time.sleep(0.008)
            except Exception as e:
                self.is_live = False
                self.cam_ref = None
                self.status = f"Stream Interrupted: {e}"
                self._render_placeholder(self.status)
                try:
                    cam.stop()
                    cam.close()
                except Exception:
                    pass
                cam = None
                time.sleep(1.0)


class StreamController:
    def __init__(self, cam0: ControllableCamera, cam1: ControllableCamera, sense_hat: SenseHatWorker):
        self.cam0 = cam0
        self.cam1 = cam1
        self.sense_hat = sense_hat
        self.active_mode = "both"
        self.set_mode("both")

    def set_mode(self, mode: str):
        if mode not in ("cam1", "cam0", "both", "off"):
            return
        self.active_mode = mode
        if mode == "cam1":
            self.cam0.set_enabled(False)
            self.cam1.set_enabled(True)
        elif mode == "cam0":
            self.cam1.set_enabled(False)
            self.cam0.set_enabled(True)
        elif mode == "both":
            self.cam1.set_enabled(True)
            self.cam0.set_enabled(True)
        elif mode == "off":
            self.cam1.set_enabled(False)
            self.cam0.set_enabled(False)

    def flip_camera(self, idx: int):
        target = self.cam0 if idx == 0 else self.cam1
        target.rotate_180 = not target.rotate_180

    def set_focus(self, idx: int, mode: str, lens_pos: float = None) -> dict:
        target = self.cam0 if idx == 0 else self.cam1
        return target.set_focus(mode, lens_pos)

    def get_state(self) -> dict:
        return {
            "mode": self.active_mode,
            "cam0": {
                "status": self.cam0.status,
                "live": self.cam0.is_live,
                "af_mode": self.cam0.af_mode,
                "af_state": self.cam0.af_state_str,
                "lens_pos": round(self.cam0.current_lens_pos, 2),
                "manual_pos": round(self.cam0.manual_lens_pos, 2),
                "ack": self.cam0.last_cmd_ack,
            },
            "cam1": {
                "status": self.cam1.status,
                "live": self.cam1.is_live,
                "af_mode": self.cam1.af_mode,
                "af_state": self.cam1.af_state_str,
                "lens_pos": round(self.cam1.current_lens_pos, 2),
                "manual_pos": round(self.cam1.manual_lens_pos, 2),
                "ack": self.cam1.last_cmd_ack,
            },
            "sense_hat": self.sense_hat.get_data(),
        }


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Raspberry Eyes — Dual Camera & Sense HAT Dashboard</title>
<style>
  body {
    margin: 0;
    background: #0d1117;
    color: #e6edf3;
    font-family: system-ui, -apple-system, sans-serif;
    display: flex;
    flex-direction: column;
    align-items: center;
    min-height: 100vh;
  }
  header {
    width: 100%;
    padding: 12px 24px;
    background: #161b22;
    border-bottom: 1px solid #30363d;
    box-sizing: border-box;
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    justify-content: space-between;
    align-items: center;
  }
  h1 { margin: 0; font-size: 1.15rem; display: flex; align-items: center; gap: 8px; }
  .controls {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }
  button {
    background: #21262d;
    color: #e6edf3;
    border: 1px solid #30363d;
    padding: 7px 13px;
    border-radius: 6px;
    font-weight: 600;
    cursor: pointer;
    font-size: 0.86rem;
    transition: background 0.15s, border-color 0.15s;
  }
  button:hover { background: #30363d; }
  button:active { background: #161b22; }
  button.active {
    background: #238636;
    border-color: #2ea043;
    color: #fff;
  }
  button.btn-step {
    padding: 5px 9px;
    font-size: 0.8rem;
    font-family: monospace;
  }

  /* Sense HAT Sensor Banner */
  .sense-banner {
    width: 96%;
    max-width: 1560px;
    margin: 14px auto 0 auto;
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 12px 18px;
    box-sizing: border-box;
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    justify-content: space-between;
    align-items: center;
  }
  .sense-metrics {
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    align-items: center;
  }
  .metric-card {
    display: flex;
    align-items: center;
    gap: 8px;
    background: #0d1117;
    border: 1px solid #30363d;
    padding: 6px 14px;
    border-radius: 8px;
  }
  .metric-icon { font-size: 1.15rem; }
  .metric-label { font-size: 0.76rem; color: #8b949e; text-transform: uppercase; font-weight: 600; }
  .metric-val { font-size: 0.98rem; font-weight: bold; color: #58a6ff; font-family: monospace; }
  .sense-led-controls {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }
  .sense-led-controls input[type=text] {
    background: #0d1117;
    border: 1px solid #30363d;
    color: #e6edf3;
    padding: 6px 10px;
    border-radius: 6px;
    font-size: 0.86rem;
    width: 140px;
  }
  .sense-led-controls input[type=text]:focus {
    border-color: #58a6ff;
    outline: none;
  }

  .stage {
    width: 96%;
    max-width: 1560px;
    margin: 14px auto 20px auto;
    display: grid;
    gap: 16px;
  }
  .stage.single { grid-template-columns: 1fr; max-width: 980px; }
  .stage.split { grid-template-columns: repeat(auto-fit, minmax(460px, 1fr)); }
  .card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    overflow: hidden;
    box-shadow: 0 6px 18px rgba(0,0,0,0.4);
    display: flex;
    flex-direction: column;
  }
  .card.hidden { display: none; }
  .card img {
    width: 100%;
    display: block;
    background: #000;
  }
  .focus-panel {
    padding: 12px 16px;
    border-top: 1px solid #30363d;
    display: flex;
    flex-direction: column;
    gap: 10px;
    background: #0f131a;
  }
  .focus-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .focus-title {
    font-weight: 600;
    font-size: 0.92rem;
    color: #c9d1d9;
  }
  .focus-badge {
    font-size: 0.84rem;
    padding: 3px 9px;
    border-radius: 12px;
    background: #21262d;
    border: 1px solid #30363d;
    color: #58a6ff;
    font-family: monospace;
  }
  .slider-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .slider-row input[type=range] {
    flex: 1;
    accent-color: #2ea043;
    cursor: pointer;
  }
  .slider-label {
    font-size: 0.8rem;
    color: #8b949e;
    min-width: 68px;
  }
  .feedback-msg {
    font-size: 0.8rem;
    color: #3fb950;
    min-height: 18px;
  }
</style>
</head>
<body>
  <header>
    <h1><span>👁️</span> Raspberry Eyes — Live Dashboard</h1>
    <div class="controls">
      <button id="btn-cam0" onclick="setMode('cam0')">Camera 0 Live (Standard)</button>
      <button id="btn-cam1" onclick="setMode('cam1')">Camera 1 Live (Wide NoIR)</button>
      <button id="btn-both" onclick="setMode('both')">Both Live (Split View)</button>
      <button id="btn-off" onclick="setMode('off')">Pause All</button>
    </div>
  </header>

  <!-- Sense HAT Live Telemetry & Control Banner -->
  <div class="sense-banner">
    <div class="sense-metrics">
      <div class="metric-card">
        <span class="metric-icon">🌡️</span>
        <div>
          <div class="metric-label">Temperature</div>
          <div id="sh-temp" class="metric-val">-- °C</div>
        </div>
      </div>
      <div class="metric-card">
        <span class="metric-icon">💧</span>
        <div>
          <div class="metric-label">Humidity</div>
          <div id="sh-hum" class="metric-val">-- %</div>
        </div>
      </div>
      <div class="metric-card">
        <span class="metric-icon">⏱️</span>
        <div>
          <div class="metric-label">Pressure</div>
          <div id="sh-press" class="metric-val">-- hPa</div>
        </div>
      </div>
      <div class="metric-card">
        <span class="metric-icon">🧭</span>
        <div>
          <div class="metric-label">Orientation (P/R/Y)</div>
          <div id="sh-ori" class="metric-val" style="font-size:0.86rem;">--</div>
        </div>
      </div>
    </div>

    <!-- Interactive 8x8 LED controls -->
    <div class="sense-led-controls">
      <span style="font-size:0.82rem;font-weight:600;color:#8b949e;">Sense HAT 8×8 LEDs:</span>
      <input id="sh-msg-input" type="text" placeholder="Scroll text..." value="Raspberry Eyes">
      <button onclick="sendSenseMsg()">Scroll</button>
      <button onclick="sendSenseRainbow()">Rainbow</button>
      <button onclick="sendSenseClear()">Clear</button>
    </div>
  </div>

  <div id="stage" class="stage split">
    <!-- Camera 0 Card -->
    <div id="card0" class="card">
      <img id="cam0" src="/stream/0" alt="Camera 0 Stream">
      <div class="focus-panel">
        <div class="focus-row">
          <span class="focus-title">Camera 0 Focus (imx708 Standard — Looking at Sense HAT)</span>
          <span id="badge0" class="focus-badge">AF: CONTINUOUS | Lens: -- D</span>
        </div>
        <div class="focus-row">
          <div class="controls">
            <button id="af0-continuous" class="active" onclick="setFocusMode(0, 'continuous')">Continuous AF</button>
            <button id="af0-trigger" onclick="setFocusMode(0, 'trigger')">Refocus Now (Single AF)</button>
            <button id="af0-manual" onclick="setFocusMode(0, 'manual')">Manual Focus</button>
            <button onclick="flipCam(0)">Flip 180°</button>
          </div>
          <div class="controls">
            <span style="font-size:0.8rem;color:#8b949e;">Step:</span>
            <button class="btn-step" onclick="stepFocus(0, -1.0)">-1.0D</button>
            <button class="btn-step" onclick="stepFocus(0, -0.2)">-0.2D</button>
            <button class="btn-step" onclick="stepFocus(0, +0.2)">+0.2D</button>
            <button class="btn-step" onclick="stepFocus(0, +1.0)">+1.0D</button>
          </div>
        </div>
        <div class="slider-row">
          <span class="slider-label">Far (0.0D)</span>
          <input id="slider0" type="range" min="0" max="15" step="0.1" value="8.5"
                 oninput="onSliderDrag(0, this.value)" onchange="onSliderCommit(0, this.value)">
          <span class="slider-label" style="text-align:right;">Macro (15D)</span>
          <span id="sval0" class="focus-badge" style="min-width:54px;text-align:center;">8.5 D</span>
        </div>
        <div id="msg0" class="feedback-msg">Ready</div>
      </div>
    </div>

    <!-- Camera 1 Card -->
    <div id="card1" class="card">
      <img id="cam1" src="/stream/1" alt="Camera 1 Stream">
      <div class="focus-panel">
        <div class="focus-row">
          <span class="focus-title">Camera 1 Focus (imx708 Wide NoIR)</span>
          <span id="badge1" class="focus-badge">AF: CONTINUOUS | Lens: -- D</span>
        </div>
        <div class="focus-row">
          <div class="controls">
            <button id="af1-continuous" class="active" onclick="setFocusMode(1, 'continuous')">Continuous AF</button>
            <button id="af1-trigger" onclick="setFocusMode(1, 'trigger')">Refocus Now (Single AF)</button>
            <button id="af1-manual" onclick="setFocusMode(1, 'manual')">Manual Focus</button>
            <button onclick="flipCam(1)">Flip 180°</button>
          </div>
          <div class="controls">
            <span style="font-size:0.8rem;color:#8b949e;">Step:</span>
            <button class="btn-step" onclick="stepFocus(1, -1.0)">-1.0D</button>
            <button class="btn-step" onclick="stepFocus(1, -0.2)">-0.2D</button>
            <button class="btn-step" onclick="stepFocus(1, +0.2)">+0.2D</button>
            <button class="btn-step" onclick="stepFocus(1, +1.0)">+1.0D</button>
          </div>
        </div>
        <div class="slider-row">
          <span class="slider-label">Far (0.0D)</span>
          <input id="slider1" type="range" min="0" max="15" step="0.1" value="2.0"
                 oninput="onSliderDrag(1, this.value)" onchange="onSliderCommit(1, this.value)">
          <span class="slider-label" style="text-align:right;">Macro (15D)</span>
          <span id="sval1" class="focus-badge" style="min-width:54px;text-align:center;">2.0 D</span>
        </div>
        <div id="msg1" class="feedback-msg">Ready</div>
      </div>
    </div>
  </div>

  <script>
    let currentMode = "both";
    let activeControllers = {0: null, 1: null};
    let sliderTimer = {0: null, 1: null};
    let userInteractingUntil = {0: 0, 1: 0};

    async function setMode(mode) {
      await fetch("/api/mode?mode=" + mode, { method: "POST" });
      applyModeUI(mode);
    }

    async function flipCam(idx) {
      await fetch("/api/flip?cam=" + idx, { method: "POST" });
    }

    async function sendSenseMsg() {
      const text = encodeURIComponent(document.getElementById("sh-msg-input").value || "Hello");
      await fetch("/api/sense/message?text=" + text, { method: "POST" });
    }

    async function sendSenseRainbow() {
      await fetch("/api/sense/rainbow", { method: "POST" });
    }

    async function sendSenseClear() {
      await fetch("/api/sense/clear", { method: "POST" });
    }

    async function sendFocusCommand(camIdx, mode, pos) {
      userInteractingUntil[camIdx] = Date.now() + 2500;
      if (activeControllers[camIdx]) {
        activeControllers[camIdx].abort();
      }
      const controller = new AbortController();
      activeControllers[camIdx] = controller;

      let url = `/api/focus?cam=${camIdx}&mode=${mode}`;
      if (pos !== undefined && pos !== null) {
        url += `&pos=${pos}`;
      }

      const msgEl = document.getElementById("msg" + camIdx);
      try {
        const resp = await fetch(url, { method: "POST", signal: controller.signal });
        const data = await resp.json();
        if (msgEl) {
          msgEl.textContent = data.ack || (data.ok ? "Applied" : data.msg);
          msgEl.style.color = data.ok ? "#3fb950" : "#f85149";
        }
      } catch (err) {
        if (err.name !== "AbortError" && msgEl) {
          msgEl.textContent = "Error: " + err.message;
          msgEl.style.color = "#f85149";
        }
      }
    }

    function setFocusMode(camIdx, mode) {
      const sl = document.getElementById("slider" + camIdx);
      const pos = sl ? sl.value : null;
      sendFocusCommand(camIdx, mode, mode === "manual" ? pos : null);
      updateAfButtons(camIdx, mode === "trigger" ? "auto" : mode);
    }

    function onSliderDrag(camIdx, val) {
      document.getElementById("sval" + camIdx).textContent = parseFloat(val).toFixed(1) + " D";
      updateAfButtons(camIdx, "manual");
      userInteractingUntil[camIdx] = Date.now() + 2500;

      if (sliderTimer[camIdx]) clearTimeout(sliderTimer[camIdx]);
      sliderTimer[camIdx] = setTimeout(() => {
        sendFocusCommand(camIdx, "manual", val);
      }, 50);
    }

    function onSliderCommit(camIdx, val) {
      if (sliderTimer[camIdx]) clearTimeout(sliderTimer[camIdx]);
      sendFocusCommand(camIdx, "manual", val);
    }

    function stepFocus(camIdx, delta) {
      const sl = document.getElementById("slider" + camIdx);
      if (!sl) return;
      let next = Math.max(0, Math.min(15, parseFloat(sl.value) + delta));
      next = Math.round(next * 10) / 10;
      sl.value = next;
      document.getElementById("sval" + camIdx).textContent = next.toFixed(1) + " D";
      updateAfButtons(camIdx, "manual");
      sendFocusCommand(camIdx, "manual", next);
    }

    function updateAfButtons(camIdx, afMode) {
      const cBtn = document.getElementById(`af${camIdx}-continuous`);
      const tBtn = document.getElementById(`af${camIdx}-trigger`);
      const mBtn = document.getElementById(`af${camIdx}-manual`);
      if (!cBtn) return;
      cBtn.classList.toggle("active", afMode === "continuous");
      tBtn.classList.toggle("active", afMode === "auto");
      mBtn.classList.toggle("active", afMode === "manual");
    }

    function applyModeUI(mode) {
      currentMode = mode;
      ["cam1", "cam0", "both", "off"].forEach(m => {
        const b = document.getElementById("btn-" + m);
        if (b) b.classList.toggle("active", m === mode);
      });
      const stage = document.getElementById("stage");
      const card1 = document.getElementById("card1");
      const card0 = document.getElementById("card0");

      if (mode === "cam1") {
        stage.className = "stage single";
        card1.classList.remove("hidden");
        card0.classList.add("hidden");
      } else if (mode === "cam0") {
        stage.className = "stage single";
        card1.classList.add("hidden");
        card0.classList.remove("hidden");
      } else {
        stage.className = "stage split";
        card1.classList.remove("hidden");
        card0.classList.remove("hidden");
      }
    }

    async function syncState() {
      try {
        const r = await fetch("/api/state");
        const st = await r.json();
        if (st.mode && st.mode !== currentMode) {
          applyModeUI(st.mode);
        }

        // Update Sense HAT Telemetry
        if (st.sense_hat && st.sense_hat.available) {
          const sh = st.sense_hat;
          document.getElementById("sh-temp").textContent = `${sh.temp_c}°C (${sh.temp_f}°F)`;
          document.getElementById("sh-hum").textContent = `${sh.humidity}%`;
          document.getElementById("sh-press").textContent = `${sh.pressure} hPa`;
          document.getElementById("sh-ori").textContent = `P: ${sh.pitch}° · R: ${sh.roll}° · Y: ${sh.yaw}°`;
        }

        // Update Camera focus badges and sliders
        [0, 1].forEach(idx => {
          const c = st["cam" + idx];
          if (!c) return;
          const badge = document.getElementById("badge" + idx);
          if (badge) {
            badge.textContent = `AF: ${c.af_mode.toUpperCase()} (${c.af_state}) | Lens: ${c.lens_pos.toFixed(1)} D`;
          }
          if (Date.now() > userInteractingUntil[idx]) {
            updateAfButtons(idx, c.af_mode);
            const sl = document.getElementById("slider" + idx);
            const sv = document.getElementById("sval" + idx);
            if (sl && document.activeElement !== sl) {
              const val = (c.af_mode === "manual" && c.manual_pos !== undefined) ? c.manual_pos : c.lens_pos;
              sl.value = val;
              if (sv) sv.textContent = val.toFixed(1) + " D";
            }
          }
        });
      } catch (e) {}
    }

    function attachMjpegWatcher(imgId, streamIdx) {
      const img = document.getElementById(imgId);
      if (!img) return;
      img.onerror = () => {
        setTimeout(() => {
          img.src = "/stream/" + streamIdx + "?t=" + Date.now();
        }, 1000);
      };
    }
    attachMjpegWatcher("cam0", 0);
    attachMjpegWatcher("cam1", 1);

    applyModeUI("both");
    setInterval(syncState, 800);
  </script>
</body>
</html>
"""


class StreamHandler(BaseHTTPRequestHandler):
    controller: StreamController = None

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        clean_path = self.path.split("?")[0]
        query = self.path.split("?")[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)

        if clean_path == "/api/mode":
            mode = params.get("mode", "both")
            self.controller.set_mode(mode)
            body = json.dumps(self.controller.get_state()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif clean_path == "/api/flip":
            cam_idx = int(params.get("cam", "1"))
            self.controller.flip_camera(cam_idx)
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif clean_path == "/api/focus":
            cam_idx = int(params.get("cam", "0"))
            mode = params.get("mode", "continuous")
            pos_str = params.get("pos")
            pos = float(pos_str) if pos_str is not None else None
            res = self.controller.set_focus(cam_idx, mode, pos)
            body = json.dumps(res).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif clean_path == "/api/sense/message":
            import urllib.parse
            text = urllib.parse.unquote(params.get("text", "Raspberry Eyes"))
            color = params.get("color", "cyan")
            self.controller.sense_hat.show_message(text, color)
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif clean_path == "/api/sense/clear":
            self.controller.sense_hat.clear()
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif clean_path == "/api/sense/rainbow":
            self.controller.sense_hat.show_rainbow()
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_GET(self):
        clean_path = self.path.split("?")[0]
        if clean_path == "/" or clean_path.startswith("/index"):
            data = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif clean_path == "/api/state":
            body = json.dumps(self.controller.get_state()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif clean_path in ("/frame/0", "/frame/1"):
            idx = int(clean_path.split("/")[-1])
            worker = self.controller.cam0 if idx == 0 else self.controller.cam1
            jpg = worker.get_jpeg()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Content-Length", str(len(jpg)))
            self.end_headers()
            try:
                self.wfile.write(jpg)
            except Exception:
                pass
        elif clean_path in ("/stream/0", "/stream/1"):
            idx = int(clean_path.split("/")[-1])
            worker = self.controller.cam0 if idx == 0 else self.controller.cam1
            self.send_response(200)
            self.send_header("Age", "0")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
            self.end_headers()
            try:
                while True:
                    jpg = worker.get_jpeg()
                    if jpg is not None:
                        self.wfile.write(b"--FRAME\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(f"Content-Length: {len(jpg)}\r\n\r\n".encode("ascii"))
                        self.wfile.write(jpg)
                        self.wfile.write(b"\r\n")
                    time.sleep(0.033)
            except Exception:
                pass
        else:
            self.send_error(404)


def run_qt_window(controller: StreamController):
    from PyQt5.QtCore import Qt, QTimer
    from PyQt5.QtGui import QImage, QPixmap
    from PyQt5.QtWidgets import (
        QApplication,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QPushButton,
        QSlider,
        QVBoxLayout,
        QWidget,
    )

    class CameraMonitorWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Raspberry Eyes — Live Monitor (http://localhost:8080)")
            self.resize(1000, 580)
            self.setStyleSheet("background-color: #0d1117; color: #e6edf3;")

            central = QWidget()
            self.setCentralWidget(central)
            layout = QVBoxLayout(central)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(6)

            # Sense HAT Telemetry Banner
            self.sh_bar = QLabel("Sense HAT: Initializing...")
            self.sh_bar.setStyleSheet("background: #161b22; border: 1px solid #30363d; padding: 6px 12px; border-radius: 6px; font-weight: bold; color: #58a6ff;")
            layout.addWidget(self.sh_bar)

            btn_bar = QHBoxLayout()
            self.base_style = (
                "QPushButton { background: #21262d; color: #e6edf3; border: 1px solid #30363d; "
                "padding: 6px 10px; border-radius: 6px; font-weight: bold; } "
                "QPushButton:hover { background: #30363d; }"
            )
            self.active_style = (
                "QPushButton { background: #238636; color: #ffffff; border: 1px solid #2ea043; "
                "padding: 6px 10px; border-radius: 6px; font-weight: bold; }"
            )

            self.btns = {}
            for mode_key, label in [
                ("cam0", "Cam 0 Live"),
                ("cam1", "Cam 1 Live"),
                ("both", "Both Live"),
                ("off", "Pause"),
            ]:
                b = QPushButton(label)
                b.clicked.connect(lambda checked=False, m=mode_key: controller.set_mode(m))
                self.btns[mode_key] = b
                btn_bar.addWidget(b)

            btn_af0 = QPushButton("Cam 0 AutoFocus")
            btn_af0.setStyleSheet(self.base_style)
            btn_af0.clicked.connect(lambda: controller.set_focus(0, "trigger"))
            btn_bar.addWidget(btn_af0)

            btn_caf0 = QPushButton("Cam 0 Cont. AF")
            btn_caf0.setStyleSheet(self.base_style)
            btn_caf0.clicked.connect(lambda: controller.set_focus(0, "continuous"))
            btn_bar.addWidget(btn_caf0)

            layout.addLayout(btn_bar)

            # Focus slider bar for Cam 0
            focus_bar = QHBoxLayout()
            lbl_f0 = QLabel("Cam 0 Focus:")
            self.slider0 = QSlider(Qt.Horizontal)
            self.slider0.setMinimum(0)
            self.slider0.setMaximum(150)
            self.slider0.setValue(85)
            self.slider0.valueChanged.connect(lambda v: controller.set_focus(0, "manual", v / 10.0))
            focus_bar.addWidget(lbl_f0)
            focus_bar.addWidget(self.slider0, stretch=1)
            layout.addLayout(focus_bar)

            self.video_label = QLabel()
            self.video_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(self.video_label, stretch=1)

            self.timer = QTimer(self)
            self.timer.timeout.connect(self.update_frame)
            self.timer.start(35)

        def update_frame(self):
            mode = controller.active_mode
            for m, b in self.btns.items():
                b.setStyleSheet(self.active_style if m == mode else self.base_style)

            sh = controller.sense_hat.get_data()
            if sh.get("available"):
                self.sh_bar.setText(f"Sense HAT: {sh['temp_c']}°C ({sh['temp_f']}°F)  |  Humidity: {sh['humidity']}%  |  Pressure: {sh['pressure']} hPa  |  P:{sh['pitch']}° R:{sh['roll']}° Y:{sh['yaw']}°")

            w = max(320, self.video_label.width())
            h = max(200, self.video_label.height())

            if mode == "cam1":
                bgr = controller.cam1.get_bgr()
            elif mode == "cam0":
                bgr = controller.cam0.get_bgr()
            else:
                f0 = cv2.resize(controller.cam0.get_bgr(), (490, 276))
                f1 = cv2.resize(controller.cam1.get_bgr(), (490, 276))
                bgr = np.hstack([f0, f1])

            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            ih, iw, ch = rgb.shape
            qimg = QImage(rgb.data, iw, ih, ch * iw, QImage.Format_RGB888)
            pix = QPixmap.fromImage(qimg).scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.video_label.setPixmap(pix)

    app = QApplication(sys.argv)
    win = CameraMonitorWindow()
    win.show()
    win.raise_()
    win.activateWindow()
    print("[Monitor] Opened PyQt5 Live Camera & Sense HAT on DISPLAY=:0!", flush=True)
    app.exec_()


def main():
    sense_hat = SenseHatWorker()
    cam0 = ControllableCamera(0, "imx708 (Standard)", rotate_180=False, fallback_img_path=FALLBACK_IMG_CAM0)
    cam1 = ControllableCamera(1, "imx708_wide_noir (Wide NoIR)", rotate_180=True)
    controller = StreamController(cam0, cam1, sense_hat)

    StreamHandler.controller = controller

    server = ThreadingHTTPServer(("0.0.0.0", 8080), StreamHandler)
    http_thread = threading.Thread(target=server.serve_forever, daemon=True)
    http_thread.start()
    print("Serving Raspberry Eyes dashboard on http://0.0.0.0:8080 ...", flush=True)

    while True:
        res = subprocess.run(
            ["python3", "-c", "import tkinter as tk; r = tk.Tk(); r.withdraw(); r.destroy()"],
            env={**os.environ, "DISPLAY": ":0"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if res.returncode == 0:
            run_qt_window(controller)
            time.sleep(1.5)
        else:
            time.sleep(1.5)


if __name__ == "__main__":
    main()
