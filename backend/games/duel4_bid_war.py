"""BID WAR — twenty coins, five prizes, and every bid spent whether it wins.

Five prizes worth 1 to 5 Victory Points go under the hammer in an order the
server shuffles at the start. Both Duelists secretly bid a whole number of
coins from the twenty they hold; the higher bid takes the prize, and **both
bids are spent regardless**. Coins left over at the end are worth nothing. Most
Victory Points takes the duel.

A tied auction pays nobody and rolls its prize into the next one, so a mirror
bid is the expensive way to make the following lot worth more to both of you.

What is secret, and for how long:

  * the bid in flight — until both are locked, which is `base_public`'s job;
  * **the prize order beyond the next lot** — the shuffled list lives in
    `DuelState.private`, and the payload publishes the current prize and the
    one after it and nothing else. Knowing the 5 was still to come would settle
    every bid before it.

Everything already spent is public: past bids, both coin balances and both VP
totals follow from auctions that have resolved.

Match length is the module's, not the engine's: `resolve_round` returns a side
only once the VP totals are final and unequal, so `wins_needed` is 1 and the
engine's tie path carries the duel from auction to auction. A duel decided on
points rather than round wins cannot be expressed any other way — winning three
small lots loses to one 5 VP prize, and the engine's counter has no idea.
"""

from __future__ import annotations

import random
from typing import Any

from backend.games.duel_base import (
    MAX_CHOICE_CHARS,
    SIDES,
    DuelState,
    base_public,
)

PRIZES = (1, 2, 3, 4, 5)      # Victory Points, shuffled into an order per duel
AUCTIONS = len(PRIZES)
STARTING_COINS = 20
CHOICE_SECONDS = 10           # the window both Duelists bid inside

# A tie that has to be broken is fought with a fresh, equal pool rather than
# whatever coins happen to be left, so the five-auction economy stays intact.
OVERTIME_COINS = 5
OVERTIME_PRIZE = 1            # the lot when the VP totals themselves are level


def _copy_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        key: dict(value) if isinstance(value, dict) else value
        for key, value in entry.items()
    }


class BidWar:
    """Twenty coins, five secret auctions, both bids always spent."""

    id = "bid_war"
    name = "Bid War"
    choice_seconds = CHOICE_SECONDS
    # The module scores the match itself (see the header), so one returned
    # winner *is* the duel.
    wins_needed = 1

    # --- lifecycle -------------------------------------------------------

    def new_duel(self, seed: int) -> DuelState:
        # The prize order is the one randomised thing in this game, and it is
        # deterministic in `seed`: the same seed replays the same auction.
        prizes = list(PRIZES)
        random.Random(seed).shuffle(prizes)
        return DuelState(
            duel_game_id=self.id,
            private={
                "auction": 1,                 # 1..AUCTIONS, then overtime
                "prizes": prizes,             # SECRET beyond the next lot
                "pot": prizes[0],             # this lot, rollovers included
                "coins": {side: STARTING_COINS for side in SIDES},
                "vp": {"a": 0, "b": 0},
                "overtime": False,
                "overtime_round": 0,
                "overtime_coins": {side: OVERTIME_COINS for side in SIDES},
                "log": [],                    # public auction log
                "last": None,                 # the auction that just resolved
            },
        )

    # --- choices ---------------------------------------------------------

    def normalize_choice(
        self, state: DuelState, choice: object, side: str | None = None
    ) -> str | None:
        """A whole-number bid this seat can afford, as a canonical string.

        `side` decides legality: the same 15 is a legal bid for a Duelist
        holding 20 coins and an illegal one for a Duelist holding 12.
        """
        try:
            raw = str(choice)
            if len(raw) > MAX_CHOICE_CHARS:
                return None  # cap before any further work
            if side not in SIDES:
                return None  # a duel move always belongs to a seat
            text = raw.strip()
            # Plain ASCII digits only: this rejects "-1", "3.5", "1e2", "+4"
            # and non-Latin numerals in one go, and leaves the canonical form
            # equal to the number's own digits.
            if not (text.isascii() and text.isdigit()):
                return None
            bid = int(text)
            if bid > self._purse(state.private, side):
                return None  # nobody bids coins they do not hold
            return str(bid)
        except Exception:
            return None  # a hostile or malformed move is simply not legal

    def _purse(self, private: dict[str, Any], side: str) -> int:
        """What this seat may bid right now — the temporary pool in overtime,
        the real balance otherwise."""
        if private["overtime"]:
            return private["overtime_coins"][side]
        return private["coins"][side]

    # --- resolution ------------------------------------------------------

    def resolve_round(self, state: DuelState) -> str | None:
        """Settle the open auction; return a side only once the duel is over."""
        private = state.private
        locked = {side: state.choices.get(side) for side in SIDES}
        bids = {
            side: int(value) if value is not None else None
            for side, value in locked.items()
        }
        purse = (
            private["overtime_coins"] if private["overtime"] else private["coins"]
        )
        for side, bid in bids.items():
            if bid is not None:
                purse[side] -= bid   # the losing bid is spent too. Always.

        if bids["a"] is None and bids["b"] is None:
            winner = None                       # a double no-show costs nobody
        elif bids["a"] is None:
            winner = "b"                        # letting the window lapse loses
        elif bids["b"] is None:
            winner = "a"
        elif bids["a"] == bids["b"]:
            winner = None                       # a tied auction pays nobody
        else:
            winner = "a" if bids["a"] > bids["b"] else "b"

        pot = private["pot"]
        if winner is not None:
            private["vp"][winner] += pot

        entry = {
            "auction": private["auction"],
            "overtime": private["overtime"],
            "prize": pot,
            "a": bids["a"],
            "b": bids["b"],
            "winner": winner,
            "vp": dict(private["vp"]),
            "coins": dict(private["coins"]),
        }
        private["log"].append(entry)
        private["last"] = dict(entry)

        if private["overtime"]:
            return self._after_overtime(private, winner)
        return self._after_auction(private, winner, pot)

    def _after_auction(
        self, private: dict[str, Any], winner: str | None, pot: int
    ) -> str | None:
        """Move to the next lot, or close the five-auction sale."""
        last_auction = private["auction"] >= AUCTIONS
        if not last_auction:
            next_prize = private["prizes"][private["auction"]]
            # A tie pays nobody, so its prize rides on top of the next one.
            private["pot"] = next_prize if winner is not None else pot + next_prize
            private["auction"] += 1
            return None

        if winner is None:
            # The last lot went unsold: it becomes the overtime prize rather
            # than evaporating.
            return self._open_overtime(private, pot)
        return self._settle(private)

    def _after_overtime(
        self, private: dict[str, Any], winner: str | None
    ) -> str | None:
        if winner is None:
            # Level again: refill both temporary pools and bid for the same lot.
            private["overtime_round"] += 1
            private["overtime_coins"] = {
                side: OVERTIME_COINS for side in SIDES
            }
            return None
        return self._settle(private)

    def _settle(self, private: dict[str, Any]) -> str | None:
        """The winner on Victory Points, or another overtime lot if level.

        Coins still in hand are worth nothing here — they were only ever a
        means of buying points.

        The level case cannot arise with prizes 1-5: they total an odd 15, so
        two whole scores can never meet. It is kept because that is an accident
        of the prize table, and a duel that ended undecided would leave both
        teams stuck with no way out.
        """
        vp = private["vp"]
        if vp["a"] != vp["b"]:
            return "a" if vp["a"] > vp["b"] else "b"
        return self._open_overtime(private, OVERTIME_PRIZE)

    def _open_overtime(self, private: dict[str, Any], pot: int) -> str | None:
        private["overtime"] = True
        private["overtime_round"] += 1
        private["overtime_coins"] = {side: OVERTIME_COINS for side in SIDES}
        private["pot"] = pot
        private["auction"] = AUCTIONS
        return None

    # --- the client view -------------------------------------------------

    def public(
        self, state: DuelState, side: str | None, revealed: bool
    ) -> dict[str, Any]:
        """`base_public` for the reveal rule, plus a payload that publishes the
        current lot and the next one — never the rest of the shuffled order."""
        view = base_public(state, side, revealed)
        view["payload"] = self._payload(state, side)
        return view

    def _payload(self, state: DuelState, side: str | None) -> dict[str, Any]:
        private = state.private
        return {
            "kind": self.id,
            "choice_seconds": CHOICE_SECONDS,
            "wins_needed": 1,
            "auctions": AUCTIONS,
            "auction": private["auction"],
            "prize": private["pot"],
            "next_prize": self._next_prize(private),
            "starting_coins": STARTING_COINS,
            "coins": dict(private["coins"]),
            "vp": dict(private["vp"]),
            "overtime": private["overtime"],
            "overtime_round": private["overtime_round"],
            "overtime_coins": dict(private["overtime_coins"]),
            "max_bid": self._purse(private, side) if side in SIDES else 0,
            "log": [_copy_entry(entry) for entry in private["log"]],
            "last": _copy_entry(private["last"]) if private["last"] else None,
        }

    def _next_prize(self, private: dict[str, Any]) -> int | None:
        """The lot after this one, or None when there isn't a published one.

        This is the only window onto `prizes`, and it is exactly one lot wide.
        """
        if private["overtime"] or private["auction"] >= AUCTIONS:
            return None
        return private["prizes"][private["auction"]]

    def reset(self) -> None:
        return None  # stateless
