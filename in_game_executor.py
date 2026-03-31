import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pyautogui

GAME_COMMANDS_FILE = Path("game_commands.json")
CONFIG_FILE = Path("config.json")
EXECUTOR_HEARTBEAT_FILE = Path("executor_heartbeat.json")

DEFAULT_POST_SEND_DELAYS = {
    "/elder": 5,
    "/growth": 3,
    "/diet1": 2,
    "/diet2": 2,
    "/diet3": 2,
    "/hunger": 3,
    "/health": 1,
}


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
        tmp.write(payload)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = tmp.name
    os.replace(tmp_path, path)


def load_config():
    return load_json(CONFIG_FILE, {})


def load_commands():
    return load_json(GAME_COMMANDS_FILE, [])


def save_commands(data):
    save_json(GAME_COMMANDS_FILE, data)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def recover_stale_executing_commands(commands_data):
    changed = False
    for command_entry in commands_data:
        if command_entry.get("status") != "EXECUTING":
            continue
        command_entry["status"] = "PENDING"
        command_entry["recovered_at"] = now_iso()
        command_entry["error"] = "Recovered from stale EXECUTING status after executor restart"
        changed = True
    return changed


def write_heartbeat(state: str, extra: dict | None = None):
    payload = {
        "executor": "in_game_executor_v2",
        "state": state,
        "timestamp": now_iso(),
        "pid": os.getpid(),
    }
    if extra:
        payload.update(extra)
    save_json(EXECUTOR_HEARTBEAT_FILE, payload)


def type_command(cmd: str):
    pyautogui.press("enter")
    time.sleep(0.25)
    pyautogui.write(cmd)
    time.sleep(0.25)
    pyautogui.press("enter")


def get_delay_overrides():
    cfg = load_config()
    raw = cfg.get("executor_command_delays", {})
    if not isinstance(raw, dict):
        return DEFAULT_POST_SEND_DELAYS
    merged = dict(DEFAULT_POST_SEND_DELAYS)
    for k, v in raw.items():
        try:
            merged[str(k).lower()] = max(0, int(v))
        except Exception:
            continue
    return merged


def get_delay_for_command(command_text: str) -> int:
    normalized = str(command_text or "").strip().lower()
    delays = get_delay_overrides()
    for prefix, delay in delays.items():
        if normalized.startswith(prefix):
            return int(delay)
    return 3


def run_recovery_hook_if_enabled(commands_data, command_entry):
    cfg = load_config()
    if not cfg.get("executor_enable_recovery_hook", False):
        return
    command_type = str(command_entry.get("command_type", "")).lower()
    if command_type != "recovery_command":
        return

    hook_sequence = cfg.get("executor_recovery_macro", [])
    if not isinstance(hook_sequence, list):
        return

    for text in hook_sequence:
        command_text = str(text).strip()
        if not command_text:
            continue
        try:
            type_command(command_text)
            time.sleep(max(0, int(cfg.get("executor_recovery_macro_delay", 2))))
        except Exception:
            continue


def get_pending_group_ids(commands_data):
    grouped = [
        c for c in commands_data
        if c.get("status") == "PENDING" and c.get("claim_group_id")
    ]
    grouped.sort(
        key=lambda c: (
            c.get("created_at") or "",
            str(c.get("claim_group_id")),
            int(c.get("claim_step", 9999)),
            str(c.get("id", "")),
        )
    )
    ordered = []
    seen = set()
    for entry in grouped:
        gid = entry.get("claim_group_id")
        if gid and gid not in seen:
            seen.add(gid)
            ordered.append(gid)
    return ordered


def process_group(commands_data, claim_group_id: str) -> bool:
    changed = False
    group_cmds = [
        c for c in commands_data
        if c.get("claim_group_id") == claim_group_id and c.get("status") == "PENDING"
    ]
    group_cmds.sort(key=lambda c: (int(c.get("claim_step", 9999)), str(c.get("id", ""))))

    for command_entry in group_cmds:
        command_text = command_entry.get("command", "")
        command_entry["status"] = "EXECUTING"
        command_entry["started_at"] = now_iso()
        save_commands(commands_data)
        write_heartbeat("executing", {"group": claim_group_id, "command": command_text})

        try:
            run_recovery_hook_if_enabled(commands_data, command_entry)
            print(f"[EXECUTOR] group={claim_group_id} step={command_entry.get('claim_step')} cmd={command_text}")
            type_command(command_text)
            time.sleep(get_delay_for_command(command_text))
            command_entry["status"] = "DONE"
            command_entry["completed_at"] = now_iso()
            command_entry["error"] = None
            changed = True
            save_commands(commands_data)
        except Exception as e:
            command_entry["status"] = "FAILED"
            command_entry["completed_at"] = now_iso()
            command_entry["error"] = str(e)
            changed = True
            save_commands(commands_data)
            write_heartbeat("error", {"group": claim_group_id, "error": str(e)})
            break

    return changed


def process_legacy(commands_data):
    changed = False
    legacy_pending = [
        c for c in commands_data
        if c.get("status") == "PENDING" and not c.get("claim_group_id")
    ]

    for command_entry in legacy_pending:
        command_text = command_entry.get("command", "")
        command_entry["status"] = "EXECUTING"
        command_entry["started_at"] = now_iso()
        save_commands(commands_data)
        write_heartbeat("executing", {"command": command_text, "type": "legacy"})

        try:
            print(f"[EXECUTOR] legacy cmd={command_text}")
            type_command(command_text)
            time.sleep(get_delay_for_command(command_text))
            command_entry["status"] = "DONE"
            command_entry["completed_at"] = now_iso()
            command_entry["error"] = None
            changed = True
            save_commands(commands_data)
        except Exception as e:
            command_entry["status"] = "FAILED"
            command_entry["completed_at"] = now_iso()
            command_entry["error"] = str(e)
            changed = True
            save_commands(commands_data)
            write_heartbeat("error", {"error": str(e), "type": "legacy"})
            break

    return changed


def main():
    print("[EXECUTOR] IN-GAME EXECUTOR STARTED")
    commands_data = load_commands()
    if recover_stale_executing_commands(commands_data):
        save_commands(commands_data)
        print("[EXECUTOR] recovered stale EXECUTING commands")

    loop_delay = max(1, int(load_config().get("executor_loop_delay_seconds", 2)))

    while True:
        write_heartbeat("idle")
        commands_data = load_commands()
        changed = False

        for group_id in get_pending_group_ids(commands_data):
            changed = process_group(commands_data, group_id) or changed
            commands_data = load_commands()

        changed = process_legacy(commands_data) or changed

        if changed:
            save_commands(commands_data)

        write_heartbeat("sleeping", {"delay": loop_delay})
        time.sleep(loop_delay)


if __name__ == "__main__":
    main()
