"""T1.1 — model `.public()` shapes match WEBSOCKET_PROTOCOL.md §3 and never leak answers."""

from __future__ import annotations

from typing import Any

from backend.games.base import PuzzleInstance
from backend.models import Event, Match, Player, Team, green


def make_puzzle(kind: str = "main") -> PuzzleInstance:
    return PuzzleInstance(
        game_id="rewire",
        kind=kind,
        prompt="Rotate the tiles so power reaches every sink.",
        answer="SECRET-solution",
        payload={"rows": 4, "cols": 4},
    )


def make_match() -> Match:
    players = {
        "p_alice": Player(
            id="p_alice", name="Alice", team_id="alpha", status="solving",
            connected=True, current_main=make_puzzle("main"),
        ),
        "p_bob": Player(
            id="p_bob", name="Bob", team_id="alpha", status="holding",
            connected=True, current_main=make_puzzle("main"),
            current_holding=make_puzzle("holding"),
            timer_kind="holding", timer_deadline="2026-07-02T12:00:15+00:00",
        ),
        "p_cara": Player(
            id="p_cara", name="Cara", team_id="bravo", status="resting",
            connected=False, current_main=make_puzzle("main"),
            timer_kind="rest", timer_deadline="2026-07-02T12:00:15+00:00",
        ),
        "p_dave": Player(id="p_dave", name="Dave", team_id="bravo", status="lobby"),
    }
    teams = {
        "alpha": Team(id="alpha", name="Alpha", stage=2, roster_size=2,
                      player_ids=["p_alice", "p_bob"]),
        "bravo": Team(id="bravo", name="Bravo", stage=1, roster_size=2,
                      player_ids=["p_cara", "p_dave"]),
    }
    return Match(
        id="m1", status="active", teams=teams, players=players,
        events=[Event(message="Alice joined.", kind="join")],
        config_snapshot={"rest_seconds": 15, "holding_seconds": 20,
                         "players_per_team": 4, "stage_count": 4},
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
    match = make_match()
    walk_no_answer(match.public())
    for player_id in match.players:
        walk_no_answer(match.public(player_id))
    walk_no_answer(make_puzzle().public())


# --- shape tests against protocol §3 ---

def test_match_public_shape():
    out = make_match().public()
    assert set(out) == {"id", "status", "winner_team_id", "config", "teams",
                        "events", "me"}
    assert out["status"] == "active"
    assert out["winner_team_id"] is None
    assert out["config"]["rest_seconds"] == 15
    assert set(out["teams"]) == {"alpha", "bravo"}
    assert out["me"] is None  # no requesting player


def test_team_public_shape_and_green_count():
    out = make_match().public()["teams"]["alpha"]
    assert set(out) == {"id", "name", "stage", "roster_size", "finished",
                        "green_count", "players"}
    assert out["stage"] == 2
    assert out["green_count"] == 1  # Bob is holding; Alice is solving
    assert [p["id"] for p in out["players"]] == ["p_alice", "p_bob"]


def test_player_public_shape_and_green_derivation():
    match = make_match()
    out = match.players["p_cara"].public()
    assert set(out) == {"id", "name", "team_id", "status", "green", "connected"}
    assert out["green"] is True and out["connected"] is False
    assert match.players["p_alice"].public()["green"] is False
    assert match.players["p_dave"].public()["green"] is False
    assert green(match.players["p_bob"]) is True


def test_player_private_adds_puzzle_and_timer():
    match = make_match()
    out = match.public("p_bob")["me"]
    assert set(out) == {"id", "name", "team_id", "status", "green", "connected",
                        "current_puzzle", "timer_kind", "timer_deadline"}
    assert set(out["current_puzzle"]) == {"id", "game_id", "kind", "prompt", "payload"}
    assert out["current_puzzle"]["kind"] == "holding"
    assert out["timer_kind"] == "holding"


def test_current_puzzle_follows_status():
    match = make_match()
    assert match.public("p_alice")["me"]["current_puzzle"]["kind"] == "main"
    assert match.public("p_bob")["me"]["current_puzzle"]["kind"] == "holding"
    assert match.public("p_cara")["me"]["current_puzzle"] is None  # resting
    assert match.public("p_dave")["me"]["current_puzzle"] is None  # lobby


def test_event_public_shape():
    out = Event(message="Ada went green.", kind="green").public()
    assert set(out) == {"message", "kind", "created_at"}


def test_events_capped_at_30():
    match = make_match()
    match.events = [Event(message=f"e{i}") for i in range(45)]
    events = match.public()["events"]
    assert len(events) == 30
    assert events[-1]["message"] == "e44"


def test_unknown_player_id_gives_no_me():
    assert make_match().public("p_nobody")["me"] is None
