# Allocation Edit, Carry-Over & Optional-Role Confirmations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make allocation changes surgical — only the person who actually changed gets pinged — and stop discarding still-valid sign-ups/allocation/confirmations when only the opponent changes at the same time.

**Architecture:** One shared single-role-replacement mechanism (`replace_allocation_role` + `ReplaceRoleView` in `cogs/talent.py`) is reused by reject→replace and a new Edit Allocation button. Carry-over is two DB copy helpers invoked from the three match-change paths when `match_time` is identical. The `confirmations` JSON dict is extended to include optional roles while finalization continues to gate on the 4 required roles only. No schema migration.

**Tech Stack:** Python 3.12, discord.py 2.x, SQLite via `database.py`, pytest (in-memory SQLite + mocked Discord).

**Spec:** `docs/superpowers/specs/2026-05-02-allocation-edit-carryover-design.md`

**Project test command:** `python -m pytest tests/ -v` (from `c:\Discord Bot Project`). Full suite currently green at 342 tests.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `database.py` | SQLite wrapper | Add `copy_signups`, `copy_allocation`, `update_allocation_lineup`, `get_scheduled_match_at_time_in_week` |
| `cogs/talent.py` | Allocation domain | Add `replace_allocation_role`, `ReplaceRoleView`, `carry_over_if_same_time`; reuse `_get_required_user_ids`, `_build_all_signup_options` |
| `cogs/confirm_view.py` | Confirmation message + Ready/Reject | Optional-role rendering; required-only finalize gate; `RejectButton` rewrite to single-role replace |
| `cogs/signup.py` | Sign-up views | Add `EditAllocationButton` to `ApprovedSignUpView`; New Match carry-over wiring |
| `cogs/weekly_proposals.py` | Weekly proposals | Proposal-swap carry-over wiring in `_UpdateScheduleButton` |
| `cogs/events.py` | Match post parsing/edit | Same-time-different-teams detection + carry-over |
| `tests/test_database.py` | DB tests | Coverage for the 4 new DB methods |
| `tests/test_view_callbacks.py` | View/callback tests | `replace_allocation_role`, `ReplaceRoleView`, reject→replace, Edit Allocation, finalize gate, optional rendering |
| `tests/test_weekly_proposals.py` | Proposal tests | Proposal-swap carry-over |
| `tests/test_events_cog.py` | Events tests | Match-post-edit same-time carry-over |

**Domain facts the implementer must know:**
- `role_assignments` is a JSON dict on `talent_allocations.role_assignments`. Keys: `producer`, `observer`, `pbp_1`, `colour_1` (required), `host`, `analyst_1` (optional). Each value is `{"user_id": str, "username": str, "display_name": str}`.
- `confirmations` is a JSON dict on `talent_allocations.confirmations`: `{user_id(str): True|False|None}`. Today it holds only required user IDs.
- `talent_allocations.status` values seen in code: `pending`, `sent`, `last_call`, `awaiting_confirm`, `accepted`, `cancelled`.
- `_get_required_user_ids(role_assignments)` (in `cogs/talent.py`) returns the set of user IDs across `producer/observer/pbp_1/colour_1`.
- `_build_all_signup_options(signups, db, include_none=False)` (in `cogs/talent.py`) builds `discord.SelectOption`s, one per distinct user, label `"{display_name} [{n} bcasts] ({role_label})"`, value = `user_id`.
- `_manager_check(interaction, db)` lives in `cogs/signup.py`.
- `set_allocation_assignments(...)` **forces** `status='awaiting_confirm'` — do NOT use it for edits that must preserve `accepted`. That is why Task 3 adds `update_allocation_lineup`.
- All bot-generated messages start with `_SEPARATOR` (imported from `scheduler`).
- Tests use `Database(":memory:")` and mock Discord with `unittest.mock.AsyncMock`/`MagicMock`. `pytest.ini` enables asyncio auto mode (async test functions need no decorator).

---

## Task 1: DB — `copy_signups`

**Files:**
- Modify: `database.py` (add method after `get_signups_for_match`, near line 533)
- Test: `tests/test_database.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_database.py`:

```python
def test_copy_signups_rekeys_rows_to_new_match(db):
    old = db.insert_match(division="D1", week="W1", team_home="A", team_away="B",
                          match_time=1000, posted_at=900)
    new = db.insert_match(division="D1", week="W1", team_home="C", team_away="D",
                          match_time=1000, posted_at=900)
    db.upsert_signup(old, "m1", "producer", "u1", "user1", "User One")
    db.upsert_signup(old, "m1", "unavailable", "u2", "user2", "User Two")

    n = db.copy_signups(old, new)

    assert n == 2
    new_sigs = {(s["role"], s["user_id"]) for s in db.get_signups_for_match(new)}
    assert new_sigs == {("producer", "u1"), ("unavailable", "u2")}
    # old rows untouched
    assert len(db.get_signups_for_match(old)) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_database.py::test_copy_signups_rekeys_rows_to_new_match -v`
Expected: FAIL with `AttributeError: 'Database' object has no attribute 'copy_signups'`

- [ ] **Step 3: Implement**

In `database.py`, immediately after `get_signups_for_match` (ends ~line 533):

```python
    def copy_signups(self, old_match_id: int, new_match_id: int) -> int:
        """Copy all broadcast_signups rows from one match to another. Returns count copied."""
        rows = self.conn.execute(
            "SELECT role, user_id, username, display_name, signed_up_at "
            "FROM broadcast_signups WHERE match_id = ?",
            (old_match_id,)
        ).fetchall()
        for r in rows:
            self.conn.execute(
                "INSERT OR IGNORE INTO broadcast_signups "
                "(match_id, message_id, role, user_id, username, display_name, signed_up_at) "
                "VALUES (?, '', ?, ?, ?, ?, ?)",
                (new_match_id, r["role"], r["user_id"], r["username"],
                 r["display_name"], r["signed_up_at"])
            )
        self.conn.commit()
        return len(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_database.py::test_copy_signups_rekeys_rows_to_new_match -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database.py
git commit -m "feat(db): add copy_signups to re-key sign-ups onto a new match"
```

---

## Task 2: DB — `copy_allocation`

**Files:**
- Modify: `database.py` (add after `reset_allocation`, ~line 789)
- Test: `tests/test_database.py`

- [ ] **Step 1: Write the failing test**

```python
def test_copy_allocation_copies_row_verbatim(db):
    old = db.insert_match(division="D1", week="W1", team_home="A", team_away="B",
                          match_time=1000, posted_at=900)
    new = db.insert_match(division="D1", week="W1", team_home="C", team_away="D",
                          match_time=1000, posted_at=900)
    db.create_allocation(old)
    ra = {"producer": {"user_id": "u1", "username": "user1", "display_name": "U1"}}
    db.set_allocation_assignments(old, ra, {"u1": True}, "msg1", "ch1")
    db.set_allocation_status(old, "accepted")

    db.copy_allocation(old, new)

    a = db.get_allocation(new)
    assert a is not None
    import json
    assert json.loads(a["role_assignments"]) == ra
    assert json.loads(a["confirmations"]) == {"u1": True}
    assert a["status"] == "accepted"
    assert a["confirmation_message_id"] == "msg1"
    assert a["confirmation_channel_id"] == "ch1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_database.py::test_copy_allocation_copies_row_verbatim -v`
Expected: FAIL with `AttributeError: ... 'copy_allocation'`

- [ ] **Step 3: Implement**

In `database.py`, after `reset_allocation` (~line 789):

```python
    def copy_allocation(self, old_match_id: int, new_match_id: int) -> None:
        """Copy the talent_allocations row from one match onto another (upsert)."""
        src = self.conn.execute(
            "SELECT role_assignments, confirmations, status, "
            "confirmation_message_id, confirmation_channel_id, "
            "allocation_message_id, allocation_channel_id "
            "FROM talent_allocations WHERE match_id = ?",
            (old_match_id,)
        ).fetchone()
        if not src:
            return
        now = int(time.time())
        self.conn.execute(
            "INSERT OR IGNORE INTO talent_allocations "
            "(match_id, confirmations, status, created_at, updated_at) "
            "VALUES (?, '{}', 'pending', ?, ?)",
            (new_match_id, now, now)
        )
        self.conn.execute(
            "UPDATE talent_allocations SET role_assignments = ?, confirmations = ?, "
            "status = ?, confirmation_message_id = ?, confirmation_channel_id = ?, "
            "allocation_message_id = ?, allocation_channel_id = ?, updated_at = ? "
            "WHERE match_id = ?",
            (src["role_assignments"], src["confirmations"], src["status"],
             src["confirmation_message_id"], src["confirmation_channel_id"],
             src["allocation_message_id"], src["allocation_channel_id"],
             now, new_match_id)
        )
        self.conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_database.py::test_copy_allocation_copies_row_verbatim -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database.py
git commit -m "feat(db): add copy_allocation to carry an allocation row to a new match"
```

---

## Task 3: DB — `update_allocation_lineup` (status-preserving)

**Files:**
- Modify: `database.py` (add after `set_allocation_assignments`, ~line 748)
- Test: `tests/test_database.py`

- [ ] **Step 1: Write the failing test**

```python
def test_update_allocation_lineup_preserves_status_and_message(db):
    mid = db.insert_match(division="D1", week="W1", team_home="A", team_away="B",
                          match_time=1000, posted_at=900)
    db.create_allocation(mid)
    db.set_allocation_assignments(
        mid, {"producer": {"user_id": "u1", "username": "x", "display_name": "U1"}},
        {"u1": True}, "cmsg", "cch")
    db.set_allocation_status(mid, "accepted")

    new_ra = {"producer": {"user_id": "u9", "username": "y", "display_name": "U9"}}
    db.update_allocation_lineup(mid, new_ra, {"u9": None})

    a = db.get_allocation(mid)
    import json
    assert json.loads(a["role_assignments"]) == new_ra
    assert json.loads(a["confirmations"]) == {"u9": None}
    assert a["status"] == "accepted"            # NOT downgraded
    assert a["confirmation_message_id"] == "cmsg"
    assert a["confirmation_channel_id"] == "cch"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_database.py::test_update_allocation_lineup_preserves_status_and_message -v`
Expected: FAIL with `AttributeError: ... 'update_allocation_lineup'`

- [ ] **Step 3: Implement**

In `database.py`, after `set_allocation_assignments` (~line 748):

```python
    def update_allocation_lineup(self, match_id: int, role_assignments: dict,
                                 confirmations: dict) -> None:
        """Update role_assignments + confirmations only, leaving status,
        confirmation_message_id and channel intact (used by single-role swaps
        so an accepted broadcast is not downgraded)."""
        self.conn.execute(
            "UPDATE talent_allocations SET role_assignments = ?, confirmations = ?, "
            "updated_at = ? WHERE match_id = ?",
            (json.dumps(role_assignments), json.dumps(confirmations),
             int(time.time()), match_id)
        )
        self.conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_database.py::test_update_allocation_lineup_preserves_status_and_message -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database.py
git commit -m "feat(db): add status-preserving update_allocation_lineup"
```

---

## Task 4: DB — `get_scheduled_match_at_time_in_week`

**Files:**
- Modify: `database.py` (add after `get_match_by_teams_in_week`, ~line 291)
- Test: `tests/test_database.py`

- [ ] **Step 1: Write the failing test**

```python
def test_get_scheduled_match_at_time_in_week(db):
    # scheduled (has teamup_event_id) at t=5000
    m1 = db.insert_match(division="D1", week="W1", team_home="A", team_away="B",
                         match_time=5000, posted_at=1)
    db.update_match_teamup_id(m1, "evt1")
    # unscheduled at same time — must be ignored
    db.insert_match(division="D1", week="W1", team_home="C", team_away="D",
                    match_time=5000, posted_at=1)

    found = db.get_scheduled_match_at_time_in_week(5000, 0, 10000)
    assert found is not None and found["id"] == m1

    assert db.get_scheduled_match_at_time_in_week(9999, 0, 10000) is None
    assert db.get_scheduled_match_at_time_in_week(5000, 6000, 10000) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_database.py::test_get_scheduled_match_at_time_in_week -v`
Expected: FAIL with `AttributeError: ... 'get_scheduled_match_at_time_in_week'`

- [ ] **Step 3: Implement**

In `database.py`, after `get_match_by_teams_in_week` (~line 291):

```python
    def get_scheduled_match_at_time_in_week(self, match_time: int,
                                            week_start_ts: int,
                                            week_end_ts: int) -> Optional[dict]:
        """First scheduled (teamup_event_id present) match at exactly match_time
        within the Mon–Sun ET window. Used to detect a same-time opponent swap."""
        row = self.conn.execute(
            "SELECT * FROM matches WHERE match_time = ? "
            "AND match_time >= ? AND match_time <= ? "
            "AND teamup_event_id IS NOT NULL LIMIT 1",
            (match_time, week_start_ts, week_end_ts)
        ).fetchone()
        return dict(row) if row else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_database.py::test_get_scheduled_match_at_time_in_week -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database.py
git commit -m "feat(db): add get_scheduled_match_at_time_in_week for same-time swap detection"
```

---

## Task 5: Confirmations model — include optional users, gate on required only

**Files:**
- Modify: `cogs/talent.py` `_ConfirmButton.callback` (the `required_ids`/`confirmations` block, ~lines 265-266)
- Modify: `cogs/confirm_view.py` `ReadyButton.callback` finalize check (~line 247)
- Test: `tests/test_view_callbacks.py`

Context: today `_ConfirmButton.callback` builds `confirmations = {uid: None for uid in required_ids}`. `ReadyButton` finalizes when `all(v is True for v in fresh_conf.values())`. After this task, optional users are tracked but do not gate.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_view_callbacks.py` (uses existing `db` fixture, `_insert_match`, `_role_assignments`, `_setup_allocation`, `_make_interaction`, `patch`):

```python
class TestOptionalDoesNotGate:
    def _ready(self, match_id):
        from cogs.confirm_view import ConfirmationView, ReadyButton
        v = ConfirmationView(match_id)
        return next(c for c in v.children if isinstance(c, ReadyButton))

    async def test_all_required_ready_finalizes_with_optional_pending(self, db):
        match_id = _insert_match(db)
        ra = _role_assignments("1", "2", "3")
        ra["host"] = {"user_id": "9", "username": "u9", "display_name": "Host9"}
        # confirmations include the optional host as pending
        _setup_allocation(db, match_id, ra, conf_msg_id="C1")
        db.set_confirmation(match_id, "9", None)
        db.set_confirmation(match_id, "2", True)
        db.set_confirmation(match_id, "3", True)
        # u1 is producer+observer; clicking Ready makes all REQUIRED true,
        # host (9) still pending — must still finalize.
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
        db.set_confirmation(match_id, "9", True)   # optional ready
        # only u1 clicks; u2/u3 still pending → required incomplete
        button = self._ready(match_id)
        interaction = _make_interaction(db, user_id="1", msg_id="C2")
        with patch("cogs.confirm_view._finalize_match", new_callable=AsyncMock) as fin:
            await button.callback(interaction)
        fin.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_view_callbacks.py::TestOptionalDoesNotGate -v`
Expected: FAIL (`test_all_required_ready_finalizes_with_optional_pending` fails because the current `all(v is True ...)` check sees host `None` and does not finalize)

- [ ] **Step 3: Implement**

In `cogs/talent.py` `_ConfirmButton.callback`, replace:

```python
        required_ids = _get_required_user_ids(role_assignments)
        confirmations = {uid: None for uid in required_ids}
```

with:

```python
        confirmations: dict = {}
        for _rk in ("producer", "observer", "pbp_1", "colour_1", "host", "analyst_1"):
            _a = role_assignments.get(_rk)
            if isinstance(_a, dict) and _a["user_id"] not in confirmations:
                confirmations[_a["user_id"]] = None
```

In `cogs/confirm_view.py` `ReadyButton.callback`, replace:

```python
        if all(v is True for v in fresh_conf.values()):
```

with:

```python
        from cogs.talent import _get_required_user_ids
        required_ids = _get_required_user_ids(role_assignments)
        if required_ids and all(fresh_conf.get(uid) is True for uid in required_ids):
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_view_callbacks.py::TestOptionalDoesNotGate -v`
Expected: PASS

Run the existing confirm/finalize tests too:
Run: `python -m pytest tests/test_view_callbacks.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add cogs/talent.py cogs/confirm_view.py tests/test_view_callbacks.py
git commit -m "feat: track optional roles in confirmations; gate finalize on required only"
```

---

## Task 6: Optional-role rendering in `build_confirmation_message`

**Files:**
- Modify: `cogs/confirm_view.py` `build_confirmation_message` (~lines 61-95)
- Test: `tests/test_view_callbacks.py`

Goal: optional roles render `[Ready]/[Rejected]/[No Response]` like required, and pending optionals appear in the Awaiting line.

- [ ] **Step 1: Write the failing test**

```python
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
        # pending optional Host (8) appears in Awaiting; rejected Analyst does not
        awaiting = content.split("Awaiting confirmation from:")[-1]
        assert "<@8>" in awaiting
        assert "<@9>" not in awaiting
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_view_callbacks.py::TestOptionalRendering -v`
Expected: FAIL (current code renders optional as `(optional): <@8> — H8` with no tag, and never adds optionals to Awaiting)

- [ ] **Step 3: Implement**

In `cogs/confirm_view.py`, replace the role loop and awaiting loop in `build_confirmation_message` (the block from `for key, label, required in _DISPLAY_ORDER:` through the `if awaiting:` block) with:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_view_callbacks.py::TestOptionalRendering -v`
Expected: PASS

Run the existing confirmation-message tests:
Run: `python -m pytest tests/test_view_callbacks.py -k BuildConfirmationMessage -v`
Expected: PASS (note: `test_shows_optional_host_without_status` asserted old behaviour — update it: change its assertion `assert "No Response" not in lines[0]` to `assert "[No Response]" in lines[0]` and keep `assert "optional" in lines[0].lower()`).

- [ ] **Step 5: Commit**

```bash
git add cogs/confirm_view.py tests/test_view_callbacks.py
git commit -m "feat: render optional-role confirmation status and await pending optionals"
```

---

## Task 7: `replace_allocation_role` helper

**Files:**
- Modify: `cogs/talent.py` (add helper near `send_allocation_request`, end of file)
- Test: `tests/test_view_callbacks.py`

- [ ] **Step 1: Write the failing test**

```python
class TestReplaceAllocationRole:
    async def test_swap_resets_only_new_person_keeps_others(self, db):
        from cogs.talent import replace_allocation_role
        match_id = _insert_match(db)
        ra = _role_assignments("1", "2", "3")  # producer/observer=1, pbp_1=2, colour_1=3
        _setup_allocation(db, match_id, ra, conf_msg_id="K1", conf_ch_id="900")
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
        assert confs["7"] is None          # new person pending
        assert confs["1"] is True          # untouched
        assert confs["2"] is True
        assert "3" not in confs            # outgoing colour caster removed
        assert a["status"] == "accepted"   # NOT downgraded
        fetched.edit.assert_awaited()      # confirmation message rebuilt
        ch.send.assert_awaited()           # targeted ping to new person
        ping = ch.send.call_args
        assert "<@7>" in str(ping)

    async def test_outgoing_kept_if_holds_other_role(self, db):
        from cogs.talent import replace_allocation_role
        match_id = _insert_match(db)
        ra = _role_assignments("1", "2", "3")  # u1 = producer AND observer
        _setup_allocation(db, match_id, ra, conf_msg_id="K2", conf_ch_id="900")
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
        assert confs["1"] is True   # u1 still producer → confirmation kept
        assert confs["5"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_view_callbacks.py::TestReplaceAllocationRole -v`
Expected: FAIL with `ImportError: cannot import name 'replace_allocation_role'`

- [ ] **Step 3: Implement**

In `cogs/talent.py`, add at end of file (after `send_allocation_request`):

```python
_ROLE_LABEL_BY_KEY = {
    "producer": "Producer", "observer": "Observer",
    "pbp_1": "Play-by-Play", "colour_1": "Colour Caster",
    "host": "Host", "analyst_1": "Analyst",
}


async def replace_allocation_role(bot, db, match_id: int, role_key: str,
                                   new_assignment: dict | None) -> None:
    """Swap one role's assignee (or clear an optional role). Resets only the
    incoming person's confirmation; preserves everyone else; does NOT change
    allocation status (an accepted broadcast stays accepted). Edits the
    existing confirmation message in place and pings only the new person."""
    import json
    from cogs.confirm_view import build_confirmation_message

    alloc = db.get_allocation(match_id)
    if not alloc:
        return
    role_assignments = json.loads(alloc.get("role_assignments") or "{}")
    confirmations = json.loads(alloc.get("confirmations") or "{}")

    old = role_assignments.get(role_key)
    if old:
        old_uid = old["user_id"]
        holds_other = any(
            isinstance(a, dict) and a.get("user_id") == old_uid
            for k, a in role_assignments.items() if k != role_key
        )
        if not holds_other:
            confirmations.pop(old_uid, None)

    if new_assignment is None:
        role_assignments.pop(role_key, None)
    else:
        role_assignments[role_key] = new_assignment
        nuid = new_assignment["user_id"]
        if confirmations.get(nuid) is not True:
            confirmations[nuid] = None

    db.update_allocation_lineup(match_id, role_assignments, confirmations)

    match = db.get_match(match_id)
    msg_id = alloc.get("confirmation_message_id")
    ch_id = alloc.get("confirmation_channel_id")
    if not match or not msg_id or not ch_id:
        return
    channel = bot.get_channel(int(ch_id))
    if not channel:
        return
    try:
        msg = await channel.fetch_message(int(msg_id))
        await msg.edit(content=build_confirmation_message(
            match, role_assignments, confirmations))
    except Exception as e:
        log.warning("replace_allocation_role: edit failed for match %s: %s",
                    match_id, e)

    if new_assignment is not None:
        label = _ROLE_LABEL_BY_KEY.get(role_key, role_key)
        ts = match["match_time"]
        try:
            await channel.send(
                f"<@{new_assignment['user_id']}> — you've been assigned "
                f"**{label}** for **[{match['division']}] "
                f"{match['team_home']} vs {match['team_away']}** | <t:{ts}:F>. "
                f"Please confirm on the message above."
            )
        except Exception as e:
            log.warning("replace_allocation_role: ping failed for match %s: %s",
                        match_id, e)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_view_callbacks.py::TestReplaceAllocationRole -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cogs/talent.py tests/test_view_callbacks.py
git commit -m "feat: replace_allocation_role — surgical single-role swap with targeted ping"
```

---

## Task 8: `ReplaceRoleView`

**Files:**
- Modify: `cogs/talent.py` (add view classes after `replace_allocation_role`)
- Test: `tests/test_view_callbacks.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_view_callbacks.py::TestReplaceRoleView -v`
Expected: FAIL with `ImportError: cannot import name 'ReplaceRoleView'`

- [ ] **Step 3: Implement**

In `cogs/talent.py`, after `replace_allocation_role`:

```python
import discord  # already imported at top of file; keep single import there

_REPLACE_ROLE_OPTIONS = [
    ("producer",  "Producer"),
    ("observer",  "Observer"),
    ("pbp_1",     "Play-by-Play"),
    ("colour_1",  "Colour Caster"),
    ("host",      "Host (optional)"),
    ("analyst_1", "Analyst (optional)"),
]
_CLEAR_SENTINEL = "__clear__"


def _replace_is_manager(interaction, db) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    if db.is_manager(str(interaction.user.id)):
        return True
    role_id = db.get_config("manager_role_id")
    if role_id:
        return any(str(r.id) == role_id for r in interaction.user.roles)
    return False


class _ReplaceRoleSelect(discord.ui.Select):
    def __init__(self, preselect_role=None):
        opts = [
            discord.SelectOption(label=lbl, value=key,
                                 default=(key == preselect_role))
            for key, lbl in _REPLACE_ROLE_OPTIONS
        ]
        super().__init__(placeholder="Role to change...", options=opts,
                         min_values=0, max_values=1, row=0)

    async def callback(self, interaction: discord.Interaction):
        self.view.selected_role = self.values[0] if self.values else None
        await interaction.response.defer()


class _ReplaceCandidateSelect(discord.ui.Select):
    def __init__(self, signups, db):
        avail = [s for s in signups if s["role"] != "unavailable"]
        opts = _build_all_signup_options(avail, db)
        if not opts:
            opts = [discord.SelectOption(label="No sign-ups", value="__none__")]
        opts.insert(0, discord.SelectOption(
            label="— Clear (optional roles only) —", value=_CLEAR_SENTINEL))
        super().__init__(placeholder="Replacement...", options=opts[:25],
                         min_values=0, max_values=1, row=1)

    async def callback(self, interaction: discord.Interaction):
        self.view.selected_user = self.values[0] if self.values else None
        await interaction.response.defer()


class _ReplaceApplyButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Apply", style=discord.ButtonStyle.primary, row=2)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not _replace_is_manager(interaction, view.db):
            await interaction.response.send_message(
                "Only managers and administrators can edit the allocation.",
                ephemeral=True)
            return
        role_key = view.selected_role
        sel = view.selected_user
        if not role_key or sel is None:
            await interaction.response.send_message(
                "Pick a role and a replacement first.", ephemeral=True)
            return
        if sel == _CLEAR_SENTINEL:
            if role_key not in ("host", "analyst_1"):
                await interaction.response.send_message(
                    "Only optional roles (Host/Analyst) can be cleared.",
                    ephemeral=True)
                return
            new_assignment = None
        elif sel == "__none__":
            await interaction.response.send_message(
                "No sign-ups available to assign.", ephemeral=True)
            return
        else:
            s = next((x for x in view.signups if x["user_id"] == sel), None)
            if not s:
                await interaction.response.send_message(
                    "That person is no longer available.", ephemeral=True)
                return
            new_assignment = {
                "user_id": s["user_id"], "username": s["username"],
                "display_name": s["display_name"],
            }
        await replace_allocation_role(
            interaction.client, view.db, view.match_id, role_key, new_assignment)
        await interaction.response.send_message(
            f"✅ Updated **{_ROLE_LABEL_BY_KEY.get(role_key, role_key)}**. "
            f"Only the new person was pinged.", ephemeral=True)


class _ReplaceDoneButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Done", style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction: discord.Interaction):
        self.view.stop()
        for c in self.view.children:
            c.disabled = True
        await interaction.response.edit_message(
            content=interaction.message.content + "\n\n☑️ **Allocation edit closed.**",
            view=self.view)


class ReplaceRoleView(discord.ui.View):
    def __init__(self, match_id: int, db, signups: list[dict],
                 preselect_role: str | None = None):
        super().__init__(timeout=86400)
        self.match_id = match_id
        self.db = db
        self.signups = signups
        self.selected_role = preselect_role
        self.selected_user = None
        self.add_item(_ReplaceRoleSelect(preselect_role))
        self.add_item(_ReplaceCandidateSelect(signups, db))
        self.add_item(_ReplaceApplyButton())
        self.add_item(_ReplaceDoneButton())
```

Note: `discord` and `_build_all_signup_options` and `log` are already imported/defined at the top of `cogs/talent.py`; do not duplicate the `import discord` line — remove the inline comment line if it would shadow.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_view_callbacks.py::TestReplaceRoleView -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cogs/talent.py tests/test_view_callbacks.py
git commit -m "feat: ReplaceRoleView — manager single-role swap UI (repeatable)"
```

---

## Task 9: Reject → single-role replace (rewrite `RejectButton`)

**Files:**
- Modify: `cogs/confirm_view.py` `RejectButton.callback` (~lines 267-344)
- Test: `tests/test_view_callbacks.py`

Replaces the "reset_allocation + re-post full AllocationView" behaviour. Required rejecter → post a pre-selected `ReplaceRoleView` to the log channel; optional rejecter → informational flag only. Keeps the existing Unavailable-marking behaviour.

- [ ] **Step 1: Write the failing test**

```python
class TestRejectSingleRoleReplace:
    def _btn(self, match_id):
        from cogs.confirm_view import ConfirmationView, RejectButton
        v = ConfirmationView(match_id)
        return next(c for c in v.children if isinstance(c, RejectButton))

    async def test_required_reject_posts_replace_view_no_reset(self, db):
        match_id = _insert_match(db)
        ra = _role_assignments("1", "2", "3")
        _setup_allocation(db, match_id, ra, conf_msg_id="R1")
        db.set_config("log_channel_id", "111")
        db.set_confirmation(match_id, "2", True)
        log_ch = AsyncMock()
        btn = self._btn(match_id)
        interaction = _make_interaction(db, user_id="3", msg_id="R1")  # colour_1 rejects
        interaction.client.get_channel = MagicMock(return_value=log_ch)

        await btn.callback(interaction)

        # allocation NOT reset (role_assignments still present)
        a = db.get_allocation(match_id)
        assert a["role_assignments"] is not None
        assert a["status"] == "awaiting_confirm"
        # u2's confirmation preserved
        assert db.get_confirmations(match_id).get("2") is True
        # a ReplaceRoleView was sent to the log channel
        from cogs.talent import ReplaceRoleView
        sent_views = [c.kwargs.get("view") for c in log_ch.send.call_args_list]
        assert any(isinstance(v, ReplaceRoleView) for v in sent_views)

    async def test_optional_reject_flags_only(self, db):
        match_id = _insert_match(db)
        ra = _role_assignments("1", "2", "3")
        ra["host"] = {"user_id": "8", "username": "u8", "display_name": "H8"}
        _setup_allocation(db, match_id, ra, conf_msg_id="R2")
        db.set_config("log_channel_id", "111")
        db.set_confirmation(match_id, "8", None)
        log_ch = AsyncMock()
        btn = self._btn(match_id)
        interaction = _make_interaction(db, user_id="8", msg_id="R2")
        interaction.client.get_channel = MagicMock(return_value=log_ch)

        await btn.callback(interaction)

        from cogs.talent import ReplaceRoleView
        sent_views = [c.kwargs.get("view") for c in log_ch.send.call_args_list]
        assert not any(isinstance(v, ReplaceRoleView) for v in sent_views)
        # an informational flag was posted
        assert any("rejected the optional" in str(c).lower()
                   for c in log_ch.send.call_args_list)
        # status unchanged
        assert db.get_allocation(match_id)["status"] == "awaiting_confirm"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_view_callbacks.py::TestRejectSingleRoleReplace -v`
Expected: FAIL (current code calls `reset_allocation` and `send_allocation_request`, so `role_assignments` becomes `None` and no `ReplaceRoleView` is posted)

- [ ] **Step 3: Implement**

In `cogs/confirm_view.py`, replace the body of `RejectButton.callback` from `db.set_confirmation(alloc["match_id"], user_id, False)` to the end of the method with:

```python
        db.set_confirmation(alloc["match_id"], user_id, False)

        match_id = alloc["match_id"]
        match = db.get_match(match_id)
        role_assignments = json.loads(alloc.get("role_assignments") or "{}")

        # Which role did this user hold?
        rejected_key = next(
            (k for k, a in role_assignments.items()
             if isinstance(a, dict) and a.get("user_id") == user_id),
            None,
        )
        decliner = role_assignments.get(rejected_key) if rejected_key else None
        name = decliner["display_name"] if decliner else f"<@{user_id}>"

        fresh_conf = db.get_confirmations(match_id)
        rejected_content = build_confirmation_message(
            match, role_assignments, fresh_conf)

        # Mark the decliner Unavailable so they drop out of candidate lists.
        if decliner:
            bcast = db.get_broadcast_message(match_id)
            signup_message_id = str(bcast["discord_message_id"]) if bcast else ""
            db.remove_all_signups_for_user(match_id, user_id)
            db.upsert_signup(
                match_id=match_id, message_id=signup_message_id,
                role="unavailable", user_id=user_id,
                username=decliner["username"],
                display_name=decliner["display_name"],
            )
            db.increment_talent_unavailable(
                user_id, decliner["username"], decliner["display_name"])

        # Re-render the confirmation message in place ([Rejected] shows). The
        # ConfirmationView buttons stay live for everyone else.
        await interaction.response.edit_message(
            content=rejected_content, view=self.view)

        log_ch_id = db.get_config("log_channel_id")
        log_ch = (interaction.client.get_channel(int(log_ch_id))
                  if log_ch_id else None)
        manager_role_id = db.get_config("manager_role_id")
        mgr = f"<@&{manager_role_id}> " if manager_role_id else ""
        label = _DISPLAY_LABEL.get(rejected_key, rejected_key or "role")
        required = rejected_key in ("producer", "observer", "pbp_1", "colour_1")

        if not log_ch:
            return

        if required:
            from cogs.talent import ReplaceRoleView
            avail = [s for s in db.get_signups_for_match(match_id)
                     if s["role"] != "unavailable"]
            await log_ch.send(
                f"{mgr}❌ **{name}** rejected **{label}** for "
                f"**[{match['division']}] {match['team_home']} vs "
                f"{match['team_away']}**. Pick a replacement — only the new "
                f"person will be pinged.",
                view=ReplaceRoleView(match_id, db, avail,
                                     preselect_role=rejected_key),
            )
        else:
            await log_ch.send(
                f"{mgr}⚠️ **{name}** rejected the optional **{label}** for "
                f"**[{match['division']}] {match['team_home']} vs "
                f"{match['team_away']}**. Use **Edit Allocation** on the "
                f"sign-up message to replace them if needed."
            )
```

Add this module-level constant near the top of `cogs/confirm_view.py` (after `_DISPLAY_ORDER`):

```python
_DISPLAY_LABEL = {k: lbl for k, lbl, _ in _DISPLAY_ORDER}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_view_callbacks.py::TestRejectSingleRoleReplace -v`
Expected: PASS

Then run the existing reject tests and update any that asserted the old reset/re-post behaviour:
Run: `python -m pytest tests/test_view_callbacks.py -k Reject -v`
Expected: PASS — update `TestRejectButton::test_reject_edits_message_and_reopens_allocation` and `test_reject_marks_user_unavailable_and_shows_rejected_tag`: they must no longer assert `send_allocation_request` is called nor that allocation is reset; instead assert the message shows `[Rejected]`, the decliner has an `unavailable` sign-up, `unavailable_count == 1`, and (for a required role with `log_channel_id` set) a `ReplaceRoleView` is posted.

- [ ] **Step 5: Commit**

```bash
git add cogs/confirm_view.py tests/test_view_callbacks.py
git commit -m "feat: reject triggers single-role replace (required) or flag (optional)"
```

---

## Task 10: Edit Allocation button on `ApprovedSignUpView`

**Files:**
- Modify: `cogs/signup.py` (add `EditAllocationButton`; add to `ApprovedSignUpView`)
- Test: `tests/test_view_callbacks.py`

- [ ] **Step 1: Write the failing test**

```python
class TestEditAllocationButton:
    def _btn(self, match_id):
        from cogs.signup import ApprovedSignUpView, EditAllocationButton
        v = ApprovedSignUpView(match_id)
        return next(c for c in v.children if isinstance(c, EditAllocationButton))

    async def test_non_manager_denied(self, db):
        match_id = _insert_match(db)
        btn = self._btn(match_id)
        interaction = _make_interaction(db, user_id="1", is_admin=False)
        interaction.guild = MagicMock()
        db.is_manager = MagicMock(return_value=False)
        await btn.callback(interaction)
        interaction.response.send_message.assert_called_once()
        assert "manager" in interaction.response.send_message.call_args[0][0].lower()

    async def test_manager_posts_replace_view_to_log(self, db):
        match_id = _insert_match(db)
        _setup_allocation(db, match_id, _role_assignments("1", "2", "3"))
        db.set_config("log_channel_id", "111")
        log_ch = AsyncMock()
        btn = self._btn(match_id)
        interaction = _make_interaction(db, user_id="1", is_admin=True)
        interaction.guild = MagicMock()
        interaction.client.get_channel = MagicMock(return_value=log_ch)
        await btn.callback(interaction)
        from cogs.talent import ReplaceRoleView
        sent = [c.kwargs.get("view") for c in log_ch.send.call_args_list]
        assert any(isinstance(v, ReplaceRoleView) for v in sent)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_view_callbacks.py::TestEditAllocationButton -v`
Expected: FAIL with `ImportError: cannot import name 'EditAllocationButton'`

- [ ] **Step 3: Implement**

In `cogs/signup.py`, add this class near `ApprovedSignUpView` (before its class definition):

```python
class EditAllocationButton(discord.ui.Button):
    def __init__(self, match_id: int):
        self.match_id = match_id
        super().__init__(
            label="Edit Allocation",
            style=discord.ButtonStyle.secondary,
            custom_id=f"edit_alloc_{match_id}",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        db = interaction.client.db
        if not _manager_check(interaction, db):
            await interaction.response.send_message(
                "Manager or Administrator permission required.", ephemeral=True
            )
            return
        match = db.get_match(self.match_id)
        if not match:
            await interaction.response.send_message(
                "Match not found.", ephemeral=True)
            return
        log_ch_id = db.get_config("log_channel_id")
        log_ch = interaction.client.get_channel(int(log_ch_id)) if log_ch_id else None
        if not log_ch:
            await interaction.response.send_message(
                "Log channel not configured.", ephemeral=True)
            return
        from cogs.talent import ReplaceRoleView
        avail = [s for s in db.get_signups_for_match(self.match_id)
                 if s["role"] != "unavailable"]
        await log_ch.send(
            f"✏️ **Editing allocation** — **[{match['division']}] "
            f"{match['team_home']} vs {match['team_away']}** | "
            f"<t:{match['match_time']}:F>\n"
            f"Pick a role and a replacement. Only the changed person is pinged.",
            view=ReplaceRoleView(self.match_id, db, avail),
        )
        await interaction.response.send_message(
            "Allocation editor posted in the log channel.", ephemeral=True)
```

Then modify `ApprovedSignUpView.__init__` to add the button:

```python
class ApprovedSignUpView(discord.ui.View):
    """Persistent view shown on sign-up messages after talent confirmation is complete."""
    def __init__(self, match_id: int):
        from cogs.threads import CreateThreadButton
        super().__init__(timeout=None)
        self.add_item(NewMatchButton(match_id))
        self.add_item(BlockDayButton(match_id))
        self.add_item(CreateThreadButton(match_id))
        self.add_item(EditAllocationButton(match_id))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_view_callbacks.py::TestEditAllocationButton -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cogs/signup.py tests/test_view_callbacks.py
git commit -m "feat: Edit Allocation button on approved broadcasts (per-role swap)"
```

---

## Task 11: `carry_over_if_same_time` helper

**Files:**
- Modify: `cogs/talent.py` (add helper after `ReplaceRoleView`)
- Test: `tests/test_view_callbacks.py`

- [ ] **Step 1: Write the failing test**

```python
class TestCarryOverIfSameTime:
    async def test_same_time_copies_signups_and_allocation(self, db):
        from cogs.talent import carry_over_if_same_time
        old = _insert_match(db)  # default match_time from helper (TS_8PM)
        new = db.insert_match(division="Premier", week="Week 1",
                              team_home="C", team_away="D",
                              match_time=db.get_match(old)["match_time"],
                              posted_at=1)
        db.upsert_signup(old, "m", "producer", "1", "u1", "U1")
        db.create_allocation(old)
        db.set_allocation_status(old, "accepted")
        bot = MagicMock(); bot.db = db
        bot.get_channel = MagicMock(return_value=None)

        note = await carry_over_if_same_time(bot, db, old, new)

        assert note is not None
        assert {s["user_id"] for s in db.get_signups_for_match(new)} == {"1"}
        assert db.get_allocation(new)["status"] == "accepted"

    async def test_different_time_returns_none_and_copies_nothing(self, db):
        from cogs.talent import carry_over_if_same_time
        old = _insert_match(db)
        new = db.insert_match(division="Premier", week="Week 1",
                              team_home="C", team_away="D",
                              match_time=db.get_match(old)["match_time"] + 3600,
                              posted_at=1)
        db.upsert_signup(old, "m", "producer", "1", "u1", "U1")
        bot = MagicMock(); bot.db = db
        note = await carry_over_if_same_time(bot, db, old, new)
        assert note is None
        assert db.get_signups_for_match(new) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_view_callbacks.py::TestCarryOverIfSameTime -v`
Expected: FAIL with `ImportError: cannot import name 'carry_over_if_same_time'`

- [ ] **Step 3: Implement**

In `cogs/talent.py`, after `ReplaceRoleView`:

```python
async def carry_over_if_same_time(bot, db, old_match_id: int,
                                  new_match_id: int) -> str | None:
    """If old and new match share match_time, copy sign-ups (and the
    allocation when one exists) onto the new match so talent are not
    re-pinged. Returns a short status note, or None when times differ
    (caller falls back to fresh-start behaviour)."""
    old = db.get_match(old_match_id)
    new = db.get_match(new_match_id)
    if not old or not new or old["match_time"] != new["match_time"]:
        return None

    db.copy_signups(old_match_id, new_match_id)
    alloc = db.get_allocation(old_match_id)
    if not alloc:
        return "Sign-ups carried over (same time slot)."

    db.copy_allocation(old_match_id, new_match_id)
    status = alloc.get("status")

    if status in ("awaiting_confirm", "accepted"):
        import json
        ra = json.loads(alloc.get("role_assignments") or "{}")
        confs = json.loads(alloc.get("confirmations") or "{}")
        msg_id = alloc.get("confirmation_message_id")
        ch_id = alloc.get("confirmation_channel_id")
        if msg_id and ch_id:
            channel = bot.get_channel(int(ch_id))
            if channel:
                from cogs.confirm_view import build_confirmation_message
                try:
                    msg = await channel.fetch_message(int(msg_id))
                    await msg.edit(content=build_confirmation_message(
                        new, ra, confs))
                except Exception as e:
                    log.warning("carry_over: edit confirmation failed "
                                "for match %s: %s", new_match_id, e)
        updates_ch_id = db.get_config("schedule_updates_channel_id")
        updates_ch = (bot.get_channel(int(updates_ch_id))
                      if updates_ch_id else None)
        if updates_ch:
            try:
                await updates_ch.send(
                    f"♻️ **Opponent changed, same time** — "
                    f"**[{new['division']}] {new['team_home']} vs "
                    f"{new['team_away']}** | <t:{new['match_time']}:F>. "
                    f"Your confirmation still stands — no action needed."
                )
            except Exception as e:
                log.warning("carry_over: notice failed for match %s: %s",
                            new_match_id, e)
        return "Crew + confirmations carried over (same time slot)."

    return "Sign-ups carried over (same time slot)."
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_view_callbacks.py::TestCarryOverIfSameTime -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cogs/talent.py tests/test_view_callbacks.py
git commit -m "feat: carry_over_if_same_time — copy state when only the opponent changes"
```

---

## Task 12: Wire carry-over into proposal slot swap

**Files:**
- Modify: `cogs/weekly_proposals.py` `_UpdateScheduleButton.callback` (the `to_remove`/`to_add` loop, ~lines 360-376)
- Test: `tests/test_weekly_proposals.py`

Context: in `_UpdateScheduleButton.callback`, `to_remove` is the list of old slot match IDs no longer selected and `to_add` is the newly selected ones. Today each `to_add` always gets a fresh `accept_combination` sign-up post. We add: if a removed match shares the exact `match_time` with an added match, carry over instead of fresh.

- [ ] **Step 1: Write the failing test**

```python
async def test_proposal_swap_same_time_carries_signups(db):
    from cogs.weekly_proposals import _UpdateScheduleButton
    # old + new match same time on the proposal day
    ts = MATCH_TS
    old = db.insert_match(division="D1", week="W", team_home="A", team_away="B",
                          match_time=ts, posted_at=1)
    new = db.insert_match(division="D1", week="W", team_home="C", team_away="D",
                          match_time=ts, posted_at=1)
    db.update_match_teamup_id(old, "evtOLD")
    db.create_proposal_message(DATE_STR, DATE_TS, "2099-06-09")
    db.update_proposal_slots(DATE_STR, old, None)
    db.upsert_signup(old, "m", "producer", "u1", "user1", "U1")

    button = _UpdateScheduleButton(DATE_STR)
    interaction = MagicMock()
    interaction.guild = MagicMock()
    interaction.user.guild_permissions.administrator = True
    interaction.client.db = db
    interaction.client.get_teamup.return_value = MagicMock()
    interaction.client.get_channel = MagicMock(return_value=AsyncMock())
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.message = AsyncMock()
    # simulate the manager selecting `new` in slot 1
    interaction.client._proposal_selections = {(DATE_STR, 1): new, (DATE_STR, 2): None}

    with patch("cogs.weekly_proposals.accept_combination",
               new_callable=AsyncMock) as acc, \
         patch("cogs.weekly_proposals._unschedule_match",
               new_callable=AsyncMock):
        await button.callback(interaction)

    # carry-over ran → new match has u1's sign-up, and accept_combination
    # was NOT called for `new` (fresh post skipped)
    assert {s["user_id"] for s in db.get_signups_for_match(new)} == {"u1"}
    assert acc.call_count == 0
```

(If `_proposal_selections` is read differently in the current code, set the slot selection the way the existing `_UpdateScheduleButton` tests do — mirror the closest existing passing test in `tests/test_weekly_proposals.py` for how selections are injected.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_weekly_proposals.py::test_proposal_swap_same_time_carries_signups -v`
Expected: FAIL (`accept_combination` is called for the new match; new match has no sign-ups)

- [ ] **Step 3: Implement**

In `cogs/weekly_proposals.py` `_UpdateScheduleButton.callback`, find the loop:

```python
        for mid in to_remove:
            await _unschedule_match(mid, db, teamup, signup_ch, bot=interaction.client)

        talent_role_id = db.get_config("talent_role_id")
        talent_role_mention = f"<@&{talent_role_id}>" if talent_role_id else ""
        for mid in to_add:
            m = db.get_match(mid)
            if not m:
                continue
            try:
                await accept_combination([m], self.date_str, db, teamup, signup_ch,
                                         talent_role_mention=talent_role_mention)
            except Exception as e:
                log.error("Update Schedule: accept_combination failed for %s: %s", mid, e)
```

Replace it with:

```python
        from cogs.talent import carry_over_if_same_time

        # Pair each added match with a removed match at the same time (if any)
        # so state carries over instead of a fresh sign-up.
        carried_new_ids: set[int] = set()
        for new_mid in list(to_add):
            new_m = db.get_match(new_mid)
            if not new_m:
                continue
            twin = next(
                (rm for rm in to_remove
                 if (db.get_match(rm) or {}).get("match_time") == new_m["match_time"]),
                None,
            )
            if twin is not None:
                # schedule the new match on TeamUp first (reuse accept_combination
                # to create the event + DB rows), then carry old state onto it.
                try:
                    await accept_combination(
                        [new_m], self.date_str, db, teamup, signup_ch,
                        talent_role_mention="")
                except Exception as e:
                    log.error("Update Schedule: accept_combination failed for %s: %s",
                              new_mid, e)
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
```

Note: `carry_over_if_same_time` re-keys the signups and copies the allocation onto the new match's row created by `accept_combination`. Because `accept_combination` runs first the new match has a TeamUp event and an allocation row; `copy_allocation` upserts over it.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_weekly_proposals.py::test_proposal_swap_same_time_carries_signups -v`
Expected: PASS

Run the full proposals suite:
Run: `python -m pytest tests/test_weekly_proposals.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cogs/weekly_proposals.py tests/test_weekly_proposals.py
git commit -m "feat: proposal slot swap carries over state when the time is unchanged"
```

---

## Task 13: Wire carry-over into the New Match button

**Files:**
- Modify: `cogs/signup.py` `_NewMatchSelect.callback` (after the new match is scheduled, before the success edit)
- Test: `tests/test_view_callbacks.py`

Context: `_NewMatchSelect.callback` swaps `current_match_id` for `selected_match_id`. Today the new match always gets a fresh sign-up via `accept_combination`. Add carry-over when times match. Locate the point **after** the new match is scheduled (its `accept_combination` / sign-up post) and call carry-over from the old match.

- [ ] **Step 1: Write the failing test**

```python
class TestNewMatchCarryOver:
    async def test_new_match_same_time_carries_signups(self, db):
        from cogs.signup import _NewMatchSelect
        ts = TS_8PM
        cur = _insert_match(db, ts, home="A", away="B")
        repl = _insert_match(db, ts, home="C", away="D")
        db.update_match_teamup_id(cur, "evtCUR")
        db.upsert_signup(cur, "m", "producer", "u1", "user1", "U1")
        db.set_config("signup_channel_id", "900")

        sel = _NewMatchSelect(cur, [db.get_match(repl)], db)
        sel._values = [str(repl)]
        interaction = _make_interaction(db, user_id="1")
        interaction.client.get_teamup.return_value = MagicMock()
        interaction.client.get_channel = MagicMock(return_value=AsyncMock())

        with patch("scheduler.accept_combination", new_callable=AsyncMock):
            await sel.callback(interaction)

        assert {s["user_id"] for s in db.get_signups_for_match(repl)} == {"u1"}
```

(Mirror the injection style of the existing `TestNewMatchSelect` tests in `tests/test_view_callbacks.py` for `_set_values`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_view_callbacks.py::TestNewMatchCarryOver -v`
Expected: FAIL (replacement match has no sign-ups)

- [ ] **Step 3: Implement**

In `cogs/signup.py` `_NewMatchSelect.callback`, immediately after the block that schedules/posts the sign-up for `selected_match_id` (after its `accept_combination` call and before the final ephemeral/edit confirming success), add:

```python
        try:
            from cogs.talent import carry_over_if_same_time
            await carry_over_if_same_time(
                interaction.client, db, self.current_match_id, selected_match_id)
        except Exception as e:
            log.warning("New Match: carry-over failed (%s → %s): %s",
                        self.current_match_id, selected_match_id, e)
```

If `carry_over_if_same_time` runs and the times match, the fresh sign-up posted by `accept_combination` will be replaced in content on the next edit; that is acceptable (no extra ping). If the implementer finds `accept_combination` is called *before* `current_match` is unscheduled, keep the call site after both the new match is scheduled AND before `current_match`'s allocation is reset, so the source allocation is still readable.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_view_callbacks.py::TestNewMatchCarryOver -v`
Expected: PASS

Run the New Match suite:
Run: `python -m pytest tests/test_view_callbacks.py -k NewMatch -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cogs/signup.py tests/test_view_callbacks.py
git commit -m "feat: New Match button carries over state when the time is unchanged"
```

---

## Task 14: Wire same-time-different-teams detection into match-post edit

**Files:**
- Modify: `cogs/events.py` `on_raw_message_edit` reschedule branch (~lines 199-221)
- Test: `tests/test_events_cog.py`

Context: today `on_raw_message_edit` reparses the edited post, then `get_match_by_teams_in_week` finds an existing same-teams match; if found at a different time it reschedules. Add: when **no** same-teams match exists but a **scheduled** match exists at the **exact same time** that week with **different teams**, treat it as a same-slot opponent swap — insert the new match, then `carry_over_if_same_time` from the scheduled match, then unschedule the old one via the existing reschedule cascade.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_events_cog.py` (mirror existing edit-test scaffolding in that file for building `payload`/cog):

```python
async def test_same_time_opponent_swap_carries_over(make_events_cog, db):
    cog = make_events_cog(db)
    db.set_config("match_channel_id", "555")
    ts = 1778198400
    # existing scheduled match at ts with different teams
    old = db.insert_match(division="D1", week="W", team_home="A", team_away="B",
                          match_time=ts, posted_at=1)
    db.update_match_teamup_id(old, "evtOLD")
    db.upsert_signup(old, "m", "producer", "u1", "user1", "U1")

    # edited post now reads a DIFFERENT matchup at the SAME time
    edited = ("[D1] C vs D\n"
              f"<t:{ts}:F>")
    payload = _make_edit_payload(channel_id=555, content=edited)  # helper in this file

    with patch.object(cog, "_handle_reschedule", new_callable=AsyncMock):
        await cog.on_raw_message_edit(payload)

    swapped = db.get_match_by_teams_in_week(
        "C", "D", *__import__("cogs.events", fromlist=["_week_bounds"])._week_bounds(ts))
    assert swapped is not None
    assert {s["user_id"] for s in db.get_signups_for_match(swapped["id"])} == {"u1"}
```

If `tests/test_events_cog.py` has no `_make_edit_payload`/`make_events_cog` helpers, add minimal ones mirroring how the existing `on_raw_message_edit` tests construct a `discord.RawMessageUpdateEvent`-like object and the `EventsCog`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_events_cog.py::test_same_time_opponent_swap_carries_over -v`
Expected: FAIL (new matchup is treated as a brand-new match; no carry-over; new match has no sign-ups)

- [ ] **Step 3: Implement**

In `cogs/events.py` `on_raw_message_edit`, locate where (after reparse) it currently does the `get_match_by_teams_in_week` lookup and the `if not old_match:` brand-new-insert branch. Replace that brand-new branch so it first checks for a same-time scheduled match:

```python
        week_start, week_end = _week_bounds(parsed.match_time)
        old_match = self.db.get_match_by_teams_in_week(
            parsed.team_home, parsed.team_away, week_start, week_end
        )

        if not old_match:
            twin = self.db.get_scheduled_match_at_time_in_week(
                parsed.match_time, week_start, week_end
            )
            if twin and not (twin["team_home"] == parsed.team_home
                             and twin["team_away"] == parsed.team_away):
                # Same-slot opponent swap: insert the new matchup, carry state,
                # then tear down the old scheduled match.
                if not self.db.match_exists(parsed.team_home, parsed.team_away,
                                            parsed.match_time):
                    self.db.insert_match(
                        division=parsed.division, week=parsed.week,
                        team_home=parsed.team_home, team_away=parsed.team_away,
                        match_time=parsed.match_time,
                        posted_at=int(message.created_at.timestamp()),
                    )
                new_match = self.db.get_match_by_teams_in_week(
                    parsed.team_home, parsed.team_away, week_start, week_end)
                if new_match:
                    from cogs.talent import carry_over_if_same_time
                    await carry_over_if_same_time(
                        self.bot, self.db, twin["id"], new_match["id"])
                    # Reuse the reschedule cascade to remove the old match
                    # cleanly (event delete, slot clear, cascade).
                    from cogs.confirm_view import cancel_orphaned_confirmation
                    await cancel_orphaned_confirmation(
                        self.bot, self.db, twin["id"],
                        reason="this slot's opponent changed (same time)")
                    self.db.clear_match_from_proposal_slots(twin["id"])
                    self.db.delete_match_cascade(twin["id"])
                    md = datetime.fromtimestamp(
                        parsed.match_time, tz=ET).strftime("%Y-%m-%d")
                    self.bot.dispatch("match_logged", md)
                return
            if not self.db.match_exists(parsed.team_home, parsed.team_away,
                                        parsed.match_time):
                self.db.insert_match(
                    division=parsed.division, week=parsed.week,
                    team_home=parsed.team_home, team_away=parsed.team_away,
                    match_time=parsed.match_time,
                    posted_at=int(message.created_at.timestamp()),
                )
                match_date = datetime.fromtimestamp(
                    parsed.match_time, tz=ET).strftime("%Y-%m-%d")
                self.bot.dispatch("match_logged", match_date)
            return

        if old_match["match_time"] == parsed.match_time:
            return  # no-op — same time, same teams

        await self._handle_reschedule(old_match, parsed)
```

(Adjust to the exact surrounding structure of `on_raw_message_edit` — preserve its existing reparse and early returns. The precedence required by the spec: exact-team reschedule check first; only when there is no same-teams match do we consider a same-time opponent swap.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_events_cog.py::test_same_time_opponent_swap_carries_over -v`
Expected: PASS

Run the events suite:
Run: `python -m pytest tests/test_events_cog.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cogs/events.py tests/test_events_cog.py
git commit -m "feat: detect same-time opponent swap on match-post edit and carry over"
```

---

## Task 15: Full-suite regression + final review

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite**

Run: `python -m pytest tests/ -v`
Expected: PASS — all prior tests plus the ~20 added here. No failures, no errors.

- [ ] **Step 2: Manual consistency scan**

Verify by reading:
- `cogs/talent.py` exports `replace_allocation_role`, `ReplaceRoleView`, `carry_over_if_same_time`, `_ROLE_LABEL_BY_KEY`.
- `cogs/confirm_view.py` `ReadyButton` gates on required-only; `RejectButton` posts `ReplaceRoleView` for required, flag for optional; `_DISPLAY_LABEL` defined.
- `cogs/signup.py` `ApprovedSignUpView` includes `EditAllocationButton`; `_NewMatchSelect` calls carry-over.
- `cogs/weekly_proposals.py` `_UpdateScheduleButton` pairs same-time add/remove.
- `cogs/events.py` `on_raw_message_edit` does exact-team reschedule first, then same-time swap.

- [ ] **Step 3: Commit any fixes, then final commit**

```bash
git add -A
git commit -m "test: full-suite green for allocation edit/carry-over/optional confirmations"
```

---

## Self-Review

**Spec coverage:**
- Carry-over (same time) across the 3 paths → Tasks 11, 12, 13, 14 ✓
- Stage-aware behaviour (sign-up / awaiting / accepted) → Task 11 ✓
- Single-role replacement core → Tasks 7, 8 ✓
- Reject → single-role replace (required) / flag (optional) → Task 9 ✓
- Edit Allocation button on approved → Task 10 ✓
- Optional-role confirmations + required-only gate → Tasks 5, 6 ✓
- DB methods (`copy_signups`, `copy_allocation`, `get_scheduled_match_at_time_in_week`, plus `update_allocation_lineup` for status-preserving edits) → Tasks 1-4 ✓
- Match-post edit precedence (exact-team first, then same-time swap) → Task 14 ✓

**Placeholder scan:** No TBD/placeholder steps; code blocks complete. Two tasks (12, 13, 14) explicitly tell the implementer to mirror existing test scaffolding in the target test file because that scaffolding (proposal-selection injection, edit-payload construction) is file-specific — the implementer must read the sibling tests, not invent. This is intentional guidance, not a placeholder.

**Type consistency:** `replace_allocation_role(bot, db, match_id, role_key, new_assignment|None)` — same signature in Tasks 7, 8, 9, 10. `carry_over_if_same_time(bot, db, old_id, new_id)` — same in Tasks 11-14. Role keys use DB form (`pbp_1`, `colour_1`, `analyst_1`) consistently. `update_allocation_lineup` (Task 3) is the status-preserving writer used by `replace_allocation_role` (Task 7) — names match. `_ROLE_LABEL_BY_KEY` (Task 7) and `_DISPLAY_LABEL` (Task 9) are distinct intentionally (talent.py vs confirm_view.py); both map role-key→label.

One known interaction to watch in execution: Task 12/13 schedule the new match via `accept_combination` (which creates a fresh allocation row) and then `copy_allocation` upserts over it — `copy_allocation` (Task 2) uses `INSERT OR IGNORE` then `UPDATE`, so it correctly overwrites the fresh row. Verified consistent.
