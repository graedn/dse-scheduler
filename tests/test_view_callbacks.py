"""Tests for view callback logic (H4, M8, M9).

Covers:
  - ReadyButton / RejectButton / _finalize_match  (M8)
  - _ConfirmButton / cancel_broadcast             (M9)
  - _NewMatchSelect match-replacement flow        (H4)

All Discord API calls are mocked.  Uses a real in-memory Database for state
transitions so the actual DB logic is exercised.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from database import Database

TS_8PM  = 1713657600   # Saturday 2024-04-20 8pm ET
TS_10PM = 1713664800   # Saturday 2024-04-20 10pm ET


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


def _make_interaction(db_instance, user_id="1", msg_id="1001",
                      msg_content="original", is_admin=False):
    interaction = MagicMock()
    interaction.client.db = db_instance
    interaction.client.get_channel.return_value = AsyncMock()
    interaction.client.get_teamup.return_value = MagicMock()
    interaction.user.id = int(user_id)
    interaction.user.display_name = f"User{user_id}"
    interaction.message.id = msg_id
    interaction.message.content = msg_content
    interaction.guild = MagicMock()
    interaction.user.guild_permissions = MagicMock()
    interaction.user.guild_permissions.administrator = is_admin
    interaction.response = AsyncMock()
    return interaction


def _insert_match(db, ts=TS_8PM, home="Team A", away="Team B", division="Premier"):
    return db.insert_match(
        division=division, week="Week 1",
        team_home=home, team_away=away,
        match_time=ts, posted_at=ts - 3600,
    )


def _setup_allocation(db, match_id, role_assignments,
                      status="awaiting_confirm",
                      conf_msg_id="10001", conf_ch_id="999"):
    """Insert allocation with given role assignments and null confirmations."""
    from cogs.talent import _get_required_user_ids
    required_ids = _get_required_user_ids(role_assignments)
    confirmations = {uid: None for uid in required_ids}
    db.create_allocation(match_id)
    db.set_allocation_assignments(
        match_id,
        role_assignments=role_assignments,
        confirmations=confirmations,
        confirmation_message_id=conf_msg_id,
        confirmation_channel_id=conf_ch_id,
    )
    db.set_allocation_status(match_id, status)
    return confirmations


# Three-user crew: u1=producer+observer, u2=pbp_1, u3=colour_1
def _role_assignments(u1="1", u2="2", u3="3"):
    return {
        "producer": {"user_id": u1, "username": f"u{u1}", "display_name": f"User{u1}"},
        "observer": {"user_id": u1, "username": f"u{u1}", "display_name": f"User{u1}"},
        "pbp_1":    {"user_id": u2, "username": f"u{u2}", "display_name": f"User{u2}"},
        "colour_1": {"user_id": u3, "username": f"u{u3}", "display_name": f"User{u3}"},
    }


# ===========================================================================
# M8 — ReadyButton
# ===========================================================================

class TestReadyButton:
    def _button_in_view(self, match_id):
        from cogs.confirm_view import ConfirmationView, ReadyButton
        view = ConfirmationView(match_id)
        button = next(c for c in view.children if isinstance(c, ReadyButton))
        return button

    async def test_inactive_allocation_sends_ephemeral(self, db):
        match_id = _insert_match(db)
        button = self._button_in_view(match_id)
        interaction = _make_interaction(db, user_id="1", msg_id="no_such")
        # Override the DB method so the allocation lookup returns nothing
        interaction.client.db.get_allocation_by_confirmation_message = MagicMock(return_value=None)
        interaction.client.db = db
        db_mock = MagicMock(wraps=db)
        db_mock.get_allocation_by_confirmation_message.return_value = None
        interaction.client.db = db_mock

        await button.callback(interaction)

        interaction.response.send_message.assert_called_once()
        assert "no longer active" in interaction.response.send_message.call_args[0][0]

    async def test_user_not_in_confirmations_sends_ephemeral(self, db):
        match_id = _insert_match(db)
        ra = _role_assignments("2", "3", "4")   # u2, u3, u4 — not u1
        _setup_allocation(db, match_id, ra, conf_msg_id="20001")

        button = self._button_in_view(match_id)
        interaction = _make_interaction(db, user_id="1", msg_id="20001")

        await button.callback(interaction)

        interaction.response.send_message.assert_called_once()
        assert "not required" in interaction.response.send_message.call_args[0][0]

    async def test_partial_confirm_edits_message_no_finalize(self, db):
        match_id = _insert_match(db)
        ra = _role_assignments("1", "2", "3")
        _setup_allocation(db, match_id, ra, conf_msg_id="30001")

        button = self._button_in_view(match_id)
        interaction = _make_interaction(db, user_id="1", msg_id="30001")

        with patch("cogs.confirm_view._finalize_match", new_callable=AsyncMock) as mock_fin:
            await button.callback(interaction)

        interaction.response.edit_message.assert_called_once()
        mock_fin.assert_not_called()
        # DB records u1 as confirmed
        assert db.get_confirmations(match_id).get("1") is True

    async def test_all_confirmed_triggers_finalize(self, db):
        match_id = _insert_match(db)
        ra = _role_assignments("1", "2", "3")
        _setup_allocation(db, match_id, ra, conf_msg_id="40001")

        # Pre-confirm u2 and u3; u1 is the last click
        db.set_confirmation(match_id, "2", True)
        db.set_confirmation(match_id, "3", True)

        button = self._button_in_view(match_id)
        interaction = _make_interaction(db, user_id="1", msg_id="40001")

        with patch("cogs.confirm_view._finalize_match", new_callable=AsyncMock) as mock_fin:
            await button.callback(interaction)

        interaction.response.edit_message.assert_called_once()
        mock_fin.assert_called_once()


# ===========================================================================
# M8 — RejectButton
# ===========================================================================

class TestRejectButton:
    def _button_in_view(self, match_id):
        from cogs.confirm_view import ConfirmationView, RejectButton
        view = ConfirmationView(match_id)
        button = next(c for c in view.children if isinstance(c, RejectButton))
        return button

    async def test_inactive_allocation_sends_ephemeral(self, db):
        match_id = _insert_match(db)
        button = self._button_in_view(match_id)
        db_mock = MagicMock(wraps=db)
        db_mock.get_allocation_by_confirmation_message.return_value = None
        interaction = _make_interaction(db_mock, user_id="1", msg_id="no_such")

        await button.callback(interaction)

        interaction.response.send_message.assert_called_once()
        assert "no longer active" in interaction.response.send_message.call_args[0][0]

    async def test_user_not_in_confirmations_sends_ephemeral(self, db):
        match_id = _insert_match(db)
        ra = _role_assignments("2", "3", "4")
        _setup_allocation(db, match_id, ra, conf_msg_id="50001")

        button = self._button_in_view(match_id)
        interaction = _make_interaction(db, user_id="1", msg_id="50001")

        await button.callback(interaction)

        interaction.response.send_message.assert_called_once()
        assert "not required" in interaction.response.send_message.call_args[0][0]

    async def test_reject_edits_message_and_posts_replace_view(self, db):
        match_id = _insert_match(db)
        ra = _role_assignments("1", "2", "3")
        _setup_allocation(db, match_id, ra, conf_msg_id="60001")
        db.set_config("log_channel_id", "111")
        db.set_config("broadcast_channel_id", "222")

        log_ch   = AsyncMock()
        bcast_ch = AsyncMock()

        def _get_ch(ch_id):
            if str(ch_id) == "111": return log_ch
            if str(ch_id) == "222": return bcast_ch
            return None

        button = self._button_in_view(match_id)
        interaction = _make_interaction(db, user_id="1", msg_id="60001")
        interaction.client.get_channel.side_effect = _get_ch

        await button.callback(interaction)

        # Confirmation message edited in place; decliner shows [Rejected].
        interaction.response.edit_message.assert_called_once()
        assert "[Rejected]" in interaction.response.edit_message.call_args[1]["content"]

        # Allocation is NOT reset (single-role replace, not full re-allocation).
        a = db.get_allocation(match_id)
        assert a["role_assignments"] is not None
        assert a["status"] == "awaiting_confirm"

        # Required-role rejecter → a pre-selected ReplaceRoleView is posted.
        from cogs.talent import ReplaceRoleView
        sent_views = [c.kwargs.get("view") for c in log_ch.send.call_args_list]
        assert any(isinstance(v, ReplaceRoleView) for v in sent_views)

    async def test_reject_marks_user_unavailable_and_shows_rejected_tag(self, db):
        """After clicking Reject, the rejecter's sign-up becomes 'unavailable',
        their unavailable_count increments, and the confirmation message content
        shows [Rejected] for them."""
        match_id = _insert_match(db)
        ra = _role_assignments("1", "2", "3")
        _setup_allocation(db, match_id, ra, conf_msg_id="70001")
        # Existing role signup for u1 — should be replaced by 'unavailable'.
        db.upsert_signup(match_id, "m1", "producer", "1", "u1", "User1")
        # Broadcast message row so RejectButton can resolve signup_message_id.
        db.insert_broadcast_message(match_id, "m1", "222")
        db.set_config("log_channel_id", "111")
        db.set_config("broadcast_channel_id", "222")

        button = self._button_in_view(match_id)
        interaction = _make_interaction(db, user_id="1", msg_id="70001")
        interaction.client.get_channel.return_value = AsyncMock()

        with patch("cogs.talent.send_allocation_request", new_callable=AsyncMock):
            await button.callback(interaction)

        # Sign-up is now 'unavailable' (prior role rows removed)
        sigs_for_u1 = [s for s in db.get_signups_for_match(match_id)
                       if s["user_id"] == "1"]
        assert len(sigs_for_u1) == 1
        assert sigs_for_u1[0]["role"] == "unavailable"

        # Talent counter: unavailable_count == 1
        row = next(r for r in db.get_all_talent() if r["user_id"] == "1")
        assert row["unavailable_count"] == 1

        # Edited content includes the [Rejected] tag for the decliner
        content = interaction.response.edit_message.call_args[1]["content"]
        assert "[Rejected]" in content


class TestRejectSingleRoleReplace:
    def _btn(self, match_id):
        from cogs.confirm_view import ConfirmationView, RejectButton
        v = ConfirmationView(match_id)
        return next(c for c in v.children if isinstance(c, RejectButton))

    async def test_required_reject_posts_replace_view_no_reset(self, db):
        match_id = _insert_match(db)
        ra = _role_assignments("1", "2", "3")
        _setup_allocation(db, match_id, ra, conf_msg_id="70001")
        db.set_config("log_channel_id", "111")
        db.set_confirmation(match_id, "2", True)
        log_ch = AsyncMock()
        btn = self._btn(match_id)
        interaction = _make_interaction(db, user_id="3", msg_id="70001")  # colour_1 rejects
        interaction.client.get_channel = MagicMock(return_value=log_ch)

        await btn.callback(interaction)

        a = db.get_allocation(match_id)
        assert a["role_assignments"] is not None
        assert a["status"] == "awaiting_confirm"
        assert db.get_confirmations(match_id).get("2") is True
        from cogs.talent import ReplaceRoleView
        sent_views = [c.kwargs.get("view") for c in log_ch.send.call_args_list]
        assert any(isinstance(v, ReplaceRoleView) for v in sent_views)

    async def test_optional_reject_flags_only(self, db):
        match_id = _insert_match(db)
        ra = _role_assignments("1", "2", "3")
        ra["host"] = {"user_id": "8", "username": "u8", "display_name": "H8"}
        _setup_allocation(db, match_id, ra, conf_msg_id="70002")
        db.set_config("log_channel_id", "111")
        db.set_confirmation(match_id, "8", None)
        log_ch = AsyncMock()
        btn = self._btn(match_id)
        interaction = _make_interaction(db, user_id="8", msg_id="70002")
        interaction.client.get_channel = MagicMock(return_value=log_ch)

        await btn.callback(interaction)

        from cogs.talent import ReplaceRoleView
        sent_views = [c.kwargs.get("view") for c in log_ch.send.call_args_list]
        assert not any(isinstance(v, ReplaceRoleView) for v in sent_views)
        assert any("rejected the optional" in str(c).lower()
                   for c in log_ch.send.call_args_list)
        assert db.get_allocation(match_id)["status"] == "awaiting_confirm"


# ===========================================================================
# M8 — _finalize_match
# ===========================================================================

class TestFinalizeMatch:
    async def test_finalize_marks_accepted_and_increments_counts(self, db):
        from cogs.confirm_view import _finalize_match
        match_id = _insert_match(db)
        db.update_match_teamup_id(match_id, "evt_1")
        db.set_config("log_channel_id", "456")

        ra = _role_assignments("1", "2", "3")
        alloc_stub = {"match_id": match_id, "role_assignments": json.dumps(ra)}

        bot = MagicMock()
        bot.db = db
        bot.get_channel.return_value = AsyncMock()
        bot.get_teamup.return_value = MagicMock()

        match = db.get_match(match_id)
        with patch("cogs.confirm_view._CALENDAR_LINK", ""):
            await _finalize_match(match, alloc_stub, ra, bot)

        assert db.get_match(match_id)["broadcast_accepted"] == 1
        counts = {r["user_id"]: r["broadcast_count"] for r in db.get_all_talent()}
        assert counts["1"] == 1   # u1 counted once despite filling producer+observer
        assert counts["2"] == 1
        assert counts["3"] == 1

    async def test_finalize_calls_teamup_update(self, db):
        from cogs.confirm_view import _finalize_match
        match_id = _insert_match(db)
        db.update_match_teamup_id(match_id, "evt_abc")
        db.set_config("log_channel_id", "456")

        ra = _role_assignments("1", "2", "3")
        alloc_stub = {"match_id": match_id, "role_assignments": json.dumps(ra)}
        teamup = MagicMock()
        bot = MagicMock()
        bot.db = db
        bot.get_channel.return_value = AsyncMock()
        bot.get_teamup.return_value = teamup

        match = db.get_match(match_id)
        with patch("cogs.confirm_view._CALENDAR_LINK", ""):
            await _finalize_match(match, alloc_stub, ra, bot)

        teamup.update_event.assert_called_once()
        assert teamup.update_event.call_args[0][0] == "evt_abc"


# ===========================================================================
# M9 — _ContinueButton (Phase 1 validation) and _ConfirmButton (Phase 2)
# ===========================================================================

def _make_phase1_view(db, match, signups):
    from cogs.talent import AllocationView
    return AllocationView(
        match=match, signups=signups, db=db,
        broadcast_channel=AsyncMock(),
        log_channel=AsyncMock(),
        get_teamup=lambda: MagicMock(),
    )


def _make_phase2_view(db, match, signups, required_selections):
    from cogs.talent import AllocationConfirmView
    return AllocationConfirmView(
        match=match, signups=signups, db=db,
        broadcast_channel=AsyncMock(),
        log_channel=AsyncMock(),
        get_teamup=lambda: MagicMock(),
        required_selections=required_selections,
    )


class TestContinueButton:
    """Phase 1 validation — _ContinueButton rejects bad role selections."""

    def _view_and_button(self, db, match, signups):
        from cogs.talent import _ContinueButton
        view = _make_phase1_view(db, match, signups)
        button = next(c for c in view.children if isinstance(c, _ContinueButton))
        return view, button

    async def test_missing_required_role_rejected(self, db):
        match_id = _insert_match(db)
        db.upsert_signup(match_id, "m1", "pbp", "2", "u2", "User2")
        signups = db.get_signups_for_match(match_id)
        match = db.get_match(match_id)
        view, button = self._view_and_button(db, match, signups)
        # No selections made

        interaction = _make_interaction(db, user_id="1", is_admin=True)
        interaction.guild = MagicMock()
        await button.callback(interaction)

        interaction.response.send_message.assert_called_once()
        assert "select" in interaction.response.send_message.call_args[0][0].lower()

    async def test_pbp_colour_overlap_rejected(self, db):
        """Same user as both PBP and Colour is rejected at the Continue step."""
        match_id = _insert_match(db)
        for role, uid in [("pbp","2"),("colour","3"),("producer","1"),("observer","4")]:
            db.upsert_signup(match_id, "m1", role, uid, f"u{uid}", f"User{uid}")
        signups = db.get_signups_for_match(match_id)
        match = db.get_match(match_id)
        view, button = self._view_and_button(db, match, signups)
        view.selections = {"producer": "1", "observer": "4", "pbp": "2", "colour": "2"}

        interaction = _make_interaction(db, user_id="1", is_admin=True)
        interaction.guild = MagicMock()
        await button.callback(interaction)

        interaction.response.send_message.assert_called_once()
        msg = interaction.response.send_message.call_args[0][0].lower()
        assert "same person" in msg or "colour caster" in msg

    async def test_producer_in_casters_rejected(self, db):
        """Producer also selected as PBP is rejected at Continue."""
        match_id = _insert_match(db)
        for role, uid in [("producer","1"),("observer","4"),("pbp","2"),("colour","3")]:
            db.upsert_signup(match_id, "m1", role, uid, f"u{uid}", f"User{uid}")
        signups = db.get_signups_for_match(match_id)
        match = db.get_match(match_id)
        view, button = self._view_and_button(db, match, signups)
        view.selections = {"producer": "1", "observer": "4", "pbp": "1", "colour": "3"}

        interaction = _make_interaction(db, user_id="1", is_admin=True)
        interaction.guild = MagicMock()
        await button.callback(interaction)

        interaction.response.send_message.assert_called_once()
        assert "producer" in interaction.response.send_message.call_args[0][0].lower()

    async def test_observer_in_casters_rejected(self, db):
        """Observer also selected as Colour is rejected at Continue."""
        match_id = _insert_match(db)
        for role, uid in [("producer","1"),("observer","4"),("pbp","2"),("colour","3")]:
            db.upsert_signup(match_id, "m1", role, uid, f"u{uid}", f"User{uid}")
        signups = db.get_signups_for_match(match_id)
        match = db.get_match(match_id)
        view, button = self._view_and_button(db, match, signups)
        view.selections = {"producer": "1", "observer": "4", "pbp": "2", "colour": "4"}

        interaction = _make_interaction(db, user_id="1", is_admin=True)
        interaction.guild = MagicMock()
        await button.callback(interaction)

        interaction.response.send_message.assert_called_once()
        assert "observer" in interaction.response.send_message.call_args[0][0].lower()

    async def test_non_manager_denied(self, db):
        match_id = _insert_match(db)
        match = db.get_match(match_id)
        view, button = self._view_and_button(db, match, [])

        interaction = _make_interaction(db, user_id="1", is_admin=False)
        interaction.guild = MagicMock()
        db.is_manager = MagicMock(return_value=False)
        await button.callback(interaction)

        interaction.response.send_message.assert_called_once()
        msg = interaction.response.send_message.call_args[0][0]
        assert "manager" in msg.lower() or "administrator" in msg.lower()

    async def test_valid_selections_advance_to_phase2(self, db):
        """All valid required selections → message edited with AllocationConfirmView."""
        match_id = _insert_match(db)
        for role, uid in [("producer","1"),("observer","2"),("pbp","3"),("colour","4")]:
            db.upsert_signup(match_id, "m1", role, uid, f"u{uid}", f"User{uid}")
        signups = db.get_signups_for_match(match_id)
        match = db.get_match(match_id)
        view, button = self._view_and_button(db, match, signups)
        view.selections = {"producer": "1", "observer": "2", "pbp": "3", "colour": "4"}

        interaction = _make_interaction(db, user_id="1", is_admin=True)
        interaction.guild = MagicMock()
        await button.callback(interaction)

        # Should not error — should edit message to phase 2 view
        interaction.response.send_message.assert_not_called()
        interaction.response.edit_message.assert_called_once()
        from cogs.talent import AllocationConfirmView
        view_arg = interaction.response.edit_message.call_args[1]["view"]
        assert isinstance(view_arg, AllocationConfirmView)


class TestConfirmButton:
    """Phase 2 confirmation — _ConfirmButton stores allocation and triggers confirmation."""

    def _view_and_button(self, db, match, signups, required_selections=None):
        from cogs.talent import _ConfirmButton
        if required_selections is None:
            required_selections = {"producer": "1", "observer": "2",
                                   "pbp": "3", "colour": "4"}
        view = _make_phase2_view(db, match, signups, required_selections)
        button = next(c for c in view.children if isinstance(c, _ConfirmButton))
        return view, button

    async def test_non_manager_denied(self, db):
        match_id = _insert_match(db)
        match = db.get_match(match_id)
        view, button = self._view_and_button(db, match, [])

        interaction = _make_interaction(db, user_id="1", is_admin=False)
        interaction.guild = MagicMock()
        db.is_manager = MagicMock(return_value=False)
        await button.callback(interaction)

        interaction.response.send_message.assert_called_once()
        msg = interaction.response.send_message.call_args[0][0]
        assert "manager" in msg.lower() or "administrator" in msg.lower()

    async def test_allocation_stored_with_correct_keys(self, db):
        """Confirm writes pbp_1 and colour_1 (single-select) to role_assignments."""
        match_id = _insert_match(db)
        db.update_match_teamup_id(match_id, "evt_test")
        db.create_allocation(match_id)
        for role, uid in [("producer","1"),("observer","2"),("pbp","3"),("colour","4")]:
            db.upsert_signup(match_id, "m1", role, uid, f"u{uid}", f"User{uid}")
        signups = db.get_signups_for_match(match_id)
        match = db.get_match(match_id)
        req = {"producer": "1", "observer": "2", "pbp": "3", "colour": "4"}
        view, button = self._view_and_button(db, match, signups, req)

        interaction = _make_interaction(db, user_id="1", is_admin=True)
        interaction.guild = MagicMock()
        await button.callback(interaction)

        interaction.response.send_message.assert_not_called()
        import json
        alloc = db.get_allocation(match_id)
        ra = json.loads(alloc["role_assignments"])
        assert ra["pbp_1"]["user_id"] == "3"
        assert ra["colour_1"]["user_id"] == "4"
        assert "pbp_2" not in ra
        assert "colour_2" not in ra

    async def test_host_analyst_via_optional_selections(self, db):
        """Host and Analyst appear in allocation when set via optional_selections."""
        match_id = _insert_match(db)
        db.update_match_teamup_id(match_id, "evt_test")
        db.create_allocation(match_id)
        for role, uid in [("producer","1"),("observer","2"),("pbp","3"),("colour","4"),
                          ("host","5"),("analyst","6")]:
            db.upsert_signup(match_id, "m1", role, uid, f"u{uid}", f"User{uid}")
        signups = db.get_signups_for_match(match_id)
        match = db.get_match(match_id)
        req = {"producer": "1", "observer": "2", "pbp": "3", "colour": "4"}
        view, button = self._view_and_button(db, match, signups, req)
        view.optional_selections = {"host": "5", "analyst": "6"}

        interaction = _make_interaction(db, user_id="1", is_admin=True)
        interaction.guild = MagicMock()
        await button.callback(interaction)

        import json
        alloc = db.get_allocation(match_id)
        ra = json.loads(alloc["role_assignments"])
        assert ra.get("host", {}).get("user_id") == "5"
        assert ra.get("analyst_1", {}).get("user_id") == "6"


# ===========================================================================
# Confirm message / talent description builders
# ===========================================================================

class TestBuildConfirmationMessage:
    def _ra(self):
        return {
            "producer":  {"user_id": "1", "username": "u1", "display_name": "Alice"},
            "observer":  {"user_id": "2", "username": "u2", "display_name": "Bob"},
            "pbp_1":     {"user_id": "3", "username": "u3", "display_name": "Carol"},
            "colour_1":  {"user_id": "4", "username": "u4", "display_name": "Dave"},
            "host":      {"user_id": "5", "username": "u5", "display_name": "Eve"},
        }

    def _match(self):
        return {"division": "Premier", "team_home": "A", "team_away": "B", "match_time": 1700000000}

    def test_shows_all_required_roles(self):
        from cogs.confirm_view import build_confirmation_message
        ra = self._ra()
        confs = {"1": None, "2": None, "3": None, "4": None}
        content = build_confirmation_message(self._match(), ra, confs)
        assert "Alice" in content
        assert "Bob" in content
        assert "Carol" in content
        assert "Dave" in content
        assert "No Response" in content

    def test_shows_optional_host_without_status(self):
        from cogs.confirm_view import build_confirmation_message
        ra = self._ra()
        confs = {"1": None, "2": None, "3": None, "4": None}
        content = build_confirmation_message(self._match(), ra, confs)
        # Host is optional — now shows a status tag like required roles
        lines = [l for l in content.splitlines() if "Eve" in l]
        assert lines
        assert "[No Response]" in lines[0]
        assert "optional" in lines[0].lower()

    def test_shows_single_pbp_and_colour(self):
        from cogs.confirm_view import build_confirmation_message
        ra = {
            "producer":  {"user_id": "1", "username": "u1", "display_name": "Alice"},
            "observer":  {"user_id": "2", "username": "u2", "display_name": "Bob"},
            "pbp_1":     {"user_id": "3", "username": "u3", "display_name": "Carol"},
            "colour_1":  {"user_id": "4", "username": "u4", "display_name": "Dave"},
        }
        confs = {"1": None, "2": None, "3": None, "4": None}
        content = build_confirmation_message(self._match(), ra, confs)
        assert "Carol" in content
        assert "Dave" in content

    def test_awaiting_deduplicates_same_user(self):
        from cogs.confirm_view import build_confirmation_message
        # u1 fills both producer and observer — should appear once in awaiting
        ra = {
            "producer":  {"user_id": "1", "username": "u1", "display_name": "Alice"},
            "observer":  {"user_id": "1", "username": "u1", "display_name": "Alice"},
            "pbp_1":     {"user_id": "2", "username": "u2", "display_name": "Bob"},
            "colour_1":  {"user_id": "3", "username": "u3", "display_name": "Carol"},
        }
        confs = {"1": None, "2": True, "3": True}
        content = build_confirmation_message(self._match(), ra, confs)
        # <@1> should appear only once in the awaiting section
        awaiting_section = content.split("Awaiting")[-1] if "Awaiting" in content else ""
        assert awaiting_section.count("<@1>") == 1

    def test_ready_status_shown(self):
        from cogs.confirm_view import build_confirmation_message
        ra = self._ra()
        confs = {"1": True, "2": None, "3": None, "4": None}
        content = build_confirmation_message(self._match(), ra, confs)
        assert "[Ready]" in content


class TestOptionalRendering:
    def _match(self):
        return {"division": "D1", "team_home": "A", "team_away": "B",
                "match_time": 1700000000}

    def test_optional_shows_status_tag_and_awaits(self):
        from cogs.confirm_view import build_confirmation_message
        ra = {
            "producer":  {"user_id": "1", "username": "u1", "display_name": "P"},
            "observer":  {"user_id": "1", "username": "u1", "display_name": "P"},
            "pbp_1":     {"user_id": "2", "username": "u2", "display_name": "PB"},
            "colour_1":  {"user_id": "3", "username": "u3", "display_name": "C"},
            "host":      {"user_id": "8", "username": "u8", "display_name": "H8"},
            "analyst_1": {"user_id": "9", "username": "u9", "display_name": "A9"},
        }
        confs = {"1": True, "2": True, "3": True, "8": None, "9": False}
        content = build_confirmation_message(self._match(), ra, confs)
        assert "**Host** (optional): <@8> — H8 [No Response]" in content
        assert "**Analyst** (optional): <@9> — A9 [Rejected]" in content
        awaiting = content.split("Awaiting confirmation from:")[-1]
        assert "<@8>" in awaiting
        assert "<@9>" not in awaiting


class TestBuildTalentDescription:
    def test_required_roles_displayed(self):
        from cogs.talent import build_talent_description_from_assignments
        ra = {
            "producer":  {"user_id": "1", "username": "u1", "display_name": "Alice"},
            "observer":  {"user_id": "2", "username": "u2", "display_name": "Bob"},
            "pbp_1":     {"user_id": "3", "username": "u3", "display_name": "Carol"},
            "colour_1":  {"user_id": "4", "username": "u4", "display_name": "Dave"},
        }
        desc = build_talent_description_from_assignments(ra)
        assert "Alice" in desc
        assert "Carol" in desc
        assert "Dave" in desc
        assert "Play-by-Play" in desc
        assert "Colour Caster" in desc

    def test_missing_slots_omitted(self):
        from cogs.talent import build_talent_description_from_assignments
        ra = {
            "producer": {"user_id": "1", "username": "u1", "display_name": "Alice"},
            "pbp_1":    {"user_id": "2", "username": "u2", "display_name": "Bob"},
        }
        desc = build_talent_description_from_assignments(ra)
        assert "Alice" in desc
        assert "Bob" in desc
        # colour and observer not in ra — should not appear
        assert "Colour" not in desc
        assert "Observer" not in desc


class TestGetRequiredUserIds:
    def test_collects_four_required_roles(self):
        from cogs.talent import _get_required_user_ids
        ra = {
            "producer":  {"user_id": "1", "username": "u1", "display_name": "A"},
            "observer":  {"user_id": "2", "username": "u2", "display_name": "B"},
            "pbp_1":     {"user_id": "3", "username": "u3", "display_name": "C"},
            "colour_1":  {"user_id": "4", "username": "u4", "display_name": "D"},
        }
        ids = _get_required_user_ids(ra)
        assert ids == {"1", "2", "3", "4"}

    def test_excludes_optional_roles(self):
        from cogs.talent import _get_required_user_ids
        ra = {
            "producer": {"user_id": "1", "username": "u1", "display_name": "A"},
            "pbp_1":    {"user_id": "2", "username": "u2", "display_name": "B"},
            "host":     {"user_id": "9", "username": "u9", "display_name": "Z"},
        }
        ids = _get_required_user_ids(ra)
        assert "9" not in ids


# ===========================================================================
# M9 — cancel_broadcast
# ===========================================================================

class TestCancelBroadcast:
    def _view(self, db, match):
        return _make_phase1_view(db, match, signups=[])

    async def test_cancel_deletes_teamup_event(self, db):
        match_id = _insert_match(db)
        db.update_match_teamup_id(match_id, "evt_del")
        db.increment_scheduled_count("Team A")
        db.increment_scheduled_count("Team B")
        match = db.get_match(match_id)

        teamup = MagicMock()
        view = self._view(db, match)
        view.get_teamup = lambda: teamup

        interaction = _make_interaction(db, user_id="1", is_admin=True)
        interaction.client.get_channel.return_value = None

        await view.cancel_broadcast(interaction)

        teamup.delete_event.assert_called_once_with("evt_del")
        assert db.get_match(match_id)["teamup_event_id"] is None

    async def test_cancel_resets_allocation(self, db):
        match_id = _insert_match(db)
        db.create_allocation(match_id)
        db.set_allocation_status(match_id, "sent")
        match = db.get_match(match_id)

        view = self._view(db, match)
        interaction = _make_interaction(db, user_id="1", is_admin=True)
        interaction.client.get_channel.return_value = None

        await view.cancel_broadcast(interaction)

        alloc = db.get_allocation(match_id)
        assert alloc is None or alloc["status"] in ("pending", "")

    async def test_cancel_edits_signup_message(self, db):
        match_id = _insert_match(db)
        db.insert_broadcast_message(match_id, "12345", "99")  # numeric message id
        db.set_config("signup_channel_id", "99")
        match = db.get_match(match_id)

        signup_msg = AsyncMock()
        signup_ch  = AsyncMock()
        signup_ch.fetch_message.return_value = signup_msg

        view = self._view(db, match)
        interaction = _make_interaction(db, user_id="1", is_admin=True)
        interaction.client.get_channel.return_value = signup_ch

        await view.cancel_broadcast(interaction)

        signup_msg.edit.assert_called_once()
        content = signup_msg.edit.call_args[1]["content"]
        assert "CANCELLED" in content


# ===========================================================================
# cancel_orphaned_confirmation helper + stale-click safety net
# ===========================================================================

class TestCancelOrphanedConfirmation:
    """The helper edits the active confirmation message into a cancelled state
    BEFORE reset_allocation/delete_match_cascade wipes the message ID."""

    async def test_edits_message_when_confirmation_present(self, db):
        from cogs.confirm_view import cancel_orphaned_confirmation
        match_id = _insert_match(db)
        ra = _role_assignments("1", "2", "3")
        _setup_allocation(db, match_id, ra,
                          conf_msg_id="80001", conf_ch_id="888")

        fetched_msg = AsyncMock()
        fetched_msg.content = "ORIGINAL"
        fetched_msg.edit = AsyncMock()
        channel = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=fetched_msg)
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=channel)

        await cancel_orphaned_confirmation(bot, db, match_id, reason="test reason")

        bot.get_channel.assert_called_once_with(888)
        channel.fetch_message.assert_awaited_once_with(80001)
        fetched_msg.edit.assert_awaited_once()
        new_content = fetched_msg.edit.call_args[1]["content"]
        assert new_content.startswith("ORIGINAL")
        assert "Talent confirmation cancelled" in new_content
        assert "test reason" in new_content

    async def test_no_op_when_confirmation_message_id_missing(self, db):
        """After reset_allocation, conf_msg_id is NULL — helper must not crash."""
        from cogs.confirm_view import cancel_orphaned_confirmation
        match_id = _insert_match(db)
        db.create_allocation(match_id)  # no confirmation_message_id set

        bot = MagicMock()
        bot.get_channel = MagicMock()

        await cancel_orphaned_confirmation(bot, db, match_id)

        bot.get_channel.assert_not_called()

    async def test_no_op_when_no_allocation_row(self, db):
        from cogs.confirm_view import cancel_orphaned_confirmation
        bot = MagicMock()
        bot.get_channel = MagicMock()

        await cancel_orphaned_confirmation(bot, db, match_id=99999)

        bot.get_channel.assert_not_called()

    async def test_no_op_when_channel_uncached(self, db):
        """get_channel returning None (cache miss) is not an error."""
        from cogs.confirm_view import cancel_orphaned_confirmation
        match_id = _insert_match(db)
        ra = _role_assignments("1", "2", "3")
        _setup_allocation(db, match_id, ra,
                          conf_msg_id="80002", conf_ch_id="999")

        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=None)

        await cancel_orphaned_confirmation(bot, db, match_id)

        bot.get_channel.assert_called_once_with(999)
        # No exception raised


class TestStaleClickSafetyNet:
    """When Ready/Reject hits the 'no longer active' branch, the message is
    edited in place so subsequent talent see the cancellation."""

    async def test_ready_click_on_orphaned_alloc_edits_message(self, db):
        from cogs.confirm_view import ConfirmationView, ReadyButton
        match_id = _insert_match(db)
        # No allocation row at all → "no longer active" branch
        view = ConfirmationView(match_id)
        button = next(c for c in view.children if isinstance(c, ReadyButton))

        interaction = _make_interaction(db, user_id="1", msg_id="no_such",
                                         msg_content="ORIGINAL CONTENT")
        interaction.message = AsyncMock()
        interaction.message.id = "no_such"
        interaction.message.content = "ORIGINAL CONTENT"

        await button.callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        assert "no longer active" in interaction.response.send_message.call_args[0][0]
        interaction.message.edit.assert_awaited_once()
        new_content = interaction.message.edit.call_args[1]["content"]
        assert new_content.startswith("ORIGINAL CONTENT")
        assert "Talent confirmation cancelled" in new_content

    async def test_reject_click_on_orphaned_alloc_edits_message(self, db):
        from cogs.confirm_view import ConfirmationView, RejectButton
        match_id = _insert_match(db)
        view = ConfirmationView(match_id)
        button = next(c for c in view.children if isinstance(c, RejectButton))

        interaction = _make_interaction(db, user_id="1", msg_id="no_such",
                                         msg_content="ORIGINAL CONTENT")
        interaction.message = AsyncMock()
        interaction.message.id = "no_such"
        interaction.message.content = "ORIGINAL CONTENT"

        await button.callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        interaction.message.edit.assert_awaited_once()
        assert "Talent confirmation cancelled" in interaction.message.edit.call_args[1]["content"]

    async def test_safety_net_idempotent_does_not_double_append(self, db):
        """If the message has already been cleaned up, don't append again."""
        from cogs.confirm_view import _stale_message_cleanup
        suffix = "⏏️ **Talent confirmation cancelled** — this broadcast is no longer scheduled."
        interaction = MagicMock()
        interaction.message = AsyncMock()
        interaction.message.content = "ORIGINAL\n\n" + suffix
        interaction.message.edit = AsyncMock()

        await _stale_message_cleanup(interaction)

        interaction.message.edit.assert_not_called()


# ===========================================================================
# UnavailableButton — talent counter behaviour
# ===========================================================================

class TestUnavailableButton:
    def _button_and_view(self, match_id):
        from cogs.signup import SignUpView, UnavailableButton
        view = SignUpView(match_id)
        button = next(c for c in view.children if isinstance(c, UnavailableButton))
        return button, view

    def _interaction(self, db_instance, user_id="1", display_name="Alice"):
        interaction = _make_interaction(db_instance, user_id=user_id, msg_id="sm_1")
        member = MagicMock()
        member.display_name = display_name
        member.__str__ = lambda self: f"user#{user_id}"
        interaction.guild.fetch_member = AsyncMock(return_value=member)
        return interaction

    async def test_unavailable_click_does_not_increment_response_count(self, db):
        """Regression: clicking Unavailable on a fresh match must NOT add
        to response_count — only unavailable_count should go up."""
        match_id = _insert_match(db)
        db.insert_broadcast_message(match_id, "sm_1", "999")

        button, _view = self._button_and_view(match_id)
        interaction = self._interaction(db, user_id="1", display_name="Alice")

        await button.callback(interaction)

        row = next(r for r in db.get_all_talent() if r["user_id"] == "1")
        assert row["unavailable_count"] == 1
        assert row["response_count"] == 0

    async def test_unavailable_toggle_off_does_not_increment(self, db):
        """Clicking Unavailable twice (toggle off) must not keep adding to
        unavailable_count on the un-toggle click."""
        match_id = _insert_match(db)
        db.insert_broadcast_message(match_id, "sm_1", "999")

        button, _view = self._button_and_view(match_id)
        interaction = self._interaction(db, user_id="1", display_name="Alice")

        await button.callback(interaction)
        await button.callback(interaction)

        row = next(r for r in db.get_all_talent() if r["user_id"] == "1")
        assert row["unavailable_count"] == 1
        assert row["response_count"] == 0


# ===========================================================================
# send_allocation_request — filter out 'unavailable' signups
# ===========================================================================

class TestSendAllocationRequestFiltersUnavailable:
    async def test_unavailable_signups_excluded_from_allocation_view(self, db):
        """Users who clicked Unavailable must not appear as options in the
        AllocationView sent to the log channel."""
        from cogs.talent import send_allocation_request, AllocationView
        match_id = _insert_match(db)
        # One normal role signup and one unavailable signup
        db.upsert_signup(match_id, "sm_1", "producer", "1", "u1", "Alice")
        db.upsert_signup(match_id, "sm_1", "unavailable", "2", "u2", "Bob")

        log_ch = AsyncMock()
        log_ch.send = AsyncMock(return_value=MagicMock(id=999))
        match = db.get_match(match_id)

        await send_allocation_request(db, match, log_ch, AsyncMock())

        log_ch.send.assert_called_once()
        view_arg = log_ch.send.call_args[1].get("view") or log_ch.send.call_args[0][1]
        assert isinstance(view_arg, AllocationView)
        signup_user_ids = {s["user_id"] for s in view_arg.signups}
        assert "1" in signup_user_ids
        assert "2" not in signup_user_ids

    async def test_allocation_view_built_with_filtered_signups_excludes_unavailable(self, db):
        """AllocationView constructed via send_allocation_request receives
        signups with 'unavailable' rows filtered out."""
        from cogs.talent import send_allocation_request, AllocationView
        match_id = _insert_match(db)
        for role, uid, name in [("producer", "1", "Alice"),
                                 ("observer", "2", "Bob"),
                                 ("unavailable", "3", "Carol"),
                                 ("pbp", "4", "Dave")]:
            db.upsert_signup(match_id, "sm_1", role, uid, f"u{uid}", name)

        log_ch = AsyncMock()
        log_ch.send = AsyncMock(return_value=MagicMock(id=999))
        match = db.get_match(match_id)

        await send_allocation_request(db, match, log_ch, AsyncMock())

        view_arg = log_ch.send.call_args[1].get("view") or log_ch.send.call_args[0][1]
        assert isinstance(view_arg, AllocationView)
        ids_in_view = {s["user_id"] for s in view_arg.signups}
        assert ids_in_view == {"1", "2", "4"}
        assert "Carol" not in log_ch.send.call_args[0][0]


# ===========================================================================
# H4 — _NewMatchSelect replacement flow
# ===========================================================================

class TestNewMatchSelect:
    def _select(self, current_match_id, replacements, db):
        from cogs.signup import _NewMatchSelect
        select = _NewMatchSelect(current_match_id, replacements, db)
        return select

    def _set_values(self, select, values):
        """discord.ui.Select.values is read-only; set the internal _values attribute."""
        select._values = values

    async def test_replacement_already_scheduled_sends_error(self, db):
        mid1 = _insert_match(db, TS_8PM,  home="Team A", away="Team B")
        mid2 = _insert_match(db, TS_10PM, home="Team C", away="Team D")
        db.update_match_teamup_id(mid1, "evt_1")
        db.update_match_teamup_id(mid2, "evt_2")   # already scheduled

        select = self._select(mid1, [db.get_match(mid2)], db)
        self._set_values(select, [str(mid2)])

        interaction = _make_interaction(db, user_id="1")
        interaction.client.get_teamup.return_value = None

        await select.callback(interaction)

        interaction.response.edit_message.assert_called_once()
        content = interaction.response.edit_message.call_args[1].get("content", "")
        assert "already scheduled" in content.lower()

    async def test_replacement_clears_old_match_event_id(self, db):
        mid1 = _insert_match(db, TS_8PM,  home="Team A", away="Team B")
        mid2 = _insert_match(db, TS_10PM, home="Team C", away="Team D")
        db.update_match_teamup_id(mid1, "evt_old")
        db.increment_scheduled_count("Team A")
        db.increment_scheduled_count("Team B")

        teamup = MagicMock()
        select = self._select(mid1, [db.get_match(mid2)], db)
        self._set_values(select, [str(mid2)])

        interaction = _make_interaction(db, user_id="1")
        interaction.client.get_teamup.return_value = teamup
        interaction.client.get_channel.return_value = None

        with patch("scheduler.accept_combination", new_callable=AsyncMock):
            await select.callback(interaction)

        assert db.get_match(mid1)["teamup_event_id"] is None
        teamup.delete_event.assert_called_once_with("evt_old")

    async def test_accepted_match_clears_broadcast_accepted_flag(self, db):
        mid1 = _insert_match(db, TS_8PM,  home="Team A", away="Team B")
        mid2 = _insert_match(db, TS_10PM, home="Team C", away="Team D")
        db.update_match_teamup_id(mid1, "evt_acc")
        db.mark_broadcast_accepted(mid1)
        db.create_allocation(mid1)
        db.set_allocation_status(mid1, "accepted")

        select = self._select(mid1, [db.get_match(mid2)], db)
        self._set_values(select, [str(mid2)])

        interaction = _make_interaction(db, user_id="1")
        interaction.client.get_teamup.return_value = MagicMock()
        interaction.client.get_channel.return_value = None

        with patch("scheduler.accept_combination", new_callable=AsyncMock):
            await select.callback(interaction)

        assert not db.get_match(mid1).get("broadcast_accepted")

    async def test_replacement_calls_accept_combination_for_new_match(self, db):
        mid1 = _insert_match(db, TS_8PM,  home="Team A", away="Team B")
        mid2 = _insert_match(db, TS_10PM, home="Team C", away="Team D")
        db.update_match_teamup_id(mid1, "evt_1")

        select = self._select(mid1, [db.get_match(mid2)], db)
        self._set_values(select, [str(mid2)])

        interaction = _make_interaction(db, user_id="1")
        interaction.client.get_teamup.return_value = MagicMock()
        interaction.client.get_channel.return_value = None

        with patch("scheduler.accept_combination", new_callable=AsyncMock) as mock_accept:
            await select.callback(interaction)

        mock_accept.assert_called_once()
        assert mock_accept.call_args[0][0][0]["id"] == mid2

    async def test_accepted_replacement_pings_talent(self, db):
        mid1 = _insert_match(db, TS_8PM,  home="Team A", away="Team B")
        mid2 = _insert_match(db, TS_10PM, home="Team C", away="Team D")
        db.update_match_teamup_id(mid1, "evt_acc2")
        db.mark_broadcast_accepted(mid1)
        db.set_config("signup_channel_id", "99")

        ra = _role_assignments("1", "2", "3")
        db.create_allocation(mid1)
        db.set_allocation_assignments(mid1, ra, {}, None, None)
        db.set_allocation_status(mid1, "accepted")

        signup_ch = AsyncMock()
        select = self._select(mid1, [db.get_match(mid2)], db)
        self._set_values(select, [str(mid2)])

        interaction = _make_interaction(db, user_id="1")
        interaction.client.get_teamup.return_value = MagicMock()
        interaction.client.get_channel.return_value = signup_ch

        with patch("scheduler.accept_combination", new_callable=AsyncMock):
            await select.callback(interaction)

        # A ping with @mentions should be sent
        ping_calls = [c for c in signup_ch.send.call_args_list if "<@" in str(c)]
        assert len(ping_calls) >= 1


# ===========================================================================
# Optional roles tracked in confirmations but never gate finalization
# ===========================================================================

class TestOptionalDoesNotGate:
    def _ready(self, match_id):
        from cogs.confirm_view import ConfirmationView, ReadyButton
        v = ConfirmationView(match_id)
        return next(c for c in v.children if isinstance(c, ReadyButton))

    async def test_all_required_ready_finalizes_with_optional_pending(self, db):
        match_id = _insert_match(db)
        ra = _role_assignments("1", "2", "3")
        ra["host"] = {"user_id": "9", "username": "u9", "display_name": "Host9"}
        _setup_allocation(db, match_id, ra, conf_msg_id="C1")
        db.set_confirmation(match_id, "9", None)
        db.set_confirmation(match_id, "2", True)
        db.set_confirmation(match_id, "3", True)
        button = self._ready(match_id)
        interaction = _make_interaction(db, user_id="1", msg_id="C1")
        with patch("cogs.confirm_view._finalize_match", new_callable=AsyncMock) as fin:
            await button.callback(interaction)
        fin.assert_called_once()

    async def test_required_pending_blocks_even_if_optional_ready(self, db):
        match_id = _insert_match(db)
        ra = _role_assignments("1", "2", "3")
        ra["analyst_1"] = {"user_id": "9", "username": "u9", "display_name": "An9"}
        _setup_allocation(db, match_id, ra, conf_msg_id="C2")
        db.set_confirmation(match_id, "9", True)
        button = self._ready(match_id)
        interaction = _make_interaction(db, user_id="1", msg_id="C2")
        with patch("cogs.confirm_view._finalize_match", new_callable=AsyncMock) as fin:
            await button.callback(interaction)
        fin.assert_not_called()


# ===========================================================================
# Task 7 — replace_allocation_role
# ===========================================================================

class TestReplaceAllocationRole:
    async def test_swap_resets_only_new_person_keeps_others(self, db):
        from cogs.talent import replace_allocation_role
        match_id = _insert_match(db)
        ra = _role_assignments("1", "2", "3")
        _setup_allocation(db, match_id, ra, conf_msg_id="7001", conf_ch_id="900")
        db.set_confirmation(match_id, "1", True)
        db.set_confirmation(match_id, "2", True)
        db.set_confirmation(match_id, "3", True)
        db.set_allocation_status(match_id, "accepted")

        ch = AsyncMock()
        fetched = AsyncMock(); fetched.content = "OLD"; fetched.edit = AsyncMock()
        ch.fetch_message = AsyncMock(return_value=fetched)
        ch.send = AsyncMock()
        bot = MagicMock(); bot.db = db
        bot.get_channel = MagicMock(return_value=ch)

        new_person = {"user_id": "7", "username": "u7", "display_name": "New7"}
        await replace_allocation_role(bot, db, match_id, "colour_1", new_person)

        import json
        a = db.get_allocation(match_id)
        ra2 = json.loads(a["role_assignments"])
        confs = json.loads(a["confirmations"])
        assert ra2["colour_1"]["user_id"] == "7"
        assert confs["7"] is None
        assert confs["1"] is True
        assert confs["2"] is True
        assert "3" not in confs
        assert a["status"] == "accepted"
        fetched.edit.assert_awaited()
        ch.send.assert_awaited()
        ping = ch.send.call_args
        assert "<@7>" in str(ping)

    async def test_outgoing_kept_if_holds_other_role(self, db):
        from cogs.talent import replace_allocation_role
        match_id = _insert_match(db)
        ra = _role_assignments("1", "2", "3")  # u1 = producer AND observer
        _setup_allocation(db, match_id, ra, conf_msg_id="7002", conf_ch_id="900")
        db.set_confirmation(match_id, "1", True)
        bot = MagicMock(); bot.db = db
        ch = AsyncMock()
        ch.fetch_message = AsyncMock(return_value=AsyncMock(content="x", edit=AsyncMock()))
        ch.send = AsyncMock()
        bot.get_channel = MagicMock(return_value=ch)

        await replace_allocation_role(bot, db, match_id, "observer",
                                      {"user_id": "5", "username": "u5", "display_name": "F5"})
        import json
        confs = json.loads(db.get_allocation(match_id)["confirmations"])
        assert confs["1"] is True   # u1 still producer -> confirmation kept
        assert confs["5"] is None


# ===========================================================================
# Task 8 — ReplaceRoleView
# ===========================================================================

class TestReplaceRoleView:
    def _view(self, db, match_id, signups, role_key=None):
        from cogs.talent import ReplaceRoleView
        return ReplaceRoleView(match_id, db, signups, preselect_role=role_key)

    async def test_apply_calls_replace_with_selected_role_and_user(self, db):
        from cogs.talent import ReplaceRoleView, _ReplaceApplyButton
        match_id = _insert_match(db)
        ra = _role_assignments("1", "2", "3")
        _setup_allocation(db, match_id, ra, conf_msg_id="V1", conf_ch_id="900")
        db.upsert_signup(match_id, "m", "colour", "7", "u7", "New7")
        signups = db.get_signups_for_match(match_id)

        view = self._view(db, match_id, signups, role_key="colour_1")
        view.selected_role = "colour_1"
        view.selected_user = "7"
        button = next(c for c in view.children if isinstance(c, _ReplaceApplyButton))
        interaction = _make_interaction(db, user_id="1", is_admin=True)
        interaction.guild = MagicMock()

        with patch("cogs.talent.replace_allocation_role",
                   new_callable=AsyncMock) as rep:
            await button.callback(interaction)

        rep.assert_awaited_once()
        args = rep.await_args[0]
        assert args[2] == match_id and args[3] == "colour_1"
        assert args[4]["user_id"] == "7"

    async def test_apply_denied_for_non_manager(self, db):
        from cogs.talent import _ReplaceApplyButton
        match_id = _insert_match(db)
        _setup_allocation(db, match_id, _role_assignments("1", "2", "3"))
        view = self._view(db, match_id, [])
        button = next(c for c in view.children if isinstance(c, _ReplaceApplyButton))
        interaction = _make_interaction(db, user_id="1", is_admin=False)
        interaction.guild = MagicMock()
        db.is_manager = MagicMock(return_value=False)
        await button.callback(interaction)
        interaction.response.send_message.assert_called_once()
        assert "manager" in interaction.response.send_message.call_args[0][0].lower()
