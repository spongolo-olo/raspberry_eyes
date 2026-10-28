#!/usr/bin/env python3
"""
CLI tool for agents to read Sense HAT telemetry and control the 8x8 LED matrix.
"""

import argparse
import json
import sys
import urllib.request
import urllib.parse

SERVER_URL = "http://localhost:8080"


def main():
    parser = argparse.ArgumentParser(description="Read Sense HAT telemetry and control 8x8 LEDs.")
    parser.add_argument("--status", action="store_true", help="Print temperature, humidity, pressure, and orientation")
    parser.add_argument("--message", "-m", type=str, default=None, help="Scroll text message across 8x8 LEDs")
    parser.add_argument("--color", type=str, default="cyan", choices=["cyan", "green", "red", "blue", "yellow", "white"],
                        help="Text color for scrolling message")
    parser.add_argument("--rainbow", action="store_true", help="Display rainbow pattern on LEDs")
    parser.add_argument("--clear", action="store_true", help="Clear/turn off all LEDs")

    args = parser.parse_args()

    if args.message:
        encoded = urllib.parse.quote(args.message)
        url = f"{SERVER_URL}/api/sense/message?text={encoded}&color={args.color}"
        req = urllib.request.Request(url, method="POST")
        urllib.request.urlopen(req, timeout=3.0)
        print(f"Sent message to Sense HAT LEDs: {args.message}")

    elif args.rainbow:
        url = f"{SERVER_URL}/api/sense/rainbow"
        req = urllib.request.Request(url, method="POST")
        urllib.request.urlopen(req, timeout=3.0)
        print("Displayed rainbow pattern on Sense HAT LEDs.")

    elif args.clear:
        url = f"{SERVER_URL}/api/sense/clear"
        req = urllib.request.Request(url, method="POST")
        urllib.request.urlopen(req, timeout=3.0)
        print("Cleared Sense HAT LEDs.")

    elif args.status or len(sys.argv) == 1:
        req = urllib.request.urlopen(f"{SERVER_URL}/api/state", timeout=3.0)
        state = json.loads(req.read().decode("utf-8"))
        sh = state.get("sense_hat", {})
        print(json.dumps(sh, indent=2))


if __name__ == "__main__":
    main()
