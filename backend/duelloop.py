"""The duel round loop: commit, score, reveal, next round.

Two callers run duels. The engine runs them inside a match, where a duel is
worth coins and a loss stamps a penalty on a team. `backend/duelroom.py` runs
them between two people who followed a link, where a duel is worth nothing at
all. What is identical in both is the *scoring*, and this is it.

Kept apart from the scheduling on purpose. The clock is where the two callers
genuinely differ — a match reads its frozen `config_snapshot` and the host's
round-window override, a room has neither — so each caller keeps its own timer
calls and this file never sees a deadline.

Why extract rather than let the room keep its own copy: three of the four duel
modules score themselves and ride the engine's *tie* path to carry a game
forward (docs/DUEL_MODULE_SPEC.md §4.2). Crown Duel spends two of these rounds
on one of its own. A second implementation would drift, and the first thing it
would drift on is that contract — which is a bug nobody would think to look for,
in a game that would simply behave differently depending on where it was played.

Nothing here knows about Match, teams, currency, or a player's status.
"""

from __future__ import annotations

from typing import Any

from backend.models import DuelSession


def apply_choice(
    duel: DuelSession,
    seat_id: str,
    duel_id: str,
    round_index: int,
    choice: str,
) -> tuple[bool, str | None]:
    """Record a seat's move for the open round.

    Returns `(both_locked, error)`. `error` is None when the move was taken;
    `both_locked` says whether this move closed the round, which is the
    caller's cue to resolve it.

    The move is recorded but never broadcast: the round resolves when both
    have committed, or when the window lapses. Choosing early therefore tells
    the opponent nothing beyond the fact that you chose.
    """
    if duel is None or duel.id != duel_id:
        return False, "no duel to answer"
    if duel.phase != "choosing":
        return False, "the round is closed"
    if round_index != duel.state.round_index:
        return False, "that round is over"
    side = duel.side_of(seat_id)
    if side is None:
        return False, "you aren't in this duel"
    if duel.state.locked(side):
        return False, "you already chose this round"
    move = duel.module.normalize_choice(duel.state, choice, side)
    if move is None:
        return False, "not a legal move"

    duel.state.choices[side] = move
    return duel.state.both_locked(), None


def score_round(duel: DuelSession) -> bool:
    """Score the open round. Returns True when the duel is decided.

    A decided duel has its `winner_side` set and is left for the caller to
    finish — paying it out is a match's business and a room has nothing to pay.
    Otherwise the duel goes to `reveal` and the caller starts the reveal clock.
    """
    state = duel.state
    winner = duel.module.resolve_round(state)
    entry: dict[str, Any] = {
        "round": state.round_index,
        "a": state.choices.get("a"),
        "b": state.choices.get("b"),
        "winner": winner,
    }
    state.history.append(entry)
    duel.last_round = entry
    if winner is not None:
        state.wins[winner] += 1
        if state.wins[winner] >= duel.module.wins_needed:
            duel.winner_side = winner
            return True
    # Choices stay on the state through the reveal beat, then clear.
    duel.phase = "reveal"
    return False


def open_next_round(duel: DuelSession) -> None:
    """Clear the reveal and open the next round. The caller starts its clock."""
    duel.state.choices.clear()
    duel.state.round_index += 1
    duel.last_round = None
    duel.phase = "choosing"
