"""Model `.public()` shapes for v2 (leader-exclusive visibility) — no answer leaks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from backend.games.base import PuzzleInstance
from backend.games.duel1_rps import RockPaperScissorsDuel
from backend.games.duel_base import DuelState
from backend.models import (
    DuelSession, Event, Match, Observer, PendingStake, Player, Team, green,
)


def _future(seconds: int = 30) -> str:
    """A deadline the view layer will read as still running. View-layer checks
    use the wall clock, so these can't be pinned to a fixed test instant."""
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def make_puzzle(kind: str = "main", game_id: str = "rewire") -> PuzzleInstance:
    return PuzzleInstance(
        game_id=game_id,
        kind=kind,
        prompt="Rotate the tiles so power reaches every sink.",
        answer="SECRET-solution",
        payload={"rows": 4, "cols": 4},
    )


def make_match(status: str = "active") -> Match:
    players = {
        "p_lead": Player(
            id="p_lead", name="Lena", team_id="alpha", status="leading",
            rejoin_code="LEAD11",
            connected=True, is_leader=True,
        ),
        "p_alice": Player(
            id="p_alice", name="Alice", team_id="alpha", status="solving",
            rejoin_code="ALIC22",
            connected=True, assigned_game="rewire", current_main=make_puzzle("main"),
        ),
        "p_bob": Player(
            id="p_bob", name="Bob", team_id="alpha", status="bonus",
            rejoin_code="BOBB33",
            connected=True, assigned_game="sweep",
            current_main=None, current_bonus=make_puzzle("main", "sweep"),
            timer_kind="wait", timer_deadline="2026-07-02T12:03:00+00:00",
        ),
        "p_cara": Player(
            id="p_cara", name="Cara", team_id="bravo", status="cleared",
            rejoin_code="CARA44",
            connected=False, assigned_game="echo", choice_pending=True,
            timer_kind="wait", timer_deadline="2026-07-02T12:03:00+00:00",
        ),
        "p_dave": Player(
            id="p_dave", name="Dave", team_id="bravo", status="solving",
            rejoin_code="DAVE55",
            connected=True, assigned_game="decant", is_leader=False,
            current_main=make_puzzle("main", "decant"),
            frozen_until="2026-07-02T12:00:10+00:00",
        ),
    }
    teams = {
        "alpha": Team(id="alpha", name="Alpha", level=2, roster_size=2,
                      player_ids=["p_lead", "p_alice", "p_bob"],
                      leader_id="p_lead", currency=5, shield_active=True),
        "bravo": Team(id="bravo", name="Bravo", level=1, roster_size=2,
                      player_ids=["p_cara", "p_dave"]),
    }
    return Match(
        id="m1", status=status, teams=teams, players=players,
        events=[Event(message="Alice joined.", kind="join")],
        config_snapshot={"wait_seconds": 180, "level_count": 10,
                         "players_per_team": 4},
    )


def walk_no_answer(node: Any, path: str = "$") -> None:
    """Recursively assert no dict anywhere contains an `answer` key."""
    if isinstance(node, dict):
        assert "answer" not in node, f"answer leaked at {path}"
        for key, value in node.items():
            walk_no_answer(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            walk_no_answer(value, f"{path}[{i}]")


# --- the AC test: no `answer` field is ever present in any .public() output ---

def test_no_answer_anywhere_in_public_output():
    for status in ("lobby", "active"):
        match = make_match(status)
        walk_no_answer(match.public())
        for player_id in match.players:
            walk_no_answer(match.public(player_id))
        # The God view lifts every mask there is, so it is the one most worth
        # walking: if an answer can reach a client at all, it reaches this one.
        match.observers["g_1"] = Observer(id="g_1")
        walk_no_answer(match.public("g_1"))
    walk_no_answer(make_puzzle().public())


# --- match & team visibility ---

def test_match_public_shape():
    out = make_match().public()
    assert set(out) == {"id", "status", "host_player_id", "min_players",
                        "max_players", "level_count", "duel_round_seconds",
                        "ended_reason", "winner_team_id", "config", "teams",
                        "unassigned", "events", "duel", "pending_stake", "me",
                        "god"}
    assert out["duel"] is None  # no Duelists in this fixture
    assert out["pending_stake"] is None  # and nothing being funded
    assert out["status"] == "active"
    assert out["winner_team_id"] is None
    assert out["config"]["wait_seconds"] == 180
    assert set(out["teams"]) == {"alpha", "bravo"}
    assert out["unassigned"] == []  # everyone in the fixture has a team
    assert out["me"] is None  # no requesting player


def test_lobby_shows_full_teams_to_everyone():
    out = make_match("lobby").public("p_alice")
    for team in out["teams"].values():
        assert "players" in team and "currency" in team


def test_leader_sees_own_team_full_and_opponent_summary():
    out = make_match().public("p_lead")
    own = out["teams"]["alpha"]
    assert set(own) == {"id", "name", "level", "roster_size", "finished",
                        "green_count", "currency", "shield_active",
                        "reflect_active", "insurance_active", "silenced_until",
                        "leader_id", "duel_streak", "duel_penalty_until",
                        "players"}
    assert own["currency"] == 5 and own["shield_active"] is True
    assert [p["id"] for p in own["players"]] == ["p_lead", "p_alice", "p_bob"]
    opponent = out["teams"]["bravo"]
    assert set(opponent) == {"id", "name", "level", "roster_size", "finished",
                             "green_count", "duel_penalty_until"}
    assert opponent["green_count"] == 1  # Cara is cleared
    assert opponent["level"] == 1


def test_a_finished_match_shows_both_teams_in_full_to_everyone():
    """Fog of war exists so neither side can scout the other while it still
    matters. Once the match is over there is nothing left to protect, and the
    result screen has to be able to name what the teams actually did."""
    match = make_match()
    match.status = "finished"
    match.winner_team_id = "alpha"
    # A plain player, not a Grandmaster: the seat that saw the least during the
    # race is the one this rule is for.
    out = match.public("p_alice")
    for team_id in ("alpha", "bravo"):
        team = out["teams"][team_id]
        assert "players" in team, team_id
        assert "currency" in team, team_id
        assert team["level"] >= 1
    # Including the opponent's roster and what each of them put in the purse.
    assert [p["name"] for p in out["teams"]["bravo"]["players"]] == ["Cara", "Dave"]
    assert all("coins_earned" in p for p in out["teams"]["bravo"]["players"])


def test_the_fog_holds_right_up_until_the_match_ends():
    """The lift is keyed on `finished` and nothing else, so an active match is
    unaffected by it — a player still sees their own team as a summary and the
    opponent as a name."""
    out = make_match("active").public("p_alice")
    assert "players" not in out["teams"]["alpha"]
    assert set(out["teams"]["bravo"]) == {"id", "name", "finished"}


def test_a_finished_match_hands_back_the_whole_event_log():
    """The who-cleared events are held back from players during the race for
    the same reason the roster is. A match nobody can still lose does not need
    them held back."""
    match = make_match()
    match.events.append(Event(message="Alice cleared Rewire", kind="green"))
    live = match.public("p_alice")["events"]
    assert not any(e["kind"] == "green" for e in live)
    match.status = "finished"
    ended = match.public("p_alice")["events"]
    assert any(e["kind"] == "green" for e in ended)


def test_silence_cannot_outlive_the_match():
    """Silence is an attack on a live Grandmaster, not on the scoreboard. A team
    silenced as the last level fell still reads out in full on the result
    screen."""
    match = make_match()
    match.teams["alpha"].silenced_until = _future()
    match.status = "finished"
    own = match.public("p_lead")["teams"]["alpha"]
    assert own["green_count"] is not None
    assert all(p["status"] != "hidden" for p in own["players"])


def test_silence_masks_the_roster_from_the_teams_own_leader():
    """The Silence perk blinds a Grandmaster to their OWN team. The shape of the
    view is unchanged — the progress values go null so the client can render a
    "?" rather than break."""
    match = make_match()
    match.teams["alpha"].silenced_until = _future()
    own = match.public("p_lead")["teams"]["alpha"]
    assert own["green_count"] is None
    playing = [p for p in own["players"] if not p["is_leader"]]
    assert [p["status"] for p in playing] == ["hidden", "hidden"]
    assert all(p["green"] is None for p in playing)
    # Not progress info, so it survives: the leader can still see the shop.
    assert own["currency"] == 5 and own["silenced_until"] is not None
    # The *enemy* leader keeps their read-out of the silenced team.
    match.players["p_cara"].is_leader = True
    assert match.public("p_cara")["teams"]["alpha"]["green_count"] == 0


def test_silence_also_hides_the_who_cleared_feed():
    """Otherwise the masked roster is trivially reconstructed from the log."""
    match = make_match()
    match.events = [
        Event(message="Alice cleared Level 2.", kind="green"),
        Event(message="Team Alpha used Freeze.", kind="perk"),
    ]
    assert [e["kind"] for e in match.public("p_lead")["events"]] == ["green", "perk"]
    match.teams["alpha"].silenced_until = _future()
    assert [e["kind"] for e in match.public("p_lead")["events"]] == ["perk"]


def test_a_lapsed_silence_stops_masking():
    match = make_match()
    match.teams["alpha"].silenced_until = "2020-01-01T00:00:00+00:00"
    own = match.public("p_lead")["teams"]["alpha"]
    assert own["green_count"] == 0 and own["players"][1]["status"] == "solving"


def test_screen_effects_only_report_live_deadlines():
    match = make_match()
    dave = match.players["p_dave"]
    dave.screen_effects = {"wobble": _future(), "static": "2020-01-01T00:00:00+00:00"}
    assert set(match.public("p_dave")["me"]["screen_effects"]) == {"wobble"}
    assert "screen_effects" not in dave.public()  # never in the roster view


def test_player_sees_only_own_level_and_no_opponent_progress():
    out = make_match().public("p_alice")
    own = out["teams"]["alpha"]
    assert set(own) == {"id", "name", "level", "roster_size", "finished",
                        "duel_penalty_until"}
    assert own["level"] == 2  # your level, but not who has cleared
    opponent = out["teams"]["bravo"]
    assert set(opponent) == {"id", "name", "finished"}  # nothing else


def test_anonymous_viewer_gets_summaries():
    out = make_match().public()
    for team in out["teams"].values():
        assert "players" not in team and "currency" not in team


# --- players ---

def test_player_public_shape_and_green_derivation():
    match = make_match()
    out = match.players["p_cara"].public()
    assert set(out) == {"id", "name", "team_id", "status", "green", "connected",
                        "is_leader", "role", "assigned_game", "has_game",
                        "coins_earned"}
    assert out["green"] is True and out["connected"] is False
    assert match.players["p_alice"].public()["green"] is False
    assert match.players["p_bob"].public()["green"] is False  # bonus isn't green
    assert match.players["p_lead"].public()["is_leader"] is True
    assert green(match.players["p_cara"]) is True


def test_player_private_adds_puzzle_timer_choice_freeze():
    match = make_match()
    out = match.public("p_bob")["me"]
    assert set(out) == {"id", "name", "team_id", "status", "green", "connected",
                        "is_leader", "role", "assigned_game", "has_game", "coins_earned",
                        "rejoin_code", "current_puzzle",
                        "timer_kind", "timer_deadline", "puzzle_deadline",
                        "choice_pending", "frozen_until", "screen_effects"}
    assert out["current_puzzle"]["game_id"] == "sweep"  # the bonus puzzle
    assert out["timer_kind"] == "wait"
    assert match.public("p_cara")["me"]["choice_pending"] is True
    assert match.public("p_dave")["me"]["frozen_until"] is not None


# --- rejoin codes ----------------------------------------------------------
#
# A rejoin code buys a seat, so it is a credential and not a display field.
# Exactly two views may carry one: your own `me`, and your own Grandmaster's
# roster. Every other view in this file is sent to someone who must not have it.

ALL_CODES = {"LEAD11", "ALIC22", "BOBB33", "CARA44", "DAVE55"}


def codes_in(node: Any) -> set[str]:
    """Every rejoin code appearing anywhere in a payload, at any depth."""
    found: set[str] = set()
    if isinstance(node, dict):
        for value in node.values():
            found |= codes_in(value)
    elif isinstance(node, list):
        for value in node:
            found |= codes_in(value)
    elif isinstance(node, str) and node in ALL_CODES:
        found.add(node)
    return found


def test_a_player_is_sent_their_own_rejoin_code_and_nobody_elses():
    out = make_match().public("p_alice")
    assert out["me"]["rejoin_code"] == "ALIC22"
    assert codes_in(out) == {"ALIC22"}


def test_a_grandmaster_is_sent_their_own_teams_codes():
    """The one view that carries other people's: a stranded player asks their
    Grandmaster, who reads it off the roster."""
    out = make_match().public("p_lead")
    own = {p["name"]: p["rejoin_code"] for p in out["teams"]["alpha"]["players"]}
    assert own == {"Lena": "LEAD11", "Alice": "ALIC22", "Bob": "BOBB33"}
    # Their own team and no further: the opponent summary has no roster at all.
    assert codes_in(out) == {"LEAD11", "ALIC22", "BOBB33"}


def test_the_lobby_never_carries_a_rejoin_code():
    """Everyone sees both full rosters in the lobby — which is exactly why the
    codes cannot ride along on them."""
    out = make_match("lobby").public("p_alice")
    assert [p["name"] for p in out["teams"]["alpha"]["players"]]  # rosters are there
    for team in out["teams"].values():
        assert all("rejoin_code" not in p for p in team["players"])
    assert codes_in(out) == {"ALIC22"}  # only their own, from `me`


def test_a_finished_match_never_carries_a_rejoin_code():
    """The result screen drops the fog and shows both rosters to everyone."""
    out = make_match("finished").public("p_alice")
    for team in out["teams"].values():
        assert all("rejoin_code" not in p for p in team["players"])
    assert codes_in(out) == {"ALIC22"}


def test_silence_does_not_take_the_rejoin_codes():
    """Silence blinds a Grandmaster to progress. Getting a stranded player back
    to their seat is not progress, and staying dark about it would only strand
    them further."""
    match = make_match()
    match.teams["alpha"].silenced_until = _future()
    own = match.public("p_lead")["teams"]["alpha"]
    assert own["green_count"] is None  # still blinded
    assert [p["rejoin_code"] for p in own["players"]] == \
        ["LEAD11", "ALIC22", "BOBB33"]



def test_current_puzzle_follows_status():
    match = make_match()
    assert match.public("p_alice")["me"]["current_puzzle"]["kind"] == "main"
    assert match.public("p_bob")["me"]["current_puzzle"]["game_id"] == "sweep"
    assert match.public("p_cara")["me"]["current_puzzle"] is None  # cleared
    assert match.public("p_lead")["me"]["current_puzzle"] is None  # leading


# --- events ---

def test_event_public_shape():
    out = Event(message="Ada cleared Level 2.", kind="green").public()
    assert set(out) == {"message", "kind", "created_at"}


def test_clear_events_are_leader_only_in_active_matches():
    match = make_match()
    match.events = [
        Event(message="Alice cleared Level 2.", kind="green"),
        Event(message="Bob lost cleared status.", kind="lost_green"),
        Event(message="Team Alpha used Freeze.", kind="perk"),
    ]
    leader_view = [e["kind"] for e in match.public("p_lead")["events"]]
    assert leader_view == ["green", "lost_green", "perk"]
    player_view = [e["kind"] for e in match.public("p_alice")["events"]]
    assert player_view == ["perk"]  # who cleared is leader-only knowledge


def test_events_capped_at_30():
    match = make_match()
    match.events = [Event(message=f"e{i}") for i in range(45)]
    events = match.public()["events"]
    assert len(events) == 30
    assert events[-1]["message"] == "e44"


def test_unknown_player_id_gives_no_me():
    assert make_match().public("p_nobody")["me"] is None


# --- the God view (backend/god.py) ---

def god_match(status: str = "active") -> tuple[Match, str]:
    match = make_match(status)
    match.observers["g_1"] = Observer(id="g_1", name="God")
    return match, "g_1"


def test_a_god_sees_both_teams_whole_in_every_status():
    """The lobby arm of `_team_view` asks whether a team is *mine*, and a God
    has no team — so the God branch has to come first, or the one viewer meant
    to see both loadouts would be the only one who sees neither."""
    for status in ("lobby", "active", "finished"):
        match, god_id = god_match(status)
        out = match.public(god_id)
        assert out["me"] is None
        assert out["god"] == {"id": "g_1", "name": "God"}
        for team_id in ("alpha", "bravo"):
            team = out["teams"][team_id]
            assert set(team) >= {"players", "currency", "green_count",
                                 "shield_active", "leader_id"}
            assert all(row["rejoin_code"] for row in team["players"])
        alice = out["teams"]["alpha"]["players"][1]
        assert alice["assigned_game"] == "rewire", f"masked in {status}"


def test_a_god_sees_through_silence():
    match, god_id = god_match()
    match.teams["alpha"].silenced_until = _future()

    blinded = match.public("p_lead")["teams"]["alpha"]
    assert blinded["green_count"] is None  # sanity: the mask is on

    seen = match.public(god_id)["teams"]["alpha"]
    assert seen["green_count"] is not None
    assert all(row["status"] != "hidden" for row in seen["players"])
    assert all(row["coins_earned"] is not None for row in seen["players"]
               if not row["is_leader"])


def test_a_god_keeps_the_leader_only_events():
    match, god_id = god_match()
    match.events.append(Event(message="Alice cleared.", kind="green"))
    kinds = {event["kind"] for event in match.public(god_id)["events"]}
    assert "green" in kinds
    assert "green" not in {
        event["kind"] for event in match.public("p_alice")["events"]
    }


def test_the_god_key_is_null_for_everyone_else():
    """Always present rather than appearing only on a God's own snapshot: a key
    that comes and goes is what a shape test exists to catch, and null says
    nothing about whether anyone is watching."""
    match, _ = god_match()
    for viewer in (None, "p_lead", "p_alice"):
        assert match.public(viewer)["god"] is None


def test_a_god_watches_the_duel_rather_than_seeing_through_it():
    """`side_of` is None for a God, which is already the case every duel module
    handles for a Grandmaster. Watching a duel is not seeing through it, and no
    duel module has to learn a new audience for a God to watch one."""
    match, god_id = god_match()
    match.duel = DuelSession(
        id="d1", module=RockPaperScissorsDuel(),
        state=DuelState(duel_game_id="rps_duel", choices={"a": "rock"}),
        sides={"a": "p_alice", "b": "p_cara"},
        team_of={"a": "alpha", "b": "bravo"},
    )
    view = match.public(god_id)["duel"]
    assert view is not None
    assert view["you"] is None
    assert view["choices"] == {}  # neither move, before the reveal
    assert view["locked"] == {"a": True, "b": False}
    # And a Duelist still sees their own, so the God branch changed nothing.
    assert match.public("p_alice")["duel"]["choices"] == {"a": "rock"}


def test_a_god_sees_both_sides_of_a_staked_duel():
    """Each Grandmaster is shown their own ask and nothing of the other's.
    A God is on neither side, so it gets both — its own method rather than a
    flag, because `public()` derives one side from a `Player` a God is not."""
    match, god_id = god_match()
    match.pending_stake = PendingStake(
        duel_game_id="bid_war",
        sides={"a": "p_alice", "b": "p_cara"},
        team_of={"a": "alpha", "b": "bravo"},
        asks={"a": 9, "b": 27},
        grants={"a": 5},
    )
    view = match.public(god_id)["pending_stake"]
    assert view["asks"] == {"a": 9, "b": 27}
    assert view["grants"] == {"a": 5}
    assert view["team_of"] == {"a": "alpha", "b": "bravo"}
    assert view["side"] is None and view["ask"] is None
    assert set(view) >= set(match.public("p_lead")["pending_stake"])

    # The player view is untouched: alpha's Grandmaster still learns nothing
    # about what bravo staked.
    theirs = match.public("p_lead")["pending_stake"]
    assert theirs["ask"] == 9 and 27 not in list(theirs.values())
