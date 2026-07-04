"""InMemoryStateStore: async create/get/require/list for matches.

Async signatures so the store can later be swapped for a real backing store
without touching callers (docs/ARCHITECTURE.md §2). Ported from legacy (T1.2).
"""

from __future__ import annotations

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
