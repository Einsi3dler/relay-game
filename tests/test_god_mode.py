"""God mode: a seat that runs a match without playing in it.

The feature is small because of one decision, and these tests exist mostly to
keep that decision true. An `Observer` is not a `Player`, so it lives outside
`match.players` and `team.player_ids` — which is where every rule that counts,
seats, gates and advances people looks. If a God ever leaks into either, the
symptoms are miles from the cause: a lobby that will not start, a team that can
never advance, a roster with a ghost on it.

Three properties, then:

  * **It costs nothing.** No seat, no start blocker, no roster row.
  * **It is invisible.** Nothing a player receives says anyone is watching.
  * **It watches.** Both teams whole, in every status, unmasked — and it cannot
    play, spend, or touch a board.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend import config
from backend.engine import RelayEngine
from backend.models import Match, Observer
from backend.registry import GameRegistry
from tests.test_engine import GAMES, NOW, FakeGame, full_match, solve


@pytest.fixture
def engine(monkeypatch) -> RelayEngine:
    monkeypatch.setattr(config, "LEVEL_COUNT", 3)
    registry = GameRegistry(modules=[FakeGame(game_id) for game_id in GAMES])
    return RelayEngine(registry)


def lobby(engine: RelayEngine) -> tuple[Match, object]:
    """A fresh match plus its host."""
    match = engine.create_match()
    host, _ = engine.join_match(match, "Ada", "alpha", now=NOW)
    return match, host


def seated(engine: RelayEngine) -> tuple[Match, dict, dict]:
    """`full_match` one step earlier: two full squads, still in the lobby.

    The host controls are lobby-only, so the tests that exercise them need the
    match before it starts rather than after.
    """
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
            assert engine.assign_role(match, leader.id, player.id, "generalist").ok
            assert engine.assign_game(match, leader.id, player.id, GAMES[i]).ok
    return match, members, leaders


# --- an observer costs nothing -------------------------------------------

def test_a_god_takes_no_seat(engine):
    match, _ = lobby(engine)
    before = len(match.players)
    god = engine.add_observer(match)
    assert isinstance(god, Observer) and god.id.startswith("g_")
    assert len(match.players) == before
    assert god.id not in match.players
    assert match.teams["alpha"].player_ids == [match.host_player_id]


def test_a_god_does_not_block_the_start(engine):
    """The lobby refuses to start while anyone is unassigned. A God has no
    team, and would be exactly that person if they were a player."""
    match, members, leaders = seated(engine)
    assert engine.start_blocker(match) is None  # sanity: it could start
    engine.add_observer(match)
    assert engine.start_blocker(match) is None
    assert match.unassigned() == []


def test_a_god_is_not_countable_anywhere(engine):
    """Every count the engine takes reads players or rosters, never observers."""
    match, members, leaders = full_match(engine)
    engine.add_observer(match)
    for team in match.teams.values():
        assert len(team.player_ids) == 5  # a leader and four solvers
        assert team.roster_size == 4  # frozen at the start; drives advancement
        assert all(player_id in match.players for player_id in team.player_ids)


def test_a_god_seat_cannot_be_bought_with_a_rejoin_code(engine):
    """`rejoin` walks the players looking for a matching code. An observer
    holds none, so there is no six-character route into a God seat."""
    match, _ = lobby(engine)
    god = engine.add_observer(match)
    assert not hasattr(god, "rejoin_code")
    with pytest.raises(ValueError):
        engine.rejoin(match, "GGGGGG")


# --- the host's controls, without the host's seat -------------------------

def test_a_god_holds_the_host_controls(engine):
    match, members, leaders = seated(engine)
    god = engine.add_observer(match)
    assert engine.host_set_min_players(match, god.id, 2).ok
    assert match.min_players == 2
    assert engine.host_set_team_name(match, god.id, "alpha", "Kestrel").ok
    assert engine.host_start(match, god.id).ok
    assert match.status == "active"
    assert engine.host_end_session(match, god.id).ok
    assert match.status == "finished"


def test_a_god_can_kick_and_move_players(engine):
    match, _ = lobby(engine)
    god = engine.add_observer(match)
    stray, _ = engine.join_match(match, "Bo", None, now=NOW)
    assert engine.host_move(match, god.id, stray.id, "bravo").ok
    assert stray.team_id == "bravo"
    assert engine.host_kick(match, god.id, stray.id).ok
    assert stray.id not in match.players


def test_a_plain_player_still_cannot_use_host_controls(engine):
    """Widening the host guard must not widen it to everyone."""
    match, _ = lobby(engine)
    engine.add_observer(match)
    guest, _ = engine.join_match(match, "Bo", "bravo", now=NOW)
    result = engine.host_set_min_players(match, guest.id, 2)
    assert result.ok is False and "host" in result.error


def test_the_host_seat_never_lands_on_a_god(engine):
    """`claim_host` decides whether the seat is free by looking its holder up
    in `match.players` and asking whether they are connected. An observer would
    come back None there, the guard would never fire, and any player could take
    the host seat whenever a God was watching. So the seat stays with people."""
    match, host = lobby(engine)
    god = engine.add_observer(match)
    guest, _ = engine.join_match(match, "Bo", "bravo", now=NOW)
    assert match.host_player_id == host.id

    # The host leaving passes the seat to a player, never to the God.
    engine.leave_match(match, host.id)
    assert match.host_player_id == guest.id
    assert match.host_player_id not in match.observers

    # And with a live host in the chair, the seat is not up for grabs.
    third, _ = engine.join_match(match, "Cass", "bravo", now=NOW)
    result = engine.claim_host(match, third.id)
    assert result.ok is False and "still here" in result.error
    assert god.id not in match.players


# --- naming a Grandmaster -------------------------------------------------

def test_a_god_can_override_a_seated_grandmaster(engine):
    """`claim_leader` refuses a seat whose holder is present. This is the
    override, and it is the whole reason the action exists: a table where the
    wrong player grabbed the seat is a table that cannot start."""
    match, _ = lobby(engine)
    god = engine.add_observer(match)
    wrong, _ = engine.join_match(match, "Bo", "alpha", now=NOW)
    engine.claim_leader(match, wrong.id)
    assert wrong.is_leader and wrong.connected

    right, _ = engine.join_match(match, "Cass", "alpha", now=NOW)
    engine.assign_role(match, wrong.id, right.id, "generalist")
    engine.assign_game(match, wrong.id, right.id, GAMES[0])

    result = engine.god_set_leader(match, god.id, right.id)
    assert result.ok
    assert right.is_leader and match.teams["alpha"].leader_id == right.id
    assert wrong.is_leader is False
    # The seat carries no role and no board.
    assert right.role is None and right.assigned_game is None


def test_the_god_handover_reads_like_an_ordinary_one(engine):
    """Every player reads the feed. "A god named you Grandmaster" would
    announce a seat the table is not supposed to know exists."""
    match, _ = lobby(engine)
    god = engine.add_observer(match)
    player, _ = engine.join_match(match, "Bo", "bravo", now=NOW)
    engine.god_set_leader(match, god.id, player.id)
    message = match.events[-1].message
    assert message == "Bo is now team Bravo's Grandmaster."
    assert "god" not in message.lower()


def test_god_set_leader_refuses_everyone_else(engine):
    """Gated on the God seat directly, not through the host guard: who leads a
    team is a move in the game, and widening the host guard for it would hand
    that to every host too."""
    match, host = lobby(engine)
    player, _ = engine.join_match(match, "Bo", "bravo", now=NOW)
    result = engine.god_set_leader(match, host.id, player.id)
    assert result.ok is False and "God" in result.error
    assert player.is_leader is False


def test_god_set_leader_is_lobby_only(engine):
    match, members, leaders = seated(engine)
    god = engine.add_observer(match)
    assert engine.host_start(match, god.id, now=NOW).ok
    result = engine.god_set_leader(match, god.id, members["alpha"][0].id)
    assert result.ok is False and "started" in result.error


def test_god_set_leader_needs_a_teamed_target(engine):
    match, _ = lobby(engine)
    god = engine.add_observer(match)
    stray, _ = engine.join_match(match, "Bo", None, now=NOW)
    assert engine.god_set_leader(match, god.id, stray.id).ok is False
    assert engine.god_set_leader(match, god.id, "p_nobody").ok is False


# --- what a God cannot do -------------------------------------------------

def test_a_god_cannot_play(engine):
    """Every one of these looks the actor up in `match.players` and finds
    nothing. The socket refuses them too (see test_server.py); this pins the
    engine half, so the property does not rest on the socket alone."""
    match, members, leaders = full_match(engine)
    god = engine.add_observer(match)
    match.teams["alpha"].currency = 20
    target = members["alpha"][0]
    refusals = [
        engine.set_team(match, god.id, "alpha"),
        engine.claim_leader(match, god.id),
        engine.release_leader(match, god.id),
        engine.claim_host(match, god.id),
        engine.leave_match(match, god.id),
        engine.assign_role(match, god.id, target.id, "generalist"),
        engine.assign_game(match, god.id, target.id, GAMES[0]),
        engine.buy_perk(match, god.id, "shield", now=NOW),
        engine.give_leader(match, god.id, target.id, now=NOW),
        engine.submit_answer(
            match, god.id, target.current_main.id, "MAIN_OK", now=NOW
        ),
        engine.choose_wait(match, god.id),
        engine.choose_bonus(match, god.id, now=NOW),
    ]
    assert all(result.ok is False for result in refusals)
    assert match.teams["alpha"].currency == 20  # nothing was spent


def test_connecting_and_leaving_is_not_news(engine):
    match, members, leaders = full_match(engine)
    god = engine.add_observer(match)
    before = len(match.events)
    assert engine.on_reconnect(match, god.id).changed is False
    assert engine.on_disconnect(match, god.id).changed is False
    assert len(match.events) == before  # nothing was announced


# --- what a God sees ------------------------------------------------------

def test_a_god_sees_both_teams_whole_in_every_status(engine):
    match, members, leaders = full_match(engine)
    god = engine.add_observer(match)
    for status in ("lobby", "active", "finished"):
        match.status = status
        state = match.public(god.id)
        assert state["god"] == {"id": god.id, "name": "God"}
        assert state["me"] is None
        for team_id in ("alpha", "bravo"):
            team = state["teams"][team_id]
            assert len(team["players"]) == 5, f"{status}: partial roster"
            assert team["currency"] is not None
            assert team["green_count"] is not None
            # The lobby masks the opposing loadout from players. A God is on
            # neither side, so neither side is "the opposition".
            assert all("rejoin_code" in row for row in team["players"])


def test_a_god_sees_through_silence(engine):
    """Silence blinds a Grandmaster to their own roster. It is an attack on a
    seat at the table, and a God is not sitting at it."""
    match, members, leaders = full_match(engine)
    god = engine.add_observer(match)
    match.teams["bravo"].currency = 20
    # A real clock, not the fixture's fixed NOW: the view layer asks whether
    # `silenced_until` is still in the future, and it asks the wall clock.
    now = datetime.now(timezone.utc)
    assert engine.buy_perk(match, leaders["bravo"].id, "silence", now=now).ok

    blinded = match.public(leaders["alpha"].id)["teams"]["alpha"]
    assert blinded["green_count"] is None  # sanity: the perk landed

    seen = match.public(god.id)["teams"]["alpha"]
    assert seen["green_count"] is not None
    assert all(row["status"] != "hidden" for row in seen["players"])


def test_a_god_reads_the_whole_event_feed(engine):
    """`green` events are leader-only. A God is watching both leaders."""
    match, members, leaders = full_match(engine)
    god = engine.add_observer(match)
    assert solve(engine, match, members["alpha"][0]).correct is True
    kinds = {event["kind"] for event in match.public(god.id)["events"]}
    assert "green" in kinds
    player_kinds = {
        event["kind"]
        for event in match.public(members["bravo"][0].id)["events"]
    }
    assert "green" not in player_kinds


def test_nobody_at_the_table_can_tell_a_god_is_there(engine):
    match, members, leaders = full_match(engine)
    before = match.public(leaders["alpha"].id)
    engine.add_observer(match)
    after = match.public(leaders["alpha"].id)
    assert before == after
    assert after["god"] is None
