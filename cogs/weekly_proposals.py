"""Weekly broadcast schedule proposal system.

Every Sunday at 11pm ET, the bot posts seven proposal messages (Mon–Sun) to
the configured Proposal Channel.  Managers use dropdown selects to assign
matches to broadcast slots and confirm with the Update Schedule button.
"""
import discord
import logging
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
from discord.ext import commands

from scheduler import (
    _SEPARATOR, _fmt_date, accept_combination,
)

ET = ZoneInfo("America/New_York")
log = logging.getLogger(__name__)

PAIR_MIN_H = 2.0  # Minimum gap (hours) between two scheduled matches


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRIORITY_SLOTS: dict[tuple[int, int], str] = {
    (19,  0): "⭐ ",
    (21, 30): "⭐ ",
    (20,  0): "🌑 ",
    (22, 30): "🌑 ",
}


def get_priority_label(match_time: int) -> str:
    """Return a priority emoji prefix for Thu/Fri prime-time slots, else empty string."""
    dt = datetime.fromtimestamp(match_time, tz=ET)
    if dt.weekday() not in (3, 4):  # 3=Thursday, 4=Friday
        return ""
    return _PRIORITY_SLOTS.get((dt.hour, dt.minute), "")


def _match_option_label(match: dict) -> str:
    label = (
        f"{get_priority_label(match['match_time'])}"
        f"[{match['division']}] {match['team_home']} vs {match['team_away']}"
    )
    return label[:100]


def _build_match_options(matches: list[dict]) -> list[discord.SelectOption]:
    options = [discord.SelectOption(label="None", value="none",
                                    description="Remove this slot")]
    for m in sorted(matches, key=lambda x: x["match_time"]):
        label = _match_option_label(m)
        options.append(discord.SelectOption(label=label, value=str(m["id"])))
    return options


def build_proposal_day_content(date_str: str, db) -> str:
    """Build the full text for a proposal day message."""
    proposal = db.get_proposal_message(date_str)
    all_matches = db.get_matches_for_date(date_str)

    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=ET)
    day_ts = int(dt.timestamp())
    status = proposal["status"] if proposal else "open"

    # Current Schedule: slot1 and slot2 matches
    current_ids: set[int] = set()
    current_matches: list[dict] = []
    if proposal:
        for slot_key in ("slot1_match_id", "slot2_match_id"):
            mid = proposal.get(slot_key)
            if mid:
                m = db.get_match(mid)
                if m:
                    current_ids.add(mid)
                    current_matches.append(m)
    current_matches.sort(key=lambda x: x["match_time"])

    # Logged Matches: all other matches for the day
    logged_matches = sorted(
        [m for m in all_matches if m["id"] not in current_ids],
        key=lambda x: x["match_time"],
    )

    lines = [
        _SEPARATOR,
        f"📋 **Broadcast Schedule Proposal — "
        f"{dt.strftime('%A')} <t:{day_ts}:D>, <t:{day_ts}:R>**",
        "",
        "**Current Schedule:**",
    ]

    if current_matches:
        for m in current_matches:
            ts = m["match_time"]
            lines.append(
                f"• {get_priority_label(ts)}[{m['division']}] {m['team_home']} vs {m['team_away']} — <t:{ts}:t>"
            )
    elif status == "blocked":
        lines.append("🚫 Day blocked — NO STREAM")
    else:
        lines.append("*No matches selected.*")

    lines += ["", "**Logged Matches:**"]
    if logged_matches:
        for m in logged_matches:
            ts = m["match_time"]
            lines.append(
                f"• {get_priority_label(ts)}[{m['division']}] {m['team_home']} vs {m['team_away']} — <t:{ts}:F>"
            )
    elif not all_matches:
        lines.append("*No matches logged for this day.*")
    else:
        lines.append("*All matches assigned to Current Schedule.*")

    if status == "passed":
        lines += ["", "🔒 *This date has passed. Proposal is closed.*"]

    return "\n".join(lines)


async def _refresh_proposal_message(date_str: str, bot, db) -> None:
    """Fetch and edit the proposal day message for a date with fresh content."""
    proposal = db.get_proposal_message(date_str)
    if not proposal:
        return
    msg_id = proposal.get("discord_message_id")
    ch_id = proposal.get("channel_id")
    if not msg_id or not ch_id:
        return

    channel = bot.get_channel(int(ch_id))
    if not channel:
        return
    try:
        msg = await channel.fetch_message(int(msg_id))
    except discord.NotFound:
        return
    except Exception as e:
        log.warning("Failed to fetch proposal message %s: %s", msg_id, e)
        return

    content = build_proposal_day_content(date_str, db)
    today = datetime.now(tz=ET).date()
    date = datetime.strptime(date_str, "%Y-%m-%d").date()
    status = proposal.get("status", "open")

    if date < today or status == "passed":
        view = discord.ui.View()
    elif status == "blocked":
        view = BlockedDayView(date_str)
    else:
        all_matches = db.get_matches_for_date(date_str)
        slot1_id = proposal.get("slot1_match_id")
        slot2_id = proposal.get("slot2_match_id")
        view = ProposalDayView(date_str, all_matches,
                               slot1_match_id=slot1_id, slot2_match_id=slot2_id)

    try:
        await msg.edit(content=content, view=view)
    except Exception as e:
        log.warning("Failed to edit proposal message %s: %s", msg_id, e)


async def update_proposal_message_for_date(date_str: str, bot, db) -> None:
    """Update the proposal message for a date when a new match is logged."""
    proposal = db.get_proposal_message(date_str)
    if not proposal:
        return
    await _refresh_proposal_message(date_str, bot, db)


async def create_weekly_proposals(bot, db, start_date=None) -> None:
    """Create (or update) proposal messages for a range of days.

    When called with no start_date (the Sunday 11pm scheduler job), creates
    Mon–Sun of the *coming* week.  When start_date is provided (manual command),
    creates from that date through its Sunday, using the Monday of that week as
    week_start so the messages belong to the correct week in the DB.
    """
    proposal_ch_id = db.get_config("proposal_channel_id")
    if not proposal_ch_id:
        return
    channel = bot.get_channel(int(proposal_ch_id))
    if not channel:
        return

    today = datetime.now(tz=ET).date()

    if start_date is None:
        # Automated Sunday job: always target next week Mon–Sun
        days_until_monday = (7 - today.weekday()) % 7 or 7
        first_day = today + timedelta(days=days_until_monday)
        days = [first_day + timedelta(days=i) for i in range(7)]
    else:
        # Manual command: from start_date through this Sunday (inclusive)
        days_until_sunday = 6 - start_date.weekday()
        days = [start_date + timedelta(days=i) for i in range(days_until_sunday + 1)]

    # week_start is always the Monday that owns this range
    week_start = (days[0] - timedelta(days=days[0].weekday())).isoformat()

    for day in days:
        date_str = day.isoformat()
        day_dt = datetime(day.year, day.month, day.day, tzinfo=ET)
        day_ts = int(day_dt.timestamp())

        db.create_proposal_message(date_str, day_ts, week_start)
        proposal = db.get_proposal_message(date_str)
        all_matches = db.get_matches_for_date(date_str)

        content = build_proposal_day_content(date_str, db)
        slot1_id = proposal.get("slot1_match_id")
        slot2_id = proposal.get("slot2_match_id")
        view = ProposalDayView(date_str, all_matches,
                               slot1_match_id=slot1_id, slot2_match_id=slot2_id)

        existing_msg_id = proposal.get("discord_message_id")
        if existing_msg_id and proposal.get("channel_id") == str(channel.id):
            try:
                msg = await channel.fetch_message(int(existing_msg_id))
                await msg.edit(content=content, view=view)
                continue
            except discord.NotFound:
                pass
            except Exception as e:
                log.warning("Failed to update proposal message for %s: %s", date_str, e)

        try:
            msg = await channel.send(content=content, view=view)
            db.set_proposal_discord_message(date_str, str(msg.id), str(channel.id))
        except Exception as e:
            log.error("Failed to post proposal message for %s: %s", date_str, e)


async def mark_passed_proposals(db, bot) -> None:
    """Mark proposals whose dates have passed, and remove their buttons."""
    today = datetime.now(tz=ET).date()
    for proposal in db.get_open_proposal_messages():
        date = datetime.strptime(proposal["date"], "%Y-%m-%d").date()
        if date < today:
            db.set_proposal_status(proposal["date"], "passed")
            await _refresh_proposal_message(proposal["date"], bot, db)


# ---------------------------------------------------------------------------
# ProposalDayView components
# ---------------------------------------------------------------------------

class _ProposalSlotSelect(discord.ui.Select):
    def __init__(self, slot: int, date_str: str, matches: list[dict],
                 current_value: str = "none"):
        self.slot = slot
        self.date_str = date_str
        options = _build_match_options(matches)
        # Mark the current selection as default
        for opt in options:
            if opt.value == current_value:
                opt.default = True
                break
        super().__init__(
            placeholder=f"Slot {slot}: Select a match...",
            options=options,
            min_values=1,
            max_values=1,
            custom_id=f"proposal_slot{slot}_{date_str}",
            row=slot - 1,
        )

    async def callback(self, interaction: discord.Interaction):
        # Store selection in a bot-level cache so _UpdateScheduleButton can read it.
        # interaction.message.components only reflects default values, not in-session picks.
        cache = getattr(interaction.client, "_proposal_selections", {})
        cache[(self.date_str, self.slot)] = self.values[0]
        interaction.client._proposal_selections = cache
        await interaction.response.defer()


class _UpdateScheduleButton(discord.ui.Button):
    def __init__(self, date_str: str, row: int = 2):
        self.date_str = date_str
        super().__init__(
            label="Update Schedule",
            style=discord.ButtonStyle.success,
            custom_id=f"proposal_update_{date_str}",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        db = interaction.client.db
        if not interaction.guild:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return

        from cogs.signup import _manager_check
        if not _manager_check(interaction, db):
            await interaction.response.send_message(
                "Only managers and administrators can update the schedule.", ephemeral=True
            )
            return

        # Read selections from the bot-level cache (populated by _ProposalSlotSelect),
        # falling back to whatever is currently saved in the DB for unchanged slots.
        cache = getattr(interaction.client, "_proposal_selections", {})
        proposal_pre = db.get_proposal_message(self.date_str)
        slot1_default = str(proposal_pre.get("slot1_match_id") or "none") if proposal_pre else "none"
        slot2_default = str(proposal_pre.get("slot2_match_id") or "none") if proposal_pre else "none"
        slot1_val = cache.pop((self.date_str, 1), slot1_default)
        slot2_val = cache.pop((self.date_str, 2), slot2_default)

        slot1_id = None if slot1_val == "none" else int(slot1_val)
        slot2_id = None if slot2_val == "none" else int(slot2_val)

        if slot1_id and slot1_id == slot2_id:
            await interaction.response.send_message(
                "❌ You cannot assign the same match to both slots.", ephemeral=True
            )
            return

        # Validate ≥2h apart if both slots selected
        if slot1_id and slot2_id:
            m1 = db.get_match(slot1_id)
            m2 = db.get_match(slot2_id)
            if m1 and m2:
                gap_h = abs(m2["match_time"] - m1["match_time"]) / 3600.0
                if gap_h < PAIR_MIN_H:
                    await interaction.response.send_message(
                        f"❌ Matches must be at least {PAIR_MIN_H:.0f} hours apart "
                        f"(selected gap: {gap_h:.1f}h).",
                        ephemeral=True,
                    )
                    return

        await interaction.response.defer(ephemeral=True)

        proposal = db.get_proposal_message(self.date_str)
        old_slot_ids: list[int] = []
        affected_signups: list[str] = []
        if proposal:
            for slot_key in ("slot1_match_id", "slot2_match_id"):
                mid = proposal.get(slot_key)
                if mid:
                    old_slot_ids.append(mid)
                    for s in db.get_signups_for_match(mid):
                        if s["role"] == "unavailable":
                            continue
                        if s["user_id"] not in affected_signups:
                            affected_signups.append(s["user_id"])

        signup_ch_id = db.get_config("signup_channel_id") or db.get_config("broadcast_channel_id")
        signup_ch = interaction.client.get_channel(int(signup_ch_id)) if signup_ch_id else None
        teamup = interaction.client.get_teamup()

        new_slot_ids = {mid for mid in [slot1_id, slot2_id] if mid}
        to_remove = [mid for mid in old_slot_ids if mid not in new_slot_ids]
        to_add = [mid for mid in [slot1_id, slot2_id]
                  if mid and mid not in set(old_slot_ids)]

        from cogs.talent import carry_over_if_same_time

        # Pair each added match with a removed match at the same time (if any)
        # so state carries over instead of a fresh sign-up.
        carried_new_ids: set[int] = set()
        for new_mid in list(to_add):
            new_m = db.get_match(new_mid)
            if not new_m:
                continue
            # to_add can't hold two same-time matches (slot1 != slot2 + >= PAIR_MIN_H gap enforced above), so no double-pair
            twin = next(
                (rm for rm in to_remove
                 if (db.get_match(rm) or {}).get("match_time") == new_m["match_time"]),
                None,
            )
            if twin is not None:
                try:
                    await accept_combination(
                        [new_m], self.date_str, db, teamup, signup_ch,
                        talent_role_mention="")
                except Exception as e:
                    log.error("Update Schedule: accept_combination failed for %s: %s",
                              new_mid, e)
                    # carry aborted: twin still in to_remove (unscheduled below) and new_mid not in carried_new_ids, so it falls through to the fresh-post loop
                    continue
                await carry_over_if_same_time(interaction.client, db, twin, new_mid)
                carried_new_ids.add(new_mid)

        for mid in to_remove:
            await _unschedule_match(mid, db, teamup, signup_ch, bot=interaction.client)

        talent_role_id = db.get_config("talent_role_id")
        talent_role_mention = f"<@&{talent_role_id}>" if talent_role_id else ""
        for mid in to_add:
            if mid in carried_new_ids:
                continue
            m = db.get_match(mid)
            if not m:
                continue
            try:
                await accept_combination([m], self.date_str, db, teamup, signup_ch,
                                         talent_role_mention=talent_role_mention)
            except Exception as e:
                log.error("Update Schedule: accept_combination failed for %s: %s", mid, e)

        db.update_proposal_slots(self.date_str, slot1_id, slot2_id)

        update_msg = None
        if affected_signups and to_remove:
            update_msg = await _send_schedule_update_ping(
                self.date_str, affected_signups, interaction.client, db,
                "The broadcast schedule for this day has been changed.",
            )

        # When a sign-up was replaced (both removed AND added), append a link
        # to the schedule update message on each new sign-up post.
        if update_msg and to_add and signup_ch:
            await _link_schedule_update_to_signups(
                to_add, update_msg, signup_ch, db
            )

        all_matches = db.get_matches_for_date(self.date_str)
        new_view = ProposalDayView(self.date_str, all_matches,
                                   slot1_match_id=slot1_id, slot2_match_id=slot2_id)
        content = build_proposal_day_content(self.date_str, db)
        await interaction.message.edit(content=content, view=new_view)
        await interaction.followup.send("✅ Schedule updated.", ephemeral=True)


class _ClearSelectionsButton(discord.ui.Button):
    def __init__(self, date_str: str, row: int = 2):
        self.date_str = date_str
        super().__init__(
            label="Clear Selections",
            style=discord.ButtonStyle.secondary,
            custom_id=f"proposal_clear_{date_str}",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        db = interaction.client.db
        if not interaction.guild:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return

        from cogs.signup import _manager_check
        if not _manager_check(interaction, db):
            await interaction.response.send_message(
                "Only managers and administrators can clear the schedule.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        proposal = db.get_proposal_message(self.date_str)
        teamup = interaction.client.get_teamup()
        signup_ch_id = db.get_config("signup_channel_id") or db.get_config("broadcast_channel_id")
        signup_ch = interaction.client.get_channel(int(signup_ch_id)) if signup_ch_id else None

        old_slot_ids: list[int] = []
        affected_signups: list[str] = []
        if proposal:
            for slot_key in ("slot1_match_id", "slot2_match_id"):
                mid = proposal.get(slot_key)
                if mid:
                    old_slot_ids.append(mid)
                    for s in db.get_signups_for_match(mid):
                        if s["role"] == "unavailable":
                            continue
                        if s["user_id"] not in affected_signups:
                            affected_signups.append(s["user_id"])

        for mid in old_slot_ids:
            await _unschedule_match(mid, db, teamup, signup_ch, bot=interaction.client)

        # Remove block if day was blocked
        blocked = db.get_blocked_day(self.date_str)
        if blocked:
            block_event_id = blocked.get("teamup_event_id")
            if block_event_id and teamup:
                try:
                    teamup.delete_event(block_event_id)
                except Exception as e:
                    log.warning("Clear: failed to delete block event %s: %s", block_event_id, e)
            db.delete_blocked_day(self.date_str)

        db.update_proposal_slots(self.date_str, None, None)
        db.set_proposal_status(self.date_str, "open")

        if affected_signups:
            await _send_schedule_update_ping(
                self.date_str, affected_signups, interaction.client, db,
                "The broadcast schedule for this day has been cleared.",
            )

        all_matches = db.get_matches_for_date(self.date_str)
        new_view = ProposalDayView(self.date_str, all_matches)
        content = build_proposal_day_content(self.date_str, db)
        await interaction.message.edit(content=content, view=new_view)
        await interaction.followup.send("✅ Selections cleared.", ephemeral=True)


class _BlockDayButton(discord.ui.Button):
    def __init__(self, date_str: str, row: int = 2):
        self.date_str = date_str
        super().__init__(
            label="Block Day",
            style=discord.ButtonStyle.danger,
            custom_id=f"proposal_block_{date_str}",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        db = interaction.client.db
        if not interaction.guild:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return

        from cogs.signup import _manager_check
        if not _manager_check(interaction, db):
            await interaction.response.send_message(
                "Only managers and administrators can block a day.", ephemeral=True
            )
            return

        if db.get_blocked_day(self.date_str):
            await interaction.response.send_message(
                "This day is already blocked.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        teamup = interaction.client.get_teamup()
        signup_ch_id = db.get_config("signup_channel_id") or db.get_config("broadcast_channel_id")
        signup_ch = interaction.client.get_channel(int(signup_ch_id)) if signup_ch_id else None

        proposal = db.get_proposal_message(self.date_str)
        affected_signups: list[str] = []
        if proposal:
            for slot_key in ("slot1_match_id", "slot2_match_id"):
                mid = proposal.get(slot_key)
                if mid:
                    for s in db.get_signups_for_match(mid):
                        if s["role"] == "unavailable":
                            continue
                        if s["user_id"] not in affected_signups:
                            affected_signups.append(s["user_id"])

        # Unschedule all matches for the day
        all_matches = db.get_matches_for_date(self.date_str)
        from cogs.confirm_view import cancel_orphaned_confirmation
        for m in all_matches:
            eid = m.get("teamup_event_id")
            if eid and teamup:
                try:
                    teamup.delete_event(eid)
                except Exception as e:
                    log.warning("Block: failed to delete event %s: %s", eid, e)
            if eid:
                await cancel_orphaned_confirmation(
                    interaction.client, db, m["id"],
                    reason="this day was blocked",
                )
                db.update_match_teamup_id(m["id"], None)
                db.decrement_scheduled_count(m["team_home"])
                db.decrement_scheduled_count(m["team_away"])
                db.reset_allocation(m["id"])

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
                    log.warning("Block: failed to edit sign-up message for match %s: %s",
                                m["id"], e)

        # Create NO STREAM event in TeamUp (00:01–23:59 ET)
        block_event_id = None
        if teamup:
            try:
                day_dt = datetime.strptime(self.date_str, "%Y-%m-%d").replace(tzinfo=ET)
                block_start = int(day_dt.replace(hour=0, minute=1, second=0).timestamp())
                block_end = int(day_dt.replace(hour=23, minute=59, second=0).timestamp())
                block_event_id = teamup.create_event(
                    f"NO STREAM — {self.date_str}", block_start, block_end,
                )
            except Exception as e:
                log.warning("Block: failed to create NO STREAM event: %s", e)

        db.insert_blocked_day(self.date_str, reason="Blocked via proposal",
                              teamup_event_id=block_event_id)
        db.update_proposal_slots(self.date_str, None, None)
        db.set_proposal_status(self.date_str, "blocked")

        if affected_signups:
            await _send_schedule_update_ping(
                self.date_str, affected_signups, interaction.client, db,
                "This day has been blocked. No broadcast will be scheduled.",
                prefix="🚫 **Day Blocked**",
            )

        content = build_proposal_day_content(self.date_str, db)
        await interaction.message.edit(content=content, view=BlockedDayView(self.date_str))
        await interaction.followup.send("🚫 Day blocked.", ephemeral=True)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _unschedule_match(match_id: int, db, teamup, signup_ch, bot=None) -> None:
    """Delete a match's TeamUp event, reset DB state, and edit the sign-up message.

    When `bot` is provided, also clears any active talent confirmation message
    for this match (must run before reset_allocation wipes the message ID)."""
    m = db.get_match(match_id)
    if not m:
        return
    eid = m.get("teamup_event_id")
    if eid and teamup:
        try:
            teamup.delete_event(eid)
        except Exception as e:
            log.warning("Unschedule match %s: failed to delete event %s: %s", match_id, eid, e)
    if eid:
        if bot is not None:
            from cogs.confirm_view import cancel_orphaned_confirmation
            await cancel_orphaned_confirmation(bot, db, match_id)
        db.update_match_teamup_id(match_id, None)
        db.decrement_scheduled_count(m["team_home"])
        db.decrement_scheduled_count(m["team_away"])
        db.reset_allocation(match_id)

    bcast = db.get_broadcast_message(match_id)
    if bcast and signup_ch:
        try:
            msg = await signup_ch.fetch_message(int(bcast["discord_message_id"]))
            ts = m["match_time"]
            await msg.edit(
                content=(
                    f"{_SEPARATOR}\n"
                    f"📋 [{m['division']}] {m['team_home']} vs {m['team_away']}\n"
                    f"<t:{ts}:F>\n\n"
                    f"⏏️ This match has been removed from the broadcast schedule."
                ),
                view=discord.ui.View(),
            )
        except Exception as e:
            log.warning("Unschedule match %s: failed to edit sign-up message: %s", match_id, e)


async def _send_schedule_update_ping(
    date_str: str, user_ids: list[str], client, db,
    reason: str, prefix: str = "📋 **Schedule Update**"
) -> Optional[discord.Message]:
    """Send a mention ping to the schedule-updates channel.

    Returns the sent message (so callers can link to it), or None if the
    channel isn't configured/reachable or the send failed."""
    updates_ch_id = db.get_config("schedule_updates_channel_id")
    if not updates_ch_id:
        return None
    updates_ch = client.get_channel(int(updates_ch_id))
    if not updates_ch:
        return None
    mentions = " ".join(f"<@{uid}>" for uid in user_ids)
    try:
        return await updates_ch.send(
            f"{prefix} — {_fmt_date(date_str)}\n{reason}\n{mentions}"
        )
    except Exception as e:
        log.warning("Failed to send schedule-updates ping for %s: %s", date_str, e)
        return None


async def _link_schedule_update_to_signups(
    new_match_ids: list[int], update_msg: discord.Message,
    signup_ch, db,
) -> None:
    """Append a 'View schedule update' link to each new sign-up message."""
    link_line = f"\n\n🔗 [View schedule update]({update_msg.jump_url})"
    for mid in new_match_ids:
        bcast = db.get_broadcast_message(mid)
        if not bcast:
            continue
        try:
            msg = await signup_ch.fetch_message(int(bcast["discord_message_id"]))
            await msg.edit(content=msg.content + link_line)
        except Exception as e:
            log.warning("Failed to link schedule update on sign-up %s: %s", mid, e)


class _UnblockDayButton(discord.ui.Button):
    def __init__(self, date_str: str):
        self.date_str = date_str
        super().__init__(
            label="Unblock Day",
            style=discord.ButtonStyle.danger,
            custom_id=f"proposal_unblock_{date_str}",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        db = interaction.client.db
        if not interaction.guild:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return

        from cogs.signup import _manager_check
        if not _manager_check(interaction, db):
            await interaction.response.send_message(
                "Only managers and administrators can unblock a day.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        teamup = interaction.client.get_teamup()
        blocked = db.get_blocked_day(self.date_str)
        if blocked:
            block_event_id = blocked.get("teamup_event_id")
            if block_event_id and teamup:
                try:
                    teamup.delete_event(block_event_id)
                except Exception as e:
                    log.warning("Unblock: failed to delete NO STREAM event %s: %s",
                                block_event_id, e)
            db.delete_blocked_day(self.date_str)

        db.set_proposal_status(self.date_str, "open")

        all_matches = db.get_matches_for_date(self.date_str)
        proposal = db.get_proposal_message(self.date_str)
        slot1_id = proposal.get("slot1_match_id") if proposal else None
        slot2_id = proposal.get("slot2_match_id") if proposal else None
        new_view = ProposalDayView(self.date_str, all_matches,
                                   slot1_match_id=slot1_id, slot2_match_id=slot2_id)
        content = build_proposal_day_content(self.date_str, db)
        await interaction.message.edit(content=content, view=new_view)
        await interaction.followup.send("✅ Day unblocked.", ephemeral=True)


class BlockedDayView(discord.ui.View):
    """Persistent view shown when a proposal day is blocked. Contains only Unblock Day."""

    def __init__(self, date_str: str):
        super().__init__(timeout=None)
        self.add_item(_UnblockDayButton(date_str))


# ---------------------------------------------------------------------------
# ProposalDayView
# ---------------------------------------------------------------------------

class ProposalDayView(discord.ui.View):
    """Persistent view for a single day's broadcast schedule proposal.

    Custom IDs encode the date so callbacks know which day they belong to.
    """

    def __init__(self, date_str: str, matches: list[dict],
                 slot1_match_id: int = None, slot2_match_id: int = None):
        super().__init__(timeout=None)
        self.date_str = date_str

        slot1_val = str(slot1_match_id) if slot1_match_id else "none"
        slot2_val = str(slot2_match_id) if slot2_match_id else "none"

        has_matches = bool(matches)
        button_row = 2 if has_matches else 0

        if has_matches:
            self.add_item(_ProposalSlotSelect(1, date_str, matches,
                                              current_value=slot1_val))
            self.add_item(_ProposalSlotSelect(2, date_str, matches,
                                              current_value=slot2_val))

        self.add_item(_UpdateScheduleButton(date_str, row=button_row))
        self.add_item(_ClearSelectionsButton(date_str, row=button_row))
        self.add_item(_BlockDayButton(date_str, row=button_row))


# ---------------------------------------------------------------------------
# WeeklyProposalsCog — registers on_match_logged listener
# ---------------------------------------------------------------------------

class WeeklyProposalsCog(commands.Cog):
    def __init__(self, bot, db):
        self.bot = bot
        self.db = db

    @commands.Cog.listener()
    async def on_match_logged(self, date_str: str) -> None:
        """Update the proposal message for a date when a new match is logged."""
        await update_proposal_message_for_date(date_str, self.bot, self.db)
