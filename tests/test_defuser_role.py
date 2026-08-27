"""The Defuser: the one role every team must field, and the only role whose
game the Grandmaster does not choose.

The sibling of `test_duels.py`. Where the Duelist is *mirrored* (one team
fielding a champion forces the other to answer), the Defuser is *required* —
both teams name one or nobody starts — because the bomb is the game no team
opts out of (docs/GAME_DESIGN.md §2c).

These run against the **real** registry: the requirement only bites when
`bomb_defuse` is actually registered, which is exactly the behaviour under test.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend import config
from backend.engine import RelayEngine
from backend.games.base import PuzzleInstance
from backend.registry import GameRegistry

NOW = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)

# Games for the seats that are not defusing. Generalist takes anything.
FILLER = ["rewire", "sweep", "mirror_run", "decant"]


@pytest.fixture
def engine() -> RelayEngine:
    return RelayEngine(GameRegistry())


def lobby(engine: RelayEngine, per_team: int = 4):
    """A full lobby with every seat roled and gamed, but no Defuser yet."""
    match = engine.create_match()
    match.min_players = per_team
    members: dict[str, list] = {"alpha": [], "bravo": []}
    leaders: dict[str, object] = {}
    for team_id in ("alpha", "bravo"):
        leader, _ = engine.join_match(match, f"{team_id}-lead", team_id, now=NOW)
        assert engine.claim_leader(match, leader.id).ok
        leaders[team_id] = leader
        for seat in range(per_team):
            player, _ = engine.join_match(
                match, f"{team_id[0]}{seat}", team_id, now=NOW
            )
            members[team_id].append(player)
            assert engine.assign_role(match, leader.id, player.id, "generalist").ok
            assert engine.assign_game(
                match, leader.id, player.id, FILLER[seat % len(FILLER)]
            ).ok
    return match, members, leaders


def make_defuser(engine, match, leaders, members, team_id: str, seat: int = 3):
    return engine.assign_role(
        match, leaders[team_id].id, members[team_id][seat].id, "defuser"
    )


# --- the role names its own game ----------------------------------------


def test_the_role_assigns_the_bomb_itself(engine):
    match, members, leaders = lobby(engine)
    target = members["alpha"][3]
    assert make_defuser(engine, match, leaders, members, "alpha").ok
    assert target.role == "defuser"
    assert target.assigned_game == "bomb_defuse"   # no game pick needed


def test_the_grandmaster_cannot_choose_a_defusers_game(engine):
    match, members, leaders = lobby(engine)
    make_defuser(engine, match, leaders, members, "alpha")
    result = engine.assign_game(
        match, leaders["alpha"].id, members["alpha"][3].id, "sweep"
    )
    assert not result.ok
    assert result.error == "the Defuser always plays Bomb Defuse"
    assert members["alpha"][3].assigned_game == "bomb_defuse"
    # ...not even the bomb itself: the role owns the assignment.
    assert not engine.assign_game(
        match, leaders["alpha"].id, members["alpha"][3].id, "bomb_defuse"
    ).ok


def test_a_team_can_only_hold_one_defuser(engine):
    match, members, leaders = lobby(engine)
    assert make_defuser(engine, match, leaders, members, "alpha", seat=3).ok
    result = make_defuser(engine, match, leaders, members, "alpha", seat=2)
    assert not result.ok
    assert result.error == "a3 is already the Defuser"
    # The refusal is total: the second player keeps the role they had.
    assert members["alpha"][2].role == "generalist"
    assert members["alpha"][2].assigned_game == "mirror_run"


def test_moving_off_the_role_drops_the_bomb(engine):
    match, members, leaders = lobby(engine)
    make_defuser(engine, match, leaders, members, "alpha")
    target = members["alpha"][3]
    assert engine.assign_role(match, leaders["alpha"].id, target.id, "logician").ok
    # bomb_defuse is not a Logician game, so the assignment cannot survive.
    assert target.assigned_game is None
    # ...and the seat is free for a new Defuser.
    assert make_defuser(engine, match, leaders, members, "alpha", seat=2).ok


def test_the_technocrat_no_longer_holds_the_bomb(engine):
    match, members, leaders = lobby(engine)
    target = members["alpha"][0]
    assert engine.assign_role(match, leaders["alpha"].id, target.id, "technocrat").ok
    result = engine.assign_game(match, leaders["alpha"].id, target.id, "bomb_defuse")
    assert not result.ok
    assert "role can't play bomb_defuse" in result.error
    assert config.ROLES["technocrat"]["games"] == ["rewire", "lane_shift"]


def test_the_library_files_the_bomb_under_the_defuser(engine):
    entry = next(
        item for item in engine.registry.library() if item["id"] == "bomb_defuse"
    )
    assert entry["role"] == "defuser"


# --- the start gate ------------------------------------------------------


def test_a_match_cannot_start_without_a_defuser_on_every_team(engine):
    match, members, leaders = lobby(engine)
    assert engine.start_blocker(match) == "team Alpha needs a Defuser"
    make_defuser(engine, match, leaders, members, "alpha")
    # One team naming a Defuser does not excuse the other — unlike the Duelist,
    # this is not a mirror rule, it is a floor.
    assert engine.start_blocker(match) == "team Bravo needs a Defuser"
    make_defuser(engine, match, leaders, members, "bravo")
    assert engine.start_blocker(match) is None
    assert engine.host_start(match, match.host_player_id, now=NOW).match_started


def test_the_gate_lifts_when_the_bomb_is_not_registered():
    """The engine validates against the library it was handed.

    A trimmed deployment (or a test on a fake library) has no `bomb_defuse` to
    assign, so requiring one would deadlock every lobby. The rule is only a
    gate when the game is really there.
    """
    class FakeGame:
        def __init__(self, game_id: str) -> None:
            self.id, self.name = game_id, game_id.title()

        def generate_main(self, seed: int, level: int = 1) -> PuzzleInstance:
            return PuzzleInstance(game_id=self.id, kind="main", prompt="?", answer="a")

        def generate_holding(self, seed: int) -> PuzzleInstance:
            return PuzzleInstance(game_id=self.id, kind="holding", prompt="?", answer="a")

        def check(self, puzzle: PuzzleInstance, answer: str) -> bool:
            return True

        def reset(self) -> None:
            return None

    bombless = RelayEngine(GameRegistry(modules=[FakeGame(g) for g in FILLER]))
    assert bombless._required_roles() == []
    match, _, _ = lobby(bombless)
    assert bombless.start_blocker(match) is None
    # ...while the real library does require one.
    assert RelayEngine(GameRegistry())._required_roles() == ["defuser"]


def test_the_squeeze_at_small_table_sizes_explains_itself(engine):
    """One playing member cannot be both the Duelist and the Defuser."""
    match, members, leaders = lobby(engine, per_team=1)
    for team_id in ("alpha", "bravo"):
        assert engine.assign_role(
            match, leaders[team_id].id, members[team_id][0].id, "duelist"
        ).ok
    blocker = engine.start_blocker(match)
    assert blocker == (
        "team Alpha needs a Defuser, but its only player is a Duelist — "
        "drop the Duelist or add a player"
    )
    # Dropping the champion resolves it on both teams.
    for team_id in ("alpha", "bravo"):
        assert engine.assign_role(
            match, leaders[team_id].id, members[team_id][0].id, "defuser"
        ).ok
    assert engine.start_blocker(match) is None


# --- and then it is an ordinary seat -------------------------------------


def test_the_defuser_solves_and_goes_green_like_anyone_else(engine):
    """Unlike the Duelist, nothing about the level loop changes for them."""
    match, members, leaders = lobby(engine)
    for team_id in ("alpha", "bravo"):
        make_defuser(engine, match, leaders, members, team_id)
    assert engine.host_start(match, match.host_player_id, now=NOW).match_started

    defuser = members["alpha"][3]
    assert defuser.status == "solving"           # not "duelling"
    puzzle = defuser.current_main
    assert puzzle.game_id == "bomb_defuse"
    result = engine.submit_answer(
        match, defuser.id, puzzle.id, puzzle.answer, now=NOW
    )
    assert result.correct is True
    assert defuser.status == "cleared"
    assert defuser.timer_kind == "wait"          # an ordinary wait timer
    assert defuser.choice_pending is True        # ...and an ordinary choice


def test_a_wrong_defusal_serves_a_fresh_bomb(engine):
    match, members, leaders = lobby(engine)
    for team_id in ("alpha", "bravo"):
        make_defuser(engine, match, leaders, members, team_id)
    engine.host_start(match, match.host_player_id, now=NOW)

    defuser = members["alpha"][3]
    first = defuser.current_main
    result = engine.submit_answer(
        match, defuser.id, first.id, '{"v":1,"failed":"give-up"}', now=NOW
    )
    assert result.correct is False
    assert defuser.status == "solving"
    assert defuser.current_main.id != first.id   # bomb.md §20's new bomb
