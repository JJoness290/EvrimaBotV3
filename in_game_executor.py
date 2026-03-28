import json
import time
from datetime import datetime
from pathlib import Path

import pyautogui

GAME_COMMANDS_FILE = Path("game_commands.json")

POST_SEND_DELAYS = {
    "/elder": 5,
    "/growth": 3,
    "/diet1": 2,
    "/diet2": 2,
    "/diet3": 2,
    "/hunger": 3,
    "/health": 1,
}


def load_commands():
    if GAME_COMMANDS_FILE.exists():
        return json.loads(GAME_COMMANDS_FILE.read_text(encoding="utf-8"))
    return []


def save_commands(data):
    GAME_COMMANDS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def type_command(cmd: str):
    pyautogui.press("enter")
    time.sleep(0.25)
    pyautogui.write(cmd)
    time.sleep(0.25)
    pyautogui.press("enter")


def get_delay_for_command(command_text: str) -> int:
    normalized = str(command_text or "").strip().lower()
    for prefix, delay in POST_SEND_DELAYS.items():
        if normalized.startswith(prefix):
            return delay
    return 3


def get_pending_claim_groups(commands_data):
    pending_group_cmds = [
        c for c in commands_data
        if c.get("status") == "PENDING" and c.get("claim_group_id")
    ]
    if not pending_group_cmds:
        return []

    pending_group_cmds.sort(
        key=lambda c: (
            c.get("created_at") or "",
            str(c.get("claim_group_id")),
            int(c.get("claim_step", 9999)),
            str(c.get("id", "")),
        )
    )
    ordered_groups = []
    seen = set()
    for cmd in pending_group_cmds:
        gid = cmd.get("claim_group_id")
        if gid and gid not in seen:
            seen.add(gid)
            ordered_groups.append(gid)
    return ordered_groups


def process_group(commands_data, claim_group_id: str) -> bool:
    changed = False
    group_cmds = [
        c for c in commands_data
        if c.get("claim_group_id") == claim_group_id and c.get("status") == "PENDING"
    ]

    group_cmds.sort(
        key=lambda c: (
            int(c.get("claim_step", 9999)),
            str(c.get("id", "")),
        )
    )

    for command_entry in group_cmds:
        command_text = command_entry.get("command", "")
        command_entry["status"] = "EXECUTING"
        save_commands(commands_data)

        try:
            print(f"Executing group={claim_group_id} step={command_entry.get('claim_step')} cmd={command_text}")
            type_command(command_text)
            time.sleep(get_delay_for_command(command_text))
            command_entry["status"] = "DONE"
            command_entry["completed_at"] = datetime.now().isoformat(sep=" ", timespec="seconds")
            command_entry["error"] = None
            changed = True
            save_commands(commands_data)
        except Exception as e:
            command_entry["status"] = "FAILED"
            command_entry["completed_at"] = datetime.now().isoformat(sep=" ", timespec="seconds")
            command_entry["error"] = str(e)
            changed = True
            save_commands(commands_data)
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
        save_commands(commands_data)

        try:
            print(f"Executing legacy cmd={command_text}")
            type_command(command_text)
            time.sleep(get_delay_for_command(command_text))
            command_entry["status"] = "DONE"
            command_entry["completed_at"] = datetime.now().isoformat(sep=" ", timespec="seconds")
            command_entry["error"] = None
            changed = True
            save_commands(commands_data)
        except Exception as e:
            command_entry["status"] = "FAILED"
            command_entry["completed_at"] = datetime.now().isoformat(sep=" ", timespec="seconds")
            command_entry["error"] = str(e)
            changed = True
            save_commands(commands_data)
            break

    return changed


def main():
    print("IN-GAME EXECUTOR STARTED")

    while True:
        commands_data = load_commands()
        changed = False

        pending_groups = get_pending_claim_groups(commands_data)
        if pending_groups:
            for group_id in pending_groups:
                loop_changed = process_group(commands_data, group_id)
                changed = changed or loop_changed
                commands_data = load_commands()

        legacy_changed = process_legacy(commands_data)
        changed = changed or legacy_changed

        if changed:
            save_commands(commands_data)

        time.sleep(2)


if __name__ == "__main__":
    main()
