"""NUMBER CLASH module suite — docs/DUEL_MODULE_SPEC.md §"Tests your duel must
ship with".

The rules are small enough to state in a line, so most of these tests are about
the edges around them: a number spent is spent, a seat can only play out of its
own hand, and the number in flight reaches nobody until the round resolves.
"""

from __future__ import annotations

import pytest

from backend.games.duel3_number_clash import (
    NORMAL_ROUNDS,
    NUMBERS,
    POINTS_NEEDED,
    NumberClash,
)
from backend.games.duel_base import (
    DUEL_RULES_VERSION,
    MAX_CHOICE_CHARS,
    SIDES,
    DuelState,
    other_side,
)


@pytest.fixture
def duel() -> NumberClash:
    return NumberClash()


def commit(duel: NumberClash, state: DuelState, a: int | None, b: int | None):
    """Both seats submit and the round resolves — the engine's loop, inlined.

    `None` is a Duelist who let the window lapse.
    """
    for side, number in (("a", a), ("b", b)):
        if number is None:
            continue
        move = duel.normalize_choice(state, str(number), side)
        assert move is not None, f"{side} could not play {number}"
        state.choices[side] = move
    winner = duel.resolve_round(state)
    if winner is None:
        state.choices.clear()          # the engine clears after the reveal beat
        state.round_index += 1
    return winner


# --- Generation ---

def test_determinism(duel):
    first, second = duel.new_duel(11), duel.new_duel(11)
    assert first.payload == second.payload
    assert first.private == second.private
    assert first.duel_game_id == second.duel_game_id == "number_clash"


def test_both_hands_start_at_one_through_nine(duel):
    state = duel.new_duel(3)
    assert NUMBERS == tuple(range(1, 10))
    for side in SIDES:
        assert duel._available(state.private, side) == list(NUMBERS)
        assert state.private["used"][side] == []
    assert state.private["points"] == {"a": 0, "b": 0}
    assert state.round_index == 1 and state.choices == {}


def test_the_module_declares_its_own_time_cost(duel):
    assert duel.choice_seconds == 8
    # The module scores the match, so one returned winner *is* the duel.
    assert duel.wins_needed == 1
    payload = duel._payload(duel.new_duel(1), "a")
    assert payload["choice_seconds"] == 8 and payload["points_needed"] == 4


# --- Rounds ---

@pytest.mark.parametrize("a,b,winner", [
    (9, 1, "a"), (1, 9, "b"), (5, 4, "a"), (4, 5, "b"), (2, 1, "a"),
    (7, 7, None), (1, 1, None), (9, 9, None),
])
def test_the_higher_number_takes_the_round(duel, a, b, winner):
    state = duel.new_duel(1)
    commit(duel, state, a, b)
    assert state.private["last"]["winner"] == winner


def test_every_pairing_is_decided_the_same_way_from_either_seat(duel):
    """Swapping the seats swaps the winner — the module can't favour a side."""
    for a in NUMBERS:
        for b in NUMBERS:
            forward, mirrored = duel.new_duel(1), duel.new_duel(1)
            commit(duel, forward, a, b)
            commit(duel, mirrored, b, a)
            first = forward.private["last"]["winner"]
            second = mirrored.private["last"]["winner"]
            if first is None:
                assert second is None
            else:
                assert second == other_side(first)


def test_a_win_pays_exactly_one_point(duel):
    state = duel.new_duel(1)
    commit(duel, state, 9, 3)
    assert state.private["points"] == {"a": 1, "b": 0}


def test_both_numbers_are_spent_even_on_a_draw(duel):
    state = duel.new_duel(1)
    commit(duel, state, 6, 6)
    assert state.private["points"] == {"a": 0, "b": 0}
    for side in SIDES:
        assert 6 not in duel._available(state.private, side)


def test_a_number_cannot_be_played_twice(duel):
    state = duel.new_duel(1)
    commit(duel, state, 9, 3)
    assert duel.normalize_choice(state, "9", "a") is None
    assert duel.normalize_choice(state, "3", "b") is None
    assert duel.normalize_choice(state, "3", "a") == "3"   # a still holds theirs


def test_four_points_takes_the_duel(duel):
    state = duel.new_duel(1)
    for round_index, (a, b) in enumerate([(9, 1), (8, 2), (7, 3), (6, 4)]):
        winner = commit(duel, state, a, b)
        expected = "a" if round_index == POINTS_NEEDED - 1 else None
        assert winner == expected
    assert state.private["points"] == {"a": POINTS_NEEDED, "b": 0}


def test_a_duel_short_of_four_points_plays_on(duel):
    state = duel.new_duel(1)
    for a, b in [(9, 1), (8, 2), (7, 3)]:
        assert commit(duel, state, a, b) is None
    assert state.private["points"] == {"a": 3, "b": 0}


@pytest.mark.parametrize("number", NUMBERS)
def test_letting_the_window_lapse_loses_the_round(duel, number):
    state = duel.new_duel(1)
    commit(duel, state, number, None)
    assert state.private["points"] == {"a": 1, "b": 0}
    state = duel.new_duel(1)
    commit(duel, state, None, number)
    assert state.private["points"] == {"a": 0, "b": 1}


def test_a_double_no_show_is_a_draw_that_spends_nothing(duel):
    state = duel.new_duel(1)
    assert commit(duel, state, None, None) is None
    assert state.private["points"] == {"a": 0, "b": 0}
    for side in SIDES:
        assert duel._available(state.private, side) == list(NUMBERS)


def test_a_forfeit_only_spends_the_number_that_was_played(duel):
    state = duel.new_duel(1)
    commit(duel, state, 4, None)
    assert duel._available(state.private, "a") == [1, 2, 3, 5, 6, 7, 8, 9]
    assert duel._available(state.private, "b") == list(NUMBERS)


# --- Sudden Death ---

def test_a_match_still_level_after_seven_rounds_is_sudden_death(duel):
    state = duel.new_duel(1)
    for number in range(1, NORMAL_ROUNDS + 1):
        assert commit(duel, state, number, number) is None   # seven draws
    assert state.private["sudden_death"] is True
    assert state.private["game_round"] == NORMAL_ROUNDS + 1
    assert commit(duel, state, 9, 8) is None    # and it still takes four points


def test_exhausted_hands_are_dealt_again_rather_than_stranded(duel):
    state = duel.new_duel(1)
    for number in NUMBERS:
        commit(duel, state, number, number)     # nine draws spend every number
    for side in SIDES:
        assert duel._available(state.private, side) == list(NUMBERS)
    assert duel.normalize_choice(state, "5", "a") == "5"


# --- Choice validation ---

@pytest.mark.parametrize("raw,expected", [
    ("7", "7"), (" 7 ", "7"), ("\t9\n", "9"), (1, "1"), (9, "9"),
])
def test_choices_are_normalised(duel, raw, expected):
    assert duel.normalize_choice(duel.new_duel(1), raw, "a") == expected


@pytest.mark.parametrize("bad", [
    "", "   ", "0", "10", "-1", "3.5", "+7", "seven", "1e2", "٧", "7 8",
    None, [], {}, ["7"], {"choice": 7}, 0, 10, -1, 3.5,
    "7" * 100, "x" * (MAX_CHOICE_CHARS + 1),
])
def test_illegal_choices_are_rejected_without_raising(duel, bad):
    assert duel.normalize_choice(duel.new_duel(1), bad, "a") is None


def test_a_move_without_a_seat_is_not_a_move(duel):
    state = duel.new_duel(1)
    assert duel.normalize_choice(state, "7") is None
    assert duel.normalize_choice(state, "7", "c") is None


def test_a_seat_cannot_play_out_of_the_opponents_hand(duel):
    state = duel.new_duel(1)
    commit(duel, state, 9, 3)
    # The same 9 is legal for whoever still holds theirs, and illegal for the
    # seat that has spent it — which is the whole point of `side`.
    assert duel.normalize_choice(state, "9", "b") == "9"
    assert duel.normalize_choice(state, "9", "a") is None


# --- The reveal rule ---

def carriers(view: dict) -> list:
    """Every place in a served view a *played* number can appear.

    A repr scan is no use here — the view is full of small integers that are
    round counters and scores — so this enumerates the channels instead: the
    choices, both spent piles, the round log and the engine's history. If the
    opponent's number is anywhere, it is in one of these.
    """
    payload = view["payload"]
    return [
        view["choices"], view["history"], payload["log"], payload["last"],
        payload["used"],
    ]


@pytest.mark.parametrize("a", NUMBERS)
@pytest.mark.parametrize("b", NUMBERS)
def test_no_matchup_leaks_to_either_seat(duel, a, b):
    """Sweep every matchup rather than trusting one sample."""
    state = duel.new_duel(1)
    state.choices["a"] = duel.normalize_choice(state, str(a), "a")
    state.choices["b"] = duel.normalize_choice(state, str(b), "b")
    for side in SIDES:
        view = duel.public(state, side=side, revealed=False)
        assert view["choices"] == {side: state.choices[side]}
        assert view["locked"] == {"a": True, "b": True}
        # An open round has resolved nothing, so every other channel is empty:
        # the opponent's number has nowhere in this view to be.
        assert carriers(view) == [
            {side: state.choices[side]}, [], [], None, {"a": [], "b": []},
        ]


def test_a_grandmaster_sees_neither_number_before_reveal(duel):
    state = duel.new_duel(1)
    state.choices.update({"a": "7", "b": "4"})
    view = duel.public(state, side=None, revealed=False)
    assert view["choices"] == {}
    assert view["payload"]["available"] == []
    assert carriers(view) == [{}, [], [], None, {"a": [], "b": []}]


def test_reveal_shows_both_numbers(duel):
    state = duel.new_duel(1)
    state.choices.update({"a": "7", "b": "4"})
    for side in (*SIDES, None):
        view = duel.public(state, side=side, revealed=True)
        assert view["choices"] == {"a": "7", "b": "4"}


def test_spent_numbers_are_public_to_both_seats(duel):
    """Nothing is gained by hiding them: each was revealed when its round
    resolved, and the log would answer the question anyway."""
    state = duel.new_duel(1)
    commit(duel, state, 9, 3)
    for side in (*SIDES, None):
        payload = duel.public(state, side=side, revealed=False)["payload"]
        assert payload["used"] == {"a": [9], "b": [3]}
        assert payload["log"][0]["winner"] == "a"


def test_public_view_shape(duel):
    state = duel.new_duel(5)
    view = duel.public(state, side="b", revealed=False)
    assert set(view) == {
        "duel_game_id", "rules_version", "round", "wins", "history",
        "you", "locked", "choices", "payload",
    }
    assert view["rules_version"] == DUEL_RULES_VERSION
    assert view["you"] == "b"
    assert view["payload"]["kind"] == "number_clash"
    assert view["payload"]["available"] == list(NUMBERS)


def test_public_view_is_a_copy(duel):
    """Mutating a served view must not reach back into live duel state."""
    state = duel.new_duel(1)
    commit(duel, state, 9, 3)
    view = duel.public(state, side="a", revealed=True)
    view["payload"]["points"]["a"] = 99
    view["payload"]["used"]["a"].append(1)
    view["payload"]["log"][0]["points"]["b"] = 99
    view["payload"]["available"].clear()
    assert state.private["points"] == {"a": 1, "b": 0}
    assert state.private["used"] == {"a": [9], "b": [3]}
    assert state.private["log"][0]["points"] == {"a": 1, "b": 0}


# --- Module hygiene ---

def test_module_is_stateless_across_duels(duel):
    first = duel.new_duel(1)
    commit(duel, first, 9, 3)
    second = duel.new_duel(1)
    assert second.private["points"] == {"a": 0, "b": 0}
    assert second.private["used"] == {"a": [], "b": []}


def test_reset_safe_and_deterministic_after(duel):
    before = duel.new_duel(4).private
    assert duel.reset() is None
    assert duel.reset() is None  # idempotent
    assert duel.new_duel(4).private == before
