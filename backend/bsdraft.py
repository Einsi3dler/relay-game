"""BULLSHIT QUIZ perk draft: the phase before the quiz where every player
picks one unique perk.

The order is shuffled once and shown before any pick, so players can trade
places first. A trade is one offer with two linked exchanges: the two
players swap draft positions *and* swap two named wager cards. It happens
whole or not at all. Once the host starts the picks, each player gets
BS_DRAFT_PICK_SECONDS on the clock; a player whose turn has begun is locked
in place, and trading carries on among those still to pick. Run out of time
and you get a random perk from what is left.

Wager cards are objects, not numbers. Everybody starts with one card for
each value 1..N-1, each with its own id, and a trade moves ownership. So a
hand can end up holding two 19s and no 1, and a set of numbers would quietly
lose one of them. Trading never spends a card and never touches a score.

Pure state and rules like `backend/bsquiz.py`: no sockets, no clock of its
own. The room (`backend/bsroom.py`) owns a `Draft`, passes `now` in, and
hands the finished picks and hands to the quiz. The design notes are in
docs/BULLSHIT_QUIZ_PERKS.md §6.

Invariants the tests hold it to: every player appears once in the order and
ends with exactly one perk, no perk goes to two players, every card has
exactly one owner, a trade preserves both hands' sizes, and each turn makes
exactly one assignment.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from backend import config

TRADING, PICKING, COMPLETE = "trading", "picking", "complete"
PENDING, ACCEPTED, DECLINED, CANCELLED, EXPIRED = (
    "pending", "accepted", "declined", "cancelled", "expired")


class Refused(Exception):
    """A draft move the rules do not allow. Carries what to tell the player."""


@dataclass
class WagerCard:
    id: str
    value: int
    spent: bool = False


@dataclass
class Offer:
    """One trade, with its terms fixed when it was made: if the positions or
    cards it names have moved since, it cannot be accepted."""
    id: int
    proposer: str
    recipient: str
    proposer_position: int        # zero-based
    recipient_position: int
    offered_card: str             # the proposer's card id
    requested_card: str           # the recipient's card id
    status: str = PENDING


@dataclass
class Draft:
    original_order: list[str]
    order: list[str]                                   # after accepted trades
    available: list[str]                               # perk ids still in the pool
    hands: dict[str, list[WagerCard]]                  # shared with the room
    stage: str = TRADING
    current: int | None = None                         # the active position
    turn: int = 0                                      # bumps every turn
    deadline: float | None = None
    picks: dict[str, str] = field(default_factory=dict)          # player -> perk
    offers: list[Offer] = field(default_factory=list)
    log: list[dict[str, Any]] = field(default_factory=list)
    next_offer: int = 1

    def position(self, player: str) -> int:
        return self.order.index(player)

    def active(self) -> str | None:
        if self.stage != PICKING or self.current is None:
            return None
        return self.order[self.current]

    def tradable(self, position: int) -> bool:
        """Every position before the picks start; afterwards, only those
        still to come. The active one is locked so its clock is never in
        dispute."""
        if self.stage == TRADING:
            return True
        return self.stage == PICKING and self.current is not None and position > self.current

    def card(self, player: str, card_id: object) -> WagerCard | None:
        return next((c for c in self.hands.get(player, []) if c.id == card_id), None)


def deal_hands(players: list[str], values: list[int]) -> dict[str, list[WagerCard]]:
    """The same hand for everybody (bsquiz.wager_hand), each card with its own
    id. A hand can hold two of a value, so the id counts cards, not values."""
    return {p: [WagerCard(f"{p}-card-{i}", v) for i, v in enumerate(values, 1)]
            for p in players}


def new_draft(players: list[str], hands: dict[str, list[WagerCard]], pool: list[str],
              rng: random.Random) -> Draft:
    """Shuffle the order once (random.shuffle is Fisher-Yates) and never
    again. Needs a perk for everybody."""
    if len(pool) < len(players):
        raise Refused(f"The draft has {len(pool)} perks, so it takes at most {len(pool)} players.")
    order = list(players)
    rng.shuffle(order)
    return Draft(original_order=list(order), order=order, available=list(pool), hands=hands)


# --- trading --------------------------------------------------------------

def _check_tradable(draft: Draft, a: str, b: str) -> None:
    if draft.stage == COMPLETE:
        raise Refused("The draft is over.")
    if a not in draft.order or b not in draft.order:
        raise Refused("They aren't in this draft.")
    if a == b:
        raise Refused("Trade with somebody else.")
    if a in draft.picks or b in draft.picks:
        raise Refused("Somebody who has picked can't trade.")
    if not (draft.tradable(draft.position(a)) and draft.tradable(draft.position(b))):
        raise Refused("Only picks still to come can be traded.")


def propose(draft: Draft, proposer: str, recipient: object, give: object,
            take: object) -> Offer:
    """Offer `recipient` a swap of places, giving your card `give` for their
    card `take`. Nothing changes until they accept."""
    if not isinstance(recipient, str):
        raise Refused("Pick who to trade with.")
    _check_tradable(draft, proposer, recipient)
    if draft.card(proposer, give) is None:
        raise Refused("Offer one of your own cards.")
    if draft.card(recipient, take) is None:
        raise Refused("Ask for one of their cards.")
    mine = [o for o in draft.offers if o.proposer == proposer and o.status == PENDING]
    if len(mine) >= config.BS_DRAFT_MAX_OFFERS:
        raise Refused(f"You have {len(mine)} offers out already. Cancel one first.")
    offer = Offer(id=draft.next_offer, proposer=proposer, recipient=recipient,
                  proposer_position=draft.position(proposer),
                  recipient_position=draft.position(recipient),
                  offered_card=give, requested_card=take)          # type: ignore[arg-type]
    draft.offers.append(offer)
    draft.next_offer += 1
    return offer


def respond(draft: Draft, player: str, offer_id: object, answer: object) -> None:
    """Accept or decline an offer made to you, or cancel one you made.

    Accepting checks every term against the draft as it is now and then does
    the whole exchange: both places and both cards, or, if any check fails,
    nothing at all. Every other open offer involving either player expires,
    because it was made against the old order."""
    offer = next((o for o in draft.offers if o.id == offer_id), None)
    if offer is None or offer.status != PENDING:
        raise Refused("That offer is no longer open.")
    if answer == "cancel":
        if player != offer.proposer:
            raise Refused("Only whoever made an offer can cancel it.")
        offer.status = CANCELLED
        return
    if player != offer.recipient:
        raise Refused("That offer isn't yours to answer.")
    if answer == "decline":
        offer.status = DECLINED
        return
    if answer != "accept":
        raise Refused("Accept or decline it.")

    a, b = offer.proposer, offer.recipient
    _check_tradable(draft, a, b)
    i, j = draft.position(a), draft.position(b)
    if (i, j) != (offer.proposer_position, offer.recipient_position):
        raise Refused("The order has changed since this offer. Ask for a fresh one.")
    give, take = draft.card(a, offer.offered_card), draft.card(b, offer.requested_card)
    if give is None or take is None:
        raise Refused("Those cards have changed hands since. Ask for a fresh offer.")

    draft.order[i], draft.order[j] = b, a
    draft.hands[a].remove(give)
    draft.hands[b].remove(take)
    draft.hands[b].append(give)
    draft.hands[a].append(take)
    offer.status = ACCEPTED
    _expire(draft, a, b)
    draft.log.append({"kind": "trade", "a": a, "b": b, "a_to": j, "b_to": i,
                      "a_gave": give.value, "b_gave": take.value})


def _expire(draft: Draft, *players: str) -> None:
    for offer in draft.offers:
        if offer.status == PENDING and (not players or {offer.proposer, offer.recipient} & set(players)):
            offer.status = EXPIRED


# --- picking --------------------------------------------------------------

def start_picking(draft: Draft, now: float) -> None:
    if draft.stage != TRADING:
        raise Refused("The picks have already started.")
    draft.stage = PICKING
    draft.current = 0
    _begin_turn(draft, now)


def _begin_turn(draft: Draft, now: float) -> None:
    draft.turn += 1
    draft.deadline = now + config.BS_DRAFT_PICK_SECONDS
    _expire(draft, draft.order[draft.current])      # their place is locked now


def pick(draft: Draft, player: str, perk: object, turn: object, now: float) -> None:
    """The active player takes a perk. `turn` must be the current turn, so a
    late request from an earlier one can never land on this one."""
    if draft.stage != PICKING:
        raise Refused("Nobody is picking right now.")
    if turn != draft.turn:
        raise Refused("That turn has passed.")
    if player != draft.active():
        raise Refused("It isn't your pick.")
    if not isinstance(perk, str) or perk not in draft.available:
        raise Refused("That perk has gone. Pick another.")
    if draft.deadline is not None and now >= draft.deadline:
        raise Refused("Time's up.")
    _assign(draft, player, perk, now, auto=False)


def _assign(draft: Draft, player: str, perk: str, now: float, auto: bool) -> None:
    """Hand over the perk and move on, in one step, so a double tap or a
    pick racing the timeout can only ever assign once."""
    draft.picks[player] = perk
    draft.available.remove(perk)
    draft.log.append({"kind": "pick", "player": player, "perk": perk, "auto": auto})
    draft.current += 1
    if draft.current >= len(draft.order):
        draft.stage, draft.current, draft.deadline = COMPLETE, None, None
        _expire(draft)
    else:
        _begin_turn(draft, now)


def tick(draft: Draft, now: float, rng: random.Random) -> bool:
    """A turn whose clock ran out gets a random perk from what is left. True
    if that happened."""
    if draft.stage != PICKING or draft.deadline is None or now < draft.deadline:
        return False
    _assign(draft, draft.active(), rng.choice(draft.available), now, auto=True)
    return True


# --- what players see -----------------------------------------------------

def public_view(draft: Draft, now: float) -> dict[str, Any]:
    """Everything here is public: the order, who is on the clock, what is
    left, who took what, and everybody's cards (an offer has to name one of
    theirs)."""
    return {
        "stage": draft.stage,
        "order": list(draft.order),
        "current": draft.current,
        "turn": draft.turn,
        "left": max(0.0, draft.deadline - now) if draft.deadline is not None else None,
        "pick_seconds": config.BS_DRAFT_PICK_SECONDS,
        "available": list(draft.available),
        "picks": dict(draft.picks),
        "hands": {p: [{"id": c.id, "value": c.value} for c in sorted(cards, key=lambda c: c.value)]
                  for p, cards in draft.hands.items()},
        "log": draft.log[-8:],
    }


def offers_for(draft: Draft, player: str) -> list[dict[str, Any]]:
    """This player's offers, both ways: every open one, and the last few
    that were settled so they know what became of them."""
    mine = [o for o in draft.offers if player in (o.proposer, o.recipient)]
    shown = [o for o in mine if o.status == PENDING] + \
            [o for o in mine if o.status != PENDING][-4:]
    out = []
    for o in shown:
        give = _value_of(draft, o.offered_card)
        take = _value_of(draft, o.requested_card)
        out.append({"id": o.id, "from": o.proposer, "to": o.recipient,
                    "from_position": o.proposer_position, "to_position": o.recipient_position,
                    "give": {"id": o.offered_card, "value": give},
                    "take": {"id": o.requested_card, "value": take},
                    "status": o.status})
    return out


def _value_of(draft: Draft, card_id: str) -> int | None:
    for cards in draft.hands.values():
        for c in cards:
            if c.id == card_id:
                return c.value
    return None
