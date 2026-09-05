"""Link duels: two people, one link, one duel, outside any match.

The feature is small because a room is deliberately not a `Match`, and these
tests are mostly about keeping that true. `match.status` is read in thirty-four
places in the engine; a room that ever grew teams, a purse or a level would be
a Match with extra steps, and the failure would show up miles from the cause.

So: a room costs nothing, tells nobody, pays nobody, and plays a duel that
behaves exactly like the one in a race.
"""

from __future__ import annotations

import pytest

from backend import config, duelroom
from backend.games.duel_base import SIDES
from backend.registry import GameRegistry

from tests.test_engine import NOW


@pytest.fixture
def registry() -> GameRegistry:
    return GameRegistry()


def seated(registry, duel_id: str = "rps_duel") -> duelroom.DuelRoom:
    """A room with both seats claimed and both people connected."""
    room = duelroom.create_room(duel_id)
    duelroom.claim_seat(room)
    for seat in room.seats.values():
        seat.connected = True
    duelroom.open_duel(room, registry.duel_by_id(duel_id), now=NOW)
    return room


def play(registry, room, move, limit: int = 300) -> duelroom.DuelRoom:
    """Drive a room to a finish. `move(duel, side)` picks each seat's answer."""
    for _ in range(limit):
        if room.status() == "done":
            return room
        duel = room.duel
        if duel.phase == "reveal":
            duelroom.on_timer(room, duelroom.ROUND_SCOPE, "duel_reveal", now=NOW)
            continue
        for side, seat in room.seats.items():
            duelroom.choose(
                room, seat.id, duel.id, duel.state.round_index,
                move(duel, side), now=NOW,
            )
    raise AssertionError(f"the duel never finished: {room.status()}")


def rps(duel, side):
    return "rock" if side == "a" else "scissors"


# --- seating --------------------------------------------------------------

def test_a_new_room_holds_one_seat_and_no_duel(registry):
    room = duelroom.create_room("rps_duel")
    assert room.status() == "waiting"
    assert list(room.seats) == ["a"]
    assert room.duel is None
    assert room.public(room.seats["a"].id)["you"] == "a"


def test_the_link_seats_the_second_person_and_then_nobody(registry):
    room = duelroom.create_room("rps_duel")
    second = duelroom.claim_seat(room)
    assert second is not None and room.full()
    # A third arrival is not an error: they may still watch.
    assert duelroom.claim_seat(room) is None


def test_the_duel_waits_for_both_sockets_not_both_seats(registry):
    """A five-second round would be half gone before the second person's
    socket finished opening."""
    room = duelroom.create_room("rps_duel")
    duelroom.claim_seat(room)
    module = registry.duel_by_id("rps_duel")
    assert duelroom.open_duel(room, module, now=NOW).changed is False
    assert room.duel is None

    room.seats["a"].connected = True
    assert duelroom.open_duel(room, module, now=NOW).changed is False
    room.seats["b"].connected = True
    assert duelroom.open_duel(room, module, now=NOW).changed is True
    assert room.status() == "duelling"


def test_a_seat_id_says_what_it_is(registry):
    room = duelroom.create_room("rps_duel")
    assert room.seats["a"].id.startswith("s_")


# --- the watcher ----------------------------------------------------------

@pytest.mark.parametrize("duel_id", ["rps_duel", "crown_duel", "number_clash",
                                     "bid_war"])
def test_a_watcher_sees_the_room_but_neither_hand(registry, duel_id):
    """Half of a duel is not knowing what the other person just did, and that
    holds for the person watching over their shoulder too."""
    room = seated(registry, duel_id)
    duel = room.duel
    duelroom.choose(room, room.seats["a"].id, duel.id, 1,
                    _first_legal(duel, "a"), now=NOW)
    view = room.public(None)
    assert view["you"] is None
    assert view["duel"]["choices"] == {}
    # ...though they can see that somebody has answered.
    assert view["duel"]["locked"]["a"] is True


def _first_legal(duel, side):
    """Any move this seat can legally make, whatever game it is."""
    module = duel.module
    payload = module.public(duel.state, side, False)["payload"]
    for candidate in (
        "rock", "normal",
        *[str(n) for n in payload.get("available", [])],
        *[str(payload.get("max_bid", 0))],
        *[c["type"] for c in payload.get("hand", []) if isinstance(c, dict)],
    ):
        if module.normalize_choice(duel.state, candidate, side) is not None:
            return candidate
    raise AssertionError(f"no legal move for {side} in {module.id}")


def test_a_watcher_cannot_play_or_restart(registry):
    room = seated(registry)
    refused = duelroom.choose(room, "w_nobody", room.duel.id, 1, "rock", now=NOW)
    assert refused.ok is False and "aren't in this duel" in refused.error
    room.duel.phase = "done"
    assert duelroom.rematch(room, "w_nobody", now=NOW).ok is False


# --- playing --------------------------------------------------------------

def test_the_playable_sweep_covers_the_whole_catalogue(registry):
    """The sweep below is parametrised by hand, so a fifth duel would ship
    without anyone ever playing it in a room. Fail here instead."""
    assert {entry["id"] for entry in registry.duel_library()} == set(PLAYABLE)


PLAYABLE = ["rps_duel", "crown_duel", "number_clash", "bid_war"]


@pytest.mark.parametrize("duel_id", PLAYABLE)
def test_every_duel_plays_to_a_winner_in_a_room(registry, duel_id):
    room = play(registry, seated(registry, duel_id), _asymmetric)
    assert room.duel.winner_side in SIDES
    assert room.status() == "done"
    # A finished duel stops its clock rather than leaving one running.
    assert room.duel.deadline is None


def _asymmetric(duel, side):
    """Two seats that never agree, so somebody eventually wins."""
    module = duel.module
    payload = module.public(duel.state, side, False)["payload"]
    if module.id == "rps_duel":
        return "rock" if side == "a" else "scissors"
    if module.id == "number_clash":
        avail = sorted(payload["available"])
        return str(avail[-1] if side == "a" else avail[0])
    if module.id == "bid_war":
        return str(min(payload["max_bid"], 3 if side == "a" else 2))
    if module.id == "crown_duel":
        # Two phases per crown round: the secret rewrite, then the card. Play
        # straight, and take the hand from opposite ends so the two seats never
        # field the same character — identical cards draw, and two seats that
        # always draw never finish.
        if payload.get("phase") == "strategy":
            return "normal"
        held = [c["type"] for c in payload.get("hand", [])
                if c["status"] == "available"]
        if not held:
            return "normal"
        return held[0] if side == "a" else held[-1]
    raise AssertionError(module.id)


def test_a_lapsed_window_costs_the_seat_that_said_nothing(registry):
    """A missing choice has to lose, or stalling is a strategy."""
    room = seated(registry)
    duel = room.duel
    duelroom.choose(room, room.seats["a"].id, duel.id, 1, "rock", now=NOW)
    result = duelroom.on_timer(room, duelroom.ROUND_SCOPE, "duel_round", now=NOW)
    assert result.changed is True
    assert duel.state.history[-1]["winner"] == "a"


def test_a_stale_timer_is_a_no_op(registry):
    room = seated(registry)
    assert duelroom.on_timer(room, "not-the-duel", "duel_round", now=NOW).changed is False
    assert duelroom.on_timer(room, duelroom.ROUND_SCOPE, "duel_reveal", now=NOW).changed is False


# --- rematch --------------------------------------------------------------

def test_a_rematch_is_a_new_duel_between_the_same_two_people(registry):
    room = play(registry, seated(registry), rps)
    before = room.duel.id
    seats = {side: seat.id for side, seat in room.seats.items()}

    assert duelroom.rematch(room, seats["a"], now=NOW).ok
    assert room.duel.id != before, "a reused id hands the new duel to the old renderer"
    assert room.duel.state.round_index == 1
    assert room.duel.winner_side is None
    assert room.duel.phase == "choosing"
    assert room.duels_played == 1
    # The links keep working, which is the point of doing this in the same room.
    assert {side: seat.id for side, seat in room.seats.items()} == seats


def test_a_rematch_waits_for_a_running_duel_and_an_absent_player(registry):
    room = seated(registry)
    refused = duelroom.rematch(room, room.seats["a"].id, now=NOW)
    assert refused.ok is False and "still running" in refused.error

    play(registry, room, rps)
    room.seats["b"].connected = False
    refused = duelroom.rematch(room, room.seats["a"].id, now=NOW)
    assert refused.ok is False and "waiting for the other player" in refused.error


# --- BID WAR without a purse ---------------------------------------------

def test_bid_war_is_funded_equally_from_config(registry):
    """A match's stakes are unequal because a Grandmaster chooses how much to
    back their champion, and that choice is the game. Nobody makes it here."""
    room = seated(registry, "bid_war")
    payload = room.duel.module.public(room.duel.state, "a", False)["payload"]
    assert payload["coins"] == {
        side: config.DUEL_ROOM_STAKE for side in SIDES
    }


def test_a_room_never_pays_a_settlement(registry, monkeypatch):
    """There are no purses to pay into. A room that called this has quietly
    grown an economy."""
    module = registry.duel_by_id("bid_war")

    def explode(*args, **kwargs):
        raise AssertionError("a room paid a settlement out")

    monkeypatch.setattr(module, "settlement", explode)
    room = play(registry, seated(registry, "bid_war"), _asymmetric)
    assert room.duel.winner_side in SIDES


def test_the_unstaked_duels_are_handed_no_stakes(registry):
    for duel_id in ("rps_duel", "crown_duel", "number_clash"):
        module = registry.duel_by_id(duel_id)
        assert duelroom._stakes(module) is None, duel_id


# --- what a room is not ---------------------------------------------------

def test_no_economy_reaches_the_view(registry):
    for room in (duelroom.create_room("bid_war"), seated(registry, "bid_war")):
        text = repr(room.public(None))
        for leak in ("currency", "'level'", "team_id", "perk", "roster_size"):
            assert leak not in text, f"{leak} in a {room.status()} room"


def test_a_room_never_imports_the_match_rules():
    """The whole reason a room is its own file. If it reaches into the engine
    for anything but a result shape, practice state can touch a live race."""
    import ast

    tree = ast.parse(open("backend/duelroom.py").read())
    engine_names: set[str] = set()
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(a.name for a in node.names)
            if node.module == "backend.engine":
                engine_names.update(a.name for a in node.names)
    # Two dataclasses and nothing else — the same import backend/timers.py makes.
    assert engine_names == {"EngineResult", "TimerRequest"}
    assert "Match" not in imported and "RelayEngine" not in imported

    # And it never names one either. Read the code, not the prose: the
    # docstrings have to stay free to explain what a room deliberately is not.
    used = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    for forbidden in ("Match", "Team", "RelayEngine", "currency", "config_snapshot"):
        assert forbidden not in used, forbidden
