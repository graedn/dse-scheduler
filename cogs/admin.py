import discord
from discord import app_commands
from discord.ext import commands
from database import Database
from typing import Optional


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot, db: Database):
        self.bot = bot
        self.db = db

    def _admin_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        return interaction.user.guild_permissions.administrator

    @app_commands.command(name="set-match-channel",
                          description="Set the channel to watch for match posts")
    async def set_match_channel(self, interaction: discord.Interaction,
                                 channel: discord.TextChannel):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.set_config("match_channel_id", str(channel.id))
        await interaction.response.send_message(
            f"✅ Match channel set to {channel.mention}", ephemeral=True
        )

    @app_commands.command(name="unset-match-channel",
                          description="Unlink the match channel")
    async def unset_match_channel(self, interaction: discord.Interaction):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.delete_config("match_channel_id")
        await interaction.response.send_message(
            "✅ Match channel unlinked.", ephemeral=True
        )

    @app_commands.command(name="set-broadcast-channel",
                          description="Set the admin channel for drafts and flags")
    async def set_broadcast_channel(self, interaction: discord.Interaction,
                                     channel: discord.TextChannel):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.set_config("broadcast_channel_id", str(channel.id))
        await interaction.response.send_message(
            f"✅ Broadcast channel set to {channel.mention}", ephemeral=True
        )

    @app_commands.command(name="unset-broadcast-channel",
                          description="Unlink the broadcast channel")
    async def unset_broadcast_channel(self, interaction: discord.Interaction):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.delete_config("broadcast_channel_id")
        await interaction.response.send_message(
            "✅ Broadcast channel unlinked.", ephemeral=True
        )

    @app_commands.command(name="set-teamup-calendar",
                          description="Set the TeamUp calendar ID")
    async def set_teamup_calendar(self, interaction: discord.Interaction,
                                   calendar_id: str):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.set_config("teamup_calendar_id", calendar_id)
        await interaction.response.send_message(
            "✅ TeamUp calendar ID saved.", ephemeral=True
        )

    @app_commands.command(name="set-teamup-key",
                          description="Set the TeamUp API key")
    # NOTE: Discord logs slash command invocations (including arguments) to the server
    # audit log. Prefer restricting audit log access before using this command, or
    # rotate the key after any suspected exposure.
    async def set_teamup_key(self, interaction: discord.Interaction, api_key: str):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.set_config("teamup_api_key", api_key)
        await interaction.response.send_message(
            "✅ TeamUp API key saved.", ephemeral=True
        )

    @app_commands.command(name="set-log-channel",
                          description="Set the channel for bot logs, errors, and TeamUp confirmations")
    async def set_log_channel(self, interaction: discord.Interaction,
                               channel: discord.TextChannel):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.set_config("log_channel_id", str(channel.id))
        await interaction.response.send_message(
            f"✅ Log channel set to {channel.mention}", ephemeral=True
        )

    @app_commands.command(name="unset-log-channel",
                          description="Unlink the log channel")
    async def unset_log_channel(self, interaction: discord.Interaction):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.delete_config("log_channel_id")
        await interaction.response.send_message(
            "✅ Log channel unlinked.", ephemeral=True
        )

    @app_commands.command(name="status",
                          description="Show current bot configuration")
    async def status(self, interaction: discord.Interaction):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        match_ch = self.db.get_config("match_channel_id")
        broadcast_ch = self.db.get_config("broadcast_channel_id")
        log_ch = self.db.get_config("log_channel_id")
        calendar_id = self.db.get_config("teamup_calendar_id")
        api_key = self.db.get_config("teamup_api_key")

        def ch_str(ch_id: Optional[str]) -> str:
            return f"<#{ch_id}>" if ch_id else "❌ Not set"

        lines = [
            "**Bot Status**",
            f"Match channel: {ch_str(match_ch)}",
            f"Broadcast channel: {ch_str(broadcast_ch)}",
            f"Log channel: {ch_str(log_ch)}",
            f"TeamUp calendar: {'✅ Set' if calendar_id else '❌ Not set'}",
            f"TeamUp API key: {'✅ Set' if api_key else '❌ Not set'}",
        ]
        missing = []
        if not match_ch: missing.append("`/set-match-channel`")
        if not broadcast_ch: missing.append("`/set-broadcast-channel`")
        if not calendar_id: missing.append("`/set-teamup-calendar`")
        if not api_key: missing.append("`/set-teamup-key`")
        if missing:
            lines.append(f"\n⚠️ Missing config: {', '.join(missing)}")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="broadcast-done",
                          description="Mark a match as broadcast-complete")
    async def broadcast_done(self, interaction: discord.Interaction, match_id: int):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        match = self.db.get_match(match_id)
        if not match:
            await interaction.response.send_message(
                f"❌ No match found with ID #{match_id}", ephemeral=True
            )
            return
        if match["broadcast_done"]:
            await interaction.response.send_message(
                f"⚠️ Match #{match_id} is already marked as done.", ephemeral=True
            )
            return
        self.db.mark_broadcast_done(match_id)
        self.db.increment_broadcast_count(match["team_home"])
        self.db.increment_broadcast_count(match["team_away"])
        await interaction.response.send_message(
            f"✅ Match #{match_id} ({match['team_home']} vs {match['team_away']}) "
            f"marked as broadcast complete.",
            ephemeral=True,
        )

    @app_commands.command(name="reset",
                          description="Reset the bot to its original state")
    async def reset(self, interaction: discord.Interaction, confirm: bool = False):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        if not confirm:
            await interaction.response.send_message(
                "⚠️ This will erase all bot data including match history, team tallies, "
                "and configuration.\nRun `/reset confirm:True` to proceed.",
                ephemeral=True,
            )
            return
        self.db.reset_all()
        await interaction.response.send_message(
            "✅ Bot has been reset to its original state.", ephemeral=True
        )
