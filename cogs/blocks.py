import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone, timedelta
from database import Database
from typing import Callable, Optional

BLOCK_PREFIX = "🚫 NO STREAM"


class BlocksCog(commands.Cog):
    def __init__(self, bot: commands.Bot, db: Database, get_teamup: Callable):
        self.bot = bot
        self.db = db
        self.get_teamup = get_teamup

    def _admin_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.guild_permissions.administrator

    @app_commands.command(name="block-day",
                          description="Block a day from broadcast scheduling")
    async def block_day(self, interaction: discord.Interaction,
                        date: str, reason: Optional[str] = None):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid date format. Use YYYY-MM-DD.", ephemeral=True
            )
            return

        title = f"{BLOCK_PREFIX} — {reason}" if reason else BLOCK_PREFIX
        event_id = None
        teamup = self.get_teamup()
        if teamup:
            day_start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            day_end = day_start + timedelta(days=1)
            event_id = teamup.create_event(
                title,
                int(day_start.timestamp()),
                int(day_end.timestamp()),
                all_day=True,
            )

        self.db.insert_blocked_day(date, reason, event_id)
        suffix = f": {reason}" if reason else ""
        await interaction.response.send_message(
            f"✅ {date} blocked{suffix}.", ephemeral=True
        )

    @app_commands.command(name="unblock-day",
                          description="Remove a broadcast block for a day")
    async def unblock_day(self, interaction: discord.Interaction, date: str):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        blocked = self.db.get_blocked_day(date)
        if not blocked:
            await interaction.response.send_message(
                f"⚠️ {date} is not blocked.", ephemeral=True
            )
            return
        teamup = self.get_teamup()
        if teamup and blocked.get("teamup_event_id"):
            teamup.delete_event(blocked["teamup_event_id"])
        self.db.delete_blocked_day(date)
        await interaction.response.send_message(
            f"✅ Block removed for {date}.", ephemeral=True
        )

    @app_commands.command(name="list-blocks",
                          description="List all upcoming blocked days")
    async def list_blocks(self, interaction: discord.Interaction):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        blocks = self.db.get_all_blocked_days()
        if not blocks:
            await interaction.response.send_message(
                "No blocked days configured.", ephemeral=True
            )
            return
        lines = ["**Blocked Days:**"]
        for b in blocks:
            reason_str = f" — {b['reason']}" if b.get("reason") else ""
            lines.append(f"  • {b['date']}{reason_str}")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)
