"""BID WAR module suite — docs/DUEL_MODULE_SPEC.md §"Tests your duel must ship
with", plus the auction rules the handoff spec calls out by name.

The two that carry the game: **both bids are always spent**, and the prize order
past the next lot is never served to anyone. Everything else follows from those.
"""

from __future__ import annotations

import pytest

from backend.games.duel4_bid_war import (
    AUCTIONS,
    OVERTIME_COINS,
    OVERTIME_PRIZE,
    PRIZES,
    STARTING_COINS,
    BidWar,
)
from backend.games.duel_base import (
    DUEL_RULES_VERSION,
    MAX_CHOICE_CHARS,
    SIDES,
    DuelState,
    other_side,
)


@pytest.fixture
def duel() -> BidWar:
    return BidWar()


def bid(duel: BidWar, state: DuelState, a: int | None, b: int | None):
    """Both seats bid and the auction settles — the engine's loop, inlined.

    `None` is a Duelist who let the window lapse.
    """
    for side, amount in (("a", a), ("b", b)):
        if amount is None:
            continue
        move = duel.normalize_choice(state, str(amount), side)
        assert move is not None, f"{side} could not bid {amount}"
        state.choices[side] = move
    winner = duel.resolve_round(state)
    if winner is None:
        state.choices.clear()          # the engine clears after the reveal beat
        state.round_index += 1
    return winner


def fixed_order(duel: BidWar, order: list[int]) -> DuelState:
    """A duel whose prize order is pinned, so an auction can be scripted."""
    state = duel.new_duel(1)
    state.private["prizes"] = list(order)
    state.private["pot"] = order[0]
    return state


# --- Generation ---

def test_determinism(duel):
    first, second = duel.new_duel(11), duel.new_duel(11)
    assert first.payload == second.payload
    assert first.private == second.private
    assert first.duel_game_id == second.duel_game_id == "bid_war"


def test_every_duel_puts_all_five_prizes_up_in_some_order(duel):
    for seed in range(25):
        prizes = duel.new_duel(seed).private["prizes"]
        assert sorted(prizes) == sorted(PRIZES)


def test_the_prize_order_is_actually_shuffled(duel):
    """Not a proof of randomness — just that the order isn't a constant."""
    orders = {tuple(duel.new_duel(seed).private["prizes"]) for seed in range(25)}
    assert len(orders) > 1


def test_both_duellists_start_with_twenty_coins_and_no_points(duel):
    state = duel.new_duel(3)
    assert state.private["coins"] == {"a": STARTING_COINS, "b": STARTING_COINS}
    assert state.private["vp"] == {"a": 0, "b": 0}
    assert state.private["auction"] == 1 and state.private["overtime"] is False
    assert state.round_index == 1 and state.choices == {}


def test_the_module_declares_its_own_time_cost(duel):
    assert duel.choice_seconds == 10
    # The module scores the match itself, so one returned winner *is* the duel.
    assert duel.wins_needed == 1
    assert duel._payload(duel.new_duel(1), "a")["choice_seconds"] == 10


# --- Bidding ---

def test_the_higher_bid_takes_the_prize(duel):
    state = fixed_order(duel, [3, 5, 1, 2, 4])
    bid(duel, state, 7, 4)
    assert state.private["vp"] == {"a": 3, "b": 0}


def test_both_bids_are_spent_even_by_the_loser(duel):
    state = fixed_order(duel, [3, 5, 1, 2, 4])
    bid(duel, state, 7, 4)
    assert state.private["coins"] == {"a": 13, "b": 16}   # never refunded


def test_a_bid_of_zero_is_legal(duel):
    state = fixed_order(duel, [3, 5, 1, 2, 4])
    assert duel.normalize_choice(state, "0", "a") == "0"
    bid(duel, state, 0, 1)
    assert state.private["vp"] == {"a": 0, "b": 3}
    assert state.private["coins"] == {"a": 20, "b": 19}


def test_nobody_can_bid_coins_they_do_not_hold(duel):
    state = fixed_order(duel, [3, 5, 1, 2, 4])
    assert duel.normalize_choice(state, str(STARTING_COINS), "a") is not None
    assert duel.normalize_choice(state, str(STARTING_COINS + 1), "a") is None
    bid(duel, state, 15, 1)
    # a spent fifteen: the same bid is now legal for b and illegal for a.
    assert duel.normalize_choice(state, "6", "b") == "6"
    assert duel.normalize_choice(state, "6", "a") is None


def test_the_seats_are_interchangeable(duel):
    """Swapping the seats swaps the winner — the module can't favour a side."""
    for a, b in [(7, 4), (4, 7), (0, 1), (20, 0), (5, 5)]:
        forward, mirrored = fixed_order(duel, [3, 5, 1, 2, 4]), fixed_order(
            duel, [3, 5, 1, 2, 4]
        )
        bid(duel, forward, a, b)
        bid(duel, mirrored, b, a)
        first = forward.private["last"]["winner"]
        second = mirrored.private["last"]["winner"]
        if first is None:
            assert second is None
        else:
            assert second == other_side(first)


@pytest.mark.parametrize("amount", [0, 1, 9, 20])
def test_letting_the_window_lapse_loses_the_auction(duel, amount):
    state = fixed_order(duel, [3, 5, 1, 2, 4])
    bid(duel, state, amount, None)
    assert state.private["vp"] == {"a": 3, "b": 0}
    assert state.private["coins"]["b"] == STARTING_COINS  # a no-show spends none


def test_a_double_no_show_rolls_the_prize_forward(duel):
    state = fixed_order(duel, [3, 5, 1, 2, 4])
    assert bid(duel, state, None, None) is None
    assert state.private["vp"] == {"a": 0, "b": 0}
    assert state.private["coins"] == {"a": 20, "b": 20}
    assert state.private["pot"] == 3 + 5


# --- Ties and rollover ---

def test_a_tied_auction_pays_nobody_and_still_spends_both_bids(duel):
    state = fixed_order(duel, [2, 5, 1, 3, 4])
    bid(duel, state, 4, 4)
    assert state.private["vp"] == {"a": 0, "b": 0}
    assert state.private["coins"] == {"a": 16, "b": 16}


def test_a_tied_prize_rolls_into_the_next_lot(duel):
    state = fixed_order(duel, [2, 5, 1, 3, 4])
    bid(duel, state, 4, 4)
    assert state.private["pot"] == 2 + 5      # the spec's worked example
    assert state.private["auction"] == 2
    bid(duel, state, 5, 1)
    assert state.private["vp"] == {"a": 7, "b": 0}


def test_two_ties_in_a_row_keep_stacking(duel):
    state = fixed_order(duel, [1, 2, 3, 4, 5])
    bid(duel, state, 1, 1)
    bid(duel, state, 1, 1)
    assert state.private["pot"] == 1 + 2 + 3
    bid(duel, state, 2, 1)
    assert state.private["vp"] == {"a": 6, "b": 0}


# --- Ending the duel ---

def test_the_most_victory_points_wins_however_they_were_bought(duel):
    """Three cheap lots lose to one big one: this is why the module scores the
    match instead of the engine counting round wins."""
    state = fixed_order(duel, [1, 2, 3, 5, 4])
    assert bid(duel, state, 2, 1) is None     # a takes 1 VP
    assert bid(duel, state, 2, 1) is None     # a takes 2 VP
    assert bid(duel, state, 2, 1) is None     # a takes 3 VP — three wins to none
    assert bid(duel, state, 1, 9) is None     # b takes 5 VP with one big bid
    assert bid(duel, state, 1, 4) == "b"      # and 4 VP with what's left: 9 - 6
    assert state.private["vp"] == {"a": 6, "b": 9}


def test_coins_left_over_are_worth_nothing(duel):
    state = fixed_order(duel, [1, 2, 3, 4, 5])
    for _ in range(AUCTIONS):
        winner = bid(duel, state, 0, 1)
    assert winner == "b"
    assert state.private["coins"]["a"] == STARTING_COINS      # every coin kept
    assert state.private["vp"] == {"a": 0, "b": sum(PRIZES)}  # and none of it counted


def test_the_duel_is_not_decided_before_the_last_lot_is_sold(duel):
    state = fixed_order(duel, [5, 4, 3, 2, 1])
    for _ in range(AUCTIONS - 1):
        assert bid(duel, state, 3, 1) is None
    assert bid(duel, state, 3, 1) == "a"


# --- Overtime ---

def test_a_tie_on_the_last_lot_goes_to_overtime_for_that_prize(duel):
    state = fixed_order(duel, [1, 2, 3, 4, 5])
    for _ in range(AUCTIONS - 1):
        bid(duel, state, 2, 1)                 # a takes the first four lots
    assert bid(duel, state, 1, 1) is None      # the 5 VP lot ties
    private = state.private
    assert private["overtime"] is True
    assert private["pot"] == 5                 # the unsold lot is the overtime lot
    assert private["overtime_coins"] == {"a": OVERTIME_COINS, "b": OVERTIME_COINS}


def test_overtime_is_fought_with_a_fresh_equal_pool(duel):
    state = fixed_order(duel, [1, 2, 3, 4, 5])
    for _ in range(AUCTIONS - 1):
        bid(duel, state, 2, 1)
    bid(duel, state, 1, 1)
    private = state.private
    spent = dict(private["coins"])
    # The real balances are untouched by overtime, and the temporary pool caps
    # the bid instead.
    assert duel.normalize_choice(state, str(OVERTIME_COINS), "a") is not None
    assert duel.normalize_choice(state, str(OVERTIME_COINS + 1), "a") is None
    assert bid(duel, state, 4, 2) == "a"
    assert private["coins"] == spent
    assert private["vp"]["a"] == 1 + 2 + 3 + 4 + 5


def test_a_tied_overtime_refills_both_pools_and_runs_again(duel):
    state = fixed_order(duel, [1, 2, 3, 4, 5])
    for _ in range(AUCTIONS - 1):
        bid(duel, state, 2, 1)
    bid(duel, state, 1, 1)
    assert bid(duel, state, 5, 5) is None
    assert state.private["overtime_coins"] == {
        "a": OVERTIME_COINS, "b": OVERTIME_COINS,
    }
    assert state.private["pot"] == 5
    assert bid(duel, state, 5, 4) == "a"


def test_a_level_score_with_every_lot_sold_still_goes_to_overtime(duel):
    """Prizes 1-5 total an odd 15, so ordinary play cannot end level and this
    branch never fires — which is exactly why it is worth pinning. It is what
    stops a duel from stopping undecided if that prize set ever changes.
    """
    state = fixed_order(duel, [1, 2, 3, 4, 5])
    for _ in range(AUCTIONS - 1):
        bid(duel, state, 2, 1)                 # a takes the first four lots
    state.private["vp"] = {"a": 1, "b": 6}     # doctored to a level finish
    assert bid(duel, state, 2, 1) is None      # a takes the 5 VP lot: 6 - 6
    assert state.private["vp"] == {"a": 6, "b": 6}
    assert state.private["overtime"] is True
    assert state.private["pot"] == OVERTIME_PRIZE
    assert bid(duel, state, 2, 1) == "a"


# --- Choice validation ---

@pytest.mark.parametrize("raw,expected", [
    ("7", "7"), (" 7 ", "7"), ("\t12\n", "12"), (0, "0"), (20, "20"),
])
def test_bids_are_normalised(duel, raw, expected):
    assert duel.normalize_choice(duel.new_duel(1), raw, "a") == expected


@pytest.mark.parametrize("bad", [
    "", "   ", "-1", "3.5", "+4", "1e2", "twenty", "٧", "21", "1 2", "0x4",
    None, [], {}, ["4"], {"choice": 4}, -1, 3.5, 21,
    "4" * 100, "x" * (MAX_CHOICE_CHARS + 1),
])
def test_illegal_bids_are_rejected_without_raising(duel, bad):
    assert duel.normalize_choice(duel.new_duel(1), bad, "a") is None


def test_a_bid_without_a_seat_is_not_a_bid(duel):
    state = duel.new_duel(1)
    assert duel.normalize_choice(state, "4") is None
    assert duel.normalize_choice(state, "4", "c") is None


# --- The reveal rule, and the prize order ---

def carriers(view: dict) -> list:
    """Every place in a served view a *placed* bid can appear.

    A repr scan is no use here — the view is full of small integers that are
    coins, points and counters — so this enumerates the channels instead.
    """
    payload = view["payload"]
    return [view["choices"], view["history"], payload["log"], payload["last"]]


@pytest.mark.parametrize("a,b", [(0, 0), (7, 4), (4, 7), (20, 0), (5, 5), (1, 2)])
def test_no_bid_reaches_the_other_seat_before_both_are_locked(duel, a, b):
    state = duel.new_duel(1)
    state.choices["a"] = duel.normalize_choice(state, str(a), "a")
    state.choices["b"] = duel.normalize_choice(state, str(b), "b")
    for side in SIDES:
        view = duel.public(state, side=side, revealed=False)
        assert view["choices"] == {side: state.choices[side]}
        assert view["locked"] == {"a": True, "b": True}
        assert carriers(view) == [{side: state.choices[side]}, [], [], None]


def test_a_grandmaster_sees_neither_bid_before_reveal(duel):
    state = duel.new_duel(1)
    state.choices.update({"a": "7", "b": "4"})
    view = duel.public(state, side=None, revealed=False)
    assert view["choices"] == {}
    assert view["payload"]["max_bid"] == 0    # and nothing to bid with
    assert carriers(view) == [{}, [], [], None]


def test_reveal_shows_both_bids(duel):
    state = duel.new_duel(1)
    state.choices.update({"a": "7", "b": "4"})
    for side in (*SIDES, None):
        view = duel.public(state, side=side, revealed=True)
        assert view["choices"] == {"a": "7", "b": "4"}


def test_only_this_lot_and_the_next_are_published(duel):
    """Knowing the 5 VP prize was still to come would settle every bid before
    it, so the rest of the shuffled order never leaves the server."""
    state = fixed_order(duel, [1, 2, 3, 4, 5])
    for expected_prize, expected_next in [(1, 2), (2, 3), (3, 4), (4, 5), (5, None)]:
        for side in (*SIDES, None):
            payload = duel.public(state, side=side, revealed=False)["payload"]
            assert payload["prize"] == expected_prize
            assert payload["next_prize"] == expected_next
            assert "prizes" not in payload
            assert [5] != payload.get("log")     # nothing else names the order
        bid(duel, state, 2, 1)


def test_the_rolled_up_prize_is_what_the_next_lot_shows(duel):
    state = fixed_order(duel, [2, 5, 1, 3, 4])
    bid(duel, state, 4, 4)                        # tie: 2 VP rolls forward
    payload = duel.public(state, side="a", revealed=False)["payload"]
    assert payload["prize"] == 7 and payload["next_prize"] == 1


def test_balances_and_points_are_public_to_both_seats(duel):
    state = fixed_order(duel, [3, 5, 1, 2, 4])
    bid(duel, state, 7, 4)
    for side in (*SIDES, None):
        payload = duel.public(state, side=side, revealed=False)["payload"]
        assert payload["coins"] == {"a": 13, "b": 16}
        assert payload["vp"] == {"a": 3, "b": 0}
        assert payload["log"][0]["winner"] == "a"


def test_max_bid_is_the_viewers_own_purse(duel):
    state = fixed_order(duel, [3, 5, 1, 2, 4])
    bid(duel, state, 7, 4)
    assert duel.public(state, "a", False)["payload"]["max_bid"] == 13
    assert duel.public(state, "b", False)["payload"]["max_bid"] == 16


def test_public_view_shape(duel):
    state = duel.new_duel(5)
    view = duel.public(state, side="b", revealed=False)
    assert set(view) == {
        "duel_game_id", "rules_version", "round", "wins", "history",
        "you", "locked", "choices", "payload",
    }
    assert view["rules_version"] == DUEL_RULES_VERSION
    assert view["you"] == "b"
    assert view["payload"]["kind"] == "bid_war"
    assert view["payload"]["auctions"] == AUCTIONS
    assert view["payload"]["starting_coins"] == STARTING_COINS


def test_public_view_is_a_copy(duel):
    """Mutating a served view must not reach back into live duel state."""
    state = fixed_order(duel, [3, 5, 1, 2, 4])
    bid(duel, state, 7, 4)
    view = duel.public(state, side="a", revealed=True)
    view["payload"]["coins"]["a"] = 999
    view["payload"]["vp"]["a"] = 999
    view["payload"]["log"][0]["vp"]["a"] = 999
    view["payload"]["overtime_coins"]["a"] = 999
    assert state.private["coins"] == {"a": 13, "b": 16}
    assert state.private["vp"] == {"a": 3, "b": 0}
    assert state.private["log"][0]["vp"] == {"a": 3, "b": 0}
    assert state.private["overtime_coins"] == {
        "a": OVERTIME_COINS, "b": OVERTIME_COINS,
    }


# --- Module hygiene ---

def test_module_is_stateless_across_duels(duel):
    first = duel.new_duel(1)
    bid(duel, first, 7, 4)
    second = duel.new_duel(1)
    assert second.private["coins"] == {"a": STARTING_COINS, "b": STARTING_COINS}
    assert second.private["vp"] == {"a": 0, "b": 0}
    assert second.private["auction"] == 1


def test_reset_safe_and_deterministic_after(duel):
    before = duel.new_duel(4).private
    assert duel.reset() is None
    assert duel.reset() is None  # idempotent
    assert duel.new_duel(4).private == before


def test_the_overtime_prize_constant_is_the_smallest_lot(duel):
    """A tiebreak should decide the duel, not rewrite its scoreline."""
    assert OVERTIME_PRIZE == min(PRIZES)
