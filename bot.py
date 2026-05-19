import os
import asyncio
import logging
import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from zoneinfo import ZoneInfo
from datetime import datetime

from database import Database
from teamup import TeamUpClient
from scheduler import (
    build_signup_message, is_fully_staffed, _SEPARATOR,
)
from cogs.admin import AdminCog
from cogs.blocks import BlocksCog
from cogs.events import EventsCog
from cogs.talent import send_allocation_request
from cogs.weekly_proposals import WeeklyProposalsCog, create_weekly_proposals, mark_passed_proposals
from cogs.threads import ThreadsCog, ReadyCheckView, send_ready_check

load_dotenv()
ET = ZoneInfo("America/New_York")
log = logging.getLogger(__name__)

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


async def scan_job():
    """Match channel scan — runs at 9am, 6pm, and 11:59pm ET (skipped on Sundays)."""
    if datetime.now(tz=ET).weekday() == 6:  # Sunday = 6
        print("[scan] Skipped — Sunday is reserved for weekly proposals.")
        return

    events_cog = bot.cogs.get("EventsCog")
    if not events_cog:
        print("[scan] Skipped — EventsCog not loaded.")
        return

    try:
        await events_cog._scan_match_history(limit=500)
    except Exception as e:
        log.error("[scan] Match history scan failed: %s", e)

    # Mark any open proposals whose dates have now passed
    try:
        await mark_passed_proposals(db, bot)
    except Exception as e:
        log.error("[scan] mark_passed_proposals failed: %s", e)


async def weekly_proposals_job():
    """Sunday 11pm ET: create or update the upcoming week's 7 proposal messages."""
    try:
        await create_weekly_proposals(bot, db)
        print("[weekly_proposals] Weekly proposal messages created/updated.")
    except Exception as e:
        log.error("[weekly_proposals] Failed to create weekly proposals: %s", e)


async def deadline_check_job():
    """Every 5 min: handle sign-up deadlines and call-time cancellations."""
    log_ch_id = db.get_config("log_channel_id")
    broadcast_ch_id = db.get_config("broadcast_channel_id")
    signup_ch_id = db.get_config("signup_channel_id")
    log_ch = bot.get_channel(int(log_ch_id)) if log_ch_id else None
    broadcast_ch = bot.get_channel(int(broadcast_ch_id)) if broadcast_ch_id else None
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
            try:
                await log_ch.send(
                    f"❌ **[{match['division']}] {match['team_home']} vs {match['team_away']}** — "
                    f"broadcast cancelled: required roles were not filled by call time."
                )
            except Exception as e:
                log.error("Cancel: failed to send log message for match %s: %s",
                          match["id"], e)

    # --- Ready check: accepted matches within 30 min that have a thread ---
    for match in db.get_approved_matches_needing_ready_check():
        try:
            await send_ready_check(bot, match)
        except Exception as e:
            log.error("Ready check failed for match %s: %s", match["id"], e)


GUILD_IDS = [
    1493650865238577172, # user server
    1493657000922451989, # admin server
    1396460856883155078, # test server 1
    1214565847419457576, # test server 2
]

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

    # Re-register persistent sign-up views
    from cogs.signup import SignUpView, ApprovedSignUpView
    active_matches = db.get_all_active_sign_up_matches()
    for match in active_matches:
        bot.add_view(SignUpView(match["id"]))
    print(f"Registered {len(active_matches)} persistent sign-up view(s).")

    accepted_matches = db.get_accepted_broadcast_matches()
    for match in accepted_matches:
        bot.add_view(ApprovedSignUpView(match["id"]))
    print(f"Registered {len(accepted_matches)} persistent approved sign-up view(s).")

    # Re-register persistent proposal day views (for open proposals)
    from cogs.weekly_proposals import ProposalDayView, BlockedDayView
    open_proposals = db.get_open_proposal_messages()
    for proposal in open_proposals:
        date_str = proposal["date"]
        all_matches = db.get_matches_for_date(date_str)
        slot1_id = proposal.get("slot1_match_id")
        slot2_id = proposal.get("slot2_match_id")
        bot.add_view(ProposalDayView(date_str, all_matches,
                                    slot1_match_id=slot1_id, slot2_match_id=slot2_id))
    print(f"Registered {len(open_proposals)} persistent proposal day view(s).")
    blocked_proposals = db.get_blocked_proposal_messages()
    for proposal in blocked_proposals:
        bot.add_view(BlockedDayView(proposal["date"]))
    print(f"Registered {len(blocked_proposals)} persistent blocked day view(s).")

    from cogs.confirm_view import ConfirmationView
    awaiting = db.get_all_awaiting_confirmation_matches()
    for row in awaiting:
        bot.add_view(ConfirmationView(row["match_id"]))
    print(f"Registered {len(awaiting)} persistent confirmation view(s).")

    # Re-register ReadyCheckViews for threads that have an active ready check
    pending_rcs = db.get_all_threads_with_pending_ready_check()
    for row in pending_rcs:
        bot.add_view(ReadyCheckView(row["match_id"]))
    print(f"Registered {len(pending_rcs)} persistent ready-check view(s).")

    # Sync commands to each guild
    for guild_id in GUILD_IDS:
        guild = discord.Object(id=guild_id)
        bot.tree.copy_global_to(guild=guild)
        try:
            synced = await bot.tree.sync(guild=guild)
            print(f"Slash commands synced to guild {guild_id}: {len(synced)} command(s).")
        except discord.Forbidden:
            print(f"ERROR: Missing 'applications.commands' scope in guild {guild_id}.")
        except discord.HTTPException as e:
            print(f"ERROR syncing to guild {guild_id}: {e}")
    bot.tree.clear_commands(guild=None)
    await bot.tree.sync()
    if not scheduler.running:
        scheduler.start()
        print("Scheduler started.")

    # Catch-up: if the bot was offline at the Sunday 23:00 ET transition,
    # APScheduler does not run the missed cron job — recreate this week's
    # proposal messages now if they're absent.
    try:
        from cogs.weekly_proposals import recover_missed_weekly_proposals
        await recover_missed_weekly_proposals(bot, db)
    except Exception as e:
        log.error("[weekly_proposals] startup recovery failed: %s", e)


async def main():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN not set in .env")

    # Scheduled jobs (all Eastern Time, all skipped on Sunday except weekly_proposals)
    scheduler.add_job(scan_job, "cron", hour=9,  minute=0)     # 9am scan
    scheduler.add_job(scan_job, "cron", hour=18, minute=0)     # 6pm scan
    scheduler.add_job(scan_job, "cron", hour=23, minute=59)    # 11:59pm scan
    scheduler.add_job(weekly_proposals_job, "cron",            # Sunday 11pm proposals
                      day_of_week="sun", hour=23, minute=0)
    scheduler.add_job(deadline_check_job, "interval",          # sign-up deadline check
                      minutes=5, max_instances=1, coalesce=True)

    await bot.add_cog(AdminCog(bot, db, get_teamup))
    await bot.add_cog(BlocksCog(bot, db, get_teamup))
    await bot.add_cog(EventsCog(bot, db, get_teamup))
    await bot.add_cog(WeeklyProposalsCog(bot, db))
    await bot.add_cog(ThreadsCog(bot, db))

    try:
        await bot.start(token)
    finally:
        await bot.close()
        scheduler.shutdown(wait=False)
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
