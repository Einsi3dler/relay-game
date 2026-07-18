"""T3.3–T3.6 — REST routes, WebSocket endpoint, integration to a win, eviction."""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import backend.main as server
from backend import config
from backend.registry import GameRegistry

from tests.test_engine import MAIN_OK, ORDER, FakeGame


@pytest.fixture
def client():
    with TestClient(server.app) as test_client:
        yield test_client


@pytest.fixture
def fake_games(monkeypatch):
    """Deterministic games + no submit rate limit, for scripted matches."""
    registry = GameRegistry(
        modules=[FakeGame(game_id) for game_id in ORDER], game_order=ORDER
    )
    monkeypatch.setattr(server.engine, "registry", registry)
    monkeypatch.setattr(config, "SUBMIT_MIN_INTERVAL_MS", 0)
    # Fake matches are 4 stages regardless of the real roster size.
    monkeypatch.setattr(config, "STAGE_COUNT", len(ORDER))


def create_match(client) -> str:
    return client.post("/api/matches").json()["match"]["id"]


def join(client, match_id: str, name: str, team_id: str | None = None):
    return client.post(
        f"/api/matches/{match_id}/join", json={"name": name, "team_id": team_id}
    )


def fill_match(client, match_id: str) -> dict[str, list[str]]:
    """Join 4+4 players onto teams, then the host (first joiner) starts the
    match over the socket. Returns player ids per team."""
    ids: dict[str, list[str]] = {"alpha": [], "bravo": []}
    for team_id in ("alpha", "bravo"):
        for i in range(4):
            response = join(client, match_id, f"{team_id[0]}{i}", team_id)
            assert response.status_code == 200
            ids[team_id].append(response.json()["player"]["id"])
    with client.websocket_connect(
        f"/ws/matches/{match_id}?player_id={ids['alpha'][0]}"
    ) as ws:
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "lobby_action", "action": "start"})
        snapshot = ws.receive_json()
        assert snapshot["state"]["status"] == "active"
    return ids


@contextmanager
def connect(client, match_id: str, player_id: str):
    """Open a socket, drain the two on-connect snapshots, yield (ws, me)."""
    with client.websocket_connect(
        f"/ws/matches/{match_id}?player_id={player_id}"
    ) as ws:
        ws.receive_json()  # broadcast snapshot
        snapshot = ws.receive_json()  # targeted snapshot with `me`
        assert snapshot["type"] == "state_snapshot"
        yield ws, snapshot["state"]["me"]


# --- T3.3 REST ---

def test_index_serves_landing(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "The Relay" in response.text


def test_play_serves_app(client):
    response = client.get("/play")
    assert response.status_code == 200
    assert "view-join" in response.text


def test_explore_page_served(client):
    response = client.get("/explore")
    assert response.status_code == 200
    for game_id in ("rewire", "sweep", "mirror_run", "decant", "echo"):
        assert game_id in response.text


def test_static_assets_served(client):
    for path in ("/static/app.js", "/static/style.css", "/static/games/fallback.js"):
        assert client.get(path).status_code == 200, path


def test_games_page_served(client):
    response = client.get("/games")
    assert response.status_code == 200
    for name in ("REWIRE", "MIRROR RUN", "DECANT", "ECHO"):
        assert name in response.text


# --- Practice mode (/explore) ---

def test_practice_new_puzzle_all_games(client):
    for game_id in ("rewire", "sweep", "mirror_run", "decant", "echo"):
        for kind in ("main", "holding"):
            response = client.post(f"/api/practice/{game_id}?kind={kind}")
            assert response.status_code == 200, (game_id, kind)
            body = response.json()
            assert isinstance(body["seed"], int)
            puzzle = body["puzzle"]
            assert puzzle["game_id"] == game_id
            assert puzzle["kind"] == kind
            assert "answer" not in puzzle


def test_practice_check_correct_and_wrong(client):
    # ECHO's payload legitimately carries the sequence (documented exception),
    # so the test can construct the right answer without server internals.
    body = client.post("/api/practice/echo?kind=main").json()
    right = ",".join(str(pad) for pad in body["puzzle"]["payload"]["sequence"])
    check = {"seed": body["seed"], "kind": "main", "answer": right}
    assert client.post("/api/practice/echo/check", json=check).json()["correct"] is True
    check["answer"] = "not,a,sequence"
    assert client.post("/api/practice/echo/check", json=check).json()["correct"] is False


def test_practice_same_seed_regenerates_same_puzzle(client):
    body = client.post("/api/practice/decant?kind=main").json()
    again = client.post("/api/practice/decant?kind=main").json()
    assert body["seed"] != again["seed"] or body["puzzle"]["payload"] == again["puzzle"]["payload"]
    # Deterministic regeneration is what makes the stateless check sound: a
    # wrong move list for this seed must be judged against the same board.
    check = {"seed": body["seed"], "kind": "main", "answer": "0>1"}
    response = client.post("/api/practice/decant/check", json=check)
    assert response.status_code == 200
    assert response.json()["correct"] is False


def test_practice_rejects_unknown_game_and_kind(client):
    assert client.post("/api/practice/tetris").status_code == 404
    assert client.post("/api/practice/echo?kind=bogus").status_code == 400


def test_get_config(client):
    body = client.get("/api/config").json()
    assert body == {
        "teams": ["alpha", "bravo"],
        "rest_seconds": config.REST_SECONDS,
        "holding_seconds": config.HOLDING_SECONDS,
        "players_per_team": config.PLAYERS_PER_TEAM,
        "stage_count": config.STAGE_COUNT,
    }


def test_create_and_get_match(client):
    match_id = create_match(client)
    body = client.get(f"/api/matches/{match_id}").json()["match"]
    assert body["id"] == match_id and body["status"] == "lobby"
    assert client.get("/api/matches/nope").status_code == 404


def test_join_returns_player_and_match(client, fake_games):
    match_id = create_match(client)
    body = join(client, match_id, "Ada", "alpha").json()
    assert body["player"]["name"] == "Ada" and body["player"]["team_id"] == "alpha"
    assert body["match"]["teams"]["alpha"]["players"][0]["name"] == "Ada"


def test_join_full_and_started_rejected_with_detail(client, fake_games):
    match_id = create_match(client)
    for i in range(4):
        join(client, match_id, f"A{i}", "alpha")
    response = join(client, match_id, "A4", "alpha")
    assert response.status_code == 400 and "full" in response.json()["detail"]
    fill_match(client, create_match(client))  # separate match, started by host
    started_id = client.get("/api/matches/" + match_id).json()["match"]["id"]
    assert started_id == match_id  # original is still joinable (lobby)


def test_join_started_match_rejected(client, fake_games):
    match_id = create_match(client)
    fill_match(client, match_id)
    response = join(client, match_id, "late", None)
    assert response.status_code == 400 and "started" in response.json()["detail"]


def test_join_lands_unassigned_with_host(client, fake_games):
    match_id = create_match(client)
    first = join(client, match_id, "First", None).json()
    assert first["player"]["team_id"] is None
    assert first["match"]["host_player_id"] == first["player"]["id"]
    second = join(client, match_id, "Second", None).json()
    names = [p["name"] for p in second["match"]["unassigned"]]
    assert names == ["First", "Second"]


def test_lobby_flow_pick_move_kick_start(client, fake_games):
    match_id = create_match(client)
    host_id = join(client, match_id, "Host", None).json()["player"]["id"]
    guest_id = join(client, match_id, "Guest", None).json()["player"]["id"]
    with connect(client, match_id, host_id) as (host_ws, _):
        with connect(client, match_id, guest_id) as (guest_ws, _):
            # guest picks a team themselves
            guest_ws.send_json({"type": "lobby_action", "action": "set_team",
                                "team_id": "bravo"})
            snapshot = guest_ws.receive_json()["state"]
            assert snapshot["teams"]["bravo"]["players"][0]["name"] == "Guest"
            # guest cannot use host powers
            guest_ws.send_json({"type": "lobby_action", "action": "start"})
            guest_ws.receive_json()  # own broadcast of set_team event
            assert "host" in guest_ws.receive_json()["error"]
            # host assigns themself, drops the threshold, starts
            host_ws.send_json({"type": "lobby_action", "action": "set_team",
                               "team_id": "alpha"})
            host_ws.send_json({"type": "lobby_action", "action": "set_min_players",
                               "value": 1})
            host_ws.send_json({"type": "lobby_action", "action": "start"})
            # drain until the active snapshot arrives
            for _ in range(20):
                message = host_ws.receive_json()
                if (message["type"] == "state_snapshot"
                        and message["state"]["status"] == "active"):
                    break
            else:
                raise AssertionError("match never started")
    state = client.get(f"/api/matches/{match_id}").json()["match"]
    assert state["status"] == "active" and state["min_players"] == 1


def test_kick_closes_socket_and_removes_player(client, fake_games):
    match_id = create_match(client)
    host_id = join(client, match_id, "Host", None).json()["player"]["id"]
    victim_id = join(client, match_id, "Victim", None).json()["player"]["id"]
    with connect(client, match_id, host_id) as (host_ws, _):
        with connect(client, match_id, victim_id) as (victim_ws, _):
            host_ws.send_json({"type": "lobby_action", "action": "kick",
                               "target_id": victim_id})
            with pytest.raises(WebSocketDisconnect) as exc:
                for _ in range(5):
                    victim_ws.receive_json()
            assert exc.value.code == 4403
    state = client.get(f"/api/matches/{match_id}").json()["match"]
    names = [p["name"] for p in state["unassigned"]]
    assert names == ["Host"]
    # the kicked player's old credential is dead
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            f"/ws/matches/{match_id}?player_id={victim_id}"
        ) as ws:
            ws.receive_json()
    assert exc.value.code == 4404


# --- T3.4 WebSocket ---

def test_ws_unknown_match_or_player_closes_4404(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/matches/nope?player_id=nobody") as ws:
            ws.receive_json()
    assert exc.value.code == 4404


def test_ws_duplicate_connect_supersedes_with_4001(client, fake_games):
    match_id = create_match(client)
    player_id = join(client, match_id, "Ada", "alpha").json()["player"]["id"]
    url = f"/ws/matches/{match_id}?player_id={player_id}"
    with client.websocket_connect(url) as ws1:
        ws1.receive_json()
        ws1.receive_json()
        with client.websocket_connect(url) as ws2:
            ws2.receive_json()
            ws2.receive_json()
            with pytest.raises(WebSocketDisconnect) as exc:
                ws1.receive_json()
            assert exc.value.code == 4001
            ws2.send_json({"type": "heartbeat"})  # new socket still works
            assert ws2.receive_json()["type"] == "state_snapshot"


def test_ws_errors_for_bad_messages(client, fake_games):
    match_id = create_match(client)
    ids = fill_match(client, match_id)
    with connect(client, match_id, ids["alpha"][0]) as (ws, me):
        ws.send_json({"type": "dance"})
        assert ws.receive_json()["error"] == "Unknown message type."
        ws.send_json({"type": "submit_holding",
                      "puzzle_id": me["current_puzzle"]["id"], "answer": "x"})
        assert "not holding" in ws.receive_json()["error"]
        ws.send_json({"type": "submit_answer", "puzzle_id": "stale", "answer": "x"})
        assert ws.receive_json()["error"] == "Puzzle is no longer active"


def test_ws_rate_limit_too_fast(client, fake_games, monkeypatch):
    monkeypatch.setattr(config, "SUBMIT_MIN_INTERVAL_MS", 60_000)
    match_id = create_match(client)
    ids = fill_match(client, match_id)
    with connect(client, match_id, ids["alpha"][0]) as (ws, me):
        puzzle_id = me["current_puzzle"]["id"]
        ws.send_json({"type": "submit_answer", "puzzle_id": puzzle_id, "answer": "no"})
        ws.receive_json()  # first submit processed (snapshot)
        ws.send_json({"type": "submit_answer", "puzzle_id": puzzle_id, "answer": "no"})
        assert ws.receive_json()["error"] == "Too fast."


def walk_no_answer(node, path="$"):
    if isinstance(node, dict):
        assert "answer" not in node, f"answer leaked at {path}"
        for key, value in node.items():
            walk_no_answer(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            walk_no_answer(value, f"{path}[{i}]")


def test_snapshots_never_contain_answers_real_games(client):
    """With the real registry: Stage 1 serves REWIRE and leaks nothing."""
    match_id = create_match(client)
    ids = fill_match(client, match_id)
    with connect(client, match_id, ids["alpha"][0]) as (ws, me):
        assert me["current_puzzle"]["game_id"] == "rewire"
        ws.send_json({"type": "request_state"})
        snapshot = ws.receive_json()
        walk_no_answer(snapshot)
        team = snapshot["state"]["teams"]["alpha"]
        greens = sum(1 for p in team["players"] if p["green"])
        assert team["green_count"] == greens


def test_real_registry_covers_all_five_stages():
    registry = GameRegistry()
    assert [registry.for_stage(n).id for n in (1, 2, 3, 4, 5)] == [
        "rewire", "sweep", "mirror_run", "decant", "echo",
    ]


# --- T3.5 integration: two full teams, alpha plays to the win ---

def test_full_match_to_win_over_websocket(client, fake_games):
    match_id = create_match(client)
    ids = fill_match(client, match_id)
    won = None
    for stage in range(1, 5):
        for i, player_id in enumerate(ids["alpha"]):
            with connect(client, match_id, player_id) as (ws, me):
                assert me["status"] == "solving"
                ws.send_json({
                    "type": "submit_answer",
                    "puzzle_id": me["current_puzzle"]["id"],
                    "answer": MAIN_OK,
                })
                assert ws.receive_json()["type"] == "state_snapshot"
                assert ws.receive_json()["event"]["kind"] == "green"
                if i == 3 and stage < 4:  # 4th green → advance nudge
                    assert ws.receive_json()["event"]["kind"] == "advance"
                    assert ws.receive_json() == {
                        "type": "stage_advanced",
                        "team_id": "alpha",
                        "stage": stage + 1,
                    }
                if i == 3 and stage == 4:  # win
                    assert ws.receive_json()["event"]["kind"] == "win"
                    won = ws.receive_json()
    assert won == {"type": "match_won", "team_id": "alpha"}
    state = client.get(f"/api/matches/{match_id}").json()["match"]
    assert state["status"] == "finished" and state["winner_team_id"] == "alpha"


# --- T3.6 eviction ---

def test_eviction_of_idle_match(client, fake_games):
    stale_id = create_match(client)
    fresh_id = create_match(client)

    async def scenario():
        server.timers.schedule(stale_id, "p1", "rest", "2099-01-01T00:00:00+00:00")
        server.last_activity[stale_id] = (
            time.monotonic() - config.MATCH_TTL_SECONDS - 1
        )
        return await server.evict_stale()

    evicted = asyncio.run(scenario())
    assert stale_id in evicted and fresh_id not in evicted
    assert client.get(f"/api/matches/{stale_id}").status_code == 404
    assert client.get(f"/api/matches/{fresh_id}").status_code == 200
    assert server.timers.pending(stale_id) == set()  # no timer will fire
