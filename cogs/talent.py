import discord
import json
import logging

from database import Database
from scheduler import (
    ROLE_EMOJIS, ROLE_LABELS, ROLE_LIMITS, REQUIRED_ROLES, _SEPARATOR,
)

log = logging.getLogger(__name__)

# Required roles in selection order
ROLE_ORDER = ["producer", "observer", "pbp", "colour"]


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------

def build_talent_description_from_assignments(role_assignments: dict) -> str:
    """Plain-text talent roster for the TeamUp event notes field."""
    display_order = [
        ("producer",  "Producer"),
        ("observer",  "Observer"),
        ("pbp",       "Play-by-Play"),
        ("colour",    "Colour Caster"),
        ("host",      "Host"),
        ("analyst_1", "Analyst"),
        ("analyst_2", "Analyst"),
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
    for role_key in ROLE_ORDER:
        assignment = role_assignments.get(role_key)
        if assignment:
            ids.add(assignment["user_id"])
    return ids


# ---------------------------------------------------------------------------
# Single-step allocation UI
# ---------------------------------------------------------------------------

class _RoleSelect(discord.ui.Select):
    """A select for a single required role (Producer or Observer)."""
    def __init__(self, role_key: str, role_label: str, match_id: int,
                 signups: list[dict], db: Database, row: int):
        self._role_key = role_key
        options: list[discord.SelectOption] = []
        for s in signups:
            count = db.get_talent_count(s["user_id"])
            label = f"{s['display_name']} [{count} bcast{'s' if count != 1 else ''}]"
            options.append(discord.SelectOption(
                label=label[:100],
                value=s["user_id"],
                description=s["username"][:100],
            ))
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


class _CasterSelect(discord.ui.Select):
    """Combined Play-by-Play + Colour Caster select (encoded values 'pbp:uid', 'colour:uid')."""
    def __init__(self, match_id: int, pbp_signups: list[dict],
                 colour_signups: list[dict], db: Database):
        options: list[discord.SelectOption] = []
        for s in pbp_signups:
            count = db.get_talent_count(s["user_id"])
            options.append(discord.SelectOption(
                label=f"PBP: {s['display_name']} [{count}]"[:100],
                value=f"pbp:{s['user_id']}",
                description=s["username"][:100],
            ))
        for s in colour_signups:
            count = db.get_talent_count(s["user_id"])
            options.append(discord.SelectOption(
                label=f"Colour: {s['display_name']} [{count}]"[:100],
                value=f"colour:{s['user_id']}",
                description=s["username"][:100],
            ))
        if not options:
            options = [discord.SelectOption(label="No sign-ups", value="__none__")]

        super().__init__(
            custom_id=f"alloc_caster_{match_id}",
            placeholder="Select Play-by-Play and Colour Caster...",
            options=options,
            min_values=0,
            max_values=min(2, len(options)),
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        self.view.caster_selections = [v for v in self.values if v != "__none__"]
        await interaction.response.defer()


class _OptionalSelect(discord.ui.Select):
    """Combined Host + Analyst select (encoded values 'host:uid', 'analyst:uid')."""
    def __init__(self, match_id: int, host_signups: list[dict],
                 analyst_signups: list[dict], db: Database):
        options: list[discord.SelectOption] = []
        for s in host_signups:
            count = db.get_talent_count(s["user_id"])
            options.append(discord.SelectOption(
                label=f"Host: {s['display_name']} [{count}]"[:100],
                value=f"host:{s['user_id']}",
                description=s["username"][:100],
            ))
        for s in analyst_signups:
            count = db.get_talent_count(s["user_id"])
            options.append(discord.SelectOption(
                label=f"Analyst: {s['display_name']} [{count}]"[:100],
                value=f"analyst:{s['user_id']}",
                description=s["username"][:100],
            ))
        if not options:
            options = [discord.SelectOption(label="None (no optional roles)", value="__none__")]

        super().__init__(
            custom_id=f"alloc_optional_{match_id}",
            placeholder="Host / Analyst (optional)...",
            options=options,
            min_values=0,
            max_values=min(3, len(options)),
            row=3,
        )

    async def callback(self, interaction: discord.Interaction):
        self.view.optional_selections = [v for v in self.values if v != "__none__"]
        await interaction.response.defer()


class _ConfirmButton(discord.ui.Button):
    def __init__(self, match_id: int):
        super().__init__(
            label="Confirm Allocation",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=f"alloc_confirm_{match_id}",
            row=4,
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        is_admin = interaction.user.guild_permissions.administrator
        is_mgr = self.view.db.is_manager(str(interaction.user.id))
        if not is_admin and not is_mgr:
            await interaction.response.send_message(
                "Only managers and administrators can confirm allocations.", ephemeral=True
            )
            return

        view = self.view

        # Guard: if the match was removed from the calendar by a schedule change,
        # the allocation is no longer valid — reject before doing anything.
        fresh_match = view.db.get_match(view.match["id"])
        if not fresh_match or not fresh_match.get("teamup_event_id"):
            await interaction.response.send_message(
                "❌ This match is no longer on the broadcast schedule — "
                "the allocation has been cancelled.",
                ephemeral=True,
            )
            return

        # Validate required roles
        missing = [r for r in ROLE_ORDER if r not in view.selections
                   and r not in ("pbp", "colour")]  # casters validated separately

        # Validate caster selections (need exactly 1 PBP and 1 Colour)
        caster_roles = {}
        for val in view.caster_selections:
            role, uid = val.split(":", 1)
            if role in caster_roles:
                await interaction.response.send_message(
                    f"❌ You selected two **{ROLE_LABELS[role]}** — pick one each for PBP and Colour.",
                    ephemeral=True,
                )
                return
            caster_roles[role] = uid

        for req_caster in ("pbp", "colour"):
            if req_caster not in caster_roles:
                missing.append(req_caster)

        if missing:
            labels = [ROLE_LABELS[r] for r in missing]
            await interaction.response.send_message(
                f"❌ Please select: {', '.join(labels)}", ephemeral=True
            )
            return

        # Build role_assignments
        role_assignments: dict = {}
        for role_key in ("producer", "observer"):
            uid = view.selections[role_key]
            s = view.signups_by_id.get(uid)
            if not s:
                all_sigs = view.db.get_signups_for_match(view.match["id"])
                s = next((x for x in all_sigs if x["user_id"] == uid), None)
            if s:
                role_assignments[role_key] = {
                    "user_id":      s["user_id"],
                    "username":     s["username"],
                    "display_name": s["display_name"],
                }

        for role_key, uid in caster_roles.items():
            s = view.signups_by_id.get(uid)
            if not s:
                all_sigs = view.db.get_signups_for_match(view.match["id"])
                s = next((x for x in all_sigs if x["user_id"] == uid), None)
            if s:
                role_assignments[role_key] = {
                    "user_id":      s["user_id"],
                    "username":     s["username"],
                    "display_name": s["display_name"],
                }

        # Optional roles (host + analysts)
        analyst_count = 0
        for val in view.optional_selections:
            role_type, uid = val.split(":", 1)
            s = view.signups_by_id.get(uid)
            if not s:
                all_sigs = view.db.get_signups_for_match(view.match["id"])
                s = next((x for x in all_sigs if x["user_id"] == uid), None)
            if s:
                if role_type == "host":
                    role_assignments["host"] = {
                        "user_id":      s["user_id"],
                        "username":     s["username"],
                        "display_name": s["display_name"],
                    }
                elif role_type == "analyst" and analyst_count < 2:
                    analyst_count += 1
                    role_assignments[f"analyst_{analyst_count}"] = {
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
        for i in range(1, 3):
            a = role_assignments.get(f"analyst_{i}")
            if a:
                optional_parts.append(f"Analyst: {a['display_name']}")
        summary = (" " + ", ".join(optional_parts) + ".") if optional_parts else " No optional roles."

        await interaction.response.edit_message(
            content=(
                interaction.message.content
                + f"\n\n✅ **Allocation confirmed — talent notified in broadcast channel.**{summary}"
            ),
            view=view,
        )


class _CancelButton(discord.ui.Button):
    def __init__(self, match_id: int):
        super().__init__(
            label="Cancel Broadcast",
            emoji="❌",
            style=discord.ButtonStyle.danger,
            custom_id=f"alloc_cancel_{match_id}",
            row=4,
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return
        is_admin = interaction.user.guild_permissions.administrator
        is_mgr = self.view.db.is_manager(str(interaction.user.id))
        if not is_admin and not is_mgr:
            await interaction.response.send_message(
                "Only managers and administrators can cancel broadcasts.", ephemeral=True
            )
            return
        await self.view.cancel_broadcast(interaction)


class AllocationView(discord.ui.View):
    def __init__(self, match: dict, signups: list[dict], db: Database,
                 broadcast_channel, log_channel, get_teamup):
        super().__init__(timeout=86400)
        self.match = match
        self.db = db
        self.broadcast_channel = broadcast_channel
        self.log_channel = log_channel
        self.get_teamup = get_teamup
        self.selections: dict[str, str] = {}      # role_key -> user_id
        self.caster_selections: list[str] = []    # encoded "pbp:uid" / "colour:uid"
        self.optional_selections: list[str] = []  # encoded "host:uid" / "analyst:uid"
        self.signups_by_id = {s["user_id"]: s for s in signups}

        by_role: dict[str, list] = {r: [] for r in ROLE_EMOJIS}
        for s in sorted(signups, key=lambda x: x["signed_up_at"]):
            if s["role"] in by_role:
                by_role[s["role"]].append(s)

        # Row 0: Producer, Row 1: Observer
        self.add_item(_RoleSelect("producer", "Producer", match["id"],
                                  by_role["producer"], db, row=0))
        self.add_item(_RoleSelect("observer", "Observer", match["id"],
                                  by_role["observer"], db, row=1))
        # Row 2: PBP + Colour combined
        self.add_item(_CasterSelect(match["id"], by_role["pbp"], by_role["colour"], db))
        # Row 3: Host + Analyst combined (optional)
        self.add_item(_OptionalSelect(match["id"], by_role["host"], by_role["analyst"], db))
        # Row 4: Confirm + Cancel
        self.add_item(_ConfirmButton(match["id"]))
        self.add_item(_CancelButton(match["id"]))

    async def cancel_broadcast(self, interaction: discord.Interaction):
        match = self.match
        teamup = self.get_teamup()

        event_id = match.get("teamup_event_id")
        if teamup and event_id:
            try:
                teamup.delete_event(event_id)
            except Exception as e:
                log.warning("Failed to delete TeamUp event %s during cancel: %s", event_id, e)
            self.db.update_match_teamup_id(match["id"], None)
            self.db.decrement_scheduled_count(match["team_home"])
            self.db.decrement_scheduled_count(match["team_away"])

        self.db.reset_allocation(match["id"])

        signups = self.db.get_signups_for_match(match["id"])
        all_user_ids = list({s["user_id"] for s in signups})
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

        if self.broadcast_channel:
            try:
                await self.broadcast_channel.send(cancel_text)
            except Exception as e:
                log.error("Failed to send cancellation for match %s: %s", match["id"], e)

        # Edit the sign-up message to show cancelled
        signup_ch_id = (self.db.get_config("signup_channel_id")
                        or self.db.get_config("broadcast_channel_id"))
        signup_ch = interaction.client.get_channel(int(signup_ch_id)) if signup_ch_id else None
        bcast = self.db.get_broadcast_message(match["id"])
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

        self.stop()
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=interaction.message.content + "\n\n❌ **Broadcast cancelled.**",
            view=self,
        )


# ---------------------------------------------------------------------------
# Top-level helper called by events.py and bot.py
# ---------------------------------------------------------------------------

async def send_allocation_request(db: Database, match: dict,
                                   log_channel, broadcast_channel,
                                   get_teamup=None) -> None:
    """Send the single-step talent allocation UI to the log channel."""
    if not log_channel:
        return

    db.create_allocation(match["id"])
    db.set_allocation_status(match["id"], "sent")

    signups = db.get_signups_for_match(match["id"])

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

    lines += ["", "Select talent for each role below, then confirm. Use **Force Schedule** on the sign-up post to re-trigger this if needed:"]
    text = "\n".join(lines)

    view = AllocationView(match, signups, db, broadcast_channel, log_channel, get_teamup)
    try:
        msg = await log_channel.send(text, view=view)
        db.set_allocation_message(match["id"], str(msg.id), str(log_channel.id))
    except Exception as e:
        log.error("Failed to send allocation request for match %s: %s", match["id"], e)
