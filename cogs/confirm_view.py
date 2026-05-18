import discord
import json
import logging

from scheduler import _SEPARATOR, _CALENDAR_LINK

log = logging.getLogger(__name__)

_DISPLAY_ORDER = [
    ("producer",  "Producer",      True),
    ("observer",  "Observer",      True),
    ("pbp_1",     "Play-by-Play",  True),
    ("colour_1",  "Colour Caster", True),
    ("host",      "Host",          False),
    ("analyst_1", "Analyst",       False),
]


async def cancel_orphaned_confirmation(bot, db, match_id: int,
                                        reason: str | None = None) -> None:
    """Edit any active talent confirmation message for a match to a cancelled
    state and remove its buttons.

    MUST be called BEFORE reset_allocation / delete_match_cascade so the
    confirmation_message_id is still readable. No-op when no confirmation
    message is associated with the match."""
    alloc = db.get_allocation(match_id)
    if not alloc:
        return
    msg_id = alloc.get("confirmation_message_id")
    ch_id = alloc.get("confirmation_channel_id")
    if not msg_id or not ch_id:
        return
    channel = bot.get_channel(int(ch_id))
    if not channel:
        return
    note = reason or "this broadcast was removed from the schedule"
    try:
        msg = await channel.fetch_message(int(msg_id))
        await msg.edit(
            content=msg.content + f"\n\n⏏️ **Talent confirmation cancelled** — {note}.",
            view=discord.ui.View(),
        )
    except Exception as e:
        log.warning("cancel_orphaned_confirmation: failed for match %s: %s", match_id, e)


def build_confirmation_message(match: dict, role_assignments: dict,
                                confirmations: dict) -> str:
    """Confirmation message with per-user status ([Ready] / [Rejected] / [No Response])."""
    ts = match["match_time"]
    lines = [
        _SEPARATOR,
        "📣 **Broadcast Talent Confirmation**",
        f"**[{match['division']}] {match['team_home']} vs {match['team_away']}** | <t:{ts}:F>",
        "",
        "Please confirm your role for this broadcast:",
        "",
    ]

    def _tag(uid):
        status = confirmations.get(uid)
        if status is True:
            return "[Ready]"
        if status is False:
            return "[Rejected]"
        return "[No Response]"

    for key, label, required in _DISPLAY_ORDER:
        assignment = role_assignments.get(key)
        if not assignment:
            continue
        uid = assignment["user_id"]
        name = assignment["display_name"]
        if required:
            lines.append(f"**{label}:** <@{uid}> — {name} {_tag(uid)}")
        else:
            lines.append(f"**{label}** (optional): <@{uid}> — {name} {_tag(uid)}")

    # Awaiting mentions: any assigned user (required or optional) still pending
    awaiting = []
    seen_awaiting: set[str] = set()
    for key, _label, _required in _DISPLAY_ORDER:
        a = role_assignments.get(key)
        if a:
            uid = a["user_id"]
            if confirmations.get(uid) is None and uid not in seen_awaiting:
                seen_awaiting.add(uid)
                awaiting.append(uid)

    if awaiting:
        mentions = " ".join(f"<@{uid}>" for uid in awaiting)
        lines += ["", f"⏳ **Awaiting confirmation from:** {mentions}"]

    lines += ["", _CALENDAR_LINK]
    return "\n".join(lines)


async def _finalize_match(match: dict, alloc: dict, role_assignments: dict, bot) -> None:
    """Move match to Accepted Calendar and credit talent broadcast counts."""
    from scheduler import match_end_ts
    from cogs.talent import build_talent_description_from_assignments

    db = bot.db
    teamup = bot.get_teamup()

    description = build_talent_description_from_assignments(role_assignments)
    title = (
        f"[{match['division']}] {match['team_home']} vs {match['team_away']}"
        f" {{{match['id']}}}"
    )
    end_ts = match_end_ts(match["match_time"])

    log_ch_id = db.get_config("log_channel_id")
    log_ch = bot.get_channel(int(log_ch_id)) if log_ch_id else None

    teamup_ok = False
    if teamup and match.get("teamup_event_id"):
        try:
            teamup.update_event(
                match["teamup_event_id"], title,
                match["match_time"], end_ts,
                subcalendar="accepted", description=description,
            )
            teamup_ok = True
        except Exception as e:
            log.error("TeamUp update failed for match %s: %s", match["id"], e)
            if log_ch:
                await log_ch.send(
                    f"⚠️ **TeamUp calendar update failed** for "
                    f"**[{match['division']}] {match['team_home']} vs {match['team_away']}**.\n"
                    f"Error: `{e}`\n"
                    f"Please update the TeamUp event manually."
                )
    elif teamup and not match.get("teamup_event_id"):
        if log_ch:
            await log_ch.send(
                f"⚠️ **No TeamUp event ID** for "
                f"**[{match['division']}] {match['team_home']} vs {match['team_away']}** "
                f"— could not move to Accepted sub-calendar."
            )

    db.mark_broadcast_accepted(match["id"])
    db.set_allocation_status(match["id"], "accepted")

    seen: set[str] = set()
    for assignment in role_assignments.values():
        if not isinstance(assignment, dict):
            continue
        uid = assignment["user_id"]
        if uid not in seen:
            seen.add(uid)
            db.increment_talent_broadcast(
                uid, assignment["username"], assignment["display_name"]
            )

    # Edit the sign-up message to show APPROVED status with allocated roster
    from scheduler import build_approved_signup_message
    from cogs.signup import ApprovedSignUpView
    signup_ch_id = db.get_config("signup_channel_id") or db.get_config("broadcast_channel_id")
    signup_ch = bot.get_channel(int(signup_ch_id)) if signup_ch_id else None
    bcast = db.get_broadcast_message(match["id"])
    if bcast and signup_ch:
        try:
            signup_msg = await signup_ch.fetch_message(int(bcast["discord_message_id"]))
            approved_content = build_approved_signup_message(match, role_assignments)
            await signup_msg.edit(
                content=approved_content,
                view=ApprovedSignUpView(match["id"]),
            )
        except Exception as e:
            log.error("Failed to edit sign-up message to APPROVED for match %s: %s",
                      match["id"], e)

    if log_ch:
        note = "Moved to Accepted Calendar." if teamup_ok else "⚠️ TeamUp not updated — see above."
        await log_ch.send(
            f"✅ **[{match['division']}] {match['team_home']} vs "
            f"{match['team_away']}** — all talent confirmed. {note}"
        )

    from cogs.threads import create_match_thread
    try:
        await create_match_thread(bot, match, role_assignments)
    except Exception as e:
        log.error("Thread creation failed for match %s: %s", match["id"], e)
        if log_ch:
            await log_ch.send(
                f"⚠️ **Thread creation failed** for "
                f"**[{match['division']}] {match['team_home']} vs {match['team_away']}**.\n"
                f"Use **Create Thread** on the sign-up message to retry."
            )


async def _stale_message_cleanup(interaction: discord.Interaction) -> None:
    """Edit a stale confirmation message in place (no buttons) so the next
    talent member who looks at it sees that it's been cancelled instead of
    clicking and getting a 'no longer active' ephemeral."""
    suffix = "\n\n⏏️ **Talent confirmation cancelled** — this broadcast is no longer scheduled."
    if suffix.strip() in (interaction.message.content or ""):
        return
    try:
        await interaction.message.edit(
            content=(interaction.message.content or "") + suffix,
            view=discord.ui.View(),
        )
    except Exception as e:
        log.warning("Failed to clean up stale confirmation message: %s", e)


class ReadyButton(discord.ui.Button):
    def __init__(self, match_id: int):
        self.match_id = match_id
        super().__init__(
            label="Ready",
            style=discord.ButtonStyle.success,
            custom_id=f"confirm_ready_{match_id}",
            emoji="✅",
        )

    async def callback(self, interaction: discord.Interaction):
        db = interaction.client.db
        alloc = db.get_allocation_by_confirmation_message(str(interaction.message.id))
        if not alloc or alloc["status"] != "awaiting_confirm":
            await interaction.response.send_message(
                "This confirmation is no longer active.", ephemeral=True
            )
            await _stale_message_cleanup(interaction)
            return

        user_id = str(interaction.user.id)
        confirmations = db.get_confirmations(alloc["match_id"])
        if user_id not in confirmations:
            await interaction.response.send_message(
                "You are not required to confirm for this broadcast.", ephemeral=True
            )
            return

        db.set_confirmation(alloc["match_id"], user_id, True)
        fresh_conf = db.get_confirmations(alloc["match_id"])
        match = db.get_match(alloc["match_id"])
        role_assignments = json.loads(alloc.get("role_assignments") or "{}")

        new_content = build_confirmation_message(match, role_assignments, fresh_conf)

        from cogs.talent import _get_required_user_ids
        required_ids = _get_required_user_ids(role_assignments)
        if required_ids and all(fresh_conf.get(uid) is True for uid in required_ids):
            await interaction.response.edit_message(
                content=new_content + "\n\n✅ **All talent confirmed — moved to Accepted Calendar!**",
                view=discord.ui.View(),
            )
            await _finalize_match(match, alloc, role_assignments, interaction.client)
        else:
            await interaction.response.edit_message(content=new_content, view=self.view)


class RejectButton(discord.ui.Button):
    def __init__(self, match_id: int):
        self.match_id = match_id
        super().__init__(
            label="Reject",
            style=discord.ButtonStyle.danger,
            custom_id=f"confirm_reject_{match_id}",
            emoji="❌",
        )

    async def callback(self, interaction: discord.Interaction):
        db = interaction.client.db
        alloc = db.get_allocation_by_confirmation_message(str(interaction.message.id))
        if not alloc or alloc["status"] != "awaiting_confirm":
            await interaction.response.send_message(
                "This confirmation is no longer active.", ephemeral=True
            )
            await _stale_message_cleanup(interaction)
            return

        user_id = str(interaction.user.id)
        confirmations = db.get_confirmations(alloc["match_id"])
        if user_id not in confirmations:
            await interaction.response.send_message(
                "You are not required to confirm for this broadcast.", ephemeral=True
            )
            return

        db.set_confirmation(alloc["match_id"], user_id, False)

        match_id = alloc["match_id"]
        match = db.get_match(match_id)
        role_assignments = json.loads(alloc.get("role_assignments") or "{}")
        decliner = next(
            (a for a in role_assignments.values()
             if isinstance(a, dict) and a.get("user_id") == user_id),
            None,
        )
        name = decliner["display_name"] if decliner else f"<@{user_id}>"

        # Build the rejected-state message BEFORE resetting allocation so the
        # decliner shows [Rejected] in the final rendered text.
        fresh_conf = db.get_confirmations(match_id)
        rejected_content = build_confirmation_message(match, role_assignments, fresh_conf)

        # Mark the decliner as unavailable on the sign-up so they're excluded
        # from the re-opened allocation dropdowns.
        if decliner:
            bcast = db.get_broadcast_message(match_id)
            signup_message_id = str(bcast["discord_message_id"]) if bcast else ""
            db.remove_all_signups_for_user(match_id, user_id)
            db.upsert_signup(
                match_id=match_id,
                message_id=signup_message_id,
                role="unavailable",
                user_id=user_id,
                username=decliner["username"],
                display_name=decliner["display_name"],
            )
            db.increment_talent_unavailable(
                user_id, decliner["username"], decliner["display_name"]
            )

        db.reset_allocation(match_id)

        await interaction.response.edit_message(
            content=rejected_content
                    + f"\n\n❌ **{name}** rejected — re-opening allocation.",
            view=discord.ui.View(),
        )

        log_ch_id = db.get_config("log_channel_id")
        broadcast_ch_id = db.get_config("broadcast_channel_id")
        log_ch = interaction.client.get_channel(int(log_ch_id)) if log_ch_id else None
        broadcast_ch = interaction.client.get_channel(int(broadcast_ch_id)) if broadcast_ch_id else None

        if log_ch:
            await log_ch.send(
                f"❌ **{name}** declined for "
                f"**[{match['division']}] {match['team_home']} vs {match['team_away']}**. "
                f"Re-opening talent allocation."
            )

        from cogs.talent import send_allocation_request
        await send_allocation_request(
            db, match, log_ch, broadcast_ch,
            get_teamup=interaction.client.get_teamup,
        )


class ConfirmationView(discord.ui.View):
    def __init__(self, match_id: int):
        super().__init__(timeout=None)
        self.add_item(ReadyButton(match_id))
        self.add_item(RejectButton(match_id))
