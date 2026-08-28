"""TimerService + per-match serialization, against the v2 wait-timer loop."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from backend import config
from backend.engine import EngineResult, RelayEngine, TimerRequest
from backend.registry import GameRegistry
from backend.state import MatchLocks
from backend.timers import TimerService

from tests.test_engine import GAMES, LEVELS, MAIN_OK, FakeGame, full_match, solve


def in_ms(ms: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(milliseconds=ms)).isoformat()


@pytest.fixture(autouse=True)
def short_matches(monkeypatch):
    # Fake matches are LEVELS levels regardless of the real config.
    monkeypatch.setattr(config, "LEVEL_COUNT", LEVELS)


def make_engine() -> RelayEngine:
    registry = GameRegistry(modules=[FakeGame(game_id) for game_id in GAMES])
    return RelayEngine(registry)


# --- TimerService ---

def test_timer_fires_at_deadline_with_args():
    async def scenario():
        fired: list[tuple[str, str, str]] = []

        async def on_fire(match_id, player_id, kind):
            fired.append((match_id, player_id, kind))

        service = TimerService(on_fire)
        service.schedule("m1", "p1", "wait", in_ms(50))
        await asyncio.sleep(0.02)
        assert fired == []  # not yet
        await asyncio.sleep(0.06)
        assert fired == [("m1", "p1", "wait")]
        assert service.pending("m1") == set()

    asyncio.run(scenario())


def test_scheduling_new_timer_cancels_old():
    async def scenario():
        fired = []

        async def on_fire(match_id, player_id, kind):
            fired.append(kind)

        service = TimerService(on_fire)
        service.schedule("m1", "p1", "wait", in_ms(30))
        service.schedule("m1", "p1", "wait-2", in_ms(60))  # replaces the first
        await asyncio.sleep(0.1)
        assert fired == ["wait-2"]  # old timer never fired

    asyncio.run(scenario())


def test_cancel_and_cancel_match():
    async def scenario():
        fired = []

        async def on_fire(match_id, player_id, kind):
            fired.append(player_id)

        service = TimerService(on_fire)
        service.schedule("m1", "p1", "wait", in_ms(30))
        service.schedule("m1", "p2", "wait", in_ms(30))
        service.schedule("m2", "p3", "wait", in_ms(30))
        service.cancel("m1", "p1")
        service.cancel_match("m2")
        assert service.pending("m1") == {"p2"} and service.pending("m2") == set()
        await asyncio.sleep(0.06)
        assert fired == ["p2"]

    asyncio.run(scenario())


def test_apply_result_schedules_and_cancels():
    async def scenario():
        async def on_fire(match_id, player_id, kind):
            pass

        service = TimerService(on_fire)
        service.schedule("m1", "p1", "wait", in_ms(500))
        result = EngineResult(
            cancel=["p1"],
            schedule=[TimerRequest(scope_id="p2", kind="wait", deadline=in_ms(500))],
        )
        service.apply_result("m1", result)
        assert service.pending("m1") == {"p2"}
        service.cancel_match("m1")

    asyncio.run(scenario())


def test_advance_cancels_team_timers():
    """AC: wait timers die with the advance — nobody loses cleared status for a
    level their team already finished."""
    async def scenario():
        engine = make_engine()
        match, members, _ = full_match(engine)
        match.config_snapshot["wait_seconds"] = 0.05  # fast waits for the test

        async def on_fire(match_id, player_id, kind):
            service.apply_result(match_id, engine.on_wait_expired(match, player_id))

        service = TimerService(on_fire)
        alpha = members["alpha"]
        for player in alpha[:3]:  # three clear; wait timers pending
            result = solve(engine, match, player, now=None)
            service.apply_result(match.id, result)
        assert service.pending(match.id) == {p.id for p in alpha[:3]}

        # 4th clear before any wait expires → advance; cancels team timers.
        result = solve(engine, match, alpha[3], now=None)
        assert result.advanced_team_ids == ["alpha"]
        service.apply_result(match.id, result)
        assert service.pending(match.id) == set()

        served = {p.id: p.current_main.id for p in alpha}
        await asyncio.sleep(0.12)  # well past the old deadlines
        # No ghost fire: everyone still solving the level-2 puzzle they got.
        assert all(p.status == "solving" for p in alpha)
        assert {p.id: p.current_main.id for p in alpha} == served

    asyncio.run(scenario())


def test_wait_timer_fires_engine_hook_and_drops_cleared():
    """AC: a scheduled wait timer fires on_wait_expired at the deadline."""
    async def scenario():
        engine = make_engine()
        match, members, _ = full_match(engine)
        match.config_snapshot["wait_seconds"] = 0.05

        async def on_fire(match_id, player_id, kind):
            service.apply_result(match_id, engine.on_wait_expired(match, player_id))

        service = TimerService(on_fire)
        player = members["alpha"][0]
        result = solve(engine, match, player, now=None)
        service.apply_result(match.id, result)
        assert player.status == "cleared"
        await asyncio.sleep(0.1)
        assert player.status == "solving"  # hook fired: cleared status lapsed
        assert player.current_main is not None
        assert service.pending(match.id) == set()
        service.cancel_match(match.id)

    asyncio.run(scenario())


def test_a_board_deadline_routes_through_the_real_server_hook(monkeypatch):
    """`_timer_fired` sends everything non-`duel_` to `on_wait_expired`, so the
    new kind needed a branch — and that branch has to unwrap `fuse:<id>` back
    into a player id. Both are tested through the real function, because a
    hand-wired callback would prove neither."""
    import backend.main as server

    async def scenario():
        engine = make_engine()
        # `_timer_fired` reaches for the module-level engine, which is the
        # point of routing through it — so the fake library goes there.
        monkeypatch.setattr(server.engine, "registry", engine.registry)
        match, members, _ = full_match(engine)
        player = members["alpha"][0]
        # Opt this seat's board in, as a capped game's payload would.
        player.current_main.payload["time_limit_seconds"] = 30
        result = EngineResult()
        engine._arm_board_deadline(match, player, result, None)
        board = player.current_main.id
        assert player.puzzle_deadline is not None
        assert result.schedule[0].scope_id == f"fuse:{player.id}"
        assert result.schedule[0].kind == "puzzle"

        await server.store.add(match)
        try:
            # Not due yet: the routing works, the engine declines.
            await server._timer_fired(match.id, f"fuse:{player.id}", "puzzle")
            assert player.current_main.id == board
            # Due: the same route serves a fresh board.
            player.puzzle_deadline = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
            await server._timer_fired(match.id, f"fuse:{player.id}", "puzzle")
            assert player.current_main.id != board
            assert player.status == "solving"
        finally:
            await server.store.remove(match.id)
            server.timers.cancel_match(match.id)

    asyncio.run(scenario())


# --- per-match serialization ---

def test_concurrent_final_submits_are_serialized_and_deterministic():
    """Two teams' winning submissions race; the lock serializes them, the first
    one wins, the second is rejected (match already finished)."""
    async def scenario():
        engine = make_engine()
        match, members, _ = full_match(engine)
        # Both teams to the last level, all but one player cleared on each.
        for _ in range(LEVELS - 1):
            for team_id in ("alpha", "bravo"):
                for player in members[team_id]:
                    solve(engine, match, player, now=None)
        for team_id in ("alpha", "bravo"):
            for player in members[team_id][:3]:
                solve(engine, match, player, now=None)
        assert all(match.teams[t].level == LEVELS for t in ("alpha", "bravo"))

        locks = MatchLocks()
        results = []

        async def final_submit(player):
            async with locks.for_match(match.id):
                result = engine.submit_answer(
                    match, player.id, player.current_main.id, MAIN_OK
                )
                await asyncio.sleep(0.02)  # simulate broadcast I/O inside the section
                results.append(result)

        await asyncio.gather(
            final_submit(members["alpha"][3]), final_submit(members["bravo"][3])
        )
        # First acquirer (alpha) wins; bravo's submit found a finished match.
        assert match.winner_team_id == "alpha"
        assert results[0].winner_team_id == "alpha"
        assert results[1].ok is False
        assert match.teams["bravo"].finished is False

    asyncio.run(scenario())


def test_locks_are_per_match():
    locks = MatchLocks()
    assert locks.for_match("m1") is locks.for_match("m1")
    assert locks.for_match("m1") is not locks.for_match("m2")
    locks.discard("m1")
    assert locks.for_match("m1") is not None  # fresh lock after discard
