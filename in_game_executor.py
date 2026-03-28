import json
import time
import pyautogui
from pathlib import Path

GAME_COMMANDS_FILE = Path("game_commands.json")

def load_commands():
    if GAME_COMMANDS_FILE.exists():
        return json.loads(GAME_COMMANDS_FILE.read_text())
    return []

def save_commands(data):
    GAME_COMMANDS_FILE.write_text(json.dumps(data, indent=2))

def type_command(cmd):
    # Open chat
    pyautogui.press("enter")
    time.sleep(0.2)

    # Type command
    pyautogui.write(cmd)
    time.sleep(0.2)

    # Send
    pyautogui.press("enter")

def main():
    print("IN-GAME EXECUTOR STARTED")

    while True:
        commands = load_commands()
        changed = False

        for cmd in commands:
            if cmd.get("status") == "PENDING":
                command_text = cmd.get("command")

                print(f"Executing: {command_text}")
                cmd["status"] = "EXECUTING"
                save_commands(commands)

                try:
                    type_command(command_text)
                    cmd["status"] = "DONE"
                    cmd["completed_at"] = str(time.time())
                except Exception as e:
                    cmd["status"] = "FAILED"
                    cmd["completed_at"] = str(time.time())
                    cmd["error"] = str(e)

                changed = True
                save_commands(commands)
                time.sleep(3)  # delay between commands

        if changed:
            save_commands(commands)

        time.sleep(3)

if __name__ == "__main__":
    main()
