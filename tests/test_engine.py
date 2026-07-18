"""T2.8 — engine unit tests for the GAME_DESIGN §4 relay loop (T2.1–T2.7)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend import config
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
def engine(monkeypatch) -> RelayEngine:
    # Fake matches are 4 stages regardless of the real roster size, so the
    # scripted win-on-stage-4 flows stay stable as real games are added.
    monkeypatch.setattr(config, "STAGE_COUNT", len(ORDER))
    registry = GameRegistry(
        modules=[FakeGame(game_id) for game_id in ORDER], game_order=ORDER
    )
    return RelayEngine(registry)


def full_match(engine: RelayEngine) -> tuple[Match, dict[str, list]]:
    """8 players join with explicit teams; the host starts the match."""
    match = engine.create_match()
    members: dict[str, list] = {"alpha": [], "bravo": []}
    for team_id in ("alpha", "bravo"):
        for i in range(4):
            player, _ = engine.join_match(match, f"{team_id[0].upper()}{i}", team_id, now=NOW)
            members[team_id].append(player)
    result = engine.host_start(match, match.host_player_id, now=NOW)
    assert result.match_started
    return match, members


def solve_main(engine, match, player, answer=MAIN_OK, now=NOW):
    return engine.submit_main(match, player.id, player.current_main.id, answer, now=now)


def make_all_green_except(engine, match, members, holdout):
    for player in members:
        if player is not holdout:
            assert solve_main(engine, match, player).correct is True


# --- T2.1 join & lobby (host-controlled) ---

def test_join_lands_unassigned_and_first_joiner_hosts(engine):
    match = engine.create_match()
    first, _ = engine.join_match(match, "Ada")
    second, _ = engine.join_match(match, "Bob")
    assert first.team_id is None and second.team_id is None
    assert match.host_player_id == first.id
    assert {p.id for p in match.unassigned()} == {first.id, second.id}


def test_join_full_team_raises(engine):
    match = engine.create_match()
    for i in range(4):
        engine.join_match(match, f"A{i}", "alpha")
    with pytest.raises(ValueError):
        engine.join_match(match, "A4", "alpha")


def test_join_full_match_raises(engine):
    match = engine.create_match()
    for i in range(8):
        engine.join_match(match, f"P{i}")
    with pytest.raises(ValueError):
        engine.join_match(match, "ninth")


def test_join_after_start_raises(engine):
    match, _ = full_match(engine)
    assert match.status == "active"
    with pytest.raises(ValueError):
        engine.join_match(match, "late", "alpha")


def test_host_start_freezes_and_serves(engine):
    match, members = full_match(engine)
    assert match.status == "active"
    assert match.config_snapshot["rest_seconds"] == 15
    for team in match.teams.values():
        assert team.roster_size == 4 and team.stage == 1
    puzzles = [p.current_main for team in members.values() for p in team]
    assert all(p.status == "solving" for team in members.values() for p in team)
    assert len({p.id for p in puzzles}) == 8  # distinct instances
    assert len({p.prompt for p in puzzles}) == 8  # distinct seeds


def test_start_blocked_until_teams_ready(engine):
    match = engine.create_match()
    host, _ = engine.join_match(match, "Host", "alpha")
    for i in range(3):
        engine.join_match(match, f"A{i}", "alpha")
    result = engine.host_start(match, host.id)
    assert result.ok is False and "bravo" in result.error.lower()
    assert match.status == "lobby"
    loner, _ = engine.join_match(match, "Loner")  # unassigned blocks too
    for i in range(3):
        engine.join_match(match, f"B{i}", "bravo")
    engine.host_set_min_players(match, host.id, 3)
    result = engine.host_start(match, host.id)
    assert result.ok is False and "Loner" in result.error
    engine.host_kick(match, host.id, loner.id)
    assert engine.host_start(match, host.id).match_started


def test_set_team_and_switch(engine):
    match = engine.create_match()
    player, _ = engine.join_match(match, "Ada")
    result = engine.set_team(match, player.id, "alpha")
    assert result.ok and player.team_id == "alpha"
    assert player.id in match.teams["alpha"].player_ids
    result = engine.set_team(match, player.id, "bravo")  # switching is fine
    assert result.ok and player.team_id == "bravo"
    assert player.id not in match.teams["alpha"].player_ids
    assert engine.set_team(match, player.id, "bravo").ok is False  # already there
    assert engine.set_team(match, player.id, "ghost").ok is False


def test_set_team_rejects_full_team(engine):
    match = engine.create_match()
    for i in range(4):
        engine.join_match(match, f"A{i}", "alpha")
    late, _ = engine.join_match(match, "Late")
    result = engine.set_team(match, late.id, "alpha")
    assert result.ok is False and "full" in result.error


def test_host_move_and_permissions(engine):
    match = engine.create_match()
    host, _ = engine.join_match(match, "Host")
    other, _ = engine.join_match(match, "Other")
    assert engine.host_move(match, other.id, host.id, "alpha").ok is False  # not host
    result = engine.host_move(match, host.id, other.id, "bravo")
    assert result.ok and other.team_id == "bravo"


def test_host_kick(engine):
    match = engine.create_match()
    host, _ = engine.join_match(match, "Host")
    victim, _ = engine.join_match(match, "Victim", "alpha")
    assert engine.host_kick(match, victim.id, host.id).ok is False  # not host
    assert engine.host_kick(match, host.id, host.id).ok is False  # not yourself
    result = engine.host_kick(match, host.id, victim.id)
    assert result.ok and result.kicked_player_ids == [victim.id]
    assert victim.id not in match.players
    assert victim.id not in match.teams["alpha"].player_ids


def test_host_set_min_players_bounds(engine):
    match = engine.create_match()
    host, _ = engine.join_match(match, "Host")
    assert engine.host_set_min_players(match, host.id, 0).ok is False
    assert engine.host_set_min_players(match, host.id, 5).ok is False
    assert engine.host_set_min_players(match, host.id, 1).ok is True
    assert match.min_players == 1


def test_min_players_one_allows_1v1(engine):
    match = engine.create_match()
    host, _ = engine.join_match(match, "Host", "alpha")
    engine.join_match(match, "Rival", "bravo")
    engine.host_set_min_players(match, host.id, 1)
    result = engine.host_start(match, host.id)
    assert result.match_started
    for team in match.teams.values():
        assert team.roster_size == 1  # advance check uses the frozen roster


def test_claim_host_only_when_host_gone(engine):
    match = engine.create_match()
    host, _ = engine.join_match(match, "Host")
    other, _ = engine.join_match(match, "Other")
    assert engine.claim_host(match, other.id).ok is False  # host still here
    engine.on_disconnect(match, host.id)
    result = engine.claim_host(match, other.id)
    assert result.ok and match.host_player_id == other.id


def test_host_actions_rejected_after_start(engine):
    match, members = full_match(engine)
    host_id = match.host_player_id
    target = members["bravo"][0]
    assert engine.host_kick(match, host_id, target.id).ok is False
    assert engine.host_move(match, host_id, target.id, "alpha").ok is False
    assert engine.set_team(match, target.id, "alpha").ok is False
    assert engine.host_set_min_players(match, host_id, 1).ok is False
    assert engine.host_start(match, host_id).ok is False


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
