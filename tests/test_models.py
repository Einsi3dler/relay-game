"""Model `.public()` shapes for v2 (leader-exclusive visibility) — no answer leaks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from backend.games.base import PuzzleInstance
from backend.models import Event, Match, Player, Team, green


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
            connected=True, is_leader=True,
        ),
        "p_alice": Player(
            id="p_alice", name="Alice", team_id="alpha", status="solving",
            connected=True, assigned_game="rewire", current_main=make_puzzle("main"),
        ),
        "p_bob": Player(
            id="p_bob", name="Bob", team_id="alpha", status="bonus",
            connected=True, assigned_game="sweep",
            current_main=None, current_bonus=make_puzzle("main", "sweep"),
            timer_kind="wait", timer_deadline="2026-07-02T12:03:00+00:00",
        ),
        "p_cara": Player(
            id="p_cara", name="Cara", team_id="bravo", status="cleared",
            connected=False, assigned_game="echo", choice_pending=True,
            timer_kind="wait", timer_deadline="2026-07-02T12:03:00+00:00",
        ),
        "p_dave": Player(
            id="p_dave", name="Dave", team_id="bravo", status="solving",
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
    walk_no_answer(make_puzzle().public())


# --- match & team visibility ---

def test_match_public_shape():
    out = make_match().public()
    assert set(out) == {"id", "status", "host_player_id", "min_players",
                        "max_players", "level_count", "ended_reason",
                        "winner_team_id", "config", "teams", "unassigned",
                        "events", "duel", "me"}
    assert out["duel"] is None  # no Duelists in this fixture
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
                        "is_leader", "role", "assigned_game", "has_game"}
    assert out["green"] is True and out["connected"] is False
    assert match.players["p_alice"].public()["green"] is False
    assert match.players["p_bob"].public()["green"] is False  # bonus isn't green
    assert match.players["p_lead"].public()["is_leader"] is True
    assert green(match.players["p_cara"]) is True


def test_player_private_adds_puzzle_timer_choice_freeze():
    match = make_match()
    out = match.public("p_bob")["me"]
    assert set(out) == {"id", "name", "team_id", "status", "green", "connected",
                        "is_leader", "role", "assigned_game", "has_game",
                        "current_puzzle",
                        "timer_kind", "timer_deadline", "puzzle_deadline",
                        "choice_pending", "frozen_until", "screen_effects"}
    assert out["current_puzzle"]["game_id"] == "sweep"  # the bonus puzzle
    assert out["timer_kind"] == "wait"
    assert match.public("p_cara")["me"]["choice_pending"] is True
    assert match.public("p_dave")["me"]["frozen_until"] is not None


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
