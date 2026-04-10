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
PLAYER_STATE_FILE = Path("player_state.json")

DEFAULT_POST_SEND_DELAYS = {
    "/elder": 3,
    "/growth": 1,
    "/diet1": 0,
    "/diet2": 0,
    "/diet3": 0,
    "/hunger": 0,
    "/thirst": 1,
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


def is_bot_in_game():
    state = load_json(PLAYER_STATE_FILE, {})
    return str(state.get("bot_presence_state", "")).strip().upper() == "BOT_IN_GAME"


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
            return max(0, int(delay))
    return 3


def is_command_expired(command_entry: dict):
    max_age_seconds = command_entry.get("max_age_seconds")
    if max_age_seconds in (None, "", 0):
        return False
    try:
        max_age_seconds = int(max_age_seconds)
    except Exception:
        return False
    created_at = command_entry.get("created_at")
    try:
        created_dt = datetime.fromisoformat(str(created_at))
    except Exception:
        return False
    age = (datetime.now(timezone.utc) - created_dt).total_seconds() if created_dt.tzinfo else (datetime.now() - created_dt).total_seconds()
    return age > max_age_seconds


def get_pending_group_ids(commands_data):
    grouped = [
        c for c in commands_data
        if c.get("status") == "PENDING" and c.get("claim_group_id")
    ]
    group_priority = {}
    for c in grouped:
        gid = str(c.get("claim_group_id"))
        p = int(c.get("priority", 0) or 0)
        group_priority[gid] = max(group_priority.get(gid, 0), p)
    grouped.sort(
        key=lambda c: (
            -int(group_priority.get(str(c.get("claim_group_id")), 0)),
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
        cmd_id = command_entry.get("id")
        step = command_entry.get("step_index", command_entry.get("claim_step"))
        phase = command_entry.get("phase") or command_entry.get("claim_phase")
        command_text = command_entry.get("command", "")
        if is_command_expired(command_entry):
            command_entry["status"] = "EXPIRED"
            command_entry["completed_at"] = now_iso()
            command_entry["error"] = "Command skipped due to max_age_seconds"
            changed = True
            continue
        if bool(command_entry.get("requires_bot_in_game", False)) and not is_bot_in_game():
            command_entry["status"] = "SKIPPED"
            command_entry["completed_at"] = now_iso()
            command_entry["error"] = "Skipped because bot is not confirmed in-game"
            changed = True
            print("[EXECUTOR] processing sustain command skipped (bot not in game)")
            continue

        command_entry["status"] = "EXECUTING"
        command_entry["started_at"] = now_iso()
        print(f"[EXEC] {command_text}")
        save_commands(commands_data)
        write_heartbeat("executing", {"group": claim_group_id, "command": command_text})

        try:
            ctype = str(command_entry.get("command_type", "")).lower()
            if ctype in {"recovery", "recovery_command"}:
                command_entry["status"] = "SKIPPED"
                command_entry["completed_at"] = now_iso()
                command_entry["error"] = "Recovery automation removed"
                changed = True
                save_commands(commands_data)
                continue
            elif ctype in {"sustain", "sustain_command"}:
                print("[EXECUTOR] processing sustain command")
                type_command(command_text)
                time.sleep(get_delay_for_command(command_text))
            else:
                print(f"[EXECUTOR] group={claim_group_id} step={command_entry.get('claim_step')} cmd={command_text}")
                type_command(command_text)
                time.sleep(get_delay_for_command(command_text))
            command_entry["status"] = "DONE"
            command_entry["completed_at"] = now_iso()
            command_entry["error"] = None
            print(f"[DONE] {command_text}")
            changed = True
            save_commands(commands_data)
            break
        except Exception as e:
            command_entry["status"] = "FAILED"
            command_entry["completed_at"] = now_iso()
            command_entry["error"] = str(e)
            print(f"[FAIL] {command_text} error={e}")
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
        if (
            str(command_entry.get("command_type", "")).lower() == "claim_command"
            or command_entry.get("phase")
            or command_entry.get("claim_phase")
        ):
            print(f"[EXECUTOR ERROR] claim command fell into legacy path cmd_id={command_entry.get('id')} command={command_text}")
        command_entry["status"] = "EXECUTING"
        command_entry["started_at"] = now_iso()
        save_commands(commands_data)
        write_heartbeat("executing", {"command": command_text, "type": "legacy"})

        try:
            print(f"[EXEC] {command_text}")
            type_command(command_text)
            time.sleep(get_delay_for_command(command_text))
            command_entry["status"] = "DONE"
            command_entry["completed_at"] = now_iso()
            command_entry["error"] = None
            changed = True
            print(f"[DONE] {command_text}")
            save_commands(commands_data)
        except Exception as e:
            command_entry["status"] = "FAILED"
            command_entry["completed_at"] = now_iso()
            command_entry["error"] = str(e)
            changed = True
            print(f"[FAIL] {command_text} error={e}")
            save_commands(commands_data)
            write_heartbeat("error", {"error": str(e), "type": "legacy"})
            break

    return changed


def main():
    print("[EXECUTOR] IN-GAME EXECUTOR STARTED")
    commands_data = load_commands()
    if recover_stale_executing_commands(commands_data):
        save_commands(commands_data)
        print("[EXECUTOR] stale command recovered on startup")

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
