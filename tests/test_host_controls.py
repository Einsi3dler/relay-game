"""Host controls and the lobby's cross-team mask.

The seat ceiling comes from the registry (a team never fields two players on
the same game), the host sizes and names the table inside it, and the host can
bin a lobby or stop a running match. The lobby shows both rosters and both sets
of roles but not the opposing loadout.
"""

from __future__ import annotations

import pytest

from backend import config
from backend.engine import RelayEngine
from backend.models import Match
from backend.registry import GameRegistry
from tests.test_engine import GAMES, NOW, FakeGame, full_match


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


# --- the ceiling comes from the registry ---------------------------------

def test_the_ceiling_is_one_seat_per_game_plus_the_duellist(engine):
    # The fixture registers five games, so five solvers and one champion.
    assert engine.max_players_ceiling() == len(GAMES) + config.DUEL_SEATS_PER_TEAM


def test_a_fresh_match_opens_at_the_ceiling(engine):
    match = engine.create_match()
    assert match.max_players == engine.max_players_ceiling()


def test_registering_a_game_raises_the_ceiling_on_its_own():
    """No hand-kept number: the cap follows the library."""
    small = RelayEngine(GameRegistry(modules=[FakeGame("only")]))
    big = RelayEngine(GameRegistry(modules=[FakeGame(g) for g in GAMES]))
    assert big.max_players_ceiling() - small.max_players_ceiling() == len(GAMES) - 1


def test_a_team_cannot_be_filled_past_the_matchs_cap(engine):
    match, host = lobby(engine)
    assert engine.host_set_max_players(match, host.id, 2).ok
    engine.join_match(match, "B", "alpha", now=NOW)   # 2 playing
    engine.join_match(match, "C", "alpha", now=NOW)   # + the leader seat
    with pytest.raises(ValueError):
        engine.join_match(match, "D", "alpha", now=NOW)


# --- the host sizes the table --------------------------------------------

def test_the_host_sets_the_max_within_the_ceiling(engine):
    match, host = lobby(engine)
    assert engine.host_set_max_players(match, host.id, 3).ok
    assert match.max_players == 3


def test_the_max_is_refused_above_the_ceiling(engine):
    match, host = lobby(engine)
    over = engine.max_players_ceiling() + 1
    result = engine.host_set_max_players(match, host.id, over)
    assert not result.ok and "one seat per game" in result.error
    assert match.max_players == engine.max_players_ceiling()


@pytest.mark.parametrize("value", [0, -1])
def test_a_team_of_nobody_is_refused(engine, value):
    match, host = lobby(engine)
    assert not engine.host_set_max_players(match, host.id, value).ok


def test_the_max_cannot_drop_below_players_already_seated(engine):
    """The seats are taken; over-filling would only surface later as a lobby
    that cannot be started."""
    match, host = lobby(engine)
    for name in ("B", "C"):
        engine.join_match(match, name, "alpha", now=NOW)
    engine.claim_leader(match, host.id)          # host leads, 2 playing
    result = engine.host_set_max_players(match, host.id, 1)
    assert not result.ok and "already has 2 players" in result.error


def test_shrinking_the_table_pulls_the_minimum_down_with_it(engine):
    """A minimum above the maximum is a threshold no team could reach, so the
    host does not have to undo the minimum first."""
    match, host = lobby(engine)
    assert engine.host_set_min_players(match, host.id, 3).ok
    result = engine.host_set_max_players(match, host.id, 2)
    assert result.ok
    assert match.max_players == 2 and match.min_players == 2
    assert "lowered to 2" in result.events[-1].message


def test_a_roomier_table_leaves_the_minimum_alone(engine):
    match, host = lobby(engine)
    assert engine.host_set_min_players(match, host.id, 2).ok
    assert engine.host_set_max_players(match, host.id, 4).ok
    assert match.min_players == 2


def test_the_minimum_is_bounded_by_this_matchs_max_not_the_ceiling(engine):
    match, host = lobby(engine)
    assert engine.host_set_max_players(match, host.id, 2).ok
    result = engine.host_set_min_players(match, host.id, 3)
    assert not result.ok and "1..2" in result.error


def test_only_the_host_sizes_the_table(engine):
    match, host = lobby(engine)
    other, _ = engine.join_match(match, "Bob", "bravo", now=NOW)
    result = engine.host_set_max_players(match, other.id, 2)
    assert not result.ok and "only the host" in result.error


# --- the host names the teams --------------------------------------------

def test_the_host_renames_a_team(engine):
    match, host = lobby(engine)
    assert engine.host_set_team_name(match, host.id, "alpha", "Red Kites").ok
    assert match.teams["alpha"].name == "Red Kites"


def test_a_team_name_is_trimmed_and_collapsed(engine):
    match, host = lobby(engine)
    assert engine.host_set_team_name(match, host.id, "alpha", "  Red   Kites  ").ok
    assert match.teams["alpha"].name == "Red Kites"


@pytest.mark.parametrize("name", ["", "   ", "\t\n"])
def test_a_team_needs_a_name(engine, name):
    match, host = lobby(engine)
    result = engine.host_set_team_name(match, host.id, "alpha", name)
    assert not result.ok and "needs a name" in result.error


def test_a_team_name_has_a_length_limit(engine):
    match, host = lobby(engine)
    result = engine.host_set_team_name(
        match, host.id, "alpha", "x" * (config.TEAM_NAME_MAX + 1)
    )
    assert not result.ok and "at most" in result.error


def test_both_teams_cannot_share_a_name(engine):
    """Case-insensitively — two teams called Kites help nobody read the feed."""
    match, host = lobby(engine)
    assert engine.host_set_team_name(match, host.id, "alpha", "Kites").ok
    result = engine.host_set_team_name(match, host.id, "bravo", "kites")
    assert not result.ok and "already has that name" in result.error


def test_renaming_an_unknown_team_is_refused(engine):
    match, host = lobby(engine)
    assert not engine.host_set_team_name(match, host.id, "charlie", "X").ok


def test_only_the_host_renames(engine):
    match, host = lobby(engine)
    other, _ = engine.join_match(match, "Bob", "bravo", now=NOW)
    result = engine.host_set_team_name(match, other.id, "alpha", "Nope")
    assert not result.ok and "only the host" in result.error


def test_a_running_match_is_not_renamed(engine):
    match, _, _ = full_match(engine)
    result = engine.host_set_team_name(
        match, match.host_player_id, "alpha", "Too Late"
    )
    assert not result.ok and "already started" in result.error


# --- cancelling a lobby ---------------------------------------------------

def test_the_host_cancels_a_lobby(engine):
    match, host = lobby(engine)
    result = engine.host_cancel_session(match, host.id)
    assert result.ok and match.status == "cancelled"
    assert match.ended_reason == "host_cancelled"
    assert "cancelled the session" in result.events[-1].message


def test_only_the_host_cancels(engine):
    match, host = lobby(engine)
    other, _ = engine.join_match(match, "Bob", "bravo", now=NOW)
    assert not engine.host_cancel_session(match, other.id).ok


def test_a_running_match_is_ended_not_cancelled(engine):
    match, _, _ = full_match(engine)
    result = engine.host_cancel_session(match, match.host_player_id)
    assert not result.ok and "end it instead" in result.error
    assert match.status == "active"


# --- ending a running match ----------------------------------------------

def test_the_host_ends_a_running_match_with_no_winner(engine):
    match, _, _ = full_match(engine)
    result = engine.host_end_session(match, match.host_player_id)
    assert result.ok and match.status == "finished"
    assert match.ended_reason == "host_ended"
    assert match.winner_team_id is None      # nothing was decided
    assert result.winner_team_id is None


def test_ending_stops_every_board_and_clock(engine):
    match, members, _ = full_match(engine)
    result = engine.host_end_session(match, match.host_player_id)
    for player in match.players.values():
        assert player.status == "finished"
        assert player.current_main is None and player.current_bonus is None
        assert player.timer_deadline is None and player.puzzle_deadline is None
        assert player.id in result.cancel
    assert not result.schedule           # nothing left queued
    assert match.duel is None


def test_a_lobby_is_cancelled_not_ended(engine):
    match, host = lobby(engine)
    result = engine.host_end_session(match, host.id)
    assert not result.ok and "no match is running" in result.error


def test_only_the_host_ends(engine):
    match, members, _ = full_match(engine)
    other = members["bravo"][0]
    result = engine.host_end_session(match, other.id)
    assert not result.ok and "only the host" in result.error
    assert match.status == "active"


def test_a_vanished_host_can_be_replaced_mid_match_so_it_can_be_ended(engine):
    """The host holds the only control that stops a session, so it must not
    leave with them when they close the tab."""
    match, members, _ = full_match(engine)
    host = match.players[match.host_player_id]
    engine.on_disconnect(match, host.id)
    heir = members["bravo"][0]
    assert engine.claim_host(match, heir.id).ok
    assert match.host_player_id == heir.id
    assert engine.host_end_session(match, heir.id).ok


def test_a_connected_host_still_cannot_be_deposed_mid_match(engine):
    match, members, _ = full_match(engine)
    result = engine.claim_host(match, members["bravo"][0].id)
    assert not result.ok and "still here" in result.error


# --- leaving, and the host seat passing on --------------------------------

def test_a_player_leaves_the_lobby(engine):
    match, host = lobby(engine)
    bob, _ = engine.join_match(match, "Bob", "alpha", now=NOW)
    result = engine.leave_match(match, bob.id)
    assert result.ok and bob.id not in match.players
    assert bob.id not in match.teams["alpha"].player_ids
    assert result.kicked_player_ids == [bob.id]


def test_a_grandmaster_can_step_back_down_in_the_lobby(engine):
    """Claiming used to be one-way: the only ways out of the seat were being
    kicked or leaving and rejoining. Letting go is the other half of it."""
    match, host = lobby(engine)
    bob, _ = engine.join_match(match, "Bob", "alpha", now=NOW)
    assert engine.claim_leader(match, bob.id).ok
    assert engine.release_leader(match, bob.id).ok
    assert match.teams["alpha"].leader_id is None
    assert bob.is_leader is False
    # And the seat is genuinely free again, not just unattributed.
    assert engine.claim_leader(match, host.id).ok
    assert match.teams["alpha"].leader_id == host.id


def test_releasing_the_seat_blocks_the_start_rather_than_stranding_it(engine):
    """Safe precisely because the lobby has not started: the existing blocker
    already refuses a team with no Grandmaster, so this delays a match rather
    than breaking one."""
    match, host = lobby(engine)
    bob, _ = engine.join_match(match, "Bob", "alpha", now=NOW)
    assert engine.claim_leader(match, bob.id).ok
    assert engine.release_leader(match, bob.id).ok
    blocker = engine.start_blocker(match)
    assert blocker and "Grandmaster" in blocker


def test_only_the_holder_can_release_the_seat(engine):
    match, host = lobby(engine)
    bob, _ = engine.join_match(match, "Bob", "alpha", now=NOW)
    assert engine.claim_leader(match, bob.id).ok
    denied = engine.release_leader(match, host.id)
    assert not denied.ok
    assert match.teams["alpha"].leader_id == bob.id


def test_the_seat_cannot_be_released_once_the_race_is_on(engine):
    """Mid-match the seat changes hands through `give_leader`, which costs the
    recipient their cleared status. Walking out of it and leaving the team with
    nobody calling the plays is not the same thing."""
    match, host = lobby(engine)
    bob, _ = engine.join_match(match, "Bob", "alpha", now=NOW)
    assert engine.claim_leader(match, bob.id).ok
    match.status = "active"
    denied = engine.release_leader(match, bob.id)
    assert not denied.ok
    assert match.teams["alpha"].leader_id == bob.id


def test_leaving_frees_the_grandmaster_seat(engine):
    match, host = lobby(engine)
    bob, _ = engine.join_match(match, "Bob", "alpha", now=NOW)
    assert engine.claim_leader(match, bob.id).ok
    assert engine.leave_match(match, bob.id).ok
    assert match.teams["alpha"].leader_id is None


def test_the_host_leaving_hands_the_seat_to_someone_still_here(engine):
    match, host = lobby(engine)
    bob, _ = engine.join_match(match, "Bob", "alpha", now=NOW)
    result = engine.leave_match(match, host.id)
    assert result.ok and match.host_player_id == bob.id
    assert "is now hosting" in result.events[-1].message


def test_the_seat_prefers_a_player_who_has_taken_a_team(engine):
    match, host = lobby(engine)
    engine.join_match(match, "Drifter", now=NOW)                # joined first, no team
    seated, _ = engine.join_match(match, "Seated", "bravo", now=NOW)
    assert engine.leave_match(match, host.id).ok
    assert match.host_player_id == seated.id


def test_the_seat_prefers_someone_still_connected(engine):
    match, host = lobby(engine)
    gone, _ = engine.join_match(match, "Gone", "alpha", now=NOW)
    here, _ = engine.join_match(match, "Here", "alpha", now=NOW)
    engine.on_disconnect(match, gone.id)
    assert engine.leave_match(match, host.id).ok
    assert match.host_player_id == here.id


def test_otherwise_the_seat_goes_to_whoever_got_here_first(engine):
    """Join order, not alphabetical — a reason a player can follow."""
    match, host = lobby(engine)
    first, _ = engine.join_match(match, "Zoe", "alpha", now=NOW)
    engine.join_match(match, "Abe", "alpha", now=NOW)
    assert engine.leave_match(match, host.id).ok
    assert match.host_player_id == first.id


def test_the_last_player_leaving_leaves_no_host(engine):
    match, host = lobby(engine)
    assert engine.leave_match(match, host.id).ok
    assert match.host_player_id is None and not match.players


def test_the_next_joiner_of_an_empty_lobby_hosts_it(engine):
    match, host = lobby(engine)
    engine.leave_match(match, host.id)
    heir, _ = engine.join_match(match, "Bob", "alpha", now=NOW)
    assert match.host_player_id == heir.id


def test_you_cannot_leave_a_running_match(engine):
    """Their team is racing against a roster size that counts them."""
    match, members, _ = full_match(engine)
    result = engine.leave_match(match, members["alpha"][0].id)
    assert not result.ok and "ask the host to end it" in result.error
    assert members["alpha"][0].id in match.players


# --- the lobby's cross-team mask -----------------------------------------

def test_the_lobby_shows_both_rosters_and_both_sets_of_roles(engine):
    """You can see who is on the other side and what they are — that is how
    you tell whether the sides are fair."""
    match, members, _ = _assigned_lobby(engine)
    view = match.public(members["alpha"][0].id)
    other = view["teams"]["bravo"]
    assert [p["name"] for p in other["players"]]
    assert all(p["role"] for p in other["players"] if not p["is_leader"])


def test_the_lobby_masks_the_other_teams_games(engine):
    match, members, _ = _assigned_lobby(engine)
    view = match.public(members["alpha"][0].id)
    other = view["teams"]["bravo"]
    assert all(p["assigned_game"] is None for p in other["players"])


def test_readiness_survives_the_mask(engine):
    """Which game is hidden; whether they have one is not, or the start
    blocker would read as a bug on a lobby that is ready to go."""
    match, members, _ = _assigned_lobby(engine)
    other = match.public(members["alpha"][0].id)["teams"]["bravo"]
    playing = [p for p in other["players"] if not p["is_leader"]]
    assert playing and all(p["has_game"] for p in playing)


def test_your_own_team_keeps_its_games(engine):
    match, members, _ = _assigned_lobby(engine)
    mine = match.public(members["alpha"][0].id)["teams"]["alpha"]
    playing = [p for p in mine["players"] if not p["is_leader"]]
    assert all(p["assigned_game"] is not None for p in playing)


def test_a_spectator_with_no_seat_sees_no_games_at_all(engine):
    """`public()` with no player is the anonymous view — it belongs to nobody,
    so it gets neither loadout."""
    match, _, _ = _assigned_lobby(engine)
    for team in match.public()["teams"].values():
        assert all(p["assigned_game"] is None for p in team["players"])


def _assigned_lobby(engine: RelayEngine):
    """A lobby with both teams staffed, rolled and assigned — but not started."""
    match = engine.create_match()
    members: dict[str, list] = {"alpha": [], "bravo": []}
    leaders: dict[str, object] = {}
    for team_id in ("alpha", "bravo"):
        leader, _ = engine.join_match(match, f"{team_id}-lead", team_id, now=NOW)
        assert engine.claim_leader(match, leader.id).ok
        leaders[team_id] = leader
        for i in range(2):
            player, _ = engine.join_match(
                match, f"{team_id[0].upper()}{i}", team_id, now=NOW
            )
            members[team_id].append(player)
            assert engine.assign_role(match, leader.id, player.id, "generalist").ok
            assert engine.assign_game(match, leader.id, player.id, GAMES[i]).ok
    return match, members, leaders


# --- the host sets how long the race is ----------------------------------

def test_a_fresh_match_opens_at_the_default_length(engine):
    assert engine.create_match().level_count == config.LEVEL_COUNT


def test_the_host_sets_the_round_count(engine):
    match, host = lobby(engine)
    assert engine.host_set_level_count(match, host.id, 5).ok
    assert match.level_count == 5


@pytest.mark.parametrize("value", [0, 2, 99])
def test_the_round_count_is_bounded(engine, value):
    match, host = lobby(engine)
    result = engine.host_set_level_count(match, host.id, value)
    low, high = config.MIN_LEVEL_COUNT, config.max_level_count()
    assert not result.ok and f"{low}..{high}" in result.error


def test_the_shortest_race_is_three_rounds(engine):
    match, host = lobby(engine)
    assert engine.host_set_level_count(match, host.id, 6).ok
    assert engine.host_set_level_count(match, host.id, config.MIN_LEVEL_COUNT).ok
    assert not engine.host_set_level_count(
        match, host.id, config.MIN_LEVEL_COUNT - 1
    ).ok


def test_setting_the_length_it_already_is_says_so(engine):
    match, host = lobby(engine)
    result = engine.host_set_level_count(match, host.id, match.level_count)
    assert not result.ok and "already" in result.error


def test_the_longest_race_leaves_the_bonus_its_headroom(engine):
    """The finale still owes a bonus board harder than itself, so the top
    BONUS_LEVEL_OFFSET rungs of the table are never main boards."""
    assert config.max_level_count() == (
        config.DIFFICULTY_TIERS - config.BONUS_LEVEL_OFFSET
    )
    match, host = lobby(engine)
    assert engine.host_set_level_count(match, host.id, config.max_level_count()).ok
    assert not engine.host_set_level_count(
        match, host.id, config.max_level_count() + 1
    ).ok


def test_only_the_host_sets_the_length(engine):
    match, host = lobby(engine)
    other, _ = engine.join_match(match, "Bob", "bravo", now=NOW)
    result = engine.host_set_level_count(match, other.id, 5)
    assert not result.ok and "only the host" in result.error


def test_a_running_match_does_not_change_length(engine):
    match, _, _ = full_match(engine)
    result = engine.host_set_level_count(match, match.host_player_id, 5)
    assert not result.ok and "already started" in result.error


# --- the difficulty curve stretches to fit -------------------------------

def test_the_full_length_race_is_one_rung_a_round(engine):
    """At the longest length the mapping is the identity — round 7 is tier 7,
    exactly as it behaved before the length was settable."""
    top = config.max_level_count()
    assert [config.difficulty_tier(r, top) for r in range(1, top + 1)] == \
        list(range(1, top + 1))


@pytest.mark.parametrize("rounds", range(3, 11))
def test_every_length_starts_at_the_bottom_and_ends_at_the_top(rounds):
    tiers = [config.difficulty_tier(r, rounds) for r in range(1, rounds + 1)]
    assert tiers[0] == 1
    assert tiers[-1] == config.max_level_count()


@pytest.mark.parametrize("rounds", range(3, 11))
def test_the_curve_never_goes_backwards(rounds):
    tiers = [config.difficulty_tier(r, rounds) for r in range(1, rounds + 1)]
    assert tiers == sorted(tiers)


def test_the_short_race_curve_is_the_agreed_one(engine):
    assert [config.difficulty_tier(r, 3) for r in (1, 2, 3)] == [1, 5, 10]
    assert [config.difficulty_tier(r, 4) for r in (1, 2, 3, 4)] == [1, 4, 7, 10]


def test_a_round_outside_the_race_is_clamped(engine):
    """Defensive: a stale round number must not index off the ladder."""
    assert config.difficulty_tier(0, 5) == 1
    assert config.difficulty_tier(99, 5) == config.max_level_count()


def test_a_short_races_finale_bonus_still_reaches_the_top_rung(engine, monkeypatch):
    """The point of the stretch: three rounds is quicker, not easier."""
    monkeypatch.setattr(config, "LEVEL_COUNT", 3)
    match, members, _ = full_match(engine)
    team = match.teams["alpha"]
    player = members["alpha"][0]
    team.level = 3                       # the finale
    assert engine._difficulty_tier(match, 3) == config.max_level_count()
    assert engine._bonus_level(match, team) == config.DIFFICULTY_TIERS


# --- the duel round timer -------------------------------------------------
#
# Every duel game declares its own choice window; one host setting overrides
# all of them, so a group can make every duel move at the pace they want.

def test_a_fresh_match_leaves_every_duel_its_own_pace(engine):
    assert engine.create_match().duel_round_seconds is None


def test_the_host_sets_the_duel_round_window(engine):
    match, host = lobby(engine)
    assert engine.host_set_duel_seconds(match, host.id, 5).ok
    assert match.duel_round_seconds == 5


def test_zero_hands_every_duel_its_own_window_back(engine):
    match, host = lobby(engine)
    assert engine.host_set_duel_seconds(match, host.id, 5).ok
    assert engine.host_set_duel_seconds(match, host.id, 0).ok
    assert match.duel_round_seconds is None


@pytest.mark.parametrize("value", [
    1, 2, 31, 999, -5, "8", 8.0, True, None, [], {"value": 8},
])
def test_the_duel_round_window_is_bounded(engine, value):
    match, host = lobby(engine)
    if value is None:
        # None is the same "each game's own" reset as 0, not a rejection.
        assert engine.host_set_duel_seconds(match, host.id, 5).ok
        assert engine.host_set_duel_seconds(match, host.id, None).ok
        assert match.duel_round_seconds is None
        return
    result = engine.host_set_duel_seconds(match, host.id, value)
    low, high = config.DUEL_ROUND_SECONDS_MIN, config.DUEL_ROUND_SECONDS_MAX
    assert not result.ok and f"{low}..{high}" in result.error
    assert match.duel_round_seconds is None


@pytest.mark.parametrize("value", list(config.DUEL_ROUND_SECONDS_CHOICES))
def test_every_offered_window_is_inside_the_bounds(engine, value):
    """The picker the client draws must not offer a value the server refuses."""
    match, host = lobby(engine)
    assert engine.host_set_duel_seconds(match, host.id, value).ok


def test_setting_the_window_it_already_is_says_so(engine):
    match, host = lobby(engine)
    assert engine.host_set_duel_seconds(match, host.id, 8).ok
    result = engine.host_set_duel_seconds(match, host.id, 8)
    assert not result.ok and "already" in result.error


def test_only_the_host_sets_the_duel_round_window(engine):
    match, host = lobby(engine)
    other, _ = engine.join_match(match, "Bob", "bravo", now=NOW)
    result = engine.host_set_duel_seconds(match, other.id, 5)
    assert not result.ok and "only the host" in result.error


def test_a_running_match_does_not_change_the_duel_round_window(engine):
    """A window that could move mid-duel would change the clock under a
    Duelist who is already choosing."""
    match, _, _ = full_match(engine)
    result = engine.host_set_duel_seconds(match, match.host_player_id, 5)
    assert not result.ok and "already started" in result.error
