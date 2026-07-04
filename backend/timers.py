"""TimerService: schedules deadline callbacks into the engine.

Per docs/ARCHITECTURE.md §4: at most one pending deadline per
(match_id, player_id), backed by one asyncio task per timer. The engine never
touches the clock — it emits `TimerRequest`s and cancel lists in an
`EngineResult`, and the server layer applies them here via `apply_result`.
On fire, the service calls the `on_fire` callback (wired to the engine hooks
and the broadcast layer by main.py).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable

from backend.engine import EngineResult

# on_fire(match_id, player_id, kind) — invoked when a deadline passes.
FireCallback = Callable[[str, str, str], Awaitable[None]]


class TimerService:
    def __init__(self, on_fire: FireCallback) -> None:
        self._on_fire = on_fire
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}

    def schedule(self, match_id: str, player_id: str, kind: str, deadline: str) -> None:
        """Schedule `on_fire` at `deadline` (UTC ISO), replacing any pending
        timer for this player."""
        self.cancel(match_id, player_id)
        delay = (
            datetime.fromisoformat(deadline) - datetime.now(timezone.utc)
        ).total_seconds()
        key = (match_id, player_id)
        self._tasks[key] = asyncio.create_task(
            self._run(key, kind, max(delay, 0.0))
        )

    def cancel(self, match_id: str, player_id: str) -> None:
        task = self._tasks.pop((match_id, player_id), None)
        if task is not None:
            task.cancel()

    def cancel_match(self, match_id: str) -> None:
        """Cancel every pending timer of a match (win, eviction)."""
        for key in [key for key in self._tasks if key[0] == match_id]:
            self.cancel(*key)

    def apply_result(self, match_id: str, result: EngineResult) -> None:
        """Apply an EngineResult's timer instructions: cancels first, then
        (re)schedules — a schedule for the same player wins over its cancel."""
        for player_id in result.cancel:
            self.cancel(match_id, player_id)
        for request in result.schedule:
            self.schedule(match_id, request.player_id, request.kind, request.deadline)

    def pending(self, match_id: str) -> set[str]:
        """Player ids with a pending timer in this match (for tests/eviction)."""
        return {player_id for mid, player_id in self._tasks if mid == match_id}

    async def _run(self, key: tuple[str, str], kind: str, delay: float) -> None:
        await asyncio.sleep(delay)
        self._tasks.pop(key, None)
        await self._on_fire(key[0], key[1], kind)
