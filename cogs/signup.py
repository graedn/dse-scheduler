import discord
import json
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from scheduler import REQUIRED_ROLES, _SEPARATOR, _fmt_date

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

# Role definitions in display order: (role_key, label, required, row)
_ROLE_DEFS = [
    ("producer", "Producer",      True,  0),
    ("observer", "Observer",      True,  0),
    ("pbp",      "Play-by-Play",  True,  0),
    ("colour",   "Colour Caster", True,  0),
    ("host",     "Host",          False, 1),
    ("analyst",  "Analyst",       False, 1),
]


def _manager_check(interaction: discord.Interaction, db) -> bool:
    if not interaction.guild:
        return False
    if interaction.user.guild_permissions.administrator:
        return True
    return db.is_manager(str(interaction.user.id))


def _get_effective_signup_ch(db, bot):
    ch_id = db.get_config("signup_channel_id") or db.get_config("broadcast_channel_id")
    return bot.get_channel(int(ch_id)) if ch_id else None


# ---------------------------------------------------------------------------
# Sign-up toggle button
# ---------------------------------------------------------------------------

class SignUpButton(discord.ui.Button):
    def __init__(self, role: str, label: str, match_id: int, required: bool, row: int):
        self.role = role
        self.match_id = match_id
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary if required else discord.ButtonStyle.danger,
            custom_id=f"signup_{role}_{match_id}",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        db = interaction.client.db
        match = db.get_match(self.match_id)
        if not match or match.get("broadcast_accepted"):
            await interaction.response.send_message(
                "This match is no longer accepting sign-ups.", ephemeral=True
            )
            return

        if not interaction.guild:
            await interaction.response.send_message(
                "Must be used in a server.", ephemeral=True
            )
            return

        try:
            member = await interaction.guild.fetch_member(int(interaction.user.id))
        except discord.HTTPException:
            member = interaction.user

        user_id = str(interaction.user.id)
        username = str(member)
        display_name = member.display_name

        signups = db.get_signups_for_match(self.match_id)
        already_mine = any(
            s["user_id"] == user_id and s["role"] == self.role for s in signups
        )

        if already_mine:
            db.remove_signup(self.match_id, self.role, user_id)
        else:
            db.upsert_signup(
                match_id=self.match_id,
                message_id=str(interaction.message.id),
                role=self.role,
                user_id=user_id,
                username=username,
                display_name=display_name,
            )

        from scheduler import build_signup_message
        fresh_match = db.get_match(self.match_id) or match
        fresh_signups = db.get_signups_for_match(self.match_id)
        new_content = build_signup_message(fresh_match, fresh_signups)
        await interaction.response.edit_message(content=new_content, view=self.view)


# ---------------------------------------------------------------------------
# Force Schedule (manager-only)
# ---------------------------------------------------------------------------

class ForceStartButton(discord.ui.Button):
    def __init__(self, match_id: int):
        self.match_id = match_id
        super().__init__(
            label="Force Schedule",
            style=discord.ButtonStyle.success,
            custom_id=f"force_start_{match_id}",
            emoji="🟢",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                "Must be used in a server.", ephemeral=True
            )
            return

        db = interaction.client.db
        if not _manager_check(interaction, db):
            await interaction.response.send_message(
                "Only managers and administrators can force-schedule talent allocation.",
                ephemeral=True,
            )
            return

        match = db.get_match(self.match_id)
        if not match or match.get("broadcast_accepted"):
            await interaction.response.send_message(
                "This match is no longer available for allocation.", ephemeral=True
            )
            return

        existing = db.get_allocation(self.match_id)
        if existing and existing["status"] in ("awaiting_confirm", "accepted"):
            await interaction.response.send_message(
                "Allocation is already in progress or complete.", ephemeral=True
            )
            return

        from cogs.talent import send_allocation_request
        log_ch_id = db.get_config("log_channel_id")
        broadcast_ch_id = db.get_config("broadcast_channel_id")
        log_ch = interaction.client.get_channel(int(log_ch_id)) if log_ch_id else None
        broadcast_ch = interaction.client.get_channel(int(broadcast_ch_id)) if broadcast_ch_id else None

        await interaction.response.defer(ephemeral=True)
        await send_allocation_request(
            db, match, log_ch, broadcast_ch,
            get_teamup=interaction.client.get_teamup,
        )
        await interaction.followup.send("Talent allocation started.", ephemeral=True)


# ---------------------------------------------------------------------------
# New Match — ephemeral dropdown to swap in an unscheduled match
# ---------------------------------------------------------------------------

class _NewMatchSelect(discord.ui.Select):
    """Ephemeral dropdown listing unscheduled matches for the current date."""
    def __init__(self, current_match_id: int, replacements: list[dict], db, user_tz=None):
        self.current_match_id = current_match_id
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(user_tz) if user_tz else ET
        tz_label = user_tz.split("/")[-1].replace("_", " ") if user_tz else "ET"
        options = []
        for m in replacements:
            home_team = db.get_team(m["team_home"])
            away_team = db.get_team(m["team_away"])
            total_bc = (
                (home_team["broadcast_count"] if home_team else 0)
                + (away_team["broadcast_count"] if away_team else 0)
            )
            dt = datetime.fromtimestamp(m["match_time"], tz=tz)
            # Plain-text time — Discord timestamps don't render in select labels
            time_str = f"{dt.strftime('%a %b')} {dt.day} {dt.strftime('%H:%M')} {tz_label}"
            label = (
                f"[{m['division']}] {m['team_home']} vs {m['team_away']} ({total_bc} bcast)"
            )[:100]
            options.append(discord.SelectOption(
                label=label,
                value=str(m["id"]),
                description=time_str[:100],
            ))

        super().__init__(
            placeholder="Select a match to broadcast...",
            options=options,
            min_values=1,
            max_values=1,
            custom_id="new_match_pick",
        )

    async def callback(self, interaction: discord.Interaction):
        selected_match_id = int(self.values[0])
        db = interaction.client.db
        teamup = interaction.client.get_teamup()

        current_match = db.get_match(self.current_match_id)
        selected_match = db.get_match(selected_match_id)

        if not current_match or not selected_match:
            await interaction.response.edit_message(
                content="❌ Match data no longer available.", view=None
            )
            return

        if selected_match.get("teamup_event_id"):
            await interaction.response.edit_message(
                content="❌ That match is already scheduled. Please try again.", view=None
            )
            return

        # If the current match is accepted, collect the allocated talent and
        # confirmation message info BEFORE resetting anything.
        was_accepted = bool(current_match.get("broadcast_accepted"))
        allocated_talent: list[dict] = []
        confirm_msg_id: str | None = None
        confirm_ch_id: str | None = None
        if was_accepted:
            alloc = db.get_allocation(self.current_match_id)
            if alloc:
                confirm_msg_id = alloc.get("confirmation_message_id")
                confirm_ch_id = alloc.get("confirmation_channel_id")
                if alloc.get("role_assignments"):
                    role_assignments = json.loads(alloc["role_assignments"])
                    seen: set[str] = set()
                    for assignment in role_assignments.values():
                        if isinstance(assignment, dict):
                            uid = assignment.get("user_id")
                            if uid and uid not in seen:
                                seen.add(uid)
                                allocated_talent.append(assignment)

        # Remove current match from calendar
        old_event_id = current_match.get("teamup_event_id")
        if teamup and old_event_id:
            try:
                teamup.delete_event(old_event_id)
            except Exception as e:
                log.warning("New Match: failed to delete event %s: %s", old_event_id, e)
        if old_event_id:
            db.update_match_teamup_id(self.current_match_id, None)
            db.decrement_scheduled_count(current_match["team_home"])
            db.decrement_scheduled_count(current_match["team_away"])
            db.reset_allocation(self.current_match_id)
        if was_accepted:
            db.clear_broadcast_accepted(self.current_match_id)

        # Edit the original sign-up message
        bcast = db.get_broadcast_message(self.current_match_id)
        signup_ch = _get_effective_signup_ch(db, interaction.client)
        if bcast and signup_ch:
            try:
                orig_msg = await signup_ch.fetch_message(int(bcast["discord_message_id"]))
                ts = current_match["match_time"]
                if was_accepted:
                    mentions = " ".join(f"<@{a['user_id']}>" for a in allocated_talent)
                    cancelled_content = (
                        f"{_SEPARATOR}\n"
                        f"❌ **CANCELLED**\n"
                        f"📋 [{current_match['division']}] "
                        f"{current_match['team_home']} vs {current_match['team_away']}\n"
                        f"<t:{ts}:F>\n\n"
                        f"This broadcast has been replaced by a new match."
                    )
                    if mentions:
                        cancelled_content += f"\n\n{mentions}"
                    await orig_msg.edit(content=cancelled_content, view=discord.ui.View())
                else:
                    await orig_msg.edit(
                        content=(
                            f"{_SEPARATOR}\n"
                            f"📋 [{current_match['division']}] "
                            f"{current_match['team_home']} vs {current_match['team_away']}\n"
                            f"<t:{ts}:F>\n\n"
                            f"⏏️ This match has been removed from the broadcast schedule."
                        ),
                        view=discord.ui.View(),
                    )
            except Exception as e:
                log.warning("New Match: failed to edit old sign-up message: %s", e)

        # Edit the talent confirmation message to show the match was replaced
        if was_accepted and confirm_msg_id and confirm_ch_id:
            try:
                confirm_ch = interaction.client.get_channel(int(confirm_ch_id))
                if confirm_ch:
                    confirm_msg = await confirm_ch.fetch_message(int(confirm_msg_id))
                    ts = current_match["match_time"]
                    ts_new = selected_match["match_time"]
                    await confirm_msg.edit(
                        content=(
                            confirm_msg.content
                            + f"\n\n⏏️ **Match replaced** — "
                            f"[{selected_match['division']}] "
                            f"{selected_match['team_home']} vs {selected_match['team_away']} "
                            f"<t:{ts_new}:F> has been selected. "
                            f"This confirmation is no longer active."
                        ),
                        view=discord.ui.View(),
                    )
            except Exception as e:
                log.warning("New Match: failed to edit confirmation message: %s", e)

        # Schedule the selected match
        date_str = datetime.fromtimestamp(
            selected_match["match_time"], tz=ET
        ).strftime("%Y-%m-%d")

        from scheduler import accept_combination
        try:
            await accept_combination([selected_match], date_str, db, teamup, signup_ch)
        except Exception as e:
            log.error("New Match: accept_combination failed for match %s: %s", selected_match_id, e)
            await interaction.response.edit_message(
                content=f"❌ Failed to schedule replacement: `{e}`", view=None
            )
            return

        # Send a ping to allocated talent when an accepted match is replaced
        if was_accepted and allocated_talent and signup_ch:
            mentions = " ".join(f"<@{a['user_id']}>" for a in allocated_talent)
            ts_old = current_match["match_time"]
            ts_new = selected_match["match_time"]
            ping_text = (
                f"{mentions} — the schedule for "
                f"**[{current_match['division']}] {current_match['team_home']} vs "
                f"{current_match['team_away']}** (<t:{ts_old}:F>) has been replaced.\n"
                f"- [{selected_match['division']}] {selected_match['team_home']} vs "
                f"{selected_match['team_away']} — <t:{ts_new}:F>"
            )
            try:
                await signup_ch.send(ping_text)
            except Exception as e:
                log.warning("New Match: failed to send talent ping: %s", e)

        await interaction.response.edit_message(
            content=(
                f"✅ **[{selected_match['division']}] "
                f"{selected_match['team_home']} vs {selected_match['team_away']}** "
                f"added to the broadcast schedule."
            ),
            view=None,
        )


class _NewMatchSelectView(discord.ui.View):
    def __init__(self, current_match_id: int, replacements: list[dict], db, user_tz=None):
        super().__init__(timeout=300)
        self.add_item(_NewMatchSelect(current_match_id, replacements, db, user_tz=user_tz))


class NewMatchButton(discord.ui.Button):
    def __init__(self, match_id: int):
        self.match_id = match_id
        super().__init__(
            label="New Match",
            style=discord.ButtonStyle.secondary,
            custom_id=f"signup_new_match_{match_id}",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return

        db = interaction.client.db
        if not _manager_check(interaction, db):
            await interaction.response.send_message(
                "Only managers and administrators can use this button.", ephemeral=True
            )
            return

        match = db.get_match(self.match_id)
        if not match:
            await interaction.response.send_message("Match not found.", ephemeral=True)
            return

        date_str = datetime.fromtimestamp(match["match_time"], tz=ET).strftime("%Y-%m-%d")

        # Block if there are unresolved proposals for this specific day.
        # Skip for accepted matches — they are being emergency-swapped.
        if not match.get("broadcast_accepted") and db.get_pending_changes_for_date(date_str):
            await interaction.response.send_message(
                "⚠️ There is an unresolved broadcast proposal for this day. "
                "Please resolve it before requesting a new match.",
                ephemeral=True,
            )
            return
        all_matches = db.get_matches_for_date(date_str)
        replacements = [m for m in all_matches if not m.get("teamup_event_id")]

        if not replacements:
            await interaction.response.send_message(
                "No unscheduled matches available for this date.", ephemeral=True
            )
            return

        user_tz = db.get_user_timezone(str(interaction.user.id))
        view = _NewMatchSelectView(self.match_id, replacements, db, user_tz=user_tz)
        await interaction.response.send_message(
            f"Select a match to add to the broadcast schedule for this day:\n"
            f"-# Times shown in **{user_tz}** — use `/set-timezone` to change.",
            view=view,
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Block Day (manager-only)
# ---------------------------------------------------------------------------

class BlockDayButton(discord.ui.Button):
    def __init__(self, match_id: int):
        self.match_id = match_id
        super().__init__(
            label="Block Day",
            style=discord.ButtonStyle.danger,
            custom_id=f"signup_block_day_{match_id}",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return

        db = interaction.client.db
        if not _manager_check(interaction, db):
            await interaction.response.send_message(
                "Only managers and administrators can use this button.", ephemeral=True
            )
            return

        match = db.get_match(self.match_id)
        if not match:
            await interaction.response.send_message("Match not found.", ephemeral=True)
            return

        date_str = datetime.fromtimestamp(match["match_time"], tz=ET).strftime("%Y-%m-%d")

        if db.get_blocked_day(date_str):
            await interaction.response.send_message(
                "This day is already blocked.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        teamup = interaction.client.get_teamup()

        signup_ch = _get_effective_signup_ch(db, interaction.client)
        log_ch_id = db.get_config("log_channel_id")
        log_ch = interaction.client.get_channel(int(log_ch_id)) if log_ch_id else None

        # Remove all proposed matches for this day from calendar
        day_matches = db.get_matches_for_date(date_str)
        day_match_ids = {m["id"] for m in day_matches}

        for m in day_matches:
            eid = m.get("teamup_event_id")
            if eid and teamup:
                try:
                    teamup.delete_event(eid)
                except Exception as e:
                    log.warning("Block: failed to remove event %s: %s", eid, e)
            if eid:
                db.update_match_teamup_id(m["id"], None)
                db.decrement_scheduled_count(m["team_home"])
                db.decrement_scheduled_count(m["team_away"])
                db.reset_allocation(m["id"])

            # Edit the sign-up message for this match
            bcast = db.get_broadcast_message(m["id"])
            if bcast and signup_ch:
                try:
                    msg = await signup_ch.fetch_message(int(bcast["discord_message_id"]))
                    ts = m["match_time"]
                    await msg.edit(
                        content=(
                            f"{_SEPARATOR}\n"
                            f"📋 [{m['division']}] {m['team_home']} vs {m['team_away']}\n"
                            f"<t:{ts}:F>\n\n"
                            f"🚫 This day has been blocked. No broadcast scheduled."
                        ),
                        view=discord.ui.View(),
                    )
                except Exception as e:
                    log.warning("Block: failed to edit sign-up message for match %s: %s", m["id"], e)

        # Edit and close any pending proposals that reference this date
        for change in db.get_all_pending_changes():
            change_match_ids = set(json.loads(change.get("new_match_ids") or "[]"))
            if not (change_match_ids & day_match_ids):
                continue
            msg_id = change.get("discord_message_id")
            if msg_id and log_ch:
                try:
                    proposal_msg = await log_ch.fetch_message(int(msg_id))
                    await proposal_msg.edit(
                        content=(
                            proposal_msg.content
                            + f"\n\n🚫 **Day blocked** — {_fmt_date(date_str)} "
                            f"has been removed from the broadcast schedule."
                        ),
                        view=discord.ui.View(),
                    )
                except Exception as e:
                    log.warning("Block: failed to edit proposal message %s: %s", msg_id, e)
            db.resolve_pending_change(change["id"], approved=False)

        # Create NO STREAM block event in TeamUp
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
                    BLOCK_PREFIX,
                    int(day_start.timestamp()),
                    int(day_end.timestamp()),
                )
            except TeamUpError as e:
                log.error("Block: failed to create NO STREAM event for %s: %s", date_str, e)

        db.insert_blocked_day(date_str, reason=None, teamup_event_id=block_event_id)

        await interaction.followup.send(
            f"🚫 **{_fmt_date(date_str)} blocked** — all events removed and NO STREAM added.",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Composite view
# ---------------------------------------------------------------------------

class SignUpView(discord.ui.View):
    def __init__(self, match_id: int):
        super().__init__(timeout=None)  # persistent
        for role, label, required, row in _ROLE_DEFS:
            self.add_item(SignUpButton(
                role=role, label=label, match_id=match_id,
                required=required, row=row,
            ))
        self.add_item(ForceStartButton(match_id))
        self.add_item(NewMatchButton(match_id))
        self.add_item(BlockDayButton(match_id))


class ApprovedSignUpView(discord.ui.View):
    """Persistent view shown on sign-up messages after talent confirmation is complete.
    Only New Match and Block Day remain active."""
    def __init__(self, match_id: int):
        super().__init__(timeout=None)
        self.add_item(NewMatchButton(match_id))
        self.add_item(BlockDayButton(match_id))
