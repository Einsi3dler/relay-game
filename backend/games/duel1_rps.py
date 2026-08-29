"""RPS DUEL — rock, paper, scissors. The first game of the Duelist role.

Both Duelists commit a move inside a 5-second window without seeing each
other's; the round then resolves. First to two round wins takes the duel. Ties
replay the round, and a Duelist who lets the window lapse forfeits that round —
so stalling is never better than guessing.

Why it is safe under the anti-cheat rules in docs/GAMES_SPEC.md: there is no
answer to look up, and the only thing worth knowing (the opponent's move) is
never sent to anyone until the round has already resolved — see
`duel_base.base_public`, which is the single place that rule is enforced.

The engine owns the clock, the currency and the penalty. This module owns the
move set, the 5-second window and the two-win target.
"""

from __future__ import annotations

from typing import Any

from backend.games.duel_base import (
    MAX_CHOICE_CHARS,
    DuelState,
    base_public,
)

MOVES = ("rock", "paper", "scissors")
# move -> the move it beats. Total over MOVES, so every matchup is decided.
BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}

CHOICE_SECONDS = 5   # the window both Duelists choose inside
WINS_NEEDED = 2      # round wins that take the duel


class RockPaperScissorsDuel:
    """First to two, five seconds a round, nobody sees the other's hand."""

    id = "rps_duel"
    name = "Rock Paper Scissors"
    choice_seconds = CHOICE_SECONDS
    wins_needed = WINS_NEEDED

    def new_duel(self, seed: int) -> DuelState:
        # RPS has no randomised setup; `seed` is part of the contract for duel
        # games that do. The payload is pure render data — the move set the
        # client draws buttons for, and the rules it displays.
        return DuelState(
            duel_game_id=self.id,
            payload={
                "moves": list(MOVES),
                "beats": dict(BEATS),
                "wins_needed": WINS_NEEDED,
                "choice_seconds": CHOICE_SECONDS,
            },
        )

    def normalize_choice(
        self, state: DuelState, choice: object, side: str | None = None
    ) -> str | None:
        # `side` is part of the interface for duels whose legal moves depend on
        # the seat asking (a card in *your* hand). Every RPS move is legal for
        # both seats, so this one ignores it.
        try:
            raw = str(choice)
            if len(raw) > MAX_CHOICE_CHARS:
                return None  # cap before any further work
            move = raw.strip().lower()
            return move if move in BEATS else None
        except Exception:
            return None  # a hostile or malformed move is simply not legal

    def resolve_round(self, state: DuelState) -> str | None:
        """Winner of the current round: "a", "b", or None for a tie.

        A missing choice loses — the Duelist let the window lapse. Both
        missing is a tie, so a double no-show costs neither team.
        """
        a = state.choices.get("a")
        b = state.choices.get("b")
        if a is None and b is None:
            return None
        if a is None:
            return "b"
        if b is None:
            return "a"
        if a == b:
            return None
        return "a" if BEATS[a] == b else "b"

    def public(
        self, state: DuelState, side: str | None, revealed: bool
    ) -> dict[str, Any]:
        return base_public(state, side, revealed)

    def reset(self) -> None:
        return None  # stateless
