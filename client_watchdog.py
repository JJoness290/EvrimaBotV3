import argparse
import json
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


def load_config():
    path = Path("config.json")
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def run_macro_once():
    cfg = load_config()
    macro = cfg.get("coordinate_join_macro", {})
    if not isinstance(macro, dict):
        print("[MACRO] no valid macro config found")
        return
    steps = macro.get("steps", [])
    delay = float(macro.get("post_step_delay_seconds", 0.5) or 0.0)
    if not isinstance(steps, list) or not steps:
        print("[MACRO] no steps configured")
        return

    for step in steps:
        if not isinstance(step, dict):
            continue
        st = str(step.get("type", "")).strip().lower()
        if st == "focus_window":
            print("[MACRO] focusing game window")
            pyautogui.press("alt")
        elif st == "click_position":
            x = int(step.get("x", 0))
            y = int(step.get("y", 0))
            label = str(step.get("label", "point"))
            print(f"[MACRO] clicking {label} x={x} y={y}")
            pyautogui.click(x, y)
        elif st == "double_click_position":
            x = int(step.get("x", 0))
            y = int(step.get("y", 0))
            label = str(step.get("label", "point"))
            print(f"[MACRO] double-clicking {label} x={x} y={y}")
            pyautogui.doubleClick(x, y)
        elif st == "type_text":
            text = str(step.get("text", ""))
            print(f"[MACRO] typing {text}")
            pyautogui.write(text)
        elif st == "wait_seconds":
            seconds = float(step.get("seconds", 1))
            print(f"[MACRO] waiting {seconds} seconds")
            time.sleep(max(0.0, seconds))
        elif st == "press_key":
            key = str(step.get("key", "enter"))
            print(f"[MACRO] pressing {key}")
            pyautogui.press(key)
        elif st == "hotkey":
            keys = step.get("keys", [])
            if isinstance(keys, list) and keys:
                print(f"[MACRO] hotkey {'+'.join([str(k) for k in keys])}")
                pyautogui.hotkey(*[str(k) for k in keys])
        if delay > 0:
            time.sleep(delay)


def test_single_step(label: str):
    cfg = load_config()
    macro = cfg.get("coordinate_join_macro", {})
    steps = macro.get("steps", []) if isinstance(macro, dict) else []
    target = None
    for step in steps:
        if not isinstance(step, dict):
            continue
        if str(step.get("label", "")).strip().lower() == str(label).strip().lower():
            target = step
            break
    if not target:
        print(f"[TEST STEP] label not found: {label}")
        return
    if "x" not in target or "y" not in target:
        print(f"[TEST STEP] step has no coordinates: {label}")
        return

    base_x = int(target.get("x", 0))
    base_y = int(target.get("y", 0))
    off_x = int(target.get("offset_x", 0) or 0)
    off_y = int(target.get("offset_y", 0) or 0)
    final_x = base_x + off_x
    final_y = base_y + off_y
    move_duration = float(target.get("move_duration_seconds", 0.15) or 0.0)
    pre_delay = float(target.get("pre_click_delay_seconds", 0.0) or 0.0)
    post_delay = float(target.get("post_click_delay_seconds", 0.0) or 0.0)
    print(f"[TEST STEP] {label} base=({base_x},{base_y}) offset=({off_x},{off_y}) final=({final_x},{final_y})")
    pyautogui.moveTo(final_x, final_y, duration=max(0.0, move_duration))
    if pre_delay > 0:
        time.sleep(pre_delay)
    st = str(target.get("type", "")).strip().lower()
    if st == "double_click_position":
        pyautogui.doubleClick(final_x, final_y)
    elif st == "move_only_position":
        pass
    else:
        pyautogui.click(final_x, final_y)
    if post_delay > 0:
        time.sleep(post_delay)


def main():
    parser = argparse.ArgumentParser(description="UI calibration/capture helper for Evrima bot rejoin flow")
    parser.add_argument("--calibrate", action="store_true", help="Print current mouse coordinates repeatedly")
    parser.add_argument("--capture-template", type=str, help="Template name to capture into assets/ui/<name>.png")
    parser.add_argument("--test-macro", action="store_true", help="Run coordinate_join_macro once from config.json")
    parser.add_argument("--test-step", type=str, help="Run only one labeled step from coordinate_join_macro")
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

    if args.test_macro:
        run_macro_once()
        return

    if args.test_step:
        test_single_step(args.test_step)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
