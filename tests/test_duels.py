"""Engine tests for the Duelist role and the cross-team duel loop.

The duel is the first mechanic where one team's action changes another team's
state directly, so these tests care most about the boundaries: who may field a
Duelist, when a duel may start, what a loss actually costs, and that neither
Duelist can learn the other's move while the round is open.

Reuses the scaffolding in tests/test_engine.py (FakeGame, NOW, GAMES).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from backend import config
from backend.engine import DUEL_SCOPE, RelayEngine, _team_scope
from backend.games.duel1_rps import RockPaperScissorsDuel
from backend.models import Match, green
from backend.registry import GameRegistry
from tests.test_engine import GAMES, LEVELS, MAIN_OK, NOW, FakeGame, solve

RPS = RockPaperScissorsDuel()


@pytest.fixture
def engine(monkeypatch) -> RelayEngine:
    monkeypatch.setattr(config, "LEVEL_COUNT", LEVELS)
    registry = GameRegistry(
        modules=[FakeGame(game_id) for game_id in GAMES],
        duels=[RockPaperScissorsDuel()],
    )
    return RelayEngine(registry)


def duel_match(
    engine: RelayEngine, duelist_teams: tuple[str, ...] = ("alpha", "bravo")
) -> tuple[Match, dict[str, list], dict]:
    """A full match where the named teams each field one Duelist (seat 0)."""
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
            if i == 0 and team_id in duelist_teams:
                assert engine.assign_role(
                    match, leader.id, player.id, "duelist"
                ).ok
                continue
            assert engine.assign_role(match, leader.id, player.id, "generalist").ok
            assert engine.assign_game(match, leader.id, player.id, GAMES[i]).ok
    return match, members, leaders


def start(engine, match):
    result = engine.host_start(match, match.host_player_id, now=NOW)
    assert result.match_started, result.error
    return result


def duellists(match) -> tuple:
    duel = match.duel
    return match.players[duel.sides["a"]], match.players[duel.sides["b"]]


def play_round(engine, match, a_move, b_move, now=NOW):
    """Both seats commit; the round resolves on the second commit."""
    duel = match.duel
    round_index = duel.state.round_index
    engine.duel_choice(match, duel.sides["a"], duel.id, round_index, a_move, now=now)
    return engine.duel_choice(
        match, duel.sides["b"], duel.id, round_index, b_move, now=now
    )


def next_round(engine, match, now=NOW):
    """Advance past the reveal beat into the next open round."""
    return engine.on_duel_timer(match, DUEL_SCOPE, "duel_reveal", now=now)


def win_duel(engine, match, winner_side: str, now=NOW):
    """Take a duel 2-0 for `winner_side`."""
    wins = "rock", "scissors"
    moves = wins if winner_side == "a" else tuple(reversed(wins))
    result = play_round(engine, match, *moves, now=now)
    next_round(engine, match, now=now)
    return play_round(engine, match, *moves, now=now) or result


# --- Fielding a Duelist ---

def test_the_server_picks_the_duelists_game(engine):
    match, members, leaders = duel_match(engine)
    champion = members["alpha"][0]
    assert champion.role == "duelist"
    assert champion.assigned_game == "rps_duel"  # never chosen by the leader


def test_the_leader_cannot_choose_the_duelists_game(engine):
    match, members, leaders = duel_match(engine)
    result = engine.assign_game(
        match, leaders["alpha"].id, members["alpha"][0].id, GAMES[1]
    )
    assert result.ok is False and "server picks" in result.error
    assert members["alpha"][0].assigned_game == "rps_duel"


def test_a_duel_needs_an_opposing_duelist(engine):
    match, _, _ = duel_match(engine, duelist_teams=("alpha",))
    blocker = engine.start_blocker(match)
    assert blocker is not None
    assert "Alpha has a Duelist" in blocker and "Bravo" in blocker
    assert engine.host_start(match, match.host_player_id, now=NOW).ok is False


def test_neither_team_fielding_a_duelist_is_fine(engine):
    match, _, _ = duel_match(engine, duelist_teams=())
    assert engine.start_blocker(match) is None
    start(engine, match)
    assert match.duel is None  # no duel, and the match plays exactly as before


def test_only_one_duelist_per_team(engine):
    match, members, leaders = duel_match(engine)
    second = members["alpha"][1]
    assert engine.assign_role(match, leaders["alpha"].id, second.id, "duelist").ok
    assert "only field one Duelist" in engine.start_blocker(match)


def test_switching_off_the_duel_role_drops_the_server_pick(engine):
    match, members, leaders = duel_match(engine)
    champion = members["alpha"][0]
    assert engine.assign_role(
        match, leaders["alpha"].id, champion.id, "generalist"
    ).ok
    # rps_duel isn't a registered *game*, so it can't survive the role change.
    assert champion.assigned_game is None


# --- Starting a duel ---

def test_the_first_duel_opens_at_match_start(engine):
    match, members, _ = duel_match(engine)
    start(engine, match)
    duel = match.duel
    assert duel is not None and duel.phase == "choosing"
    assert duel.state.round_index == 1 and duel.state.wins == {"a": 0, "b": 0}
    a, b = duellists(match)
    assert {a.name, b.name} == {"A0", "B0"}
    assert a.status == b.status == "duelling"
    assert not green(a) and not green(b)  # a duel must be won to score


def test_duellists_get_no_puzzle(engine):
    match, members, _ = duel_match(engine)
    start(engine, match)
    champion = members["alpha"][0]
    assert champion.current_main is None and champion.current_puzzle() is None
    assert champion.timer_deadline is None  # and no wait timer to extend
    for solver in members["alpha"][1:]:
        assert solver.current_main is not None


def test_the_duel_clock_does_not_displace_a_wait_timer(engine):
    """The whole reason timers are scope-keyed: both must be pending at once."""
    match, members, _ = duel_match(engine)
    result = start(engine, match)
    scopes = {(request.scope_id, request.kind) for request in result.schedule}
    assert (DUEL_SCOPE, "duel_round") in scopes
    solver = members["alpha"][1]
    cleared = solve(engine, match, solver)
    solver_scopes = {(r.scope_id, r.kind) for r in cleared.schedule}
    assert (solver.id, "wait") in solver_scopes
    assert solver.timer_deadline is not None


# --- Committing a move ---

def test_a_move_is_never_visible_to_the_opponent_while_the_round_is_open(engine):
    match, members, _ = duel_match(engine)
    start(engine, match)
    duel = match.duel
    a_id, b_id = duel.sides["a"], duel.sides["b"]
    assert engine.duel_choice(match, a_id, duel.id, 1, "rock", now=NOW).ok

    opponent_view = match.public(b_id)["duel"]
    assert opponent_view["choices"] == {}          # nothing of A's leaked
    assert opponent_view["locked"] == {"a": True, "b": False}
    assert "rock" not in repr(opponent_view["choices"])
    own_view = match.public(a_id)["duel"]
    assert own_view["choices"] == {"a": "rock"}    # you see your own move
    assert own_view["you"] == "a"


def test_a_grandmaster_cannot_relay_the_opponents_move(engine):
    match, members, leaders = duel_match(engine)
    start(engine, match)
    duel = match.duel
    engine.duel_choice(match, duel.sides["a"], duel.id, 1, "paper", now=NOW)
    for leader in leaders.values():
        view = match.public(leader.id)["duel"]
        assert view["choices"] == {} and view["you"] is None


def test_ordinary_solvers_never_see_the_duel(engine):
    match, members, _ = duel_match(engine)
    start(engine, match)
    for solver in members["alpha"][1:] + members["bravo"][1:]:
        assert match.public(solver.id)["duel"] is None


def test_duel_view_never_carries_a_player_id(engine):
    """Player ids are WS credentials; the duel view names people instead."""
    match, members, _ = duel_match(engine)
    start(engine, match)
    a, b = duellists(match)
    view = match.public(a.id)["duel"]
    assert view["duellists"] == {"a": "A0", "b": "B0"}
    assert b.id not in repr(view)


@pytest.mark.parametrize("bad,message", [
    ("lizard", "legal move"),
    ("", "legal move"),
])
def test_illegal_moves_are_rejected(engine, bad, message):
    match, _, _ = duel_match(engine)
    start(engine, match)
    duel = match.duel
    result = engine.duel_choice(match, duel.sides["a"], duel.id, 1, bad, now=NOW)
    assert result.ok is False and message in result.error
    assert duel.state.choices == {}


def test_a_duellist_cannot_change_their_mind(engine):
    match, _, _ = duel_match(engine)
    start(engine, match)
    duel = match.duel
    assert engine.duel_choice(match, duel.sides["a"], duel.id, 1, "rock", now=NOW).ok
    result = engine.duel_choice(match, duel.sides["a"], duel.id, 1, "paper", now=NOW)
    assert result.ok is False and "already chose" in result.error
    assert duel.state.choices["a"] == "rock"


def test_outsiders_cannot_play_the_duel(engine):
    match, members, leaders = duel_match(engine)
    start(engine, match)
    duel = match.duel
    for intruder in (members["alpha"][1], leaders["bravo"]):
        result = engine.duel_choice(match, intruder.id, duel.id, 1, "rock", now=NOW)
        assert result.ok is False and "aren't in this duel" in result.error


def test_a_stale_duel_or_round_id_is_rejected(engine):
    match, _, _ = duel_match(engine)
    start(engine, match)
    duel = match.duel
    a_id = duel.sides["a"]
    assert engine.duel_choice(match, a_id, "nope", 1, "rock", now=NOW).ok is False
    result = engine.duel_choice(match, a_id, duel.id, 7, "rock", now=NOW)
    assert result.ok is False and "that round is over" in result.error


# --- Resolving rounds ---

def test_the_round_resolves_when_both_have_committed(engine):
    match, _, _ = duel_match(engine)
    start(engine, match)
    duel = match.duel
    play_round(engine, match, "rock", "scissors")
    assert duel.state.wins == {"a": 1, "b": 0}
    assert duel.phase == "reveal"
    assert duel.last_round == {"round": 1, "a": "rock", "b": "scissors", "winner": "a"}
    # Both moves are public the instant the round is scored.
    for player_id in duel.sides.values():
        assert match.public(player_id)["duel"]["choices"] == {
            "a": "rock", "b": "scissors",
        }


def test_a_tie_replays_the_round_without_scoring(engine):
    match, _, _ = duel_match(engine)
    start(engine, match)
    duel = match.duel
    play_round(engine, match, "rock", "rock")
    assert duel.state.wins == {"a": 0, "b": 0}
    next_round(engine, match)
    assert duel.phase == "choosing" and duel.state.round_index == 2
    assert duel.state.choices == {}  # the reveal is cleared for the new round


def test_letting_the_window_lapse_forfeits_the_round(engine):
    match, _, _ = duel_match(engine)
    start(engine, match)
    duel = match.duel
    engine.duel_choice(match, duel.sides["a"], duel.id, 1, "rock", now=NOW)
    engine.on_duel_timer(match, DUEL_SCOPE, "duel_round", now=NOW)
    assert duel.state.wins == {"a": 1, "b": 0}  # B never chose


def test_a_double_no_show_scores_nothing(engine):
    match, _, _ = duel_match(engine)
    start(engine, match)
    engine.on_duel_timer(match, DUEL_SCOPE, "duel_round", now=NOW)
    assert match.duel.state.wins == {"a": 0, "b": 0}


def test_stale_duel_timers_are_no_ops(engine):
    match, _, _ = duel_match(engine)
    start(engine, match)
    # A reveal timer arriving while the round is open must change nothing.
    assert engine.on_duel_timer(match, DUEL_SCOPE, "duel_reveal", now=NOW).changed is False
    assert engine.on_duel_timer(match, "duel:ghost", "duel_round", now=NOW).changed is False


# --- Winning a duel ---

def test_first_to_two_takes_the_duel(engine):
    match, members, _ = duel_match(engine)
    start(engine, match)
    duel = match.duel
    result = win_duel(engine, match, "a")
    assert duel.winner_side == "a" and duel.phase == "done"
    assert duel.state.wins == {"a": 2, "b": 0}
    champion, loser = duellists(match)
    assert champion.status == "cleared" and green(champion)
    assert loser.status == "duelling" and not green(loser)
    assert result.duel_result["winner_team_id"] == "alpha"
    assert result.duel_result["loser_team_id"] == "bravo"


def test_the_winner_holds_green_with_no_wait_timer(engine):
    """The next duel is what takes the win away — not a lapsing clock."""
    match, _, _ = duel_match(engine)
    start(engine, match)
    win_duel(engine, match, "a")
    champion, _ = duellists(match)
    assert champion.timer_kind is None and champion.timer_deadline is None
    # ...so a lapsing wait timer can't strip it either.
    assert engine.on_wait_expired(match, champion.id, now=NOW).changed is False
    assert green(champion)


def test_the_next_duel_is_queued_and_takes_green_back(engine):
    match, _, _ = duel_match(engine)
    start(engine, match)
    result = win_duel(engine, match, "a")
    assert (DUEL_SCOPE, "duel_next") in {
        (r.scope_id, r.kind) for r in result.schedule
    }
    first_id = match.duel.id
    engine.on_duel_timer(match, DUEL_SCOPE, "duel_next", now=NOW)
    assert match.duel.id != first_id and match.duel.phase == "choosing"
    a, b = duellists(match)
    assert a.status == b.status == "duelling"  # everything is on the line again
    assert not green(a) and not green(b)


# --- Currency ---

def test_duel_pay_doubles_with_the_streak_and_caps(engine):
    match, _, _ = duel_match(engine)
    start(engine, match)
    paid = []
    for _ in range(4):
        result = win_duel(engine, match, "a")
        paid.append(result.duel_result["currency"])
        engine.on_duel_timer(match, DUEL_SCOPE, "duel_next", now=NOW)
    assert paid == [2, 4, 8, 8]  # doubling, capped at DUEL_CURRENCY_CAP
    assert match.teams["alpha"].currency == sum(paid)
    assert match.teams["bravo"].currency == 0


def test_losing_resets_the_streak(engine):
    match, _, _ = duel_match(engine)
    start(engine, match)
    win_duel(engine, match, "a")
    engine.on_duel_timer(match, DUEL_SCOPE, "duel_next", now=NOW)
    win_duel(engine, match, "a")  # alpha now on 4
    assert match.teams["alpha"].duel_streak == 2
    engine.on_duel_timer(match, DUEL_SCOPE, "duel_next", now=NOW)
    result = win_duel(engine, match, "b")
    assert match.teams["alpha"].duel_streak == 0
    assert result.duel_result["currency"] == 2  # bravo starts its own streak


# --- The penalty ---

def test_a_loss_stamps_a_once_per_level_penalty(engine):
    match, _, _ = duel_match(engine)
    start(engine, match)
    result = win_duel(engine, match, "a")
    bravo = match.teams["bravo"]
    assert bravo.duel_penalty_until is not None
    assert bravo.duel_penalty_level == bravo.level == 1
    assert result.duel_result["penalty_until"] == bravo.duel_penalty_until
    assert (_team_scope("bravo"), "duel_penalty") in {
        (r.scope_id, r.kind) for r in result.schedule
    }
    stamped = bravo.duel_penalty_until

    # Losing again at the same level costs no extra time.
    engine.on_duel_timer(match, DUEL_SCOPE, "duel_next", now=NOW)
    again = win_duel(engine, match, "a")
    assert bravo.duel_penalty_until == stamped
    assert again.duel_result["penalty_until"] is None


def test_a_penalised_team_cannot_advance_even_fully_green(engine):
    match, members, _ = duel_match(engine)
    start(engine, match)
    win_duel(engine, match, "a")  # bravo loses: penalty + un-green champion
    bravo = match.teams["bravo"]

    for solver in members["bravo"][1:]:
        assert solve(engine, match, solver).correct is True
    assert bravo.level == 1  # the champion still isn't green

    # Bravo wins the rematch, so every member is green — but the lock holds.
    engine.on_duel_timer(match, DUEL_SCOPE, "duel_next", now=NOW)
    win_duel(engine, match, "b")
    assert all(green(member) for member in members["bravo"])
    assert bravo.level == 1, "the once-per-level penalty must still bite"

    # It releases when the lock lapses.
    later = NOW + timedelta(seconds=config.DUEL_PENALTY_SECONDS + 1)
    result = engine.on_duel_timer(match, _team_scope("bravo"), "duel_penalty", now=later)
    assert bravo.duel_penalty_until is None
    assert bravo.level == 2 and result.advanced_team_ids == ["bravo"]


def test_advancing_clears_the_penalty_so_the_next_level_can_take_its_own(engine):
    match, members, _ = duel_match(engine)
    start(engine, match)
    win_duel(engine, match, "b")  # alpha loses at level 1
    alpha = match.teams["alpha"]
    assert alpha.duel_penalty_level == 1

    engine.on_duel_timer(match, DUEL_SCOPE, "duel_next", now=NOW)
    win_duel(engine, match, "a")  # alpha wins it back
    for solver in members["alpha"][1:]:
        solve(engine, match, solver)
    later = NOW + timedelta(seconds=config.DUEL_PENALTY_SECONDS + 1)
    engine.on_duel_timer(match, _team_scope("alpha"), "duel_penalty", now=later)
    assert alpha.level == 2
    assert alpha.duel_penalty_until is None and alpha.duel_penalty_level == 0


def test_the_champion_keeps_their_win_across_a_level_advance(engine):
    match, members, _ = duel_match(engine)
    start(engine, match)
    win_duel(engine, match, "a")
    for solver in members["alpha"][1:]:
        solve(engine, match, solver)
    alpha = match.teams["alpha"]
    assert alpha.level == 2  # alpha never lost, so nothing locks it
    champion = members["alpha"][0]
    assert green(champion) and champion.current_main is None  # still no puzzle


# --- Interactions with the rest of the loop ---

def test_a_duellist_cannot_submit_or_take_a_bonus(engine):
    match, members, _ = duel_match(engine)
    start(engine, match)
    champion = members["alpha"][0]
    assert engine.submit_answer(
        match, champion.id, "any", MAIN_OK, now=NOW
    ).ok is False
    assert engine.choose_bonus(match, champion.id, now=NOW).ok is False
    assert engine.choose_wait(match, champion.id).ok is False


def test_attack_perks_cannot_touch_a_duellist(engine):
    """A Duelist is never in solving/bonus, so freeze and scramble skip them.

    Documented consequence, not an accident: with only the Duelist left
    un-cleared there is no legal target and the perk is refused, not wasted.
    """
    match, members, leaders = duel_match(engine)
    start(engine, match)
    match.teams["alpha"].currency = 20
    for solver in members["bravo"][1:]:
        solve(engine, match, solver)  # only bravo's champion is still un-cleared
    result = engine.buy_perk(match, leaders["alpha"].id, "freeze", now=NOW)
    assert result.ok is False
    assert match.teams["alpha"].currency == 20  # rejected attacks aren't billed


def test_the_duellist_cannot_be_handed_the_grandmaster_seat(engine):
    match, members, leaders = duel_match(engine)
    start(engine, match)
    result = engine.give_leader(
        match, leaders["alpha"].id, members["alpha"][0].id, now=NOW
    )
    assert result.ok is False and "Duelist" in result.error
    assert match.duel is not None and len(match.duel.sides) == 2


def test_extend_wait_cannot_prolong_a_duel_win(engine):
    match, members, leaders = duel_match(engine)
    start(engine, match)
    win_duel(engine, match, "a")
    match.teams["alpha"].currency = 20
    champion = members["alpha"][0]
    result = engine.buy_perk(
        match, leaders["alpha"].id, "extend_wait", target_id=champion.id, now=NOW
    )
    assert result.ok is False  # green, but holding no timer to extend
    assert match.teams["alpha"].currency == 20


def test_a_duel_stops_once_the_match_is_won(engine):
    match, members, _ = duel_match(engine)
    start(engine, match)
    alpha = match.teams["alpha"]
    for level in range(LEVELS):
        win_duel(engine, match, "a")
        for solver in members["alpha"][1:]:
            solve(engine, match, solver)
        if match.status == "finished":
            break
        engine.on_duel_timer(match, DUEL_SCOPE, "duel_next", now=NOW)
    assert match.status == "finished" and match.winner_team_id == "alpha"
    assert members["alpha"][0].status == "finished"
    # No further duel may start after the win.
    assert engine.on_duel_timer(match, DUEL_SCOPE, "duel_next", now=NOW).changed is False
