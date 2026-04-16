import os
import asyncio
import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

from database import Database
from teamup import TeamUpClient
from scheduler import run_daily_sweep, run_morning_check, build_matches_announcement
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

# Expose db and get_teamup on the bot so persistent view callbacks can reach them
bot.db = db


def get_teamup() -> "TeamUpClient | None":
    api_key = db.get_config("teamup_api_key")
    calendar_key = db.get_config("teamup_calendar_id")
    if api_key and calendar_key:
        return TeamUpClient(api_key, calendar_key)
    return None

bot.get_teamup = get_teamup


async def announce_job():
    log_ch_id = db.get_config("log_channel_id")
    log_ch = bot.get_channel(int(log_ch_id)) if log_ch_id else None
    if log_ch:
        msg = build_matches_announcement(db)
        if msg:
            await log_ch.send(msg)
    else:
        print("[announce] Skipped — log channel not configured.")


async def morning_job():
    teamup = get_teamup()
    broadcast_ch_id = db.get_config("broadcast_channel_id")
    signup_ch_id = db.get_config("signup_channel_id")
    broadcast_ch = bot.get_channel(int(broadcast_ch_id)) if broadcast_ch_id else None
    signup_ch = bot.get_channel(int(signup_ch_id)) if signup_ch_id else None
    if teamup and broadcast_ch:
        await run_morning_check(db, teamup, broadcast_ch, signup_channel=signup_ch)
    else:
        print("[morning] Skipped — missing TeamUp credentials or broadcast channel.")


async def deadline_check_job():
    """Every 5 min: handle sign-up deadlines and call-time cancellations."""
    import logging
    log = logging.getLogger(__name__)
    from cogs.talent import send_allocation_request
    from scheduler import build_signup_message, is_fully_staffed, _SEPARATOR
    log_ch_id = db.get_config("log_channel_id")
    broadcast_ch_id = db.get_config("broadcast_channel_id")
    signup_ch_id = db.get_config("signup_channel_id")
    log_ch = bot.get_channel(int(log_ch_id)) if log_ch_id else None
    broadcast_ch = bot.get_channel(int(broadcast_ch_id)) if broadcast_ch_id else None
    signup_ch_id = db.get_config("signup_channel_id")
    signup_ch = bot.get_channel(int(signup_ch_id)) if signup_ch_id else broadcast_ch
    if not log_ch:
        return

    # --- Past deadline: either trigger allocation or mark LAST CALL ---
    for match in db.get_matches_past_deadline():
        signups = db.get_signups_for_match(match["id"])
        if is_fully_staffed(signups):
            await send_allocation_request(db, match, log_ch, broadcast_ch,
                                          get_teamup=get_teamup)
        else:
            # Not enough sign-ups yet — mark last_call and edit the sign-up message
            db.create_allocation(match["id"])
            db.set_allocation_status(match["id"], "last_call")
            bcast = db.get_broadcast_message(match["id"])
            if bcast and signup_ch:
                try:
                    msg = await signup_ch.fetch_message(int(bcast["discord_message_id"]))
                    new_content = build_signup_message(match, signups, last_call=True)
                    await msg.edit(content=new_content)
                except Exception as e:
                    log.warning("LAST CALL: failed to edit sign-up message for match %s: %s",
                                match["id"], e)

    # --- Past call time: cancel matches that are still understaffed ---
    teamup = get_teamup()
    for match in db.get_matches_past_calltime_last_call():
        signups = db.get_signups_for_match(match["id"])
        if is_fully_staffed(signups):
            # Somehow filled after LAST CALL — send allocation now
            db.set_allocation_status(match["id"], "pending")
            db.reset_allocation(match["id"])
            await send_allocation_request(db, match, log_ch, broadcast_ch,
                                          get_teamup=get_teamup)
            continue

        # Remove from calendar
        event_id = match.get("teamup_event_id")
        if teamup and event_id:
            try:
                teamup.delete_event(event_id)
            except Exception as e:
                log.warning("Cancel: failed to delete TeamUp event %s: %s", event_id, e)
        if event_id:
            db.update_match_teamup_id(match["id"], None)
            db.decrement_scheduled_count(match["team_home"])
            db.decrement_scheduled_count(match["team_away"])
        db.set_allocation_status(match["id"], "cancelled")

        # Edit the sign-up message
        bcast = db.get_broadcast_message(match["id"])
        if bcast and signup_ch:
            try:
                msg = await signup_ch.fetch_message(int(bcast["discord_message_id"]))
                ts = match["match_time"]
                await msg.edit(
                    content=(
                        f"{_SEPARATOR}\n"
                        f"📋 [{match['division']}] {match['team_home']} vs {match['team_away']}\n"
                        f"<t:{ts}:F>\n\n"
                        f"❌ Broadcast cancelled — insufficient sign-ups by call time."
                    ),
                    view=discord.ui.View(),
                )
            except Exception as e:
                log.warning("Cancel: failed to edit sign-up message for match %s: %s",
                            match["id"], e)

        if log_ch:
            await log_ch.send(
                f"❌ **[{match['division']}] {match['team_home']} vs {match['team_away']}** — "
                f"broadcast cancelled: required roles were not filled by call time."
            )


async def daily_sweep_job():
    teamup = get_teamup()
    broadcast_ch_id = db.get_config("broadcast_channel_id")
    signup_ch_id = db.get_config("signup_channel_id")
    broadcast_ch = bot.get_channel(int(broadcast_ch_id)) if broadcast_ch_id else None
    signup_ch = bot.get_channel(int(signup_ch_id)) if signup_ch_id else None
    if teamup and broadcast_ch:
        await run_daily_sweep(db, teamup, broadcast_ch, signup_channel=signup_ch)
    else:
        print("[sweep] Skipped — missing TeamUp credentials or broadcast channel.")


GUILD_IDS = [
    1493650865238577172, # user server
    1493657000922451989,  # admin server replace with your server ID(s)
]

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    # Re-register persistent sign-up views for all active matches so buttons
    # keep working after a bot restart.
    from cogs.signup import SignUpView, ApprovedSignUpView
    active_matches = db.get_all_active_sign_up_matches()
    for match in active_matches:
        bot.add_view(SignUpView(match["id"]))
    print(f"Registered {len(active_matches)} persistent sign-up view(s).")

    accepted_matches = db.get_accepted_broadcast_matches()
    for match in accepted_matches:
        bot.add_view(ApprovedSignUpView(match["id"]))
    print(f"Registered {len(accepted_matches)} persistent approved sign-up view(s).")

    from cogs.proposal import ProposalView
    pending_changes = db.get_all_pending_changes()
    for change in pending_changes:
        bot.add_view(ProposalView(change["id"]))
    print(f"Registered {len(pending_changes)} persistent proposal view(s).")

    from cogs.confirm_view import ConfirmationView
    awaiting = db.get_all_awaiting_confirmation_matches()
    for row in awaiting:
        bot.add_view(ConfirmationView(row["match_id"]))
    print(f"Registered {len(awaiting)} persistent confirmation view(s).")
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
    scheduler.add_job(morning_job, "cron", hour=9, minute=0)     # 9am ET day-of check
    scheduler.add_job(announce_job, "cron", hour=11, minute=0)   # 11am ET
    scheduler.add_job(announce_job, "cron", hour=23, minute=0)   # 11pm ET
    scheduler.add_job(deadline_check_job, "interval", minutes=5, # sign-up deadline check
                      max_instances=1, coalesce=True)

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
