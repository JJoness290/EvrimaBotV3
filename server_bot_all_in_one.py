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
import os
import socket
import struct
import stat
import threading
import tempfile
from zoneinfo import ZoneInfo
from typing import Any

import paramiko

TOKEN = ""

DATA_FILE = Path("player_data.json")
STATE_FILE = Path("player_state.json")
LINK_FILE = Path("links.json")
SHOP_FILE = Path("shop.json")
PURCHASES_FILE = Path("purchases.json")
GAME_COMMANDS_FILE = Path("game_commands.json")
REFERRALS_FILE = Path("referrals.json")
CONFIG_FILE = Path("config.json")
EXECUTOR_HEARTBEAT_FILE = Path("executor_heartbeat.json")
CONFIG_DEBUG_LOGGED = False

PURCHASE_TIMEOUT_MINUTES = 15
CLAIM_QUEUE_TIMEOUT_MINUTES = 2

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

PRECHECK_VERIFY_TIMEOUT_SECONDS = 20
FINAL_VERIFY_TIMEOUT_SECONDS = 20
REMOTE_LOG_TAIL_BYTES = 16 * 1024
CLAIM_ACTIVE_TIMEOUT_SECONDS = 90

last_remote_log_match = {}
cached_resolved_remote_log_path = None
last_remote_log_match_raw_line_by_steam = {}
last_remote_grow_match = {}
last_sftp_eof_warn_at = 0.0

announcement_messages = [
    "=== PRIMAL ABYSS ===\nNew Survival Universe\nEarn Energy • !buy & !claim PRIME\ndiscord.gg/HpJVNa69Ww"
]

RCON_SCRIPT = r"C:\Users\joshu\Downloads\The-Isle-Evrima-Server-Tools-main\TheIsle_RCON.py"
RCONCLI_PATH = r"C:\Users\joshu\Documents\EvrimaBot\RconCli\bin\Release\net8.0\RconCli.exe"
RCON_IP = "68.168.208.54"
RCON_PORT = "11218"
RCON_PASSWORD = ""
ANNOUNCEMENT_INTERVAL_SECONDS = 600

TOKEN = os.getenv("DISCORD_TOKEN", TOKEN)
RCON_PASSWORD = os.getenv("RCON_PASSWORD", RCON_PASSWORD)

HEALTH_LOG_PATTERN = re.compile(
    r"used command:\s*(?P<command>\w+).*?\[(?P<steam_id>\d{17})\].*?Class:\s*(?P<class_name>[^,]+),"
    r".*?Previous value:\s*(?P<previous_value>[0-9.]+)%"
    r".*?New value:\s*(?P<new_value>[0-9.]+)%",
    re.IGNORECASE,
)
GROW_LOG_PATTERN = re.compile(
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
MAIN_LOOP = None

DEFAULT_RESTART_TIMES = ["00:00", "06:00", "12:00", "18:00"]
RESTART_WARN_MINUTES = [3, 2, 1]
LONDON_TZ = ZoneInfo("Europe/London")
restart_cycle_state = {}
server_health_state = {
    "status": "ONLINE",
    "fail_count": 0,
    "success_count": 0,
    "last_status_at": None,
    "last_health_poll": 0.0,
    "last_skip_log_at": 0.0,
}
patreon_role_cache = {}
last_role_cache_refresh = 0.0
DEFAULT_ENERGY_RATE_PER_HOUR = 15.0
PATREON_TIER_RATES = {
    "supporter": 18.0,
    "vip": 22.5,
    "apex supporter": 30.0,
}
BOT_STATE_IN_GAME = "BOT_IN_GAME"
BOT_STATE_MISSING = "BOT_MISSING"
BOT_STATE_WAITING_SERVER = "BOT_WAITING_FOR_SERVER"

SERVER_STATE_ONLINE = "SERVER_ONLINE"
SERVER_STATE_RESTARTING = "SERVER_RESTARTING"
SERVER_STATE_SUSPECTED_DOWN = "SERVER_SUSPECTED_DOWN"
SERVER_STATE_DOWN = "SERVER_DOWN"
SERVER_STATE_RECOVERING = "SERVER_RECOVERING"

bot_runtime_state = {
    "presence_state": BOT_STATE_MISSING,
    "server_state": SERVER_STATE_ONLINE,
    "admin_bot_state": "UNKNOWN",
    "missing_since": None,
    "last_bot_seen_at": 0.0,
    "last_detection_source": "Unknown",
    "last_player_count": 0,
    "last_rcon_check_at": 0.0,
    "last_rcon_error": "",
    "startup_started_at": 0.0,
    "startup_tracking_checked": False,
    "startup_rcon_checked": False,
    "startup_warmup_complete_logged": False,
    "startup_warmup_banner_logged": False,
    "startup_initialized": False,
    "last_sustain_at": 0.0,
    "last_presence_log_at": 0.0,
    "empty_playerlist_logged_at": 0.0,
    "log_suppression": {},
    "last_tracking_summary_at": 0.0,
    "last_successful_players": {},
    "last_player_poll_success_at": 0.0,
    "last_player_poll_player_count": 0,
    "last_player_poll_source": "unknown",
    "last_player_poll_failed_at": 0.0,
    "last_player_poll_ok": False,
}
admin_runtime_state = {
    "outage_active": False,
    "outage_started_at": None,
    "outage_issue_count": 0,
    "manual_issues": [],
    "last_alert_sent_at": None,
    "offline_reminder_sent_at": 0.0,
    "last_seen_in_game_at": None,
    "last_dashboard_refresh_at": 0.0,
    "last_alert_summary": "None",
}

PLAYER_DATA_LOCK = threading.RLock()
PURCHASES_LOCK = threading.RLock()
GAME_COMMANDS_LOCK = threading.RLock()
ECONOMY_LOCK = threading.RLock()


class ConfigManager:
    @staticmethod
    def get(config_key: str, env_name: str | None = None, default: Any = None):
        if env_name:
            env_value = os.getenv(env_name)
            if env_value not in (None, ""):
                return env_value
        config = load_config()
        return config.get(config_key, default)

    @staticmethod
    def get_int(config_key: str, env_name: str | None = None, default: int = 0, minimum: int | None = None):
        raw = ConfigManager.get(config_key, env_name, default)
        try:
            value = int(raw)
        except Exception:
            value = int(default)
        if minimum is not None:
            value = max(minimum, value)
        return value

    @staticmethod
    def get_bool(config_key: str, env_name: str | None = None, default: bool = False):
        raw = ConfigManager.get(config_key, env_name, default)
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        return text in {"1", "true", "yes", "on"}

    @staticmethod
    def get_section(section_key: str):
        config = load_config()
        section = config.get(section_key, {})
        return section if isinstance(section, dict) else {}


def load_json(path: Path, default):
    with ECONOMY_LOCK:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return default
        return default


def save_json(path: Path, data) -> None:
    with ECONOMY_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, indent=2)
        with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = tmp.name
        os.replace(tmp_path, path)


def load_config():
    global CONFIG_DEBUG_LOGGED
    config = load_json(CONFIG_FILE, {})
    if not isinstance(config, dict):
        config = {}
    if not CONFIG_DEBUG_LOGGED:
        try:
            abs_path = str(CONFIG_FILE.resolve())
        except Exception:
            abs_path = str(CONFIG_FILE)
        raw_presence = config.get("bot_presence", {})
        print(f"[CONFIG] loading from: {abs_path}")
        print(f"[CONFIG] bot_presence raw: {raw_presence}")
        CONFIG_DEBUG_LOGGED = True
    return config


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


def get_starting_energy() -> int:
    return ConfigManager.get_int("starting_energy", "STARTING_ENERGY", 100, minimum=0)


def get_reward_interval_seconds() -> int:
    return ConfigManager.get_int("reward_interval_seconds", "REWARD_INTERVAL_SECONDS", 60, minimum=10)


def get_max_reward_catchup_minutes() -> int:
    return ConfigManager.get_int("max_reward_catchup_minutes", "MAX_REWARD_CATCHUP_MINUTES", 60, minimum=1)


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
    return load_json(
        STATE_FILE,
        {
            "online_since": {},
            "last_minute_tick": {},
            "restart_cycle_state": {},
            "bot_presence_state": BOT_STATE_MISSING,
            "last_bot_seen_at": 0.0,
        },
    )


def save_state():
    state = {
        "online_since": online_since,
        "last_minute_tick": last_minute_tick,
        "restart_cycle_state": restart_cycle_state,
        "bot_presence_state": bot_runtime_state.get("presence_state", BOT_STATE_MISSING),
        "last_bot_seen_at": float(bot_runtime_state.get("last_bot_seen_at", 0.0) or 0.0),
    }
    save_json(STATE_FILE, state)


def get_env_or_config(env_name: str, config_key: str, default=None):
    env_value = os.getenv(env_name)
    if env_value not in (None, ""):
        return env_value
    config = load_config()
    cfg_value = config.get(config_key, default)
    return cfg_value


def hydrate_runtime_secrets():
    global TOKEN, RCON_PASSWORD
    TOKEN = str(ConfigManager.get("discord_token", "DISCORD_TOKEN", TOKEN or "") or "")
    RCON_PASSWORD = str(ConfigManager.get("rcon_password", "RCON_PASSWORD", RCON_PASSWORD or "") or "")


def get_remote_log_config():
    host = get_env_or_config("PINGPLAYERS_SFTP_HOST", "sftp_host", "68.168.208.54")
    port = int(get_env_or_config("PINGPLAYERS_SFTP_PORT", "sftp_port", 11216) or 11216)
    username = get_env_or_config("PINGPLAYERS_SFTP_USERNAME", "sftp_username", "server26449")
    password = get_env_or_config("PINGPLAYERS_SFTP_PASSWORD", "sftp_password", "LYcxz02dLm")
    remote_log_path = get_env_or_config(
        "PINGPLAYERS_REMOTE_LOG_PATH",
        "remote_log_path",
        "TheIsle/Saved/Logs/TheIsle.log",
    )
    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "remote_log_path": remote_log_path,
    }


def open_sftp_client(remote_cfg):
    transport = paramiko.Transport((remote_cfg["host"], int(remote_cfg["port"])))
    transport.connect(
        username=remote_cfg["username"],
        password=remote_cfg["password"],
    )
    sftp = paramiko.SFTPClient.from_transport(transport)
    return transport, sftp


def _remote_file_exists(sftp, remote_path: str) -> bool:
    try:
        attrs = sftp.stat(remote_path)
        return not stat.S_ISDIR(attrs.st_mode)
    except Exception:
        return False


def _normalize_path_variants(configured_path: str):
    path = str(configured_path or "").strip()
    variants = []
    if path:
        variants.append(path)
        variants.append("/" + path.lstrip("/"))
        variants.append("./" + path.lstrip("./"))
        if path.startswith("TheIsle/"):
            stripped = path[len("TheIsle/"):]
            variants.extend([
                stripped,
                "/" + stripped.lstrip("/"),
                "./" + stripped.lstrip("./"),
            ])
    variants.extend([
        "server/TheIsle/Saved/Logs/TheIsle.log",
        "./server/TheIsle/Saved/Logs/TheIsle.log",
        "/server/TheIsle/Saved/Logs/TheIsle.log",
        "server/Saved/Logs/TheIsle.log",
        "./server/Saved/Logs/TheIsle.log",
        "/server/Saved/Logs/TheIsle.log",
        "server/TheIsle.log",
        "./server/TheIsle.log",
        "/server/TheIsle.log",
        "Saved/Logs/TheIsle.log",
        "./Saved/Logs/TheIsle.log",
        "/Saved/Logs/TheIsle.log",
        "TheIsle.log",
        "./TheIsle.log",
    ])

    seen = set()
    deduped = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            deduped.append(v)
    return deduped


def _is_log_file(name: str) -> bool:
    return str(name or "").lower().endswith(".log")


def _is_preferred_log_name(name: str) -> bool:
    lname = str(name or "").lower()
    return lname in {"theisle.log", "shootergame.log"}


def _score_log_candidate(path_name: str, attrs) -> tuple:
    p = str(path_name or "")
    lname = p.lower()
    base = p.rsplit("/", 1)[-1].lower()
    in_logs_dir = "logs" in lname
    is_theisle = base == "theisle.log"
    is_shooter = base == "shootergame.log"
    mtime = int(getattr(attrs, "st_mtime", 0) or 0)
    # Higher is better, mtime secondary.
    return (
        3 if is_theisle and in_logs_dir else
        2 if is_shooter and in_logs_dir else
        1 if in_logs_dir else
        0,
        mtime,
    )


def resolve_remote_log_path(sftp, configured_path: str):
    global cached_resolved_remote_log_path

    if cached_resolved_remote_log_path:
        if _remote_file_exists(sftp, cached_resolved_remote_log_path):
            print(f"[CLAIM] using cached remote log path: {cached_resolved_remote_log_path}")
            return cached_resolved_remote_log_path
        cached_resolved_remote_log_path = None

    for candidate in _normalize_path_variants(configured_path):
        log_debug("SFTP", f"Trying remote path: {candidate}", flag="debug_sftp")
        if _remote_file_exists(sftp, candidate):
            cached_resolved_remote_log_path = candidate
            log_debug("SFTP", f"Found remote log path: {candidate}", flag="debug_sftp")
            return candidate

    candidate_dirs = [
        ".",
        "./server",
        "./server/TheIsle",
        "./server/Saved",
        "./server/Saved/Logs",
        "./TheIsle",
        "./Saved",
        "./Saved/Logs",
        "/",
        "/server",
        "/server/TheIsle",
        "/server/Saved",
        "/server/Saved/Logs",
        "/TheIsle",
        "/TheIsle/Saved",
        "/TheIsle/Saved/Logs",
    ]

    discovered = []
    for d in candidate_dirs:
        log_debug("SFTP", f"Scanning candidate directory: {d}", flag="debug_sftp")
        try:
            entries = sftp.listdir_attr(d)
        except Exception:
            log_debug("SFTP", f"Candidate directory missing: {d}", flag="debug_sftp")
            continue

        for entry in entries:
            name = entry.filename
            full_path = f"{d.rstrip('/')}/{name}" if d not in {".", "/"} else (name if d == "." else f"/{name}")
            if _is_log_file(name):
                discovered.append((full_path, entry))
            elif stat.S_ISDIR(entry.st_mode) and "log" in name.lower():
                try:
                    sub_entries = sftp.listdir_attr(full_path)
                    for sub in sub_entries:
                        if _is_log_file(sub.filename):
                            sub_path = f"{full_path.rstrip('/')}/{sub.filename}"
                            discovered.append((sub_path, sub))
                except Exception:
                    continue

    if discovered:
        preferred = [c for c in discovered if _is_preferred_log_name(c[0].rsplit("/", 1)[-1])]
        ranked = preferred if preferred else discovered
        ranked.sort(key=lambda x: _score_log_candidate(x[0], x[1]), reverse=True)
        best_path = ranked[0][0]
        if _remote_file_exists(sftp, best_path):
            cached_resolved_remote_log_path = best_path
            log_debug("SFTP", f"Auto-discovered remote log path: {best_path}", flag="debug_sftp")
            return best_path

    # Diagnostics only on failure.
    try:
        cwd = sftp.getcwd()
        log_warn("SFTP", f"Path resolution failed. SFTP cwd: {cwd}")
    except Exception:
        log_warn("SFTP", "Path resolution failed. Could not read SFTP cwd.")

    for d in [".", "/", "./Saved", "./Saved/Logs", "/TheIsle/Saved/Logs"]:
        try:
            names = sftp.listdir(d)
            preview = ", ".join(names[:15])
            log_debug("SFTP", f"Directory snapshot {d}: {preview}", flag="debug_sftp")
        except Exception:
            continue

    return None


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


def get_steam_id_for_discord(discord_id: str, links_data=None):
    links = links_data if isinstance(links_data, dict) else load_json(LINK_FILE, {})
    return links.get(str(discord_id))


def get_latest_player_record_by_discord_id(discord_id: str):
    links = load_json(LINK_FILE, {})
    data = load_json(DATA_FILE, {})
    steam_id = get_steam_id_for_discord(discord_id, links)
    if not steam_id:
        return None, None, data, links
    return data.get(steam_id), steam_id, data, links


def get_player_by_discord_id(discord_id: str):
    links = load_json(LINK_FILE, {})
    data = load_json(DATA_FILE, {})

    steam_id = links.get(discord_id)
    if not steam_id:
        return None, None, data

    return data.get(steam_id), steam_id, data


async def refresh_patreon_role_cache(force: bool = False):
    global last_role_cache_refresh
    now = time.time()
    ttl = ConfigManager.get_int("patreon_role_cache_ttl_seconds", "PATREON_ROLE_CACHE_TTL_SECONDS", 120, minimum=30)
    if not force and (now - last_role_cache_refresh) < ttl:
        return

    links = load_json(LINK_FILE, {})
    updated = {}
    for discord_id, steam_id in links.items():
        member = None
        for guild in bot.guilds:
            try:
                member = guild.get_member(int(discord_id)) or await guild.fetch_member(int(discord_id))
            except Exception:
                member = None
            if member:
                break
        rate = DEFAULT_ENERGY_RATE_PER_HOUR
        tier_name = "Default"
        if member:
            names = {str(role.name).strip().lower() for role in getattr(member, "roles", [])}
            for tier_key, tier_rate in PATREON_TIER_RATES.items():
                if tier_key in names:
                    rate = float(tier_rate)
                    tier_name = tier_key.title()
        updated[str(steam_id)] = {"rate_per_hour": rate, "tier": tier_name, "discord_id": str(discord_id)}

    patreon_role_cache.clear()
    patreon_role_cache.update(updated)
    last_role_cache_refresh = now


def get_player_energy_rate_per_hour(steam_id: str):
    info = patreon_role_cache.get(str(steam_id), {})
    try:
        return float(info.get("rate_per_hour", DEFAULT_ENERGY_RATE_PER_HOUR))
    except Exception:
        return DEFAULT_ENERGY_RATE_PER_HOUR


def adjust_energy_in_data(data: dict, steam_id: str, delta: int):
    if steam_id not in data:
        return None, None
    before = int(data[steam_id].get("energy", 0))
    after = max(0, before + int(delta))
    data[steam_id]["energy"] = after
    return before, after


def deduct_player_energy(steam_id: str, amount: int, reason: str = ""):
    if amount < 0:
        amount = abs(amount)
    data = load_json(DATA_FILE, {})
    if steam_id not in data:
        return False, None, None
    before = int(data[steam_id].get("energy", 0))
    if before < int(amount):
        return False, before, before
    _, after = adjust_energy_in_data(data, steam_id, -int(amount))
    if reason:
        data[steam_id]["last_energy_note"] = f"{reason} @ {datetime.now()}"
    save_json(DATA_FILE, data)
    return True, before, after


def refund_player_energy(steam_id: str, amount: int, reason: str = ""):
    if amount < 0:
        amount = abs(amount)
    data = load_json(DATA_FILE, {})
    if steam_id not in data:
        return False, None, None
    before, after = adjust_energy_in_data(data, steam_id, int(amount))
    if reason:
        data[steam_id]["last_energy_note"] = f"{reason} @ {datetime.now()}"
    save_json(DATA_FILE, data)
    return True, before, after


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

    command = str(match.group("command")).strip().lower()
    if command not in {"sethealth", "health"}:
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


def parse_grow_command_log_line(line: str):
    match = GROW_LOG_PATTERN.search(line or "")
    if not match:
        return None

    command = str(match.group("command")).strip().lower()
    if command != "grow":
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
        "command": "Grow",
        "event_time": event_dt.isoformat(sep=" ") if event_dt else None,
        "raw_line": line.strip(),
    }


def read_remote_log_tail(tail_bytes: int = REMOTE_LOG_TAIL_BYTES):
    global last_sftp_eof_warn_at
    cfg = get_remote_log_config()
    if not all([cfg.get("host"), cfg.get("username"), cfg.get("password"), cfg.get("remote_log_path")]):
        log_warn("SFTP", "Missing SFTP config values (host/username/password/remote_log_path).")
        return [], "missing_sftp_config"

    transport = None
    sftp = None
    try:
        open_start = time.time()
        log_debug("SFTP", f"Connecting host={cfg['host']} port={cfg['port']} user={cfg['username']}", flag="debug_sftp")
        transport, sftp = open_sftp_client(cfg)
        open_elapsed = time.time() - open_start
        log_debug("SFTP", f"open_sftp duration={open_elapsed:.3f}s", flag="debug_sftp")
        remote_path = resolve_remote_log_path(sftp, cfg["remote_log_path"])
        if not remote_path:
            log_warn("SFTP", "Could not resolve remote log path.")
            return [], "remote_log_not_found"
        log_debug("SFTP", "Connected to remote log", flag="debug_sftp")
        log_debug("SFTP", f"Reading tail from {remote_path}", flag="debug_sftp")
        read_start_ts = time.time()
        with sftp.open(remote_path, "rb") as remote_file:
            remote_file.seek(0, 2)
            size = remote_file.tell()
            read_start = max(0, int(size) - int(tail_bytes))
            remote_file.seek(read_start)
            raw = remote_file.read()
        read_elapsed = time.time() - read_start_ts
        log_debug("SFTP", f"read_tail duration={read_elapsed:.3f}s bytes={len(raw)}", flag="debug_sftp")
        decoded = raw.decode("utf-8", errors="ignore")
        return decoded.splitlines(), None
    except Exception as e:
        msg = str(e or "")
        lowered = msg.lower()
        if "eof" in lowered:
            now_ts = time.time()
            if now_ts - float(last_sftp_eof_warn_at or 0.0) > 60:
                log_warn("SFTP", f"transient EOF while reading remote log tail: {e}")
                last_sftp_eof_warn_at = now_ts
            return [], "transient_eof"
        log_error("SFTP", f"Failed reading remote log tail: {e}")
        return [], str(e)
    finally:
        try:
            if sftp:
                sftp.close()
        except Exception:
            pass
        try:
            if transport:
                transport.close()
        except Exception:
            pass


def get_latest_health_log_for_steam(steam_id: str):
    lines, err = read_remote_log_tail(REMOTE_LOG_TAIL_BYTES)
    if err:
        print(f"[CLAIM] waiting for health log for steam_id={steam_id} err={err}")
        return None

    newest_match = None
    for line in reversed(lines):
        parsed = parse_health_command_log_line(line)
        if not parsed:
            continue
        if parsed["steam_id"] != str(steam_id):
            continue
        log_debug("SFTP", f"Found candidate SetHealth line for steam_id={steam_id}", flag="debug_sftp")
        newest_match = parsed
        break

    if newest_match:
        last_remote_log_match[str(steam_id)] = newest_match
        last_remote_log_match_raw_line_by_steam[str(steam_id)] = newest_match.get("raw_line")
        print(f"[CLAIM] health log found class={newest_match.get('class_name')} steam_id={steam_id}")
        return newest_match

    print(f"[CLAIM] parser found no valid health line for steam_id={steam_id}")
    return last_remote_log_match.get(str(steam_id))


def get_latest_grow_log_for_steam(steam_id: str):
    lines, err = read_remote_log_tail(REMOTE_LOG_TAIL_BYTES)
    if err:
        print(f"[CLAIM] waiting for grow log for steam_id={steam_id} err={err}")
        return None

    newest_match = None
    for line in reversed(lines):
        parsed = parse_grow_command_log_line(line)
        if not parsed:
            continue
        if parsed["steam_id"] != str(steam_id):
            continue
        newest_match = parsed
        break

    if newest_match:
        last_remote_grow_match[str(steam_id)] = newest_match
        print(f"[CLAIM] grow log found class={newest_match.get('class_name')} steam_id={steam_id}")
        return newest_match

    print(f"[CLAIM] parser found no valid grow line for steam_id={steam_id}")
    return last_remote_grow_match.get(str(steam_id))


def verify_growth_log_for_purchase(purchase, grow_log):
    if not grow_log:
        return False, "No Grow verification log found."
    if str(grow_log.get("steam_id")) != str(purchase.get("steam_id")):
        return False, "Grow verification failed: player mismatch."
    if str(grow_log.get("command", "")).lower() != "grow":
        return False, "Grow verification failed: wrong command in log."

    item = str(purchase.get("item", "")).lower().strip()
    if not classes_match(item, grow_log.get("class_name", "")):
        return False, f"Grow verification failed: expected {item}, detected {grow_log.get('class_name', 'Unknown')}."

    try:
        new_value = float(grow_log.get("new_value", 0))
    except Exception:
        return False, "Grow verification failed: invalid growth value."

    normalized_growth = new_value / 100.0 if new_value > 1.0 else new_value
    if not (0.64 <= normalized_growth <= 0.66):
        return False, f"Grow verification failed: growth ended at {new_value:.6f}%."

    return True, f"Growth confirmed at {new_value:.6f}% for {grow_log.get('class_name', 'Unknown')}."


def get_claim_status_display(status: str):
    mapping = {
        "UNCLAIMED": ("Not claimed yet", 0),
        "PRECHECK_QUEUED": ("Pre-check queued", 20),
        "PRECHECK_VERIFYING": ("Verification in progress", 35),
        "PRECHECK_PASSED": ("Verification passed", 50),
        "CLAIM_SEQUENCE_QUEUED": ("Growth queued", 75),
        "FINAL_VERIFY_PENDING": ("Final verification in progress", 90),
        "DELIVERED": ("Claim completed", 100),
        "WRONG_DINO": ("Refunded", None),
        "WRONG_DINO_REFUNDED": ("Refunded", None),
        "CANCELLED_TIMEOUT": ("Failed", None),
        "FAILED": ("Failed", None),
        "EXPIRED": ("Expired", None),
    }
    return mapping.get(status, ("In progress", None))


def clean_claim_note_for_user(note: str):
    text = str(note or "").strip()
    if not text:
        return ""
    text = re.sub(r"/[a-z0-9]+\s+\d{5,}\s+\S+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d{17}\b", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" -|")
    if not text:
        return ""
    return text


def mask_steam_id(steam_id: str):
    value = str(steam_id or "")
    if len(value) < 8:
        return value
    return f"{value[:4]}••••{value[-4:]}"


def render_progress_bar(pct: int | None):
    if pct is None:
        return "██████░░░░ ~"
    pct = max(0, min(100, int(pct)))
    filled = int(round(pct / 10))
    return f"{'█' * filled}{'░' * (10 - filled)} {pct}%"


def build_claim_progress_embed(purchase):
    status = purchase.get("status")
    label, pct = get_claim_status_display(status)
    item = str(purchase.get("item", "dino")).upper()
    result = clean_claim_note_for_user(
        purchase.get("failure_note") or purchase.get("delivery_note") or label
    ) or label
    mention = ""
    if purchase.get("progress_user_id"):
        mention = f"<@{purchase.get('progress_user_id')}>"

    if status == "DELIVERED":
        color = discord.Color.green()
        pct = 100
    elif status in {"FAILED", "WRONG_DINO", "WRONG_DINO_REFUNDED", "CANCELLED_TIMEOUT"}:
        color = discord.Color.red()
    else:
        color = discord.Color.blurple()

    embed = discord.Embed(title="Claim Status", color=color)
    embed.add_field(name="Dino", value=item, inline=True)
    embed.add_field(name="Stage", value=label, inline=True)
    embed.add_field(name="Progress", value=render_progress_bar(pct), inline=False)
    embed.add_field(name="Result", value=result[:1000], inline=False)
    if mention:
        embed.add_field(name="Player", value=mention, inline=True)
    embed.add_field(name="Steam", value=mask_steam_id(purchase.get("steam_id", "")), inline=True)
    embed.timestamp = datetime.now(timezone.utc)
    return embed


async def _update_claim_progress_message_async(purchase_snapshot: dict):
    channel_id = purchase_snapshot.get("progress_channel_id")
    message_id = purchase_snapshot.get("progress_message_id")
    if not channel_id:
        return

    embed = build_claim_progress_embed(purchase_snapshot)
    try:
        channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
        if message_id:
            try:
                message = await channel.fetch_message(int(message_id))
                await message.edit(content=None, embed=embed)
                return
            except Exception:
                pass

        sent = await channel.send(embed=embed)
        with ECONOMY_LOCK:
            purchases = load_purchases()
            for p in purchases:
                if p.get("claim_group_id") == purchase_snapshot.get("claim_group_id"):
                    p["progress_channel_id"] = int(channel_id)
                    p["progress_message_id"] = int(sent.id)
                    p["progress_guild_id"] = purchase_snapshot.get("progress_guild_id")
                    p["progress_user_id"] = purchase_snapshot.get("progress_user_id")
                    break
            save_purchases(purchases)
    except Exception:
        return


def queue_claim_progress_message_update(purchase_snapshot: dict):
    if not MAIN_LOOP:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            _update_claim_progress_message_async(dict(purchase_snapshot)),
            MAIN_LOOP,
        )
    except Exception:
        return


def set_purchase_status(purchase: dict, new_status: str, delivery_note: str | None = None, failure_note: str | None = None):
    old_status = purchase.get("status")
    purchase["status"] = new_status
    if delivery_note is not None:
        purchase["delivery_note"] = delivery_note
    if failure_note is not None:
        purchase["failure_note"] = failure_note
    if old_status != new_status:
        queue_claim_progress_message_update(purchase)


def get_discord_id_for_steam_id(steam_id: str):
    links = load_json(LINK_FILE, {})
    target = str(steam_id or "").strip()
    if not target:
        return None
    for discord_id, linked_steam in links.items():
        if str(linked_steam or "").strip() == target:
            return str(discord_id)
    return None


async def _notify_claim_timeout_refund_async(discord_id: str, message: str):
    did = str(discord_id or "").strip()
    if not did:
        return False
    user_obj = None
    try:
        user_obj = bot.get_user(int(did)) or await bot.fetch_user(int(did))
    except Exception:
        user_obj = None
    if user_obj:
        try:
            await user_obj.send(message)
            return True
        except Exception:
            pass
    fallback = await get_admin_alert_channel()
    if fallback:
        try:
            await fallback.send(f"<@{did}> {message}")
            return True
        except Exception:
            pass
    return False


def notify_claim_timeout_refund(steam_id: str, message: str):
    discord_id = get_discord_id_for_steam_id(steam_id)
    if not discord_id:
        return False
    if MAIN_LOOP is None:
        return False
    try:
        future = asyncio.run_coroutine_threadsafe(
            _notify_claim_timeout_refund_async(discord_id, message),
            MAIN_LOOP,
        )

        def _done(fut):
            try:
                ok = bool(fut.result())
            except Exception:
                ok = False
            if ok:
                log_info("CLAIM TIMEOUT", f"notified player discord_id={discord_id}")
            else:
                log_warn("CLAIM TIMEOUT", f"notify failed discord_id={discord_id}")

        future.add_done_callback(_done)
        return True
    except Exception:
        return False


def expire_claim_commands_for_purchase(game_commands: list, purchase: dict):
    steam_id = str(purchase.get("steam_id", "")).strip()
    item = str(purchase.get("item", "")).lower().strip()
    claim_group_id = str(purchase.get("claim_group_id", "") or "").strip()
    expired_count = 0
    for cmd in game_commands:
        if str(cmd.get("status", "")).upper() not in {"PENDING", "EXECUTING"}:
            continue
        grouped_match = bool(claim_group_id) and str(cmd.get("claim_group_id", "")).strip() == claim_group_id
        fallback_match = (
            (str(cmd.get("steam_id", "")).strip() == steam_id)
            and (str(cmd.get("item", "")).lower().strip() == item)
            and (
                str(cmd.get("command_type", "")).lower() in {"claim", "precheck"}
                or str(cmd.get("claim_phase", "")).upper() in {"PRECHECK", "RECOVERY", "CLAIM", "FINAL_VERIFY"}
            )
        )
        if not grouped_match and not fallback_match:
            continue
        cmd["status"] = "EXPIRED"
        cmd["completed_at"] = str(datetime.now())
        cmd["error"] = "Claim queue timed out after 2 minutes."
        expired_count += 1
    return expired_count


def refund_timed_out_claim_purchase(purchase: dict, data: dict):
    if purchase.get("refund_applied"):
        return False
    steam_id = str(purchase.get("steam_id", "")).strip()
    item = str(purchase.get("item", "")).lower().strip()
    price, _ = find_shop_price(item)
    if price is None or steam_id not in data:
        return False
    _, _ = adjust_energy_in_data(data, steam_id, int(price))
    purchase["refund_applied"] = True
    purchase["refund_amount"] = int(price)
    purchase["refunded_at"] = str(datetime.now())
    purchase["refund_note"] = "Claim timed out after 2 minutes. Energy refunded automatically."
    purchase["timeout_reason"] = "claim_queue_timeout"
    return True


def expire_old_purchases():
    with ECONOMY_LOCK:
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
                    if price is not None and steam_id in data and not purchase.get("refund_applied"):
                        _, _ = adjust_energy_in_data(data, steam_id, int(price))
                        purchase["refund_applied"] = True
                        purchase["refund_amount"] = int(price)
                        purchase["refunded_at"] = str(datetime.now())
                        purchase["refund_note"] = "Unclaimed purchase expired. Energy refunded."
                        changed_data = True

                    purchase["status"] = "EXPIRED"
                    purchase["delivery_note"] = f"Expired after {PURCHASE_TIMEOUT_MINUTES} minutes"
                    changed_purchases = True

            elif status in {"CLAIM_SEQUENCE_QUEUED", "PRECHECK_QUEUED", "PRECHECK_VERIFYING", "FINAL_VERIFY_PENDING", "PRECHECK_PASSED"}:
                claimed_at = parse_dt(purchase.get("claimed_at", "")) or parse_dt(purchase.get("time", ""))
                if not claimed_at:
                    continue

                if now - claimed_at >= timedelta(minutes=CLAIM_QUEUE_TIMEOUT_MINUTES):
                    if purchase.get("timeout_reason") == "claim_queue_timeout":
                        continue
                    log_info("CLAIM TIMEOUT", f"purchase timed out steam={steam_id} item={item}")
                    expired_count = expire_claim_commands_for_purchase(game_commands, purchase)
                    if expired_count > 0:
                        changed_commands = True
                        log_info("CLAIM TIMEOUT", f"commands expired count={expired_count}")

                    refund_applied = refund_timed_out_claim_purchase(purchase, data)
                    if refund_applied:
                        changed_data = True
                        log_info("CLAIM TIMEOUT", f"refunded {int(purchase.get('refund_amount', 0) or 0)} energy steam={steam_id}")

                    timeout_note = "⚠️ Claim timed out after 2 minutes. Your energy has been refunded. Please run !claim again."
                    purchase["status"] = "FAILED"
                    purchase["delivery_note"] = timeout_note
                    purchase["failure_note"] = timeout_note
                    purchase["timeout_reason"] = "claim_queue_timeout"

                    if not purchase.get("timeout_notified"):
                        notify_claim_timeout_refund(steam_id, timeout_note)
                        purchase["timeout_notified"] = True
                    changed_purchases = True

        # Timeout behavior examples:
        # - queued claim >2 minutes -> FAILED + refunded + notified + related commands expired.
        # - second expire pass -> no second refund, no second timeout notification.
        # - UNCLAIMED purchase expiry still follows PURCHASE_TIMEOUT_MINUTES path.
        # - successful claim completed before 2 minutes -> not touched by timeout branch.

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
    def _noise_line(line: str) -> bool:
        l = str(line or "").strip().lower()
        if not l:
            return True
        noise_prefixes = (
            "tcp connection established with server",
            "sending:",
            "password accepted",
            "[info",
            "connected to",
        )
        return any(l.startswith(p) for p in noise_prefixes)

    def _clean_response(raw_text: str):
        lines = [str(x).rstrip() for x in str(raw_text or "").splitlines()]
        useful = [ln for ln in lines if not _noise_line(ln)]
        return "\n".join(useful).strip()

    def _is_usable(cleaned_text: str):
        if not cleaned_text:
            return False
        lines = [ln.strip() for ln in cleaned_text.splitlines() if ln.strip()]
        if any(re.search(r"\d{17}", ln) for ln in lines):
            return True
        return any(len(ln) >= 3 for ln in lines)

    def _run_backend(backend: str):
        if backend == "RconCli":
            argv = [RCONCLI_PATH, RCON_IP, RCON_PORT, RCON_PASSWORD, command]
        else:
            argv = [
                "python",
                RCON_SCRIPT,
                "--ip", RCON_IP,
                "--port", RCON_PORT,
                "--password", RCON_PASSWORD,
                "--command", command,
            ]
        log_debug("RCON", f"backend={backend}", flag="debug_rcon")
        log_debug("RCON", f"command={command}", flag="debug_rcon")
        log_debug("RCON", f"argv={argv}", flag="debug_rcon")
        result = subprocess.run(argv, input="\n", capture_output=True, text=True, timeout=20)
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        log_debug("RCON", f"returncode={result.returncode}", flag="debug_rcon")
        for idx, line in enumerate(stdout.splitlines()[:3], start=1):
            log_debug("RCON", f"stdout line {idx}: {line}", flag="debug_rcon")
        for idx, line in enumerate(stderr.splitlines()[:3], start=1):
            log_debug("RCON", f"stderr line {idx}: {line}", flag="debug_rcon")
        merged = f"{stdout}\n{stderr}".strip()
        cleaned = _clean_response(merged)
        for idx, line in enumerate(cleaned.splitlines()[:3], start=1):
            log_debug("RCON", f"cleaned response line {idx}: {line}", flag="debug_rcon")
        return cleaned, merged

    backends = []
    if Path(RCONCLI_PATH).exists():
        backends.append("RconCli")
    if Path(RCON_SCRIPT).exists():
        backends.append("PythonScript")
    if not backends:
        backends = ["PythonScript", "RconCli"]

    last_cleaned = ""
    last_raw = ""
    for backend in backends:
        try:
            cleaned, raw = _run_backend(backend)
            last_cleaned, last_raw = cleaned, raw
            if _is_usable(cleaned):
                return cleaned
        except Exception as e:
            log_debug("RCON", f"backend={backend} failed: {e}", flag="debug_rcon")
            continue
    return last_cleaned or last_raw


def run_rcon_raw(command: str) -> str:
    AUTH = 3
    AUTH_RESPONSE = 2
    COMMAND = 2
    RESPONSE_VALUE = 0
    request_id = int(time.time() * 1000) & 0x7FFFFFFF

    def _pack_packet(req_id: int, packet_type: int, payload: str) -> bytes:
        body = struct.pack("<ii", req_id, packet_type) + payload.encode("utf-8", errors="ignore") + b"\x00\x00"
        return struct.pack("<i", len(body)) + body

    def _recv_exact(sock_obj: socket.socket, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = sock_obj.recv(size - len(data))
            if not chunk:
                break
            data.extend(chunk)
        return bytes(data)

    def _read_packet(sock_obj: socket.socket):
        header = _recv_exact(sock_obj, 4)
        if len(header) < 4:
            return None
        packet_len = struct.unpack("<i", header)[0]
        if packet_len < 10:
            return None
        body = _recv_exact(sock_obj, packet_len)
        if len(body) < packet_len:
            return None
        req_id, packet_type = struct.unpack("<ii", body[:8])
        payload_bytes = body[8:-2] if packet_len >= 10 else b""
        payload = payload_bytes.decode("utf-8", errors="ignore")
        return req_id, packet_type, payload

    def _is_useful_line(line: str) -> bool:
        trimmed = str(line or "").strip()
        if not trimmed:
            return False
        lowered = trimmed.lower()
        noise_prefixes = (
            "tcp connection established with server",
            "sending:",
            "password accepted",
            "[info",
            "connected to",
        )
        return not any(lowered.startswith(p) for p in noise_prefixes)

    response_parts = []
    timeout_seconds = 6
    log_debug("RCON", f"raw connecting to {RCON_IP}:{RCON_PORT}", flag="debug_rcon")
    with socket.create_connection((RCON_IP, int(RCON_PORT)), timeout=timeout_seconds) as sock_obj:
        sock_obj.settimeout(timeout_seconds)
        sock_obj.sendall(_pack_packet(request_id, AUTH, RCON_PASSWORD))
        auth_success = False
        auth_deadline = time.time() + timeout_seconds
        while time.time() < auth_deadline:
            packet = _read_packet(sock_obj)
            if packet is None:
                break
            resp_id, packet_type, payload = packet
            if packet_type == AUTH_RESPONSE:
                auth_success = (resp_id == request_id and resp_id != -1)
                break
            if packet_type == RESPONSE_VALUE and payload:
                response_parts.append(payload)
        log_debug("RCON", f"raw auth success={auth_success}", flag="debug_rcon")
        if not auth_success:
            return ""

        response_parts.clear()
        log_debug("RCON", f"raw sending command={command}", flag="debug_rcon")
        command_id = request_id + 1
        sentinel_id = request_id + 2
        sock_obj.sendall(_pack_packet(command_id, COMMAND, command))
        sock_obj.sendall(_pack_packet(sentinel_id, COMMAND, ""))

        while True:
            try:
                packet = _read_packet(sock_obj)
            except socket.timeout:
                break
            if packet is None:
                break
            resp_id, packet_type, payload = packet
            if packet_type == RESPONSE_VALUE:
                if resp_id == sentinel_id and payload == "":
                    break
                if resp_id in (command_id, request_id):
                    response_parts.append(payload)

    cleaned_lines = [ln.strip() for chunk in response_parts for ln in str(chunk or "").splitlines() if _is_useful_line(ln)]
    response_text = "\n".join(cleaned_lines).strip()
    for idx, line in enumerate(cleaned_lines[:5], start=1):
        log_debug("RCON", f"raw response line {idx}: {line}", flag="debug_rcon")
    log_debug("RCON", f"raw response length={len(response_text)}", flag="debug_rcon")
    return response_text


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


def get_restart_schedule_times():
    config = load_config()
    raw = config.get("restart_times", DEFAULT_RESTART_TIMES)
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.split(",") if x.strip()]
    if not isinstance(raw, list) or not raw:
        raw = DEFAULT_RESTART_TIMES
    times = []
    for t in raw:
        try:
            hh, mm = str(t).split(":")
            times.append((int(hh), int(mm)))
        except Exception:
            continue
    return times or [(0, 0), (6, 0), (12, 0), (18, 0)]


def get_next_restart_datetime_london(now_london: datetime | None = None):
    if now_london is None:
        now_london = datetime.now(LONDON_TZ)

    schedule = get_restart_schedule_times()
    candidates = []
    for hour, minute in schedule:
        dt = now_london.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if dt <= now_london:
            dt += timedelta(days=1)
        candidates.append(dt)

    return min(candidates)


def _ensure_restart_cycle_state(now_london: datetime):
    next_restart = get_next_restart_datetime_london(now_london)
    next_iso = next_restart.isoformat()

    if restart_cycle_state.get("next_restart_iso") != next_iso:
        restart_cycle_state["next_restart_iso"] = next_iso
        restart_cycle_state["is_active"] = True
        restart_cycle_state["sent_3m"] = False
        restart_cycle_state["sent_2m"] = False
        restart_cycle_state["sent_1m"] = False
        restart_cycle_state["sent_restart_now"] = False
        restart_cycle_state["sent_back_up"] = False
        restart_cycle_state["last_countdown_text"] = None
        save_state()

    return next_restart


def process_restart_announcements():
    now_london = datetime.now(LONDON_TZ).replace(second=0, microsecond=0)
    next_restart = _ensure_restart_cycle_state(now_london)

    mins_until = int((next_restart - now_london).total_seconds() // 60)

    warn_flags = {
        3: "sent_3m",
        2: "sent_2m",
        1: "sent_1m",
    }

    for warn_min in RESTART_WARN_MINUTES:
        if mins_until == warn_min and not restart_cycle_state.get(warn_flags[warn_min]):
            if warn_min == 1:
                msg = "Server restart in 1 minute. Please move to safety."
            else:
                msg = f"Server restart in {warn_min} minutes."
            sent = send_announcement_silent(msg)
            if sent:
                restart_cycle_state[warn_flags[warn_min]] = True
                save_state()


def format_restart_countdown(seconds_until: int):
    if seconds_until <= 0:
        return "🔄 Server restarting now."
    if seconds_until >= 60:
        mins = seconds_until // 60
        secs = seconds_until % 60
        if secs == 0:
            if mins == 1:
                return "⏳ Server restart in 1 minute."
            return f"⏳ Server restart in {mins} minutes."
        return f"⏳ Server restart in {mins}m {secs}s."
    return f"⏳ Server restart in {seconds_until}s."


def build_restart_embed(title: str, description: str, color: discord.Color | None = None):
    embed = discord.Embed(
        title=title,
        description=description,
        color=color or discord.Color.orange(),
        timestamp=datetime.now(timezone.utc),
    )
    return embed


def build_action_embed(title: str, description: str, player_name: str = "", energy: int | None = None, color: discord.Color | None = None):
    embed = discord.Embed(title=title, description=description, color=color or discord.Color.blurple())
    if player_name:
        embed.add_field(name="Player", value=player_name, inline=True)
    if energy is not None:
        percent = max(0, min(100, int((energy / 200.0) * 100)))
        embed.add_field(name="Energy", value=render_progress_bar(percent), inline=True)
    embed.timestamp = datetime.now(timezone.utc)
    return embed


def get_logging_config():
    section = ConfigManager.get_section("logging")
    if not isinstance(section, dict):
        section = {}
    env_mode = str(os.getenv("BOT_LOG_MODE", "") or "").strip().lower()
    cfg_mode = str(section.get("log_mode", ConfigManager.get("log_mode", "BOT_LOG_MODE", "concise")) or "concise").strip().lower()
    log_mode = env_mode if env_mode in {"concise", "debug"} else cfg_mode
    if log_mode not in {"concise", "debug"}:
        log_mode = "concise"
    debug_master = bool(log_mode == "debug")
    return {
        "log_mode": log_mode,
        "debug_logging": debug_master or ConfigManager.get_bool("debug_logging", "DEBUG_LOGGING", bool(section.get("debug_logging", False))),
        "debug_rcon": ConfigManager.get_bool("debug_rcon", "DEBUG_RCON", bool(section.get("debug_rcon", False))),
        "debug_sftp": ConfigManager.get_bool("debug_sftp", "DEBUG_SFTP", bool(section.get("debug_sftp", False))),
        "debug_presence": ConfigManager.get_bool("debug_presence", "DEBUG_PRESENCE", bool(section.get("debug_presence", False))),
    }


def log_info(tag: str, msg: str):
    print(f"[{tag}] {msg}")


def log_warn(tag: str, msg: str):
    print(f"[{tag} WARNING] {msg}")


def log_error(tag: str, msg: str):
    print(f"[{tag} ERROR] {msg}")


def log_debug(tag: str, msg: str, flag: str = "debug_logging"):
    cfg = get_logging_config()
    if not cfg.get("debug_logging", False):
        return
    if flag != "debug_logging" and not cfg.get(flag, False):
        return
    print(f"[{tag} DEBUG] {msg}")


def log_limited(key: str, interval_seconds: float, tag: str, msg: str, level: str = "info"):
    now = time.time()
    suppression = bot_runtime_state.setdefault("log_suppression", {})
    last = float(suppression.get(key, 0.0) or 0.0)
    if now - last < interval_seconds:
        return
    suppression[key] = now
    if level == "warn":
        log_warn(tag, msg)
    elif level == "error":
        log_error(tag, msg)
    elif level == "debug":
        log_debug(tag, msg)
    else:
        log_info(tag, msg)


# Expected concise console output example:
# [STARTUP] Bot logged in
# [ADMIN BOT] ONLINE
# [TRACKING] Players online: 1
# JJoness290 | 76561198798435427 | session=43 mins | total=570 mins | energy=1666
# [CLAIM TIMEOUT] refunded 25 energy steam=...


def get_bot_presence_config():
    section = ConfigManager.get_section("bot_presence")
    config = load_config()
    if not isinstance(config, dict):
        config = {}
    if not isinstance(section, dict):
        section = {}

    env_player_name = str(os.getenv("BOT_PLAYER_NAME", "") or "").strip()
    env_steam_id = str(os.getenv("BOT_STEAM_ID", "") or "").strip()
    cfg_player_name = str(section.get("player_name", config.get("bot_player_name", "")) or "").strip()
    cfg_steam_id = str(section.get("steam_id", config.get("bot_steam_id", "")) or "").strip()

    return {
        "player_name": env_player_name if env_player_name else cfg_player_name,
        "steam_id": env_steam_id if env_steam_id else cfg_steam_id,
        "missing_grace_seconds": int(section.get("missing_grace_seconds", 60) or 60),
        "admin_bot_grace_seconds": ConfigManager.get_int("admin_bot_grace_seconds", "ADMIN_BOT_GRACE_SECONDS", 120, minimum=30),
        "rcon_check_interval_seconds": ConfigManager.get_int("rcon_check_interval_seconds", "RCON_CHECK_INTERVAL_SECONDS", 10, minimum=5),
        "admin_bot_startup_warmup_seconds": ConfigManager.get_int("admin_bot_startup_warmup_seconds", "ADMIN_BOT_STARTUP_WARMUP_SECONDS", 30, minimum=5),
        "debug_admin_bot_detection": ConfigManager.get_bool("debug_admin_bot_detection", "DEBUG_ADMIN_BOT_DETECTION", False),
        "debug_logging": ConfigManager.get_bool("debug_logging", "DEBUG_LOGGING", False),
        "debug_rcon": ConfigManager.get_bool("debug_rcon", "DEBUG_RCON", False),
        "debug_sftp": ConfigManager.get_bool("debug_sftp", "DEBUG_SFTP", False),
        "debug_presence": ConfigManager.get_bool("debug_presence", "DEBUG_PRESENCE", False),
    }


def get_bot_sustain_config():
    section = ConfigManager.get_section("bot_sustain")
    commands = section.get("commands", ["/heal 100", "/hunger 100", "/thirst 100"])
    if not isinstance(commands, list) or not commands:
        commands = ["/heal 100", "/hunger 100", "/thirst 100"]
    normalized = [str(x).strip() for x in commands if str(x).strip()]
    if len(normalized) != 3:
        normalized = ["/heal 100", "/hunger 100", "/thirst 100"]
    return {
        "enabled": bool(section.get("enabled", True)),
        "interval_seconds": int(os.getenv("BOT_SUSTAIN_INTERVAL_SECONDS", section.get("interval_seconds", 600)) or 600),
        "commands": normalized,
    }


def get_admin_dashboard_config():
    section = ConfigManager.get_section("admin_dashboard")
    return {
        "enabled": bool(section.get("enabled", True)),
        "channel_id": int(section.get("channel_id", 0) or 0),
        "refresh_interval_seconds": int(section.get("refresh_interval_seconds", 60) or 60),
    }


def get_admin_alerts_config():
    section = ConfigManager.get_section("admin_alerts")
    return {
        "enabled": bool(section.get("enabled", True)),
        "channel_id": int(section.get("channel_id", 0) or 0),
        "ping_role_id": int(section.get("ping_role_id", 0) or 0),
        "ping_user_ids": section.get("ping_user_ids", []) if isinstance(section.get("ping_user_ids", []), list) else [],
        "send_immediate_offline_alert": bool(section.get("send_immediate_offline_alert", True)),
        "send_recovery_alert": bool(section.get("send_recovery_alert", True)),
    }


def is_admin_bot_online():
    return str(bot_runtime_state.get("admin_bot_state", "OFFLINE")).upper() in {"ONLINE", "GRACE"}


def is_admin_bot_offline():
    return str(bot_runtime_state.get("admin_bot_state", "OFFLINE")).upper() == "OFFLINE"


def get_valid_last_seen_at() -> float | None:
    raw = bot_runtime_state.get("last_bot_seen_at", 0.0)
    try:
        ts = float(raw or 0.0)
    except Exception:
        return None
    return ts if ts > 0 else None


def _format_duration(seconds_value: float | int | None):
    if not seconds_value:
        return "0m"
    secs = int(max(0, seconds_value))
    h, rem = divmod(secs, 3600)
    m, _ = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"


def _fmt_ts(ts_value):
    if not ts_value:
        return "—"
    if isinstance(ts_value, str):
        return ts_value
    try:
        return datetime.fromtimestamp(float(ts_value), tz=timezone.utc).isoformat()
    except Exception:
        return str(ts_value)


def is_any_claim_waiting_for_precheck() -> bool:
    with ECONOMY_LOCK:
        purchases = load_purchases()
    return any(str(p.get("status", "")).upper() == "PRECHECK_VERIFYING" for p in purchases)


def is_any_claim_waiting_for_final_verify() -> bool:
    with ECONOMY_LOCK:
        purchases = load_purchases()
    return any(str(p.get("status", "")).upper() == "FINAL_VERIFY_PENDING" for p in purchases)


def has_active_claim_verification_work() -> bool:
    return is_any_claim_waiting_for_precheck() or is_any_claim_waiting_for_final_verify()


def is_bot_present_in_players(players: dict):
    matched, _, _ = detect_admin_bot_match(players, log_debug=False, source_label="generic")
    return matched


def _normalize_admin_name(value: str):
    text = str(value or "").strip().lower()
    return " ".join(text.split())


def detect_admin_bot_match(players: dict, log_debug: bool = False, source_label: str = "tracked"):
    cfg = get_bot_presence_config()
    target_name_raw = str(cfg.get("player_name", "")).strip()
    target_steam = str(cfg.get("steam_id", "")).strip()
    target_name = _normalize_admin_name(target_name_raw)

    if log_debug:
        print(f"[ADMIN BOT] checking {source_label} player list for admin bot")

    if target_steam:
        for steam_id, player_name in players.items():
            if str(steam_id).strip() == target_steam:
                if log_debug:
                    print(f"[ADMIN BOT] matched by steam id: {target_steam}")
                return True, "steam_id", {"steam_id": str(steam_id), "name": str(player_name)}

    if target_name:
        for steam_id, player_name in players.items():
            current_name = _normalize_admin_name(player_name)
            if current_name == target_name:
                if log_debug:
                    print(f"[ADMIN BOT] matched by player name: {player_name}")
                return True, "player_name", {"steam_id": str(steam_id), "name": str(player_name)}

    if log_debug:
        for steam_id, player_name in players.items():
            print(f"[ADMIN BOT DEBUG] tracked player: name={player_name} steam_id={steam_id}")
    return False, "none", {}


def normalize_players_map(players_raw):
    normalized = {}
    if isinstance(players_raw, dict):
        for steam_id, player_name in players_raw.items():
            sid = str(steam_id or "").strip()
            name = str(player_name or "").strip()
            if sid and name:
                normalized[sid] = name
        return normalized
    if isinstance(players_raw, list):
        for row in players_raw:
            if not isinstance(row, dict):
                continue
            sid = str(row.get("steam_id", "") or "").strip()
            name = str(row.get("name", "") or "").strip()
            if sid and name:
                normalized[sid] = name
    return normalized


def poll_player_snapshot():
    now = time.time()
    snapshot = {
        "players": {},
        "player_count": 0,
        "seen_at": now,
        "source": "RCON",
        "success": False,
        "server_state": map_server_state(),
        "error": "",
    }
    poll_result = get_players_from_rcon()
    players = normalize_players_map(poll_result.get("players", {}))
    snapshot["players"] = players
    snapshot["player_count"] = int(poll_result.get("player_count", len(players)) or len(players))
    snapshot["source"] = str(poll_result.get("source", "RCON") or "RCON")
    snapshot["success"] = bool(poll_result.get("success", False))
    snapshot["error"] = str(poll_result.get("error", "") or "")
    now_ts = time.time()
    bot_runtime_state["last_player_poll_ok"] = bool(snapshot["success"])
    bot_runtime_state["last_player_poll_source"] = str(snapshot["source"])
    if snapshot["success"]:
        bot_runtime_state["last_player_poll_success_at"] = now_ts
        bot_runtime_state["last_player_poll_player_count"] = int(snapshot["player_count"])
    else:
        bot_runtime_state["last_player_poll_failed_at"] = now_ts
    return snapshot


def match_admin_bot_in_snapshot(snapshot: dict, source_label: str = "RCON"):
    players = normalize_players_map(snapshot.get("players", {}))
    cfg = get_bot_presence_config()
    target_steam = str(cfg.get("steam_id", "") or "").strip()
    target_name = _normalize_admin_name(str(cfg.get("player_name", "") or ""))

    if target_steam and target_steam in players:
        return True, target_steam, str(players.get(target_steam, "")), source_label

    if target_name:
        for steam_id, player_name in players.items():
            if _normalize_admin_name(player_name) == target_name:
                return True, steam_id, str(player_name), source_label

    return False, "", "", source_label


def queue_priority_commands(commands: list[dict]):
    if not commands:
        return
    with ECONOMY_LOCK:
        existing = load_game_commands()
        next_id = get_next_command_id(existing)
        for idx, cmd in enumerate(commands, start=1):
            cmd.setdefault("id", f"cmd_{next_id + idx - 1:03d}")
            existing.append(cmd)
        save_game_commands(existing)


def queue_sustain_commands():
    cfg = get_bot_sustain_config()
    now_iso = datetime.now(timezone.utc).isoformat()
    sustain_commands = cfg["commands"]
    if len(sustain_commands) != 3:
        sustain_commands = ["/heal 100", "/hunger 100", "/thirst 100"]
    batch_id = f"sustain_{int(time.time())}"
    payload = []
    for i, cmd in enumerate(sustain_commands, start=1):
        payload.append({
            "steam_id": "__bot__",
            "player_name": "SYSTEM",
            "item": "sustain",
            "command": cmd,
            "status": "PENDING",
            "created_at": now_iso,
            "completed_at": None,
            "claim_group_id": batch_id,
            "claim_step": i,
            "claim_final": i == len(sustain_commands),
            "claim_phase": "SUSTAIN",
            "command_type": "sustain",
            "priority": 10,
            "requires_bot_in_game": True,
            "max_age_seconds": cfg["interval_seconds"] * 2,
        })
    queue_priority_commands(payload)
    log_info("BOT SUSTAIN", f"queued {len(payload)} commands")


def has_pending_sustain_commands() -> bool:
    with ECONOMY_LOCK:
        commands = load_game_commands()
    for entry in commands:
        if str(entry.get("command_type", "")).lower() != "sustain":
            continue
        status = str(entry.get("status", "")).upper()
        if status in {"PENDING", "EXECUTING"}:
            return True
    return False


def try_queue_sustain(now_ts: float, cfg_sustain: dict, immediate: bool = False) -> bool:
    if has_pending_sustain_commands():
        log_limited("sustain_skipped_pending", 30, "BOT SUSTAIN", "skipped (already pending)")
        return False
    interval = int(cfg_sustain.get("interval_seconds", 600) or 600)
    last_sustain_at = float(bot_runtime_state.get("last_sustain_at", 0.0) or 0.0)
    if (not immediate) and last_sustain_at > 0 and (now_ts - last_sustain_at) < interval:
        log_limited("sustain_skipped_interval", 30, "BOT SUSTAIN", "skipped (interval not reached)")
        return False
    queue_sustain_commands()
    bot_runtime_state["last_sustain_at"] = now_ts
    return True


# Sustain behavior examples:
# - Admin bot ONLINE transition -> immediate sustain once (heal/hunger/thirst).
# - Admin bot stays ONLINE for >= 600s -> sustain queues again.
# - Admin bot OFFLINE/UNKNOWN or server not ONLINE -> sustain skipped.
# - Sustain commands already pending in queue -> skip duplicate batch.

async def get_restarts_channel():
    configured_id = ConfigManager.get_int("restart_channel_id", "RESTART_CHANNEL_ID", 0, minimum=0)
    if configured_id:
        channel = bot.get_channel(configured_id)
        if channel:
            return channel

    channel_name = str(ConfigManager.get("restart_channel_name", "RESTART_CHANNEL_NAME", "restarts") or "restarts")
    for guild in bot.guilds:
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if channel:
            return channel
    return None


async def _send_or_edit_restart_message(text: str, title: str = "Server Status", color: discord.Color | None = None):
    channel = await get_restarts_channel()
    if not channel:
        return

    embed = build_restart_embed(title, text, color)
    message_id = restart_cycle_state.get("progress_message_id")
    if message_id:
        try:
            msg = await channel.fetch_message(int(message_id))
            await msg.edit(content=None, embed=embed)
            restart_cycle_state["progress_channel_id"] = int(channel.id)
            restart_cycle_state["last_countdown_text"] = text
            save_state()
            return
        except Exception:
            restart_cycle_state["progress_message_id"] = None

    try:
        sent = await channel.send(embed=embed)
        restart_cycle_state["progress_message_id"] = int(sent.id)
        restart_cycle_state["progress_channel_id"] = int(channel.id)
        restart_cycle_state["last_countdown_text"] = text
        save_state()
    except Exception:
        return


def detect_server_back_up():
    try:
        raw = run_rcon("playerlist")
        lowered = str(raw or "").lower()
        if "error" in lowered and "connection" in lowered:
            return False
        if "timeout" in lowered:
            return False
        return True
    except Exception:
        return False


async def process_restart_discord_updates():
    now_london = datetime.now(LONDON_TZ)
    next_restart = _ensure_restart_cycle_state(now_london)
    seconds_until = int((next_restart - now_london).total_seconds())

    if seconds_until > 180:
        return

    if seconds_until > 0:
        text = format_restart_countdown(seconds_until)
        last_text = restart_cycle_state.get("last_countdown_text")
        if text != last_text:
            await _send_or_edit_restart_message(text, "Server Restart Incoming", discord.Color.orange())
        return

    if not restart_cycle_state.get("sent_restart_now"):
        await _send_or_edit_restart_message(
            "The server is restarting now. Recovery monitoring has started.",
            "Server Restarting",
            discord.Color.dark_orange(),
        )
        restart_cycle_state["sent_restart_now"] = True
        save_state()
        return

    if not restart_cycle_state.get("sent_back_up"):
        is_up = await asyncio.to_thread(detect_server_back_up)
        if is_up:
            await _send_or_edit_restart_message(
                "Server is back online and systems are reconnecting.",
                "Server Online",
                discord.Color.green(),
            )
            restart_cycle_state["sent_back_up"] = True
            restart_cycle_state["is_active"] = False
            save_state()


async def send_restart_incident(title: str, description: str, color: discord.Color):
    channel = await get_restarts_channel()
    if not channel:
        return
    try:
        await channel.send(embed=build_restart_embed(title, description, color))
    except Exception:
        return


async def get_admin_alert_channel():
    cfg = get_admin_alerts_config()
    if cfg["channel_id"]:
        ch = bot.get_channel(cfg["channel_id"])
        if ch:
            return ch
    return None


async def send_admin_transition_alert(is_offline: bool):
    cfg = get_admin_alerts_config()
    if not cfg["enabled"]:
        return
    if is_offline and not cfg["send_immediate_offline_alert"]:
        return
    if (not is_offline) and not cfg["send_recovery_alert"]:
        return
    channel = await get_admin_alert_channel()
    if not channel:
        return
    if is_offline and admin_runtime_state.get("last_alert_type") == "offline":
        print("[ALERT] skipped duplicate offline alert")
        return
    if (not is_offline) and admin_runtime_state.get("last_alert_type") == "recovery":
        return

    mentions = []
    if cfg["ping_role_id"]:
        mentions.append(f"<@&{cfg['ping_role_id']}>")
    for uid in cfg["ping_user_ids"]:
        try:
            mentions.append(f"<@{int(uid)}>")
        except Exception:
            continue
    mention_text = " ".join(mentions).strip()

    if is_offline:
        embed = discord.Embed(
            title="Admin Bot Went Offline",
            description=(
                "The in-game admin bot has just gone offline.\n\n"
                "Purchases and claims are now temporarily disabled.\n"
                "Players are being told to open a ticket."
            ),
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        await channel.send(content=mention_text or None, embed=embed)
        admin_runtime_state["last_alert_type"] = "offline"
        admin_runtime_state["last_alert_sent_at"] = datetime.now(timezone.utc).isoformat()
        admin_runtime_state["last_alert_summary"] = "Admin bot offline"
        print("[ALERT] offline alert sent")
        await refresh_admin_dashboard(force=True)
    else:
        embed = discord.Embed(
            title="Admin Bot Restored",
            description="The in-game admin bot is back online.\n\nPurchases and claims are enabled again.",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        await channel.send(content=mention_text or None, embed=embed)
        admin_runtime_state["last_alert_type"] = "recovery"
        admin_runtime_state["last_alert_sent_at"] = datetime.now(timezone.utc).isoformat()
        admin_runtime_state["last_alert_summary"] = "Admin bot restored"
        print("[ALERT] recovery alert sent")
        await refresh_admin_dashboard(force=True)


def record_manual_issue(ctx, command_name: str, item: str = ""):
    if is_admin_bot_online():
        return
    issue = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_id": int(ctx.author.id),
        "username": ctx.author.display_name,
        "command": command_name,
        "item": item,
    }
    admin_runtime_state["manual_issues"].append(issue)
    if len(admin_runtime_state["manual_issues"]) > 100:
        admin_runtime_state["manual_issues"] = admin_runtime_state["manual_issues"][-100:]
    admin_runtime_state["outage_issue_count"] = int(admin_runtime_state.get("outage_issue_count", 0)) + 1
    print(f"[MANUAL ISSUE] recorded blocked {command_name} for user {ctx.author.id}")


async def get_admin_dashboard_channel():
    cfg = get_admin_dashboard_config()
    if cfg["channel_id"]:
        ch = bot.get_channel(cfg["channel_id"])
        if ch:
            return ch
    return None


def build_admin_dashboard_embed():
    state = str(bot_runtime_state.get("admin_bot_state", "OFFLINE")).upper()
    status_text = "🔴 Bot Offline"
    color = discord.Color.red()
    if state == "ONLINE":
        status_text = "🟢 Bot Online"
        color = discord.Color.green()
    elif state == "GRACE":
        status_text = "🟠 Bot Starting / Recovering"
        color = discord.Color.orange()
    elif state == "UNKNOWN":
        status_text = "⚪ Bot Status Unknown"
        color = discord.Color.light_grey()

    embed = discord.Embed(
        title="Primal Abyss Bot Status",
        description=status_text,
        color=color,
    )
    return embed


async def refresh_admin_dashboard(force: bool = False):
    cfg = get_admin_dashboard_config()
    if not cfg["enabled"]:
        return
    now = time.time()
    if (not force) and (now - float(admin_runtime_state.get("last_dashboard_refresh_at", 0.0)) < cfg["refresh_interval_seconds"]):
        return
    channel = await get_admin_dashboard_channel()
    if not channel:
        return

    embed = build_admin_dashboard_embed()
    message_id = restart_cycle_state.get("admin_dashboard_message_id")
    if message_id:
        try:
            msg = await channel.fetch_message(int(message_id))
            await msg.edit(embed=embed, content=None)
            admin_runtime_state["last_dashboard_refresh_at"] = now
            return
        except Exception:
            restart_cycle_state["admin_dashboard_message_id"] = None
            save_state()
    sent = await channel.send(embed=embed)
    restart_cycle_state["admin_dashboard_message_id"] = int(sent.id)
    restart_cycle_state["admin_dashboard_channel_id"] = int(channel.id)
    save_state()
    admin_runtime_state["last_dashboard_refresh_at"] = now

def _classify_health_status(skip_sftp: bool = False):
    rcon_ok = detect_server_back_up()
    if skip_sftp:
        sftp_ok = rcon_ok
    else:
        lines, sftp_err = read_remote_log_tail(2048)
        sftp_ok = sftp_err is None and isinstance(lines, list)

    if rcon_ok and sftp_ok:
        return "ONLINE"
    if rcon_ok or sftp_ok:
        return "SUSPECTED_DOWN"
    return "DOWN"


def get_recent_player_poll_signal(fresh_seconds: int = 30):
    now_ts = time.time()
    last_ok_at = float(bot_runtime_state.get("last_player_poll_success_at", 0.0) or 0.0)
    ok_recent = last_ok_at > 0 and (now_ts - last_ok_at) <= int(max(5, fresh_seconds))
    player_count = int(bot_runtime_state.get("last_player_poll_player_count", 0) or 0)
    return {
        "ok_recent": ok_recent,
        "player_count": player_count,
        "source": str(bot_runtime_state.get("last_player_poll_source", "unknown") or "unknown"),
    }


async def process_server_health_updates():
    poll_interval = ConfigManager.get_int("health_poll_interval_seconds", "HEALTH_POLL_INTERVAL_SECONDS", 10, minimum=3)
    now_ts = time.time()
    if now_ts - float(server_health_state.get("last_health_poll", 0.0)) < poll_interval:
        return
    server_health_state["last_health_poll"] = now_ts

    no_players_online = len(online_since) == 0
    no_active_verify = not has_active_claim_verification_work()
    skip_sftp = bool(no_players_online and no_active_verify)
    if skip_sftp:
        now_skip = time.time()
        if now_skip - float(server_health_state.get("last_skip_log_at", 0.0) or 0.0) > 30:
            log_debug("SFTP", "skipped (no active verification work)", flag="debug_sftp")
            log_debug("CLAIM", "skipped remote log poll (no active claim)", flag="debug_sftp")
            log_debug("TRACKING", "skipped remote log read (server empty)", flag="debug_sftp")
            server_health_state["last_skip_log_at"] = now_skip

    health = await asyncio.to_thread(_classify_health_status, skip_sftp)
    previous = server_health_state.get("status", "ONLINE")

    recent_poll_signal = get_recent_player_poll_signal(30)
    if recent_poll_signal["ok_recent"] and recent_poll_signal["player_count"] > 0:
        if health != "ONLINE":
            log_limited(
                "server_health_override_players_online",
                30,
                "SERVER",
                "suppressing DOWN state because players are currently online",
            )
        health = "ONLINE"
    elif recent_poll_signal["ok_recent"] and health != "ONLINE":
        if skip_sftp:
            log_limited(
                "server_aux_health_failed_rcon_ok",
                30,
                "SERVER",
                "auxiliary health check failed but RCON is healthy",
            )
        else:
            log_limited(
                "server_health_override_recent_poll",
                30,
                "SERVER",
                "health overridden to ONLINE due to recent successful RCON player poll",
            )
        health = "ONLINE"

    if health == "ONLINE":
        server_health_state["success_count"] = int(server_health_state.get("success_count", 0)) + 1
        server_health_state["fail_count"] = 0
    else:
        server_health_state["fail_count"] = int(server_health_state.get("fail_count", 0)) + 1
        server_health_state["success_count"] = 0

    suspect_threshold = ConfigManager.get_int("crash_suspect_threshold", "CRASH_SUSPECT_THRESHOLD", 2, minimum=1)
    down_threshold = ConfigManager.get_int("crash_confirm_threshold", "CRASH_CONFIRM_THRESHOLD", 4, minimum=2)

    new_status = previous
    fail_count = int(server_health_state.get("fail_count", 0))
    success_count = int(server_health_state.get("success_count", 0))
    if fail_count >= down_threshold:
        new_status = "DOWN"
    elif fail_count >= suspect_threshold:
        new_status = "SUSPECTED_DOWN"
    elif success_count >= 2:
        new_status = "ONLINE"

    if new_status != previous:
        server_health_state["status"] = new_status
        server_health_state["last_status_at"] = datetime.now(timezone.utc).isoformat()
        log_info("SERVER", f"State: {new_status}")
        if new_status == "SUSPECTED_DOWN":
            await send_restart_incident(
                "Server Issue Detected",
                "Connection checks are failing. Monitoring closely.",
                discord.Color.gold(),
            )
        elif new_status == "DOWN":
            await send_restart_incident(
                "Server Offline",
                "Server crash/offline confirmed. Auto recovery is in progress.",
                discord.Color.red(),
            )
        elif new_status == "ONLINE":
            await send_restart_incident(
                "Recovery Complete",
                "Bot systems reconnected and monitoring has resumed.",
                discord.Color.green(),
            )


async def process_executor_health_updates():
    if not EXECUTOR_HEARTBEAT_FILE.exists():
        return
    heartbeat = load_json(EXECUTOR_HEARTBEAT_FILE, {})
    ts = parse_dt(str(heartbeat.get("timestamp", "")))
    if not ts:
        return
    timeout = ConfigManager.get_int("executor_heartbeat_timeout_seconds", "EXECUTOR_HEARTBEAT_TIMEOUT_SECONDS", 60, minimum=15)
    age = (datetime.now(timezone.utc) - ts).total_seconds() if ts.tzinfo else (datetime.now() - ts).total_seconds()
    if age > timeout:
        if restart_cycle_state.get("executor_stale_reported"):
            return
        restart_cycle_state["executor_stale_reported"] = True
        save_state()
        await send_restart_incident(
            "Server Issue Detected",
            "Executor heartbeat is stale. Attempting recovery...",
            discord.Color.red(),
        )
    else:
        if restart_cycle_state.get("executor_stale_reported"):
            restart_cycle_state["executor_stale_reported"] = False
            save_state()
            await send_restart_incident(
                "Recovery Complete",
                "Executor heartbeat recovered and command processing resumed.",
                discord.Color.green(),
            )


def map_server_state():
    health = server_health_state.get("status", "ONLINE")
    if health == "ONLINE":
        return SERVER_STATE_ONLINE
    if health == "SUSPECTED_DOWN":
        return SERVER_STATE_SUSPECTED_DOWN
    return SERVER_STATE_DOWN


def log_startup_warmup_banner_once():
    if bot_runtime_state.get("startup_warmup_banner_logged", False):
        return
    log_info("STARTUP", "warmup active")
    bot_runtime_state["startup_warmup_banner_logged"] = True


async def process_bot_presence_and_recovery(snapshot: dict):
    cfg_presence = get_bot_presence_config()
    cfg_sustain = get_bot_sustain_config()
    now = time.time()
    admin_grace_seconds = int(cfg_presence.get("admin_bot_grace_seconds", 120))
    startup_warmup_seconds = int(cfg_presence.get("admin_bot_startup_warmup_seconds", 30))
    previous_state = str(bot_runtime_state.get("admin_bot_state", "OFFLINE")).upper()

    players = normalize_players_map(snapshot.get("players", {}))
    poll_success = bool(snapshot.get("success", False))
    poll_source = str(snapshot.get("source", "RCON") or "RCON")
    bot_runtime_state["server_state"] = str(snapshot.get("server_state", map_server_state()))
    bot_runtime_state["startup_tracking_checked"] = True
    bot_runtime_state["startup_rcon_checked"] = poll_success

    if poll_success:
        bot_runtime_state["last_player_count"] = int(snapshot.get("player_count", len(players)))
        bot_runtime_state["last_rcon_error"] = ""
    else:
        bot_runtime_state["last_rcon_error"] = str(snapshot.get("error", "") or "RCON poll failed")

    if bot_runtime_state["server_state"] != SERVER_STATE_ONLINE:
        bot_runtime_state["presence_state"] = BOT_STATE_WAITING_SERVER
        bot_runtime_state["admin_bot_state"] = "UNKNOWN"
        log_limited("presence_waiting_server", 30, "SERVER", f"State: {bot_runtime_state['server_state']}")
        await refresh_admin_dashboard()
        return

    startup_started_at = float(bot_runtime_state.get("startup_started_at", 0.0) or 0.0)
    warmup_active = startup_started_at > 0 and (now - startup_started_at) < startup_warmup_seconds
    if warmup_active and (not bot_runtime_state.get("startup_warmup_complete_logged", False)):
        log_startup_warmup_banner_once()

    if not poll_success:
        log_limited("rcon_poll_failure", 30, "RCON", f"poll unsuccessful: {bot_runtime_state.get('last_rcon_error', 'unknown')}", level="warn")
        await refresh_admin_dashboard()
        return

    matched, matched_steam_id, matched_name, source_label = match_admin_bot_in_snapshot(snapshot, poll_source)
    if matched:
        bot_runtime_state["admin_bot_state"] = "ONLINE"
        bot_runtime_state["presence_state"] = BOT_STATE_IN_GAME
        bot_runtime_state["missing_since"] = None
        bot_runtime_state["last_bot_seen_at"] = now
        bot_runtime_state["last_detection_source"] = source_label
        bot_runtime_state["last_player_count"] = len(players)
        admin_runtime_state["last_seen_in_game_at"] = datetime.now(timezone.utc).isoformat()
        admin_runtime_state["last_alert_summary"] = "Admin bot online"
        if previous_state != "ONLINE":
            log_info("ADMIN BOT", f"ONLINE via {source_label}")
            if admin_runtime_state.get("outage_active"):
                admin_runtime_state["outage_active"] = False
                admin_runtime_state["outage_started_at"] = None
                admin_runtime_state["offline_reminder_sent_at"] = 0.0
                await send_admin_transition_alert(False)
            log_info("BOT SUSTAIN", "immediate sustain on ONLINE transition")
            try_queue_sustain(now, cfg_sustain, immediate=True)
            await refresh_admin_dashboard(force=True)
        log_debug("PRESENCE", f"matched steam_id={matched_steam_id} name={matched_name}", flag="debug_presence")
    else:
        valid_last_seen_at = get_valid_last_seen_at()
        if warmup_active and (not bot_runtime_state.get("startup_warmup_complete_logged", False)):
            bot_runtime_state["admin_bot_state"] = "UNKNOWN"
            bot_runtime_state["presence_state"] = BOT_STATE_MISSING
            bot_runtime_state["last_detection_source"] = "Unknown"
            admin_runtime_state["last_alert_summary"] = "Startup warmup active"
            log_limited("startup_warmup_presence", 30, "ADMIN BOT", "presence unknown (startup warmup)")
            await refresh_admin_dashboard()
            return

        if valid_last_seen_at is not None and (now - valid_last_seen_at) < admin_grace_seconds:
            bot_runtime_state["admin_bot_state"] = "GRACE"
            bot_runtime_state["presence_state"] = BOT_STATE_IN_GAME
            bot_runtime_state["missing_since"] = None
            bot_runtime_state["last_detection_source"] = "Unknown"
            admin_runtime_state["last_alert_summary"] = "Grace period active"
            log_limited("presence_grace", 30, "ADMIN BOT", "GRACE period active")
            if previous_state != "GRACE":
                await refresh_admin_dashboard(force=True)
        else:
            bot_runtime_state["admin_bot_state"] = "OFFLINE"
            bot_runtime_state["presence_state"] = BOT_STATE_MISSING
            bot_runtime_state["missing_since"] = now
            bot_runtime_state["last_detection_source"] = "Unknown"
            admin_runtime_state["last_alert_summary"] = "Admin bot offline"
            if previous_state != "OFFLINE":
                log_warn("ADMIN BOT", "OFFLINE")
            if not admin_runtime_state.get("outage_active"):
                admin_runtime_state["outage_active"] = True
                admin_runtime_state["outage_started_at"] = time.time()
                admin_runtime_state["outage_issue_count"] = 0
                admin_runtime_state["manual_issues"] = []
                await send_admin_transition_alert(True)
                await refresh_admin_dashboard(force=True)
            else:
                now_ts = time.time()
                if now_ts - float(admin_runtime_state.get("offline_reminder_sent_at", 0.0)) >= 300:
                    await send_restart_incident(
                        "Admin Bot Offline",
                        "Admin bot is still offline after 5 minutes. Purchases/claims remain disabled.",
                        discord.Color.red(),
                    )
                    admin_runtime_state["offline_reminder_sent_at"] = now_ts

    if str(bot_runtime_state.get("admin_bot_state", "OFFLINE")).upper() == "ONLINE" and cfg_sustain["enabled"]:
        try_queue_sustain(now, cfg_sustain, immediate=False)
    elif cfg_sustain["enabled"] and str(bot_runtime_state.get("admin_bot_state", "OFFLINE")).upper() == "OFFLINE":
        log_limited("sustain_skipped_offline", 60, "BOT SUSTAIN", "skipped (offline)")

    await refresh_admin_dashboard()

def parse_rcon_playerlist(raw_text: str):
    def _is_noise_line(line: str) -> bool:
        lowered = str(line or "").strip().lower()
        if not lowered:
            return True
        noise_prefixes = (
            "[debug]",
            "tcp connection established with server",
            "sending:",
            "password accepted",
            "connected to",
            "[info",
        )
        return any(lowered.startswith(prefix) for prefix in noise_prefixes)

    cleaned_lines = []
    for raw_line in str(raw_text or "").splitlines():
        line = str(raw_line or "").strip().rstrip(",").strip()
        if not line:
            continue
        if line.lower() == "playerlist":
            continue
        if _is_noise_line(line):
            continue
        cleaned_lines.append(line)

    players = {}
    pending_steam_id = None

    for line in cleaned_lines:
        line = line.strip().rstrip(",")

        if not line or line.lower() == "playerlist":
            continue

        # Steam ID line
        if line.isdigit() and len(line) >= 17:
            pending_steam_id = line
            continue

        # Name line
        if pending_steam_id:
            players[pending_steam_id] = line
            log_debug("RCON", f"paired steam_id={pending_steam_id} with name={line}", flag="debug_rcon")
            pending_steam_id = None

    log_debug("RCON", f"parsed players: {players}", flag="debug_rcon")
    if not players:
        log_debug("RCON", "playerlist parsed empty", flag="debug_rcon")
        for i, l in enumerate(cleaned_lines[:10]):
            log_debug("RCON", f"cleaned[{i}]={l}", flag="debug_rcon")

    return players


def is_usable_rcon_playerlist_output(raw_text: str) -> bool:
    text = str(raw_text or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    if any(re.search(r"\d{17}", line) for line in lines):
        return True
    noise_prefixes = (
        "tcp connection established with server",
        "sending:",
        "password accepted",
        "[info",
        "connected to",
        "auth success",
        "response length",
    )
    useful_lines = [line for line in lines if not any(line.lower().startswith(prefix) for prefix in noise_prefixes)]
    return len(useful_lines) >= 2


def get_rcon_playerlist():
    def _clean_lines(raw_text: str):
        raw_lines = str(raw_text or "").splitlines()
        cleaned = []
        for line in raw_lines:
            line = str(line or "").strip()
            if not line:
                continue
            if line.startswith("[DEBUG]"):
                continue
            cleaned.append(line)
        return cleaned

    def _has_transport_error(raw_text: str):
        lowered = str(raw_text or "").lower()
        return any(token in lowered for token in ("timeout", "error", "failed", "exception"))

    def _build_success(players: dict, source: str, error: str = ""):
        return {
            "success": True,
            "players": normalize_players_map(players),
            "player_count": len(normalize_players_map(players)),
            "source": source,
            "error": error,
        }

    raw_text = ""
    raw_error = ""
    try:
        raw_text = str(run_rcon_raw("playerlist") or "")
    except Exception as e:
        raw_error = str(e)
        log_debug("RCON", f"raw backend exception: {e}", flag="debug_rcon")

    raw_cleaned_lines = _clean_lines(raw_text)
    log_debug("RCON", f"raw cleaned lines preview: {raw_cleaned_lines[:5]}", flag="debug_rcon")
    raw_players = parse_rcon_playerlist("\n".join(raw_cleaned_lines)) if raw_cleaned_lines else {}
    if raw_players:
        return _build_success(raw_players, "RCON_RAW")
    if raw_cleaned_lines and (not _has_transport_error(raw_text)):
        return _build_success({}, "RCON_RAW")

    fallback_text = ""
    fallback_error = ""
    try:
        fallback_text = str(run_rcon("playerlist") or "")
    except Exception as e:
        fallback_error = str(e)
        log_debug("RCON", f"fallback backend exception: {e}", flag="debug_rcon")

    fallback_cleaned_lines = _clean_lines(fallback_text)
    log_debug("RCON", f"fallback cleaned lines preview: {fallback_cleaned_lines[:5]}", flag="debug_rcon")
    fallback_players = parse_rcon_playerlist("\n".join(fallback_cleaned_lines)) if fallback_cleaned_lines else {}
    if fallback_players:
        return _build_success(fallback_players, "RCONCLI")
    if fallback_cleaned_lines and (not _has_transport_error(fallback_text)):
        return _build_success({}, "RCONCLI")

    error_parts = []
    if raw_error:
        error_parts.append(f"raw={raw_error}")
    if fallback_error:
        error_parts.append(f"fallback={fallback_error}")
    if _has_transport_error(raw_text):
        error_parts.append("raw transport error")
    if _has_transport_error(fallback_text):
        error_parts.append("fallback transport error")
    if not error_parts:
        error_parts.append("unusable playerlist response")
    return {
        "success": False,
        "players": {},
        "player_count": 0,
        "source": "RCON",
        "error": "; ".join(error_parts),
    }


def get_players_from_rcon():
    first_result = get_rcon_playerlist()
    if first_result.get("success", False):
        log_info("RCON", f"player poll OK via {first_result.get('source', 'RCON')} (players={int(first_result.get('player_count', 0))})")
        return first_result
    time.sleep(0.8)
    second_result = get_rcon_playerlist()
    if second_result.get("success", False):
        log_info("RCON", f"player poll OK via {second_result.get('source', 'RCON')} (players={int(second_result.get('player_count', 0))})")
        return second_result
    error_text = str(second_result.get("error", "") or first_result.get("error", "") or "unknown error")
    log_error("RCON", f"player poll failed: {error_text}")
    return second_result


def restore_state():
    state = load_state()
    saved_online_since = state.get("online_since", {})
    saved_last_tick = state.get("last_minute_tick", {})
    saved_restart_cycle = state.get("restart_cycle_state", {})
    saved_bot_presence = state.get("bot_presence_state")
    saved_last_bot_seen_at = state.get("last_bot_seen_at", 0.0)
    now_ts = int(time.time())
    catchup_cap_minutes = get_max_reward_catchup_minutes()
    catchup_cap_seconds = catchup_cap_minutes * 60

    if isinstance(saved_online_since, dict):
        for k, v in saved_online_since.items():
            steam_id = str(k)
            try:
                restored_online_since = int(v)
            except Exception:
                continue

            missed_seconds = max(0, now_ts - restored_online_since)
            capped_seconds = min(missed_seconds, catchup_cap_seconds)
            if restored_online_since <= 0 or missed_seconds > catchup_cap_seconds:
                restored_online_since = now_ts - capped_seconds

            online_since[steam_id] = restored_online_since
            print(
                f"[RECOVERY] online_since steam={steam_id} restored={restored_online_since} "
                f"missed_minutes={missed_seconds // 60} capped_minutes={capped_seconds // 60}"
            )

    if isinstance(saved_last_tick, dict):
        for k, v in saved_last_tick.items():
            steam_id = str(k)
            try:
                restored_last_tick = int(v)
            except Exception:
                continue

            if steam_id not in online_since:
                continue

            missed_seconds = max(0, now_ts - restored_last_tick)
            capped_seconds = min(missed_seconds, catchup_cap_seconds)
            if restored_last_tick <= 0 or missed_seconds > catchup_cap_seconds:
                restored_last_tick = now_ts - capped_seconds
            if restored_last_tick < online_since[steam_id]:
                restored_last_tick = online_since[steam_id]

            default_rate = DEFAULT_ENERGY_RATE_PER_HOUR
            reward_estimate = int((capped_seconds * (default_rate / 3600.0)))
            last_minute_tick[steam_id] = restored_last_tick
            print(
                f"[RECOVERY] last_minute_tick steam={steam_id} restored={restored_last_tick} "
                f"missed_minutes={missed_seconds // 60} capped_minutes={capped_seconds // 60} "
                f"estimated_reward={reward_estimate}"
            )

    if isinstance(saved_restart_cycle, dict):
        restart_cycle_state.update(saved_restart_cycle)
    if isinstance(saved_bot_presence, str) and saved_bot_presence:
        bot_runtime_state["presence_state"] = saved_bot_presence
    try:
        bot_runtime_state["last_bot_seen_at"] = float(saved_last_bot_seen_at or 0.0)
    except Exception:
        bot_runtime_state["last_bot_seen_at"] = 0.0


def ensure_player_record(data: dict, steam_id: str, name: str):
    if steam_id not in data:
        data[steam_id] = {
            "name": name,
            "steam_id": steam_id,
            "total_minutes": 0,
            "current_session_minutes": 0,
            "energy": get_starting_energy(),
            "energy_fraction": 0.0,
            "sessions": 0,
        }
        print(f"[PLAYER INIT] created new player steam={steam_id} with starting_energy={get_starting_energy()}")
        return "created"

    player = data[steam_id]
    original_energy = player.get("energy")
    original_total = player.get("total_minutes")
    merged = False
    defaults = {
        "name": name,
        "steam_id": steam_id,
        "total_minutes": 0,
        "current_session_minutes": 0,
        "energy": get_starting_energy(),
        "energy_fraction": 0.0,
        "sessions": 0,
    }
    for k, v in defaults.items():
        if k not in player:
            player[k] = v
            merged = True
    player["name"] = name
    player["steam_id"] = steam_id
    if merged:
        print(f"[PLAYER INIT] merged missing fields only for steam={steam_id}")
    print(f"[PLAYER INIT] existing player preserved steam={steam_id} energy={original_energy} total_minutes={original_total}")
    return "existing"


def update_players(players):
    data = load_json(DATA_FILE, {})
    now = int(time.time())
    current_ids = set(players.keys())
    if not players:
        print("[TRACKING] empty playerlist detected after restart/offline event")
        print("[TRACKING] preserving stored player data")

    for steam_id, name in players.items():
        status = ensure_player_record(data, steam_id, name)
        if status == "existing":
            print(f"[TRACKING] restored known player steam={steam_id}")

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
    with ECONOMY_LOCK:
        data = load_json(DATA_FILE, {})
        now = int(time.time())
        reward_interval_seconds = get_reward_interval_seconds()
        catchup_cap_seconds = get_max_reward_catchup_minutes() * 60

        for steam_id in list(online_since.keys()):
            if steam_id not in data:
                continue

            last_tick = last_minute_tick.get(steam_id, now)
            if not isinstance(last_tick, int) or last_tick <= 0:
                last_tick = now
            elapsed = now - last_tick
            if elapsed < 0:
                last_tick = now
                elapsed = 0
            if elapsed > catchup_cap_seconds:
                print(
                    f"[REWARD] Catch-up clamped steam={steam_id} elapsed_minutes={elapsed // 60} "
                    f"cap_minutes={catchup_cap_seconds // 60}"
                )
                elapsed = catchup_cap_seconds
                last_tick = now - elapsed

            if elapsed < reward_interval_seconds:
                continue

            intervals = elapsed // reward_interval_seconds
            if intervals <= 0:
                continue

            player = data[steam_id]
            add_seconds = intervals * reward_interval_seconds
            gained_minutes = add_seconds / 60.0
            old_total = float(player.get("total_minutes", 0))
            new_total = old_total + gained_minutes

            rate_per_hour = get_player_energy_rate_per_hour(steam_id)
            energy_per_second = rate_per_hour / 3600.0
            energy_to_add = add_seconds * energy_per_second + float(player.get("energy_fraction", 0.0))
            gained_energy_int = int(energy_to_add)
            player["energy_fraction"] = max(0.0, energy_to_add - gained_energy_int)

            player["total_minutes"] = int(new_total)
            player["current_session_minutes"] = int((now - online_since[steam_id]) // 60)

            if gained_energy_int > 0:
                adjust_energy_in_data(data, steam_id, int(gained_energy_int))
                player = data[steam_id]
                print(
                    f"[REWARD] {player.get('name', steam_id)} | "
                    f"{steam_id} | +{gained_energy_int} energy | "
                    f"total={player['total_minutes']} mins | "
                    f"energy={player['energy']}"
                )

            last_minute_tick[steam_id] = int(last_tick + add_seconds)

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


def find_existing_active_claim_group_id(commands_data, steam_id: str, item: str):
    normalized_item = str(item or "").lower().strip()
    for command_entry in commands_data:
        if (
            command_entry.get("steam_id") == steam_id
            and str(command_entry.get("item", "")).lower().strip() == normalized_item
            and command_entry.get("status") in {"PENDING", "EXECUTING"}
            and command_entry.get("claim_group_id")
        ):
            return command_entry.get("claim_group_id")
    return None


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

    ok, _, _ = refund_player_energy(steam_id, int(price), reason=f"Purchase refund ({item})")
    if not ok:
        return False

    purchase["refund_applied"] = True
    purchase["refund_amount"] = int(price)
    purchase["refunded_at"] = str(datetime.now())
    purchase["refund_note"] = f"{reason_suffix}".strip()
    return True


def fail_purchase_with_refund(purchase: dict, status: str, delivery_note: str, failure_note: str):
    if purchase.get("status") == "DELIVERED":
        return False
    refund_purchase_energy_if_needed(purchase, delivery_note)
    if status not in {"FAILED", "CANCELLED_TIMEOUT"}:
        status = "FAILED"
    set_purchase_status(purchase, status, delivery_note, failure_note)
    return True


def queue_claim_phase_commands(purchase, player_name: str, phase: str):
    game_commands = load_game_commands()
    next_id = get_next_command_id(game_commands)
    steam_id = purchase["steam_id"]
    item = str(purchase.get("item", "")).lower().strip()
    claim_group_id = purchase.get("claim_group_id")

    precheck_default = ["/health {steam_id} 100"]
    pre_grow_default = [
        "/diet1 {steam_id} 100",
        "/diet2 {steam_id} 100",
        "/diet3 {steam_id} 100",
        "/health {steam_id} 100",
    ]
    claim_default = [
        "/growth {steam_id} 65",
        "/diet1 {steam_id} 100",
        "/diet2 {steam_id} 100",
        "/diet3 {steam_id} 100",
        "/hunger {steam_id} 100",
        "/health {steam_id} 100",
    ]

    if phase == "PRECHECK":
        raw_sequence = ConfigManager.get("claim_precheck_commands", "CLAIM_PRECHECK_COMMANDS", precheck_default)
    elif phase == "RECOVERY":
        raw_sequence = ConfigManager.get("claim_recovery_commands", "CLAIM_RECOVERY_COMMANDS", pre_grow_default)
    else:
        raw_sequence = ConfigManager.get("claim_commands", "CLAIM_COMMANDS", claim_default)
    if not isinstance(raw_sequence, list) or not raw_sequence:
        raw_sequence = precheck_default if phase == "PRECHECK" else claim_default
    sequence = [str(cmd).format(steam_id=steam_id) for cmd in raw_sequence]

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
            "command_type": "recovery_command" if phase == "RECOVERY" else "claim_command",
        })

    save_game_commands(game_commands)
    return sequence


def process_claim_orchestration():
    with ECONOMY_LOCK:
        purchases = load_purchases()
        game_commands = load_game_commands()
        changed_purchases = False
        changed_commands = False

        for purchase in purchases:
            status = purchase.get("status")
            claim_group_id = purchase.get("claim_group_id")
            steam_id = purchase.get("steam_id")
            item = str(purchase.get("item", "")).lower().strip()

            if status == "PRECHECK_QUEUED" and claim_group_id:
                if any_group_step_failed(game_commands, claim_group_id):
                    fail_purchase_with_refund(
                        purchase,
                        "FAILED",
                        "Claim failed. Points refunded.",
                        "Claim failed. Points refunded.",
                    )
                    changed_purchases = True
                    continue

                if has_pending_group_commands(game_commands, claim_group_id):
                    continue

                if all_group_steps_done(game_commands, claim_group_id, "PRECHECK"):
                    set_purchase_status(purchase, "PRECHECK_VERIFYING")
                    purchase["precheck_verify_started_at"] = str(datetime.now())
                    purchase["delivery_note"] = "Verification in progress — 35% complete."
                    changed_purchases = True
                continue

            if status == "PRECHECK_VERIFYING" and claim_group_id:
                verify_started_at = parse_dt(purchase.get("precheck_verify_started_at"))
                verify_start_ts = time.time()
                elapsed_secs = 0
                if verify_started_at:
                    elapsed_secs = max(0, int((datetime.now() - verify_started_at).total_seconds()))
                max_attempts = max(1, PRECHECK_VERIFY_TIMEOUT_SECONDS)
                attempt_no = min(max_attempts, elapsed_secs + 1)
                print(f"[CLAIM] precheck verify attempt {attempt_no}/{max_attempts}")
                precheck_log = get_latest_health_log_for_steam(steam_id)
                if not precheck_log:
                    print(f"[CLAIM] waiting for health log for steam_id={steam_id}")
                    if verify_started_at and (datetime.now() - verify_started_at).total_seconds() >= PRECHECK_VERIFY_TIMEOUT_SECONDS:
                        fail_purchase_with_refund(
                            purchase,
                            "FAILED",
                            "Verification timed out. Points refunded.",
                            "Verification timed out. Points refunded.",
                        )
                        print(f"[CLAIM] precheck verify duration={time.time() - verify_start_ts:.3f}s")
                        print(f"[CLAIM VERIFY] Verification timed out for {steam_id} (pre-check)")
                        changed_purchases = True
                    continue

                if not classes_match(item, precheck_log["class_name"]):
                    reason = f"Claim blocked: expected {item}, detected class {precheck_log['class_name']}."
                    cancel_reason = f"Claim group cancelled: wrong dino detected ({precheck_log['class_name']})"
                    if cancel_claim_group_commands(game_commands, claim_group_id, cancel_reason):
                        changed_commands = True
                    refund_purchase_energy_if_needed(purchase, reason)
                    set_purchase_status(
                        purchase,
                        "WRONG_DINO_REFUNDED",
                        "Wrong dinosaur detected. Your points were refunded.",
                        f"{reason} Energy refunded.",
                    )
                    print(f"[CLAIM] wrong dino detected expected={item} actual={precheck_log['class_name']}")
                    print(f"[CLAIM] precheck verify duration={time.time() - verify_start_ts:.3f}s")
                    changed_purchases = True
                    continue

                set_purchase_status(purchase, "PRECHECK_PASSED")
                purchase["delivery_note"] = (
                    "Verification passed. Growth queued — 75% complete."
                )
                purchase["failure_note"] = "Verification passed. Growth queued."
                print(f"[CLAIM VERIFY] Pre-check passed for {steam_id} on class {precheck_log['class_name']}")
                print(f"[CLAIM] precheck verify duration={time.time() - verify_start_ts:.3f}s")
                changed_purchases = True

                player_name = purchase.get("player") or "Unknown"
                if ConfigManager.get_bool("claim_use_recovery_chain", "CLAIM_USE_RECOVERY_CHAIN", True):
                    queue_claim_phase_commands(purchase, player_name, "RECOVERY")
                queue_claim_phase_commands(purchase, player_name, "CLAIM")
                set_purchase_status(purchase, "CLAIM_SEQUENCE_QUEUED")
                purchase["delivery_note"] = "Growth queued — 75% complete."
                changed_purchases = True
                game_commands = load_game_commands()
                continue

            if status == "CLAIM_SEQUENCE_QUEUED" and claim_group_id:
                if any_group_step_failed(game_commands, claim_group_id):
                    fail_purchase_with_refund(
                        purchase,
                        "FAILED",
                        "Claim failed. Points refunded.",
                        "Claim failed. Points refunded.",
                    )
                    changed_purchases = True
                    continue

                if has_pending_group_commands(game_commands, claim_group_id):
                    continue

                if all_group_steps_done(game_commands, claim_group_id, "CLAIM"):
                    set_purchase_status(purchase, "FINAL_VERIFY_PENDING")
                    purchase["final_verify_started_at"] = str(datetime.now())
                    purchase["delivery_note"] = "Final verification in progress — 90% complete."
                    changed_purchases = True
                    continue

            if status == "FINAL_VERIFY_PENDING":
                final_started_at = parse_dt(purchase.get("final_verify_started_at"))
                final_start_ts = time.time()
                final_elapsed_secs = 0
                if final_started_at:
                    final_elapsed_secs = max(0, int((datetime.now() - final_started_at).total_seconds()))
                final_max_attempts = max(1, FINAL_VERIFY_TIMEOUT_SECONDS)
                final_attempt = min(final_max_attempts, final_elapsed_secs + 1)
                print(f"[CLAIM] final grow verify attempt {final_attempt}/{final_max_attempts}")
                grow_log = get_latest_grow_log_for_steam(steam_id)
                if not grow_log:
                    print(f"[CLAIM] waiting for grow log for steam_id={steam_id}")
                    if final_started_at and (datetime.now() - final_started_at).total_seconds() >= FINAL_VERIFY_TIMEOUT_SECONDS:
                        fail_purchase_with_refund(
                            purchase,
                            "FAILED",
                            "Verification timed out. Points refunded.",
                            "Verification timed out. Points refunded.",
                        )
                        print(f"[CLAIM] final verify duration={time.time() - final_start_ts:.3f}s")
                        print(f"[CLAIM VERIFY] Verification timed out for {steam_id} (final verify)")
                        changed_purchases = True
                    continue

                growth_ok, growth_note = verify_growth_log_for_purchase(purchase, grow_log)
                if not growth_ok:
                    max_retries = ConfigManager.get_int("claim_retry_limit", "CLAIM_RETRY_LIMIT", 1, minimum=0)
                    retries_used = int(purchase.get("retry_count", 0))
                    if retries_used < max_retries:
                        purchase["retry_count"] = retries_used + 1
                        purchase["retry_reason"] = growth_note
                        purchase["status"] = "PRECHECK_PASSED"
                        purchase["delivery_note"] = f"Retrying claim ({purchase['retry_count']}/{max_retries})"
                        changed_purchases = True
                        continue
                    fail_purchase_with_refund(
                        purchase,
                        "FAILED",
                        "Claim failed. Points refunded.",
                        "Claim failed. Points refunded.",
                    )
                    print(f"[CLAIM] final verify duration={time.time() - final_start_ts:.3f}s")
                    changed_purchases = True
                    continue

                set_purchase_status(purchase, "DELIVERED", growth_note, "Growth confirmed — 100% complete.")
                print(f"[CLAIM] final verify duration={time.time() - final_start_ts:.3f}s")
                changed_purchases = True

        if changed_commands:
            save_game_commands(game_commands)
        if changed_purchases:
            save_purchases(purchases)


def process_game_command_queue():
    # Command execution ownership is handled exclusively by in_game_executor.py.
    # This bot-side function only orchestrates claim-state transitions from metadata/logs.
    process_claim_orchestration()


def enforce_claim_watchdog_timeout():
    active_states = {
        "PRECHECK_QUEUED",
        "PRECHECK_VERIFYING",
        "PRECHECK_PASSED",
        "CLAIM_SEQUENCE_QUEUED",
        "FINAL_VERIFY_PENDING",
    }
    now = datetime.now()
    with ECONOMY_LOCK:
        purchases = load_purchases()
        game_commands = load_game_commands()
        changed_purchases = False
        changed_commands = False

        for purchase in purchases:
            if purchase.get("status") not in active_states:
                continue

            started_at = parse_dt(purchase.get("claim_started_at")) or parse_dt(purchase.get("claimed_at")) or parse_dt(purchase.get("time"))
            if not started_at:
                purchase["claim_started_at"] = str(now)
                changed_purchases = True
                continue

            if (now - started_at).total_seconds() <= CLAIM_ACTIVE_TIMEOUT_SECONDS:
                continue

            claim_group_id = purchase.get("claim_group_id")
            if claim_group_id and cancel_claim_group_commands(
                game_commands,
                claim_group_id,
                f"Claim timed out after {CLAIM_ACTIVE_TIMEOUT_SECONDS} seconds.",
            ):
                changed_commands = True

            timeout_note = f"Claim timed out after {CLAIM_ACTIVE_TIMEOUT_SECONDS} seconds. Points refunded."
            fail_purchase_with_refund(
                purchase,
                "CANCELLED_TIMEOUT",
                timeout_note,
                "Claim timed out — points refunded.",
            )
            purchase["timeout_at"] = str(now)
            changed_purchases = True

        if changed_commands:
            save_game_commands(game_commands)
        if changed_purchases:
            save_purchases(purchases)


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
                refund_player_energy(steam_id, int(energy_reward), reason=f"Referral milestone {invite_count}")
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
        await refresh_patreon_role_cache()
        snapshot = await asyncio.to_thread(poll_player_snapshot)
        if snapshot.get("success", False):
            players = normalize_players_map(snapshot.get("players", {}))
            bot_runtime_state["last_successful_players"] = dict(players)
            update_players(players)
        else:
            players = normalize_players_map(bot_runtime_state.get("last_successful_players", {}))
            log_limited("tracking_poll_failed_keep_state", 30, "TRACKING", "player poll failed; preserving last successful snapshot", level="warn")
        tick_rewards()
        expire_old_purchases()
        await asyncio.to_thread(process_game_command_queue)
        await asyncio.to_thread(enforce_claim_watchdog_timeout)
        await asyncio.to_thread(process_restart_announcements)
        await process_restart_discord_updates()
        await process_server_health_updates()
        await process_executor_health_updates()
        await process_bot_presence_and_recovery(snapshot)
        if time.time() - float(bot_runtime_state.get("last_tracking_summary_at", 0.0) or 0.0) >= 60:
            log_info("TRACKING", f"Players online: {len(players)}")
            bot_runtime_state["last_tracking_summary_at"] = time.time()
        print_live_status(players)
    except Exception as e:
        log_error("TRACKING", f"loop failed: {e}")


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
    global MAIN_LOOP
    hydrate_runtime_secrets()
    log_info("STARTUP", "Bot logged in")
    MAIN_LOOP = asyncio.get_running_loop()
    restore_state()
    if bot_runtime_state.get("startup_initialized"):
        log_limited("startup_duplicate_on_ready", 120, "STARTUP", "duplicate on_ready prevented")
        if not tracking_loop.is_running():
            tracking_loop.change_interval(seconds=get_scan_interval_seconds())
            tracking_loop.start()
            log_info("TRACKING", "background tracking loop online")
        if not announcement_loop.is_running():
            announcement_loop.start()
            log_info("ANNOUNCEMENTS", "started")
        return
    bot_runtime_state["admin_bot_state"] = "UNKNOWN"
    bot_runtime_state["startup_started_at"] = time.time()
    bot_runtime_state["startup_tracking_checked"] = False
    bot_runtime_state["startup_rcon_checked"] = False
    bot_runtime_state["startup_warmup_complete_logged"] = False
    bot_runtime_state["startup_warmup_banner_logged"] = False
    bot_runtime_state["startup_initialized"] = True
    log_limited("startup_warmup_banner", 120, "STARTUP", "warmup active")
    cfg_presence = get_bot_presence_config()
    log_info("STARTUP", f"Admin bot config loaded (steam_id={str(cfg_presence.get('steam_id', '')).strip() or '(empty)'})")
    if (not str(cfg_presence.get("player_name", "")).strip()) and (not str(cfg_presence.get("steam_id", "")).strip()):
        log_warn("STARTUP", "no admin bot identity configured")
    startup_snapshot = await asyncio.to_thread(poll_player_snapshot)
    if startup_snapshot.get("success", False):
        startup_players = normalize_players_map(startup_snapshot.get("players", {}))
        bot_runtime_state["last_successful_players"] = dict(startup_players)
        update_players(startup_players)
    else:
        startup_players = normalize_players_map(bot_runtime_state.get("last_successful_players", {}))
        log_warn("STARTUP", "initial player poll failed; using last successful snapshot")
    await process_bot_presence_and_recovery(startup_snapshot)
    admin_state = str(bot_runtime_state.get("admin_bot_state", "UNKNOWN")).lower()
    log_info("STARTUP", f"Initial player poll OK (players={len(startup_players)}, admin_bot={admin_state})")

    if not tracking_loop.is_running():
        tracking_loop.change_interval(seconds=get_scan_interval_seconds())
        tracking_loop.start()
        log_info("TRACKING", "background tracking loop online")
    else:
        log_limited("tracking_duplicate_start", 120, "TRACKING", "duplicate start prevented")

    if not announcement_loop.is_running():
        announcement_loop.start()
    else:
        log_limited("announcement_duplicate_start", 120, "ANNOUNCEMENTS", "duplicate start prevented")
    log_info("ANNOUNCEMENTS", "started")

    for guild in bot.guilds:
        await cache_guild_invites(guild)
    await send_restart_incident(
        "Recovery Complete",
        "Bot systems reconnected and monitoring has resumed.",
        discord.Color.green(),
    )
    await refresh_admin_dashboard(force=True)


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
    data = load_json(DATA_FILE, {})
    if steam_id not in data:
        data[steam_id] = {
            "name": ctx.author.display_name,
            "steam_id": steam_id,
            "total_minutes": 0,
            "current_session_minutes": 0,
            "energy": get_starting_energy(),
            "energy_fraction": 0.0,
            "sessions": 0,
        }
        save_json(DATA_FILE, data)
    await ctx.send(embed=build_action_embed("Account Linked", "Your Steam account has been linked.", ctx.author.display_name, int(data.get(steam_id, {}).get("energy", get_starting_energy())), discord.Color.green()))


@bot.command()
async def stats(ctx):
    expire_old_purchases()

    player, steam_id, data, _ = get_latest_player_record_by_discord_id(str(ctx.author.id))
    if not player:
        await ctx.send("❌ Use !link first")
        return

    player = data.get(steam_id, player)

    previous_total = int(player.get("total_minutes", 0))
    current_session = int(player.get("current_session_minutes", 0))
    combined_total = previous_total + current_session
    energy = int(player.get("energy", 0))
    name = player.get("name", "Unknown")

    await ctx.send(
        f"📊 **{name}**\n"
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
    if is_admin_bot_offline():
        print("[BUY BLOCKED] admin bot offline")
        record_manual_issue(ctx, "!buy", item)
        await ctx.send("⚠️ Purchases are temporarily disabled while the admin bot is offline. Please open a support ticket.")
        return
    if str(bot_runtime_state.get("admin_bot_state", "")).upper() == "GRACE":
        await ctx.send("⚠️ Admin bot temporarily unavailable (grace period active). Request may be delayed.")

    item = item.lower().strip()
    price, category = find_shop_price(item)

    if price is None:
        await ctx.send("❌ Item not found")
        return

    if category == "extras":
        await ctx.send("❌ Extras are not part of the prime claim flow")
        return

    response_message = None
    with ECONOMY_LOCK:
        data = load_json(DATA_FILE, {})
        links = load_json(LINK_FILE, {})
        purchases = load_purchases()

        steam_id = get_steam_id_for_discord(str(ctx.author.id), links)
        player = data.get(steam_id) if steam_id else None
        if not player:
            response_message = "❌ Use !link first"
        elif any(p.get("steam_id") == steam_id and p.get("status") in CLAIM_OPEN_STATES for p in purchases):
            response_message = "❌ You already have an active purchase. Use `!claim` first."
        elif int(player.get("energy", 0)) < int(price):
            response_message = "❌ Not enough energy"
        else:
            duplicate_unclaimed = any(
                p.get("steam_id") == steam_id
                and str(p.get("item", "")).lower().strip() == item
                and p.get("status") in CLAIM_OPEN_STATES
                for p in purchases
            )
            if duplicate_unclaimed:
                response_message = "❌ You already have an active purchase for this dino. Use `!claim` first."
            else:
                _, after = adjust_energy_in_data(data, steam_id, -int(price))
                save_json(DATA_FILE, data)
                new_purchase = {
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
                    "economy_note": f"Buy deducted {price} energy @ {datetime.now()}",
                }
                purchases.append(new_purchase)
                try:
                    save_purchases(purchases)
                    response_message = (
                        f"🧬 **{item.upper()} PURCHASED**\n\n"
                        f"⚡ -{price} energy\n"
                        f"💰 Remaining energy: {after}\n"
                        f"📦 Claim saved\n"
                        f"⏳ Expires in {PURCHASE_TIMEOUT_MINUTES} minutes if not claimed\n\n"
                        f"Use `!claim` when you are ready to be primed."
                    )
                except Exception:
                    adjust_energy_in_data(data, steam_id, int(price))
                    save_json(DATA_FILE, data)
                    response_message = "❌ Purchase failed to save. Your energy was restored."

    await ctx.send(response_message or "❌ Purchase failed unexpectedly.")


@bot.command()
async def claim(ctx):
    expire_old_purchases()
    if is_admin_bot_offline():
        print("[CLAIM BLOCKED] admin bot offline")
        record_manual_issue(ctx, "!claim", "")
        await ctx.send("⚠️ Claims are temporarily disabled while the admin bot is offline. Please open a support ticket.")
        return
    if str(bot_runtime_state.get("admin_bot_state", "")).upper() == "GRACE":
        await ctx.send("⚠️ Admin bot temporarily unavailable (grace period active). Claim may be delayed.")

    player, steam_id = get_player(ctx)

    if not player:
        await ctx.send("❌ Use !link first")
        return

    with ECONOMY_LOCK:
        purchases = load_purchases()
        purchase_index, purchase_status = get_claimable_purchase_index(purchases, steam_id)

        if purchase_index is None:
            latest_mine = None
            for p in reversed(purchases):
                if p.get("steam_id") == steam_id:
                    latest_mine = p
                    break
            if latest_mine and latest_mine.get("status") == "WRONG_DINO_REFUNDED":
                response = (
                    "❌ Claim blocked: wrong dino detected on your last attempt. "
                    "Your energy has been refunded. Switch dinos and buy again when ready."
                )
            elif latest_mine and latest_mine.get("status") == "FAILED":
                if latest_mine.get("timeout_reason") == "claim_queue_timeout":
                    response = "⚠️ Your claim timed out after 2 minutes. Your energy has been refunded. Please run !claim again."
                else:
                    note = latest_mine.get("delivery_note") or "⚠️ Verification timed out. No grow was applied."
                    response = f"⚠️ {note}"
            else:
                response = "❌ You do not have any active dinosaur purchases."
        else:
            purchase = purchases[purchase_index]
            if purchase_status in {"PRECHECK_QUEUED", "PRECHECK_VERIFYING", "PRECHECK_PASSED", "CLAIM_SEQUENCE_QUEUED", "FINAL_VERIFY_PENDING"}:
                status_label, pct = get_claim_status_display(purchase.get("status"))
                pct_text = f"{pct}% complete" if pct is not None else "in progress"
                response = (
                    f"⏳ Your claim is already in progress — {pct_text}.\n"
                    f"Status: {status_label}."
                )
            else:
                game_commands = load_game_commands()
                existing_group_id = find_existing_active_claim_group_id(
                    game_commands,
                    steam_id,
                    purchase.get("item", ""),
                )
                if existing_group_id:
                    purchase["status"] = "PRECHECK_QUEUED"
                    purchase["claim_started_at"] = purchase.get("claim_started_at") or str(datetime.now())
                    purchase["claimed_at"] = str(datetime.now())
                    purchase["delivery_note"] = "Existing pending command found"
                    purchase["claim_group_id"] = existing_group_id
                    save_purchases(purchases)
                    response = "Your claim is already in progress — 20% complete."
                else:
                    if not purchase.get("claim_group_id"):
                        purchase["claim_group_id"] = f"claim_{steam_id}_{uuid.uuid4().hex[:10]}"
                    queue_claim_phase_commands(purchase, player["name"], "PRECHECK")
                    purchase["status"] = "PRECHECK_QUEUED"
                    purchase["claim_started_at"] = str(datetime.now())
                    purchase["claimed_at"] = str(datetime.now())
                    purchase["delivery_note"] = "Pre-check queued. Awaiting health/class verification."
                    purchase["failure_note"] = None
                    save_purchases(purchases)
                    response = "Pre-check queued — 20% complete. Stay on the dinosaur you bought."

    sent_message = await ctx.send(response)
    with ECONOMY_LOCK:
        purchases = load_purchases()
        target = None
        for p in reversed(purchases):
            if p.get("steam_id") == steam_id and p.get("status") in CLAIM_OPEN_STATES.union({"FAILED", "WRONG_DINO_REFUNDED", "DELIVERED"}):
                target = p
                break
        if target and target.get("claim_group_id"):
            target["progress_channel_id"] = int(ctx.channel.id)
            target["progress_message_id"] = int(sent_message.id)
            target["progress_guild_id"] = int(ctx.guild.id) if ctx.guild else None
            target["progress_user_id"] = int(ctx.author.id)
            save_purchases(purchases)


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
        label, pct = get_claim_status_display(p.get("status"))
        pct_text = f"{pct}% complete" if pct is not None else "Not completed"
        extra_note = clean_claim_note_for_user(p.get("failure_note") or p.get("delivery_note") or "")
        lines.append(
            f"{p.get('item', '?').upper()} — {label} — {pct_text}"
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


@bot.command()
async def patreon(ctx):
    embed = discord.Embed(
        title="Patreon Benefits",
        description="Support the server and unlock higher passive energy rates.",
        color=discord.Color.gold(),
    )
    embed.add_field(name="Supporter", value="18 energy/hour", inline=False)
    embed.add_field(name="VIP", value="22.5 energy/hour", inline=False)
    embed.add_field(name="Apex Supporter", value="30 energy/hour", inline=False)
    embed.add_field(name="Default", value="15 energy/hour", inline=False)
    await ctx.send(embed=embed)


@bot.command()
async def tiers(ctx):
    await patreon(ctx)


@bot.command()
async def checktier(ctx):
    await refresh_patreon_role_cache(force=True)
    links = load_json(LINK_FILE, {})
    steam_id = links.get(str(ctx.author.id))
    if not steam_id:
        await ctx.send(embed=build_action_embed("Tier Check", "Use `!link <steamid>` first.", ctx.author.display_name, None, discord.Color.red()))
        return
    info = patreon_role_cache.get(str(steam_id), {})
    tier = info.get("tier", "Default")
    rate = float(info.get("rate_per_hour", DEFAULT_ENERGY_RATE_PER_HOUR))
    data = load_json(DATA_FILE, {})
    energy = int(data.get(str(steam_id), {}).get("energy", 0))
    embed = build_action_embed(
        "Current Tier",
        f"Tier: **{tier}**\nRate: **{rate}/hour**",
        ctx.author.display_name,
        energy,
        discord.Color.green(),
    )
    await ctx.send(embed=embed)


@bot.command()
async def botstatus(ctx):
    if not (ctx.author.guild_permissions and ctx.author.guild_permissions.administrator):
        await ctx.send("❌ Admin only.")
        return
    await refresh_admin_dashboard(force=True)
    await ctx.send(embed=build_admin_dashboard_embed())


@bot.command()
async def botissues(ctx):
    if not (ctx.author.guild_permissions and ctx.author.guild_permissions.administrator):
        await ctx.send("❌ Admin only.")
        return
    issues = admin_runtime_state.get("manual_issues", [])
    if not issues:
        await ctx.send("No blocked requests recorded in the current outage.")
        return
    lines = ["Recent blocked requests:"]
    for issue in issues[-10:]:
        lines.append(
            f"- {issue.get('timestamp')} | {issue.get('command')} | "
            f"{issue.get('username')} ({issue.get('user_id')}) {issue.get('item')}"
        )
    await ctx.send("\n".join(lines))


if __name__ == "__main__":
    hydrate_runtime_secrets()
    bot.run(TOKEN)
