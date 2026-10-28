#!/usr/bin/env python3
"""
CLI tool for agents to capture live camera snapshots, adjust focus,
and query telemetry from the Raspberry Pi 5 camera server.
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.parse

SERVER_URL = "http://localhost:8080"


def get_state() -> dict:
    try:
        req = urllib.request.urlopen(f"{SERVER_URL}/api/state", timeout=3.0)
        return json.loads(req.read().decode("utf-8"))
    except Exception as e:
        sys.stderr.write(f"Error connecting to camera server at {SERVER_URL}: {e}\n")
        return {}


def set_focus(cam: int, mode: str, pos: float = None) -> bool:
    params = {"cam": str(cam), "mode": mode}
    if pos is not None:
        params["pos"] = str(pos)
    query = urllib.parse.urlencode(params)
    url = f"{SERVER_URL}/api/focus?{query}"
    try:
        req = urllib.request.Request(url, method="POST")
        resp = urllib.request.urlopen(req, timeout=3.0)
        data = json.loads(resp.read().decode("utf-8"))
        if data.get("ok"):
            # Give the lens motor a brief moment to slew to position
            time.sleep(0.3)
            return True
        else:
            sys.stderr.write(f"Focus command failed: {data.get('msg')}\n")
            return False
    except Exception as e:
        sys.stderr.write(f"Error setting focus: {e}\n")
        return False


def capture_frame(cam: int, output_path: str) -> str:
    url = f"{SERVER_URL}/frame/{cam}?t={int(time.time() * 1000)}"
    try:
        req = urllib.request.urlopen(url, timeout=5.0)
        img_data = req.read()
        if not img_data or img_data[:2] != b"\xff\xd8":
            sys.stderr.write("Warning: Response does not appear to be a valid JPEG image.\n")
        
        output_path = os.path.abspath(output_path)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(img_data)
        return output_path
    except Exception as e:
        sys.stderr.write(f"Failed to capture frame from {url}: {e}\n")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Capture live camera frames and control focus on Raspberry Pi 5.")
    parser.add_argument("--cam", type=int, choices=[0, 1], default=0,
                        help="Camera index: 0 = imx708 Standard (pointing at Sense HAT), 1 = imx708_wide_noir (pointing outward)")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Output JPEG file path (default: /tmp/cam<idx>_<timestamp>.jpg)")
    parser.add_argument("--focus", type=str, choices=["continuous", "trigger", "manual"], default=None,
                        help="Set focus mode before capture: 'continuous', 'trigger' (single AF scan), or 'manual'")
    parser.add_argument("--lens-pos", type=float, default=None,
                        help="Manual lens position in dioptres (0.0=infinity, ~8.5=Sense HAT macro, max 15.0)")
    parser.add_argument("--telemetry", action="store_true",
                        help="Print current camera and Sense HAT telemetry as JSON")

    args = parser.parse_args()

    if args.telemetry:
        state = get_state()
        print(json.dumps(state, indent=2))
        if not args.output and not args.focus:
            return

    if args.focus or args.lens_pos is not None:
        mode = args.focus or "manual"
        set_focus(args.cam, mode, args.lens_pos)

    output_path = args.output
    if not output_path:
        ts = int(time.time())
        output_path = f"/tmp/cam{args.cam}_{ts}.jpg"

    saved_file = capture_frame(args.cam, output_path)
    print(saved_file)


if __name__ == "__main__":
    main()
