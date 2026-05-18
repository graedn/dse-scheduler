import discord
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
log = logging.getLogger(__name__)


class _ApproveButton(discord.ui.Button):
    def __init__(self, change_id: int):
        self.change_id = change_id
        super().__init__(
            label="Approve",
            style=discord.ButtonStyle.success,
            custom_id=f"proposal_approve_{change_id}",
            emoji="✅",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await _check_manager(interaction):
            return
        db = interaction.client.db
        change = db.get_pending_change(self.change_id)
        if not change or change.get("approved") is not None:
            await interaction.response.send_message(
                "This proposal has already been resolved.", ephemeral=True
            )
            return

        await interaction.response.defer()

        from scheduler import apply_pending_change, _fmt_date
        broadcast_ch = _get_channel(interaction.client, db, "broadcast_channel_id")
        signup_ch = _get_channel(interaction.client, db, "signup_channel_id")
        teamup = interaction.client.get_teamup()

        try:
            # Collect old broadcast info before apply wipes teamup_event_ids
            old_info = _collect_old_broadcast_info(change, db, interaction.client)
            # Collect new match IDs before apply so we can reference them in pings
            new_match_ids: list[int] = json.loads(change.get("new_match_ids") or "[]")
            new_matches = [m for mid in new_match_ids if (m := db.get_match(mid))]
            date_str = await apply_pending_change(
                change, db, teamup, broadcast_ch, signup_channel=signup_ch
            )
            if old_info:
                await _edit_old_signup_messages(old_info, interaction.client, new_matches=new_matches)
            # Cancel any active talent allocations for displaced matches
            log_ch = _get_channel(interaction.client, db, "log_channel_id")
            await _cancel_displaced_allocations(old_info, db, interaction.client, log_ch)
        except Exception as e:
            log.exception("Error applying pending change %s", self.change_id)
            log_ch = _get_channel(interaction.client, db, "log_channel_id")
            if log_ch:
                await log_ch.send(
                    f"❌ **Error approving proposal**: `{e}`\n"
                    f"The proposal may be partially applied — check TeamUp and sign-up messages."
                )
            await interaction.followup.send(f"❌ Error during approval: `{e}`", ephemeral=True)
            return

        await interaction.message.edit(
            content=interaction.message.content + "\n\n✅ **Approved.**",
            view=discord.ui.View(),
        )
        log_ch = _get_channel(interaction.client, db, "log_channel_id")
        if log_ch and date_str:
            from scheduler import _fmt_date
            await log_ch.send(
                f"✅ Schedule approved for {_fmt_date(date_str)}. "
                f"New sign-up posts sent to broadcast channel."
            )


class _RejectButton(discord.ui.Button):
    def __init__(self, change_id: int):
        self.change_id = change_id
        super().__init__(
            label="Reject",
            style=discord.ButtonStyle.danger,
            custom_id=f"proposal_reject_{change_id}",
            emoji="❌",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await _check_manager(interaction):
            return
        db = interaction.client.db
        change = db.get_pending_change(self.change_id)
        if not change or change.get("approved") is not None:
            await interaction.response.send_message(
                "This proposal has already been resolved.", ephemeral=True
            )
            return
        db.resolve_pending_change(self.change_id, approved=False)
        await interaction.response.edit_message(
            content=interaction.message.content + "\n\n❌ **Rejected.**",
            view=discord.ui.View(),
        )


class _DeleteButton(discord.ui.Button):
    def __init__(self, change_id: int):
        self.change_id = change_id
        super().__init__(
            label="Delete Events",
            style=discord.ButtonStyle.secondary,
            custom_id=f"proposal_delete_{change_id}",
            emoji="🗑️",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await _check_manager(interaction):
            return
        db = interaction.client.db
        change = db.get_pending_change(self.change_id)
        if not change or change.get("approved") is not None:
            await interaction.response.send_message(
                "This proposal has already been resolved.", ephemeral=True
            )
            return

        await interaction.response.defer()
        teamup = interaction.client.get_teamup()
        old_ids: list[str] = json.loads(change.get("old_event_ids") or "[]")
        from cogs.confirm_view import cancel_orphaned_confirmation
        for event_id in old_ids:
            if teamup:
                try:
                    teamup.delete_event(event_id)
                except Exception as e:
                    log.warning("Delete: failed to remove event %s: %s", event_id, e)
            for m in db.get_matches_by_teamup_event_id(event_id):
                await cancel_orphaned_confirmation(
                    interaction.client, db, m["id"],
                    reason="this proposal was deleted",
                )
                db.update_match_teamup_id(m["id"], None)
                db.decrement_scheduled_count(m["team_home"])
                db.decrement_scheduled_count(m["team_away"])
                db.reset_allocation(m["id"])

        db.resolve_pending_change(self.change_id, approved=False)
        await interaction.message.edit(
            content=interaction.message.content + "\n\n🗑️ **Deleted — scheduled events removed.**",
            view=discord.ui.View(),
        )


class _BlockDayButton(discord.ui.Button):
    def __init__(self, change_id: int):
        self.change_id = change_id
        super().__init__(
            label="Block Day",
            style=discord.ButtonStyle.danger,
            custom_id=f"proposal_block_{change_id}",
            emoji="🚫",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await _check_manager(interaction):
            return
        db = interaction.client.db
        change = db.get_pending_change(self.change_id)
        if not change or change.get("approved") is not None:
            await interaction.response.send_message(
                "This proposal has already been resolved.", ephemeral=True
            )
            return

        await interaction.response.defer()
        teamup = interaction.client.get_teamup()
        old_ids: list[str] = json.loads(change.get("old_event_ids") or "[]")
        new_ids: list[int] = json.loads(change.get("new_match_ids") or "[]")

        # Determine date_str from the change
        date_str = None
        for event_id in old_ids:
            matches = db.get_matches_by_teamup_event_id(event_id)
            if matches:
                date_str = datetime.fromtimestamp(
                    matches[0]["match_time"], tz=ET
                ).strftime("%Y-%m-%d")
                break
        if not date_str:
            for mid in new_ids:
                m = db.get_match(mid)
                if m:
                    date_str = datetime.fromtimestamp(
                        m["match_time"], tz=ET
                    ).strftime("%Y-%m-%d")
                    break

        if not date_str:
            await interaction.followup.send("Could not determine date.", ephemeral=True)
            return

        # Delete all events for the day
        from cogs.confirm_view import cancel_orphaned_confirmation
        for m in db.get_matches_for_date(date_str):
            eid = m.get("teamup_event_id")
            if eid and teamup:
                try:
                    teamup.delete_event(eid)
                except Exception as e:
                    log.warning("Block: failed to remove event %s: %s", eid, e)
            if eid:
                await cancel_orphaned_confirmation(
                    interaction.client, db, m["id"],
                    reason="this day was blocked",
                )
                db.update_match_teamup_id(m["id"], None)
                db.decrement_scheduled_count(m["team_home"])
                db.decrement_scheduled_count(m["team_away"])
                db.reset_allocation(m["id"])

        # Create NO STREAM block event
        from cogs.blocks import BLOCK_PREFIX
        from teamup import TeamUpError
        from zoneinfo import ZoneInfo as _ZoneInfo
        block_event_id = None
        if teamup:
            _ET_block = _ZoneInfo("America/New_York")
            day_start = datetime.strptime(date_str, "%Y-%m-%d").replace(
                tzinfo=_ET_block, hour=0, minute=1, second=0
            )
            day_end = day_start.replace(hour=23, minute=59, second=0)
            try:
                block_event_id = teamup.create_event(
                    BLOCK_PREFIX, int(day_start.timestamp()), int(day_end.timestamp()),
                )
            except TeamUpError as e:
                log.error("Block: failed to create NO STREAM event for %s: %s", date_str, e)

        db.insert_blocked_day(date_str, reason=None, teamup_event_id=block_event_id)
        db.resolve_pending_change(self.change_id, approved=False)

        from scheduler import _fmt_date
        await interaction.message.edit(
            content=interaction.message.content
                    + f"\n\n🚫 **{_fmt_date(date_str)} blocked** — all events removed and NO STREAM added.",
            view=discord.ui.View(),
        )


class ProposalView(discord.ui.View):
    def __init__(self, change_id: int):
        super().__init__(timeout=None)
        self.add_item(_ApproveButton(change_id))
        self.add_item(_RejectButton(change_id))
        self.add_item(_DeleteButton(change_id))
        self.add_item(_BlockDayButton(change_id))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _check_manager(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        await interaction.response.send_message("Must be used in a server.", ephemeral=True)
        return False
    db = interaction.client.db
    is_admin = interaction.user.guild_permissions.administrator
    is_mgr = db.is_manager(str(interaction.user.id))
    if not is_mgr:
        role_id = db.get_config("manager_role_id")
        if role_id:
            is_mgr = any(str(r.id) == role_id for r in interaction.user.roles)
    if not is_admin and not is_mgr:
        await interaction.response.send_message(
            "Only managers and administrators can manage proposals.", ephemeral=True
        )
        return False
    return True


def _get_channel(bot, db, config_key: str):
    ch_id = db.get_config(config_key)
    return bot.get_channel(int(ch_id)) if ch_id else None


def _collect_old_broadcast_info(change: dict, db, bot) -> list[dict]:
    old_event_ids: list[str] = json.loads(change.get("old_event_ids") or "[]")
    info = []
    for event_id in old_event_ids:
        for match in db.get_matches_by_teamup_event_id(event_id):
            bcast = db.get_broadcast_message(match["id"])
            signups = db.get_signups_for_match(match["id"])
            info.append({"match": match, "bcast": bcast, "signups": signups})
    return info


async def _edit_old_signup_messages(old_info: list[dict], bot,
                                    new_matches: list[dict] = None) -> None:
    from scheduler import _SEPARATOR
    for item in old_info:
        match = item["match"]
        bcast = item["bcast"]
        signups = item["signups"]
        if not bcast:
            continue
        ts = match["match_time"]
        content = (
            f"{_SEPARATOR}\n"
            f"📋 [{match['division']}] {match['team_home']} vs {match['team_away']}\n"
            f"<t:{ts}:F>\n\n"
            f"⚠️ The broadcast schedule has been updated. New sign-up posts have been sent."
        )
        ping_text = None
        notifiable = [s for s in signups if s["role"] != "unavailable"]
        if notifiable:
            user_ids = list(dict.fromkeys(s["user_id"] for s in notifiable))
            mentions = " ".join(f"<@{uid}>" for uid in user_ids)
            content += (
                f"\n\n{mentions} — the schedule has changed. "
                f"Please check the new sign-up post and re-confirm your availability."
            )
            # Editing a message doesn't ping users — send a separate message to notify them.
            replacement_lines = ""
            if new_matches:
                replacement_lines = "\n" + "\n".join(
                    f"- [{m['division']}] {m['team_home']} vs {m['team_away']} — <t:{m['match_time']}:F>"
                    for m in sorted(new_matches, key=lambda m: m["match_time"])
                )
            ping_text = (
                f"{mentions} — the schedule for "
                f"**[{match['division']}] {match['team_home']} vs {match['team_away']}** "
                f"(<t:{ts}:F>) has changed."
                f"{replacement_lines}"
            )
        ch = bot.get_channel(int(bcast["channel_id"]))
        if not ch:
            continue
        try:
            msg = await ch.fetch_message(int(bcast["discord_message_id"]))
            await msg.edit(content=content, view=discord.ui.View())
            if ping_text:
                await ch.send(ping_text)
        except Exception as e:
            log.warning("Failed to edit old sign-up message for match %s: %s", match["id"], e)


async def _cancel_displaced_allocations(old_info: list[dict], db, bot, log_ch) -> None:
    """For each displaced match that has an active talent allocation, cancel it and
    edit the allocation message in the log channel to prevent stale confirmations."""
    from scheduler import _SEPARATOR

    _ACTIVE_STATUSES = {"pending", "sent", "last_call"}

    for item in old_info:
        match = item["match"]
        alloc = db.get_allocation(match["id"])
        if not alloc or alloc.get("status") not in _ACTIVE_STATUSES:
            continue

        # Mark cancelled so _ConfirmButton's guard also blocks it
        db.set_allocation_status(match["id"], "cancelled")

        alloc_msg_id = alloc.get("allocation_message_id")
        alloc_ch_id  = alloc.get("allocation_channel_id")
        if not alloc_msg_id or not alloc_ch_id:
            # Message ID not stored (pre-migration allocation) — just send a notice
            if log_ch:
                ts = match["match_time"]
                try:
                    await log_ch.send(
                        f"{_SEPARATOR}\n"
                        f"❌ **Talent allocation cancelled**\n"
                        f"**[{match['division']}] {match['team_home']} vs {match['team_away']}**"
                        f" | <t:{ts}:F>\n\n"
                        f"This match was removed from the broadcast schedule by a proposal approval."
                    )
                except Exception as e:
                    log.warning("Failed to send allocation cancel notice for match %s: %s",
                                match["id"], e)
            continue

        # Edit the original allocation message
        ch = bot.get_channel(int(alloc_ch_id))
        if not ch:
            continue
        ts = match["match_time"]
        try:
            msg = await ch.fetch_message(int(alloc_msg_id))
            await msg.edit(
                content=(
                    f"{_SEPARATOR}\n"
                    f"❌ **Talent Allocation Cancelled**\n"
                    f"**[{match['division']}] {match['team_home']} vs {match['team_away']}**"
                    f" | <t:{ts}:F>\n\n"
                    f"This match was removed from the broadcast schedule by a proposal approval."
                ),
                view=discord.ui.View(),
            )
        except Exception as e:
            log.warning("Failed to edit allocation message for match %s: %s", match["id"], e)
