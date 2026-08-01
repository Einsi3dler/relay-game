"""RPS DUEL module suite — docs/DUEL_MODULE_SPEC.md §"Tests your duel must ship with".

The engine owns the clock and the consequences; these tests pin the module's
own contract: the move set, who wins a round, and — the security-critical part —
that a choice never reaches the other player before the round resolves.
"""

from __future__ import annotations

import pytest

from backend.games.duel1_rps import BEATS, MOVES, RockPaperScissorsDuel
from backend.games.duel_base import (
    DUEL_RULES_VERSION,
    MAX_CHOICE_CHARS,
    SIDES,
    DuelState,
    other_side,
)


@pytest.fixture
def duel() -> RockPaperScissorsDuel:
    return RockPaperScissorsDuel()


def state_with(duel: RockPaperScissorsDuel, **choices: str) -> DuelState:
    state = duel.new_duel(seed=1)
    state.choices.update(choices)
    return state


# --- Generation ---

def test_determinism(duel):
    first, second = duel.new_duel(11), duel.new_duel(11)
    assert first.payload == second.payload
    assert first.duel_game_id == second.duel_game_id == "rps_duel"


def test_fresh_duel_starts_empty(duel):
    state = duel.new_duel(3)
    assert state.round_index == 1
    assert state.wins == {"a": 0, "b": 0}
    assert state.choices == {} and state.history == []
    assert not state.both_locked()


def test_payload_is_pure_render_data(duel):
    payload = duel.new_duel(3).payload
    assert payload["moves"] == list(MOVES)
    assert payload["wins_needed"] == duel.wins_needed == 2
    assert payload["choice_seconds"] == duel.choice_seconds == 5


def test_move_set_is_a_total_cycle(duel):
    """Every move beats exactly one other and loses to exactly one other, so
    no matchup is undecided and no move is dominant."""
    assert set(BEATS) == set(MOVES) and len(MOVES) == 3
    assert set(BEATS.values()) == set(MOVES)          # each move gets beaten once
    assert all(BEATS[BEATS[BEATS[move]]] == move for move in MOVES)


# --- Choice validation ---

@pytest.mark.parametrize("move", MOVES)
def test_every_move_is_legal(duel, move):
    state = duel.new_duel(1)
    assert duel.normalize_choice(state, move) == move


@pytest.mark.parametrize("raw,expected", [
    ("ROCK", "rock"), ("  Paper  ", "paper"), ("SciSSors", "scissors"),
    ("rock\n", "rock"), ("\tpaper", "paper"),
])
def test_choices_are_normalised(duel, raw, expected):
    assert duel.normalize_choice(duel.new_duel(1), raw) == expected


@pytest.mark.parametrize("bad", [
    "", "   ", "lizard", "spock", "rock paper", "roc", "rockk",
    None, 0, 1, [], {}, ["rock"], {"choice": "rock"}, True, 3.5,
    "rock" * 100, "x" * (MAX_CHOICE_CHARS + 1),
])
def test_illegal_choices_are_rejected_without_raising(duel, bad):
    assert duel.normalize_choice(duel.new_duel(1), bad) is None


# --- Round resolution ---

@pytest.mark.parametrize("a,b,winner", [
    ("rock", "scissors", "a"), ("scissors", "rock", "b"),
    ("paper", "rock", "a"), ("rock", "paper", "b"),
    ("scissors", "paper", "a"), ("paper", "scissors", "b"),
    ("rock", "rock", None), ("paper", "paper", None), ("scissors", "scissors", None),
])
def test_all_nine_matchups(duel, a, b, winner):
    assert duel.resolve_round(state_with(duel, a=a, b=b)) == winner


def test_resolution_is_symmetric(duel):
    """Swapping the seats swaps the winner — the module can't favour a side."""
    for a in MOVES:
        for b in MOVES:
            forward = duel.resolve_round(state_with(duel, a=a, b=b))
            mirrored = duel.resolve_round(state_with(duel, a=b, b=a))
            if forward is None:
                assert mirrored is None
            else:
                assert mirrored == other_side(forward)


@pytest.mark.parametrize("move", MOVES)
def test_letting_the_window_lapse_loses_the_round(duel, move):
    assert duel.resolve_round(state_with(duel, a=move)) == "a"   # b never chose
    assert duel.resolve_round(state_with(duel, b=move)) == "b"   # a never chose


def test_a_double_no_show_is_a_tie(duel):
    """Neither team is punished when both Duelists miss the window."""
    assert duel.resolve_round(duel.new_duel(1)) is None


# --- The reveal rule (the security-critical part) ---

def live_view(view: dict) -> str:
    """The parts of a served view that could carry *this round's* moves.

    `payload` names every move by design (the client draws buttons from it) and
    `history` only holds rounds that already resolved, so both are public — a
    leak would have to surface anywhere else.
    """
    return repr({
        key: value for key, value in view.items()
        if key not in ("payload", "history")
    })


def test_a_duellist_never_sees_the_opponents_choice_before_reveal(duel):
    state = state_with(duel, a="rock", b="paper")
    view = duel.public(state, side="a", revealed=False)
    assert view["choices"] == {"a": "rock"}          # own choice echoed back
    assert "b" not in view["choices"]                # opponent's is not there
    assert view["locked"] == {"a": True, "b": True}  # only *that* they locked
    assert "paper" not in live_view(view)


def test_a_grandmaster_sees_neither_choice_before_reveal(duel):
    """side=None is a watcher: they must not be able to relay a move."""
    view = duel.public(state_with(duel, a="rock", b="paper"), side=None, revealed=False)
    assert view["choices"] == {}
    assert view["locked"] == {"a": True, "b": True}
    assert "rock" not in live_view(view) and "paper" not in live_view(view)


def test_locked_flags_do_not_leak_the_move(duel):
    view = duel.public(state_with(duel, a="scissors"), side="b", revealed=False)
    assert view["choices"] == {}
    assert view["locked"] == {"a": True, "b": False}
    assert "scissors" not in live_view(view)


@pytest.mark.parametrize("a", MOVES)
@pytest.mark.parametrize("b", MOVES)
def test_no_matchup_leaks_to_either_seat(duel, a, b):
    """Sweep every matchup rather than trusting one sample."""
    state = state_with(duel, a=a, b=b)
    for side in SIDES:
        rendered = live_view(duel.public(state, side=side, revealed=False))
        opponent_move = state.choices[other_side(side)]
        if opponent_move != state.choices[side]:
            assert opponent_move not in rendered


def test_reveal_shows_both_choices(duel):
    state = state_with(duel, a="rock", b="paper")
    for side in (*SIDES, None):
        view = duel.public(state, side=side, revealed=True)
        assert view["choices"] == {"a": "rock", "b": "paper"}


def test_public_view_shape(duel):
    state = duel.new_duel(5)
    state.wins["a"] = 1
    state.history.append({"round": 1, "a": "rock", "b": "scissors", "winner": "a"})
    view = duel.public(state, side="b", revealed=False)
    assert set(view) == {
        "duel_game_id", "rules_version", "round", "wins", "history",
        "you", "locked", "choices", "payload",
    }
    assert view["rules_version"] == DUEL_RULES_VERSION
    assert view["you"] == "b" and view["wins"] == {"a": 1, "b": 0}
    assert view["history"][0]["winner"] == "a"


def test_public_view_is_a_copy(duel):
    """Mutating a served view must not reach back into live duel state."""
    state = state_with(duel, a="rock")
    view = duel.public(state, side="a", revealed=True)
    view["wins"]["a"] = 99
    view["history"].append({"round": 9})
    view["payload"]["moves"] = []
    assert state.wins == {"a": 0, "b": 0}
    assert state.history == []
    assert state.payload["moves"] == list(MOVES)


# --- Module hygiene ---

def test_module_is_stateless_across_duels(duel):
    first = duel.new_duel(1)
    first.choices["a"] = "rock"
    first.wins["a"] = 2
    second = duel.new_duel(1)
    assert second.choices == {} and second.wins == {"a": 0, "b": 0}


def test_reset_safe_and_deterministic_after(duel):
    before = duel.new_duel(4).payload
    assert duel.reset() is None
    assert duel.reset() is None  # idempotent
    assert duel.new_duel(4).payload == before
