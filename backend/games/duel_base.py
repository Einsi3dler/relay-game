"""DuelModule Protocol and DuelState — the contract for cross-team duels.

The sibling of `backend/games/base.py`. A GameModule is a puzzle owned by one
player and checked in isolation; a DuelModule is a **live head-to-head between
the two teams' Duelists**, resolved by the engine round by round.

Per docs/DUEL_MODULE_SPEC.md, a duel module answers three questions and nothing
more:

  1. "Start a fresh duel."            -> new_duel()
  2. "Is that a legal move for this seat, and what is its canonical form?"
                                      -> normalize_choice()
  3. "Who won this round?"            -> resolve_round()

The engine owns the clock, the statuses, the currency and the penalty. Duel
modules differ only in their move set and their *time consequences* — the
per-round choice window and how many round wins take the duel are declared by
the module, not hard-coded in the engine.

Hard rules (mirroring GAME_MODULE_SPEC.md §"Hard rules"):

  1. **Deterministic:** `new_duel` depends only on `seed` and constants.
  2. **Stateless between calls.** Modules are long-lived singletons shared by
     every match in the process; all per-duel state lives in `DuelState`.
  3. **No choice leakage.** `DuelState.choices` is server-only until the round
     resolves. Build every client view through `base_public()` — it is the one
     place the reveal rule is enforced.
  4. **No engine imports.** Import only from this module and the stdlib.
  5. **Never raise.** A malformed or hostile choice is simply not valid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

DUEL_RULES_VERSION = 2

# The two seats in a duel. The engine maps a side to a team, never the reverse:
# a module never learns which team it is resolving, so it cannot play favourites.
SIDES = ("a", "b")

# Cap a raw submitted choice before any parsing (game9_shadow_cast.py precedent).
MAX_CHOICE_CHARS = 32


def other_side(side: str) -> str:
    return "b" if side == "a" else "a"


@dataclass
class DuelState:
    """One duel in progress. Created by a DuelModule, owned by the engine."""

    duel_game_id: str
    round_index: int = 1                                  # 1-based
    wins: dict[str, int] = field(
        default_factory=lambda: {"a": 0, "b": 0}
    )
    # side -> choice for the CURRENT round. SERVER ONLY until the round
    # resolves; cleared by the engine at the start of each new round.
    choices: dict[str, str] = field(default_factory=dict)
    # Resolved rounds: {"round", "a", "b", "winner"} — safe to show anyone.
    history: list[dict[str, Any]] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)  # render hints
    # SERVER ONLY, for duels that carry more than one choice's worth of state:
    # a hand of cards, a coin balance, a shuffled prize order. `base_public`
    # never reads it, so anything parked here is hidden by default and a module
    # publishes only what it means to, through its own `public()`. That is the
    # opposite default from `payload`, which is sent to everyone verbatim.
    private: dict[str, Any] = field(default_factory=dict)

    def locked(self, side: str) -> bool:
        """Has this side committed a choice for the current round?"""
        return side in self.choices

    def both_locked(self) -> bool:
        return all(side in self.choices for side in SIDES)


def base_public(
    state: DuelState, side: str | None, revealed: bool
) -> dict[str, Any]:
    """The client view of a duel — the single enforcement point for the
    reveal rule.

    `side` is the viewer's own seat, or None for a Grandmaster watching. Until
    `revealed`, a viewer sees only their *own* choice; everyone else's is a
    bare `locked` boolean. A Grandmaster (side=None) sees neither choice, so
    they cannot relay a move to their Duelist mid-round.
    """
    if revealed:
        choices = {seat: state.choices.get(seat) for seat in SIDES}
    elif side is not None and side in state.choices:
        choices = {side: state.choices[side]}
    else:
        choices = {}
    return {
        "duel_game_id": state.duel_game_id,
        "rules_version": DUEL_RULES_VERSION,
        "round": state.round_index,
        "wins": dict(state.wins),
        "history": [dict(entry) for entry in state.history],
        "you": side,
        "locked": {seat: seat in state.choices for seat in SIDES},
        "choices": choices,
        "payload": dict(state.payload),
    }


class DuelModule(Protocol):
    """Every duel game implements this. The engine only talks to this
    interface."""

    id: str               # unique, stable, snake_case. e.g. "rps_duel"
    name: str             # display name. e.g. "Rock Paper Scissors"
    choice_seconds: int   # per-round choice window — the module's time cost
    wins_needed: int      # round wins that take the duel
    # True for a duel fought with the team's own coins (BID WAR). The engine
    # collects a stake from each Grandmaster before the duel opens, passes the
    # two pools to `new_duel`, and reads `settlement` when it ends. A staked
    # duel is also fought once per level rather than twice: see
    # config.DUEL_STAKE_* and docs/DUEL_MODULE_SPEC.md.
    staked: bool

    # `stakes` is the coins each side was granted, keyed by side. It is None
    # for an unstaked duel, and a staked module may assume it is not.
    def new_duel(self, seed: int, stakes: dict[str, int] | None = None) -> DuelState: ...
    # The canonical move, or None if it is illegal. Validating and normalising
    # in one call is what guarantees `DuelState.choices` only ever holds
    # canonical values, so `resolve_round` never re-parses client text.
    # `side` is the seat submitting, so a duel whose legal moves depend on who
    # is asking — a card only in *your* hand, a bid only *you* can afford — can
    # reject an opponent's move instead of taking it on trust. RPS ignores it.
    def normalize_choice(
        self, state: DuelState, choice: object, side: str | None = None
    ) -> str | None: ...
    # "a" | "b" | None (tie — the engine replays the round). Called exactly
    # once per round, which also makes it the one place a duel that carries
    # state between rounds may advance it: spend the cards, pay the coins,
    # move to the next auction. Everything it touches lives on `state`.
    def resolve_round(self, state: DuelState) -> str | None: ...
    def public(
        self, state: DuelState, side: str | None, revealed: bool
    ) -> dict[str, Any]: ...
    # Coins each side won and is owed back into their team purse, keyed by
    # side. Staked duels only; the engine pays it once, when the duel ends.
    # An unstaked module need not define it.
    def settlement(self, state: DuelState) -> dict[str, int]: ...
    def reset(self) -> None: ...
