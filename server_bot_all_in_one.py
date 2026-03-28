import asyncio
import discord
from discord.ext import commands, tasks
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
import subprocess
import time
import re
import uuid

TOKEN = "MTQ4NjQ2NTQ4ODczNjQ4NTQ0Ng.GSiZhh.u074voiptO6mC4zIuF6lsD2U59V1APSCrrLugg"

DATA_FILE = Path("player_data.json")
STATE_FILE = Path("player_state.json")
LINK_FILE = Path("links.json")
SHOP_FILE = Path("shop.json")
PURCHASES_FILE = Path("purchases.json")
GAME_COMMANDS_FILE = Path("game_commands.json")
REFERRALS_FILE = Path("referrals.json")
CONFIG_FILE = Path("config.json")
LOG_CURSOR_FILE = Path("log_cursor_state.json")

PURCHASE_TIMEOUT_MINUTES = 15
QUEUED_TIMEOUT_MINUTES = 5

DEFAULT_SCAN_INTERVAL = 5
DEFAULT_REWARD_INTERVAL_MINUTES = 60
DEFAULT_REWARD_AMOUNT = 15

REFERRAL_REWARDS = {
    5: 15,
    10: 30,
    20: 60,
}

DINO_CLASS_MAP = {
    "hypsi": ["Hypsilophodon"],
    "dryo": ["Dryosaurus"],
    "pachy": ["Pachycephalosaurus"],
    "beipi": ["Beipiaosaurus"],
    "galli": ["Gallimimus"],
    "tenonto": ["Tenontosaurus"],
    "maia": ["Maiasaura"],
    "dibble": ["Diabloceratops", "Dibble"],
    "stego": ["Stegosaurus"],
    "trike": ["Triceratops"],
    "ptera": ["Pteranodon"],
    "troodon": ["Troodon"],
    "herrera": ["Herrerasaurus"],
    "omni": ["Omniraptor", "Omni"],
    "dilo": ["Dilophosaurus"],
    "carno": ["Carnotaurus"],
    "cera": ["Ceratosaurus"],
    "deino": ["Deinosuchus"],
    "rex": ["Tyrannosaurus", "TRex", "Rex"],
}

CLAIM_PRECHECK_STATES = {"PRECHECK_QUEUED"}
CLAIM_OPEN_STATES = {
    "UNCLAIMED",
    "PRECHECK_QUEUED",
    "PRECHECK_VERIFYING",
    "PRECHECK_PASSED",
    "CLAIM_SEQUENCE_QUEUED",
    "FINAL_VERIFY_PENDING",
}

PRECHECK_VERIFY_TIMEOUT_SECONDS = 10
FINAL_VERIFY_TIMEOUT_SECONDS = 12
LOG_TAIL_FALLBACK_BYTES = 256 * 1024

log_cursor_cache = {
    "path": None,
    "offset": 0,
    "inode": None,
}
health_log_recent = []

announcement_messages = [
    "=== PRIMAL ABYSS ===\nNew Survival Universe\nEarn Energy • !buy & !claim PRIME\ndiscord.gg/HpJVNa69Ww"
]

RCON_SCRIPT = r"C:\Users\joshu\Downloads\The-Isle-Evrima-Server-Tools-main\TheIsle_RCON.py"
RCONCLI_PATH = r"C:\Users\joshu\Documents\EvrimaBot\RconCli\bin\Release\net8.0\RconCli.exe"
RCON_IP = "68.168.208.54"
RCON_PORT = "11218"
RCON_PASSWORD = "qFHrZpel6qwF"
ANNOUNCEMENT_INTERVAL_SECONDS = 600

HEALTH_LOG_PATTERN = re.compile(
    r"used command:\s*(?P<command>\w+).*?\[(?P<steam_id>\d{17})\].*?Class:\s*(?P<class_name>[^,]+),"
    r".*?Previous value:\s*(?P<previous_value>[0-9.]+)%"
    r".*?New value:\s*(?P<new_value>[0-9.]+)%",
    re.IGNORECASE,
)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

invite_cache = {}
online_since = {}
last_minute_tick = {}


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_config():
    return load_json(CONFIG_FILE, {})


def get_scan_interval_seconds() -> int:
    config = load_config()
    value = int(config.get("scan_interval", DEFAULT_SCAN_INTERVAL))
    return max(1, value)


def get_reward_interval_minutes() -> int:
    config = load_config()
    value = int(config.get("interval_minutes", DEFAULT_REWARD_INTERVAL_MINUTES))
    return max(1, value)


def get_reward_amount() -> int:
    config = load_config()
    value = int(config.get("energy_per_interval", DEFAULT_REWARD_AMOUNT))
    return max(1, value)


def load_shop():
    return load_json(SHOP_FILE, {})


def load_purchases():
    return load_json(PURCHASES_FILE, [])


def save_purchases(data):
    save_json(PURCHASES_FILE, data)


def load_game_commands():
    return load_json(GAME_COMMANDS_FILE, [])


def save_game_commands(data):
    save_json(GAME_COMMANDS_FILE, data)


def load_referrals():
    return load_json(REFERRALS_FILE, {})


def save_referrals(data):
    save_json(REFERRALS_FILE, data)


def load_state():
    return load_json(STATE_FILE, {"online_since": {}, "last_minute_tick": {}})


def save_state():
    state = {
        "online_since": online_since,
        "last_minute_tick": last_minute_tick,
    }
    save_json(STATE_FILE, state)


def load_log_cursor_state():
    state = load_json(LOG_CURSOR_FILE, {})
    if not isinstance(state, dict):
        return
    log_cursor_cache["path"] = state.get("path")
    log_cursor_cache["offset"] = int(state.get("offset", 0) or 0)
    log_cursor_cache["inode"] = state.get("inode")


def save_log_cursor_state():
    save_json(LOG_CURSOR_FILE, {
        "path": log_cursor_cache.get("path"),
        "offset": int(log_cursor_cache.get("offset", 0) or 0),
        "inode": log_cursor_cache.get("inode"),
    })


def ensure_referral_record(referrals, discord_id: str):
    if discord_id not in referrals:
        referrals[discord_id] = {
            "count": 0,
            "users": [],
            "rewards": [],
        }


def get_player(ctx):
    links = load_json(LINK_FILE, {})
    data = load_json(DATA_FILE, {})

    steam_id = links.get(str(ctx.author.id))
    if not steam_id:
        return None, None

    return data.get(steam_id), steam_id


def get_player_by_discord_id(discord_id: str):
    links = load_json(LINK_FILE, {})
    data = load_json(DATA_FILE, {})

    steam_id = links.get(discord_id)
    if not steam_id:
        return None, None, data

    return data.get(steam_id), steam_id, data


def find_shop_price(item_name: str):
    shop = load_shop()
    for category, items in shop.items():
        if item_name in items:
            return items[item_name], category
    return None, None


def normalize_class_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


def classes_match(item_key: str, actual_class: str) -> bool:
    aliases = DINO_CLASS_MAP.get(str(item_key or "").lower().strip(), [])
    if not aliases:
        return False

    norm_actual = normalize_class_name(actual_class)
    norm_aliases = {normalize_class_name(x) for x in aliases}
    if norm_actual in norm_aliases:
        return True

    # safe alias fallback: substring-safe normalization match
    return any(norm_actual == alias or norm_actual in alias or alias in norm_actual for alias in norm_aliases)


def get_next_command_id(commands_data):
    if not commands_data:
        return 1

    max_id = 0
    for entry in commands_data:
        try:
            cid = int(str(entry.get("id", "0")).replace("cmd_", ""))
            max_id = max(max_id, cid)
        except Exception:
            pass

    return max_id + 1


def parse_dt(value: str):
    if not value:
        return None

    for fmt in (
        None,
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            if fmt is None:
                return datetime.fromisoformat(value)
            return datetime.strptime(value, fmt)
        except Exception:
            continue

    return None


def parse_health_command_log_line(line: str):
    match = HEALTH_LOG_PATTERN.search(line or "")
    if not match:
        return None

    command = match.group("command")
    if str(command).strip().lower() != "sethealth":
        return None

    try:
        prev_value = float(match.group("previous_value"))
        new_value = float(match.group("new_value"))
    except Exception:
        return None

    event_ts_match = re.search(r"LogTheIsleCommandData:\s*\[(?P<event_ts>[0-9.\-:]+)\]", line or "", re.IGNORECASE)
    event_dt = None
    if event_ts_match:
        ts_raw = event_ts_match.group("event_ts")
        try:
            event_dt = datetime.strptime(ts_raw, "%Y.%m.%d-%H.%M.%S")
        except Exception:
            event_dt = None

    return {
        "steam_id": str(match.group("steam_id")),
        "class_name": str(match.group("class_name")).strip(),
        "previous_value": prev_value,
        "new_value": new_value,
        "command": "SetHealth",
        "event_time": event_dt.isoformat(sep=" ") if event_dt else None,
        "raw_line": line.strip(),
    }


def resolve_log_file_path() -> Path | None:
    config = load_config()
    configured = config.get("server_log_path") or config.get("log_file")
    if configured:
        p = Path(configured)
        if p.exists():
            return p

    candidates = [
        Path("server.log"),
        Path("TheIsle.log"),
        Path("Saved/Logs/TheIsle.log"),
        Path("ShooterGame/Saved/Logs/ShooterGame.log"),
    ]
    for p in candidates:
        if p.exists():
            return p

    logs_dir = Path("Saved/Logs")
    if logs_dir.exists() and logs_dir.is_dir():
        logs = sorted(logs_dir.glob("*.log"), key=lambda x: x.stat().st_mtime, reverse=True)
        if logs:
            return logs[0]

    return None


def _reset_log_cursor_for_path(log_path: Path):
    try:
        stat = log_path.stat()
    except Exception:
        return
    log_cursor_cache["path"] = str(log_path)
    log_cursor_cache["offset"] = 0
    log_cursor_cache["inode"] = getattr(stat, "st_ino", None)
    save_log_cursor_state()


def _read_new_log_lines(log_path: Path):
    path_str = str(log_path)
    try:
        stat = log_path.stat()
    except Exception:
        return []

    current_inode = getattr(stat, "st_ino", None)
    current_size = int(stat.st_size)
    cached_path = log_cursor_cache.get("path")
    cached_inode = log_cursor_cache.get("inode")
    cached_offset = int(log_cursor_cache.get("offset", 0) or 0)

    if cached_path != path_str or (cached_inode is not None and current_inode != cached_inode) or cached_offset > current_size:
        _reset_log_cursor_for_path(log_path)
        cached_offset = 0

    if cached_offset < 0:
        cached_offset = 0

    try:
        with log_path.open("r", encoding="utf-8", errors="ignore") as f:
            f.seek(cached_offset)
            raw = f.read()
            new_offset = f.tell()
    except Exception:
        return []

    log_cursor_cache["path"] = path_str
    log_cursor_cache["offset"] = int(new_offset)
    log_cursor_cache["inode"] = current_inode
    save_log_cursor_state()

    if not raw:
        return []
    return raw.splitlines()


def _read_log_tail_lines(log_path: Path, tail_bytes: int = LOG_TAIL_FALLBACK_BYTES):
    try:
        with log_path.open("rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            seek_to = max(0, file_size - tail_bytes)
            f.seek(seek_to)
            data = f.read()
        return data.decode("utf-8", errors="ignore").splitlines()
    except Exception:
        return []


def refresh_health_log_cache():
    log_path = resolve_log_file_path()
    if not log_path:
        return

    new_lines = _read_new_log_lines(log_path)
    if not new_lines:
        return

    for line in new_lines:
        parsed = parse_health_command_log_line(line)
        if parsed:
            parsed["log_file"] = str(log_path)
            health_log_recent.append(parsed)

    if len(health_log_recent) > 500:
        del health_log_recent[:-500]


def get_latest_health_log_for_steam(steam_id: str, min_event_time: datetime | None = None):
    refresh_health_log_cache()

    for parsed in reversed(health_log_recent):
        if parsed.get("steam_id") != str(steam_id):
            continue
        event_time = parse_dt(parsed.get("event_time"))
        if min_event_time and event_time and event_time < min_event_time:
            continue
        return parsed

    log_path = resolve_log_file_path()
    if not log_path:
        return None

    lines = _read_log_tail_lines(log_path)

    for line in reversed(lines):
        parsed = parse_health_command_log_line(line)
        if not parsed:
            continue
        if parsed["steam_id"] == str(steam_id):
            event_time = parse_dt(parsed.get("event_time"))
            if min_event_time and event_time and event_time < min_event_time:
                continue
            parsed["log_file"] = str(log_path)
            return parsed

    return None


def expire_old_purchases():
    purchases = load_purchases()
    data = load_json(DATA_FILE, {})
    game_commands = load_game_commands()

    now = datetime.now()
    changed_purchases = False
    changed_data = False
    changed_commands = False

    for purchase in purchases:
        status = purchase.get("status")
        steam_id = purchase.get("steam_id")
        item = str(purchase.get("item", "")).lower().strip()

        if status == "UNCLAIMED":
            created_at = parse_dt(purchase.get("time", ""))
            if not created_at:
                continue

            if now - created_at >= timedelta(minutes=PURCHASE_TIMEOUT_MINUTES):
                price, _ = find_shop_price(item)
                if price is not None and steam_id in data:
                    data[steam_id]["energy"] = data[steam_id].get("energy", 0) + price
                    changed_data = True

                purchase["status"] = "EXPIRED"
                purchase["delivery_note"] = f"Expired after {PURCHASE_TIMEOUT_MINUTES} minutes"
                changed_purchases = True

        elif status in {"CLAIM_SEQUENCE_QUEUED", "PRECHECK_QUEUED", "PRECHECK_VERIFYING", "FINAL_VERIFY_PENDING"}:
            claimed_at = parse_dt(purchase.get("claimed_at", "")) or parse_dt(purchase.get("time", ""))
            if not claimed_at:
                continue

            if now - claimed_at >= timedelta(minutes=QUEUED_TIMEOUT_MINUTES):
                for cmd in game_commands:
                    if (
                        cmd.get("steam_id") == steam_id
                        and str(cmd.get("item", "")).lower().strip() == item
                        and cmd.get("status") in {"PENDING", "EXECUTING"}
                    ):
                        cmd["status"] = "EXPIRED"
                        cmd["completed_at"] = str(datetime.now())
                        changed_commands = True

                purchase["status"] = "FAILED"
                purchase["delivery_note"] = f"Claim queue expired after {QUEUED_TIMEOUT_MINUTES} minutes"
                changed_purchases = True

    if changed_purchases:
        save_purchases(purchases)
    if changed_data:
        save_json(DATA_FILE, data)
    if changed_commands:
        save_game_commands(game_commands)


def has_open_purchase(steam_id: str) -> bool:
    purchases = load_purchases()
    return any(
        p.get("steam_id") == steam_id and p.get("status") in CLAIM_OPEN_STATES
        for p in purchases
    )


def get_claimable_purchase_index(purchases, steam_id: str):
    for i in range(len(purchases) - 1, -1, -1):
        p = purchases[i]
        if p.get("steam_id") == steam_id and p.get("status") in {"UNCLAIMED", "WRONG_DINO"}:
            return i, p.get("status")

    for i in range(len(purchases) - 1, -1, -1):
        p = purchases[i]
        if p.get("steam_id") == steam_id and p.get("status") in CLAIM_PRECHECK_STATES.union({"PRECHECK_VERIFYING", "CLAIM_SEQUENCE_QUEUED", "FINAL_VERIFY_PENDING", "PRECHECK_PASSED"}):
            return i, p.get("status")

    return None, None


def get_online_players_from_data():
    data = load_json(DATA_FILE, {})
    online = []

    for steam_id, player in data.items():
        session = int(player.get("current_session_minutes", 0))
        if session > 0:
            online.append({
                "name": player.get("name", "Unknown"),
                "steam_id": steam_id,
                "session": session,
                "total": int(player.get("total_minutes", 0)),
                "energy": int(player.get("energy", 0)),
            })

    return online


def run_rcon(command):
    result = subprocess.run([
        "python",
        RCON_SCRIPT,
        "--ip", RCON_IP,
        "--port", RCON_PORT,
        "--password", RCON_PASSWORD,
        "--command", command
    ], input="\n", capture_output=True, text=True)
    return (result.stdout or "") + (result.stderr or "")


def clean_message(msg):
    return msg.encode("ascii", "ignore").decode()


def send_announcement_silent(message: str):
    cleaned = clean_message(message)
    command = f"announce {cleaned}"

    try:
        result = subprocess.run(
            [
                RCONCLI_PATH,
                RCON_IP,
                RCON_PORT,
                RCON_PASSWORD,
                command,
            ],
            capture_output=True,
            text=True,
        )

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        success = result.returncode == 0 and "Announced:" in stdout
        if not success:
            print(f"[ANNOUNCEMENT STDOUT] {stdout}")
            print(f"[ANNOUNCEMENT STDERR] {stderr}")
            print(f"[ANNOUNCEMENT RETURN CODE] {result.returncode}")
        return success
    except FileNotFoundError:
        print("[ANNOUNCEMENT STDERR] ERROR: RconCli.exe not found")
        print("[ANNOUNCEMENT RETURN CODE] -1")
        return False
    except Exception as e:
        print(f"[ANNOUNCEMENT STDERR] ERROR: {e}")
        print("[ANNOUNCEMENT RETURN CODE] -1")
        return False


def get_players_from_rcon():
    raw = run_rcon("list")
    lines = [l.strip() for l in raw.splitlines() if l.strip()]

    ids, names = None, None

    for line in lines:
        if "," not in line:
            continue

        parts = [p.strip() for p in line.split(",") if p.strip()]

        if all(p.isdigit() for p in parts):
            ids = parts
        else:
            names = parts

    if not ids or not names:
        return {}

    return {ids[i]: names[i] for i in range(min(len(ids), len(names)))}


def restore_state():
    state = load_state()
    saved_online_since = state.get("online_since", {})
    saved_last_tick = state.get("last_minute_tick", {})

    if isinstance(saved_online_since, dict):
        online_since.update({str(k): int(v) for k, v in saved_online_since.items()})
    if isinstance(saved_last_tick, dict):
        last_minute_tick.update({str(k): int(v) for k, v in saved_last_tick.items()})


def update_players(players):
    data = load_json(DATA_FILE, {})
    now = int(time.time())
    current_ids = set(players.keys())

    for steam_id, name in players.items():
        if steam_id not in data:
            data[steam_id] = {
                "name": name,
                "steam_id": steam_id,
                "total_minutes": 0,
                "current_session_minutes": 0,
                "energy": 0,
                "sessions": 0,
            }

        data[steam_id]["name"] = name
        data[steam_id]["steam_id"] = steam_id

        if steam_id not in online_since:
            online_since[steam_id] = now
            last_minute_tick[steam_id] = now
            data[steam_id]["sessions"] = int(data[steam_id].get("sessions", 0)) + 1

        session_minutes = (now - online_since[steam_id]) // 60
        data[steam_id]["current_session_minutes"] = int(session_minutes)

    for steam_id in list(online_since.keys()):
        if steam_id not in current_ids:
            if steam_id in data:
                data[steam_id]["current_session_minutes"] = 0
            del online_since[steam_id]
            last_minute_tick.pop(steam_id, None)

    save_json(DATA_FILE, data)
    save_state()


def tick_rewards():
    data = load_json(DATA_FILE, {})
    now = int(time.time())

    reward_interval_minutes = get_reward_interval_minutes()
    reward_amount = get_reward_amount()

    for steam_id in list(online_since.keys()):
        if steam_id not in data:
            continue

        last_tick = last_minute_tick.get(steam_id, now)
        elapsed = now - last_tick

        if elapsed < 60:
            continue

        whole_minutes = elapsed // 60
        if whole_minutes <= 0:
            continue

        player = data[steam_id]

        old_total = int(player.get("total_minutes", 0))
        new_total = old_total + whole_minutes

        old_rewards = old_total // reward_interval_minutes
        new_rewards = new_total // reward_interval_minutes
        gained_energy = (new_rewards - old_rewards) * reward_amount

        player["total_minutes"] = new_total
        player["current_session_minutes"] = int((now - online_since[steam_id]) // 60)

        if gained_energy > 0:
            player["energy"] = int(player.get("energy", 0)) + gained_energy
            print(
                f"[REWARD] {player.get('name', steam_id)} | "
                f"{steam_id} | +{gained_energy} energy | "
                f"total={player['total_minutes']} mins | "
                f"energy={player['energy']}"
            )

        last_minute_tick[steam_id] = last_tick + (whole_minutes * 60)

    save_json(DATA_FILE, data)
    save_state()


def print_live_status(players):
    data = load_json(DATA_FILE, {})

    print(f"\n--- Online: {len(players)} ---")
    for steam_id, name in players.items():
        p = data.get(steam_id, {})
        session = int(p.get("current_session_minutes", 0))
        total = int(p.get("total_minutes", 0))
        energy = int(p.get("energy", 0))

        print(
            f"{name} | "
            f"{steam_id} | "
            f"session={session} mins | "
            f"total={total} mins | "
            f"energy={energy}"
        )


def find_purchase_by_group(purchases, claim_group_id: str):
    for purchase in purchases:
        if purchase.get("claim_group_id") == claim_group_id:
            return purchase
    return None


def has_pending_group_commands(commands_data, claim_group_id: str):
    if not claim_group_id:
        return False
    return any(
        c.get("claim_group_id") == claim_group_id
        and c.get("status") in {"PENDING", "EXECUTING"}
        for c in commands_data
    )


def all_group_steps_done(commands_data, claim_group_id: str, expected_phase: str):
    group_cmds = [
        c for c in commands_data
        if c.get("claim_group_id") == claim_group_id and c.get("claim_phase") == expected_phase
    ]
    if not group_cmds:
        return False

    return all(c.get("status") == "DONE" for c in group_cmds)


def any_group_step_failed(commands_data, claim_group_id: str):
    return any(
        c.get("claim_group_id") == claim_group_id and c.get("status") == "FAILED"
        for c in commands_data
    )


def cancel_claim_group_commands(commands_data, claim_group_id: str, reason: str):
    changed = False
    for command_entry in commands_data:
        if command_entry.get("claim_group_id") != claim_group_id:
            continue
        if command_entry.get("status") in {"PENDING", "EXECUTING"}:
            command_entry["status"] = "CANCELLED"
            command_entry["completed_at"] = str(datetime.now())
            command_entry["error"] = reason
            changed = True
    return changed


def refund_purchase_energy_if_needed(purchase, reason_suffix: str):
    if purchase.get("refund_applied"):
        return False

    steam_id = purchase.get("steam_id")
    item = str(purchase.get("item", "")).lower().strip()
    price, _ = find_shop_price(item)
    if price is None:
        return False

    data = load_json(DATA_FILE, {})
    if steam_id not in data:
        return False

    data[steam_id]["energy"] = int(data[steam_id].get("energy", 0)) + int(price)
    save_json(DATA_FILE, data)

    purchase["refund_applied"] = True
    purchase["refund_amount"] = int(price)
    purchase["refunded_at"] = str(datetime.now())
    purchase["refund_note"] = f"Wrong dino detected. Energy refunded. {reason_suffix}".strip()
    return True


def queue_claim_phase_commands(purchase, player_name: str, phase: str):
    game_commands = load_game_commands()
    next_id = get_next_command_id(game_commands)
    steam_id = purchase["steam_id"]
    item = str(purchase.get("item", "")).lower().strip()
    claim_group_id = purchase.get("claim_group_id")

    if phase == "PRECHECK":
        sequence = [f"/health {steam_id} 100"]
    else:
        sequence = [
            f"/elder {steam_id} prime",
            f"/hunger {steam_id} 100",
            f"/hunger {steam_id} 30",
            f"/elder {steam_id} prime",
            f"/hunger {steam_id} 100",
            f"/health {steam_id} 100",
        ]

    for idx, command_text in enumerate(sequence, start=1):
        game_commands.append({
            "id": f"cmd_{next_id + idx - 1:03d}",
            "steam_id": steam_id,
            "player_name": player_name,
            "item": item,
            "command": command_text,
            "status": "PENDING",
            "created_at": str(datetime.now()),
            "completed_at": None,
            "claim_group_id": claim_group_id,
            "claim_step": idx,
            "claim_final": idx == len(sequence),
            "claim_phase": phase,
        })

    save_game_commands(game_commands)
    return sequence


def process_claim_orchestration():
    purchases = load_purchases()
    game_commands = load_game_commands()
    changed_purchases = False
    changed_commands = False

    refresh_health_log_cache()

    for purchase in purchases:
        status = purchase.get("status")
        claim_group_id = purchase.get("claim_group_id")
        steam_id = purchase.get("steam_id")
        item = str(purchase.get("item", "")).lower().strip()

        if status == "PRECHECK_QUEUED" and claim_group_id:
            if any_group_step_failed(game_commands, claim_group_id):
                purchase["status"] = "FAILED"
                purchase["delivery_note"] = "Pre-check command execution failed"
                changed_purchases = True
                continue

            if has_pending_group_commands(game_commands, claim_group_id):
                continue

            if all_group_steps_done(game_commands, claim_group_id, "PRECHECK"):
                purchase["status"] = "PRECHECK_VERIFYING"
                purchase["precheck_verify_started_at"] = str(datetime.now())
                purchase["delivery_note"] = "Pre-check command done. Waiting for SetHealth verification log."
                changed_purchases = True
            continue

        if status == "PRECHECK_VERIFYING" and claim_group_id:
            verify_started_at = parse_dt(purchase.get("precheck_verify_started_at"))
            min_event_time = None
            if verify_started_at:
                min_event_time = verify_started_at - timedelta(seconds=2)

            precheck_log = get_latest_health_log_for_steam(steam_id, min_event_time=min_event_time)
            if not precheck_log:
                if verify_started_at and (datetime.now() - verify_started_at).total_seconds() >= PRECHECK_VERIFY_TIMEOUT_SECONDS:
                    purchase["status"] = "FAILED"
                    purchase["delivery_note"] = "Pre-check timed out: no SetHealth verification log found."
                    purchase["failure_note"] = "⚠️ Verification timed out. No grow was applied."
                    changed_purchases = True
                continue

            if not classes_match(item, precheck_log["class_name"]):
                reason = f"Claim blocked: expected {item}, detected class {precheck_log['class_name']}."
                cancel_reason = f"Claim group cancelled: wrong dino detected ({precheck_log['class_name']})"
                if cancel_claim_group_commands(game_commands, claim_group_id, cancel_reason):
                    changed_commands = True
                refund_purchase_energy_if_needed(purchase, reason)
                purchase["status"] = "WRONG_DINO_REFUNDED"
                purchase["failure_note"] = f"{reason} Energy refunded."
                purchase["delivery_note"] = "❌ Claim blocked: wrong dino detected. Your energy has been refunded."
                changed_purchases = True
                continue

            purchase["status"] = "PRECHECK_PASSED"
            purchase["delivery_note"] = (
                f"Pre-check verified class {precheck_log['class_name']} via {precheck_log['command']}"
            )
            purchase["failure_note"] = "✅ Verification passed. Grow sequence queued."
            changed_purchases = True

            player_name = purchase.get("player") or "Unknown"
            claim_sequence = queue_claim_phase_commands(purchase, player_name, "CLAIM")
            purchase["status"] = "CLAIM_SEQUENCE_QUEUED"
            purchase["delivery_note"] = " | ".join(claim_sequence)
            changed_purchases = True
            game_commands = load_game_commands()
            continue

        if status == "CLAIM_SEQUENCE_QUEUED" and claim_group_id:
            if any_group_step_failed(game_commands, claim_group_id):
                purchase["status"] = "FAILED"
                purchase["delivery_note"] = "Claim sequence command execution failed"
                changed_purchases = True
                continue

            if has_pending_group_commands(game_commands, claim_group_id):
                continue

            if all_group_steps_done(game_commands, claim_group_id, "CLAIM"):
                purchase["status"] = "FINAL_VERIFY_PENDING"
                purchase["final_verify_started_at"] = str(datetime.now())
                changed_purchases = True
                continue

        if status == "FINAL_VERIFY_PENDING":
            final_started_at = parse_dt(purchase.get("final_verify_started_at"))
            min_event_time = None
            if final_started_at:
                min_event_time = final_started_at - timedelta(seconds=2)

            final_log = get_latest_health_log_for_steam(steam_id, min_event_time=min_event_time)
            if not final_log:
                if final_started_at and (datetime.now() - final_started_at).total_seconds() >= FINAL_VERIFY_TIMEOUT_SECONDS:
                    purchase["status"] = "FAILED"
                    purchase["delivery_note"] = "Final verify timed out: no SetHealth verification log found."
                    changed_purchases = True
                continue

            if final_log["steam_id"] != str(steam_id):
                purchase["status"] = "FAILED"
                purchase["delivery_note"] = "Final verify failed: Steam ID mismatch"
                changed_purchases = True
                continue

            if not classes_match(item, final_log["class_name"]):
                purchase["status"] = "FAILED"
                purchase["delivery_note"] = f"Final verify failed: class mismatch ({final_log['class_name']})"
                changed_purchases = True
                continue

            if abs(float(final_log["new_value"]) - 100.0) > 0.000001:
                purchase["status"] = "FAILED"
                purchase["delivery_note"] = f"Final verify failed: health ended at {final_log['new_value']:.6f}%"
                changed_purchases = True
                continue

            purchase["status"] = "DELIVERED"
            purchase["delivery_note"] = (
                f"Claim completed and verified: class {final_log['class_name']} | new health {final_log['new_value']:.6f}%"
            )
            purchase["failure_note"] = None
            changed_purchases = True

    if changed_commands:
        save_game_commands(game_commands)
    if changed_purchases:
        save_purchases(purchases)


def process_game_command_queue():
    # Command execution ownership is handled exclusively by in_game_executor.py.
    # This bot-side function only orchestrates claim-state transitions from metadata/logs.
    process_claim_orchestration()


async def cache_guild_invites(guild: discord.Guild):
    try:
        invites = await guild.invites()
        invite_cache[guild.id] = {invite.code: invite.uses for invite in invites}
    except Exception:
        if guild.id not in invite_cache:
            invite_cache[guild.id] = {}


def reward_referral_if_eligible(inviter_id: str, guild: discord.Guild):
    referrals = load_referrals()
    ensure_referral_record(referrals, inviter_id)

    record = referrals[inviter_id]
    rewarded_levels = set(record.get("rewards", []))
    gained_messages = []

    for invite_count, energy_reward in sorted(REFERRAL_REWARDS.items()):
        reward_key = str(invite_count)
        if record.get("count", 0) >= invite_count and reward_key not in rewarded_levels:
            player, steam_id, data = get_player_by_discord_id(inviter_id)
            if player and steam_id and steam_id in data:
                data[steam_id]["energy"] = int(data[steam_id].get("energy", 0)) + energy_reward
                save_json(DATA_FILE, data)
                record["rewards"].append(reward_key)
                gained_messages.append(
                    f"🎉 <@{inviter_id}> reached **{invite_count} invites** and earned **+{energy_reward} energy**!"
                )

    save_referrals(referrals)

    if gained_messages:
        general_channel = discord.utils.get(guild.text_channels, name="general")
        if general_channel:
            return gained_messages, general_channel

    return [], None


@tasks.loop(seconds=1)
async def tracking_loop():
    try:
        players = await asyncio.to_thread(get_players_from_rcon)
        update_players(players)
        tick_rewards()
        expire_old_purchases()
        await asyncio.to_thread(process_game_command_queue)
        print_live_status(players)
    except Exception as e:
        print(f"[ERROR] tracking loop failed: {e}")


@tasks.loop(seconds=ANNOUNCEMENT_INTERVAL_SECONDS)
async def announcement_loop():
    try:
        message = announcement_messages[0]
        success = await asyncio.to_thread(send_announcement_silent, message)
        if success:
            print("[ANNOUNCEMENT SUCCESS]")
        else:
            print("[ANNOUNCEMENT FAILED]")
    except Exception as e:
        print(f"[ERROR] announcement loop failed: {e}")


@bot.event
async def on_ready():
    print(f"[BOT STARTED] Logged in as {bot.user}")
    restore_state()
    load_log_cursor_state()

    if not tracking_loop.is_running():
        tracking_loop.change_interval(seconds=get_scan_interval_seconds())
        tracking_loop.start()
        print("[TRACKING STARTED] background tracking loop online")

    if not announcement_loop.is_running():
        announcement_loop.start()
    print("[ANNOUNCEMENTS STARTED]")

    for guild in bot.guilds:
        await cache_guild_invites(guild)


@announcement_loop.before_loop
async def before_announcement_loop():
    await bot.wait_until_ready()
    await asyncio.sleep(ANNOUNCEMENT_INTERVAL_SECONDS)


@bot.event
async def on_guild_join(guild):
    await cache_guild_invites(guild)


@bot.event
async def on_member_join(member):
    if member.bot:
        return

    general_channel = discord.utils.get(member.guild.text_channels, name="general")
    if general_channel:
        await general_channel.send(
            f"👋 Welcome {member.mention} to Primal Abyss!\n"
            f"⚡ Earn energy by playing\n"
            f"🔗 Use !link <steamid>"
        )

    if datetime.now(timezone.utc) - member.created_at < timedelta(days=1):
        await cache_guild_invites(member.guild)
        return

    previous_invites = invite_cache.get(member.guild.id, {})
    used_inviter_id = None

    try:
        current_invites = await member.guild.invites()
    except Exception:
        current_invites = []

    if current_invites:
        for invite in current_invites:
            previous_uses = previous_invites.get(invite.code, 0)
            if invite.uses > previous_uses and invite.inviter:
                used_inviter_id = str(invite.inviter.id)
                break

        invite_cache[member.guild.id] = {invite.code: invite.uses for invite in current_invites}

    if not used_inviter_id:
        return

    if used_inviter_id == str(member.id):
        return

    referrals = load_referrals()
    ensure_referral_record(referrals, used_inviter_id)
    record = referrals[used_inviter_id]

    if str(member.id) in record["users"]:
        save_referrals(referrals)
        return

    record["users"].append(str(member.id))
    record["count"] = len(record["users"])
    save_referrals(referrals)

    gained_messages, reward_channel = reward_referral_if_eligible(used_inviter_id, member.guild)
    if reward_channel and gained_messages:
        for message in gained_messages:
            await reward_channel.send(message)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    raise error


@bot.command()
async def link(ctx, steam_id: str):
    links = load_json(LINK_FILE, {})
    links[str(ctx.author.id)] = steam_id
    save_json(LINK_FILE, links)
    await ctx.send(f"✅ Linked to {steam_id}")


@bot.command()
async def stats(ctx):
    expire_old_purchases()

    player, steam_id = get_player(ctx)
    if not player:
        await ctx.send("❌ Use !link first")
        return

    previous_total = int(player.get("total_minutes", 0))
    current_session = int(player.get("current_session_minutes", 0))
    combined_total = previous_total + current_session
    energy = int(player.get("energy", 0))
    name = player.get("name", "Unknown")

    await ctx.send(
        f"📊 **{name}**\n"
        f"🆔 Steam ID: `{steam_id}`\n"
        f"⏱ Current session: {current_session} mins\n"
        f"🕒 Previously played: {previous_total} mins\n"
        f"📈 Total tracked: {combined_total} mins\n"
        f"⚡ Energy: {energy}"
    )


@bot.command()
async def online(ctx):
    expire_old_purchases()

    players = get_online_players_from_data()
    if not players:
        await ctx.send("📭 No tracked players are currently online.")
        return

    lines = [f"🟢 **Online Players ({len(players)})**\n"]
    for p in players:
        lines.append(
            f"**{p['name']}**\n"
            f"🆔 `{p['steam_id']}`\n"
            f"⏱ Session: {p['session']} mins\n"
            f"🕒 Previous total: {p['total']} mins\n"
            f"⚡ Energy: {p['energy']}\n"
        )

    await ctx.send("\n".join(lines))


@bot.command()
async def shop(ctx):
    expire_old_purchases()

    shop_data = load_shop()
    msg = "🛒 **Primal Abyss Shop**\n\n"

    for cat, items in shop_data.items():
        msg += f"**{cat.upper()}**\n"
        for item, price in items.items():
            msg += f"{item} — ⚡ {price}\n"
        msg += "\n"

    await ctx.send(msg)


@bot.command()
async def buy(ctx, item: str):
    expire_old_purchases()

    data = load_json(DATA_FILE, {})
    purchases = load_purchases()
    player, steam_id = get_player(ctx)

    if not player:
        await ctx.send("❌ Use !link first")
        return

    item = item.lower().strip()
    price, category = find_shop_price(item)

    if price is None:
        await ctx.send("❌ Item not found")
        return

    if category == "extras":
        await ctx.send("❌ Extras are not part of the prime claim flow")
        return

    if has_open_purchase(steam_id):
        await ctx.send("❌ You already have an active purchase. Use `!claim` first.")
        return

    if player.get("energy", 0) < price:
        await ctx.send("❌ Not enough energy")
        return

    player["energy"] -= price
    save_json(DATA_FILE, data)

    purchases.append({
        "player": player["name"],
        "steam_id": steam_id,
        "item": item,
        "status": "UNCLAIMED",
        "time": str(datetime.now()),
        "claimed_at": None,
        "delivery_note": None,
        "failure_note": None,
        "claim_group_id": None,
        "refund_applied": False,
        "refund_amount": 0,
        "refunded_at": None,
        "refund_note": None,
    })
    save_purchases(purchases)

    await ctx.send(
        f"🧬 **{item.upper()} PURCHASED**\n\n"
        f"⚡ -{price} energy\n"
        f"📦 Claim saved\n"
        f"⏳ Expires in {PURCHASE_TIMEOUT_MINUTES} minutes if not claimed\n\n"
        f"Use `!claim` when you are ready to be primed."
    )


@bot.command()
async def claim(ctx):
    expire_old_purchases()

    player, steam_id = get_player(ctx)

    if not player:
        await ctx.send("❌ Use !link first")
        return

    purchases = load_purchases()
    purchase_index, purchase_status = get_claimable_purchase_index(purchases, steam_id)

    if purchase_index is None:
        latest_mine = None
        for p in reversed(purchases):
            if p.get("steam_id") == steam_id:
                latest_mine = p
                break
        if latest_mine and latest_mine.get("status") == "WRONG_DINO_REFUNDED":
            await ctx.send(
                "❌ Claim blocked: wrong dino detected on your last attempt. "
                "Your energy has been refunded. Switch dinos and buy again when ready."
            )
            return
        if latest_mine and latest_mine.get("status") == "FAILED":
            note = latest_mine.get("delivery_note") or "⚠️ Verification timed out. No grow was applied."
            await ctx.send(f"⚠️ {note}")
            return
        await ctx.send("❌ You do not have any active dinosaur purchases.")
        return

    purchase = purchases[purchase_index]

    if purchase_status in {"PRECHECK_QUEUED", "PRECHECK_VERIFYING", "PRECHECK_PASSED", "CLAIM_SEQUENCE_QUEUED", "FINAL_VERIFY_PENDING"}:
        await ctx.send(
            f"⏳ Your claim is already being processed.\n\n"
            f"🧬 Dino: **{purchase['item'].upper()}**\n"
            f"Status: **{purchase.get('status', 'UNKNOWN')}**\n"
            f"Use `!myclaims` to check status."
        )
        return

    game_commands = load_game_commands()
    existing_pending = any(
        cmd.get("steam_id") == steam_id
        and str(cmd.get("item", "")).lower().strip() == str(purchase.get("item", "")).lower().strip()
        and cmd.get("status") in {"PENDING", "EXECUTING"}
        for cmd in game_commands
    )
    if existing_pending:
        purchase["status"] = "PRECHECK_QUEUED"
        purchase["claimed_at"] = str(datetime.now())
        purchase["delivery_note"] = "Existing pending command found"
        if not purchase.get("claim_group_id"):
            purchase["claim_group_id"] = f"claim_{steam_id}_{uuid.uuid4().hex[:10]}"
        save_purchases(purchases)

        await ctx.send("⏳ Health/class verification queued. Stay on the dino you bought.")
        return

    if not purchase.get("claim_group_id"):
        purchase["claim_group_id"] = f"claim_{steam_id}_{uuid.uuid4().hex[:10]}"

    queued = queue_claim_phase_commands(purchase, player["name"], "PRECHECK")
    purchase["status"] = "PRECHECK_QUEUED"
    purchase["claimed_at"] = str(datetime.now())
    purchase["delivery_note"] = " | ".join(queued)
    purchase["failure_note"] = None
    save_purchases(purchases)

    await ctx.send(
        "🩺 **PRE-CHECK QUEUED**\n\n"
        "Health/class verification queued. Stay on the dino you bought.\n"
        f"1. `{queued[0]}`"
    )


@bot.command()
async def myclaims(ctx):
    expire_old_purchases()

    player, steam_id = get_player(ctx)

    if not player:
        await ctx.send("❌ Use !link first")
        return

    purchases = load_purchases()
    mine = [p for p in purchases if p.get("steam_id") == steam_id]

    if not mine:
        await ctx.send("📭 You have no purchases.")
        return

    lines = ["📦 **Your Purchases**\n"]
    for p in mine[-10:]:
        extra_note = p.get("failure_note") or p.get("delivery_note") or ""
        lines.append(
            f"{p.get('item', '?')} — {p.get('status', '?')} — {p.get('time', '?')}"
            + (f" — {extra_note}" if extra_note else "")
        )

    await ctx.send("\n".join(lines))


@bot.command()
async def invites(ctx):
    referrals = load_referrals()
    discord_id = str(ctx.author.id)
    ensure_referral_record(referrals, discord_id)
    save_referrals(referrals)

    record = referrals[discord_id]
    await ctx.send(
        f"🔗 **{ctx.author.display_name}** has **{record.get('count', 0)}** valid invites.\n"
        f"🏆 Reward milestones: 5 / 10 / 20"
    )


@bot.command()
async def leaderboard(ctx):
    referrals = load_referrals()

    leaderboard_rows = []
    for discord_id, record in referrals.items():
        leaderboard_rows.append((discord_id, int(record.get("count", 0))))

    if not leaderboard_rows:
        await ctx.send("📭 No invite referrals tracked yet.")
        return

    leaderboard_rows.sort(key=lambda x: x[1], reverse=True)
    top_five = leaderboard_rows[:5]

    lines = ["🏆 **Top Inviters**\n"]
    for idx, (discord_id, count) in enumerate(top_five, start=1):
        user = bot.get_user(int(discord_id))
        display_name = user.name if user else f"User {discord_id}"
        lines.append(f"{idx}. **{display_name}** — {count} invites")

    await ctx.send("\n".join(lines))


if __name__ == "__main__":
    bot.run(TOKEN)
