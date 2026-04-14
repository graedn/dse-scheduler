import logging
import traceback
import discord
from discord.ext import commands
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Callable

from database import Database
from parser import has_required_structure, has_partial_structure, parse_post, ParseError
from scheduler import (
    best_combination, score_combination, combo_match_ids,
    is_weekend, accept_combination, propose_change,
)

ET = ZoneInfo("America/New_York")
log = logging.getLogger(__name__)


class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot, db: Database, get_teamup: Callable):
        self.bot = bot
        self.db = db
        self.get_teamup = get_teamup

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

        match_id = self.db.insert_match(
            division=parsed.division,
            week=parsed.week,
            team_home=parsed.team_home,
            team_away=parsed.team_away,
            match_time=parsed.match_time,
            posted_at=int(message.created_at.timestamp()),
        )

        match_date = datetime.fromtimestamp(parsed.match_time, tz=ET).strftime("%Y-%m-%d")

        if self.db.get_blocked_day(match_date):
            return

        try:
            all_matches = self.db.get_matches_for_date(match_date)
            scheduled = self.db.get_scheduled_matches_for_date(match_date)
            best = best_combination(all_matches, self.db)

            if best is None:
                log_ch = self._get_log_channel()
                if log_ch:
                    await log_ch.send(
                        f"ℹ️ Match stored for **{match_date}** "
                        f"({len(all_matches)} match{'es' if len(all_matches) != 1 else ''} posted that day — "
                        f"need at least 2 to schedule)."
                    )
                return

            teamup = self.get_teamup()
            broadcast_ch = self._get_broadcast_channel()
            log_ch = self._get_log_channel()

            if not scheduled:
                if teamup:
                    await accept_combination(best, match_date, self.db, teamup, broadcast_ch)
                    if log_ch:
                        lines = ", ".join(
                            f"{m['team_home']} vs {m['team_away']}" for m in best
                        )
                        await log_ch.send(
                            f"📅 TeamUp updated for **{match_date}**: {lines}"
                        )
                else:
                    msg = f"⚠️ TeamUp not configured — match for {match_date} stored but not added to calendar."
                    log.warning(msg)
                    if log_ch:
                        await log_ch.send(msg)
            else:
                weekend = is_weekend(scheduled[0]["match_time"])
                current_score = score_combination(scheduled, weekend, self.db)
                proposed_score = score_combination(best, is_weekend(best[0]["match_time"]), self.db)
                if combo_match_ids(best) == combo_match_ids(scheduled):
                    return
                if proposed_score <= current_score:
                    return
                if broadcast_ch is None:
                    msg = (
                        f"⚠️ Broadcast channel not configured — dropping proposal for "
                        f"{match_date} (match_id={match_id})"
                    )
                    log.warning(msg)
                    if log_ch:
                        await log_ch.send(msg)
                    return
                await propose_change(
                    match_date, scheduled, best, current_score, proposed_score,
                    self.db, broadcast_ch,
                )
        except Exception:
            tb = traceback.format_exc()
            log.exception("Scheduling failed for match_id=%s on %s", match_id, match_date)
            log_ch = self._get_log_channel()
            if log_ch:
                await log_ch.send(
                    f"❌ **Scheduling error** for match_id={match_id} on {match_date}:\n"
                    f"```{tb[-1500:]}```"
                )

    async def _flag_missing_timestamp(self, message: discord.Message):
        """DM the player when their post has the right structure but no Discord timestamp."""
        dm_text = (
            f"⚠️ Your match post is missing a Discord timestamp.\n\n"
            f"The `Time:` field needs a Discord timestamp tag so the bot can read the exact time. "
            f"Visit **hammertime.cyou**, pick your match time, and copy the generated tag.\n\n"
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
        """DM the player with what specifically failed. Fall back to a reply if DMs are off."""
        dm_text = (
            f"⚠️ Your match post couldn't be parsed.\n"
            f"**Issue:** {reason}\n\n"
            f"**Your post:**\n```{message.content[:500]}```\n"
            f"Please fix the issue and repost."
        )
        try:
            await message.author.send(dm_text)
        except discord.Forbidden:
            # User has DMs disabled — reply quietly in channel
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

    def _get_log_channel(self):
        ch_id = self.db.get_config("log_channel_id")
        if ch_id:
            return self.bot.get_channel(int(ch_id))
        return None

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return
        if str(payload.emoji) != "❌":
            return
        # Validate that the reaction is in the configured broadcast channel
        broadcast_ch_id = self.db.get_config("broadcast_channel_id")
        if not broadcast_ch_id or str(payload.channel_id) != broadcast_ch_id:
            return
        change = self.db.get_pending_change_by_message(str(payload.message_id))
        if not change:
            return
        self.db.resolve_pending_change(change["id"], approved=False)
        broadcast_ch = self._get_broadcast_channel()
        if broadcast_ch:
            await broadcast_ch.send("❌ Schedule proposal rejected.")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        channel_id = str(channel.id)
        match_ch = self.db.get_config("match_channel_id")
        broadcast_ch_id = self.db.get_config("broadcast_channel_id")
        warning = (
            "⚠️ A configured channel was deleted. "
            "Use `/set-match-channel` or `/set-broadcast-channel` to reconfigure."
        )
        if channel_id == match_ch:
            self.db.delete_config("match_channel_id")
            remaining = self._get_broadcast_channel()
            if remaining:
                await remaining.send(warning)
        if channel_id == broadcast_ch_id:
            self.db.delete_config("broadcast_channel_id")
            log.warning("Broadcast channel %s deleted. No channel to send warning.", channel_id)
