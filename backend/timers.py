"""TimerService: schedules deadline callbacks into the engine.

Per docs/ARCHITECTURE.md §4: at most one pending deadline per
(match_id, scope_id), backed by one asyncio task per timer. A scope is
usually a player id (the wait timer), but match-level mechanics own their own
scopes — `"duel"` for the duel phase clock, `"team:<id>"` for a team's duel
penalty — so a duel deadline can never displace a player's wait timer. Player
ids are 8-char uuid hex, so they never collide with those literals.

The engine never touches the clock — it emits `TimerRequest`s and cancel lists
in an `EngineResult`, and the server layer applies them here via
`apply_result`. On fire, the service calls the `on_fire` callback (wired to the
engine hooks and the broadcast layer by main.py), which routes on `kind`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable

from backend.engine import EngineResult

# on_fire(match_id, scope_id, kind) — invoked when a deadline passes.
FireCallback = Callable[[str, str, str], Awaitable[None]]


class TimerService:
    def __init__(self, on_fire: FireCallback) -> None:
        self._on_fire = on_fire
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}

    def schedule(self, match_id: str, scope_id: str, kind: str, deadline: str) -> None:
        """Schedule `on_fire` at `deadline` (UTC ISO), replacing any pending
        timer for this scope."""
        self.cancel(match_id, scope_id)
        delay = (
            datetime.fromisoformat(deadline) - datetime.now(timezone.utc)
        ).total_seconds()
        key = (match_id, scope_id)
        self._tasks[key] = asyncio.create_task(
            self._run(key, kind, max(delay, 0.0))
        )

    def cancel(self, match_id: str, scope_id: str) -> None:
        task = self._tasks.pop((match_id, scope_id), None)
        if task is not None:
            task.cancel()

    def cancel_match(self, match_id: str) -> None:
        """Cancel every pending timer of a match (win, eviction)."""
        for key in [key for key in self._tasks if key[0] == match_id]:
            self.cancel(*key)

    def apply_result(self, match_id: str, result: EngineResult) -> None:
        """Apply an EngineResult's timer instructions: cancels first, then
        (re)schedules — a schedule for the same scope wins over its cancel."""
        for scope_id in result.cancel:
            self.cancel(match_id, scope_id)
        for request in result.schedule:
            self.schedule(match_id, request.scope_id, request.kind, request.deadline)

    def pending(self, match_id: str) -> set[str]:
        """Scope ids with a pending timer in this match (for tests/eviction)."""
        return {scope_id for mid, scope_id in self._tasks if mid == match_id}

    async def _run(self, key: tuple[str, str], kind: str, delay: float) -> None:
        await asyncio.sleep(delay)
        self._tasks.pop(key, None)
        await self._on_fire(key[0], key[1], kind)
