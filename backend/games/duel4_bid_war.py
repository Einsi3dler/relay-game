"""BID WAR — the team's own coins, five blind lots, every bid spent.

The one **staked** duel. The others are free; this one is bought. Before it
opens, each Duelist asks their Grandmaster for coins out of the team purse and
fights with exactly what they are handed, so the two pools are deliberately
**unequal**: a Grandmaster who believes in their champion can fund them past
the other side, and pays for it out of the perk shop. The engine collects the
stakes and hands them here (`new_duel(seed, stakes)`); the grant is gone from
the purse either way, win or lose.

Five lots go under the hammer. Both Duelists secretly bid a whole number of
coins, the higher bid takes the lot, and **both bids are spent regardless**.
Most coins won takes the duel, and what each side won is paid back into their
team's purse when it ends (`settlement`).

The sale is **funded by the two stakes**:

    pool = 2 * min(stake_a, stake_b) * DUEL_STAKE_POOL_MULTIPLIER

then cut into `DUEL_STAKE_LOTS` **deliberately uneven** pieces. A pool of 160
might come out 8, 61, 5, 74, 12. So the lots are not a ladder and not a
constant: most are small, one or two are worth more than a Duelist can pay, and
telling which is which is the game. A lot that looks like it is not worth
bidding on usually is not, and misjudging that is the player's own problem.

`min`, not the sum, is what keeps this honest. Sizing the pool off the combined
stake means out-staking your opponent inflates the prize you are bidding for,
so staking everything is always right and the Grandmaster has no decision to
make. Off the smaller stake, out-staking buys bidding power but cannot grow the
pot: there is a best stake to find, and going past it burns coins for nothing.

A tied auction pays nobody and rolls its value onto the next lot, so a mirror
bid is the expensive way to make the following one bigger for both of you.

What is secret, and for how long:

  * the bid in flight — until both are locked, which is `base_public`'s job;
  * nothing else. Both purses, both winnings and every resolved lot are public,
    because they all follow from auctions that have already happened.

Match length is the module's, not the engine's: `resolve_round` returns a side
only once the winnings are final and unequal, so `wins_needed` is 1 and the
engine's tie path carries the duel from auction to auction.
"""

from __future__ import annotations

import random
from typing import Any

from backend import config
from backend.games.duel_base import (
    MAX_CHOICE_CHARS,
    SIDES,
    DuelState,
    base_public,
)

AUCTIONS = config.DUEL_STAKE_LOTS   # lots under the hammer before the sale closes
CHOICE_SECONDS = 10           # the window both Duelists bid inside

# A tie that has to be broken is fought with a fresh, equal pool rather than
# whatever coins happen to be left. It is the one pool the team does not pay
# for: purses can legitimately be empty by now (a Grandmaster may grant zero),
# and a tiebreak nobody can bid in would never end.
OVERTIME_COINS = 5

# Cubing a uniform weight skews the split hard toward small: most lots come out
# modest and one or two carry the sale. A flat split gives five forgettable
# middling lots and no reason ever to sit one out. At 3 a typical sale has about
# one lot not worth a Duelist's while and two worth more than they can pay.
_SKEW = 3
_FLOOR_WEIGHT = 0.05          # so no lot rounds away to nothing


def _copy_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        key: dict(value) if isinstance(value, dict) else value
        for key, value in entry.items()
    }


def _coins(stakes: dict[str, int] | None, side: str) -> int:
    """One seat's grant, coerced. Never raises (duel_base hard rule 5): the
    engine always sends whole numbers, but a module that could throw here
    would take the whole duel down with it."""
    try:
        return max(0, int((stakes or {}).get(side, 0)))
    except (TypeError, ValueError):
        return 0


def pool_for(stakes: dict[str, int]) -> int:
    """What the whole sale is worth, funded by the two stakes.

    Off the *smaller* stake: see the module header for why the sum would break
    the game. Floored so two nearly-broke teams still have something to fight
    over rather than five lots worth a coin each.
    """
    smallest = min(_coins(stakes, side) for side in SIDES)
    return max(
        config.DUEL_STAKE_POOL_FLOOR,
        2 * smallest * config.DUEL_STAKE_POOL_MULTIPLIER,
    )


def split_pool(seed: int, pool: int, lots: int = AUCTIONS) -> list[int]:
    """Cut `pool` into `lots` uneven pieces, deterministically in `seed`.

    Uneven is the whole point: a flat split is five identical decisions. The
    pieces always sum to exactly `pool` (the remainder lands on the largest, so
    rounding never invents or loses coins) and none is ever zero.
    """
    rng = random.Random(seed)
    weights = [rng.random() ** _SKEW + _FLOOR_WEIGHT for _ in range(lots)]
    total = sum(weights)
    cut = [max(1, int(pool * weight / total)) for weight in weights]
    drift = pool - sum(cut)
    if drift:
        cut[cut.index(max(cut))] = max(1, cut[cut.index(max(cut))] + drift)
    rng.shuffle(cut)
    return cut


class BidWar:
    """The team's coins, five blind lots, both bids always spent."""

    id = "bid_war"
    name = "Bid War"
    choice_seconds = CHOICE_SECONDS
    # The module scores the match itself (see the header), so one returned
    # winner *is* the duel.
    wins_needed = 1
    staked = True    # fought with the team's coins. The only one that is.

    # --- lifecycle -------------------------------------------------------

    def new_duel(
        self, seed: int, stakes: dict[str, int] | None = None
    ) -> DuelState:
        """Open the sale with whatever each Grandmaster granted.

        `stakes` missing or short is treated as a grant of nothing rather than
        as an error: a duel that refused to start would strand both teams, and
        a Duelist with an empty purse can still bid zero and go to overtime.
        """
        purses = {side: _coins(stakes, side) for side in SIDES}
        lots = split_pool(seed, pool_for(stakes or {}))
        return DuelState(
            duel_game_id=self.id,
            private={
                "seed": seed,
                "auction": 1,                 # 1..AUCTIONS, then overtime
                "lots": lots,                 # SECRET beyond the next one
                "pot": lots[0],               # this lot, rollovers included
                "staked": dict(purses),       # what each side was granted
                "coins": dict(purses),        # what each side still holds
                "won": {"a": 0, "b": 0},      # coins taken, owed to the purse
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

        `side` decides legality, and here it really does differ between seats:
        the pools are unequal by design, so the same 15 can be legal for one
        Duelist and impossible for the other.
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

    def _on_the_table(self, private: dict[str, Any]) -> int:
        """Coins both seats still hold, which is what sets the next lot's floor."""
        purse = (
            private["overtime_coins"] if private["overtime"] else private["coins"]
        )
        return sum(purse.values())

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
            private["won"][winner] += pot

        entry = {
            "auction": private["auction"],
            "overtime": private["overtime"],
            "prize": pot,
            "a": bids["a"],
            "b": bids["b"],
            "winner": winner,
            "won": dict(private["won"]),
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
            private["auction"] += 1
            nxt = private["lots"][private["auction"] - 1]
            # A tie pays nobody, so its value rides on top of the next lot.
            private["pot"] = nxt if winner is not None else pot + nxt
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
        """The winner on coins won, or another overtime lot if level.

        Coins still in hand count for nothing here. They were only ever a means
        of buying lots, and the grant they came from has already left the team
        purse — which is exactly why sitting on them is the losing move.
        """
        won = private["won"]
        if won["a"] != won["b"]:
            return "a" if won["a"] > won["b"] else "b"
        # Lots are rolled rather than drawn from an odd-summed table, so two
        # equal scores are entirely reachable now. Overtime is load-bearing.
        return self._open_overtime(
            private, max(1, round(sum(private["lots"]) / AUCTIONS))
        )

    def _open_overtime(self, private: dict[str, Any], pot: int) -> str | None:
        private["overtime"] = True
        private["overtime_round"] += 1
        private["overtime_coins"] = {side: OVERTIME_COINS for side in SIDES}
        private["pot"] = pot
        private["auction"] = AUCTIONS
        return None

    # --- settlement ------------------------------------------------------

    def settlement(self, state: DuelState) -> dict[str, int]:
        """Coins each side won, owed back into their team's purse.

        The stake itself is *not* here: it left the purse when the Grandmaster
        granted it and does not come back. This is winnings only, which is what
        makes funding a champion a gamble rather than a transfer.
        """
        return {side: int(state.private["won"].get(side, 0)) for side in SIDES}

    # --- the client view -------------------------------------------------

    def public(
        self, state: DuelState, side: str | None, revealed: bool
    ) -> dict[str, Any]:
        """`base_public` for the reveal rule, plus a payload that publishes the
        lot on the block and both purses — but never a lot that has not been
        rolled, because the unrolled ones do not exist yet."""
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
            "staked": dict(private["staked"]),
            "coins": dict(private["coins"]),
            "won": dict(private["won"]),
            "overtime": private["overtime"],
            "overtime_round": private["overtime_round"],
            "overtime_coins": dict(private["overtime_coins"]),
            "max_bid": self._purse(private, side) if side in SIDES else 0,
            "log": [_copy_entry(entry) for entry in private["log"]],
            "last": _copy_entry(private["last"]) if private["last"] else None,
        }

    def _next_prize(self, private: dict[str, Any]) -> int | None:
        """The lot after this one, or None when there isn't a published one.

        The only window onto `lots`, and exactly one lot wide. Seeing the whole
        schedule would settle every bid in the sale before it opened; seeing the
        next one is what lets you decide to sit this lot out and save for it.
        """
        if private["overtime"] or private["auction"] >= AUCTIONS:
            return None
        return private["lots"][private["auction"]]

    def reset(self) -> None:
        return None  # stateless
