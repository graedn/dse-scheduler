import discord
from discord.ext import commands
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Callable

from database import Database
from parser import has_required_structure, parse_post, ParseError
from scheduler import (
    best_combination, score_combination, combo_match_ids,
    is_weekend, accept_combination, propose_change,
)

ET = ZoneInfo("America/New_York")


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
            return  # Silent ignore

        try:
            parsed = parse_post(message.content, self.db)
        except ParseError:
            await self._flag_error(message)
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

        all_matches = self.db.get_matches_for_date(match_date)
        scheduled = self.db.get_scheduled_matches_for_date(match_date)
        best = best_combination(all_matches, self.db)

        if best is None:
            return

        teamup = self.get_teamup()
        broadcast_ch = self._get_broadcast_channel()

        if not scheduled:
            if teamup:
                await accept_combination(best, match_date, self.db, teamup, broadcast_ch)
        else:
            weekend = is_weekend(scheduled[0]["match_time"])
            current_score = score_combination(scheduled, weekend, self.db)
            proposed_score = score_combination(best, is_weekend(best[0]["match_time"]), self.db)
            if combo_match_ids(best) == combo_match_ids(scheduled):
                return
            if proposed_score <= current_score:
                return
            await propose_change(
                match_date, scheduled, best, current_score, proposed_score,
                self.db, broadcast_ch,
            )

    async def _flag_error(self, message: discord.Message):
        flag_text = (
            f"⚠️ Could not parse a match post from **{message.author.display_name}**. "
            f"Please review:\n```{message.content[:500]}```"
        )
        await message.reply(flag_text)
        broadcast_ch = self._get_broadcast_channel()
        if broadcast_ch:
            await broadcast_ch.send(flag_text)

    def _get_broadcast_channel(self):
        ch_id = self.db.get_config("broadcast_channel_id")
        if ch_id:
            return self.bot.get_channel(int(ch_id))
        return None

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return
        if str(payload.emoji) != "❌":
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
        broadcast_ch = self.db.get_config("broadcast_channel_id")
        warning = (
            "⚠️ A configured channel was deleted. "
            "Use `/set-match-channel` or `/set-broadcast-channel` to reconfigure."
        )
        if channel_id == match_ch:
            self.db.delete_config("match_channel_id")
            remaining = self._get_broadcast_channel()
            if remaining:
                await remaining.send(warning)
                return
        if channel_id == broadcast_ch:
            self.db.delete_config("broadcast_channel_id")
            print(f"[WARNING] Broadcast channel {channel_id} deleted. No channel to send warning.")
