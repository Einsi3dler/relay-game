"""T1.2 — InMemoryStateStore basics."""

from __future__ import annotations

import asyncio

import pytest

from backend.models import Match
from backend.state import InMemoryStateStore


def test_add_then_get_returns_same_match():
    async def scenario():
        store = InMemoryStateStore()
        match = Match(id="m1")
        added = await store.add(match)
        assert added is match
        assert await store.get("m1") is match
        assert await store.require("m1") is match
        assert await store.all() == [match]

    asyncio.run(scenario())


def test_get_missing_returns_none_and_require_raises():
    async def scenario():
        store = InMemoryStateStore()
        assert await store.get("nope") is None
        with pytest.raises(KeyError):
            await store.require("nope")

    asyncio.run(scenario())
