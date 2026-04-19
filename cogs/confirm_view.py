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

    for key, label, required in _DISPLAY_ORDER:
        assignment = role_assignments.get(key)
        if not assignment:
            continue
        uid = assignment["user_id"]
        name = assignment["display_name"]
        if required:
            status = confirmations.get(uid)
            if status is True:
                tag = "[Ready]"
            elif status is False:
                tag = "[Rejected]"
            else:
                tag = "[No Response]"
            lines.append(f"**{label}:** <@{uid}> — {name} {tag}")
        else:
            lines.append(f"**{label}** (optional): <@{uid}> — {name}")

    # Awaiting mentions (only required users who haven't responded)
    awaiting = []
    seen_awaiting: set[str] = set()
    for key, _label, required in _DISPLAY_ORDER:
        if not required:
            continue
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

        if all(v is True for v in fresh_conf.values()):
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
            return

        user_id = str(interaction.user.id)
        confirmations = db.get_confirmations(alloc["match_id"])
        if user_id not in confirmations:
            await interaction.response.send_message(
                "You are not required to confirm for this broadcast.", ephemeral=True
            )
            return

        db.set_confirmation(alloc["match_id"], user_id, False)
        db.reset_allocation(alloc["match_id"])

        match = db.get_match(alloc["match_id"])
        role_assignments = json.loads(alloc.get("role_assignments") or "{}")
        decliner = next(
            (a for a in role_assignments.values()
             if isinstance(a, dict) and a.get("user_id") == user_id),
            None,
        )
        name = decliner["display_name"] if decliner else f"<@{user_id}>"

        await interaction.response.edit_message(
            content=interaction.message.content
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
