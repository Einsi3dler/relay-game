"""T2.8 — engine unit tests for the GAME_DESIGN §4 relay loop (T2.1–T2.7)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.engine import RelayEngine
from backend.games.base import PuzzleInstance
from backend.models import Match, green
from backend.registry import GameRegistry

NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)

MAIN_OK = "main-ok"
HOLD_OK = "hold-ok"


class FakeGame:
    """Deterministic stand-in: one known answer per kind, seed in the prompt."""

    def __init__(self, game_id: str) -> None:
        self.id = game_id
        self.name = game_id.title()

    def generate_main(self, seed: int) -> PuzzleInstance:
        return PuzzleInstance(
            game_id=self.id, kind="main", prompt=f"main {seed}", answer=MAIN_OK
        )

    def generate_holding(self, seed: int) -> PuzzleInstance:
        return PuzzleInstance(
            game_id=self.id, kind="holding", prompt=f"hold {seed}", answer=HOLD_OK
        )

    def check(self, puzzle: PuzzleInstance, answer: str) -> bool:
        return answer == puzzle.answer

    def reset(self) -> None:
        return None


ORDER = ["g1", "g2", "g3", "g4"]


@pytest.fixture
def engine() -> RelayEngine:
    registry = GameRegistry(
        modules=[FakeGame(game_id) for game_id in ORDER], game_order=ORDER
    )
    return RelayEngine(registry)


def full_match(engine: RelayEngine) -> tuple[Match, dict[str, list]]:
    """8 players join; the last join starts the match. Returns members per team."""
    match = engine.create_match()
    members: dict[str, list] = {"alpha": [], "bravo": []}
    for team_id in ("alpha", "bravo"):
        for i in range(4):
            player, _ = engine.join_match(match, f"{team_id[0].upper()}{i}", team_id, now=NOW)
            members[team_id].append(player)
    return match, members


def solve_main(engine, match, player, answer=MAIN_OK, now=NOW):
    return engine.submit_main(match, player.id, player.current_main.id, answer, now=now)


def make_all_green_except(engine, match, members, holdout):
    for player in members:
        if player is not holdout:
            assert solve_main(engine, match, player).correct is True


# --- T2.1 join & lobby ---

def test_join_autobalance_alternates_teams(engine):
    match = engine.create_match()
    sides = []
    for i in range(4):
        player, _ = engine.join_match(match, f"P{i}")
        sides.append(player.team_id)
    assert sides.count("alpha") == 2 and sides.count("bravo") == 2


def test_join_full_team_raises(engine):
    match = engine.create_match()
    for i in range(4):
        engine.join_match(match, f"A{i}", "alpha")
    with pytest.raises(ValueError):
        engine.join_match(match, "A4", "alpha")


def test_join_after_start_raises(engine):
    match, _ = full_match(engine)
    assert match.status == "active"
    with pytest.raises(ValueError):
        engine.join_match(match, "late", "alpha")


def test_match_starts_when_both_teams_full(engine):
    match, members = full_match(engine)
    assert match.status == "active"
    assert match.config_snapshot["rest_seconds"] == 15
    for team in match.teams.values():
        assert team.roster_size == 4 and team.stage == 1
    puzzles = [p.current_main for team in members.values() for p in team]
    assert all(p.status == "solving" for team in members.values() for p in team)
    assert len({p.id for p in puzzles}) == 8  # distinct instances
    assert len({p.prompt for p in puzzles}) == 8  # distinct seeds


def test_lobby_not_started_before_min(engine):
    match = engine.create_match()
    for i in range(4):
        engine.join_match(match, f"A{i}", "alpha")
    assert match.status == "lobby"  # bravo still empty


# --- T2.2 submit_main ---

def test_correct_main_goes_resting_with_deadline(engine):
    match, members = full_match(engine)
    player = members["alpha"][0]
    result = solve_main(engine, match, player)
    assert result.correct is True and player.status == "resting"
    assert player.timer_kind == "rest"
    assert player.timer_deadline == (NOW + timedelta(seconds=15)).isoformat()
    assert [(r.player_id, r.kind) for r in result.schedule] == [(player.id, "rest")]


def test_wrong_main_stays_solving_with_fresh_puzzle(engine):
    match, members = full_match(engine)
    player = members["alpha"][0]
    before = player.current_main
    result = solve_main(engine, match, player, answer="nope")
    assert result.correct is False and player.status == "solving"
    assert player.current_main.id != before.id
    assert player.current_main.prompt != before.prompt  # new seed
    assert player.attempt == 2


def test_stale_or_foreign_puzzle_id_rejected(engine):
    match, members = full_match(engine)
    player = members["alpha"][0]
    result = engine.submit_main(match, player.id, "bogus-id", MAIN_OK, now=NOW)
    assert result.ok is False and player.status == "solving"
    other = members["alpha"][1]
    result = engine.submit_main(match, player.id, other.current_main.id, MAIN_OK, now=NOW)
    assert result.ok is False


def test_submit_while_resting_rejected(engine):
    match, members = full_match(engine)
    player = members["alpha"][0]
    puzzle_id = player.current_main.id
    solve_main(engine, match, player)
    result = engine.submit_main(match, player.id, puzzle_id, MAIN_OK, now=NOW)
    assert result.ok is False


# --- T2.3 advance check + win ---

def test_advance_blocked_until_all_green(engine):
    match, members = full_match(engine)
    make_all_green_except(engine, match, members["alpha"], members["alpha"][3])
    assert match.teams["alpha"].stage == 1


def test_advance_on_fourth_green_mid_rest(engine):
    match, members = full_match(engine)
    make_all_green_except(engine, match, members["alpha"], members["alpha"][3])
    result = solve_main(engine, match, members["alpha"][3])
    team = match.teams["alpha"]
    assert result.advanced_team_ids == ["alpha"] and team.stage == 2
    for player in members["alpha"]:
        assert player.status == "solving"
        assert player.current_main.game_id == "g2"
        assert player.timer_deadline is None
    # No rest timer survives the advance; all team timers cancelled.
    assert result.schedule == []
    assert set(result.cancel) == {p.id for p in members["alpha"]}


def test_teams_advance_independently(engine):
    match, members = full_match(engine)
    for player in members["alpha"]:
        solve_main(engine, match, player)
    assert match.teams["alpha"].stage == 2
    assert match.teams["bravo"].stage == 1
    assert all(p.status == "solving" for p in members["bravo"])


def test_win_fires_only_on_stage_4(engine):
    match, members = full_match(engine)
    for stage in (1, 2, 3):
        for player in members["alpha"]:
            solve_main(engine, match, player)
        assert match.winner_team_id is None
        assert match.teams["alpha"].stage == stage + 1
    for player in members["alpha"][:3]:
        solve_main(engine, match, player)
    result = solve_main(engine, match, members["alpha"][3])
    assert result.winner_team_id == "alpha"
    assert match.status == "finished" and match.winner_team_id == "alpha"
    assert match.teams["alpha"].finished is True
    assert all(p.status == "finished" for p in members["alpha"])
    assert result.schedule == []
    # Match over: no further submissions accepted.
    late = engine.submit_main(
        match, members["bravo"][0].id, members["bravo"][0].current_main.id, MAIN_OK, now=NOW
    )
    assert late.ok is False


# --- T2.4 rest expiry ---

def test_rest_expiry_moves_to_holding(engine):
    match, members = full_match(engine)
    player = members["alpha"][0]
    solve_main(engine, match, player)
    later = NOW + timedelta(seconds=15)
    result = engine.on_rest_expired(match, player.id, now=later)
    assert player.status == "holding" and green(player)
    assert player.current_holding is not None
    assert player.timer_kind == "holding"
    assert player.timer_deadline == (later + timedelta(seconds=20)).isoformat()
    assert [(r.player_id, r.kind) for r in result.schedule] == [(player.id, "holding")]


def test_rest_expiry_noop_when_not_resting(engine):
    match, members = full_match(engine)
    player = members["alpha"][0]
    result = engine.on_rest_expired(match, player.id, now=NOW)  # still solving
    assert result.changed is False and player.status == "solving"


# --- T2.5 submit_holding ---

def hold_player(engine, match, player):
    solve_main(engine, match, player)
    engine.on_rest_expired(match, player.id, now=NOW)
    return player.current_holding


def test_correct_holding_back_to_resting(engine):
    match, members = full_match(engine)
    player = members["alpha"][0]
    puzzle = hold_player(engine, match, player)
    result = engine.submit_holding(match, player.id, puzzle.id, HOLD_OK, now=NOW)
    assert result.correct is True and player.status == "resting"
    assert player.current_holding is None and player.timer_kind == "rest"


def test_wrong_holding_loses_green(engine):
    match, members = full_match(engine)
    player = members["alpha"][0]
    old_main_prompt = None
    puzzle = hold_player(engine, match, player)
    result = engine.submit_holding(match, player.id, puzzle.id, "nope", now=NOW)
    assert result.correct is False
    assert player.status == "solving" and not green(player)
    assert player.current_main is not None and player.current_main.prompt != old_main_prompt
    assert player.id in result.cancel


def test_holding_player_counts_green_for_advance(engine):
    """A holding player is green, so the 4th teammate's green advances the team
    immediately (design rule 1) — and the holding timer must not survive it."""
    match, members = full_match(engine)
    player = members["alpha"][0]
    hold_player(engine, match, player)
    for teammate in members["alpha"][1:3]:
        solve_main(engine, match, teammate)
    result = solve_main(engine, match, members["alpha"][3])
    assert result.advanced_team_ids == ["alpha"]
    assert match.teams["alpha"].stage == 2
    assert player.status == "solving" and player.current_holding is None
    assert player.id in result.cancel  # no ghost holding question after advance


def test_lose_green_then_cannot_advance(engine):
    match, members = full_match(engine)
    player = members["alpha"][0]
    puzzle = hold_player(engine, match, player)
    engine.submit_holding(match, player.id, puzzle.id, "nope", now=NOW)  # lost green
    make_all_green_except(engine, match, members["alpha"], player)
    assert match.teams["alpha"].stage == 1  # blocked: player must re-qualify
    result = solve_main(engine, match, player)
    assert result.advanced_team_ids == ["alpha"]


# --- T2.6 holding expiry ---

def test_holding_expiry_loses_green(engine):
    match, members = full_match(engine)
    player = members["alpha"][0]
    hold_player(engine, match, player)
    old_main = player.current_main
    result = engine.on_holding_expired(match, player.id, now=NOW)
    assert result.changed is True
    assert player.status == "solving"
    assert player.current_main is not None and old_main is None
    assert player.current_holding is None


def test_holding_expiry_noop_when_not_holding(engine):
    match, members = full_match(engine)
    player = members["alpha"][0]
    result = engine.on_holding_expired(match, player.id, now=NOW)
    assert result.changed is False and player.status == "solving"


# --- T2.7 reconnect / disconnect ---

def test_disconnect_keeps_green_and_team_can_advance(engine):
    match, members = full_match(engine)
    player = members["alpha"][0]
    solve_main(engine, match, player)
    engine.on_disconnect(match, player.id)
    assert player.connected is False and player.status == "resting" and green(player)
    make_all_green_except(engine, match, members["alpha"], player)
    assert match.teams["alpha"].stage == 2  # advanced with a dead socket


def test_reconnect_while_holding_resumes_same_puzzle(engine):
    match, members = full_match(engine)
    player = members["alpha"][0]
    puzzle = hold_player(engine, match, player)
    deadline = player.timer_deadline
    engine.on_disconnect(match, player.id)
    engine.on_reconnect(match, player.id)
    assert player.status == "holding"
    assert player.current_holding.id == puzzle.id  # same instance
    assert player.timer_deadline == deadline  # same timer


def test_reconnect_while_solving_gets_fresh_puzzle(engine):
    match, members = full_match(engine)
    player = members["alpha"][0]
    before = player.current_main
    engine.on_disconnect(match, player.id)
    result = engine.on_reconnect(match, player.id)
    assert result.changed is True and player.connected is True
    assert player.current_main.id != before.id
    assert player.current_main.prompt != before.prompt  # new seed — no replay


# --- GAME_DESIGN §7 worked example, step by step ---

def test_design_section7_worked_example(engine):
    match, members = full_match(engine)
    a, b, c, d = members["alpha"]
    # Get Alpha to Stage 2 first.
    for player in members["alpha"]:
        solve_main(engine, match, player)
    team = match.teams["alpha"]
    assert team.stage == 2

    # 1. All four are solving Game 2.
    assert all(p.status == "solving" and p.current_main.game_id == "g2"
               for p in members["alpha"])

    # 2. A solves → resting, 15s rest. No advance (B, C, D not green).
    result = solve_main(engine, match, a)
    assert a.status == "resting" and result.advanced_team_ids == []

    # 3. B solves → resting. No advance.
    result = solve_main(engine, match, b)
    assert b.status == "resting" and result.advanced_team_ids == []

    # 4. A's rest ends; team not all green → A holding, 20s timer.
    engine.on_rest_expired(match, a.id, now=NOW + timedelta(seconds=15))
    assert a.status == "holding" and a.current_holding.game_id == "g2"

    # 5. C solves → resting. No advance (D not green).
    result = solve_main(engine, match, c)
    assert c.status == "resting" and result.advanced_team_ids == []

    # 6. A answers holding correctly → resting, new rest timer. No advance.
    result = engine.submit_holding(match, a.id, a.current_holding.id, HOLD_OK, now=NOW)
    assert a.status == "resting" and result.advanced_team_ids == []

    # 7. D solves → all green → advance to Stage 3; everyone solving Game 3.
    result = solve_main(engine, match, d)
    assert result.advanced_team_ids == ["alpha"] and team.stage == 3
    assert all(p.status == "solving" and p.current_main.game_id == "g3"
               for p in members["alpha"])


def test_design_section7_alternate_branch_holding_expires(engine):
    """§7 alternate: A's holding timer expires → even D solving doesn't advance."""
    match, members = full_match(engine)
    a, b, c, d = members["alpha"]
    for player in (b, c):
        solve_main(engine, match, player)
    solve_main(engine, match, a)
    engine.on_rest_expired(match, a.id, now=NOW + timedelta(seconds=15))
    engine.on_holding_expired(match, a.id, now=NOW + timedelta(seconds=35))
    assert a.status == "solving"
    result = solve_main(engine, match, d)
    assert result.advanced_team_ids == []  # A isn't green
    assert match.teams["alpha"].stage == 1
    result = solve_main(engine, match, a)  # A re-qualifies
    assert result.advanced_team_ids == ["alpha"]
