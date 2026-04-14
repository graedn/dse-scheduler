import os
import asyncio
import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

from database import Database
from teamup import TeamUpClient
from scheduler import run_daily_sweep
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


def get_teamup() -> "TeamUpClient | None":
    api_key = db.get_config("teamup_api_key")
    calendar_key = db.get_config("teamup_calendar_id")
    if api_key and calendar_key:
        return TeamUpClient(api_key, calendar_key)
    return None


async def daily_sweep_job():
    teamup = get_teamup()
    broadcast_ch_id = db.get_config("broadcast_channel_id")
    broadcast_ch = bot.get_channel(int(broadcast_ch_id)) if broadcast_ch_id else None
    if teamup and broadcast_ch:
        await run_daily_sweep(db, teamup, broadcast_ch)
    else:
        print("[sweep] Skipped — missing TeamUp credentials or broadcast channel.")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await bot.tree.sync()
    print("Slash commands synced.")
    scheduler.start()
    print("Scheduler started.")


async def main():
    global scheduler
    scheduler = AsyncIOScheduler(timezone=ET)
    scheduler.add_job(daily_sweep_job, "cron", hour=3, minute=0)

    await bot.add_cog(AdminCog(bot, db))
    await bot.add_cog(BlocksCog(bot, db, get_teamup))
    await bot.add_cog(EventsCog(bot, db, get_teamup))

    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN not set in .env")

    await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
