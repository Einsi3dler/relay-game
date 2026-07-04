"""T3.1 TimerService + T3.2 per-match serialization."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from backend.engine import EngineResult, RelayEngine, TimerRequest
from backend.registry import GameRegistry
from backend.state import MatchLocks
from backend.timers import TimerService

from tests.test_engine import MAIN_OK, ORDER, FakeGame


def in_ms(ms: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(milliseconds=ms)).isoformat()


def make_engine() -> RelayEngine:
    registry = GameRegistry(
        modules=[FakeGame(game_id) for game_id in ORDER], game_order=ORDER
    )
    return RelayEngine(registry)


# --- T3.1 TimerService ---

def test_timer_fires_at_deadline_with_args():
    async def scenario():
        fired: list[tuple[str, str, str]] = []

        async def on_fire(match_id, player_id, kind):
            fired.append((match_id, player_id, kind))

        service = TimerService(on_fire)
        service.schedule("m1", "p1", "rest", in_ms(50))
        await asyncio.sleep(0.02)
        assert fired == []  # not yet
        await asyncio.sleep(0.06)
        assert fired == [("m1", "p1", "rest")]
        assert service.pending("m1") == set()

    asyncio.run(scenario())


def test_scheduling_new_timer_cancels_old():
    async def scenario():
        fired = []

        async def on_fire(match_id, player_id, kind):
            fired.append(kind)

        service = TimerService(on_fire)
        service.schedule("m1", "p1", "rest", in_ms(30))
        service.schedule("m1", "p1", "holding", in_ms(60))  # replaces the rest timer
        await asyncio.sleep(0.1)
        assert fired == ["holding"]  # old timer never fired

    asyncio.run(scenario())


def test_cancel_and_cancel_match():
    async def scenario():
        fired = []

        async def on_fire(match_id, player_id, kind):
            fired.append(player_id)

        service = TimerService(on_fire)
        service.schedule("m1", "p1", "rest", in_ms(30))
        service.schedule("m1", "p2", "rest", in_ms(30))
        service.schedule("m2", "p3", "rest", in_ms(30))
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
        service.schedule("m1", "p1", "rest", in_ms(500))
        result = EngineResult(
            cancel=["p1"],
            schedule=[TimerRequest(player_id="p2", kind="rest", deadline=in_ms(500))],
        )
        service.apply_result("m1", result)
        assert service.pending("m1") == {"p2"}
        service.cancel_match("m1")

    asyncio.run(scenario())


def test_advance_cancels_team_timers_no_ghost_holding():
    """AC: rest timers die with the advance — nobody gets a holding question
    after their team moved on."""
    async def scenario():
        engine = make_engine()
        match = engine.create_match()
        members = []
        for team_id in ("alpha", "bravo"):
            for i in range(4):
                player, result = engine.join_match(match, f"{team_id[0]}{i}", team_id)
                members.append(player)
        match.config_snapshot["rest_seconds"] = 0.05  # fast rest for the test

        async def on_fire(match_id, player_id, kind):
            hook = {"rest": engine.on_rest_expired, "holding": engine.on_holding_expired}[kind]
            service.apply_result(match_id, hook(match, player_id))

        service = TimerService(on_fire)
        alpha = members[:4]
        for player in alpha[:3]:  # three go green; rest timers pending
            result = engine.submit_main(match, player.id, player.current_main.id, MAIN_OK)
            service.apply_result(match.id, result)
        assert service.pending(match.id) == {p.id for p in alpha[:3]}

        # 4th green before any rest expires → advance; cancels team timers.
        result = engine.submit_main(match, alpha[3].id, alpha[3].current_main.id, MAIN_OK)
        assert result.advanced_team_ids == ["alpha"]
        service.apply_result(match.id, result)
        assert service.pending(match.id) == set()

        await asyncio.sleep(0.12)  # well past the old deadlines
        assert all(p.status == "solving" for p in alpha)  # no ghost holding

    asyncio.run(scenario())


def test_rest_timer_fires_engine_hook_and_serves_holding():
    """AC: a scheduled rest timer fires on_rest_expired at the deadline."""
    async def scenario():
        engine = make_engine()
        match = engine.create_match()
        players = []
        for team_id in ("alpha", "bravo"):
            for i in range(4):
                player, _ = engine.join_match(match, f"{team_id[0]}{i}", team_id)
                players.append(player)
        match.config_snapshot["rest_seconds"] = 0.05
        match.config_snapshot["holding_seconds"] = 5

        async def on_fire(match_id, player_id, kind):
            hook = {"rest": engine.on_rest_expired, "holding": engine.on_holding_expired}[kind]
            service.apply_result(match_id, hook(match, player_id))

        service = TimerService(on_fire)
        player = players[0]
        result = engine.submit_main(match, player.id, player.current_main.id, MAIN_OK)
        service.apply_result(match.id, result)
        assert player.status == "resting"
        await asyncio.sleep(0.1)
        assert player.status == "holding"  # hook fired and cascaded
        assert player.current_holding is not None
        assert service.pending(match.id) == {player.id}  # holding timer now pending
        service.cancel_match(match.id)

    asyncio.run(scenario())


# --- T3.2 per-match serialization ---

def test_concurrent_final_submits_are_serialized_and_deterministic():
    """Two teams' winning submissions race; the lock serializes them, the first
    one wins, the second is rejected (match already finished)."""
    async def scenario():
        engine = make_engine()
        match = engine.create_match()
        members = {"alpha": [], "bravo": []}
        for team_id in ("alpha", "bravo"):
            for i in range(4):
                player, _ = engine.join_match(match, f"{team_id[0]}{i}", team_id)
                members[team_id].append(player)
        # Both teams to Stage 4, all but one player green on each.
        for _ in range(3):
            for team_id in ("alpha", "bravo"):
                for player in members[team_id]:
                    engine.submit_main(match, player.id, player.current_main.id, MAIN_OK)
        for team_id in ("alpha", "bravo"):
            for player in members[team_id][:3]:
                engine.submit_main(match, player.id, player.current_main.id, MAIN_OK)
        assert all(match.teams[t].stage == 4 for t in ("alpha", "bravo"))

        locks = MatchLocks()
        results = []

        async def final_submit(player):
            async with locks.for_match(match.id):
                result = engine.submit_main(
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
