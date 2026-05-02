import discord
import logging
from discord import app_commands
from discord.ext import commands
from database import Database
from scheduler import build_matches_announcement, match_end_ts
from typing import Callable, Optional

log = logging.getLogger(__name__)


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
        """Passes for administrators, DB managers, or users with the manager Discord role."""
        if not interaction.guild:
            return False
        if interaction.user.guild_permissions.administrator:
            return True
        if self.db.is_manager(str(interaction.user.id)):
            return True
        role_id = self.db.get_config("manager_role_id")
        if role_id:
            return any(str(r.id) == role_id for r in interaction.user.roles)
        return False

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

    @app_commands.command(name="set-proposal-channel",
                          description="Set the channel where weekly broadcast schedule proposals are posted")
    async def set_proposal_channel(self, interaction: discord.Interaction,
                                    channel: discord.TextChannel):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.set_config("proposal_channel_id", str(channel.id))
        await interaction.response.send_message(
            f"✅ Proposal channel set to {channel.mention}", ephemeral=True
        )

    @app_commands.command(name="unset-proposal-channel",
                          description="Unlink the proposal channel")
    async def unset_proposal_channel(self, interaction: discord.Interaction):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.delete_config("proposal_channel_id")
        await interaction.response.send_message(
            "✅ Proposal channel unlinked.", ephemeral=True
        )

    @app_commands.command(name="set-schedule-updates-channel",
                          description="Set the channel for schedule change notifications (pings affected talent)")
    async def set_schedule_updates_channel(self, interaction: discord.Interaction,
                                            channel: discord.TextChannel):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.set_config("schedule_updates_channel_id", str(channel.id))
        await interaction.response.send_message(
            f"✅ Schedule updates channel set to {channel.mention}", ephemeral=True
        )

    @app_commands.command(name="unset-schedule-updates-channel",
                          description="Unlink the schedule updates channel")
    async def unset_schedule_updates_channel(self, interaction: discord.Interaction):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.delete_config("schedule_updates_channel_id")
        await interaction.response.send_message(
            "✅ Schedule updates channel unlinked.", ephemeral=True
        )

    @app_commands.command(name="add-talent-role",
                          description="Set the Discord role pinged in talent sign-up messages")
    async def add_talent_role(self, interaction: discord.Interaction,
                               role: discord.Role):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.set_config("talent_role_id", str(role.id))
        await interaction.response.send_message(
            f"✅ Talent role set to **{role.name}**. "
            f"This role will be @mentioned in new sign-up messages.",
            ephemeral=True,
        )

    @app_commands.command(name="remove-talent-role",
                          description="Clear the Discord role pinged in talent sign-up messages")
    async def remove_talent_role(self, interaction: discord.Interaction):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        if not self.db.get_config("talent_role_id"):
            await interaction.response.send_message(
                "No talent role is configured.", ephemeral=True
            )
            return
        self.db.delete_config("talent_role_id")
        await interaction.response.send_message(
            "✅ Talent role cleared. Sign-up messages will no longer @mention a role.",
            ephemeral=True,
        )

    @app_commands.command(name="clear-message-history",
                          description="Delete all messages from a channel (use with caution)")
    async def clear_message_history(self, interaction: discord.Interaction,
                                     channel: discord.TextChannel):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        deleted = 0
        try:
            # bulk_delete only works on messages <14 days old; purge handles the fallback
            deleted = await channel.purge(limit=None)
            deleted = len(deleted)
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Missing 'Manage Messages' permission in that channel.", ephemeral=True
            )
            return
        except Exception as e:
            await interaction.followup.send(
                f"❌ Failed to clear channel: {e}", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"✅ Deleted {deleted} message(s) from {channel.mention}.", ephemeral=True
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
        proposal_ch = self.db.get_config("proposal_channel_id")
        updates_ch = self.db.get_config("schedule_updates_channel_id")
        thread_ch = self.db.get_config("thread_channel_id")
        talent_role = self.db.get_config("talent_role_id")
        manager_role = self.db.get_config("manager_role_id")
        league_admin_role = self.db.get_config("league_admin_role_id")
        calendar_id = self.db.get_config("teamup_calendar_id")
        api_key = self.db.get_config("teamup_api_key")

        def ch_str(ch_id: Optional[str]) -> str:
            return f"<#{ch_id}>" if ch_id else "❌ Not set"

        def role_str(role_id: Optional[str]) -> str:
            return f"<@&{role_id}>" if role_id else "❌ Not set"

        lines = [
            "**Bot Status**",
            f"Match channel: {ch_str(match_ch)}",
            f"Sign-up channel: {ch_str(signup_ch)}",
            f"Broadcast channel: {ch_str(broadcast_ch)}",
            f"Log channel: {ch_str(log_ch)}",
            f"Proposal channel: {ch_str(proposal_ch)}",
            f"Schedule updates channel: {ch_str(updates_ch)}",
            f"Thread channel: {ch_str(thread_ch)}",
            f"Talent role: {role_str(talent_role)}",
            f"Manager role: {role_str(manager_role)}",
            f"League Admin role: {role_str(league_admin_role)}",
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
            lines.append(f"\n⚠️ Missing required config: {', '.join(missing)}")

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
        if not self._manager_check(interaction):
            await interaction.response.send_message(
                "Manager or Administrator permission required.", ephemeral=True
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
        if not self._manager_check(interaction):
            await interaction.response.send_message(
                "Manager or Administrator permission required.", ephemeral=True
            )
            return
        if not self.db.get_config("match_channel_id"):
            await interaction.response.send_message(
                "❌ Match channel not configured. Use `/set-match-channel` first.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        events_cog = self.bot.cogs.get("EventsCog")
        if not events_cog:
            await interaction.followup.send("❌ EventsCog not loaded.", ephemeral=True)
            return
        try:
            new_count, date_count = await events_cog._scan_match_history()
        except Exception as e:
            log.error("sync-history failed: %s", e)
            await interaction.followup.send(
                f"❌ Scan failed: `{e}`", ephemeral=True
            )
            return
        if new_count:
            await interaction.followup.send(
                f"✅ Scan complete — **{new_count}** new match(es) logged across "
                f"**{date_count}** date(s). Proposal messages updated where available.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "✅ Scan complete — no new future matches found (all already logged).",
                ephemeral=True,
            )

    @app_commands.command(name="post-weekly-proposals",
                          description="Manually post the weekly proposal messages for the coming week")
    @app_commands.describe(force="Set to True to update proposals that already exist")
    async def post_weekly_proposals(self, interaction: discord.Interaction,
                                    force: bool = False):
        if not self._manager_check(interaction):
            await interaction.response.send_message(
                "Manager or Administrator permission required.", ephemeral=True
            )
            return

        if not self.db.get_config("proposal_channel_id"):
            await interaction.response.send_message(
                "❌ Proposal channel not configured. Use `/set-proposal-channel` first.",
                ephemeral=True,
            )
            return

        import datetime as _dt
        from datetime import timedelta
        from zoneinfo import ZoneInfo
        ET = ZoneInfo("America/New_York")
        today = _dt.datetime.now(tz=ET).date()

        # week_start = this Monday (so the safeguard checks the current week)
        this_monday = today - timedelta(days=today.weekday())
        week_start = this_monday.isoformat()

        existing = self.db.get_proposal_messages_for_week(week_start)
        already_posted = [p for p in existing if p.get("discord_message_id")]

        if already_posted and not force:
            await interaction.response.send_message(
                f"⚠️ Proposal messages for this week (starting **{week_start}**) are already "
                f"posted ({len(already_posted)} message(s)). Run with `force: True` to "
                f"update them (e.g. after a bot restart).",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        from cogs.weekly_proposals import create_weekly_proposals
        try:
            await create_weekly_proposals(self.bot, self.db, start_date=today)
        except Exception as e:
            log.error("post-weekly-proposals failed: %s", e)
            await interaction.followup.send(f"❌ Failed: `{e}`", ephemeral=True)
            return

        days_until_sunday = 6 - today.weekday()
        day_count = days_until_sunday + 1
        action = "updated" if already_posted else "created"
        await interaction.followup.send(
            f"✅ Weekly proposal messages {action} — **{day_count}** day(s) "
            f"from today through Sunday.",
            ephemeral=True,
        )

    @app_commands.command(name="set-timezone",
                          description="Set your preferred timezone for time displays (e.g. in the New Match picker)")
    async def set_timezone(self, interaction: discord.Interaction):
        if not self._manager_check(interaction):
            await interaction.response.send_message(
                "Manager or Administrator permission required.", ephemeral=True
            )
            return
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
    # Thread channel and League Admin role config (admin-only)
    # ------------------------------------------------------------------

    @app_commands.command(name="set-thread-channel",
                          description="Set the channel where match threads are created")
    async def set_thread_channel(self, interaction: discord.Interaction,
                                  channel: discord.TextChannel):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.set_config("thread_channel_id", str(channel.id))
        await interaction.response.send_message(
            f"✅ Thread channel set to {channel.mention}", ephemeral=True
        )

    @app_commands.command(name="unset-thread-channel",
                          description="Remove the thread channel configuration")
    async def unset_thread_channel(self, interaction: discord.Interaction):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.delete_config("thread_channel_id")
        await interaction.response.send_message(
            "✅ Thread channel unlinked.", ephemeral=True
        )

    @app_commands.command(name="add-league-admin-role",
                          description="Set the League Admin role used in broadcast threads and ready checks")
    async def add_league_admin_role(self, interaction: discord.Interaction,
                                    role: discord.Role):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.set_config("league_admin_role_id", str(role.id))
        await interaction.response.send_message(
            f"✅ League Admin role set to **{role.name}**.", ephemeral=True
        )

    @app_commands.command(name="remove-league-admin-role",
                          description="Remove the League Admin role configuration")
    async def remove_league_admin_role(self, interaction: discord.Interaction):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.delete_config("league_admin_role_id")
        await interaction.response.send_message(
            "✅ League Admin role cleared.", ephemeral=True
        )

    # ------------------------------------------------------------------
    # Manager management (admin-only)
    # ------------------------------------------------------------------

    @app_commands.command(name="add-manager-role",
                          description="Set the Discord role automatically assigned to broadcast managers")
    async def add_manager_role(self, interaction: discord.Interaction,
                               role: discord.Role):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        self.db.set_config("manager_role_id", str(role.id))
        await interaction.response.send_message(
            f"✅ Manager role set to **{role.name}**. "
            f"This role will be added/removed automatically when using `/add-manager` and `/remove-manager`.",
            ephemeral=True,
        )

    @app_commands.command(name="remove-manager-role",
                          description="Clear the Discord role assigned to broadcast managers")
    async def remove_manager_role(self, interaction: discord.Interaction):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return
        existing = self.db.get_config("manager_role_id")
        if not existing:
            await interaction.response.send_message(
                "No manager role is configured.", ephemeral=True
            )
            return
        self.db.delete_config("manager_role_id")
        await interaction.response.send_message(
            "✅ Manager role cleared. `/add-manager` and `/remove-manager` will no longer touch Discord roles.",
            ephemeral=True,
        )

    @app_commands.command(name="add-manager",
                          description="Grant broadcast manager permissions to a user")
    async def add_manager(self, interaction: discord.Interaction,
                          user: discord.Member):
        if not self._admin_check(interaction):
            await interaction.response.send_message(
                "Administrator permission required.", ephemeral=True
            )
            return

        role_id = self.db.get_config("manager_role_id")
        if not role_id:
            await interaction.response.send_message(
                "⚠️ No manager role configured. Run `/add-manager-role` first to set a Discord role "
                "before adding managers.",
                ephemeral=True,
            )
            return

        role = interaction.guild.get_role(int(role_id))
        if not role:
            await interaction.response.send_message(
                "⚠️ The configured manager role no longer exists in this server. "
                "Run `/add-manager-role` to set a new one.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        self.db.add_manager(
            str(user.id), str(user), user.display_name,
            added_by=str(interaction.user.id),
        )
        try:
            await user.add_roles(role, reason=f"Added as broadcast manager by {interaction.user}")
        except discord.Forbidden:
            await interaction.followup.send(
                f"✅ {user.mention} added as broadcast manager, but the bot lacks permission "
                f"to assign the **{role.name}** role — grant it 'Manage Roles' and ensure the "
                f"role is below the bot's highest role.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"✅ {user.mention} added as broadcast manager and given the **{role.name}** role.",
            ephemeral=True,
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

        role_id = self.db.get_config("manager_role_id")
        if not role_id:
            await interaction.response.send_message(
                "⚠️ No manager role configured. Run `/add-manager-role` first to set a Discord role "
                "before removing managers.",
                ephemeral=True,
            )
            return

        role = interaction.guild.get_role(int(role_id))
        if not role:
            await interaction.response.send_message(
                "⚠️ The configured manager role no longer exists in this server. "
                "Run `/add-manager-role` to set a new one.",
                ephemeral=True,
            )
            return

        removed = self.db.remove_manager(str(user.id))
        if not removed:
            await interaction.response.send_message(
                f"⚠️ {user.mention} is not a manager.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            await user.remove_roles(role, reason=f"Removed as broadcast manager by {interaction.user}")
        except discord.Forbidden:
            await interaction.followup.send(
                f"✅ {user.mention} removed as broadcast manager, but the bot lacks permission "
                f"to remove the **{role.name}** role — grant it 'Manage Roles' and ensure the "
                f"role is below the bot's highest role.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"✅ {user.mention} removed as broadcast manager and the **{role.name}** role was revoked.",
            ephemeral=True,
        )

    @app_commands.command(name="list-managers",
                          description="List all broadcast managers")
    async def list_managers(self, interaction: discord.Interaction):
        if not self._manager_check(interaction):
            await interaction.response.send_message(
                "Manager or Administrator permission required.", ephemeral=True
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
        talent = self.db.get_all_talent()
        if not talent:
            await interaction.response.send_message(
                "No talent records yet.", ephemeral=True
            )
            return
        lines = ["**Talent (Broadcasts - Responses - Unavailable):**"]
        for i, t in enumerate(talent, 1):
            bc = t["broadcast_count"]
            rc = t.get("response_count", 0)
            uc = t.get("unavailable_count", 0)
            lines.append(
                f"  {i}. {t['display_name']} ({t['username']}) "
                f"— {bc} - {rc} - {uc}"
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
