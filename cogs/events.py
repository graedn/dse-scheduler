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
    accept_combination, schedule_for_date,
    apply_pending_change, _fmt_date,
    EMOJI_TO_ROLE, SIGNUP_EMOJIS,
    build_signup_message, is_fully_staffed,
    build_talent_description, MATCH_DURATION_H,
)

ET = ZoneInfo("America/New_York")
log = logging.getLogger(__name__)


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

    async def _scan_match_history(self):
        """Scan the match channel history and log future matches not yet in the DB.

        Skips past matches. Deduplicates by (team_home, team_away, match_time).
        After logging, attempts to schedule the best 2-match block for each new date.
        """
        match_ch_id = self.db.get_config("match_channel_id")
        channel = self.bot.get_channel(int(match_ch_id)) if match_ch_id else None
        if not channel:
            return

        log_ch = self._get_log_channel()
        broadcast_ch = self._get_broadcast_channel()
        now_ts = int(datetime.now(tz=ET).timestamp())
        new_count = 0
        dates_with_new: set[str] = set()

        async for message in channel.history(limit=500, oldest_first=False):
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

        # Run the full scheduling logic for each affected date
        teamup = self.get_teamup()
        for date_str in sorted(dates_with_new):
            try:
                await schedule_for_date(date_str, self.db, teamup, broadcast_ch, log_ch)
            except Exception:
                if log_ch:
                    await log_ch.send(
                        f"⚠️ Scheduling error for **{date_str}** during history scan."
                    )

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

        try:
            await schedule_for_date(
                match_date, self.db, self.get_teamup(),
                self._get_broadcast_channel(), self._get_log_channel(),
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
        emoji = str(payload.emoji)
        broadcast_ch_id = self.db.get_config("broadcast_channel_id")
        if not broadcast_ch_id or str(payload.channel_id) != broadcast_ch_id:
            return

        # --- Proposal approval / rejection ---
        if emoji in ("✅", "❌"):
            change = self.db.get_pending_change_by_message(str(payload.message_id))
            if not change:
                return
            broadcast_ch = self._get_broadcast_channel()
            if emoji == "❌":
                self.db.resolve_pending_change(change["id"], approved=False)
                if broadcast_ch:
                    await broadcast_ch.send("❌ Schedule proposal rejected.")
            else:
                teamup = self.get_teamup()
                date_str = await apply_pending_change(change, self.db, teamup, broadcast_ch)
                if broadcast_ch and date_str:
                    await broadcast_ch.send(
                        f"✅ Schedule proposal approved for {_fmt_date(date_str)}."
                    )
            return

        # --- Talent sign-up ---
        if emoji not in EMOJI_TO_ROLE:
            return
        match = self.db.get_match_by_broadcast_message(str(payload.message_id))
        if not match or match.get("broadcast_accepted"):
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        try:
            member = await guild.fetch_member(payload.user_id)
        except discord.HTTPException:
            return
        role = EMOJI_TO_ROLE[emoji]
        is_new = self.db.upsert_signup(
            match_id=match["id"],
            message_id=str(payload.message_id),
            role=role,
            user_id=str(payload.user_id),
            username=str(member),
            display_name=member.display_name,
        )
        if is_new:
            await self._update_signup_message(match, str(payload.message_id))
            await self._check_and_finalize_match(match, str(payload.message_id))

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return
        emoji = str(payload.emoji)
        if emoji not in EMOJI_TO_ROLE:
            return
        broadcast_ch_id = self.db.get_config("broadcast_channel_id")
        if not broadcast_ch_id or str(payload.channel_id) != broadcast_ch_id:
            return
        match = self.db.get_match_by_broadcast_message(str(payload.message_id))
        if not match or match.get("broadcast_accepted"):
            return
        role = EMOJI_TO_ROLE[emoji]
        removed = self.db.remove_signup(match["id"], role, str(payload.user_id))
        if removed:
            await self._update_signup_message(match, str(payload.message_id))

    async def _update_signup_message(self, match: dict, message_id: str) -> None:
        """Edit the sign-up message in the broadcast channel to reflect current signups."""
        broadcast_ch = self._get_broadcast_channel()
        if not broadcast_ch:
            return
        try:
            msg = await broadcast_ch.fetch_message(int(message_id))
            signups = self.db.get_signups_for_match(match["id"])
            await msg.edit(content=build_signup_message(match, signups))
        except Exception:
            pass

    async def _check_and_finalize_match(self, match: dict, message_id: str) -> None:
        """If all required talent roles are filled, move match to the Accepted Calendar."""
        signups = self.db.get_signups_for_match(match["id"])
        if not is_fully_staffed(signups):
            return
        teamup = self.get_teamup()
        if not teamup or not match.get("teamup_event_id"):
            return
        description = build_talent_description(signups)
        title = (
            f"[{match['division']}] {match['team_home']} vs {match['team_away']}"
            f" {{{match['id']}}}"
        )
        end_ts = match["match_time"] + int(MATCH_DURATION_H * 3600)
        try:
            teamup.update_event(
                match["teamup_event_id"], title, match["match_time"], end_ts,
                subcalendar="accepted", description=description,
            )
        except Exception as e:
            log.error("TeamUp update failed when finalizing match %s: %s", match["id"], e)
            return
        self.db.mark_broadcast_accepted(match["id"])
        broadcast_ch = self._get_broadcast_channel()
        if broadcast_ch:
            try:
                msg = await broadcast_ch.fetch_message(int(message_id))
                confirmed_text = (
                    build_signup_message(match, signups)
                    + "\n\n✅ **All required talent confirmed — moved to Accepted Calendar!**"
                )
                await msg.edit(content=confirmed_text)
            except Exception:
                pass
        log_ch = self._get_log_channel()
        if log_ch:
            await log_ch.send(
                f"✅ **[{match['division']}] {match['team_home']} vs {match['team_away']}** "
                f"fully staffed — moved to Accepted Calendar."
            )

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
