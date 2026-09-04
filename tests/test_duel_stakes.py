"""Funding a staked duel: the negotiation between a Duelist and their leader.

BID WAR is bought, not given. Before it opens, each Duelist asks their
Grandmaster for coins out of the team purse and fights with exactly what they
are handed, so the two pools are deliberately unequal. These cover the part
that is engine-side: whose coins move, when, who may move them, and what
happens when nobody answers.

The auction itself is tests/games/test_duel4_bid_war.py; playing one through
the engine is tests/games/test_duels_through_the_engine.py.
"""

from __future__ import annotations

import pytest

from backend import config
from backend.engine import DUEL_SCOPE, EngineResult, RelayEngine
from backend.games.duel4_bid_war import BidWar
from backend.games.duel_base import SIDES
from backend.models import green
from backend.registry import GameRegistry
from tests.test_duels import duel_match
from tests.test_engine import GAMES, LEVELS, NOW, FakeGame


@pytest.fixture
def engine(monkeypatch) -> RelayEngine:
    """An engine whose only duel game is the staked one."""
    monkeypatch.setattr(config, "LEVEL_COUNT", LEVELS)
    return RelayEngine(
        GameRegistry(modules=[FakeGame(g) for g in GAMES], duels=[BidWar()])
    )


def waiting(engine: RelayEngine, purse: int = 40):
    """A started match parked on the stake request, with money to spend."""
    match, members, leaders = duel_match(engine)
    for team in match.teams.values():
        team.currency = purse
    assert engine.host_start(match, match.host_player_id, now=NOW).match_started
    assert match.pending_stake is not None
    return match, members, leaders


def side_of_team(match, team_id: str) -> str:
    return next(
        side for side, tid in match.pending_stake.team_of.items() if tid == team_id
    )


# --- opening the negotiation ---

def test_a_staked_duel_waits_to_be_funded_before_it_exists(engine):
    """The module cannot roll its opening lot without knowing both purses, so
    there is genuinely no duel yet — only a request."""
    match, _, _ = waiting(engine)
    assert match.duel is None
    pending = match.pending_stake
    assert pending.duel_game_id == "bid_war"
    assert set(pending.sides) == set(SIDES)
    assert pending.grants == {} and pending.asks == {}
    assert pending.deadline is not None


def test_the_champions_are_out_of_the_puzzle_pool_while_it_is_funded(engine):
    """They are not solving and they are not green: a team cannot slip a level
    through while its Duelist waits on the money."""
    match, _, _ = waiting(engine)
    for player_id in match.pending_stake.sides.values():
        player = match.players[player_id]
        assert player.status == "duelling"
        assert player.current_main is None and not green(player)


def test_an_unstaked_duel_opens_straight_away(monkeypatch):
    """Only BID WAR negotiates. The other three are free and must not grow a
    funding step by accident."""
    from backend.games.duel1_rps import RockPaperScissorsDuel
    monkeypatch.setattr(config, "LEVEL_COUNT", LEVELS)
    engine = RelayEngine(GameRegistry(
        modules=[FakeGame(g) for g in GAMES], duels=[RockPaperScissorsDuel()]
    ))
    match, _, _ = duel_match(engine)
    engine.host_start(match, match.host_player_id, now=NOW)
    assert match.pending_stake is None
    assert match.duel is not None


def test_a_broke_table_is_dealt_a_free_duel_instead(monkeypatch):
    """Teams open a match on an empty purse. A staked duel there would be two
    empty hands, every lot tied, and a tiebreak neither side paid for."""
    from backend.games.duel1_rps import RockPaperScissorsDuel
    monkeypatch.setattr(config, "LEVEL_COUNT", LEVELS)
    engine = RelayEngine(GameRegistry(
        modules=[FakeGame(g) for g in GAMES],
        duels=[BidWar(), RockPaperScissorsDuel()],
    ))
    match, _, _ = duel_match(engine)
    assert all(team.currency == 0 for team in match.teams.values())
    engine.host_start(match, match.host_player_id, now=NOW)
    assert match.pending_stake is None
    assert match.duel.module.staked is False


def test_one_broke_team_is_enough_to_call_it_off(monkeypatch):
    """Both purses, not either: a staked duel between a rich team and a broke
    one is not a contest, and the broke side did not choose to be there."""
    from backend.games.duel1_rps import RockPaperScissorsDuel
    monkeypatch.setattr(config, "LEVEL_COUNT", LEVELS)
    engine = RelayEngine(GameRegistry(
        modules=[FakeGame(g) for g in GAMES],
        duels=[BidWar(), RockPaperScissorsDuel()],
    ))
    match, _, _ = duel_match(engine)
    match.teams["alpha"].currency = 500
    match.teams["bravo"].currency = config.DUEL_STAKE_MIN_PURSE - 1
    engine.host_start(match, match.host_player_id, now=NOW)
    assert match.duel.module.staked is False


def test_a_catalogue_with_nothing_free_still_deals_the_staked_one(engine):
    """The fallback must not deadlock a server whose only duel is staked."""
    match, _, _ = duel_match(engine)
    engine.host_start(match, match.host_player_id, now=NOW)
    assert match.pending_stake is not None


# --- asking, and being answered ---

def test_a_duelist_asks_and_their_leader_answers_with_what_they_choose(engine):
    """A counter-offer is the normal case, not an error: the ask is a number
    the Grandmaster is free to ignore in either direction."""
    match, _, leaders = waiting(engine)
    side = side_of_team(match, "alpha")
    duellist = match.players[match.pending_stake.sides[side]]

    assert engine.request_stake(match, duellist.id, 30).ok
    assert match.pending_stake.asks[side] == 30
    assert match.teams["alpha"].currency == 40  # asking moves no coins

    assert engine.answer_stake(match, leaders["alpha"].id, 12, now=NOW).ok
    assert match.pending_stake.grants[side] == 12
    assert match.teams["alpha"].currency == 28  # and granting does


def test_the_grant_can_be_more_than_was_asked_for(engine):
    match, _, leaders = waiting(engine)
    side = side_of_team(match, "alpha")
    engine.request_stake(match, match.pending_stake.sides[side], 5)
    engine.answer_stake(match, leaders["alpha"].id, 25, now=NOW)
    assert match.pending_stake.grants[side] == 25


def test_granting_nothing_is_a_legal_answer(engine):
    """A rejection is a stake of zero rather than a refusal to answer: the
    Duelist still plays, they just bid with nothing."""
    match, _, leaders = waiting(engine)
    side = side_of_team(match, "alpha")
    assert engine.answer_stake(match, leaders["alpha"].id, 0, now=NOW).ok
    assert match.pending_stake.grants[side] == 0
    assert match.teams["alpha"].currency == 40


def test_nobody_can_stake_coins_the_team_does_not_hold(engine):
    match, _, leaders = waiting(engine, purse=7)
    side = side_of_team(match, "alpha")
    engine.answer_stake(match, leaders["alpha"].id, 999, now=NOW)
    assert match.pending_stake.grants[side] == 7      # capped by the purse
    assert match.teams["alpha"].currency == 0         # which is now empty


def test_an_ask_is_capped_by_the_purse_too(engine):
    """A Duelist asking for a fortune is noise, not a negotiating position."""
    match, _, _ = waiting(engine, purse=9)
    side = side_of_team(match, "alpha")
    engine.request_stake(match, match.pending_stake.sides[side], 500)
    assert match.pending_stake.asks[side] == 9


def test_only_the_grandmaster_of_that_team_may_stake(engine):
    match, members, leaders = waiting(engine)
    solver = members["alpha"][1]
    assert engine.answer_stake(match, solver.id, 10, now=NOW).ok is False
    duellist = match.players[match.pending_stake.sides[side_of_team(match, "alpha")]]
    assert engine.answer_stake(match, duellist.id, 10, now=NOW).ok is False
    assert match.teams["alpha"].currency == 40


def test_a_leader_cannot_stake_twice(engine):
    match, _, leaders = waiting(engine)
    assert engine.answer_stake(match, leaders["alpha"].id, 10, now=NOW).ok
    assert engine.answer_stake(match, leaders["alpha"].id, 10, now=NOW).ok is False
    assert match.teams["alpha"].currency == 30  # billed once


def test_only_a_seated_duelist_may_ask(engine):
    match, members, _ = waiting(engine)
    assert engine.request_stake(match, members["alpha"][1].id, 5).ok is False


def test_asking_after_the_answer_is_too_late(engine):
    match, _, leaders = waiting(engine)
    side = side_of_team(match, "alpha")
    duellist_id = match.pending_stake.sides[side]
    engine.answer_stake(match, leaders["alpha"].id, 10, now=NOW)
    assert engine.request_stake(match, duellist_id, 30).ok is False


# --- opening the duel ---

def test_the_duel_opens_once_both_leaders_have_answered(engine):
    match, _, leaders = waiting(engine)
    engine.answer_stake(match, leaders["alpha"].id, 25, now=NOW)
    assert match.duel is None, "one answer is not both"
    engine.answer_stake(match, leaders["bravo"].id, 6, now=NOW)

    assert match.pending_stake is None
    duel = match.duel
    assert duel is not None and duel.module.id == "bid_war"
    purses = duel.state.private["coins"]
    assert {purses[side_of_team_in(duel, "alpha")],
            purses[side_of_team_in(duel, "bravo")]} == {25, 6}


def side_of_team_in(duel, team_id: str) -> str:
    return next(side for side, tid in duel.team_of.items() if tid == team_id)


def test_the_pools_are_unequal_by_design(engine):
    """The whole point of the feature: a Grandmaster who backs their champion
    buys them a bigger hand and pays for it out of the perk shop."""
    match, _, leaders = waiting(engine)
    engine.answer_stake(match, leaders["alpha"].id, 30, now=NOW)
    engine.answer_stake(match, leaders["bravo"].id, 2, now=NOW)
    purses = match.duel.state.private["coins"]
    assert purses[side_of_team_in(match.duel, "alpha")] == 30
    assert purses[side_of_team_in(match.duel, "bravo")] == 2
    assert match.teams["alpha"].currency == 10
    assert match.teams["bravo"].currency == 38


# --- nobody answers ---

def test_the_window_lapsing_funds_both_sides_and_starts_the_duel(engine):
    """A duel that waited forever on an absent Grandmaster would stall both
    teams, so silence is answered with the configured default."""
    match, _, _ = waiting(engine)
    engine.on_duel_timer(match, DUEL_SCOPE, "duel_stake", now=NOW)
    assert match.pending_stake is None
    assert match.duel is not None
    for side in SIDES:
        assert match.duel.state.private["coins"][side] == config.DUEL_STAKE_DEFAULT
    for team in match.teams.values():
        assert team.currency == 40 - config.DUEL_STAKE_DEFAULT


def test_a_lapse_leaves_an_answer_that_was_already_given_alone(engine):
    match, _, leaders = waiting(engine)
    engine.answer_stake(match, leaders["alpha"].id, 31, now=NOW)
    engine.on_duel_timer(match, DUEL_SCOPE, "duel_stake", now=NOW)
    duel = match.duel
    assert duel.state.private["coins"][side_of_team_in(duel, "alpha")] == 31
    assert duel.state.private["coins"][side_of_team_in(duel, "bravo")] == (
        config.DUEL_STAKE_DEFAULT
    )


def test_an_empty_purse_stakes_nothing_and_still_plays(engine):
    """Not an error. A Duelist with no coins bids zero and reaches overtime,
    which is a bleak position rather than a broken one."""
    match, _, _ = waiting(engine, purse=0)
    engine.on_duel_timer(match, DUEL_SCOPE, "duel_stake", now=NOW)
    assert match.duel is not None
    assert match.duel.state.private["coins"] == {"a": 0, "b": 0}


def test_the_feed_never_carries_a_stake_amount(engine):
    """The events go to everyone, so a number in one would hand the opposing
    Duelist exactly the read that the secret stake exists to deny them."""
    match, _, leaders = waiting(engine)
    side = side_of_team(match, "alpha")
    engine.request_stake(match, match.pending_stake.sides[side], 34)
    engine.answer_stake(match, leaders["alpha"].id, 27, now=NOW)
    feed = " ".join(event.message for event in match.events)
    assert "has staked" in feed, "the fact of it is public"
    assert "34" not in feed and "27" not in feed, "the amount is not"


def test_the_staking_timer_is_routed_like_every_other_duel_timer(engine):
    """`main.py` routes a fired timer on a `duel_` prefix. A duel-scope timer
    named anything else falls through to `on_wait_expired` with the duel scope
    in place of a player id, and silently never fires — so the auto-grant that
    stops an absent Grandmaster stalling both teams would never happen.
    """
    import inspect

    from backend import main as server

    match, _, _ = waiting(engine)
    kind = next(
        timer.kind for timer in _scheduled(engine, match)
        if timer.scope_id == DUEL_SCOPE
    )
    assert kind.startswith("duel_"), kind
    # And the routing this depends on is really there.
    assert 'kind.startswith("duel_")' in inspect.getsource(server._timer_fired)


def _scheduled(engine, match):
    """The timers `_open_staking` asked for, by re-running it on a fresh duel."""
    result = EngineResult()
    pending = match.pending_stake
    seats = engine._duel_seats(match)
    match.pending_stake = None
    engine._open_staking(
        match, result, engine.registry.duel_by_id(pending.duel_game_id), seats, NOW
    )
    return result.schedule


# --- visibility ---

def test_a_duelist_sees_their_own_negotiation_and_not_the_other_teams(engine):
    """An opponent's purse is the one thing worth knowing before the first
    bid, so it stays hidden until the duel opens."""
    match, _, leaders = waiting(engine)
    side = side_of_team(match, "alpha")
    duellist_id = match.pending_stake.sides[side]
    engine.request_stake(match, duellist_id, 18)
    engine.answer_stake(match, leaders["bravo"].id, 27, now=NOW)

    view = match.public(duellist_id)["pending_stake"]
    assert view["ask"] == 18
    assert view["granted"] is None and view["settled"] is False
    assert "27" not in repr(view), "the other team's stake leaked"
    assert 27 not in list(view.values())


def test_a_grandmaster_sees_the_ask_they_have_to_answer(engine):
    match, _, leaders = waiting(engine)
    side = side_of_team(match, "alpha")
    engine.request_stake(match, match.pending_stake.sides[side], 18)
    view = match.public(leaders["alpha"].id)["pending_stake"]
    assert view["ask"] == 18 and view["settled"] is False


def test_an_ordinary_solver_never_learns_a_duel_is_being_funded(engine):
    match, members, _ = waiting(engine)
    assert match.public(members["alpha"][1].id)["pending_stake"] is None


# --- settlement, and the single duel ---

def test_winnings_come_back_to_the_purse_but_the_stake_does_not(engine):
    match, _, leaders = waiting(engine)
    engine.answer_stake(match, leaders["alpha"].id, 20, now=NOW)
    engine.answer_stake(match, leaders["bravo"].id, 20, now=NOW)
    duel, purse_before = match.duel, match.teams["alpha"].currency
    alpha_side = side_of_team_in(duel, "alpha")

    # Hand alpha the whole sale, then let the last auction settle it.
    duel.state.private["won"] = {alpha_side: 12, "a" if alpha_side == "b" else "b": 0}
    duel.state.private["auction"] = 5
    duel.state.private["pot"] = 3
    engine.duel_choice(match, duel.sides["a"], duel.id, duel.state.round_index,
                       "1" if alpha_side == "a" else "0", now=NOW)
    engine.duel_choice(match, duel.sides["b"], duel.id, duel.state.round_index,
                       "1" if alpha_side == "b" else "0", now=NOW)

    assert duel.winner_side == alpha_side
    won = duel.module.settlement(duel.state)[alpha_side]
    # The winnings landed, and the 20 that was staked never came back.
    assert match.teams["alpha"].currency >= purse_before + won
    assert match.teams["alpha"].currency < 40 + won


def test_a_staked_duel_is_fought_once_a_level(engine):
    """No bonus round. The teams already paid for this one out of their
    purses, and a second would ask them to do it again in the same breath."""
    match, _, leaders = waiting(engine)
    engine.answer_stake(match, leaders["alpha"].id, 20, now=NOW)
    engine.answer_stake(match, leaders["bravo"].id, 20, now=NOW)
    duel = match.duel
    duel.state.private["auction"] = 5
    duel.state.private["won"] = {"a": 5, "b": 0}
    round_index = duel.state.round_index
    engine.duel_choice(match, duel.sides["a"], duel.id, round_index, "1", now=NOW)
    engine.duel_choice(match, duel.sides["b"], duel.id, round_index, "0", now=NOW)

    assert duel.phase == "done"
    assert match.duels_played >= match.config_snapshot["duels_per_level"]
    # Both stand down, the loser included, so no team is blocked for good.
    for player_id in duel.sides.values():
        assert green(match.players[player_id])
