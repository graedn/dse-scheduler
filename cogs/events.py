import logging
import traceback
import discord
from discord.ext import commands
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Callable

from database import Database
from parser import has_required_structure, has_partial_structure, parse_post, ParseError
from scheduler import _SEPARATOR

ET = ZoneInfo("America/New_York")
log = logging.getLogger(__name__)


def _week_bounds(ts: int) -> tuple[int, int]:
    """Return (week_start_ts, week_end_ts) for the Mon–Sun ET week containing ts."""
    dt = datetime.fromtimestamp(ts, tz=ET)
    monday = (dt - timedelta(days=dt.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    sunday = (monday + timedelta(days=6)).replace(hour=23, minute=59, second=59)
    return int(monday.timestamp()), int(sunday.timestamp())


_REQUIRED_CONFIG = [
    "match_channel_id",
    "broadcast_channel_id",
    "teamup_api_key",
    "teamup_calendar_id",
]


class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot, db: Database, get_teamup: Callable):
        self.bot = bot
        self.db = db
        self.get_teamup = get_teamup
        self._history_scanned = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self._history_scanned:
            return
        if not all(self.db.get_config(k) for k in _REQUIRED_CONFIG):
            return
        self._history_scanned = True
        await self._scan_match_history()

    async def _scan_match_history(self, limit: int = 500) -> tuple[int, int]:
        """Scan the match channel history and log future matches not yet in the DB.

        After scanning, dispatches 'match_logged' for each date with newly found
        matches so proposal messages can be updated by the weekly proposals cog.

        Returns (new_match_count, affected_date_count).
        """
        match_ch_id = self.db.get_config("match_channel_id")
        channel = self.bot.get_channel(int(match_ch_id)) if match_ch_id else None
        if not channel:
            return 0, 0

        log_ch = self._get_log_channel()
        now_ts = int(datetime.now(tz=ET).timestamp())
        new_count = 0
        dates_with_new: set[str] = set()

        async for message in channel.history(limit=limit, oldest_first=False):
            if message.author.bot:
                continue
            if not has_required_structure(message.content):
                continue
            try:
                parsed = parse_post(message.content, self.db)
            except ParseError:
                continue
            if parsed.match_time <= now_ts:
                continue
            if self.db.match_exists(parsed.team_home, parsed.team_away, parsed.match_time):
                continue
            self.db.insert_match(
                division=parsed.division,
                week=parsed.week,
                team_home=parsed.team_home,
                team_away=parsed.team_away,
                match_time=parsed.match_time,
                posted_at=int(message.created_at.timestamp()),
            )
            new_count += 1
            match_date = datetime.fromtimestamp(parsed.match_time, tz=ET).strftime("%Y-%m-%d")
            dates_with_new.add(match_date)

        if log_ch:
            if new_count:
                await log_ch.send(
                    f"🔍 History scan complete: **{new_count}** future match(es) logged "
                    f"across **{len(dates_with_new)}** date(s)."
                )
            else:
                await log_ch.send("🔍 History scan complete: no new future matches found.")

        # Notify weekly proposals cog to update proposal messages for affected dates
        for date_str in sorted(dates_with_new):
            self.bot.dispatch("match_logged", date_str)

        return new_count, len(dates_with_new)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        match_channel_id = self.db.get_config("match_channel_id")
        if not match_channel_id or str(message.channel.id) != match_channel_id:
            return
        if not has_required_structure(message.content):
            if has_partial_structure(message.content):
                await self._flag_missing_timestamp(message)
            return

        try:
            parsed = parse_post(message.content, self.db)
        except ParseError as e:
            await self._flag_parse_error(message, str(e))
            return

        week_start, week_end = _week_bounds(parsed.match_time)
        old_match = self.db.get_match_by_teams_in_week(
            parsed.team_home, parsed.team_away, week_start, week_end
        )
        if old_match:
            if old_match["match_time"] == parsed.match_time:
                return  # True duplicate — silently ignore
            await self._handle_reschedule(old_match, parsed)
            return

        self.db.insert_match(
            division=parsed.division,
            week=parsed.week,
            team_home=parsed.team_home,
            team_away=parsed.team_away,
            match_time=parsed.match_time,
            posted_at=int(message.created_at.timestamp()),
        )

        match_date = datetime.fromtimestamp(parsed.match_time, tz=ET).strftime("%Y-%m-%d")

        log_ch = self._get_log_channel()
        if log_ch:
            try:
                await log_ch.send(
                    f"📋 Match logged for **{match_date}**: "
                    f"[{parsed.division}] {parsed.team_home} vs {parsed.team_away} "
                    f"— <t:{parsed.match_time}:F>"
                )
            except Exception:
                log.exception("Failed to send match log message")

        self.bot.dispatch("match_logged", match_date)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        match_channel_id = self.db.get_config("match_channel_id")
        if not match_channel_id or str(payload.channel_id) != match_channel_id:
            return

        channel = self.bot.get_channel(payload.channel_id)
        if not channel:
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return
        except discord.Forbidden:
            log.warning(
                "Missing Read Message History in channel %s — cannot process edit",
                payload.channel_id,
            )
            return
        except discord.HTTPException as e:
            log.warning("fetch_message failed for message %s: %s", payload.message_id, e)
            return

        if message.author.bot:
            return
        if not has_required_structure(message.content):
            return

        try:
            parsed = parse_post(message.content, self.db)
        except ParseError:
            return

        now_ts = int(datetime.now(tz=ET).timestamp())
        if parsed.match_time <= now_ts:
            return

        week_start, week_end = _week_bounds(parsed.match_time)
        old_match = self.db.get_match_by_teams_in_week(
            parsed.team_home, parsed.team_away, week_start, week_end
        )

        if not old_match:
            twin = self.db.get_scheduled_match_at_time_in_week(
                parsed.match_time, week_start, week_end
            )
            if twin and not (twin["team_home"] == parsed.team_home
                             and twin["team_away"] == parsed.team_away):
                if not self.db.match_exists(parsed.team_home, parsed.team_away,
                                            parsed.match_time):
                    self.db.insert_match(
                        division=parsed.division, week=parsed.week,
                        team_home=parsed.team_home, team_away=parsed.team_away,
                        match_time=parsed.match_time,
                        posted_at=int(message.created_at.timestamp()),
                    )
                new_match = self.db.get_match_by_teams_in_week(
                    parsed.team_home, parsed.team_away, week_start, week_end)
                if new_match:
                    from cogs.talent import carry_over_if_same_time
                    await carry_over_if_same_time(
                        self.bot, self.db, twin["id"], new_match["id"])
                    from cogs.confirm_view import cancel_orphaned_confirmation
                    await cancel_orphaned_confirmation(
                        self.bot, self.db, twin["id"],
                        reason="this slot's opponent changed (same time)")
                    self.db.clear_match_from_proposal_slots(twin["id"])
                    self.db.delete_match_cascade(twin["id"])
                    md = datetime.fromtimestamp(
                        parsed.match_time, tz=ET).strftime("%Y-%m-%d")
                    self.bot.dispatch("match_logged", md)
                return
            if not self.db.match_exists(parsed.team_home, parsed.team_away,
                                        parsed.match_time):
                self.db.insert_match(
                    division=parsed.division, week=parsed.week,
                    team_home=parsed.team_home, team_away=parsed.team_away,
                    match_time=parsed.match_time,
                    posted_at=int(message.created_at.timestamp()),
                )
                match_date = datetime.fromtimestamp(
                    parsed.match_time, tz=ET).strftime("%Y-%m-%d")
                self.bot.dispatch("match_logged", match_date)
            return

        if old_match["match_time"] == parsed.match_time:
            return  # no-op — same time, same teams

        await self._handle_reschedule(old_match, parsed)

    async def _handle_reschedule(self, old_match: dict, parsed) -> None:
        """State-based reschedule handler. Does NOT insert the new match for State 4."""
        from cogs.reschedule import RescheduleView

        old_ts = old_match["match_time"]
        new_ts = parsed.match_time
        old_mid = old_match["id"]
        old_date = datetime.fromtimestamp(old_ts, tz=ET).strftime("%Y-%m-%d")
        new_date = datetime.fromtimestamp(new_ts, tz=ET).strftime("%Y-%m-%d")
        db = self.db
        log_ch = self._get_log_channel()
        manager_role_id = db.get_config("manager_role_id")
        manager_mention = f"<@&{manager_role_id}> " if manager_role_id else ""
        match_label = (
            f"[{old_match['division']}] "
            f"{old_match['team_home']} vs {old_match['team_away']}"
        )

        # State 4: confirmed broadcast — post action buttons, leave DB alone
        if old_match.get("broadcast_accepted"):
            if log_ch:
                view = RescheduleView(old_mid, old_ts, new_ts)
                await log_ch.send(
                    f"⚠️ **Match Time Changed — Action Required** {manager_mention}\n"
                    f"📋 {match_label}\n"
                    f"~~<t:{old_ts}:F>~~ → <t:{new_ts}:F>\n\n"
                    f"This match has a confirmed broadcast. Choose how to handle the time change:",
                    view=view,
                )
            return

        # States 1–3: pre-confirmed — always delete old, insert new
        signups = db.get_signups_for_match(old_mid)
        bcast = db.get_broadcast_message(old_mid)
        status_note = ""

        if signups and bcast:
            # State 3: cancel sign-up message and notify talent
            signup_ch_id = (db.get_config("signup_channel_id")
                            or db.get_config("broadcast_channel_id"))
            signup_ch = self.bot.get_channel(int(signup_ch_id)) if signup_ch_id else None
            if signup_ch:
                try:
                    signup_msg = await signup_ch.fetch_message(
                        bcast["discord_message_id"]
                    )
                    await signup_msg.edit(
                        content=(
                            f"{_SEPARATOR}\n"
                            f"❌ **CANCELLED — Match Rescheduled**\n"
                            f"📋 {match_label}\n"
                            f"~~<t:{old_ts}:F>~~ → <t:{new_ts}:F>\n\n"
                            f"This match has been rescheduled. "
                            f"A new sign-up will be posted once confirmed."
                        ),
                        view=discord.ui.View(),
                    )
                except Exception:
                    log.exception(
                        "Failed to edit sign-up message on reschedule for match %s", old_mid
                    )

            all_uids = list({s["user_id"] for s in signups
                             if s["role"] != "unavailable"})
            mentions = " ".join(f"<@{uid}>" for uid in all_uids)
            updates_ch_id = db.get_config("schedule_updates_channel_id")
            updates_ch = (self.bot.get_channel(int(updates_ch_id)) if updates_ch_id else None) or signup_ch
            if updates_ch and all_uids:
                try:
                    await updates_ch.send(
                        f"📢 **Schedule Update** — {match_label} has been rescheduled.\n"
                        f"~~<t:{old_ts}:F>~~ → <t:{new_ts}:F>\n\n"
                        f"{mentions} — your sign-up has been removed. "
                        f"A new sign-up will be posted if the slot is rescheduled."
                    )
                except Exception:
                    log.exception(
                        "Failed to send talent reschedule notification for match %s", old_mid
                    )
            status_note = (
                "Sign-up cancelled. Signed-up talent have been notified."
                if all_uids else "Sign-up cancelled."
            )
        else:
            status_note = "Updated in Logged Matches."

        from cogs.confirm_view import cancel_orphaned_confirmation
        await cancel_orphaned_confirmation(
            self.bot, db, old_mid,
            reason="this match was rescheduled to a new time",
        )
        db.clear_match_from_proposal_slots(old_mid)
        db.delete_match_cascade(old_mid)
        db.insert_match(
            division=parsed.division,
            week=parsed.week,
            team_home=parsed.team_home,
            team_away=parsed.team_away,
            match_time=new_ts,
            posted_at=int(datetime.now(tz=ET).timestamp()),
        )

        if log_ch:
            try:
                await log_ch.send(
                    f"⚠️ **Match Rescheduled** {manager_mention}\n"
                    f"📋 {match_label}\n"
                    f"~~<t:{old_ts}:F>~~ → <t:{new_ts}:F>\n"
                    f"{status_note}"
                )
            except Exception:
                log.exception(
                    "Failed to send reschedule log notification for match %s", old_mid
                )

        self.bot.dispatch("match_logged", new_date)
        if old_date != new_date:
            self.bot.dispatch("match_logged", old_date)

    async def _flag_missing_timestamp(self, message: discord.Message):
        """DM the player when their post has the right structure but no Discord timestamp."""
        dm_text = (
            f"⚠️ Your match post is missing a Discord timestamp.\n\n"
            f"The `Time:` field needs a Discord timestamp so the bot can read the exact time. "
            f"Use the built-in `@time` command — it's simple:\n"
            f"• `today 10pm`\n"
            f"• `tuesday 18:00`\n"
            f"• `sunday 6pm`\n\n"
            f"⚠️ `@time` uses a **24-hour clock** — if you forget `pm`, it'll set an AM time.\n\n"
            f"Copy the tag it generates and paste it into your `Time:` field. "
            f"**It looks like this:**\n"
            f"```\nTime: <t:1713477600:F>\n```\n"
            f"**Your post:**\n```{message.content[:500]}```"
        )
        try:
            await message.author.send(dm_text)
        except discord.Forbidden:
            await message.reply(
                "⚠️ Your match post needs a Discord timestamp in the `Time:` field. "
                "Visit **hammertime.cyou** to generate one.",
                mention_author=True,
            )
        log_ch = self._get_log_channel()
        if log_ch:
            await log_ch.send(
                f"⚠️ **Missing timestamp** from {message.author.mention} "
                f"in {message.channel.mention}"
            )

    async def _flag_parse_error(self, message: discord.Message, reason: str):
        """DM the player with what specifically failed."""
        dm_text = (
            f"⚠️ Your match post couldn't be parsed.\n"
            f"**Issue:** {reason}\n\n"
            f"**Your post:**\n```{message.content[:500]}```\n"
            f"Please fix the issue and repost."
        )
        try:
            await message.author.send(dm_text)
        except discord.Forbidden:
            await message.reply(
                f"⚠️ Couldn't parse your match post. **Issue:** {reason}",
                mention_author=True,
            )

        log_ch = self._get_log_channel()
        if log_ch:
            await log_ch.send(
                f"⚠️ **Parse error** from {message.author.mention} "
                f"in {message.channel.mention}\n"
                f"**Issue:** {reason}"
            )

    def _get_broadcast_channel(self):
        ch_id = self.db.get_config("broadcast_channel_id")
        if ch_id:
            return self.bot.get_channel(int(ch_id))
        return None

    def _get_signup_channel(self):
        ch_id = self.db.get_config("signup_channel_id")
        if ch_id:
            return self.bot.get_channel(int(ch_id))
        return None

    def _get_log_channel(self):
        ch_id = self.db.get_config("log_channel_id")
        if ch_id:
            return self.bot.get_channel(int(ch_id))
        return None

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        channel_id = str(channel.id)
        match_ch = self.db.get_config("match_channel_id")
        broadcast_ch_id = self.db.get_config("broadcast_channel_id")
        signup_ch_id = self.db.get_config("signup_channel_id")
        log_ch = self._get_log_channel()

        if channel_id == match_ch:
            self.db.delete_config("match_channel_id")
            notify = log_ch or self._get_broadcast_channel()
            if notify:
                await notify.send(
                    "⚠️ The match channel was deleted. Use `/set-match-channel` to reconfigure."
                )
        if channel_id == broadcast_ch_id:
            self.db.delete_config("broadcast_channel_id")
            log.warning("Broadcast channel %s deleted. No channel to send warning.", channel_id)
            if log_ch:
                await log_ch.send(
                    "⚠️ The broadcast channel was deleted. "
                    "Use `/set-broadcast-channel` to reconfigure."
                )
        if channel_id == signup_ch_id:
            self.db.delete_config("signup_channel_id")
            notify = log_ch or self._get_broadcast_channel()
            if notify:
                await notify.send(
                    "⚠️ The sign-up channel was deleted. "
                    "Use `/set-signup-channel` to reconfigure."
                )
