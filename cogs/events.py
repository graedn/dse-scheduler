import logging
import traceback
import discord
from discord.ext import commands
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Callable

from database import Database
from parser import has_required_structure, has_partial_structure, parse_post, ParseError

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

    async def _handle_reschedule(self, old_match: dict, parsed) -> None:
        """Dispatch to correct handling based on old match state. Full logic added in Task 4."""
        status = old_match.get("status", "pending")
        if status not in ("pending", "criteria_met"):
            log.warning(
                "Reschedule attempted on match %s with status %r — skipping (full state handling in Task 4)",
                old_match["id"], status,
            )
            return

        old_ts = old_match["match_time"]
        new_ts = parsed.match_time
        old_date = datetime.fromtimestamp(old_ts, tz=ET).strftime("%Y-%m-%d")
        new_date = datetime.fromtimestamp(new_ts, tz=ET).strftime("%Y-%m-%d")

        self.db.clear_match_from_proposal_slots(old_match["id"])
        self.db.delete_match_cascade(old_match["id"])
        self.db.insert_match(
            division=parsed.division,
            week=parsed.week,
            team_home=parsed.team_home,
            team_away=parsed.team_away,
            match_time=new_ts,
            posted_at=int(datetime.now(tz=ET).timestamp()),
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
