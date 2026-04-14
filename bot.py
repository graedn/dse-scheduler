import os
import asyncio
import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

from database import Database
from teamup import TeamUpClient
from scheduler import run_daily_sweep, build_matches_announcement
from cogs.admin import AdminCog
from cogs.blocks import BlocksCog
from cogs.events import EventsCog

load_dotenv()
ET = ZoneInfo("America/New_York")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)
db = Database()
scheduler = AsyncIOScheduler(timezone=ET)


def get_teamup() -> "TeamUpClient | None":
    api_key = db.get_config("teamup_api_key")
    calendar_key = db.get_config("teamup_calendar_id")
    if api_key and calendar_key:
        return TeamUpClient(api_key, calendar_key)
    return None


async def announce_job():
    broadcast_ch_id = db.get_config("broadcast_channel_id")
    broadcast_ch = bot.get_channel(int(broadcast_ch_id)) if broadcast_ch_id else None
    if broadcast_ch:
        msg = build_matches_announcement(db)
        if msg:
            await broadcast_ch.send(msg)
    else:
        print("[announce] Skipped — broadcast channel not configured.")


async def daily_sweep_job():
    teamup = get_teamup()
    broadcast_ch_id = db.get_config("broadcast_channel_id")
    broadcast_ch = bot.get_channel(int(broadcast_ch_id)) if broadcast_ch_id else None
    if teamup and broadcast_ch:
        await run_daily_sweep(db, teamup, broadcast_ch)
    else:
        print("[sweep] Skipped — missing TeamUp credentials or broadcast channel.")


GUILD_IDS = [
    1493650865238577172, # user server
    1493657000922451989,  # admin server replace with your server ID(s)
]

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    # Sync commands to each guild first (instant), then clear global to remove duplicates
    for guild_id in GUILD_IDS:
        guild = discord.Object(id=guild_id)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print(f"Slash commands synced to guild {guild_id}.")
    bot.tree.clear_commands(guild=None)
    await bot.tree.sync()
    if not scheduler.running:
        scheduler.start()
        print("Scheduler started.")


async def main():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN not set in .env")

    scheduler.add_job(daily_sweep_job, "cron", hour=3, minute=0)
    scheduler.add_job(announce_job, "cron", hour=11, minute=0)   # 11am ET
    scheduler.add_job(announce_job, "cron", hour=23, minute=0)   # 11pm ET

    await bot.add_cog(AdminCog(bot, db, get_teamup))
    await bot.add_cog(BlocksCog(bot, db, get_teamup))
    await bot.add_cog(EventsCog(bot, db, get_teamup))

    try:
        await bot.start(token)
    finally:
        await bot.close()
        scheduler.shutdown(wait=False)
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
