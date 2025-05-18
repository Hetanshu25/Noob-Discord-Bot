import discord
from dotenv import load_dotenv

load_dotenv()
from discord.ext import commands, tasks
from datetime import datetime, timezone
import aiosqlite
from datetime import datetime, timedelta, timezone
import os
import asyncio
from aiohttp import web

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.messages = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
DB_PATH = "activity.db"


# --- DATABASE SETUP ---
async def setup_database():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS activity (
                user_id INTEGER PRIMARY KEY,
                last_active TEXT
            )
        """)
        await db.commit()


async def update_activity(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO activity (user_id, last_active)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET last_active=excluded.last_active
        """, (user_id, datetime.now(timezone.utc).isoformat()))
        await db.commit()


async def get_last_active(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
                "SELECT last_active FROM activity WHERE user_id = ?",
            (user_id, )) as cursor:
            row = await cursor.fetchone()
            if row:
                last_active = datetime.fromisoformat(row[0])
                if last_active.tzinfo is None:
                    last_active = last_active.replace(tzinfo=timezone.utc)
                return last_active
            return None


# --- BOT EVENTS ---


@bot.event
async def on_message(message):
    if message.author.bot:
        return
    await update_activity(message.author.id)
    await bot.process_commands(message)


@bot.event
async def on_voice_state_update(member, before, after):
    if before.channel != after.channel:
        await update_activity(member.id)


# --- INACTIVITY CHECK LOOP ---


@tasks.loop(hours=10)
async def check_inactive_members():
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(days=30)  # your test window

    for guild in bot.guilds:
        inactive_role = discord.utils.get(guild.roles, name="Inactive")
        if not inactive_role:
            inactive_role = await guild.create_role(
                name="Inactive", reason="Inactive role created")

        for member in guild.members:
            if member.bot:
                continue

            last_active = await get_last_active(member.id)
            if last_active is None or last_active < threshold:
                if inactive_role not in member.roles:
                    try:
                        print(
                            f"Marking {member} as Inactive (last_active={last_active})"
                        )
                        await member.add_roles(inactive_role,
                                               reason="Inactive for 30+ days")
                    except discord.Forbidden:
                        print(
                            f"⚠️ No permission to add Inactive role to {member}"
                        )
            else:
                if inactive_role in member.roles:
                    try:
                        print(f"Removing Inactive from {member}")
                        await member.remove_roles(
                            inactive_role, reason="User is active again")
                    except discord.Forbidden:
                        print(
                            f"⚠️ No permission to remove Inactive role from {member}"
                        )


# --- ADMIN COMMAND TO MARK ALL MEMBERS ACTIVE ---


@bot.command()
@commands.has_permissions(administrator=True)
async def mark_active(ctx):
    for member in ctx.guild.members:
        if not member.bot:
            await update_activity(member.id)
    await ctx.send("✅ All members have been marked as active.")


@bot.command()
@commands.has_permissions(administrator=True)
async def last_actives(ctx, limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
                "SELECT user_id, last_active FROM activity ORDER BY last_active ASC LIMIT ?",
            (limit, )) as cursor:
            rows = await cursor.fetchall()
            msg = "UserID | Last Active\n"
            for user_id, last_active in rows:
                msg += f"{user_id} | {last_active}\n"
            await ctx.send(f"```{msg}```")


@bot.command()
async def ping(ctx):
    await ctx.send("Pong! Bot is working.")


async def handle_ping(request):
    return web.Response(text="Bot is alive!")


async def start_webserver():
    app = web.Application()
    app.add_routes([web.get('/', handle_ping)])

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()

    print("✅ Webserver started on http://0.0.0.0:8080")


@bot.event
async def on_ready():
    print(f'✅ Logged in as {bot.user}')
    await setup_database()
    check_inactive_members.start()

    # Start the webserver in the background
    bot.loop.create_task(start_webserver())


# --- Start Bot ---
token = os.getenv("DISCORD_TOKEN")
if token is None:
    raise ValueError(
        "DISCORD_TOKEN environment variable not found. Please set it in Replit Secrets."
    )
bot.run(token)
