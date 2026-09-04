"""BID WAR module suite — docs/DUEL_MODULE_SPEC.md §"Tests your duel must ship
with", plus the auction rules the handoff spec calls out by name.

Three carry the game now that it is **staked**: both bids are always spent, the
two purses come from the Grandmasters and are deliberately unequal, and a lot's
value is rolled against what is still on the table rather than drawn from a
ladder anyone could count.
"""

from __future__ import annotations

import math

import pytest

from backend import config
from backend.games.duel4_bid_war import (
    AUCTIONS,
    OVERTIME_COINS,
    BidWar,
    roll_lot,
)
from backend.games.duel_base import (
    DUEL_RULES_VERSION,
    MAX_CHOICE_CHARS,
    SIDES,
    DuelState,
    other_side,
)

RICH = {"a": 20, "b": 20}


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


def scripted(duel: BidWar, pot: int, stakes: dict | None = None) -> DuelState:
    """A duel whose *current* lot is pinned, so an auction can be scripted.

    Only the open lot: the ones after it are rolled from purses that do not
    exist yet, which is the point of the redesign.
    """
    state = duel.new_duel(1, dict(stakes or RICH))
    state.private["pot"] = pot
    return state


def floor_for(on_table: int) -> int:
    return max(1, math.ceil(on_table / config.DUEL_STAKE_LOT_FLOOR_DIVISOR))


# --- Generation ---

def test_determinism(duel):
    first, second = duel.new_duel(11, dict(RICH)), duel.new_duel(11, dict(RICH))
    assert first.payload == second.payload
    assert first.private == second.private
    assert first.duel_game_id == second.duel_game_id == "bid_war"


def test_the_purses_are_whatever_the_grandmasters_staked(duel):
    """Unequal on purpose: a Grandmaster who backs their champion buys them a
    bigger hand, and pays for it out of the perk shop."""
    state = duel.new_duel(3, {"a": 30, "b": 4})
    assert state.private["coins"] == {"a": 30, "b": 4}
    assert state.private["staked"] == {"a": 30, "b": 4}
    assert state.private["won"] == {"a": 0, "b": 0}
    assert state.private["auction"] == 1 and state.private["overtime"] is False
    assert state.round_index == 1 and state.choices == {}


@pytest.mark.parametrize("stakes", [None, {}, {"a": 5}, {"a": -3, "b": "x"}])
def test_a_missing_or_broken_stake_is_a_grant_of_nothing(duel, stakes):
    """Never an exception: a duel that refused to open would strand both
    teams, and bidding zero is a playable, if bleak, position."""
    state = duel.new_duel(2, stakes)
    for side in SIDES:
        assert state.private["coins"][side] >= 0
    assert duel.normalize_choice(state, "0", "b") == "0"


def test_the_module_is_the_staked_one(duel):
    assert duel.staked is True
    assert duel.choice_seconds == 10
    # The module scores the match itself, so one returned winner *is* the duel.
    assert duel.wins_needed == 1
    assert duel._payload(duel.new_duel(1, dict(RICH)), "a")["choice_seconds"] == 10


# --- What a lot is worth ---

def test_a_lot_is_never_worth_less_than_the_floor(duel):
    """The rule that keeps a late auction worth contesting: a lot is always
    worth at least the money still on the table over the divisor."""
    for on_table in (0, 1, 7, 40, 137):
        for auction in range(1, AUCTIONS + 2):
            value = roll_lot(99, auction, on_table)
            assert value >= floor_for(on_table)
            assert value <= max(
                floor_for(on_table),
                int(floor_for(on_table) * config.DUEL_STAKE_LOT_SPREAD),
            )


def test_a_lot_is_never_worthless(duel):
    """Including when both purses are empty. A lot nobody would bid on is not
    a lot, and it would make the last auctions meaningless."""
    assert roll_lot(1, 1, 0) >= 1


def test_the_opening_lot_is_rolled_against_both_stakes(duel):
    state = duel.new_duel(7, {"a": 12, "b": 8})
    assert state.private["pot"] == roll_lot(7, 1, 20)
    assert state.private["pot"] >= floor_for(20)


def test_lots_are_not_a_ladder_anyone_can_count(duel):
    """Not a proof of randomness. Just that the value moves, so paying over
    the odds because 'this must be the big one' is a real risk."""
    values = {roll_lot(seed, 1, 40) for seed in range(40)}
    assert len(values) > 1


def test_the_floor_falls_with_the_money_left(duel):
    """As purses drain the lots shrink, which is what stops the last auction
    being worth more than anyone can pay for it."""
    assert floor_for(40) > floor_for(10) > floor_for(2)
    assert roll_lot(5, 2, 40) > roll_lot(5, 2, 2)


# --- Bidding ---

def test_the_higher_bid_takes_the_lot(duel):
    state = scripted(duel, 30)
    bid(duel, state, 7, 4)
    assert state.private["won"] == {"a": 30, "b": 0}


def test_both_bids_are_spent_even_by_the_loser(duel):
    state = scripted(duel, 30)
    bid(duel, state, 7, 4)
    assert state.private["coins"] == {"a": 13, "b": 16}   # never refunded


def test_a_bid_of_zero_is_legal(duel):
    state = scripted(duel, 30)
    assert duel.normalize_choice(state, "0", "a") == "0"
    bid(duel, state, 0, 1)
    assert state.private["won"] == {"a": 0, "b": 30}
    assert state.private["coins"] == {"a": 20, "b": 19}


def test_nobody_can_bid_coins_they_do_not_hold(duel):
    state = scripted(duel, 30, {"a": 20, "b": 6})
    assert duel.normalize_choice(state, "20", "a") is not None
    assert duel.normalize_choice(state, "21", "a") is None
    # The pools are unequal, so the same bid is legal for one seat only.
    assert duel.normalize_choice(state, "9", "a") == "9"
    assert duel.normalize_choice(state, "9", "b") is None


def test_the_seats_are_interchangeable(duel):
    """Swapping the seats swaps the winner — the module can't favour a side."""
    for a, b in [(7, 4), (4, 7), (0, 1), (20, 0), (5, 5)]:
        forward, mirrored = scripted(duel, 30), scripted(duel, 30)
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
    state = scripted(duel, 30)
    bid(duel, state, amount, None)
    assert state.private["won"] == {"a": 30, "b": 0}
    assert state.private["coins"]["b"] == 20  # a no-show spends none


def test_a_double_no_show_rolls_the_lot_forward(duel):
    state = scripted(duel, 30)
    assert bid(duel, state, None, None) is None
    assert state.private["won"] == {"a": 0, "b": 0}
    assert state.private["coins"] == {"a": 20, "b": 20}
    assert state.private["pot"] > 30       # the unsold lot rides on the next


# --- Ties and rollover ---

def test_a_tied_auction_pays_nobody_and_still_spends_both_bids(duel):
    state = scripted(duel, 30)
    bid(duel, state, 4, 4)
    assert state.private["won"] == {"a": 0, "b": 0}
    assert state.private["coins"] == {"a": 16, "b": 16}


def test_a_tied_lot_rolls_into_the_next_one(duel):
    state = scripted(duel, 30)
    bid(duel, state, 4, 4)
    assert state.private["auction"] == 2
    # The next lot is its own roll *plus* the unsold one riding on top.
    assert state.private["pot"] == 30 + roll_lot(state.private["seed"], 2, 32)
    carried = state.private["pot"]
    bid(duel, state, 5, 1)
    assert state.private["won"] == {"a": carried, "b": 0}


def test_two_ties_in_a_row_keep_stacking(duel):
    state = scripted(duel, 10)
    bid(duel, state, 1, 1)
    first_carry = state.private["pot"]
    assert first_carry > 10
    bid(duel, state, 1, 1)
    assert state.private["pot"] > first_carry
    stacked = state.private["pot"]
    bid(duel, state, 2, 1)
    assert state.private["won"] == {"a": stacked, "b": 0}


# --- Ending the duel ---

def test_the_most_coins_won_takes_the_duel_however_they_were_bought(duel):
    """One big lot beats three cheap ones: this is why the module scores the
    match instead of the engine counting round wins."""
    state = scripted(duel, 4)
    for _ in range(AUCTIONS - 1):
        assert bid(duel, state, 2, 1) is None       # a keeps taking small lots
        state.private["pot"] = 4
    state.private["pot"] = 500                      # one enormous last lot
    assert bid(duel, state, 1, 9) == "b"
    assert state.private["won"]["b"] > state.private["won"]["a"]


def test_coins_left_over_are_worth_nothing(duel):
    state = scripted(duel, 20)
    winner = None
    for _ in range(AUCTIONS):
        winner = bid(duel, state, 0, 1)
    assert winner == "b"
    assert state.private["coins"]["a"] == 20        # every coin kept
    assert state.private["won"]["a"] == 0           # and none of it counted


def test_the_duel_is_not_decided_before_the_last_lot_is_sold(duel):
    state = scripted(duel, 20)
    for _ in range(AUCTIONS - 1):
        assert bid(duel, state, 3, 1) is None
    assert bid(duel, state, 3, 1) == "a"


# --- Settlement: what goes back to the purse ---

def test_settlement_pays_out_the_winnings(duel):
    state = scripted(duel, 30)
    bid(duel, state, 7, 4)
    assert duel.settlement(state) == {"a": 30, "b": 0}


def test_settlement_never_returns_the_stake(duel):
    """The gamble. A team that stakes 20 and wins 12 back is 8 down, and that
    is the trade a Grandmaster is making against the perk shop."""
    state = duel.new_duel(1, {"a": 20, "b": 20})
    state.private["pot"] = 12
    bid(duel, state, 5, 1)
    settled = duel.settlement(state)
    assert settled["a"] == 12                       # winnings only
    assert settled["a"] < state.private["staked"]["a"]


def test_settlement_is_zero_before_anything_is_won(duel):
    assert duel.settlement(duel.new_duel(1, dict(RICH))) == {"a": 0, "b": 0}


# --- Overtime ---

def test_a_tie_on_the_last_lot_goes_to_overtime_for_that_lot(duel):
    state = scripted(duel, 20)
    for _ in range(AUCTIONS - 1):
        bid(duel, state, 2, 1)                 # a takes the first four lots
        state.private["pot"] = 20
    assert bid(duel, state, 1, 1) is None      # the last lot ties
    private = state.private
    assert private["overtime"] is True
    assert private["pot"] == 20                # the unsold lot is the overtime lot
    assert private["overtime_coins"] == {"a": OVERTIME_COINS, "b": OVERTIME_COINS}


def test_overtime_is_fought_with_a_fresh_equal_pool(duel):
    """The one pool the team does not pay for. Purses can legitimately be
    empty by now, and a tiebreak nobody can bid in would never end."""
    state = scripted(duel, 20, {"a": 0, "b": 0})
    for _ in range(AUCTIONS):
        bid(duel, state, 0, 0)                 # nothing to bid: every lot ties
    private = state.private
    assert private["overtime"] is True
    assert duel.normalize_choice(state, str(OVERTIME_COINS), "a") is not None
    assert duel.normalize_choice(state, str(OVERTIME_COINS + 1), "a") is None
    spent = dict(private["coins"])
    assert bid(duel, state, 4, 2) == "a"
    assert private["coins"] == spent           # real balances untouched


def test_a_tied_overtime_refills_both_pools_and_runs_again(duel):
    state = scripted(duel, 20, {"a": 0, "b": 0})
    for _ in range(AUCTIONS):
        bid(duel, state, 0, 0)
    pot = state.private["pot"]
    assert bid(duel, state, 5, 5) is None
    assert state.private["overtime_coins"] == {
        "a": OVERTIME_COINS, "b": OVERTIME_COINS,
    }
    assert state.private["pot"] == pot
    assert bid(duel, state, 5, 4) == "a"


def test_a_level_score_with_every_lot_sold_still_goes_to_overtime(duel):
    """Rolled lots can and do produce two equal scores, so unlike the old
    odd-summed 1-5 table this branch is reachable in ordinary play. Without it
    a duel could stop undecided and leave both teams stuck."""
    state = scripted(duel, 20)
    for _ in range(AUCTIONS - 1):
        bid(duel, state, 2, 1)
        state.private["pot"] = 20
    state.private["won"] = {"a": 7, "b": 27}   # doctored to a level finish
    assert bid(duel, state, 2, 1) is None      # a takes the last 20: 27 - 27
    assert state.private["won"] == {"a": 27, "b": 27}
    assert state.private["overtime"] is True
    assert state.private["pot"] >= 1
    assert bid(duel, state, 2, 1) == "a"


# --- Choice validation ---

@pytest.mark.parametrize("raw,expected", [
    ("7", "7"), (" 7 ", "7"), ("\t12\n", "12"), (0, "0"), (20, "20"),
])
def test_bids_are_normalised(duel, raw, expected):
    assert duel.normalize_choice(duel.new_duel(1, dict(RICH)), raw, "a") == expected


@pytest.mark.parametrize("bad", [
    "", "   ", "-1", "3.5", "+4", "1e2", "twenty", "٧", "21", "1 2", "0x4",
    None, [], {}, ["4"], {"choice": 4}, -1, 3.5, 21,
    "4" * 100, "x" * (MAX_CHOICE_CHARS + 1),
])
def test_illegal_bids_are_rejected_without_raising(duel, bad):
    assert duel.normalize_choice(duel.new_duel(1, dict(RICH)), bad, "a") is None


def test_a_bid_without_a_seat_is_not_a_bid(duel):
    state = duel.new_duel(1, dict(RICH))
    assert duel.normalize_choice(state, "4") is None
    assert duel.normalize_choice(state, "4", "c") is None


# --- The reveal rule, and the lot that has not been rolled ---

def carriers(view: dict) -> list:
    """Every place in a served view a *placed* bid can appear.

    A repr scan is no use here — the view is full of small integers that are
    coins, winnings and counters — so this enumerates the channels instead.
    """
    payload = view["payload"]
    return [view["choices"], view["history"], payload["log"], payload["last"]]


@pytest.mark.parametrize("a,b", [(0, 0), (7, 4), (4, 7), (20, 0), (5, 5), (1, 2)])
def test_no_bid_reaches_the_other_seat_before_both_are_locked(duel, a, b):
    state = duel.new_duel(1, dict(RICH))
    state.choices["a"] = duel.normalize_choice(state, str(a), "a")
    state.choices["b"] = duel.normalize_choice(state, str(b), "b")
    for side in SIDES:
        view = duel.public(state, side=side, revealed=False)
        assert view["choices"] == {side: state.choices[side]}
        assert view["locked"] == {"a": True, "b": True}
        assert carriers(view) == [{side: state.choices[side]}, [], [], None]


def test_a_grandmaster_sees_neither_bid_before_reveal(duel):
    state = duel.new_duel(1, dict(RICH))
    state.choices.update({"a": "7", "b": "4"})
    view = duel.public(state, side=None, revealed=False)
    assert view["choices"] == {}
    assert view["payload"]["max_bid"] == 0    # and nothing to bid with
    assert carriers(view) == [{}, [], [], None]


def test_reveal_shows_both_bids(duel):
    state = duel.new_duel(1, dict(RICH))
    state.choices.update({"a": "7", "b": "4"})
    for side in (*SIDES, None):
        view = duel.public(state, side=side, revealed=True)
        assert view["choices"] == {"a": "7", "b": "4"}


def test_the_next_lot_is_never_published_because_it_does_not_exist(duel):
    """The old game showed one lot ahead off a pre-shuffled list. A lot whose
    floor depends on what this auction costs the pair of you cannot be shown
    before this auction resolves, so the lookahead is gone rather than faked.
    """
    state = scripted(duel, 20)
    for _ in range(AUCTIONS):
        for side in (*SIDES, None):
            payload = duel.public(state, side=side, revealed=False)["payload"]
            assert payload["next_prize"] is None
            assert "prizes" not in payload
        bid(duel, state, 2, 1)


def test_the_rolled_up_lot_is_what_the_next_auction_shows(duel):
    state = scripted(duel, 30)
    bid(duel, state, 4, 4)                        # tie: 30 rolls forward
    payload = duel.public(state, side="a", revealed=False)["payload"]
    assert payload["prize"] == state.private["pot"] > 30


def test_balances_and_winnings_are_public_to_both_seats(duel):
    state = scripted(duel, 30)
    bid(duel, state, 7, 4)
    for side in (*SIDES, None):
        payload = duel.public(state, side=side, revealed=False)["payload"]
        assert payload["coins"] == {"a": 13, "b": 16}
        assert payload["won"] == {"a": 30, "b": 0}
        assert payload["staked"] == {"a": 20, "b": 20}
        assert payload["log"][0]["winner"] == "a"


def test_max_bid_is_the_viewers_own_purse(duel):
    state = scripted(duel, 30)
    bid(duel, state, 7, 4)
    assert duel.public(state, "a", False)["payload"]["max_bid"] == 13
    assert duel.public(state, "b", False)["payload"]["max_bid"] == 16


def test_public_view_shape(duel):
    state = duel.new_duel(5, dict(RICH))
    view = duel.public(state, side="b", revealed=False)
    assert set(view) == {
        "duel_game_id", "rules_version", "round", "wins", "history",
        "you", "locked", "choices", "payload",
    }
    assert view["rules_version"] == DUEL_RULES_VERSION
    assert view["you"] == "b"
    assert view["payload"]["kind"] == "bid_war"
    assert view["payload"]["auctions"] == AUCTIONS
    assert view["payload"]["staked"] == RICH


def test_public_view_is_a_copy(duel):
    """Mutating a served view must not reach back into live duel state."""
    state = scripted(duel, 30)
    bid(duel, state, 7, 4)
    view = duel.public(state, side="a", revealed=True)
    view["payload"]["coins"]["a"] = 999
    view["payload"]["won"]["a"] = 999
    view["payload"]["staked"]["a"] = 999
    view["payload"]["log"][0]["won"]["a"] = 999
    view["payload"]["overtime_coins"]["a"] = 999
    assert state.private["coins"] == {"a": 13, "b": 16}
    assert state.private["won"] == {"a": 30, "b": 0}
    assert state.private["staked"] == {"a": 20, "b": 20}
    assert state.private["log"][0]["won"] == {"a": 30, "b": 0}
    assert state.private["overtime_coins"] == {
        "a": OVERTIME_COINS, "b": OVERTIME_COINS,
    }


# --- Module hygiene ---

def test_module_is_stateless_across_duels(duel):
    first = duel.new_duel(1, dict(RICH))
    bid(duel, first, 7, 4)
    second = duel.new_duel(1, dict(RICH))
    assert second.private["coins"] == RICH
    assert second.private["won"] == {"a": 0, "b": 0}
    assert second.private["auction"] == 1


def test_reset_safe_and_deterministic_after(duel):
    before = duel.new_duel(4, dict(RICH)).private
    assert duel.reset() is None
    assert duel.reset() is None  # idempotent
    assert duel.new_duel(4, dict(RICH)).private == before
