import argparse
from pathlib import Path
import time

import pyautogui


def calibrate_loop():
    print("[CALIBRATE] Press Ctrl+C to stop.")
    try:
        while True:
            pos = pyautogui.position()
            print(f"[CALIBRATE] x={int(pos.x)} y={int(pos.y)}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("[CALIBRATE] stopped")


def capture_template(name: str, x: int, y: int, width: int, height: int):
    out_dir = Path("assets/ui")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{name}.png"
    image = pyautogui.screenshot(region=(x, y, width, height))
    image.save(out_file)
    print(f"[CAPTURE] saved template: {out_file}")


def main():
    parser = argparse.ArgumentParser(description="UI calibration/capture helper for Evrima bot rejoin flow")
    parser.add_argument("--calibrate", action="store_true", help="Print current mouse coordinates repeatedly")
    parser.add_argument("--capture-template", type=str, help="Template name to capture into assets/ui/<name>.png")
    parser.add_argument("--x", type=int, default=0)
    parser.add_argument("--y", type=int, default=0)
    parser.add_argument("--width", type=int, default=300)
    parser.add_argument("--height", type=int, default=120)
    args = parser.parse_args()

    if args.calibrate:
        calibrate_loop()
        return

    if args.capture_template:
        capture_template(args.capture_template, args.x, args.y, args.width, args.height)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
