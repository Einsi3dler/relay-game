"""InMemoryStateStore: async create/get/require/list for matches, plus the
per-match locks that serialize mutations.

Async signatures so the store can later be swapped for a real backing store
without touching callers (docs/ARCHITECTURE.md §2). Store ported from legacy
(T1.2); `MatchLocks` is T3.2.
"""

from __future__ import annotations

import asyncio

from backend.models import Match


class InMemoryStateStore:
    def __init__(self) -> None:
        self._matches: dict[str, Match] = {}

    async def add(self, match: Match) -> Match:
        self._matches[match.id] = match
        return match

    async def get(self, match_id: str) -> Match | None:
        return self._matches.get(match_id)

    async def require(self, match_id: str) -> Match:
        match = await self.get(match_id)
        if match is None:
            raise KeyError(match_id)
        return match

    async def all(self) -> list[Match]:
        return list(self._matches.values())

    async def remove(self, match_id: str) -> None:
        """Drop a match (T3.6 eviction). Missing ids are ignored."""
        self._matches.pop(match_id, None)


class MatchLocks:
    """One asyncio.Lock per match so WebSocket messages and timer callbacks
    mutate a match one at a time (T3.2). Everything that touches a match —
    submits, timer fires, connects — must run under `async with locks.for_match(id)`.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def for_match(self, match_id: str) -> asyncio.Lock:
        return self._locks.setdefault(match_id, asyncio.Lock())

    def discard(self, match_id: str) -> None:
        """Drop the lock of an evicted match (T3.6)."""
        self._locks.pop(match_id, None)
