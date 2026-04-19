"""Thread creation, ready-check, and League Admin correction listener."""
import discord
import json
import logging
import time
from discord import app_commands
from discord.ext import commands
from typing import Optional

from role_matcher import find_best_match
from scheduler import _SEPARATOR

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Thread creation
# ---------------------------------------------------------------------------

async def create_match_thread(
    bot,
    match: dict,
    role_assignments: dict,
    *,
    test_mode: bool = False,
    test_teams: Optional[tuple[str, str]] = None,
) -> Optional[discord.Thread]:
    """Create a private thread for the match and post the opening message.

    Returns the created Thread, or None if thread_channel_id is not configured.
    In test_mode the match is not stored in the DB and test_teams overrides
    the team names used for role matching.
    """
    db = bot.db
    thread_channel_id = db.get_config("thread_channel_id")
    league_admin_role_id = db.get_config("league_admin_role_id")

    if not thread_channel_id:
        return None

    channel = bot.get_channel(int(thread_channel_id))
    if not channel:
        return None

    guild = channel.guild
    all_roles = [(str(r.id), r.name) for r in guild.roles if not r.is_default()]

    home_name = test_teams[0] if (test_mode and test_teams) else match["team_home"]
    away_name = test_teams[1] if (test_mode and test_teams) else match["team_away"]

    team1_role_id, _ = find_best_match(home_name, all_roles)
    team2_role_id, _ = find_best_match(away_name, all_roles)
    team1_low = team1_role_id is None
    team2_low = team2_role_id is None

    thread_name = (
        f"[TEST] {home_name} vs {away_name}"
        if test_mode
        else f"[{match['division']}] {match['team_home']} vs {match['team_away']}"
    )

    try:
        thread = await channel.create_thread(
            name=thread_name,
            type=discord.ChannelType.private_thread,
            invitable=False,
        )
    except Exception as e:
        log.error("Failed to create thread for match %s: %s", match.get("id"), e)
        return None

    if not test_mode:
        db.insert_thread_message(
            match["id"], str(thread.id), thread_channel_id,
            team1_role_id, team2_role_id,
            int(team1_low), int(team2_low),
        )

    # Build opening message
    pings: list[str] = []
    if league_admin_role_id:
        pings.append(f"<@&{league_admin_role_id}>")

    if test_mode:
        pass  # League Admin role already in pings
    else:
        producer = role_assignments.get("producer")
        observer = role_assignments.get("observer")
        seen_uids: set[str] = set()
        for assignment in (producer, observer):
            if assignment and assignment.get("user_id") not in seen_uids:
                seen_uids.add(assignment["user_id"])
                pings.append(f"<@{assignment['user_id']}>")

    if team1_role_id:
        pings.append(f"<@&{team1_role_id}>")
    if team2_role_id:
        pings.append(f"<@&{team2_role_id}>")

    ts = match["match_time"]
    lines = [
        " ".join(pings),
        "",
        _SEPARATOR,
        f"📺 **{'Test ' if test_mode else ''}Broadcast Thread**",
        f"**[{match['division']}] {match['team_home']} vs {match['team_away']}**"
        f" | <t:{ts}:F>",
    ]

    low_conf_teams: list[str] = []
    if team1_low:
        low_conf_teams.append(home_name)
    if team2_low:
        low_conf_teams.append(away_name)

    if low_conf_teams:
        admin_mention = (
            f"<@&{league_admin_role_id}>" if league_admin_role_id else "a League Admin"
        )
        lines += [
            "",
            "⚠️ **Could not find a Discord role for the following teams:**",
        ]
        for team in low_conf_teams:
            lines.append(f"- **{team}**")
        lines.append(
            f"{admin_mention} — please reply in this thread with the correct role @mentions."
        )

    await thread.send("\n".join(lines))
    return thread


# ---------------------------------------------------------------------------
# Ready Check
# ---------------------------------------------------------------------------

def _build_ready_check_content(match: dict, responses: dict, bot) -> str:
    db = bot.db
    alloc = db.get_allocation(match["id"])
    role_assignments: dict = {}
    if alloc and alloc.get("role_assignments"):
        role_assignments = json.loads(alloc["role_assignments"])

    lines = [
        _SEPARATOR,
        "🔔 **Ready Check**",
        f"**[{match['division']}] {match['team_home']} vs {match['team_away']}**"
        f" | <t:{match['match_time']}:F>",
        "",
        "Match is 30 minutes away — please confirm your readiness:",
        "",
    ]

    seen: set[str] = set()
    for key in ("producer", "observer"):
        assignment = role_assignments.get(key)
        if not assignment:
            continue
        uid = assignment["user_id"]
        if uid in seen:
            continue
        seen.add(uid)
        name = assignment["display_name"]
        status = responses.get(uid)
        if status is True:
            tag = "✅ Ready"
        elif status is False:
            tag = "❌ Not Ready"
        else:
            tag = "⏳ No Response"
        lines.append(f"<@{uid}> ({name}): {tag}")

    return "\n".join(lines)


class ReadyButton(discord.ui.Button):
    def __init__(self, match_id: int):
        super().__init__(
            label="Ready",
            style=discord.ButtonStyle.success,
            custom_id=f"thread_ready_{match_id}",
            emoji="✅",
            row=0,
        )
        self.match_id = match_id

    async def callback(self, interaction: discord.Interaction):
        db = interaction.client.db
        match = db.get_match(self.match_id)
        if not match:
            await interaction.response.send_message("Match not found.", ephemeral=True)
            return

        user_id = str(interaction.user.id)
        db.set_thread_ready_check_response(self.match_id, user_id, True)
        responses = db.get_thread_ready_check_responses(self.match_id)

        await interaction.response.edit_message(
            content=_build_ready_check_content(match, responses, interaction.client),
            view=self.view,
        )


class NotReadyButton(discord.ui.Button):
    def __init__(self, match_id: int):
        super().__init__(
            label="Not Ready",
            style=discord.ButtonStyle.danger,
            custom_id=f"thread_not_ready_{match_id}",
            emoji="❌",
            row=0,
        )
        self.match_id = match_id

    async def callback(self, interaction: discord.Interaction):
        db = interaction.client.db
        match = db.get_match(self.match_id)
        if not match:
            await interaction.response.send_message("Match not found.", ephemeral=True)
            return

        user_id = str(interaction.user.id)
        db.set_thread_ready_check_response(self.match_id, user_id, False)
        responses = db.get_thread_ready_check_responses(self.match_id)

        league_admin_role_id = db.get_config("league_admin_role_id")
        ping = f"<@&{league_admin_role_id}>" if league_admin_role_id else "League Admin"

        await interaction.response.edit_message(
            content=(
                _build_ready_check_content(match, responses, interaction.client)
                + f"\n\n⚠️ {ping} — <@{interaction.user.id}> is **not ready**."
            ),
            view=self.view,
        )


class ReadyCheckView(discord.ui.View):
    def __init__(self, match_id: int):
        super().__init__(timeout=None)
        self.add_item(ReadyButton(match_id))
        self.add_item(NotReadyButton(match_id))


async def send_ready_check(bot, match: dict) -> None:
    """Send the ready-check message into the match thread."""
    db = bot.db
    thread_msg = db.get_thread_message(match["id"])
    if not thread_msg or thread_msg.get("ready_check_message_id"):
        return

    thread = bot.get_channel(int(thread_msg["thread_id"]))
    if not thread:
        return

    # Build @mention pings
    pings: list[str] = []
    league_admin_role_id = db.get_config("league_admin_role_id")
    if league_admin_role_id:
        pings.append(f"<@&{league_admin_role_id}>")

    alloc = db.get_allocation(match["id"])
    role_assignments: dict = {}
    if alloc and alloc.get("role_assignments"):
        role_assignments = json.loads(alloc["role_assignments"])

    seen: set[str] = set()
    for key in ("producer", "observer"):
        a = role_assignments.get(key)
        if a and a["user_id"] not in seen:
            seen.add(a["user_id"])
            pings.append(f"<@{a['user_id']}>")

    ping_line = " ".join(pings)
    content = (
        ping_line + "\n\n"
        + _build_ready_check_content(match, {}, bot)
        if ping_line else _build_ready_check_content(match, {}, bot)
    )

    view = ReadyCheckView(match["id"])
    try:
        msg = await thread.send(content=content, view=view)
        bot.add_view(view)
        db.set_thread_ready_check_message(match["id"], str(msg.id))
    except Exception as e:
        log.error("Failed to send ready check for match %s: %s", match["id"], e)


# ---------------------------------------------------------------------------
# Create Thread button (on ApprovedSignUpView)
# ---------------------------------------------------------------------------

class CreateThreadButton(discord.ui.Button):
    """Grey 'Create Thread' button shown on approved sign-up messages."""

    def __init__(self, match_id: int):
        super().__init__(
            label="Create Thread",
            style=discord.ButtonStyle.secondary,
            custom_id=f"create_thread_{match_id}",
            row=2,
        )
        self.match_id = match_id

    async def callback(self, interaction: discord.Interaction):
        from cogs.signup import _manager_check
        db = interaction.client.db

        if not _manager_check(interaction, db):
            await interaction.response.send_message(
                "Manager or Administrator permission required.", ephemeral=True
            )
            return

        existing = db.get_thread_message(self.match_id)
        if existing:
            await interaction.response.send_message(
                f"A thread already exists for this match: <#{existing['thread_id']}>",
                ephemeral=True,
            )
            return

        match = db.get_match(self.match_id)
        if not match:
            await interaction.response.send_message("Match not found.", ephemeral=True)
            return

        alloc = db.get_allocation(self.match_id)
        role_assignments: dict = {}
        if alloc and alloc.get("role_assignments"):
            role_assignments = json.loads(alloc["role_assignments"])

        await interaction.response.defer(ephemeral=True)
        thread = await create_match_thread(
            interaction.client, match, role_assignments
        )
        if thread:
            await interaction.followup.send(
                f"✅ Thread created: {thread.mention}", ephemeral=True
            )
        else:
            await interaction.followup.send(
                "⚠️ Could not create thread — check that `/set-thread-channel` is configured.",
                ephemeral=True,
            )


# ---------------------------------------------------------------------------
# ThreadsCog
# ---------------------------------------------------------------------------

class ThreadsCog(commands.Cog):
    def __init__(self, bot: commands.Bot, db):
        self.bot = bot
        self.db = db

    def _league_admin_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        if interaction.user.guild_permissions.administrator:
            return True
        role_id = self.db.get_config("league_admin_role_id")
        if role_id:
            return any(str(r.id) == role_id for r in interaction.user.roles)
        return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for League Admin role corrections in threads with low-confidence matches."""
        if message.author.bot:
            return
        if not isinstance(message.channel, discord.Thread):
            return

        thread_id = str(message.channel.id)
        thread_msg = self.db.get_thread_by_id(thread_id)
        if not thread_msg:
            return
        if not thread_msg.get("team1_low_confidence") and not thread_msg.get("team2_low_confidence"):
            return

        league_admin_role_id = self.db.get_config("league_admin_role_id")
        if not league_admin_role_id:
            return

        is_league_admin = any(
            str(r.id) == league_admin_role_id for r in message.author.roles
        )
        if not is_league_admin and not message.author.guild_permissions.administrator:
            return

        role_mentions = message.role_mentions
        if not role_mentions:
            return

        # Assign mentioned roles to the teams that need correction (in order)
        low_slots: list[int] = []
        if thread_msg.get("team1_low_confidence"):
            low_slots.append(1)
        if thread_msg.get("team2_low_confidence"):
            low_slots.append(2)

        new_team1 = thread_msg.get("team1_role_id")
        new_team2 = thread_msg.get("team2_role_id")
        new_low1 = thread_msg.get("team1_low_confidence", 0)
        new_low2 = thread_msg.get("team2_low_confidence", 0)

        for i, slot in enumerate(low_slots):
            if i >= len(role_mentions):
                break
            role_id = str(role_mentions[i].id)
            if slot == 1:
                new_team1 = role_id
                new_low1 = 0
            else:
                new_team2 = role_id
                new_low2 = 0

        self.db.update_thread_roles(
            thread_msg["match_id"], new_team1, new_team2, new_low1, new_low2
        )
        parts = []
        if new_team1:
            parts.append(f"<@&{new_team1}>")
        if new_team2:
            parts.append(f"<@&{new_team2}>")
        await message.channel.send(
            "✅ Team roles updated: " + " vs ".join(parts or ["—"])
        )

    @app_commands.command(
        name="test-thread",
        description="Test thread creation (League Admin or Administrator only)",
    )
    async def test_thread(self, interaction: discord.Interaction):
        if not self._league_admin_check(interaction):
            await interaction.response.send_message(
                "League Admin role or Administrator permission required.", ephemeral=True
            )
            return

        thread_channel_id = self.db.get_config("thread_channel_id")
        if not thread_channel_id:
            await interaction.response.send_message(
                "Thread channel not configured. Use `/set-thread-channel` first.",
                ephemeral=True,
            )
            return

        league_admin_role_id = self.db.get_config("league_admin_role_id")

        # Fake match for testing
        fake_match = {
            "id": -1,
            "division": "Premier",
            "team_home": "test1",           # should match the "test1" Discord role
            "team_away": "Zyx9000 Phantoms",  # intentionally unmatchable — triggers warning
            "match_time": int(time.time()) + 1800,
        }

        # League Admin role stands in for Producer/Observer
        # We pass empty role_assignments and handle the pings manually via test_mode
        await interaction.response.defer(ephemeral=True)

        thread = await create_match_thread(
            self.bot,
            fake_match,
            {},
            test_mode=True,
            test_teams=("test1", "Zyx9000 Phantoms"),
        )

        if not thread:
            await interaction.followup.send(
                "⚠️ Could not create thread. Check `/set-thread-channel` and bot permissions.",
                ephemeral=True,
            )
            return

        # Send a test ready check into the thread immediately
        alloc_data = {
            "producer": None,
            "observer": None,
        }

        league_admin_role_id = self.db.get_config("league_admin_role_id")
        pings: list[str] = []
        if league_admin_role_id:
            pings.append(f"<@&{league_admin_role_id}>")

        rc_lines = [
            _SEPARATOR,
            "🔔 **Test Ready Check**",
            f"**[{fake_match['division']}] {fake_match['team_home']} vs {fake_match['team_away']}**"
            f" | <t:{fake_match['match_time']}:F>",
            "",
            "*(This is a test — no real match data)*",
            "",
            "Producer / Observer confirmation would appear here.",
        ]

        # For testing the Ready/Not Ready flow we use match_id=-1 which won't
        # persist responses, but demonstrates the button UI.
        view = ReadyCheckView(-1)
        try:
            ping_str = " ".join(pings)
            content = (ping_str + "\n\n" if ping_str else "") + "\n".join(rc_lines)
            await thread.send(content=content, view=view)
        except Exception as e:
            log.error("test-thread: failed to send ready check: %s", e)

        await interaction.followup.send(
            f"✅ Test thread created: {thread.mention}\n"
            "- Check the thread for the opening message and ready check.\n"
            "- One team should show ⚠️ (no role found) to test the correction flow.",
            ephemeral=True,
        )
