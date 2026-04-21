import discord
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from scheduler import _SEPARATOR, match_end_ts, build_signup_message, build_approved_signup_message

log = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _disable_view(view: discord.ui.View) -> None:
    for child in view.children:
        child.disabled = True


def _is_manager(interaction: discord.Interaction, db) -> bool:
    from cogs.signup import _manager_check
    return _manager_check(interaction, db)


async def send_thread_reschedule_notice(bot, db, match_id: int, message_text: str) -> None:
    """Post a reschedule notice to the match thread (if one exists)."""
    thread_row = db.get_thread_message(match_id)
    if thread_row is None:
        return

    channel = bot.get_channel(int(thread_row["thread_id"]))
    if channel is None:
        return

    mentions: list[str] = []

    # League admin role
    admin_role_id = db.get_config("league_admin_role_id")
    if admin_role_id:
        mentions.append(f"<@&{admin_role_id}>")

    # Team roles
    team1_role_id = thread_row.get("team1_role_id")
    if team1_role_id:
        mentions.append(f"<@&{team1_role_id}>")

    team2_role_id = thread_row.get("team2_role_id")
    if team2_role_id:
        mentions.append(f"<@&{team2_role_id}>")

    # Producer and observer user IDs from allocation
    alloc = db.get_allocation(match_id)
    if alloc and alloc.get("role_assignments"):
        role_assignments = json.loads(alloc["role_assignments"])
        seen: set[str] = set()
        for role_key in ("producer", "observer"):
            assignment = role_assignments.get(role_key)
            if isinstance(assignment, dict):
                uid = assignment.get("user_id")
                if uid and uid not in seen:
                    seen.add(uid)
                    mentions.append(f"<@{uid}>")

    if mentions:
        content = " ".join(mentions) + "\n" + message_text
    else:
        content = message_text

    try:
        await channel.send(content)
    except Exception as e:
        log.warning("send_thread_reschedule_notice: failed to send to thread %s: %s",
                    thread_row["thread_id"], e)


# ---------------------------------------------------------------------------
# Button: Update Broadcast
# ---------------------------------------------------------------------------

class _UpdateBroadcastButton(discord.ui.Button):
    def __init__(self, match_id: int):
        self._match_id = match_id
        super().__init__(
            label="⚙️ Update Broadcast",
            style=discord.ButtonStyle.primary,
            custom_id=f"reschedule_update_{match_id}",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        db = interaction.client.db
        if not _is_manager(interaction, db):
            await interaction.response.send_message(
                "Only managers and administrators can update broadcasts.",
                ephemeral=True,
            )
            return

        match_id = self._match_id
        new_ts = self.view.new_ts

        # Update match time in DB
        db.update_match_time(match_id, new_ts)
        fresh_match = db.get_match(match_id)

        # Update TeamUp event to new time (accepted subcalendar)
        event_id = fresh_match.get("teamup_event_id") if fresh_match else None
        if event_id:
            teamup = interaction.client.get_teamup()
            if teamup:
                try:
                    title = (
                        f"[{fresh_match['division']}] "
                        f"{fresh_match['team_home']} vs {fresh_match['team_away']}"
                    )
                    teamup.update_event(
                        event_id, title, new_ts, match_end_ts(new_ts),
                        subcalendar="accepted",
                    )
                except Exception as e:
                    log.warning("_UpdateBroadcastButton: failed to update TeamUp event: %s", e)

        # Edit sign-up message to updated approved state
        alloc = db.get_allocation(match_id)
        role_assignments: dict = {}
        if alloc and alloc.get("role_assignments"):
            role_assignments = json.loads(alloc["role_assignments"])

        bcast = db.get_broadcast_message(match_id)
        signup_ch_id = db.get_config("signup_channel_id") or db.get_config("broadcast_channel_id")
        signup_ch = interaction.client.get_channel(int(signup_ch_id)) if signup_ch_id else None
        if bcast and signup_ch and fresh_match:
            try:
                msg = await signup_ch.fetch_message(int(bcast["discord_message_id"]))
                new_content = build_approved_signup_message(fresh_match, role_assignments)
                await msg.edit(content=new_content, view=discord.ui.View())
            except Exception as e:
                log.warning("_UpdateBroadcastButton: failed to edit sign-up message: %s", e)

        # Notify schedule updates channel with allocated talent mentions
        all_user_ids: list[str] = []
        seen: set[str] = set()
        for assignment in role_assignments.values():
            if isinstance(assignment, dict):
                uid = assignment.get("user_id")
                if uid and uid not in seen:
                    seen.add(uid)
                    all_user_ids.append(uid)

        updates_ch_id = db.get_config("schedule_updates_channel_id")
        notify_ch = (
            interaction.client.get_channel(int(updates_ch_id)) if updates_ch_id else signup_ch
        )
        if notify_ch and fresh_match:
            mentions = " ".join(f"<@{uid}>" for uid in all_user_ids)
            notify_text = (
                f"{mentions}\n" if mentions else ""
            ) + (
                f"📅 **Schedule Update** — "
                f"[{fresh_match['division']}] {fresh_match['team_home']} vs "
                f"{fresh_match['team_away']} has been rescheduled to <t:{new_ts}:F>."
            )
            try:
                await notify_ch.send(notify_text)
            except Exception as e:
                log.warning("_UpdateBroadcastButton: failed to send update notification: %s", e)

        # Thread notice
        await send_thread_reschedule_notice(
            interaction.client, db, match_id,
            "The updated match time has been approved by the broadcast team.",
        )

        # Disable view and edit interaction message
        self.view.stop()
        _disable_view(self.view)
        await interaction.response.edit_message(
            content=interaction.message.content + "\n\n✅ **Broadcast time updated.**",
            view=self.view,
        )


# ---------------------------------------------------------------------------
# Button: Initiate Sign Up
# ---------------------------------------------------------------------------

class _InitiateSignUpButton(discord.ui.Button):
    def __init__(self, match_id: int):
        self._match_id = match_id
        super().__init__(
            label="🔄 Initiate Sign Up",
            style=discord.ButtonStyle.secondary,
            custom_id=f"reschedule_initiate_{match_id}",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        db = interaction.client.db
        if not _is_manager(interaction, db):
            await interaction.response.send_message(
                "Only managers and administrators can initiate sign-ups.",
                ephemeral=True,
            )
            return

        match_id = self._match_id
        old_ts = self.view.old_ts
        new_ts = self.view.new_ts

        fresh_match = db.get_match(match_id)

        # Collect allocated talent before reset for notifications
        alloc = db.get_allocation(match_id)
        allocated_user_ids: list[str] = []
        if alloc and alloc.get("role_assignments"):
            role_assignments = json.loads(alloc["role_assignments"])
            seen: set[str] = set()
            for assignment in role_assignments.values():
                if isinstance(assignment, dict):
                    uid = assignment.get("user_id")
                    if uid and uid not in seen:
                        seen.add(uid)
                        allocated_user_ids.append(uid)

        # Update match time in DB
        db.update_match_time(match_id, new_ts)
        fresh_match = db.get_match(match_id)

        # Update TeamUp event to proposed subcalendar
        event_id = fresh_match.get("teamup_event_id") if fresh_match else None
        if event_id:
            teamup = interaction.client.get_teamup()
            if teamup:
                try:
                    title = (
                        f"[{fresh_match['division']}] "
                        f"{fresh_match['team_home']} vs {fresh_match['team_away']}"
                    )
                    teamup.update_event(
                        event_id, title, new_ts, match_end_ts(new_ts),
                        subcalendar="proposed",
                    )
                except Exception as e:
                    log.warning("_InitiateSignUpButton: failed to update TeamUp event: %s", e)

        # Edit old sign-up message to RESCHEDULED state (no buttons)
        bcast = db.get_broadcast_message(match_id)
        signup_ch_id = db.get_config("signup_channel_id") or db.get_config("broadcast_channel_id")
        signup_ch = interaction.client.get_channel(int(signup_ch_id)) if signup_ch_id else None

        if bcast and signup_ch and fresh_match:
            try:
                old_msg = await signup_ch.fetch_message(int(bcast["discord_message_id"]))
                await old_msg.edit(
                    content=(
                        f"{_SEPARATOR}\n"
                        f"📋 [{fresh_match['division']}] "
                        f"{fresh_match['team_home']} vs {fresh_match['team_away']}\n"
                        f"~~<t:{old_ts}:F>~~\n\n"
                        f"🔄 This match has been rescheduled. A new sign-up has been posted."
                    ),
                    view=discord.ui.View(),
                )
            except Exception as e:
                log.warning("_InitiateSignUpButton: failed to edit old sign-up message: %s", e)

        # Reset allocation
        db.reset_allocation(match_id)

        # Send update notification before posting new sign-up
        updates_ch_id = db.get_config("schedule_updates_channel_id")
        notify_ch = (
            interaction.client.get_channel(int(updates_ch_id)) if updates_ch_id else signup_ch
        )
        if notify_ch and fresh_match and allocated_user_ids:
            mentions = " ".join(f"<@{uid}>" for uid in allocated_user_ids)
            notify_text = (
                f"{mentions}\n"
                f"🔄 **Reschedule Notice** — "
                f"[{fresh_match['division']}] {fresh_match['team_home']} vs "
                f"{fresh_match['team_away']} has been rescheduled to <t:{new_ts}:F>. "
                f"A new sign-up has been posted."
            )
            try:
                await notify_ch.send(notify_text)
            except Exception as e:
                log.warning("_InitiateSignUpButton: failed to send update notification: %s", e)

        # Post new sign-up message
        from cogs.signup import SignUpView
        if signup_ch and fresh_match:
            try:
                talent_role_id = db.get_config("talent_role_id")
                talent_role_mention = f"<@&{talent_role_id}>" if talent_role_id else ""
                signups = db.get_signups_for_match(match_id)
                new_content = build_signup_message(
                    fresh_match, signups, talent_role_mention=talent_role_mention
                )
                new_msg = await signup_ch.send(content=new_content, view=SignUpView(match_id))
                db.insert_broadcast_message(match_id, str(new_msg.id), str(signup_ch.id))
            except Exception as e:
                log.warning("_InitiateSignUpButton: failed to post new sign-up message: %s", e)

        # Dispatch match_logged to refresh proposal dropdown
        new_date = datetime.fromtimestamp(new_ts, tz=_ET).strftime("%Y-%m-%d")
        interaction.client.dispatch("match_logged", new_date)

        # Thread notice
        await send_thread_reschedule_notice(
            interaction.client, db, match_id,
            "A new match time has been detected, the broadcast team are checking for "
            "availability. If approved a new match thread will be created.",
        )

        # Disable view and edit interaction message
        self.view.stop()
        _disable_view(self.view)
        await interaction.response.edit_message(
            content=interaction.message.content + "\n\n🔄 **New sign-up posted.**",
            view=self.view,
        )


# ---------------------------------------------------------------------------
# Button: Cancel Broadcast
# ---------------------------------------------------------------------------

class _CancelBroadcastButton(discord.ui.Button):
    def __init__(self, match_id: int):
        self._match_id = match_id
        super().__init__(
            label="❌ Cancel Broadcast",
            style=discord.ButtonStyle.danger,
            custom_id=f"reschedule_cancel_{match_id}",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        db = interaction.client.db
        if not _is_manager(interaction, db):
            await interaction.response.send_message(
                "Only managers and administrators can cancel broadcasts.",
                ephemeral=True,
            )
            return

        match_id = self._match_id
        fresh_match = db.get_match(match_id)

        # Delete TeamUp event if present
        event_id = fresh_match.get("teamup_event_id") if fresh_match else None
        if event_id:
            teamup = interaction.client.get_teamup()
            if teamup:
                try:
                    teamup.delete_event(event_id)
                except Exception as e:
                    log.warning("_CancelBroadcastButton: failed to delete TeamUp event: %s", e)
            db.update_match_teamup_id(match_id, None)
            if fresh_match:
                db.decrement_scheduled_count(fresh_match["team_home"])
                db.decrement_scheduled_count(fresh_match["team_away"])

        # Collect signed-up talent for notification
        signups = db.get_signups_for_match(match_id)
        all_user_ids = list({s["user_id"] for s in signups})

        # Reset allocation and mark match cancelled
        db.reset_allocation(match_id)
        db.set_allocation_status(match_id, "cancelled")

        # Edit sign-up message to CANCELLED state
        bcast = db.get_broadcast_message(match_id)
        signup_ch_id = db.get_config("signup_channel_id") or db.get_config("broadcast_channel_id")
        signup_ch = interaction.client.get_channel(int(signup_ch_id)) if signup_ch_id else None

        if bcast and signup_ch and fresh_match:
            try:
                msg = await signup_ch.fetch_message(int(bcast["discord_message_id"]))
                ts = fresh_match["match_time"]
                await msg.edit(
                    content=(
                        f"{_SEPARATOR}\n"
                        f"❌ **BROADCAST CANCELLED**\n"
                        f"📋 [{fresh_match['division']}] "
                        f"{fresh_match['team_home']} vs {fresh_match['team_away']}\n"
                        f"<t:{ts}:F>\n\n"
                        f"This broadcast has been cancelled by management."
                    ),
                    view=discord.ui.View(),
                )
            except Exception as e:
                log.warning("_CancelBroadcastButton: failed to edit sign-up message: %s", e)

        # Send cancellation notification
        updates_ch_id = db.get_config("schedule_updates_channel_id")
        notify_ch = (
            interaction.client.get_channel(int(updates_ch_id)) if updates_ch_id else signup_ch
        )
        if notify_ch and fresh_match:
            mentions = " ".join(f"<@{uid}>" for uid in all_user_ids)
            ts = fresh_match["match_time"]
            cancel_text = (
                f"{_SEPARATOR}\n"
                f"🚫 **Broadcast Cancelled**\n"
                f"**[{fresh_match['division']}] {fresh_match['team_home']} vs "
                f"{fresh_match['team_away']}** | <t:{ts}:F>\n\n"
                f"This broadcast has been cancelled by management."
            )
            if mentions:
                cancel_text += f"\n\n{mentions}"
            try:
                await notify_ch.send(cancel_text)
            except Exception as e:
                log.warning("_CancelBroadcastButton: failed to send cancel notification: %s", e)

        # Thread notice
        await send_thread_reschedule_notice(
            interaction.client, db, match_id,
            "A new match time has been detected, the broadcast team has decided "
            "to cancel this stream.",
        )

        # Disable view and edit interaction message
        self.view.stop()
        _disable_view(self.view)
        await interaction.response.edit_message(
            content=interaction.message.content + "\n\n❌ **Broadcast cancelled.**",
            view=self.view,
        )


# ---------------------------------------------------------------------------
# Composite view
# ---------------------------------------------------------------------------

class RescheduleView(discord.ui.View):
    """Posted to the log channel when a confirmed broadcast is rescheduled."""

    def __init__(self, match_id: int, old_ts: int, new_ts: int):
        super().__init__(timeout=None)
        self.match_id = match_id
        self.old_ts = old_ts
        self.new_ts = new_ts
        self.add_item(_UpdateBroadcastButton(match_id))
        self.add_item(_InitiateSignUpButton(match_id))
        self.add_item(_CancelBroadcastButton(match_id))
