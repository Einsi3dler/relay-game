"""BULLSHIT QUIZ perk draft (backend/bsdraft.py) and its place in the room.

The draft's promises: every player appears once in the order and ends with
exactly one perk, no perk goes to two players, every card has one owner, a
trade preserves both hands' sizes and happens whole or not at all, and each
turn makes exactly one assignment.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from backend import bsdraft, bsperks, bsroom, config
from backend.bsdraft import Refused
from backend.bsroom import _spend
from tests.test_bsroom import draft_everyone, make_room, play_round, started

PLAYERS = ["ada", "tolu", "divine", "emeka"]
POOL = ["kingmaker", "lone_wolf", "blackmail", "the_audit", "pickpocket"]
TURN = config.BS_DRAFT_PICK_SECONDS


def draft(n: int = 20, players: list[str] | None = None, seed: int = 1) -> bsdraft.Draft:
    players = players or PLAYERS
    hands = bsdraft.deal_hands(players, list(range(1, n)))
    pool = [p.id for p in bsperks.PERKS][:max(len(players), len(POOL))]
    return bsdraft.new_draft(players, hands, pool, random.Random(seed))


def place(d: bsdraft.Draft, order: list[str]) -> None:
    """Fix the order for a test, as if the shuffle came out this way."""
    d.order[:] = order
    d.original_order[:] = order


def card(d, player, value):
    return next(c for c in d.hands[player] if c.value == value)


def values(d, player):
    return sorted(c.value for c in d.hands[player])


# --- setting up -----------------------------------------------------------

def test_everybody_starts_with_one_card_per_value_each_with_its_own_id():
    hands = bsdraft.deal_hands(PLAYERS, list(range(1, 20)))
    for player, cards in hands.items():
        assert [c.value for c in cards] == list(range(1, 20))
    ids = [c.id for cards in hands.values() for c in cards]
    assert len(ids) == len(set(ids)) == 4 * 19
    assert card_ids_are_named(hands)


def card_ids_are_named(hands) -> bool:
    return all(c.id == f"{p}-card-{c.value}" for p, cards in hands.items() for c in cards)


def test_the_order_is_one_shuffle_of_everybody():
    d = draft()
    assert sorted(d.order) == sorted(PLAYERS)
    assert d.original_order == d.order
    assert d.stage == bsdraft.TRADING and d.active() is None


def test_a_perk_for_everybody_or_no_draft():
    with pytest.raises(Refused, match="at most 2"):
        bsdraft.new_draft(PLAYERS, bsdraft.deal_hands(PLAYERS, [1, 2, 3]), ["a", "b"], random.Random(1))


# --- trading --------------------------------------------------------------

def test_the_handoffs_example_trade():
    """Divine, sixth, offers Ada, first, his 19 for her 1."""
    players = ["ada", "p1", "p2", "p3", "p4", "divine"]
    d = draft(20, players)
    place(d, players)
    offer = bsdraft.propose(d, "divine", "ada", "divine-card-19", "ada-card-1")
    assert (offer.proposer_position, offer.recipient_position) == (5, 0)
    assert d.order[0] == "ada"                      # nothing yet: an offer is not a trade
    bsdraft.respond(d, "ada", offer.id, "accept")
    assert d.order[0] == "divine" and d.order[5] == "ada"
    assert values(d, "ada").count(19) == 2 and 1 not in values(d, "ada")
    assert values(d, "divine").count(1) == 2 and 19 not in values(d, "divine")
    assert len(d.hands["ada"]) == len(d.hands["divine"]) == 19
    assert offer.status == bsdraft.ACCEPTED


def test_only_the_recipient_accepts_and_only_the_proposer_cancels():
    d = draft()
    offer = bsdraft.propose(d, "ada", "tolu", "ada-card-3", "tolu-card-4")
    with pytest.raises(Refused, match="isn't yours"):
        bsdraft.respond(d, "ada", offer.id, "accept")
    with pytest.raises(Refused, match="cancel"):
        bsdraft.respond(d, "tolu", offer.id, "cancel")
    bsdraft.respond(d, "ada", offer.id, "cancel")
    assert offer.status == bsdraft.CANCELLED
    with pytest.raises(Refused, match="no longer open"):
        bsdraft.respond(d, "tolu", offer.id, "accept")


def test_declining_changes_nothing():
    d = draft()
    before = list(d.order)
    offer = bsdraft.propose(d, "ada", "tolu", "ada-card-3", "tolu-card-4")
    bsdraft.respond(d, "tolu", offer.id, "decline")
    assert offer.status == bsdraft.DECLINED and d.order == before


@pytest.mark.parametrize("give, take, why", [
    ("tolu-card-3", "tolu-card-4", "your own"),
    ("ada-card-3", "ada-card-4", "their cards"),
    ("ada-card-99", "tolu-card-4", "your own"),
])
def test_an_offer_names_your_card_and_theirs(give, take, why):
    with pytest.raises(Refused, match=why):
        bsdraft.propose(draft(), "ada", "tolu", give, take)


def test_no_trading_with_yourself():
    with pytest.raises(Refused, match="somebody else"):
        bsdraft.propose(draft(), "ada", "ada", "ada-card-1", "ada-card-2")


def test_an_accepted_trade_expires_every_other_offer_involving_either():
    d = draft()
    keep = bsdraft.propose(d, "divine", "emeka", "divine-card-2", "emeka-card-2")
    stale = bsdraft.propose(d, "ada", "divine", "ada-card-5", "divine-card-6")
    deal = bsdraft.propose(d, "tolu", "ada", "tolu-card-7", "ada-card-8")
    bsdraft.respond(d, "ada", deal.id, "accept")
    assert stale.status == bsdraft.EXPIRED
    assert keep.status == bsdraft.PENDING


def test_a_stale_offer_changes_nothing():
    """Terms are checked against the draft as it is now; if anything they
    name has moved, the whole trade is refused and nothing changes."""
    d = draft()
    offer = bsdraft.propose(d, "ada", "tolu", "ada-card-3", "tolu-card-4")
    d.hands["ada"].remove(card(d, "ada", 3))         # as if it had gone elsewhere
    d.hands["emeka"].append(bsdraft.WagerCard("ada-card-3", 3))
    order, hands = list(d.order), {p: values(d, p) for p in PLAYERS}
    with pytest.raises(Refused, match="changed hands"):
        bsdraft.respond(d, "tolu", offer.id, "accept")
    assert d.order == order and {p: values(d, p) for p in PLAYERS} == hands
    assert offer.status == bsdraft.PENDING


def test_moved_positions_refuse_the_offer():
    d = draft()
    offer = bsdraft.propose(d, "ada", "tolu", "ada-card-3", "tolu-card-4")
    d.order.reverse()
    with pytest.raises(Refused, match="order has changed"):
        bsdraft.respond(d, "tolu", offer.id, "accept")


def test_too_many_open_offers():
    d = draft(20, [f"p{i}" for i in range(8)])
    for i in range(1, config.BS_DRAFT_MAX_OFFERS + 1):
        bsdraft.propose(d, "p0", f"p{i}", "p0-card-1", f"p{i}-card-1")
    with pytest.raises(Refused, match="Cancel one"):
        bsdraft.propose(d, "p0", "p7", "p0-card-1", "p7-card-1")


def test_many_random_trades_keep_every_card_and_every_player():
    rng = random.Random(7)
    players = [f"p{i}" for i in range(10)]
    d = draft(10, players)
    every = Counter(c.id for cards in d.hands.values() for c in cards)
    for _ in range(300):
        a, b = rng.sample(players, 2)
        offer = bsdraft.propose(d, a, b, rng.choice(d.hands[a]).id, rng.choice(d.hands[b]).id)
        bsdraft.respond(d, b, offer.id, rng.choice(["accept", "decline"]))
    assert sorted(d.order) == sorted(players)
    assert all(len(cards) == 9 for cards in d.hands.values())
    assert Counter(c.id for cards in d.hands.values() for c in cards) == every


# --- picking --------------------------------------------------------------

def test_picking_runs_down_the_order_one_perk_each():
    d = draft()
    bsdraft.start_picking(d, now=0)
    taken = []
    for i in range(4):
        player = d.active()
        assert player == d.order[i]
        perk = d.available[-1]
        bsdraft.pick(d, player, perk, d.turn, now=i)
        taken.append(perk)
    assert d.stage == bsdraft.COMPLETE and d.active() is None
    assert sorted(d.picks) == sorted(PLAYERS)
    assert len(set(d.picks.values())) == 4 and set(d.picks.values()) == set(taken)


def test_a_pick_is_checked_against_the_turn_the_player_and_the_pool():
    d = draft()
    bsdraft.start_picking(d, now=0)
    first, second = d.order[0], d.order[1]
    with pytest.raises(Refused, match="isn't your pick"):
        bsdraft.pick(d, second, d.available[0], d.turn, now=1)
    with pytest.raises(Refused, match="turn has passed"):
        bsdraft.pick(d, first, d.available[0], d.turn - 1, now=1)
    with pytest.raises(Refused, match="gone"):
        bsdraft.pick(d, first, "not_a_perk", d.turn, now=1)
    with pytest.raises(Refused, match="Time"):
        bsdraft.pick(d, first, d.available[0], d.turn, now=TURN)


def test_a_double_tap_assigns_once():
    d = draft()
    bsdraft.start_picking(d, now=0)
    first, turn = d.active(), d.turn
    bsdraft.pick(d, first, d.available[0], turn, now=1)
    with pytest.raises(Refused):
        bsdraft.pick(d, first, d.available[0], turn, now=1)
    assert len(d.picks) == 1


def test_running_out_of_time_gets_a_random_perk():
    d = draft()
    bsdraft.start_picking(d, now=0)
    first = d.active()
    assert not bsdraft.tick(d, now=TURN - 0.1, rng=random.Random(1))
    assert bsdraft.tick(d, now=TURN, rng=random.Random(1))
    assert first in d.picks and d.active() == d.order[1]
    assert d.log[-1]["auto"] is True
    assert d.deadline == TURN + TURN                     # the next player's full turn


def test_a_turn_locks_the_active_place_but_not_the_ones_still_to_come():
    d = draft()
    bsdraft.start_picking(d, now=0)
    active, later, last = d.order[0], d.order[2], d.order[3]
    with pytest.raises(Refused, match="still to come"):
        bsdraft.propose(d, later, active, f"{later}-card-1", f"{active}-card-1")
    offer = bsdraft.propose(d, last, later, f"{last}-card-1", f"{later}-card-2")
    bsdraft.respond(d, later, offer.id, "accept")
    assert d.order[2] == last and d.order[3] == later


def test_an_offer_to_the_next_player_expires_when_their_turn_starts():
    d = draft()
    nxt, after = d.order[1], d.order[2]
    offer = bsdraft.propose(d, after, nxt, f"{after}-card-1", f"{nxt}-card-1")
    bsdraft.start_picking(d, now=0)
    bsdraft.pick(d, d.active(), d.available[0], d.turn, now=1)
    assert offer.status == bsdraft.EXPIRED


def test_the_end_of_the_draft_expires_everything_left_open():
    d = draft()
    bsdraft.start_picking(d, now=0)
    for _ in range(3):
        bsdraft.pick(d, d.active(), d.available[0], d.turn, now=1)
    assert all(o.status != bsdraft.PENDING for o in d.offers)
    bsdraft.pick(d, d.active(), d.available[0], d.turn, now=1)
    with pytest.raises(Refused, match="over"):
        bsdraft.propose(d, "ada", "tolu", "ada-card-1", "tolu-card-1")


# --- what players see -----------------------------------------------------

def test_offers_are_seen_by_the_two_players_only():
    d = draft()
    bsdraft.propose(d, "ada", "tolu", "ada-card-3", "tolu-card-4")
    assert [o["give"]["value"] for o in bsdraft.offers_for(d, "tolu")] == [3]
    assert bsdraft.offers_for(d, "emeka") == []


def test_the_public_view_shows_duplicate_cards():
    players = ["ada", "divine"]
    d = draft(5, players)
    offer = bsdraft.propose(d, "divine", "ada", "divine-card-4", "ada-card-1")
    bsdraft.respond(d, "ada", offer.id, "accept")
    view = bsdraft.public_view(d, now=0)
    assert [c["value"] for c in view["hands"]["ada"]] == [2, 3, 4, 4]


# --- in the room ----------------------------------------------------------

def test_the_game_opens_with_the_draft_and_the_host_starts_the_quiz():
    room, qs = make_room(5)
    bsroom.start(room, qs, 7)
    assert room.phase == "draft" and room.panel is None
    view = room.public("p0", now=0)
    assert view["draft"]["stage"] == "trading" and len(view["cards"]) == 7    # one per round
    with pytest.raises(bsroom.Rejected, match="Finish the draft"):
        bsroom.begin_quiz(room)
    draft_everyone(room)
    bsroom.begin_quiz(room)
    assert room.phase == "briefing"
    assert all(seat.perk for seat in room.seats.values())


def test_the_room_runs_out_a_draft_turn_on_its_own():
    room, qs = make_room(4)
    bsroom.start(room, qs, len(room.seats))
    bsroom.draft_go(room, now=0)
    first = room.draft.active()
    assert bsroom.tick(room, now=TURN)
    assert room.seats[first].perk is not None


def test_a_traded_duplicate_can_be_wagered_twice():
    room, qs = make_room(4)
    bsroom.start(room, qs, 3)                 # three rounds: cards 1, 2 and 3
    d = room.draft
    a, b = d.order[0], d.order[1]
    bsroom.draft_offer(room, b, a, f"{b}-card-3", f"{a}-card-1")
    bsroom.draft_respond(room, a, d.offers[-1].id, "accept")
    assert room.left(a).count(3) == 2
    draft_everyone(room)
    bsroom.begin_quiz(room)
    seat = room.seats[a]
    _spend(room, seat, 3)
    _spend(room, seat, 3)
    with pytest.raises(bsroom.Rejected, match="no 3 card"):
        _spend(room, seat, 3)


def test_a_full_game_after_a_draft_spends_a_card_every_wagering_turn():
    """One card per round, one spent per round you wager in: whoever was
    truthful keeps one card for each of those rounds, and nobody runs out."""
    room = started(5, seed=4)
    while room.phase != "final":
        play_round(room)
        bsroom.next_round(room)
    truthful = Counter(entry["author"] for entry in room.history)
    assert len(room.history) == room.rounds
    assert all(len(room.left(pid)) == truthful[pid] for pid in room.seats)


def test_more_players_than_perks_cannot_start():
    room, qs = make_room(len(bsperks.PERKS) + 1)
    with pytest.raises(bsroom.Rejected, match="at most"):
        bsroom.start(room, qs, len(room.seats))
