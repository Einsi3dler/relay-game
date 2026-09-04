"""Engine unit tests for the v2 loop (docs/REDESIGN_PLAN.md).

Covers: lobby with leaders + game assignment, the clear/wait/bonus state
machine, the chained bonus economy, perks, leader handoff, and reconnect.
"""

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

GAMES = ["g1", "g2", "g3", "g4", "g5"]
LEVELS = 3  # pinned in the fixture so scripted wins stay short


class FakeGame:
    """Deterministic stand-in: known answer, seed and level in the prompt."""

    def __init__(self, game_id: str) -> None:
        self.id = game_id
        self.name = game_id.title()

    def generate_main(self, seed: int, level: int = 1) -> PuzzleInstance:
        return PuzzleInstance(
            game_id=self.id,
            kind="main",
            prompt=f"main {self.id} L{level} {seed}",
            answer=MAIN_OK,
        )

    def generate_holding(self, seed: int) -> PuzzleInstance:
        return PuzzleInstance(
            game_id=self.id, kind="holding", prompt=f"hold {seed}", answer="hold-ok"
        )

    def check(self, puzzle: PuzzleInstance, answer: str) -> bool:
        return answer == puzzle.answer

    def reset(self) -> None:
        return None


@pytest.fixture
def engine(monkeypatch) -> RelayEngine:
    monkeypatch.setattr(config, "LEVEL_COUNT", LEVELS)
    registry = GameRegistry(modules=[FakeGame(game_id) for game_id in GAMES])
    return RelayEngine(registry)


def full_match(engine: RelayEngine) -> tuple[Match, dict[str, list], dict]:
    """5 per team (leader + 4 players), games assigned, host starts."""
    match = engine.create_match()
    members: dict[str, list] = {"alpha": [], "bravo": []}
    leaders: dict[str, object] = {}
    for team_id in ("alpha", "bravo"):
        leader, _ = engine.join_match(match, f"{team_id}-lead", team_id, now=NOW)
        assert engine.claim_leader(match, leader.id).ok
        leaders[team_id] = leader
        for i in range(4):
            player, _ = engine.join_match(
                match, f"{team_id[0].upper()}{i}", team_id, now=NOW
            )
            members[team_id].append(player)
            # Generalist keeps the fake game ids assignable (any game fits).
            assert engine.assign_role(match, leader.id, player.id, "generalist").ok
            assert engine.assign_game(match, leader.id, player.id, GAMES[i]).ok
    result = engine.host_start(match, match.host_player_id, now=NOW)
    assert result.match_started
    return match, members, leaders


def solve(engine, match, player, answer=MAIN_OK, now=NOW):
    return engine.submit_answer(
        match, player.id, player.current_main.id, answer, now=now
    )


def solve_bonus(engine, match, player, answer=MAIN_OK, now=NOW):
    return engine.submit_answer(
        match, player.id, player.current_bonus.id, answer, now=now
    )


def make_all_cleared_except(engine, match, members, holdout):
    for player in members:
        if player is not holdout:
            assert solve(engine, match, player).correct is True


# --- lobby: joining, leaders, assignment ---

def test_join_lands_unassigned_and_first_joiner_hosts(engine):
    match = engine.create_match()
    first, _ = engine.join_match(match, "Ada")
    second, _ = engine.join_match(match, "Bob")
    assert first.team_id is None and second.team_id is None
    assert match.host_player_id == first.id
    assert {p.id for p in match.unassigned()} == {first.id, second.id}


def test_join_full_team_raises(engine):
    match = engine.create_match()
    capacity = match.max_players + 1  # playing members + a leader seat
    for i in range(capacity):
        engine.join_match(match, f"A{i}", "alpha")
    with pytest.raises(ValueError):
        engine.join_match(match, "one-too-many", "alpha")


def test_join_full_match_raises(engine):
    match = engine.create_match()
    capacity = (match.max_players + 1) * len(config.TEAM_IDS)
    for i in range(capacity):
        engine.join_match(match, f"P{i}")
    with pytest.raises(ValueError):
        engine.join_match(match, "one-too-many")


def test_join_after_start_raises(engine):
    match, _, _ = full_match(engine)
    assert match.status == "active"
    with pytest.raises(ValueError):
        engine.join_match(match, "late", "alpha")


def test_claim_leader_rules(engine):
    match = engine.create_match()
    loner, _ = engine.join_match(match, "Loner")
    assert engine.claim_leader(match, loner.id).ok is False  # needs a team
    ada, _ = engine.join_match(match, "Ada", "alpha")
    bob, _ = engine.join_match(match, "Bob", "alpha")
    result = engine.claim_leader(match, ada.id)
    assert result.ok and ada.is_leader
    assert match.teams["alpha"].leader_id == ada.id
    assert engine.claim_leader(match, ada.id).ok is False  # already leads
    assert engine.claim_leader(match, bob.id).ok is False  # seat taken
    engine.on_disconnect(match, ada.id)
    result = engine.claim_leader(match, bob.id)  # claimable when leader is gone
    assert result.ok and bob.is_leader and not ada.is_leader
    assert match.teams["alpha"].leader_id == bob.id


def test_assign_game_rules(engine):
    match = engine.create_match()
    lead, _ = engine.join_match(match, "Lead", "alpha")
    engine.claim_leader(match, lead.id)
    a0, _ = engine.join_match(match, "A0", "alpha")
    a1, _ = engine.join_match(match, "A1", "alpha")
    rival, _ = engine.join_match(match, "Rival", "bravo")
    assert engine.assign_game(match, a0.id, a1.id, "g1").ok is False  # not leader
    assert engine.assign_game(match, lead.id, rival.id, "g1").ok is False  # not teammate
    assert engine.assign_game(match, lead.id, lead.id, "g1").ok is False  # leader doesn't play
    assert engine.assign_game(match, lead.id, a0.id, "ghost").ok is False  # unknown game
    result = engine.assign_game(match, lead.id, a0.id, "g1")  # no role yet
    assert result.ok is False and "role" in result.error
    assert engine.assign_role(match, lead.id, a0.id, "generalist").ok
    assert engine.assign_role(match, lead.id, a1.id, "generalist").ok
    assert engine.assign_game(match, lead.id, a0.id, "g1").ok
    assert a0.assigned_game == "g1"
    result = engine.assign_game(match, lead.id, a1.id, "g1")  # duplicate in team
    assert result.ok is False and "A0" in result.error
    assert engine.assign_game(match, lead.id, a1.id, "g2").ok
    assert engine.assign_game(match, lead.id, a0.id, "g3").ok  # reassign replaces
    assert a0.assigned_game == "g3"
    assert engine.assign_game(match, lead.id, a1.id, "g1").ok  # g1 freed up


def test_assign_role_rules(engine):
    match = engine.create_match()
    lead, _ = engine.join_match(match, "Lead", "alpha")
    engine.claim_leader(match, lead.id)
    a0, _ = engine.join_match(match, "A0", "alpha")
    rival, _ = engine.join_match(match, "Rival", "bravo")
    assert engine.assign_role(match, a0.id, a0.id, "generalist").ok is False  # not leader
    assert engine.assign_role(match, lead.id, rival.id, "generalist").ok is False  # not teammate
    assert engine.assign_role(match, lead.id, lead.id, "generalist").ok is False  # seat has no role
    assert engine.assign_role(match, lead.id, a0.id, "ghost").ok is False  # unknown role
    result = engine.assign_role(match, lead.id, a0.id, "generalist")
    assert result.ok and a0.role == "generalist"
    assert any("Generalist" in event.message for event in result.events)
    # Duplicate roles are legal — game uniqueness is the real constraint.
    a1, _ = engine.join_match(match, "A1", "alpha")
    assert engine.assign_role(match, lead.id, a1.id, "generalist").ok


def test_roles_gate_game_assignment(engine, monkeypatch):
    monkeypatch.setattr(config, "ROLES", {
        "solo": {"name": "Solo", "games": ["g1"]},
        "pair": {"name": "Pair", "games": ["g2", "g3"]},
        "generalist": {"name": "Generalist", "games": None},
        "reserved": {"name": "Reserved", "games": []},
    })
    match = engine.create_match()
    lead, _ = engine.join_match(match, "Lead", "alpha")
    engine.claim_leader(match, lead.id)
    a0, _ = engine.join_match(match, "A0", "alpha")
    a1, _ = engine.join_match(match, "A1", "alpha")
    # A role with no games shipped yet can't be handed out at all.
    result = engine.assign_role(match, lead.id, a0.id, "reserved")
    assert result.ok is False and "no games" in result.error
    assert engine.assign_role(match, lead.id, a0.id, "solo").ok
    result = engine.assign_game(match, lead.id, a0.id, "g2")  # out of role
    assert result.ok is False and "role" in result.error
    assert engine.assign_game(match, lead.id, a0.id, "g1").ok
    # A multi-game role may take either of its games.
    assert engine.assign_role(match, lead.id, a1.id, "pair").ok
    assert engine.assign_game(match, lead.id, a1.id, "g3").ok
    # A role change that no longer fits the current game clears it...
    assert engine.assign_role(match, lead.id, a1.id, "solo").ok
    assert a1.assigned_game is None
    # ...but a change that still fits keeps it.
    engine.assign_role(match, lead.id, a1.id, "pair")
    engine.assign_game(match, lead.id, a1.id, "g2")
    assert engine.assign_role(match, lead.id, a1.id, "generalist").ok
    assert a1.assigned_game == "g2"
    # The Generalist takes anything not already taken.
    assert engine.assign_game(match, lead.id, a1.id, "g5").ok
    result = engine.assign_game(match, lead.id, a1.id, "g1")
    assert result.ok is False and "A0" in result.error  # duplicate rule intact


def test_give_leader_in_lobby(engine):
    match = engine.create_match()
    lead, _ = engine.join_match(match, "Lead", "alpha")
    engine.claim_leader(match, lead.id)
    a0, _ = engine.join_match(match, "A0", "alpha")
    engine.assign_role(match, lead.id, a0.id, "generalist")
    engine.assign_game(match, lead.id, a0.id, "g1")
    result = engine.give_leader(match, lead.id, a0.id)
    assert result.ok
    assert a0.is_leader and not lead.is_leader
    assert a0.role is None and a0.assigned_game is None  # leaders don't play
    assert match.teams["alpha"].leader_id == a0.id
    assert lead.assigned_game is None  # old leader now needs an assignment


def test_switching_team_clears_leadership_role_and_assignment(engine):
    match = engine.create_match()
    lead, _ = engine.join_match(match, "Lead", "alpha")
    engine.claim_leader(match, lead.id)
    a0, _ = engine.join_match(match, "A0", "alpha")
    engine.assign_role(match, lead.id, a0.id, "generalist")
    engine.assign_game(match, lead.id, a0.id, "g1")
    result = engine.set_team(match, a0.id, "bravo")
    assert result.ok
    assert a0.role is None and a0.assigned_game is None
    result = engine.set_team(match, lead.id, "bravo")
    assert result.ok
    assert not lead.is_leader and lead.assigned_game is None
    assert match.teams["alpha"].leader_id is None


def test_kicking_leader_clears_seat(engine):
    match = engine.create_match()
    host, _ = engine.join_match(match, "Host", "alpha")
    victim, _ = engine.join_match(match, "Victim", "bravo")
    engine.claim_leader(match, victim.id)
    result = engine.host_kick(match, host.id, victim.id)
    assert result.ok
    assert match.teams["bravo"].leader_id is None


def test_start_blockers(engine):
    match = engine.create_match()
    lead_a, _ = engine.join_match(match, "LeadA", "alpha")
    engine.claim_leader(match, lead_a.id)
    a0, _ = engine.join_match(match, "A0", "alpha")
    lead_b, _ = engine.join_match(match, "LeadB", "bravo")
    b0, _ = engine.join_match(match, "B0", "bravo")
    engine.host_set_min_players(match, lead_a.id, 1)
    engine.assign_role(match, lead_a.id, a0.id, "generalist")
    engine.assign_game(match, lead_a.id, a0.id, "g1")

    result = engine.host_start(match, lead_a.id)
    assert result.ok is False and "Grandmaster" in result.error  # bravo has none
    engine.claim_leader(match, lead_b.id)

    result = engine.host_start(match, lead_a.id)
    assert result.ok is False and "role" in result.error  # B0's role missing
    engine.assign_role(match, lead_b.id, b0.id, "generalist")

    result = engine.host_start(match, lead_a.id)
    assert result.ok is False and "game" in result.error  # B0's game missing
    engine.assign_game(match, lead_b.id, b0.id, "g1")

    assert engine.host_start(match, lead_a.id).match_started


def test_start_freezes_and_serves_per_player_games(engine):
    match, members, leaders = full_match(engine)
    assert match.status == "active"
    assert match.config_snapshot["wait_seconds"] == config.WAIT_SECONDS
    assert match.config_snapshot["level_count"] == LEVELS
    for team_id, team in match.teams.items():
        assert team.roster_size == 4 and team.level == 1
        assert leaders[team_id].status == "leading"
        assert leaders[team_id].current_puzzle() is None
    for team_players in members.values():
        for i, player in enumerate(team_players):
            assert player.status == "solving"
            assert player.current_main.game_id == GAMES[i]  # their own game
    prompts = {p.current_main.prompt for tp in members.values() for p in tp}
    assert len(prompts) == 8  # distinct seeds


def test_min_players_counts_playing_members_only(engine):
    match = engine.create_match()
    lead_a, _ = engine.join_match(match, "LeadA", "alpha")
    engine.claim_leader(match, lead_a.id)
    a0, _ = engine.join_match(match, "A0", "alpha")
    lead_b, _ = engine.join_match(match, "LeadB", "bravo")
    engine.claim_leader(match, lead_b.id)
    b0, _ = engine.join_match(match, "B0", "bravo")
    engine.assign_role(match, lead_a.id, a0.id, "generalist")
    engine.assign_role(match, lead_b.id, b0.id, "generalist")
    engine.assign_game(match, lead_a.id, a0.id, "g1")
    engine.assign_game(match, lead_b.id, b0.id, "g1")
    engine.host_set_min_players(match, lead_a.id, 1)
    result = engine.host_start(match, lead_a.id)
    assert result.match_started
    for team in match.teams.values():
        assert team.roster_size == 1  # the leader isn't counted


# --- clearing, the wait timer, and currency ---

def test_correct_answer_clears_with_wait_timer_and_pay(engine):
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    result = solve(engine, match, player)
    assert result.correct is True and player.status == "cleared"
    assert green(player) and player.choice_pending
    assert player.timer_kind == "wait"
    expected = (NOW + timedelta(seconds=config.WAIT_SECONDS)).isoformat()
    assert player.timer_deadline == expected
    assert [(r.scope_id, r.kind) for r in result.schedule] == [(player.id, "wait")]
    assert match.teams["alpha"].currency == config.CURRENCY_PER_CLEAR
    assert player.earned_level == 1


def test_wrong_answer_stays_solving_with_fresh_puzzle(engine):
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    before = player.current_main
    result = solve(engine, match, player, answer="nope")
    assert result.correct is False and player.status == "solving"
    assert player.current_main.id != before.id
    assert player.current_main.prompt != before.prompt  # new seed
    assert player.attempt == 2
    assert match.teams["alpha"].currency == 0


def test_stale_or_foreign_puzzle_id_rejected(engine):
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    result = engine.submit_answer(match, player.id, "bogus-id", MAIN_OK, now=NOW)
    assert result.ok is False and player.status == "solving"
    other = members["alpha"][1]
    result = engine.submit_answer(
        match, player.id, other.current_main.id, MAIN_OK, now=NOW
    )
    assert result.ok is False


def test_submit_while_cleared_rejected(engine):
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    puzzle_id = player.current_main.id
    solve(engine, match, player)
    result = engine.submit_answer(match, player.id, puzzle_id, MAIN_OK, now=NOW)
    assert result.ok is False


def test_leader_cannot_submit(engine):
    match, members, leaders = full_match(engine)
    result = engine.submit_answer(
        match, leaders["alpha"].id, "anything", MAIN_OK, now=NOW
    )
    assert result.ok is False


def test_wait_expiry_loses_cleared_status(engine):
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    solve(engine, match, player)
    later = NOW + timedelta(seconds=config.WAIT_SECONDS)
    result = engine.on_wait_expired(match, player.id, now=later)
    assert result.changed is True
    assert player.status == "solving" and not green(player)
    assert player.current_main is not None
    assert player.choice_pending is False


def test_wait_expiry_noop_when_solving(engine):
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    result = engine.on_wait_expired(match, player.id, now=NOW)
    assert result.changed is False and player.status == "solving"


def test_reclear_after_lapse_pays_nothing_but_keeps_earnings(engine):
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    solve(engine, match, player)
    team = match.teams["alpha"]
    assert team.currency == 1
    engine.on_wait_expired(match, player.id, now=NOW + timedelta(seconds=180))
    result = solve(engine, match, player)  # re-clear the same level
    assert result.correct is True and player.status == "cleared"
    assert team.currency == 1  # no farming: first clear per level pays


# --- the wait-or-bonus choice ---

def test_choose_wait_clears_the_choice(engine):
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    solve(engine, match, player)
    result = engine.choose_wait(match, player.id)
    assert result.ok and player.choice_pending is False
    assert player.status == "cleared" and player.timer_kind == "wait"
    assert engine.choose_wait(match, player.id).ok is False  # nothing pending
    assert engine.choose_bonus(match, player.id).ok is False  # choice is locked


def test_choose_bonus_serves_harder_instance_on_same_deadline(engine):
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    solve(engine, match, player)
    deadline = player.timer_deadline
    result = engine.choose_bonus(match, player.id, now=NOW)
    assert result.ok and player.status == "bonus"
    assert not green(player)  # bonus resets cleared status
    assert player.choice_pending is False
    bonus_level = min(1 + config.BONUS_LEVEL_OFFSET, LEVELS + config.BONUS_LEVEL_OFFSET)
    assert f"L{bonus_level}" in player.current_bonus.prompt  # harder instance
    assert player.current_bonus.game_id == "g1"  # their own game
    assert player.timer_deadline == deadline  # same running deadline
    assert result.schedule == [] and result.cancel == []


def test_bonus_success_pays_and_rechains(engine):
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    team = match.teams["alpha"]
    solve(engine, match, player)
    engine.choose_bonus(match, player.id, now=NOW)
    result = solve_bonus(engine, match, player)
    assert result.correct is True and player.status == "cleared"
    assert team.currency == 1 + config.CURRENCY_BONUS_FIRST
    assert player.bonus_earned == config.CURRENCY_BONUS_FIRST
    assert player.choice_pending is True  # a fresh wait-or-bonus choice
    assert player.timer_kind == "wait"
    # Chain a second bonus: diminishing pay.
    engine.choose_bonus(match, player.id, now=NOW)
    result = solve_bonus(engine, match, player)
    assert result.correct is True
    assert team.currency == 1 + config.CURRENCY_BONUS_FIRST + config.CURRENCY_BONUS_REPEAT


def test_bonus_failure_forfeits_bonus_earnings(engine):
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    team = match.teams["alpha"]
    solve(engine, match, player)
    engine.choose_bonus(match, player.id, now=NOW)
    solve_bonus(engine, match, player)  # +3 (first bonus)
    engine.choose_bonus(match, player.id, now=NOW)
    result = solve_bonus(engine, match, player, answer="nope")
    assert result.correct is False and player.status == "solving"
    assert team.currency == 1  # base clear pay stays; bonus pay forfeited
    assert player.bonus_earned == 0
    assert player.id in result.cancel  # wait timer cancelled


def test_bonus_deadline_expiry_is_a_failure(engine):
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    team = match.teams["alpha"]
    solve(engine, match, player)
    engine.choose_bonus(match, player.id, now=NOW)
    solve_bonus(engine, match, player)
    engine.choose_bonus(match, player.id, now=NOW)
    result = engine.on_wait_expired(
        match, player.id, now=NOW + timedelta(seconds=config.WAIT_SECONDS)
    )
    assert result.changed is True and player.status == "solving"
    assert team.currency == 1 and player.bonus_earned == 0


def test_forfeit_clamps_team_currency_at_zero(engine):
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    team = match.teams["alpha"]
    solve(engine, match, player)
    engine.choose_bonus(match, player.id, now=NOW)
    solve_bonus(engine, match, player)  # currency = 1 + 3
    team.currency = 2  # the leader spent most of it in the meantime
    result = solve_bonus_after_rechain(engine, match, player, answer="nope")
    assert result.correct is False
    assert team.currency == 0  # clamped, not negative


def solve_bonus_after_rechain(engine, match, player, answer):
    engine.choose_bonus(match, player.id, now=NOW)
    return solve_bonus(engine, match, player, answer=answer)


# --- advance & win ---

def test_advance_blocked_until_all_cleared(engine):
    match, members, _ = full_match(engine)
    make_all_cleared_except(engine, match, members["alpha"], members["alpha"][3])
    assert match.teams["alpha"].level == 1


def test_advance_on_fourth_clear(engine):
    match, members, _ = full_match(engine)
    make_all_cleared_except(engine, match, members["alpha"], members["alpha"][3])
    result = solve(engine, match, members["alpha"][3])
    team = match.teams["alpha"]
    assert result.advanced_team_ids == ["alpha"] and team.level == 2
    for i, player in enumerate(members["alpha"]):
        assert player.status == "solving"
        assert player.current_main.game_id == GAMES[i]  # still their game
        # Round 2 of a LEVELS-long race, mapped onto the 13-rung ladder.
        tier = config.difficulty_tier(2, LEVELS)
        assert f"L{tier}" in player.current_main.prompt
        assert player.timer_deadline is None
    assert result.schedule == []  # no wait timer survives the advance
    # Both of a member's scopes go: the wait timer, and the board deadline the
    # game they are being re-served may or may not ask for.
    assert set(result.cancel) == (
        {p.id for p in members["alpha"]}
        | {f"fuse:{p.id}" for p in members["alpha"]}
    )


def test_bonus_player_blocks_advance(engine):
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    solve(engine, match, player)
    engine.choose_bonus(match, player.id, now=NOW)
    make_all_cleared_except(engine, match, members["alpha"], player)
    assert match.teams["alpha"].level == 1  # bonus player isn't cleared
    result = solve_bonus(engine, match, player)
    assert result.advanced_team_ids == ["alpha"]  # bonus success advances


def test_advance_resets_bonus_streak(engine):
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    team = match.teams["alpha"]
    solve(engine, match, player)
    engine.choose_bonus(match, player.id, now=NOW)
    solve_bonus(engine, match, player)  # first bonus of level 1: +3
    make_all_cleared_except(engine, match, members["alpha"], player)
    assert team.level == 2
    currency_before = team.currency
    solve(engine, match, player)
    engine.choose_bonus(match, player.id, now=NOW)
    solve_bonus(engine, match, player)
    # First bonus of the NEW level pays the first-bonus rate again.
    assert team.currency == currency_before + config.CURRENCY_PER_CLEAR + config.CURRENCY_BONUS_FIRST


def test_teams_advance_independently(engine):
    match, members, _ = full_match(engine)
    for player in members["alpha"]:
        solve(engine, match, player)
    assert match.teams["alpha"].level == 2
    assert match.teams["bravo"].level == 1
    assert all(p.status == "solving" for p in members["bravo"])


def test_win_on_last_level_only(engine):
    match, members, leaders = full_match(engine)
    for level in range(1, LEVELS):
        for player in members["alpha"]:
            solve(engine, match, player)
        assert match.winner_team_id is None
        assert match.teams["alpha"].level == level + 1
    for player in members["alpha"][:3]:
        solve(engine, match, player)
    result = solve(engine, match, members["alpha"][3])
    assert result.winner_team_id == "alpha"
    assert match.status == "finished" and match.winner_team_id == "alpha"
    assert match.teams["alpha"].finished is True
    assert all(p.status == "finished" for p in members["alpha"])
    assert leaders["alpha"].status == "finished"  # the leader finishes too
    assert result.schedule == []
    late = engine.submit_answer(
        match,
        members["bravo"][0].id,
        members["bravo"][0].current_main.id,
        MAIN_OK,
        now=NOW,
    )
    assert late.ok is False  # match over: no further submissions


# --- perks ---

def test_buy_perk_guards(engine):
    match, members, leaders = full_match(engine)
    leader = leaders["alpha"]
    assert engine.buy_perk(match, members["alpha"][0].id, "shield").ok is False
    assert engine.buy_perk(match, leader.id, "ghost").ok is False
    result = engine.buy_perk(match, leader.id, "shield")
    assert result.ok is False and "currency" in result.error  # can't afford yet


def test_shield_blocks_next_attack_and_is_consumed(engine):
    match, members, leaders = full_match(engine)
    alpha, bravo = match.teams["alpha"], match.teams["bravo"]
    alpha.currency = 10
    bravo.currency = 10
    assert engine.buy_perk(match, leaders["bravo"].id, "shield").ok
    assert bravo.shield_active
    assert engine.buy_perk(match, leaders["bravo"].id, "shield").ok is False  # once
    result = engine.buy_perk(match, leaders["alpha"].id, "freeze")
    assert result.ok and result.perk_used == {"perk_id": "freeze", "by_team_id": "alpha"}
    assert bravo.shield_active is False  # consumed
    assert all(p.frozen_until is None for p in members["bravo"])  # attack blocked
    assert alpha.currency == 10 - config.PERKS["freeze"]["cost"]  # still charged


def test_freeze_locks_a_random_solving_opponent(engine):
    match, members, leaders = full_match(engine)
    match.teams["alpha"].currency = 10
    # Only one valid target: the rest of bravo is cleared.
    make_all_cleared_except(engine, match, members["bravo"], members["bravo"][0])
    target = members["bravo"][0]
    result = engine.buy_perk(match, leaders["alpha"].id, "freeze", now=NOW)
    assert result.ok
    expected = (NOW + timedelta(seconds=config.PERKS["freeze"]["seconds"])).isoformat()
    assert target.frozen_until == expected
    frozen = engine.submit_answer(
        match, target.id, target.current_main.id, MAIN_OK, now=NOW
    )
    assert frozen.ok is False and "frozen" in frozen.error.lower()
    after = engine.submit_answer(
        match,
        target.id,
        target.current_main.id,
        MAIN_OK,
        now=NOW + timedelta(seconds=11),
    )
    assert after.correct is True  # freeze lapsed lazily
    assert target.frozen_until is None


def test_scramble_rerolls_a_solving_opponent(engine):
    match, members, leaders = full_match(engine)
    match.teams["alpha"].currency = 10
    make_all_cleared_except(engine, match, members["bravo"], members["bravo"][0])
    target = members["bravo"][0]
    before = target.current_main
    result = engine.buy_perk(match, leaders["alpha"].id, "scramble", now=NOW)
    assert result.ok
    assert target.status == "solving"
    assert target.current_main.id != before.id  # forced reroll


def test_attack_with_no_valid_target_rejected_and_not_charged(engine):
    match, members, leaders = full_match(engine)
    match.teams["alpha"].currency = 10
    # Scramble needs a *solving* opponent: put bravo at 3 cleared + 1 in bonus
    # (bonus first, so the third clear doesn't advance the team).
    straggler = members["bravo"][0]
    solve(engine, match, straggler)
    engine.choose_bonus(match, straggler.id, now=NOW)
    make_all_cleared_except(engine, match, members["bravo"], straggler)
    result = engine.buy_perk(match, leaders["alpha"].id, "scramble", now=NOW)
    assert result.ok is False and "target" in result.error
    assert match.teams["alpha"].currency == 10  # no charge
    # Freeze can still hit the bonus player, though.
    result = engine.buy_perk(match, leaders["alpha"].id, "freeze", now=NOW)
    assert result.ok and straggler.frozen_until is not None


def test_extend_wait_pushes_a_teammates_deadline(engine):
    match, members, leaders = full_match(engine)
    match.teams["alpha"].currency = 10
    player = members["alpha"][0]
    assert (
        engine.buy_perk(
            match, leaders["alpha"].id, "extend_wait", target_id=player.id
        ).ok
        is False
    )  # not cleared yet
    solve(engine, match, player)
    old_deadline = datetime.fromisoformat(player.timer_deadline)
    result = engine.buy_perk(
        match, leaders["alpha"].id, "extend_wait", target_id=player.id, now=NOW
    )
    assert result.ok
    extended = old_deadline + timedelta(seconds=config.PERKS["extend_wait"]["seconds"])
    assert player.timer_deadline == extended.isoformat()
    assert [(r.scope_id, r.kind) for r in result.schedule] == [(player.id, "wait")]
    opponent = members["bravo"][0]
    solve(engine, match, opponent)
    result = engine.buy_perk(
        match, leaders["alpha"].id, "extend_wait", target_id=opponent.id
    )
    assert result.ok is False  # teammates only


# --- perks: screen effects (cosmetic sabotage) ---

def test_screen_effect_stamps_a_deadline_only_the_victim_can_see(engine):
    match, members, leaders = full_match(engine)
    match.teams["alpha"].currency = 10
    make_all_cleared_except(engine, match, members["bravo"], members["bravo"][0])
    target = members["bravo"][0]
    result = engine.buy_perk(match, leaders["alpha"].id, "wobble", now=NOW)
    assert result.ok
    expected = (NOW + timedelta(seconds=config.PERKS["wobble"]["seconds"])).isoformat()
    assert target.screen_effects == {"wobble": expected}
    # Fog of war: the effect rides the victim's private view, never the public
    # roster the buying leader reads.
    assert "screen_effects" not in target.public()


def test_screen_effects_stack_forward_and_never_shorten(engine):
    """Deadlines are pushed out, never overwritten — an out-of-order or skewed
    second buy must not cut the running effect short."""
    match, members, leaders = full_match(engine)
    match.teams["alpha"].currency = 20
    make_all_cleared_except(engine, match, members["bravo"], members["bravo"][0])
    target = members["bravo"][0]
    assert engine.buy_perk(match, leaders["alpha"].id, "wobble", now=NOW).ok
    long_deadline = target.screen_effects["wobble"]
    assert engine.buy_perk(
        match, leaders["alpha"].id, "wobble", now=NOW - timedelta(seconds=5)
    ).ok
    assert target.screen_effects["wobble"] == long_deadline  # not shortened
    # A different effect id is a separate deadline, not a replacement.
    assert engine.buy_perk(match, leaders["alpha"].id, "static", now=NOW).ok
    assert set(target.screen_effects) == {"wobble", "static"}


def test_screen_effects_reach_bonus_players_but_never_a_duellist(engine):
    match, members, leaders = full_match(engine)
    match.teams["alpha"].currency = 10
    straggler = members["bravo"][0]
    solve(engine, match, straggler)
    engine.choose_bonus(match, straggler.id, now=NOW)
    make_all_cleared_except(engine, match, members["bravo"], straggler)
    result = engine.buy_perk(match, leaders["alpha"].id, "mirror", now=NOW)
    assert result.ok and "mirror" in straggler.screen_effects


# --- perks: Reflect ---

def test_reflect_bounces_an_attack_back_at_its_buyer(engine):
    match, members, leaders = full_match(engine)
    alpha, bravo = match.teams["alpha"], match.teams["bravo"]
    alpha.currency = bravo.currency = 10
    assert engine.buy_perk(match, leaders["bravo"].id, "reflect").ok
    assert engine.buy_perk(match, leaders["bravo"].id, "reflect").ok is False  # once
    result = engine.buy_perk(match, leaders["alpha"].id, "freeze", now=NOW)
    assert result.ok
    assert bravo.reflect_active is False  # consumed
    assert all(p.frozen_until is None for p in members["bravo"])  # they're untouched
    assert sum(p.frozen_until is not None for p in members["alpha"]) == 1  # it came home
    assert alpha.currency == 10 - config.PERKS["freeze"]["cost"]  # still charged


def test_reflect_beats_shield_and_a_bounced_attack_cannot_bounce_again(engine):
    """Both teams holding Reflect must not ping-pong an attack forever: the
    bounced attack lands on the buyer, ignoring their own defenses."""
    match, members, leaders = full_match(engine)
    alpha, bravo = match.teams["alpha"], match.teams["bravo"]
    alpha.currency = bravo.currency = 20
    assert engine.buy_perk(match, leaders["bravo"].id, "reflect").ok
    assert engine.buy_perk(match, leaders["bravo"].id, "shield").ok
    assert engine.buy_perk(match, leaders["alpha"].id, "reflect").ok
    assert engine.buy_perk(match, leaders["alpha"].id, "freeze", now=NOW).ok
    assert bravo.reflect_active is False  # reflect resolves before shield
    assert bravo.shield_active is True  # ...so the shield is untouched
    assert alpha.reflect_active is True  # the buyer's own reflect never fires
    assert sum(p.frozen_until is not None for p in members["alpha"]) == 1


def test_a_reflected_attack_with_no_target_consumes_nothing(engine):
    """The validate-then-mutate rule: a rejected buy must leave reflect, shield
    and currency exactly as it found them."""
    match, members, leaders = full_match(engine)
    alpha, bravo = match.teams["alpha"], match.teams["bravo"]
    alpha.currency = bravo.currency = 10
    assert engine.buy_perk(match, leaders["bravo"].id, "reflect").ok
    # Scramble needs a *solving* victim, and the bounce would land on alpha — so
    # leave alpha with nobody solving. Bonus first, so the last clear doesn't
    # advance the team and hand everyone a fresh board.
    straggler = members["alpha"][0]
    solve(engine, match, straggler)
    engine.choose_bonus(match, straggler.id, now=NOW)
    make_all_cleared_except(engine, match, members["alpha"], straggler)
    banked = (alpha.currency, bravo.currency)  # clearing paid out along the way
    result = engine.buy_perk(match, leaders["alpha"].id, "scramble", now=NOW)
    assert result.ok is False and "target" in result.error
    assert bravo.reflect_active is True  # NOT consumed by a rejected attack
    assert (alpha.currency, bravo.currency) == banked  # nobody charged


# --- perks: Skim, Clock Burn, Silence, Insurance ---

def test_skim_moves_currency_and_is_refused_on_an_empty_pool(engine):
    match, _, leaders = full_match(engine)
    alpha, bravo = match.teams["alpha"], match.teams["bravo"]
    alpha.currency, bravo.currency = 10, 5
    cost = config.PERKS["skim"]["cost"]
    amount = config.PERKS["skim"]["amount"]
    assert engine.buy_perk(match, leaders["alpha"].id, "skim", now=NOW).ok
    assert bravo.currency == 5 - amount
    # Attrition, not farming: the buyer pays more than they take.
    assert alpha.currency == 10 + amount - cost
    bravo.currency = 0
    result = engine.buy_perk(match, leaders["alpha"].id, "skim", now=NOW)
    assert result.ok is False and "empty" in result.error
    assert alpha.currency == 10 + amount - cost  # no charge


def test_clock_burn_shortens_a_cleared_opponents_wait(engine):
    match, members, leaders = full_match(engine)
    match.teams["alpha"].currency = 10
    result = engine.buy_perk(match, leaders["alpha"].id, "clock_burn", now=NOW)
    assert result.ok is False and "target" in result.error  # nobody cleared yet
    target = members["bravo"][0]
    solve(engine, match, target)
    before = datetime.fromisoformat(target.timer_deadline)
    result = engine.buy_perk(match, leaders["alpha"].id, "clock_burn", now=NOW)
    assert result.ok
    burned = before - timedelta(seconds=config.PERKS["clock_burn"]["seconds"])
    assert target.timer_deadline == burned.isoformat()
    assert [(r.scope_id, r.kind) for r in result.schedule] == [(target.id, "wait")]


def test_silence_stamps_a_deadline_on_the_victim_team(engine):
    match, _, leaders = full_match(engine)
    match.teams["alpha"].currency = 10
    result = engine.buy_perk(match, leaders["alpha"].id, "silence", now=NOW)
    assert result.ok
    expected = (NOW + timedelta(seconds=config.PERKS["silence"]["seconds"])).isoformat()
    assert match.teams["bravo"].silenced_until == expected
    assert match.teams["alpha"].silenced_until is None  # the buyer keeps their eyes


def test_insurance_covers_one_failed_bonus(engine):
    match, members, leaders = full_match(engine)
    player = members["alpha"][0]
    team = match.teams["alpha"]
    solve(engine, match, player)
    engine.choose_bonus(match, player.id, now=NOW)
    solve_bonus(engine, match, player)  # +3 (first bonus)
    team.currency += 10
    assert engine.buy_perk(match, leaders["alpha"].id, "insurance").ok
    assert engine.buy_perk(match, leaders["alpha"].id, "insurance").ok is False  # once
    banked = team.currency
    result = solve_bonus_after_rechain(engine, match, player, answer="nope")
    assert result.correct is False
    assert team.currency == banked  # earnings kept, not forfeited
    assert team.insurance_active is False  # consumed
    assert player.bonus_earned == 0


def test_insurance_is_not_burned_by_a_failure_that_costs_nothing(engine):
    match, members, leaders = full_match(engine)
    player = members["alpha"][0]
    team = match.teams["alpha"]
    team.currency = 10
    assert engine.buy_perk(match, leaders["alpha"].id, "insurance").ok
    solve(engine, match, player)
    engine.choose_bonus(match, player.id, now=NOW)
    result = solve_bonus(engine, match, player, answer="nope")  # nothing earned yet
    assert result.correct is False and player.bonus_earned == 0
    assert team.insurance_active is True  # still held for a failure that hurts


# --- the bonus difficulty ladder ---

def test_bonus_level_climbs_past_the_last_level(engine):
    """The bonus board used to clamp at LEVEL_COUNT, so a team on the final
    level was handed a board exactly as hard as the one they had just cleared.
    Bonus tiers run to the top of the table — and because a short race still
    ends on the hardest main tier, its finale bonus reaches the top rung too."""
    match, members, _ = full_match(engine)
    team = match.teams["alpha"]
    player = members["alpha"][0]
    team.level = LEVELS
    solve(engine, match, player)
    assert engine.choose_bonus(match, player.id, now=NOW).ok
    top_tier = config.DIFFICULTY_TIERS
    assert f"L{top_tier}" in player.current_bonus.prompt
    # Reconnecting mid-bonus re-rolls the board at the same tier.
    engine.on_disconnect(match, player.id)
    engine.on_reconnect(match, player.id)
    assert f"L{top_tier}" in player.current_bonus.prompt


# --- leader handoff mid-match ---

def test_give_leader_full_swap(engine):
    match, members, leaders = full_match(engine)
    leader, target = leaders["alpha"], members["alpha"][0]
    team = match.teams["alpha"]
    solve(engine, match, target)  # target is cleared with bonus history
    engine.choose_bonus(match, target.id, now=NOW)
    solve_bonus(engine, match, target)
    earned_level, streak, earned = (
        target.earned_level,
        target.bonus_streak,
        target.bonus_earned,
    )

    result = engine.give_leader(match, leader.id, target.id, now=NOW)
    assert result.ok
    assert target.is_leader and target.status == "leading"
    assert target.current_puzzle() is None and target.timer_deadline is None
    assert target.id in result.cancel  # their wait timer dies
    assert not leader.is_leader and leader.status == "solving"
    assert leader.role == "generalist" and target.role is None  # role moves too
    assert leader.assigned_game == GAMES[0]  # took over the target's game
    assert leader.current_main.game_id == GAMES[0]
    assert "L1" in leader.current_main.prompt  # fresh puzzle at the team level
    # Economy counters move with the seat — no double base pay.
    assert (leader.earned_level, leader.bonus_streak, leader.bonus_earned) == (
        earned_level,
        streak,
        earned,
    )
    assert (target.earned_level, target.bonus_streak, target.bonus_earned) == (0, 0, 0)
    assert team.leader_id == target.id
    assert team.handoff_used_level == team.level


def test_give_leader_once_per_level(engine):
    match, members, leaders = full_match(engine)
    leader, first, second = leaders["alpha"], members["alpha"][0], members["alpha"][1]
    assert engine.give_leader(match, leader.id, first.id, now=NOW).ok
    result = engine.give_leader(match, first.id, second.id, now=NOW)
    assert result.ok is False and "level" in result.error
    # After the team advances, the seat can move again.
    for player in members["alpha"][1:] + [leader]:
        solve(engine, match, player)
    assert match.teams["alpha"].level == 2
    assert engine.give_leader(match, first.id, second.id, now=NOW).ok


def test_give_leader_swap_costs_a_clear(engine):
    match, members, leaders = full_match(engine)
    leader = leaders["alpha"]
    target = members["alpha"][3]
    for player in (members["alpha"][0], members["alpha"][1], target):
        solve(engine, match, player)  # 3 cleared, members[2] still solving
    engine.give_leader(match, leader.id, target.id, now=NOW)  # burns target's clear
    result = solve(engine, match, members["alpha"][2])
    assert result.advanced_team_ids == []  # would have advanced without the swap
    assert match.teams["alpha"].level == 1
    result = solve(engine, match, leader)  # the old leader must clear instead
    assert result.advanced_team_ids == ["alpha"]


def test_give_leader_guards(engine):
    match, members, leaders = full_match(engine)
    leader = leaders["alpha"]
    assert engine.give_leader(match, members["alpha"][0].id, leader.id).ok is False
    assert engine.give_leader(match, leader.id, members["bravo"][0].id).ok is False
    assert engine.give_leader(match, leader.id, leader.id).ok is False


# --- reconnect / disconnect ---

def test_disconnect_keeps_cleared_and_team_can_advance(engine):
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    solve(engine, match, player)
    engine.on_disconnect(match, player.id)
    assert player.connected is False and player.status == "cleared" and green(player)
    make_all_cleared_except(engine, match, members["alpha"], player)
    assert match.teams["alpha"].level == 2  # advanced with a dead socket


def test_reconnect_while_cleared_keeps_timer(engine):
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    solve(engine, match, player)
    deadline = player.timer_deadline
    engine.on_disconnect(match, player.id)
    engine.on_reconnect(match, player.id)
    assert player.status == "cleared"
    assert player.timer_deadline == deadline  # same timer


def test_reconnect_while_solving_gets_fresh_puzzle(engine):
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    before = player.current_main
    engine.on_disconnect(match, player.id)
    result = engine.on_reconnect(match, player.id)
    assert result.changed is True and player.connected is True
    assert player.current_main.id != before.id
    assert player.current_main.prompt != before.prompt  # new seed — no replay


def test_reconnect_while_bonus_gets_fresh_bonus(engine):
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    solve(engine, match, player)
    engine.choose_bonus(match, player.id, now=NOW)
    before = player.current_bonus
    engine.on_disconnect(match, player.id)
    engine.on_reconnect(match, player.id)
    assert player.status == "bonus"
    assert player.current_bonus.id != before.id  # no replay
    assert player.current_bonus.game_id == before.game_id


# --- rejoin codes ----------------------------------------------------------
#
# `on_reconnect` above covers coming back with the id still in hand. These
# cover getting the id back after the browser holding it is gone.

def test_every_seat_gets_its_own_rejoin_code(engine):
    match, members, leaders = full_match(engine)
    everyone = list(match.players.values())
    codes = [player.rejoin_code for player in everyone]
    assert all(codes), "a seat with no code cannot be recovered"
    assert len(set(codes)) == len(codes), "a shared code is an ambiguous seat"
    assert all(len(code) == config.REJOIN_CODE_LENGTH for code in codes)
    assert all(set(code) <= set(config.REJOIN_CODE_ALPHABET) for code in codes)


def test_rejoin_returns_the_same_seat_mid_match(engine):
    match, members, _ = full_match(engine)
    player = members["alpha"][1]
    engine.on_disconnect(match, player.id)
    got = engine.rejoin(match, player.rejoin_code)
    assert got is player  # the seat itself, not a copy and not a new one
    assert got.id == player.id


def test_rejoin_survives_how_a_player_actually_types_it(engine):
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    typed = " " + player.rejoin_code.lower()[:3] + "-" + player.rejoin_code[3:] + " "
    assert engine.rejoin(match, typed) is player


def test_rejoin_refuses_a_code_no_seat_holds(engine):
    match, _, _ = full_match(engine)
    with pytest.raises(ValueError):
        engine.rejoin(match, "ZZZZZZ")
    with pytest.raises(ValueError):
        engine.rejoin(match, "   ")


def test_rejoin_changes_nothing_by_itself(engine):
    """It hands back an identity. `on_reconnect` owns every state change."""
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    team = match.teams["alpha"]
    engine.on_disconnect(match, player.id)
    before = (team.roster_size, list(team.player_ids), player.status,
              player.connected, player.assigned_game, player.role)
    engine.rejoin(match, player.rejoin_code)
    assert (team.roster_size, list(team.player_ids), player.status,
            player.connected, player.assigned_game, player.role) == before


def test_a_rejoined_player_still_counts_toward_the_advance(engine):
    """The bug this exists for: a lost seat used to freeze its team forever,
    because roster_size is frozen at the start and nothing could vacate it."""
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    engine.on_disconnect(match, player.id)
    same = engine.rejoin(match, player.rejoin_code)
    engine.on_reconnect(match, same.id)
    solve(engine, match, same)
    make_all_cleared_except(engine, match, members["alpha"], same)
    assert match.teams["alpha"].level == 2


def test_a_grandmaster_rejoins_without_claiming_the_seat_again(engine):
    """Mid-match claim_leader is disabled by design (GAME_DESIGN.md). Coming
    back on the original id is how a lost Grandmaster returns instead."""
    match, _, leaders = full_match(engine)
    leader = leaders["alpha"]
    engine.on_disconnect(match, leader.id)
    got = engine.rejoin(match, leader.rejoin_code)
    engine.on_reconnect(match, got.id)
    assert got is leader and leader.is_leader is True
    assert match.teams["alpha"].leader_id == leader.id


def test_rejoin_works_while_someone_is_still_apparently_connected(engine):
    """A half-open socket the server has not noticed must not lock the real
    owner out; the WS layer supersedes the stale one when the new one opens."""
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    assert player.connected is True
    assert engine.rejoin(match, player.rejoin_code) is player


# --- per-player coin ledger (Grandmaster leaderboard) ----------------------

def test_a_clear_credits_the_player_who_cleared(engine):
    """Base pay is once per level, so the ledger has to move with the purse and
    not with every submission."""
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    team = match.teams["alpha"]
    assert player.coins_earned == 0
    solve(engine, match, player)
    assert player.coins_earned == config.CURRENCY_PER_CLEAR
    assert team.currency == config.CURRENCY_PER_CLEAR
    # A failed bonus puts them back on a main board. Clearing it again is still
    # the same level, so it pays nothing and credits nothing.
    engine.choose_bonus(match, player.id, now=NOW)
    solve_bonus(engine, match, player, answer="nope")
    solve(engine, match, player)
    assert player.coins_earned == config.CURRENCY_PER_CLEAR
    assert team.currency == config.CURRENCY_PER_CLEAR


def test_a_bonus_credits_the_gambler_and_a_failure_takes_it_back(engine):
    """The point of the net figure: a player who gambles and loses is not shown
    as having brought in coins the team no longer has."""
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    team = match.teams["alpha"]
    solve(engine, match, player)
    engine.choose_bonus(match, player.id, now=NOW)
    solve_bonus(engine, match, player)
    assert player.coins_earned == config.CURRENCY_PER_CLEAR + config.CURRENCY_BONUS_FIRST

    engine.choose_bonus(match, player.id, now=NOW)
    solve_bonus(engine, match, player, answer="nope")
    # Exactly what left the purse came off the ledger: the clear pay survives.
    assert team.currency == config.CURRENCY_PER_CLEAR
    assert player.coins_earned == config.CURRENCY_PER_CLEAR


def test_insurance_leaves_the_ledger_alone_because_nothing_was_forfeited(engine):
    """Insurance stops the coins leaving the purse, so there is nothing to take
    off the player either."""
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    team = match.teams["alpha"]
    solve(engine, match, player)
    engine.choose_bonus(match, player.id, now=NOW)
    solve_bonus(engine, match, player)
    banked = player.coins_earned
    team.insurance_active = True
    engine.choose_bonus(match, player.id, now=NOW)
    solve_bonus(engine, match, player, answer="nope")
    assert player.coins_earned == banked
    assert team.currency == banked


def test_a_forfeit_never_charges_more_than_the_purse_lost(engine):
    """The team clamps at zero, so a forfeit against a spent purse removes less
    than it owes. The player is charged the same, or the two disagree."""
    match, members, _ = full_match(engine)
    player = members["alpha"][0]
    team = match.teams["alpha"]
    solve(engine, match, player)
    engine.choose_bonus(match, player.id, now=NOW)
    solve_bonus(engine, match, player)
    earned = player.coins_earned
    team.currency = 1  # the Grandmaster spent almost all of it
    engine.choose_bonus(match, player.id, now=NOW)
    solve_bonus(engine, match, player, answer="nope")
    assert team.currency == 0
    assert player.coins_earned == earned - 1


def test_the_ledger_is_hidden_while_the_grandmaster_is_silenced(engine):
    """Earnings track clears, so leaving them visible would say who had cleared
    and undo the blinding."""
    match, members, leaders = full_match(engine)
    player = members["alpha"][0]
    solve(engine, match, player)
    assert player.coins_earned > 0
    # The view masks against real time, not the test clock, so this deadline
    # has to be in the real future to read as silenced at all.
    match.teams["alpha"].silenced_until = (
        datetime.now(timezone.utc) + timedelta(hours=1)
    ).isoformat()
    roster = match.public(leaders["alpha"].id)["teams"]["alpha"]["players"]
    playing = [view for view in roster if not view["is_leader"]]
    assert playing and all(view["coins_earned"] is None for view in playing)
