import discord
from discord import app_commands
from discord.ext import commands
from database import Database
from scheduler import build_matches_announcement, match_end_ts
from typing import Callable, Optional


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot, db: Database, get_teamup: Callable = None):
        self.bot = bot
        self.db = db
        self.get_teamup = get_teamup

    def _admin_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        return interaction.user.guild_permissions.administrator

    def _manager_check(self, interaction: discord.Interaction) -> bool:
        """Passes for Discord administrators and users added via /add-manager."""
        if not interaction.guild:
            return False
        if interaction.user.guild_permissions.administrator:
            return True
        return self.db.is_manager(str(interaction.user.id))

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

    @app_commands.command(name="set-signup-channel",
                          description="Set the channel where talent sign-up messages are posted")
    async def set_signup_channel(self, interaction: discord.Interaction,
                                  channel: discord.TextChannel):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.set_config("signup_channel_id", str(channel.id))
        await interaction.response.send_message(
            f"✅ Sign-up channel set to {channel.mention}", ephemeral=True
        )

    @app_commands.command(name="unset-signup-channel",
                          description="Unlink the sign-up channel")
    async def unset_signup_channel(self, interaction: discord.Interaction):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.delete_config("signup_channel_id")
        await interaction.response.send_message(
            "✅ Sign-up channel unlinked.", ephemeral=True
        )

    @app_commands.command(name="set-broadcast-channel",
                          description="Set the channel for talent confirmation messages")
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
        self.db.set_config("teamup_api_key", api_key.strip())
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
        signup_ch = self.db.get_config("signup_channel_id")
        broadcast_ch = self.db.get_config("broadcast_channel_id")
        log_ch = self.db.get_config("log_channel_id")
        calendar_id = self.db.get_config("teamup_calendar_id")
        api_key = self.db.get_config("teamup_api_key")

        def ch_str(ch_id: Optional[str]) -> str:
            return f"<#{ch_id}>" if ch_id else "❌ Not set"

        lines = [
            "**Bot Status**",
            f"Match channel: {ch_str(match_ch)}",
            f"Sign-up channel: {ch_str(signup_ch)}",
            f"Broadcast channel: {ch_str(broadcast_ch)}",
            f"Log channel: {ch_str(log_ch)}",
            f"TeamUp calendar: {'✅ Set' if calendar_id else '❌ Not set'}",
            f"TeamUp API key: {'✅ Set' if api_key else '❌ Not set'}",
        ]
        missing = []
        if not match_ch: missing.append("`/set-match-channel`")
        if not signup_ch: missing.append("`/set-signup-channel`")
        if not broadcast_ch: missing.append("`/set-broadcast-channel`")
        if not calendar_id: missing.append("`/set-teamup-calendar`")
        if not api_key: missing.append("`/set-teamup-key`")
        if missing:
            lines.append(f"\n⚠️ Missing config: {', '.join(missing)}")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="test-teamup",
                          description="Test the TeamUp API connection")
    async def test_teamup(self, interaction: discord.Interaction):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        api_key = self.db.get_config("teamup_api_key")
        calendar_id = self.db.get_config("teamup_calendar_id")
        if not api_key or not calendar_id:
            await interaction.followup.send(
                "❌ TeamUp not fully configured. Run `/status` to check.", ephemeral=True
            )
            return
        key_preview = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) > 8 else "(too short)"
        import requests
        try:
            resp = requests.get(
                f"https://api.teamup.com/{calendar_id}/events",
                headers={"Teamup-Token": api_key},
                params={"startDate": "2026-01-01", "endDate": "2026-01-02"},
                timeout=10,
            )
            if resp.ok:
                await interaction.followup.send(
                    f"✅ TeamUp connection successful.\n"
                    f"Calendar: `{calendar_id}`\n"
                    f"Key ({len(api_key)} chars): `{key_preview}`",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"❌ TeamUp error {resp.status_code}\n"
                    f"Calendar: `{calendar_id}`\n"
                    f"Key ({len(api_key)} chars): `{key_preview}`\n"
                    f"Response: `{resp.text[:300]}`",
                    ephemeral=True,
                )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Request failed: {e}", ephemeral=True
            )

    @app_commands.command(name="accept-broadcast",
                          description="Accept a match for broadcast — moves it to Accepted Broadcasts calendar")
    async def accept_broadcast(self, interaction: discord.Interaction, match_id: int):
        if not self._manager_check(interaction):
            await interaction.response.send_message(
                "Manager or Administrator permission required.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        match = self.db.get_match(match_id)
        if not match:
            await interaction.followup.send(
                f"❌ No match found with ID #{match_id}", ephemeral=True
            )
            return
        if match["broadcast_accepted"]:
            await interaction.followup.send(
                f"⚠️ Match #{match_id} is already accepted for broadcast.", ephemeral=True
            )
            return
        teamup = self.get_teamup() if self.get_teamup else None
        if teamup and match.get("teamup_event_id"):
            end_ts = match_end_ts(match["match_time"])
            title = (
                f"[{match['division']}] {match['team_home']} vs {match['team_away']}"
                f" {{{match_id}}}"
            )
            teamup.update_event(
                match["teamup_event_id"], title,
                match["match_time"], end_ts,
                subcalendar="accepted",
            )
        self.db.mark_broadcast_accepted(match_id)
        await interaction.followup.send(
            f"✅ Match #{match_id} ({match['team_home']} vs {match['team_away']}) "
            f"moved to Accepted Broadcasts.",
            ephemeral=True,
        )

    @app_commands.command(name="announce-matches",
                          description="Post the logged matches summary to the broadcast channel now")
    async def announce_matches_cmd(self, interaction: discord.Interaction):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        log_ch_id = self.db.get_config("log_channel_id")
        if not log_ch_id:
            await interaction.followup.send(
                "❌ Log channel not configured.", ephemeral=True
            )
            return
        log_ch = self.bot.get_channel(int(log_ch_id))
        msg = build_matches_announcement(self.db)
        if msg:
            await log_ch.send(msg)
            await interaction.followup.send("✅ Announcement posted to log channel.", ephemeral=True)
        else:
            await interaction.followup.send(
                "No upcoming matches logged to announce.", ephemeral=True
            )

    @app_commands.command(name="broadcast-done",
                          description="Mark a match as broadcast-complete")
    async def broadcast_done(self, interaction: discord.Interaction, match_id: int):
        if not self._manager_check(interaction):
            await interaction.response.send_message(
                "Manager or Administrator permission required.", ephemeral=True
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

    @app_commands.command(name="sync-history",
                          description="Scan the match channel history and log any future matches")
    async def sync_history(self, interaction: discord.Interaction):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        events_cog = self.bot.cogs.get("EventsCog")
        if not events_cog:
            await interaction.followup.send("❌ EventsCog not loaded.", ephemeral=True)
            return
        await events_cog._scan_match_history()
        await interaction.followup.send(
            "✅ History scan complete. Check the log channel for results.", ephemeral=True
        )

    @app_commands.command(name="set-timezone",
                          description="Set your preferred timezone for time displays (e.g. in the New Match picker)")
    async def set_timezone(self, interaction: discord.Interaction):
        options = [
            discord.SelectOption(label="ET — America/New_York",        value="America/New_York"),
            discord.SelectOption(label="CT — America/Chicago",         value="America/Chicago"),
            discord.SelectOption(label="MT — America/Denver",          value="America/Denver"),
            discord.SelectOption(label="PT — America/Los_Angeles",     value="America/Los_Angeles"),
            discord.SelectOption(label="AKT — America/Anchorage",      value="America/Anchorage"),
            discord.SelectOption(label="HT — Pacific/Honolulu",        value="Pacific/Honolulu"),
            discord.SelectOption(label="AT — America/Halifax",         value="America/Halifax"),
            discord.SelectOption(label="NT — America/St_Johns",        value="America/St_Johns"),
            discord.SelectOption(label="BRT — America/Sao_Paulo",      value="America/Sao_Paulo"),
            discord.SelectOption(label="GMT — Europe/London",          value="Europe/London"),
            discord.SelectOption(label="CET — Europe/Paris",           value="Europe/Paris"),
            discord.SelectOption(label="EET — Europe/Helsinki",        value="Europe/Helsinki"),
            discord.SelectOption(label="MSK — Europe/Moscow",          value="Europe/Moscow"),
            discord.SelectOption(label="GST — Asia/Dubai",             value="Asia/Dubai"),
            discord.SelectOption(label="PKT — Asia/Karachi",           value="Asia/Karachi"),
            discord.SelectOption(label="IST — Asia/Kolkata",           value="Asia/Kolkata"),
            discord.SelectOption(label="BST — Asia/Dhaka",             value="Asia/Dhaka"),
            discord.SelectOption(label="ICT — Asia/Bangkok",           value="Asia/Bangkok"),
            discord.SelectOption(label="CST — Asia/Shanghai",          value="Asia/Shanghai"),
            discord.SelectOption(label="JST — Asia/Tokyo",             value="Asia/Tokyo"),
            discord.SelectOption(label="AEST — Australia/Sydney",      value="Australia/Sydney"),
            discord.SelectOption(label="NZST — Pacific/Auckland",      value="Pacific/Auckland"),
            discord.SelectOption(label="UTC",                          value="UTC"),
        ]

        class _TZSelect(discord.ui.Select):
            def __init__(self_inner):
                super().__init__(
                    placeholder="Select your timezone...",
                    options=options,
                    min_values=1,
                    max_values=1,
                )

            async def callback(self_inner, tz_interaction: discord.Interaction):
                chosen = self_inner.values[0]
                tz_interaction.client.db.set_user_timezone(
                    str(tz_interaction.user.id), chosen
                )
                await tz_interaction.response.edit_message(
                    content=f"✅ Timezone set to **{chosen}**.", view=None
                )

        view = discord.ui.View(timeout=120)
        view.add_item(_TZSelect())
        await interaction.response.send_message(
            "Select your preferred timezone:", view=view, ephemeral=True
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
        await interaction.response.defer(ephemeral=True)
        # Delete all TeamUp events before wiping the database
        teamup = self.get_teamup() if self.get_teamup else None
        deleted, failed = 0, 0
        if teamup:
            for match in self.db.get_all_matches_with_teamup_id():
                try:
                    teamup.delete_event(match["teamup_event_id"])
                    deleted += 1
                except Exception:
                    failed += 1
            for day in self.db.get_all_blocked_days():
                if day.get("teamup_event_id"):
                    try:
                        teamup.delete_event(day["teamup_event_id"])
                        deleted += 1
                    except Exception:
                        failed += 1
        self.db.reset_all()
        summary = f"Removed {deleted} TeamUp event(s) from the calendar." if teamup else "TeamUp not configured — calendar events were not cleared."
        if failed:
            summary += f" ({failed} deletion(s) failed — remove manually.)"
        await interaction.followup.send(
            f"✅ Bot has been reset to its original state.\n{summary}", ephemeral=True
        )

    # ------------------------------------------------------------------
    # Manager management (admin-only)
    # ------------------------------------------------------------------

    @app_commands.command(name="add-manager",
                          description="Grant broadcast manager permissions to a user")
    async def add_manager(self, interaction: discord.Interaction,
                          user: discord.Member):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.add_manager(
            str(user.id), str(user), user.display_name,
            added_by=str(interaction.user.id),
        )
        await interaction.response.send_message(
            f"✅ {user.mention} added as broadcast manager.", ephemeral=True
        )

    @app_commands.command(name="remove-manager",
                          description="Revoke broadcast manager permissions from a user")
    async def remove_manager(self, interaction: discord.Interaction,
                             user: discord.Member):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        removed = self.db.remove_manager(str(user.id))
        if removed:
            await interaction.response.send_message(
                f"✅ {user.mention} removed as broadcast manager.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"⚠️ {user.mention} is not a manager.", ephemeral=True
            )

    @app_commands.command(name="list-managers",
                          description="List all broadcast managers")
    async def list_managers(self, interaction: discord.Interaction):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        managers = self.db.get_all_managers()
        if not managers:
            await interaction.response.send_message(
                "No managers configured.", ephemeral=True
            )
            return
        lines = ["**Broadcast Managers:**"]
        for m in managers:
            lines.append(f"  • {m['display_name']} ({m['username']}) — <@{m['user_id']}>")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # ------------------------------------------------------------------
    # Talent leaderboard (managers + admins)
    # ------------------------------------------------------------------

    @app_commands.command(name="talent",
                          description="List talent by broadcast count")
    async def talent_list(self, interaction: discord.Interaction):
        if not self._manager_check(interaction):
            await interaction.response.send_message(
                "Manager or Administrator permission required.", ephemeral=True
            )
            return
        talent = self.db.get_all_talent()
        if not talent:
            await interaction.response.send_message(
                "No talent records yet.", ephemeral=True
            )
            return
        lines = ["**Talent Broadcast Counts:**"]
        for i, t in enumerate(talent, 1):
            lines.append(
                f"  {i}. {t['display_name']} ({t['username']}) "
                f"— **{t['broadcast_count']}** broadcast{'s' if t['broadcast_count'] != 1 else ''}"
            )
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # ------------------------------------------------------------------
    # New season reset (admin-only)
    # ------------------------------------------------------------------

    @app_commands.command(name="new-season",
                          description="Reset season data (keeps channels, TeamUp config, and managers)")
    async def new_season(self, interaction: discord.Interaction,
                         confirm: bool = False):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        if not confirm:
            await interaction.response.send_message(
                "⚠️ This will reset all match history, team tallies, talent records, "
                "and blocked days.\n"
                "Channel settings, TeamUp config, and the manager list will be preserved.\n"
                "Run `/new-season confirm:True` to proceed.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        teamup = self.get_teamup() if self.get_teamup else None
        deleted, failed = 0, 0
        if teamup:
            for match in self.db.get_all_matches_with_teamup_id():
                try:
                    teamup.delete_event(match["teamup_event_id"])
                    deleted += 1
                except Exception:
                    failed += 1
            for day in self.db.get_all_blocked_days():
                if day.get("teamup_event_id"):
                    try:
                        teamup.delete_event(day["teamup_event_id"])
                        deleted += 1
                    except Exception:
                        failed += 1
        self.db.reset_season()
        summary = (
            f"Removed {deleted} TeamUp event(s)."
            if teamup else "TeamUp not configured — calendar events were not cleared."
        )
        if failed:
            summary += f" ({failed} deletion(s) failed — remove manually.)"
        await interaction.followup.send(
            f"✅ New season started. Season data cleared.\n{summary}\n"
            f"Channel settings, TeamUp config, and managers were preserved.",
            ephemeral=True,
        )
