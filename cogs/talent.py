import discord
import logging

from database import Database
from scheduler import (
    ROLE_EMOJIS, ROLE_LABELS, REQUIRED_ROLES, _SEPARATOR,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------

def build_talent_description_from_assignments(role_assignments: dict) -> str:
    """Plain-text talent roster for the TeamUp event notes field."""
    display_order = [
        ("producer",  "Producer"),
        ("observer",  "Observer"),
        ("pbp_1",     "Play-by-Play"),
        ("colour_1",  "Colour Caster"),
        ("host",      "Host"),
        ("analyst_1", "Analyst"),
    ]
    parts = []
    for key, label in display_order:
        assignment = role_assignments.get(key)
        if not assignment:
            continue
        parts.append(f"{label}: {assignment['display_name']} ({assignment['username']})")
    return "\n".join(parts)


def _get_required_user_ids(role_assignments: dict) -> set[str]:
    """User IDs from required roles only (the ones who must confirm)."""
    ids: set[str] = set()
    for role_key in ("producer", "observer", "pbp_1", "colour_1"):
        assignment = role_assignments.get(role_key)
        if assignment:
            ids.add(assignment["user_id"])
    return ids


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_all_signup_options(signups: list[dict], db: Database,
                               include_none: bool = False) -> list[discord.SelectOption]:
    """Build select options from all signups, sorted by sign-up time."""
    options = []
    if include_none:
        options.append(discord.SelectOption(label="None", value="__none__", default=True))
    seen = set()
    for s in sorted(signups, key=lambda x: x["signed_up_at"]):
        if s["user_id"] in seen:
            continue
        seen.add(s["user_id"])
        count = db.get_talent_count(s["user_id"])
        role_label = ROLE_LABELS.get(s["role"], s["role"])
        label = f"{s['display_name']} [{count} bcast{'s' if count != 1 else ''}] ({role_label})"
        options.append(discord.SelectOption(
            label=label[:100],
            value=s["user_id"],
            description=s["username"][:100],
        ))
    return options


# ---------------------------------------------------------------------------
# Phase 1 components — required roles
# ---------------------------------------------------------------------------

class _RoleSelect(discord.ui.Select):
    """Single-select for a required role, showing ALL sign-ups."""
    def __init__(self, role_key: str, role_label: str, match_id: int,
                 all_signups: list[dict], db: Database, row: int):
        self._role_key = role_key
        options = _build_all_signup_options(all_signups, db)
        if not options:
            options = [discord.SelectOption(label="No sign-ups", value="__none__")]

        super().__init__(
            custom_id=f"alloc_{role_key}_{match_id}",
            placeholder=f"Select {role_label}...",
            options=options,
            min_values=0,
            max_values=1,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values and self.values[0] != "__none__":
            self.view.selections[self._role_key] = self.values[0]
        elif self._role_key in self.view.selections:
            del self.view.selections[self._role_key]
        await interaction.response.defer()


class _ContinueButton(discord.ui.Button):
    """Validates required role selections and advances to Phase 2."""
    def __init__(self, match_id: int):
        super().__init__(
            label="Continue →",
            style=discord.ButtonStyle.primary,
            custom_id=f"alloc_continue_{match_id}",
            row=4,
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        is_admin = interaction.user.guild_permissions.administrator
        is_mgr = self.view.db.is_manager(str(interaction.user.id))
        if not is_mgr:
            role_id = self.view.db.get_config("manager_role_id")
            if role_id:
                is_mgr = any(str(r.id) == role_id for r in interaction.user.roles)
        if not is_admin and not is_mgr:
            await interaction.response.send_message(
                "Only managers and administrators can confirm allocations.", ephemeral=True
            )
            return

        sel = self.view.selections
        missing = [label for key, label in [
            ("producer", "Producer"), ("observer", "Observer"),
            ("pbp", "Play-by-Play"), ("colour", "Colour Caster"),
        ] if key not in sel]
        if missing:
            await interaction.response.send_message(
                f"❌ Please select: {', '.join(missing)}", ephemeral=True
            )
            return

        if sel["pbp"] == sel["colour"]:
            await interaction.response.send_message(
                "❌ The same person cannot be both Play-by-Play and Colour Caster.",
                ephemeral=True,
            )
            return

        casters = {sel["pbp"], sel["colour"]}
        if sel.get("producer") in casters:
            await interaction.response.send_message(
                "❌ The Producer cannot also be a Play-by-Play or Colour Caster.",
                ephemeral=True,
            )
            return
        if sel.get("observer") in casters:
            await interaction.response.send_message(
                "❌ The Observer cannot also be a Play-by-Play or Colour Caster.",
                ephemeral=True,
            )
            return

        phase2 = AllocationConfirmView(
            self.view.match, self.view.signups, self.view.db,
            self.view.broadcast_channel, self.view.log_channel, self.view.get_teamup,
            required_selections=dict(sel),
        )
        await interaction.response.edit_message(view=phase2)


# ---------------------------------------------------------------------------
# Phase 2 components — optional roles + confirm
# ---------------------------------------------------------------------------

class _OptionalRoleSelect(discord.ui.Select):
    """Single-select for an optional role (Host/Analyst), with None as default."""
    def __init__(self, role_key: str, role_label: str, match_id: int,
                 all_signups: list[dict], db: Database, row: int):
        self._role_key = role_key
        options = _build_all_signup_options(all_signups, db, include_none=True)
        if len(options) == 1:  # only None option
            options.append(discord.SelectOption(label="No sign-ups", value="__none__"))

        super().__init__(
            custom_id=f"alloc_{role_key}_{match_id}",
            placeholder=f"Select {role_label} (optional)...",
            options=options,
            min_values=0,
            max_values=1,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values and self.values[0] != "__none__":
            self.view.optional_selections[self._role_key] = self.values[0]
        elif self._role_key in self.view.optional_selections:
            del self.view.optional_selections[self._role_key]
        await interaction.response.defer()


class _ConfirmButton(discord.ui.Button):
    def __init__(self, match_id: int):
        super().__init__(
            label="Confirm Allocation",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=f"alloc_confirm_{match_id}",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        is_admin = interaction.user.guild_permissions.administrator
        is_mgr = self.view.db.is_manager(str(interaction.user.id))
        if not is_mgr:
            role_id = self.view.db.get_config("manager_role_id")
            if role_id:
                is_mgr = any(str(r.id) == role_id for r in interaction.user.roles)
        if not is_admin and not is_mgr:
            await interaction.response.send_message(
                "Only managers and administrators can confirm allocations.", ephemeral=True
            )
            return

        view = self.view

        fresh_match = view.db.get_match(view.match["id"])
        if not fresh_match or not fresh_match.get("teamup_event_id"):
            await interaction.response.send_message(
                "❌ This match is no longer on the broadcast schedule — "
                "the allocation has been cancelled.",
                ephemeral=True,
            )
            return

        all_sigs = [s for s in view.db.get_signups_for_match(view.match["id"])
                    if s["role"] != "unavailable"]
        signups_by_id = {s["user_id"]: s for s in all_sigs}
        for uid, s in view.signups_by_id.items():
            if uid not in signups_by_id:
                signups_by_id[uid] = s

        role_assignments: dict = {}

        key_map = {"producer": "producer", "observer": "observer",
                   "pbp": "pbp_1", "colour": "colour_1"}
        for sel_key, db_key in key_map.items():
            uid = view.required_selections[sel_key]
            s = signups_by_id.get(uid)
            if s:
                role_assignments[db_key] = {
                    "user_id":      s["user_id"],
                    "username":     s["username"],
                    "display_name": s["display_name"],
                }

        for role_key, db_key in [("host", "host"), ("analyst", "analyst_1")]:
            uid = view.optional_selections.get(role_key)
            if uid:
                s = signups_by_id.get(uid)
                if s:
                    role_assignments[db_key] = {
                        "user_id":      s["user_id"],
                        "username":     s["username"],
                        "display_name": s["display_name"],
                    }

        required_ids = _get_required_user_ids(role_assignments)
        confirmations = {uid: None for uid in required_ids}

        from cogs.confirm_view import build_confirmation_message, ConfirmationView
        conf_msg = None
        if view.broadcast_channel:
            conf_text = build_confirmation_message(view.match, role_assignments, confirmations)
            try:
                conf_msg = await view.broadcast_channel.send(
                    conf_text, view=ConfirmationView(view.match["id"])
                )
            except Exception as e:
                log.error("Failed to send confirmation message for match %s: %s",
                          view.match["id"], e)

        view.db.set_allocation_assignments(
            view.match["id"],
            role_assignments=role_assignments,
            confirmations=confirmations,
            confirmation_message_id=str(conf_msg.id) if conf_msg else None,
            confirmation_channel_id=str(conf_msg.channel.id) if conf_msg else None,
        )

        view.stop()
        for child in view.children:
            child.disabled = True

        optional_parts = []
        if "host" in role_assignments:
            optional_parts.append(f"Host: {role_assignments['host']['display_name']}")
        if "analyst_1" in role_assignments:
            optional_parts.append(f"Analyst: {role_assignments['analyst_1']['display_name']}")
        summary = (" " + ", ".join(optional_parts) + ".") if optional_parts else " No optional roles."

        await interaction.response.edit_message(
            content=(
                interaction.message.content
                + f"\n\n✅ **Allocation confirmed — talent notified in broadcast channel.**{summary}"
            ),
            view=view,
        )


# ---------------------------------------------------------------------------
# Shared cancel logic
# ---------------------------------------------------------------------------

async def _cancel_broadcast(interaction: discord.Interaction, view) -> None:
    """Shared cancel logic callable from any allocation view phase."""
    match = view.match
    teamup = view.get_teamup()

    event_id = match.get("teamup_event_id")
    if teamup and event_id:
        try:
            teamup.delete_event(event_id)
        except Exception as e:
            log.warning("Failed to delete TeamUp event %s during cancel: %s", event_id, e)
        view.db.update_match_teamup_id(match["id"], None)
        view.db.decrement_scheduled_count(match["team_home"])
        view.db.decrement_scheduled_count(match["team_away"])

    view.db.reset_allocation(match["id"])

    signups = view.db.get_signups_for_match(match["id"])
    all_user_ids = list({s["user_id"] for s in signups
                         if s["role"] != "unavailable"})
    mentions = " ".join(f"<@{uid}>" for uid in all_user_ids)
    ts = match["match_time"]
    cancel_text = (
        f"{_SEPARATOR}\n"
        f"🚫 **Broadcast Cancelled**\n"
        f"**[{match['division']}] {match['team_home']} vs {match['team_away']}** | <t:{ts}:F>\n\n"
        f"This broadcast has been cancelled by management.\n"
    )
    if mentions:
        cancel_text += f"\n{mentions}"

    updates_ch_id = view.db.get_config("schedule_updates_channel_id")
    updates_ch = (interaction.client.get_channel(int(updates_ch_id))
                  if updates_ch_id else None)
    notify_ch = updates_ch or view.broadcast_channel
    if notify_ch:
        try:
            await notify_ch.send(cancel_text)
        except Exception as e:
            log.error("Failed to send cancellation for match %s: %s", match["id"], e)

    signup_ch_id = (view.db.get_config("signup_channel_id")
                    or view.db.get_config("broadcast_channel_id"))
    signup_ch = interaction.client.get_channel(int(signup_ch_id)) if signup_ch_id else None
    bcast = view.db.get_broadcast_message(match["id"])
    if bcast and signup_ch:
        try:
            signup_msg = await signup_ch.fetch_message(int(bcast["discord_message_id"]))
            await signup_msg.edit(
                content=(
                    f"{_SEPARATOR}\n"
                    f"❌ **BROADCAST CANCELLED**\n"
                    f"📋 [{match['division']}] {match['team_home']} vs {match['team_away']}\n"
                    f"<t:{ts}:F>\n\n"
                    f"This broadcast has been cancelled by management."
                ),
                view=discord.ui.View(),
            )
        except Exception as e:
            log.error("Failed to edit sign-up message for cancelled match %s: %s",
                      match["id"], e)

    view.stop()
    for child in view.children:
        child.disabled = True
    await interaction.response.edit_message(
        content=interaction.message.content + "\n\n❌ **Broadcast cancelled.**",
        view=view,
    )


class _CancelButton(discord.ui.Button):
    def __init__(self, match_id: int, row: int = 4):
        super().__init__(
            label="Cancel Broadcast",
            emoji="❌",
            style=discord.ButtonStyle.danger,
            custom_id=f"alloc_cancel_{match_id}",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        is_admin = interaction.user.guild_permissions.administrator
        is_mgr = self.view.db.is_manager(str(interaction.user.id))
        if not is_mgr:
            role_id = self.view.db.get_config("manager_role_id")
            if role_id:
                is_mgr = any(str(r.id) == role_id for r in interaction.user.roles)
        if not is_admin and not is_mgr:
            await interaction.response.send_message(
                "Only managers and administrators can cancel broadcasts.", ephemeral=True
            )
            return
        await self.view.cancel_broadcast(interaction)


# ---------------------------------------------------------------------------
# View classes
# ---------------------------------------------------------------------------

class AllocationView(discord.ui.View):
    """Phase 1 — select the four required roles, then click Continue."""

    def __init__(self, match: dict, signups: list[dict], db: Database,
                 broadcast_channel, log_channel, get_teamup):
        super().__init__(timeout=86400)
        self.match = match
        self.signups = signups
        self.db = db
        self.broadcast_channel = broadcast_channel
        self.log_channel = log_channel
        self.get_teamup = get_teamup
        self.selections: dict[str, str] = {}
        self.signups_by_id = {s["user_id"]: s for s in signups}

        self.add_item(_RoleSelect("producer",  "Producer",      match["id"], signups, db, row=0))
        self.add_item(_RoleSelect("observer",  "Observer",      match["id"], signups, db, row=1))
        self.add_item(_RoleSelect("pbp",       "Play-by-Play",  match["id"], signups, db, row=2))
        self.add_item(_RoleSelect("colour",    "Colour Caster", match["id"], signups, db, row=3))
        self.add_item(_ContinueButton(match["id"]))
        self.add_item(_CancelButton(match["id"], row=4))

    async def cancel_broadcast(self, interaction: discord.Interaction):
        await _cancel_broadcast(interaction, self)


class AllocationConfirmView(discord.ui.View):
    """Phase 2 — select optional roles, then confirm or cancel."""

    def __init__(self, match: dict, signups: list[dict], db: Database,
                 broadcast_channel, log_channel, get_teamup,
                 required_selections: dict):
        super().__init__(timeout=86400)
        self.match = match
        self.signups = signups
        self.db = db
        self.broadcast_channel = broadcast_channel
        self.log_channel = log_channel
        self.get_teamup = get_teamup
        self.required_selections = required_selections
        self.optional_selections: dict[str, str] = {}
        self.signups_by_id = {s["user_id"]: s for s in signups}

        self.add_item(_OptionalRoleSelect("host",    "Host",    match["id"], signups, db, row=0))
        self.add_item(_OptionalRoleSelect("analyst", "Analyst", match["id"], signups, db, row=1))
        self.add_item(_ConfirmButton(match["id"]))
        self.add_item(_CancelButton(match["id"], row=2))

    async def cancel_broadcast(self, interaction: discord.Interaction):
        await _cancel_broadcast(interaction, self)


# ---------------------------------------------------------------------------
# Top-level helper called by events.py and bot.py
# ---------------------------------------------------------------------------

async def send_allocation_request(db: Database, match: dict,
                                   log_channel, broadcast_channel,
                                   get_teamup=None) -> None:
    """Send the two-phase talent allocation UI to the log channel."""
    if not log_channel:
        return

    db.create_allocation(match["id"])
    db.set_allocation_status(match["id"], "sent")

    signups = [s for s in db.get_signups_for_match(match["id"])
               if s["role"] != "unavailable"]

    ts = match["match_time"]
    lines = [
        _SEPARATOR,
        "🎙️ **Talent Allocation Required**",
        f"**[{match['division']}] {match['team_home']} vs {match['team_away']}** — <t:{ts}:F>",
        "",
        "**Sign-ups received:**",
    ]

    by_role: dict[str, list] = {r: [] for r in ROLE_EMOJIS}
    for s in sorted(signups, key=lambda x: x["signed_up_at"]):
        if s["role"] in by_role:
            by_role[s["role"]].append(s)

    for role_key in ["producer", "observer", "pbp", "colour", "host", "analyst"]:
        label = ROLE_LABELS[role_key]
        required = role_key in REQUIRED_ROLES
        opt = "" if required else " *(optional)*"
        people = by_role.get(role_key, [])
        if people:
            names = ", ".join(
                f"{s['display_name']} [{db.get_talent_count(s['user_id'])}]"
                for s in people
            )
        else:
            names = "—"
        lines.append(f"**{label}**{opt}: {names}")

    lines += [
        "",
        "Select required roles below, then click **Continue** to assign optional roles.",
        "Use **Force Schedule** on the sign-up post to re-trigger this if needed.",
    ]
    text = "\n".join(lines)

    view = AllocationView(match, signups, db, broadcast_channel, log_channel, get_teamup)
    try:
        msg = await log_channel.send(text, view=view)
        db.set_allocation_message(match["id"], str(msg.id), str(log_channel.id))
    except Exception as e:
        log.error("Failed to send allocation request for match %s: %s", match["id"], e)
