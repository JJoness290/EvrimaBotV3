import asyncio
import discord
from discord.ext import commands, tasks
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
import subprocess
import time

TOKEN = "MTQ4NjQ2NTQ4ODczNjQ4NTQ0Ng.GSiZhh.u074voiptO6mC4zIuF6lsD2U59V1APSCrrLugg"

DATA_FILE = Path("player_data.json")
STATE_FILE = Path("player_state.json")
LINK_FILE = Path("links.json")
SHOP_FILE = Path("shop.json")
PURCHASES_FILE = Path("purchases.json")
GAME_COMMANDS_FILE = Path("game_commands.json")
REFERRALS_FILE = Path("referrals.json")
CONFIG_FILE = Path("config.json")

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

announcement_messages = [
    "=== PRIMAL ABYSS ===\nNew Survival Universe\nEarn Energy • !buy & !claim PRIME\ndiscord.gg/HpJVNa69Ww"
]

RCON_SCRIPT = r"C:\Users\joshu\Downloads\The-Isle-Evrima-Server-Tools-main\TheIsle_RCON.py"
RCONCLI_PATH = r"C:\Users\joshu\Documents\EvrimaBot\RconCli\bin\Release\net8.0\RconCli.exe"
RCON_IP = "68.168.208.54"
RCON_PORT = "11218"
RCON_PASSWORD = "qFHrZpel6qwF"
ANNOUNCEMENT_INTERVAL_SECONDS = 600

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

        elif status == "QUEUED_FOR_PRIME":
            claimed_at = parse_dt(purchase.get("claimed_at", "")) or parse_dt(purchase.get("time", ""))
            if not claimed_at:
                continue

            if now - claimed_at >= timedelta(minutes=QUEUED_TIMEOUT_MINUTES):
                for cmd in game_commands:
                    if (
                        cmd.get("steam_id") == steam_id
                        and str(cmd.get("item", "")).lower().strip() == item
                        and cmd.get("status") in {"PENDING", "SENDING"}
                    ):
                        cmd["status"] = "EXPIRED"
                        cmd["completed_at"] = str(datetime.now())
                        changed_commands = True

                purchase["status"] = "UNCLAIMED"
                purchase["delivery_note"] = f"Prime queue expired after {QUEUED_TIMEOUT_MINUTES} minutes"
                changed_purchases = True

    if changed_purchases:
        save_purchases(purchases)
    if changed_data:
        save_json(DATA_FILE, data)
    if changed_commands:
        save_game_commands(game_commands)


def has_open_purchase(steam_id: str) -> bool:
    purchases = load_purchases()
    open_statuses = {"UNCLAIMED", "QUEUED_FOR_PRIME", "CLAIMING"}
    return any(
        p.get("steam_id") == steam_id and p.get("status") in open_statuses
        for p in purchases
    )


def get_claimable_purchase_index(purchases, steam_id: str):
    for i in range(len(purchases) - 1, -1, -1):
        p = purchases[i]
        if p.get("steam_id") == steam_id and p.get("status") == "UNCLAIMED":
            return i, "UNCLAIMED"

    for i in range(len(purchases) - 1, -1, -1):
        p = purchases[i]
        if p.get("steam_id") == steam_id and p.get("status") == "QUEUED_FOR_PRIME":
            return i, "QUEUED_FOR_PRIME"

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


def process_game_command_queue():
    game_commands = load_game_commands()
    purchases = load_purchases()

    changed_commands = False
    changed_purchases = False

    for cmd in game_commands:
        if cmd.get("status") != "PENDING":
            continue

        steam_id = cmd.get("steam_id")
        item = str(cmd.get("item", "")).lower().strip()
        command_text = cmd.get("command", "")

        cmd["status"] = "SENDING"
        changed_commands = True

        try:
            run_rcon(command_text)
            cmd["status"] = "SENT"
            cmd["completed_at"] = str(datetime.now())
            print(f"[CLAIM QUEUED] {steam_id} | {item} | {command_text}")

            final_command_text = f"/hunger {steam_id} 100"
            if str(command_text).strip() == final_command_text:
                completed_statuses = {"SENT", "DONE"}
                sent_for_purchase = [
                    c for c in game_commands
                    if c.get("steam_id") == steam_id
                    and str(c.get("item", "")).lower().strip() == item
                    and c.get("status") in completed_statuses
                ]
                sent_texts = [str(c.get("command", "")).strip() for c in sent_for_purchase]

                elder_count = sum(1 for t in sent_texts if t == f"/elder {steam_id} prime")
                hunger_30_seen = any(t == f"/hunger {steam_id} 30" for t in sent_texts)
                hunger_100_count = sum(1 for t in sent_texts if t == final_command_text)

                if elder_count >= 2 and hunger_30_seen and hunger_100_count >= 2:
                    for purchase in purchases:
                        if (
                            purchase.get("steam_id") == steam_id
                            and str(purchase.get("item", "")).lower().strip() == item
                            and purchase.get("status") == "QUEUED_FOR_PRIME"
                        ):
                            purchase["status"] = "DELIVERED"
                            purchase["delivery_note"] = command_text
                            changed_purchases = True

        except Exception as e:
            cmd["status"] = "FAILED"
            cmd["completed_at"] = str(datetime.now())
            cmd["error"] = str(e)
            print(f"[ERROR] claim queue send failed: {e}")

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
        await ctx.send("❌ You do not have any active dinosaur purchases.")
        return

    if purchase_status == "QUEUED_FOR_PRIME":
        purchase = purchases[purchase_index]
        await ctx.send(
            f"⏳ Your prime is already queued.\n\n"
            f"🧬 Dino: **{purchase['item'].upper()}**\n"
            f"Use `!myclaims` to check status, or wait for the bridge."
        )
        return

    game_commands = load_game_commands()

    claim_sequence_commands = [
        f"/elder {steam_id} prime",
        f"/hunger {steam_id} 100",
        f"/hunger {steam_id} 30",
        f"/elder {steam_id} prime",
        f"/hunger {steam_id} 100",
    ]

    existing_pending = any(
        cmd.get("steam_id") == steam_id
        and str(cmd.get("item", "")).lower().strip() == str(purchases[purchase_index]["item"]).lower().strip()
        and str(cmd.get("command", "")) in set(claim_sequence_commands)
        and cmd.get("status") in {"PENDING", "SENDING", "EXECUTING"}
        for cmd in game_commands
    )
    if existing_pending:
        purchases[purchase_index]["status"] = "QUEUED_FOR_PRIME"
        purchases[purchase_index]["claimed_at"] = str(datetime.now())
        purchases[purchase_index]["delivery_note"] = "Existing pending prime command found"
        save_purchases(purchases)

        await ctx.send("⏳ Your prime is already queued and waiting to be sent.")
        return

    next_id = get_next_command_id(game_commands)
    for idx, command_text in enumerate(claim_sequence_commands):
        game_commands.append({
            "id": f"cmd_{next_id + idx:03d}",
            "steam_id": steam_id,
            "player_name": player["name"],
            "item": purchases[purchase_index]["item"],
            "command": command_text,
            "status": "PENDING",
            "created_at": str(datetime.now()),
            "completed_at": None
        })
    save_game_commands(game_commands)

    purchases[purchase_index]["status"] = "QUEUED_FOR_PRIME"
    purchases[purchase_index]["claimed_at"] = str(datetime.now())
    purchases[purchase_index]["delivery_note"] = " | ".join(claim_sequence_commands)
    save_purchases(purchases)

    print(f"[CLAIM QUEUED] {player['name']} | {steam_id} | {' ; '.join(claim_sequence_commands)}")

    queued_commands_display = "\n".join(
        f"{i + 1}. `{command}`" for i, command in enumerate(claim_sequence_commands)
    )

    await ctx.send(
        f"⚡ **PRIME QUEUED**\n\n"
        f"🧬 Dino: **{purchases[purchase_index]['item'].upper()}**\n"
        f"👤 Player: **{player['name']}**\n"
        f"📨 Commands queued:\n{queued_commands_display}\n\n"
        f"Stay in game while the admin bridge sends it."
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
        lines.append(
            f"{p.get('item', '?')} — {p.get('status', '?')} — {p.get('time', '?')}"
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
