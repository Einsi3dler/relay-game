"""NUMBER CLASH — nine numbers, each spent once, higher takes the round.

Both Duelists hold 1 through 9. Each round both commit one unused number; the
higher one wins a point, an equal one draws, and **both numbers are spent
either way**. First to four points takes the duel.

There is no power, no arithmetic and nothing to memorise — the whole game is
what a number is worth *this* round. Beating a 1 with your 9 wins the round and
hands the opponent the better trade, so the interesting move is usually the
cheap one.

Unlike Crown Duel, nothing here is secret for long: every number played is
revealed the moment the round resolves, so what remains in each hand follows
from the log. The only hidden thing is the number in flight, and that is
`base_public`'s job. State still lives in `DuelState.private` because it is the
server's copy of the truth — the client's view is rebuilt from it, never the
other way round.

Match length is the module's, not the engine's: `resolve_round` returns a side
only once that side has four points, so `wins_needed` is 1 and the engine's tie
path carries the duel from round to round. That is what lets a tied duel run
past round seven into Sudden Death, and lets exhausted hands refresh, instead
of ending on a counter the engine keeps.
"""

from __future__ import annotations

from typing import Any

from backend.games.duel_base import (
    MAX_CHOICE_CHARS,
    SIDES,
    DuelState,
    base_public,
)

NUMBERS = tuple(range(1, 10))   # 1..9, each playable once

CHOICE_SECONDS = 8    # the window both Duelists choose inside
POINTS_NEEDED = 4     # points that take the duel
NORMAL_ROUNDS = 7     # after these, a tied duel is in Sudden Death


def _copy_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        key: dict(value) if isinstance(value, dict) else value
        for key, value in entry.items()
    }


class NumberClash:
    """Nine numbers, each once, first to four points."""

    id = "number_clash"
    name = "Number Clash"
    choice_seconds = CHOICE_SECONDS
    # The module scores the match itself (see the header), so one returned
    # winner *is* the duel.
    wins_needed = 1
    staked = False   # fought with the team's coins? Not this one.

    # --- lifecycle -------------------------------------------------------

    def new_duel(
        self, seed: int, stakes: dict[str, int] | None = None
    ) -> DuelState:
        # Both hands are 1..9; `seed` is part of the contract for duel games
        # that randomise their setup.
        return DuelState(
            duel_game_id=self.id,
            private={
                "game_round": 1,
                "points": {"a": 0, "b": 0},
                "used": {side: [] for side in SIDES},
                "sudden_death": False,
                "log": [],       # public round log
                "last": None,    # the round that just resolved
            },
        )

    # --- choices ---------------------------------------------------------

    def normalize_choice(
        self, state: DuelState, choice: object, side: str | None = None
    ) -> str | None:
        """A number this seat still holds, as a canonical string.

        `side` decides legality: 7 is a legal move for whoever has not spent
        their 7 yet, and an illegal one for the other seat.
        """
        try:
            raw = str(choice)
            if len(raw) > MAX_CHOICE_CHARS:
                return None  # cap before any further work
            if side not in SIDES:
                return None  # a duel move always belongs to a seat
            text = raw.strip()
            # int() would take "+7", "7_0" and the Arabic-Indic "٧"; only
            # plain ASCII digits count, so the canonical form is always the
            # ordinary numeral the client can render straight back.
            if not (text.isascii() and text.isdigit()):
                return None
            number = int(text)
            if number not in NUMBERS:
                return None
            if number in state.private["used"][side]:
                return None  # every number is spent exactly once
            return str(number)
        except Exception:
            return None  # a hostile or malformed move is simply not legal

    # --- resolution ------------------------------------------------------

    def resolve_round(self, state: DuelState) -> str | None:
        """Score the open round; return a side only once it has four points."""
        private = state.private
        played = {side: state.choices.get(side) for side in SIDES}
        numbers = {
            side: int(value) if value is not None else None
            for side, value in played.items()
        }
        for side, number in numbers.items():
            if number is not None:
                private["used"][side].append(number)

        if numbers["a"] is None and numbers["b"] is None:
            winner = None                       # a double no-show costs nobody
        elif numbers["a"] is None:
            winner = "b"                        # letting the window lapse loses
        elif numbers["b"] is None:
            winner = "a"
        elif numbers["a"] == numbers["b"]:
            winner = None                       # equal numbers draw
        else:
            winner = "a" if numbers["a"] > numbers["b"] else "b"

        if winner is not None:
            private["points"][winner] += 1
        entry = {
            "round": private["game_round"],
            "a": numbers["a"],
            "b": numbers["b"],
            "winner": winner,
            "points": dict(private["points"]),
        }
        private["log"].append(entry)
        private["last"] = dict(entry)

        if winner is not None and private["points"][winner] >= POINTS_NEEDED:
            return winner  # the duel is decided; the engine finishes it

        private["game_round"] += 1
        if private["game_round"] > NORMAL_ROUNDS:
            private["sudden_death"] = True
        if any(not self._available(private, side) for side in SIDES):
            # Sudden Death can outlast a hand: deal both sides a fresh 1..9
            # rather than let the duel run out of legal moves.
            private["used"] = {side: [] for side in SIDES}
        return None

    def _available(self, private: dict[str, Any], side: str) -> list[int]:
        used = set(private["used"][side])
        return [number for number in NUMBERS if number not in used]

    # --- the client view -------------------------------------------------

    def public(
        self, state: DuelState, side: str | None, revealed: bool
    ) -> dict[str, Any]:
        view = base_public(state, side, revealed)
        view["payload"] = self._payload(state, side)
        return view

    def _payload(self, state: DuelState, side: str | None) -> dict[str, Any]:
        private = state.private
        return {
            "kind": self.id,
            "choice_seconds": CHOICE_SECONDS,
            "wins_needed": POINTS_NEEDED,
            "points_needed": POINTS_NEEDED,
            "normal_rounds": NORMAL_ROUNDS,
            "game_round": private["game_round"],
            "sudden_death": private["sudden_death"],
            "points": dict(private["points"]),
            "numbers": list(NUMBERS),
            # Both spent piles are public: every number played was revealed
            # when its round resolved, so hiding them would only cost the
            # players a memory test the log already answers.
            "used": {seat: list(private["used"][seat]) for seat in SIDES},
            "available": self._available(private, side) if side in SIDES else [],
            "log": [_copy_entry(entry) for entry in private["log"]],
            "last": _copy_entry(private["last"]) if private["last"] else None,
        }

    def reset(self) -> None:
        return None  # stateless
