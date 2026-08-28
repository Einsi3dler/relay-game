"""The server-authoritative board deadline (`payload["time_limit_seconds"]`).

docs/GAMES_SPEC.md §0.4 has always said the level puzzle has no hard limit and
that a hard one is the stretch hardening. This is that limit, and it is opt-in:
the engine reads one key off the payload and knows nothing else about the game.

What it is *not*: a fix for BOMB DEFUSE's per-bank fuse. A bank arming is a
client-side event, so a per-bank server deadline would need the client to
report it — client-claimed time, which this repo refuses to trust. What the
server can own with no new channel is the whole board's budget. On every main
board of the bomb that budget is the fuse exactly, because levels 1-10 are
single-bank; on the two-bank bonus tiers it is the sum, so a player could carry
unspent time from one bank to the next and never notice the difference.

The risk here is not the timer. It is the interaction surface — freeze,
scramble, the bonus deadline, reconnect and level advance — which is where most
of this file goes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend import config
from backend.engine import EngineResult, RelayEngine, _fuse_scope
from backend.games.base import PuzzleInstance
from backend.registry import GameRegistry

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
MAIN_OK = "main-ok"
LIMIT = 60          # the capped game's board budget
UNCAPPED = "loose"  # a game that never asks for one
CAPPED = "tight"
DARK = "dark"       # capped, and routes its deadline to the Grandmaster


class Game:
    """A stand-in that opts in or out of a board deadline."""

    def __init__(
        self, game_id: str, limit: int | None, blackout: bool = False
    ) -> None:
        self.id = game_id
        self.name = game_id.title()
        self.limit = limit
        self.blackout = blackout

    def _payload(self) -> dict:
        if self.limit is None:
            return {}
        return {"time_limit_seconds": self.limit, "blackout": self.blackout}

    def generate_main(self, seed: int, level: int = 1) -> PuzzleInstance:
        return PuzzleInstance(
            game_id=self.id, kind="main", prompt=f"main L{level} {seed}",
            answer=MAIN_OK, payload=self._payload(),
        )

    def generate_holding(self, seed: int) -> PuzzleInstance:
        return PuzzleInstance(
            game_id=self.id, kind="holding", prompt=f"hold {seed}",
            answer="hold-ok", payload=self._payload(),
        )

    def check(self, puzzle: PuzzleInstance, answer: str) -> bool:
        return answer == puzzle.answer

    def reset(self) -> None:
        return None


@pytest.fixture
def engine(monkeypatch) -> RelayEngine:
    monkeypatch.setattr(config, "LEVEL_COUNT", 3)
    return RelayEngine(GameRegistry(modules=[
        Game(CAPPED, LIMIT), Game(UNCAPPED, None),
        Game(DARK, LIMIT, blackout=True),
    ]))


def match_of(engine: RelayEngine, per_team: int = 2):
    """Two teams; seat 0 plays the capped game, seat 1 the uncapped one."""
    match = engine.create_match()
    match.min_players = per_team
    members: dict[str, list] = {}
    leaders: dict[str, object] = {}
    for team_id in ("alpha", "bravo"):
        leader, _ = engine.join_match(match, f"{team_id}-lead", team_id, now=NOW)
        assert engine.claim_leader(match, leader.id).ok
        leaders[team_id] = leader
        members[team_id] = []
        for seat in range(per_team):
            player, _ = engine.join_match(
                match, f"{team_id[0]}{seat}", team_id, now=NOW
            )
            members[team_id].append(player)
            assert engine.assign_role(match, leader.id, player.id, "generalist").ok
            assert engine.assign_game(
                match, leader.id, player.id,
                [CAPPED, UNCAPPED, DARK][seat % 3],
            ).ok
    assert engine.host_start(match, match.host_player_id, now=NOW).match_started
    return match, members, leaders


def solve(engine, match, player, answer=MAIN_OK, now=NOW):
    return engine.submit_answer(
        match, player.id, player.current_main.id, answer, now=now
    )


def deadline_of(player) -> datetime:
    return datetime.fromisoformat(player.puzzle_deadline)


def fuse_requests(result):
    return [r for r in result.schedule if r.kind == "puzzle"]


# --- the deadline itself --------------------------------------------------


def test_a_capped_game_arms_a_deadline_and_an_uncapped_one_does_not(engine):
    match, members, _ = match_of(engine)
    capped, loose = members["alpha"]
    assert deadline_of(capped) == NOW + timedelta(seconds=LIMIT)
    assert loose.puzzle_deadline is None


def test_the_key_is_the_whole_contract(engine):
    """The engine reads one payload key and knows nothing else about the game,
    so a game opts in by emitting it and out by not."""
    match, members, _ = match_of(engine)
    capped = members["alpha"][0]
    assert capped.current_main.payload["time_limit_seconds"] == LIMIT


def test_the_deadline_the_player_is_told_is_not_the_one_that_fires(engine):
    """The published deadline is the honest one — it is what the client draws
    and what the bomb's face counts. The timer fires PUZZLE_GRACE_SECONDS
    later, so an answer already in flight when the clock runs out still lands.
    """
    match, members, _ = match_of(engine)
    capped = members["alpha"][0]
    # Re-serving is the observable route to a scheduled request.
    served = engine.on_reconnect(match, capped.id, now=NOW)
    request = fuse_requests(served)[0]
    assert request.scope_id == _fuse_scope(capped.id)
    told = deadline_of(capped)
    fires = datetime.fromisoformat(request.deadline)
    assert told == NOW + timedelta(seconds=LIMIT)
    assert fires == told + timedelta(seconds=config.PUZZLE_GRACE_SECONDS)


def test_a_fire_that_is_not_due_yet_is_refused(engine):
    """A timer already past its sleep cannot be cancelled, so a board re-served
    in that window would otherwise be killed by the previous board's clock.
    The grace makes the guard free: the timer fires a full grace *after* the
    deadline it is checked against."""
    match, members, _ = match_of(engine)
    capped = members["alpha"][0]
    board = capped.current_main.id
    almost = NOW + timedelta(seconds=LIMIT - 1)
    assert engine.on_puzzle_expired(match, capped.id, now=almost).changed is False
    assert capped.current_main.id == board
    # ...and the real fire, a grace later, still lands.
    due = NOW + timedelta(seconds=LIMIT + config.PUZZLE_GRACE_SECONDS)
    assert engine.on_puzzle_expired(match, capped.id, now=due).changed is True
    assert capped.current_main.id != board


def test_the_deadline_rides_its_own_scope(engine):
    """`fuse:<id>`, never the bare player id — a board deadline that displaced
    the wait timer would silently cost a cleared player their hold."""
    match, members, _ = match_of(engine)
    capped = members["alpha"][0]
    served = engine.on_reconnect(match, capped.id, now=NOW)
    assert fuse_requests(served)[0].scope_id == f"fuse:{capped.id}"
    assert capped.id not in {r.scope_id for r in fuse_requests(served)}


def test_a_lapsed_deadline_serves_a_fresh_board(engine):
    """Exactly what a wrong answer does, and nothing more: losing the board is
    the whole penalty."""
    match, members, _ = match_of(engine)
    capped = members["alpha"][0]
    before = capped.current_main.id
    team = match.teams["alpha"]
    currency, level = team.currency, team.level

    later = NOW + timedelta(seconds=LIMIT + config.PUZZLE_GRACE_SECONDS)
    result = engine.on_puzzle_expired(match, capped.id, now=later)

    assert result.changed is True
    assert capped.status == "solving"
    assert capped.current_main.id != before
    assert capped.attempt == 2
    assert deadline_of(capped) == later + timedelta(seconds=LIMIT)
    assert team.currency == currency and team.level == level
    assert any("ran out of time" in event.message for event in result.events)


def test_a_lapsed_deadline_on_a_player_who_moved_on_is_a_no_op(engine):
    """The scope is replaced on every serve, so a stale fire is already
    unlikely — but a fire that races a clear must not take the board they were
    given for clearing it."""
    match, members, _ = match_of(engine)
    capped = members["alpha"][0]
    assert solve(engine, match, capped).correct is True
    held = capped.timer_deadline

    result = engine.on_puzzle_expired(match, capped.id, now=NOW)
    assert result.changed is False
    assert capped.status == "cleared"
    assert capped.timer_deadline == held


# --- everything that ends a board cancels it ------------------------------


def test_clearing_the_board_cancels_the_deadline(engine):
    match, members, _ = match_of(engine)
    capped = members["alpha"][0]
    result = solve(engine, match, capped)
    assert capped.puzzle_deadline is None
    assert _fuse_scope(capped.id) in result.cancel


def test_a_wrong_answer_restarts_it(engine):
    match, members, _ = match_of(engine)
    capped = members["alpha"][0]
    later = NOW + timedelta(seconds=20)
    result = solve(engine, match, capped, answer="nope", now=later)
    assert result.correct is False
    assert deadline_of(capped) == later + timedelta(seconds=LIMIT)
    assert fuse_requests(result)[0].scope_id == _fuse_scope(capped.id)


def test_a_level_advance_cancels_every_members_deadline_then_rearms_it(engine):
    match, members, _ = match_of(engine)
    capped, loose = members["alpha"]
    assert solve(engine, match, loose).correct is True
    result = solve(engine, match, capped)
    assert result.advanced_team_ids == ["alpha"]
    for player in (capped, loose):
        assert _fuse_scope(player.id) in result.cancel
    # The capped seat's new board carries a new deadline; the uncapped seat's
    # stays cancelled, and stays cancelled without a schedule to undo it.
    assert deadline_of(capped) == NOW + timedelta(seconds=LIMIT)
    assert loose.puzzle_deadline is None
    armed = {r.scope_id for r in fuse_requests(result)}
    assert armed == {_fuse_scope(capped.id)}


def test_winning_the_match_stops_every_board(engine):
    match, members, _ = match_of(engine)
    for _ in range(match.config_snapshot["level_count"]):
        for player in members["alpha"]:
            if player.status == "solving":
                solve(engine, match, player)
    assert match.status == "finished"
    for player in members["alpha"]:
        assert player.puzzle_deadline is None


def test_the_grandmaster_handoff_moves_the_deadline_with_the_seat(engine):
    """The old Grandmaster takes over the board and its deadline; the new one
    stops playing, so theirs goes."""
    match, members, leaders = match_of(engine)
    leader, capped = leaders["alpha"], members["alpha"][0]
    result = engine.give_leader(match, leader.id, capped.id, now=NOW)
    assert result.ok
    assert capped.is_leader and capped.puzzle_deadline is None
    assert _fuse_scope(capped.id) in result.cancel
    assert leader.assigned_game == CAPPED
    assert deadline_of(leader) == NOW + timedelta(seconds=LIMIT)


# --- the interaction surface ----------------------------------------------


def only_solver(engine, match, members) -> object:
    """Clear every alpha seat but the capped one, so an attack can only pick it."""
    for player in members["alpha"][1:]:
        assert solve(engine, match, player).correct is True
    return members["alpha"][0]


def test_scramble_takes_your_work_but_not_your_clock(engine):
    """The attack that used to *help* its victim. A fresh board with a fresh
    deadline hands a player 80 seconds back when they were 80 seconds into a
    90-second board — the enemy paying 2 coins to rescue them. The new board
    inherits the clock the old one was running against, which is exactly what
    Scramble costs on an untimed game: your work, and nothing else."""
    match, members, leaders = match_of(engine)
    victim = only_solver(engine, match, members)
    match.teams["bravo"].currency = 99
    before_board = victim.current_main.id
    before_deadline = victim.puzzle_deadline

    late = NOW + timedelta(seconds=LIMIT - 10)      # ten seconds from the end
    result = engine.buy_perk(match, leaders["bravo"].id, "scramble", now=late)
    assert result.ok
    assert victim.current_main.id != before_board   # the work is gone...
    assert victim.puzzle_deadline == before_deadline  # ...the clock is not
    # The backstop still points at the same instant.
    request = fuse_requests(result)[0]
    assert request.scope_id == _fuse_scope(victim.id)
    assert datetime.fromisoformat(request.deadline) == \
        deadline_of(victim) + timedelta(seconds=config.PUZZLE_GRACE_SECONDS)


def test_a_freeze_gives_back_the_time_it_locks_away(engine):
    """The freeze overlay covers the whole screen, so a frozen player cannot
    touch their board at all. On a timed game that made a 3-coin annoyance into
    a board-killer. The deadline now moves with the freeze: it costs the player
    their input for those seconds, not the board."""
    match, members, leaders = match_of(engine)
    victim = only_solver(engine, match, members)
    match.teams["bravo"].currency = 99
    before = deadline_of(victim)

    later = NOW + timedelta(seconds=10)
    result = engine.buy_perk(match, leaders["bravo"].id, "freeze", now=later)
    assert result.ok
    seconds = config.PERKS["freeze"]["seconds"]
    assert victim.frozen_until == (later + timedelta(seconds=seconds)).isoformat()
    assert deadline_of(victim) == before + timedelta(seconds=seconds)
    assert fuse_requests(result)[0].scope_id == _fuse_scope(victim.id)


def test_stacking_two_freezes_never_pays_out_more_than_it_locks_away(engine):
    """`_extend_deadline` stacks a second freeze *forward* rather than
    restarting it, so the board deadline has to move by however much the frozen
    window actually grew — not by the perk's full duration each time, which
    would turn a double freeze into free time."""
    match, members, leaders = match_of(engine)
    victim = only_solver(engine, match, members)
    match.teams["bravo"].currency = 99
    seconds = config.PERKS["freeze"]["seconds"]
    before = deadline_of(victim)

    assert engine.buy_perk(match, leaders["bravo"].id, "freeze", now=NOW).ok
    # Half way through the first one: the second only adds the overhang.
    midway = NOW + timedelta(seconds=seconds / 2)
    assert engine.buy_perk(match, leaders["bravo"].id, "freeze", now=midway).ok

    frozen_for = (
        datetime.fromisoformat(victim.frozen_until) - NOW
    ).total_seconds()
    gained = (deadline_of(victim) - before).total_seconds()
    assert gained == frozen_for == seconds * 1.5


def test_an_untimed_board_is_untouched_by_either(engine):
    """Both rules key off the board's own deadline, so a game that asks for
    none plays exactly as it did before any of this landed."""
    match, members, leaders = match_of(engine)
    loose = members["alpha"][1]
    match.teams["bravo"].currency = 99
    for player in match.players.values():
        if player.team_id == "alpha" and player is not loose and not player.is_leader:
            assert solve(engine, match, player).correct is True
    board = loose.current_main.id

    assert engine.buy_perk(match, leaders["bravo"].id, "freeze", now=NOW).ok
    assert loose.frozen_until is not None
    assert loose.puzzle_deadline is None
    assert engine.buy_perk(match, leaders["bravo"].id, "scramble", now=NOW).ok
    assert loose.current_main.id != board       # still a real setback
    assert loose.puzzle_deadline is None


def test_a_bonus_board_runs_against_the_wait_deadline_not_a_board_one(engine):
    """The bonus already has a cap — the running wait deadline — and giving it
    a second one would mean two clocks on one bar."""
    match, members, _ = match_of(engine)
    capped = members["alpha"][0]
    assert solve(engine, match, capped).correct is True
    assert engine.choose_bonus(match, capped.id, now=NOW).ok
    assert capped.status == "bonus"
    assert capped.puzzle_deadline is None
    assert capped.timer_deadline is not None


def test_a_failed_bonus_comes_back_to_a_board_with_a_deadline(engine):
    match, members, _ = match_of(engine)
    capped = members["alpha"][0]
    assert solve(engine, match, capped).correct is True
    assert engine.choose_bonus(match, capped.id, now=NOW).ok
    later = NOW + timedelta(seconds=45)
    result = engine.submit_answer(
        match, capped.id, capped.current_bonus.id, "nope", now=later
    )
    assert result.correct is False
    assert capped.status == "solving"
    assert deadline_of(capped) == later + timedelta(seconds=LIMIT)


def test_a_lapsed_wait_comes_back_to_a_board_with_a_deadline(engine):
    match, members, _ = match_of(engine)
    capped = members["alpha"][0]
    assert solve(engine, match, capped).correct is True
    later = NOW + timedelta(seconds=config.WAIT_SECONDS)
    result = engine.on_wait_expired(match, capped.id, now=later)
    assert capped.status == "solving"
    assert deadline_of(capped) == later + timedelta(seconds=LIMIT)
    assert fuse_requests(result)[0].scope_id == _fuse_scope(capped.id)


def test_reconnect_restarts_the_deadline_with_the_board(engine):
    """Reconnect already re-serves, so a board nobody could see must not have
    been burning down while they were gone."""
    match, members, _ = match_of(engine)
    capped = members["alpha"][0]
    engine.on_disconnect(match, capped.id)
    later = NOW + timedelta(seconds=40)
    result = engine.on_reconnect(match, capped.id, now=later)
    assert deadline_of(capped) == later + timedelta(seconds=LIMIT)
    assert fuse_requests(result)[0].scope_id == _fuse_scope(capped.id)


def test_a_wait_timer_and_a_board_deadline_never_collide(engine):
    """Different scopes, so a cleared player's hold and a solving player's
    board deadline can be in flight at the same instant."""
    match, members, _ = match_of(engine)
    capped, loose = members["alpha"]
    cleared = solve(engine, match, loose)
    wait_scopes = {r.scope_id for r in cleared.schedule if r.kind == "wait"}
    assert wait_scopes == {loose.id}
    assert loose.id != _fuse_scope(capped.id)
    assert capped.puzzle_deadline is not None and loose.timer_deadline is not None


# --- what the player is sent ----------------------------------------------


def test_the_deadline_reaches_the_player_twice_from_one_source(engine):
    """Top level for the shell's timer bar, and inside the puzzle for a
    renderer that draws a clock of its own and takes no other argument."""
    match, members, _ = match_of(engine)
    capped = members["alpha"][0]
    me = match.public(capped.id)["me"]
    assert me["puzzle_deadline"] == capped.puzzle_deadline
    assert me["current_puzzle"]["deadline"] == capped.puzzle_deadline


def test_an_uncapped_game_sends_no_deadline_at_all(engine):
    match, members, _ = match_of(engine)
    loose = members["alpha"][1]
    me = match.public(loose.id)["me"]
    assert me["puzzle_deadline"] is None
    assert "deadline" not in me["current_puzzle"]


def test_the_grace_is_frozen_at_start_like_every_other_timer(engine):
    match, _, _ = match_of(engine)
    assert match.config_snapshot["puzzle_grace_seconds"] == \
        config.PUZZLE_GRACE_SECONDS


# --- blackout: the same deadline, routed to one seat ----------------------
#
# A visibility rule, not a sync channel. It is only honest because the deadline
# is real server state: there is nothing to keep in step, because there is only
# ever one copy of it.


def dark_match(engine: RelayEngine):
    """Three seats on alpha: one capped game, one uncapped, one blacked out."""
    match, members, leaders = match_of(engine, per_team=3)
    leader, player = leaders["alpha"], members["alpha"][2]
    assert player.assigned_game == DARK
    return match, player, leader


def roster_entry(match, leader, player) -> dict:
    team = match.public(leader.id)["teams"][leader.team_id]
    return next(view for view in team["players"] if view["id"] == player.id)


def test_a_blackout_board_withholds_the_deadline_from_the_player(engine):
    match, player, leader = dark_match(engine)
    me = match.public(player.id)["me"]
    assert me["puzzle_deadline"] is None
    assert "deadline" not in me["current_puzzle"]
    # The engine still holds it — this is a view rule, not a missing deadline.
    assert player.puzzle_deadline is not None


def test_a_blackout_board_sends_the_deadline_to_the_grandmaster(engine):
    match, player, leader = dark_match(engine)
    assert roster_entry(match, leader, player)["board_deadline"] == \
        player.puzzle_deadline


def test_exactly_one_seat_ever_holds_the_deadline(engine):
    """The whole invariant, both ways round."""
    match, dark, leader = dark_match(engine)
    lit = None
    for member in match.teams["alpha"].player_ids:
        candidate = match.players[member]
        if candidate.assigned_game == CAPPED:
            lit = candidate
    assert lit is not None

    for player, seat in ((dark, "leader"), (lit, "player")):
        mine = match.public(player.id)["me"]["puzzle_deadline"]
        theirs = roster_entry(match, leader, player)["board_deadline"]
        assert [mine, theirs].count(None) == 1, (player.assigned_game, seat)
        held = mine or theirs
        assert held == player.puzzle_deadline


def test_an_uncapped_board_gives_the_deadline_to_neither(engine):
    match, _, leader = dark_match(engine)
    loose = next(
        match.players[pid] for pid in match.teams["alpha"].player_ids
        if match.players[pid].assigned_game == UNCAPPED
    )
    assert match.public(loose.id)["me"]["puzzle_deadline"] is None
    assert roster_entry(match, leader, loose)["board_deadline"] is None


def test_the_deadline_comes_back_to_the_player_when_the_board_changes(engine):
    """Blackout follows the board, not the player: a fresh board from an
    ordinary game hands the clock straight back."""
    match, player, leader = dark_match(engine)
    player.assigned_game = CAPPED
    engine._serve_main(match, player, EngineResult(), NOW)
    assert match.public(player.id)["me"]["puzzle_deadline"] is not None
    assert roster_entry(match, leader, player)["board_deadline"] is None


def test_silence_takes_the_grandmasters_clock_too(engine):
    """A silenced Grandmaster loses the roster, the feed and the manual. If
    they kept the one number their Defuser cannot see, the perk would be no
    real blackout at all."""
    match, player, leader = dark_match(engine)
    match.teams["bravo"].currency = 99
    assert engine.buy_perk(
        match, match.teams["bravo"].leader_id, "silence", now=NOW
    ).ok
    assert roster_entry(match, leader, player)["board_deadline"] is None
    # ...and it is still withheld from the Defuser, so for those seconds the
    # clock is in nobody's hands. That is the perk landing, not a bug.
    assert match.public(player.id)["me"]["puzzle_deadline"] is None


# --- the bomb, which is the game that asked for this ----------------------


def test_the_bomb_budgets_the_sum_of_its_bank_fuses():
    from backend.games.game11_bomb_defuse import BombDefuseGame

    game = BombDefuseGame()
    for level in range(1, 14):
        payload = game.generate_main(11, level).payload
        banks = payload["banks"]
        assert payload["time_limit_seconds"] == \
            sum(bank["fuse_seconds"] for bank in banks)
        if len(banks) == 1:
            # Levels 1-10: the budget *is* the fuse, exactly. This is the case
            # the server enforces precisely rather than approximately.
            assert payload["time_limit_seconds"] == banks[0]["fuse_seconds"]
