"""Avatar codes: validation, the join path, and client/server agreement.

A face is cosmetic, so most of what is checked here is not "does it look
right" — it is the two things that are not cosmetic at all:

* A code is broadcast to every other client and drawn into SVG at the far end.
  So the server must never pass through anything but a code.
* The drawings live in `frontend/avatar.js` and the bounds live in
  `backend/config.py`. Nothing but a test stops those two drifting apart, and
  when they drift the failure is a blank face that only shows up in a browser.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend import accounts, config
from backend.engine import RelayEngine
from backend.registry import GameRegistry

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


# --- codes -----------------------------------------------------------------

def test_a_code_is_one_digit_per_slot():
    assert config.AVATAR_CODE_LENGTH == len(config.AVATAR_SLOTS)
    code = "0" * config.AVATAR_CODE_LENGTH
    assert config.avatar_normalise(code) == code
    assert config.avatar_decode(code) == [0] * config.AVATAR_CODE_LENGTH


def test_every_in_range_code_round_trips():
    """The top variant of every slot at once — the corner a fencepost error in
    the bounds check would fail on."""
    top = "".join(config.avatar_digit(count - 1) for count in config.AVATAR_SLOTS.values())
    assert config.avatar_normalise(top) == top
    assert config.avatar_decode(top) == [c - 1 for c in config.AVATAR_SLOTS.values()]


def test_a_variant_past_the_end_of_its_slot_is_refused():
    for at, (slot, count) in enumerate(config.AVATAR_SLOTS.items()):
        digits = ["0"] * config.AVATAR_CODE_LENGTH
        digits[at] = config.avatar_digit(count)  # one past the last variant
        assert config.avatar_normalise("".join(digits)) is None, slot


def test_codes_are_case_insensitive_and_stored_lower():
    slots = list(config.AVATAR_SLOTS.values())
    # Only meaningful where a slot actually reaches the letters.
    if max(slots) <= 10:
        pytest.skip("no slot has enough variants to use a letter digit")
    code = "".join(config.avatar_digit(min(10, count - 1)) for count in slots)
    assert config.avatar_normalise(code.upper()) == code


@pytest.mark.parametrize("junk", [
    "",
    "0" * (len(config.AVATAR_SLOTS) - 1),          # too short
    "0" * (len(config.AVATAR_SLOTS) + 1),          # too long
    "-" * len(config.AVATAR_SLOTS),                # not digits
    '"><script>alert(1)</script>',                 # the one that matters
    "<svg onload=alert(1)>",
    None,
    12345,
    ["0", "0"],
])
def test_nothing_but_a_code_survives(junk):
    assert config.avatar_normalise(junk) is None


def test_a_code_can_never_carry_markup():
    """Belt and braces on the property the client relies on: whatever comes
    back out of `avatar_normalise` is digits, so interpolating it cannot open a
    tag or close an attribute."""
    every = [
        "".join(config.avatar_digit(v % count) for count in config.AVATAR_SLOTS.values())
        for v in range(40)
    ]
    for code in every:
        assert re.fullmatch(r"[0-9a-z]+", config.avatar_normalise(code) or "")


# --- the join path ---------------------------------------------------------

@pytest.fixture
def engine():
    """The real game registry: nothing here depends on which games exist, and a
    fake one would only be another thing to keep in step."""
    return RelayEngine(GameRegistry())


def _join(engine, match, name="Ada", avatar=None):
    player, _ = engine.join_match(match, name, avatar=avatar)
    return player


def test_a_picked_face_reaches_the_roster(engine):
    match = engine.create_match()
    code = config.avatar_normalise("0" * config.AVATAR_CODE_LENGTH)
    player = _join(engine, match, avatar=code)
    assert player.avatar == code
    # PlayerPublic is what every other client draws from.
    assert player.public()["avatar"] == code


def test_joining_without_a_face_is_fine(engine):
    """The seeded face is the fallback, so no pick is a normal state and not an
    error — most players will never touch the picker."""
    match = engine.create_match()
    assert _join(engine, match).avatar is None


def test_a_junk_face_does_not_block_a_join(engine):
    """Cosmetic: a bad code costs the player their pick, never their seat."""
    match = engine.create_match()
    player = _join(engine, match, avatar="<script>alert(1)</script>")
    assert player.avatar is None
    assert player.id in match.players


def test_set_avatar_changes_and_clears(engine):
    match = engine.create_match()
    player = _join(engine, match)
    code = "".join(config.avatar_digit(1) for _ in config.AVATAR_SLOTS)

    assert engine.set_avatar(match, player.id, code).changed is True
    assert player.avatar == code

    # Setting the same face again is not a state change, so it broadcasts nothing.
    assert engine.set_avatar(match, player.id, code).changed is False

    # "" is the only way back to the seeded face.
    assert engine.set_avatar(match, player.id, "").changed is True
    assert player.avatar is None


def test_set_avatar_refuses_a_bad_code_without_changing_anything(engine):
    match = engine.create_match()
    player = _join(engine, match)
    good = "".join(config.avatar_digit(1) for _ in config.AVATAR_SLOTS)
    engine.set_avatar(match, player.id, good)

    result = engine.set_avatar(match, player.id, "<img src=x onerror=1>")
    assert result.ok is False
    assert result.changed is False
    assert player.avatar == good  # the old face survives a refused change


def test_set_avatar_on_an_unknown_player_is_refused(engine):
    match = engine.create_match()
    assert engine.set_avatar(match, "p_nobody", "0" * config.AVATAR_CODE_LENGTH).ok is False


# --- accounts --------------------------------------------------------------

@pytest.fixture
def db():
    accounts.close()
    accounts.connect(":memory:")
    yield
    accounts.close()


def _account():
    return accounts.create_user(
        "Ada", "Lovelace", "ada@example.com", "ada_l",
        "correct horse battery", "correct horse battery",
    )


def test_an_account_remembers_a_face(db):
    user = _account()
    assert user.avatar is None
    code = "".join(config.avatar_digit(2) for _ in config.AVATAR_SLOTS)
    assert accounts.set_avatar(user.id, code) == code
    assert accounts.by_id(user.id).avatar == code
    assert accounts.by_id(user.id).public()["avatar"] == code


def test_a_junk_face_is_stored_as_no_face(db):
    user = _account()
    assert accounts.set_avatar(user.id, "<script>") is None
    assert accounts.by_id(user.id).avatar is None


def test_the_avatar_column_is_added_to_an_existing_database(tmp_path):
    """The accounts file outlives a deploy, so a new column has to be migrated
    in. Without this, every query against an older database raises."""
    path = tmp_path / "old.db"
    import sqlite3
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,"
        " username TEXT NOT NULL UNIQUE, first_name TEXT NOT NULL,"
        " last_name TEXT NOT NULL, password_hash TEXT NOT NULL,"
        " verified INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);"
        "INSERT INTO users VALUES"
        " ('u_old','old@example.com','oldtimer','Old','Timer','x',1,1700000000);"
    )
    old.commit()
    old.close()

    accounts.close()
    accounts.connect(str(path))
    try:
        user = accounts.by_id("u_old")
        assert user is not None and user.avatar is None   # the account survived
        code = "0" * config.AVATAR_CODE_LENGTH
        assert accounts.set_avatar("u_old", code) == code
        assert accounts.by_id("u_old").avatar == code
    finally:
        accounts.close()


# --- the client draws what the server allows -------------------------------

def _js_variants() -> dict[str, int]:
    """`AVATAR_VARIANTS` out of frontend/avatar.js.

    Read rather than executed, because there is no JS runtime in the test
    environment and this is the only fact from that file the server cares
    about.
    """
    source = (FRONTEND / "avatar.js").read_text()
    match = re.search(r"var AVATAR_VARIANTS = \{(.*?)\};", source, re.S)
    assert match, "AVATAR_VARIANTS is not where the test expects it"
    return {
        key: int(value)
        for key, value in re.findall(r"(\w+)\s*:\s*(\d+)", match.group(1))
    }


def _js_table_lengths() -> dict[str, int]:
    """How many drawings each feature table actually holds, counted by the
    top-level `function () {` entries between its brackets."""
    source = (FRONTEND / "avatar.js").read_text()
    out = {}
    for slot, name in (("eyes", "EYES"), ("mouth", "MOUTHS"), ("brow", "BROWS"),
                       ("hat", "HATS"), ("extra", "EXTRAS")):
        match = re.search(r"var %s = \[(.*?)\n  \];" % name, source, re.S)
        assert match, f"{name} is not where the test expects it"
        out[slot] = len(re.findall(r"^    function \(\)", match.group(1), re.M))
    for slot, name in (("back", "AVATAR_BACKS"), ("skin", "AVATAR_SKINS")):
        match = re.search(r"var %s = \[(.*?)\];" % name, source, re.S)
        assert match, f"{name} is not where the test expects it"
        out[slot] = len(re.findall(r'"#[0-9a-fA-F]{6}"', match.group(1)))
    return out


def test_the_client_declares_the_same_slots_as_the_server():
    assert _js_variants() == dict(config.AVATAR_SLOTS)


def test_the_client_can_draw_every_variant_the_server_accepts():
    """The failure this catches: config offers eight hats, avatar.js draws
    seven, and the eighth is a code the server happily stores and no browser
    can render."""
    assert _js_table_lengths() == dict(config.AVATAR_SLOTS)


def test_the_slots_are_in_the_same_order_on_both_sides():
    """The code is positional, so a reordered slot silently reinterprets every
    face already saved to an account."""
    source = (FRONTEND / "avatar.js").read_text()
    match = re.search(r"var AVATAR_ORDER = \[(.*?)\];", source, re.S)
    assert match
    assert re.findall(r'"(\w+)"', match.group(1)) == list(config.AVATAR_SLOTS)


def test_the_config_endpoint_publishes_the_slots():
    """The picker builds itself from this, so it has to be in the payload the
    client already fetches."""
    from fastapi.testclient import TestClient
    from backend.main import app

    with TestClient(app) as client:
        body = client.get("/api/config").json()
    assert body["avatar_slots"] == dict(config.AVATAR_SLOTS)
    # It has to survive JSON unchanged — the client indexes tables with it.
    assert json.loads(json.dumps(body["avatar_slots"])) == dict(config.AVATAR_SLOTS)
