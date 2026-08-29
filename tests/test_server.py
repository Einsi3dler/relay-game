"""REST routes, WebSocket endpoint, integration to a win, eviction — v2."""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import backend.main as server
from backend import config, protocol
from backend.games.duel1_rps import RockPaperScissorsDuel
from backend.registry import GameRegistry

from tests.test_engine import GAMES, LEVELS, MAIN_OK, FakeGame

REAL_GAMES = ["rewire", "sweep", "mirror_run", "decant"]


@pytest.fixture
def client():
    with TestClient(server.app) as test_client:
        yield test_client


@pytest.fixture
def fake_games(monkeypatch):
    """Deterministic games + no submit rate limit, for scripted matches.

    The duel catalogue is pinned to RPS as well: the server picks a Duelist's
    game at random from everything registered, and a scripted socket test that
    sends "rock" needs to know which duel it is talking to. The other duel
    modules have their own suites under tests/games/.
    """
    registry = GameRegistry(
        modules=[FakeGame(game_id) for game_id in GAMES],
        duels=[RockPaperScissorsDuel()],
    )
    monkeypatch.setattr(server.engine, "registry", registry)
    monkeypatch.setattr(config, "SUBMIT_MIN_INTERVAL_MS", 0)
    # Fake matches are LEVELS levels regardless of the real config.
    monkeypatch.setattr(config, "LEVEL_COUNT", LEVELS)


def create_match(client) -> str:
    return client.post("/api/matches").json()["match"]["id"]


def join(client, match_id: str, name: str, team_id: str | None = None):
    return client.post(
        f"/api/matches/{match_id}/join", json={"name": name, "team_id": team_id}
    )


def fill_match(
    client, match_id: str, games=None, duelists: bool = False,
    defuser_seat: int | None = None,
) -> dict[str, list[str]]:
    """Join a leader + 4 players per team, claim seats, assign games over the
    socket, and have the host (alpha's leader) start. Returns ids per team,
    with leaders under 'alpha-lead' / 'bravo-lead'.

    `duelists` makes seat 0 of each team a Duelist — mirrored, because the
    start gate refuses a lone champion — and skips its game assignment, since
    the server picks a Duelist's game.

    `defuser_seat` makes that seat the Defuser, whose game the role fixes. It
    is only needed against the **real** registry: the Defuser is a required
    role, but the gate only bites when `bomb_defuse` is registered, so matches
    running on `fake_games` never need one."""
    games = games or GAMES[:4]
    ids: dict[str, list[str]] = {"alpha": [], "bravo": []}
    for team_id in ("alpha", "bravo"):
        response = join(client, match_id, f"{team_id}-lead", team_id)
        assert response.status_code == 200
        ids[f"{team_id}-lead"] = response.json()["player"]["id"]
        for i in range(4):
            response = join(client, match_id, f"{team_id[0]}{i}", team_id)
            assert response.status_code == 200
            ids[team_id].append(response.json()["player"]["id"])
    for team_id in ("alpha", "bravo"):
        with client.websocket_connect(
            f"/ws/matches/{match_id}?player_id={ids[f'{team_id}-lead']}"
        ) as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_json({"type": "lobby_action", "action": "claim_leader"})
            for i, player_id in enumerate(ids[team_id]):
                if duelists and i == 0:
                    ws.send_json({
                        "type": "lobby_action", "action": "assign_role",
                        "target_id": player_id, "role_id": "duelist",
                    })
                    continue
                if i == defuser_seat:
                    ws.send_json({
                        "type": "lobby_action", "action": "assign_role",
                        "target_id": player_id, "role_id": "defuser",
                    })
                    continue  # the role names the game; assign_game is refused
                ws.send_json({
                    "type": "lobby_action", "action": "assign_role",
                    "target_id": player_id, "role_id": "generalist",
                })
                ws.send_json({
                    "type": "lobby_action", "action": "assign_game",
                    "target_id": player_id, "game_id": games[i],
                })
            ws.send_json({"type": "heartbeat"})  # fence: all actions processed
            for _ in range(40):
                if ws.receive_json().get("type") == "state_snapshot":
                    pass
                break  # first reply after the queue means actions ran in order
    with client.websocket_connect(
        f"/ws/matches/{match_id}?player_id={ids['alpha-lead']}"
    ) as ws:
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "lobby_action", "action": "start"})
        for _ in range(40):
            message = ws.receive_json()
            if message["type"] == "error":
                # A refused start sends one message and then nothing, so
                # without this the next receive_json() blocks forever. Fail
                # with the lobby's own reason instead of hanging the suite.
                raise AssertionError(f"start refused: {message['error']}")
            if (message["type"] == "state_snapshot"
                    and message["state"]["status"] == "active"):
                break
        else:
            raise AssertionError("match never started")
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


# --- REST ---

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


def test_practice_rejects_unknown_game_and_kind(client):
    assert client.post("/api/practice/tetris").status_code == 404
    assert client.post("/api/practice/echo?kind=bogus").status_code == 400


def test_practice_missions_are_listed_and_playable(client):
    from backend.games.game11_bomb_defuse import BombDefuseGame

    # Only the games that ship a ladder have one; the rest answer empty.
    assert client.get("/api/practice/echo/missions").json() == {"missions": []}
    assert client.get("/api/practice/tetris/missions").status_code == 404
    missions = client.get("/api/practice/bomb_defuse/missions").json()["missions"]
    assert missions and all({"id", "name", "blurb"} == set(m) for m in missions)

    game = BombDefuseGame()
    for mission in missions:
        body = client.post(f"/api/practice/bomb_defuse?kind={mission['id']}").json()
        assert body["puzzle"]["game_id"] == "bomb_defuse"
        assert "answer" not in body["puzzle"]
        check = {
            "seed": body["seed"], "kind": mission["id"],
            "answer": game.generate_mission(mission["id"]).answer,
        }
        assert client.post("/api/practice/bomb_defuse/check", json=check
                           ).json()["correct"] is True

    # A mission id is not a kind another game will answer to.
    assert client.post("/api/practice/echo?kind=maze_drill").status_code == 400
    assert client.post("/api/practice/bomb_defuse?kind=no_such").status_code == 400


def test_get_config(client):
    body = client.get("/api/config").json()
    assert body["teams"] == ["alpha", "bravo"]
    assert body["players_per_team"] == server.engine.max_players_ceiling()
    assert body["level_count"] == config.LEVEL_COUNT
    assert body["wait_seconds"] == config.WAIT_SECONDS
    assert set(body["perks"]) == set(config.PERKS)
    assert set(body["roles"]) == set(config.ROLES)
    for role_id, role in config.ROLES.items():
        assert body["roles"][role_id] == {
            "name": role["name"],
            "games": role["games"],
            # The lobby mirrors both rules client-side, so both flags travel.
            "fixed": bool(role.get("fixed")),
            "required": bool(role.get("required")),
        }
    assert body["roles"]["generalist"]["games"] is None  # any game
    assert body["roles"]["duelist"]["games"] == [
        "rps_duel", "crown_duel", "number_clash", "bid_war",
    ]
    # The Defuser is the one role every team must field, and its game is not
    # the Grandmaster's to choose.
    assert body["roles"]["defuser"] == {
        "name": "Defuser", "games": ["bomb_defuse"], "fixed": True, "required": True,
    }
    library_ids = {entry["id"] for entry in body["library"]}
    assert {"rewire", "sweep", "mirror_run", "decant", "echo", "overprint",
            "stackdrop", "lane_shift", "shadow_cast", "threadline",
            "bomb_defuse"} <= library_ids
    # The server picks duels, not the leader: none of them is in the library.
    assert not ({"rps_duel", "crown_duel", "number_clash", "bid_war"} & library_ids)


def test_protocol_parses_assign_role():
    parsed = protocol.parse_client_message({
        "type": "lobby_action", "action": "assign_role",
        "target_id": "p1", "role_id": "logician",
    })
    assert parsed == ("lobby_action", {
        "action": "assign_role", "target_id": "p1", "role_id": "logician",
    })
    malformed = protocol.parse_client_message({
        "type": "lobby_action", "action": "assign_role", "role_id": 7,
    })
    assert isinstance(malformed, str)  # non-string role_id is rejected


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
    capacity = server.engine.max_players_ceiling() + 1  # playing members + the leader
    for i in range(capacity):
        join(client, match_id, f"A{i}", "alpha")
    response = join(client, match_id, "one-too-many", "alpha")
    assert response.status_code == 400 and "full" in response.json()["detail"]


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


def test_lobby_leader_and_start_blockers(client, fake_games):
    match_id = create_match(client)
    host_id = join(client, match_id, "Host", None).json()["player"]["id"]
    guest_id = join(client, match_id, "Guest", None).json()["player"]["id"]
    with connect(client, match_id, host_id) as (host_ws, _):
        with connect(client, match_id, guest_id) as (guest_ws, _):
            guest_ws.send_json({"type": "lobby_action", "action": "set_team",
                                "team_id": "bravo"})
            snapshot = guest_ws.receive_json()["state"]
            assert snapshot["teams"]["bravo"]["players"][0]["name"] == "Guest"
            # guest cannot use host powers
            guest_ws.send_json({"type": "lobby_action", "action": "start"})
            guest_ws.receive_json()  # own broadcast of set_team event
            assert "host" in guest_ws.receive_json()["error"]
            # claiming leader requires a team
            host_ws.send_json({"type": "lobby_action", "action": "claim_leader"})
            error = drain_for_error(host_ws)
            assert "team" in error
            host_ws.send_json({"type": "lobby_action", "action": "set_team",
                               "team_id": "alpha"})
            host_ws.send_json({"type": "lobby_action", "action": "claim_leader"})
            host_ws.send_json({"type": "lobby_action", "action": "set_min_players",
                               "value": 1})
            # start still blocked: alpha's leader has no playing teammates
            host_ws.send_json({"type": "lobby_action", "action": "start"})
            error = drain_for_error(host_ws)
            assert "player" in error or "Grandmaster" in error
    state = client.get(f"/api/matches/{match_id}").json()["match"]
    assert state["status"] == "lobby" and state["min_players"] == 1


def drain_for_error(ws, tries: int = 20) -> str:
    for _ in range(tries):
        message = ws.receive_json()
        if message["type"] == "error":
            return message["error"]
    raise AssertionError("no error message arrived")


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


# --- WebSocket ---

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
        ws.send_json({"type": "submit_holding",  # v1 message: gone
                      "puzzle_id": "x", "answer": "x"})
        assert ws.receive_json()["error"] == "Unknown message type."
        ws.send_json({"type": "choose_wait"})
        assert "choice" in ws.receive_json()["error"]  # nothing cleared yet
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


def test_choice_and_perk_flow_over_websocket(client, fake_games):
    match_id = create_match(client)
    ids = fill_match(client, match_id)
    with connect(client, match_id, ids["alpha"][0]) as (ws, me):
        ws.send_json({"type": "submit_answer",
                      "puzzle_id": me["current_puzzle"]["id"], "answer": MAIN_OK})
        snapshot = ws.receive_json()
        assert snapshot["state"]["me"]["status"] == "cleared"
        assert snapshot["state"]["me"]["choice_pending"] is True
        ws.send_json({"type": "choose_bonus"})
        snapshot = ws.receive_json()
        assert snapshot["state"]["me"]["status"] == "bonus"
        assert snapshot["state"]["me"]["current_puzzle"] is not None
        ws.send_json({"type": "buy_perk", "perk_id": "shield"})
        assert "Grandmaster" in ws.receive_json()["error"]  # players can't buy


def test_screen_effect_perk_reaches_only_the_victim_over_websocket(client, fake_games):
    """End to end: the Grandmaster buys Wobble, the server picks the victim, and
    the deadline arrives in exactly one opposing player's own snapshot."""
    match_id = create_match(client)
    ids = fill_match(client, match_id)
    # Two clears fund the shop (+1 each on a first clear). Two of four is short
    # of the roster, so the team can't advance and reroll everyone's board.
    for player_id in ids["alpha"][:2]:
        with connect(client, match_id, player_id) as (ws, me):
            ws.send_json({"type": "submit_answer",
                          "puzzle_id": me["current_puzzle"]["id"], "answer": MAIN_OK})
            assert ws.receive_json()["state"]["me"]["status"] == "cleared"
    with connect(client, match_id, ids["alpha-lead"]) as (ws, _):
        ws.send_json({"type": "buy_perk", "perk_id": "wobble"})
        for _ in range(5):  # a perk_used nudge may land before the snapshot
            message = ws.receive_json()
            assert message["type"] != "error", message
            if message["type"] == "state_snapshot":
                break
    hit = [
        set(me["screen_effects"])
        for me in (_reconnect_me(client, match_id, pid) for pid in ids["bravo"])
        if me["screen_effects"]
    ]
    assert hit == [{"wobble"}]  # exactly one victim, server's choice


def _reconnect_me(client, match_id: str, player_id: str) -> dict:
    with connect(client, match_id, player_id) as (_, me):
        return me


def test_give_leader_over_websocket(client, fake_games):
    match_id = create_match(client)
    ids = fill_match(client, match_id)
    with connect(client, match_id, ids["alpha-lead"]) as (ws, me):
        assert me["is_leader"] is True
        ws.send_json({"type": "give_leader", "target_id": ids["alpha"][0]})
        for _ in range(10):
            message = ws.receive_json()
            if message["type"] == "state_snapshot":
                state = message["state"]
                break
        assert state["me"]["is_leader"] is False
        assert state["me"]["status"] == "solving"
        assert state["me"]["current_puzzle"] is not None
        # The demoted leader now gets the limited team view (no leader_id).
        assert "leader_id" not in state["teams"]["alpha"]
    with connect(client, match_id, ids["alpha"][0]) as (ws, me):
        assert me["is_leader"] is True and me["status"] == "leading"


def walk_no_answer(node, path="$"):
    if isinstance(node, dict):
        assert "answer" not in node, f"answer leaked at {path}"
        for key, value in node.items():
            walk_no_answer(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            walk_no_answer(value, f"{path}[{i}]")


def test_snapshots_never_contain_answers_real_games(client):
    """With the real registry: real games served per assignment, no leaks,
    and the leader's full view holds while players get the limited one."""
    match_id = create_match(client)
    # Seat 3 defuses: against the real registry every team must field one.
    ids = fill_match(client, match_id, games=REAL_GAMES, defuser_seat=3)
    with connect(client, match_id, ids["alpha"][3]) as (ws, me):
        # The required role served its own game, and the bomb's payload keeps
        # its reference transcript to itself.
        assert me["current_puzzle"]["game_id"] == "bomb_defuse"
        ws.send_json({"type": "request_state"})
        walk_no_answer(ws.receive_json())
    with connect(client, match_id, ids["alpha"][0]) as (ws, me):
        assert me["current_puzzle"]["game_id"] == "rewire"
        ws.send_json({"type": "request_state"})
        snapshot = ws.receive_json()
        walk_no_answer(snapshot)
        assert "players" not in snapshot["state"]["teams"]["alpha"]  # limited view
    with connect(client, match_id, ids["alpha-lead"]) as (ws, me):
        ws.send_json({"type": "request_state"})
        snapshot = ws.receive_json()
        walk_no_answer(snapshot)
        team = snapshot["state"]["teams"]["alpha"]
        greens = sum(1 for p in team["players"] if p["green"])
        assert team["green_count"] == greens
        assert "currency" in team
        assert "players" not in snapshot["state"]["teams"]["bravo"]  # opponent summary


# --- integration: two full teams, alpha plays to the win ---

def test_full_match_to_win_over_websocket(client, fake_games):
    match_id = create_match(client)
    ids = fill_match(client, match_id)
    won = None
    for level in range(1, LEVELS + 1):
        for i, player_id in enumerate(ids["alpha"]):
            with connect(client, match_id, player_id) as (ws, me):
                assert me["status"] == "solving"
                ws.send_json({
                    "type": "submit_answer",
                    "puzzle_id": me["current_puzzle"]["id"],
                    "answer": MAIN_OK,
                })
                assert ws.receive_json()["type"] == "state_snapshot"
                # "cleared" events are leader-only; players get the nudges.
                if i == 3 and level < LEVELS:
                    assert ws.receive_json()["event"]["kind"] == "advance"
                    assert ws.receive_json() == {
                        "type": "level_advanced",
                        "team_id": "alpha",
                        "level": level + 1,
                    }
                if i == 3 and level == LEVELS:
                    assert ws.receive_json()["event"]["kind"] == "win"
                    won = ws.receive_json()
    assert won == {"type": "match_won", "team_id": "alpha"}
    state = client.get(f"/api/matches/{match_id}").json()["match"]
    assert state["status"] == "finished" and state["winner_team_id"] == "alpha"


# --- eviction ---

def test_eviction_of_idle_match(client, fake_games):
    stale_id = create_match(client)
    fresh_id = create_match(client)

    async def scenario():
        server.timers.schedule(stale_id, "p1", "wait", "2099-01-01T00:00:00+00:00")
        server.last_activity[stale_id] = (
            time.monotonic() - config.MATCH_TTL_SECONDS - 1
        )
        return await server.evict_stale()

    evicted = asyncio.run(scenario())
    assert stale_id in evicted and fresh_id not in evicted
    assert client.get(f"/api/matches/{stale_id}").status_code == 404
    assert client.get(f"/api/matches/{fresh_id}").status_code == 200
    assert server.timers.pending(stale_id) == set()  # no timer will fire


# --- duels over the socket ---

def test_duel_over_the_websocket(client, fake_games):
    """Both Duelists commit a move; the round resolves and the outcome fans out.

    The load-bearing assertion is the one in the middle: while the round is
    open, the opponent's socket has been sent nothing about A0's move.
    """
    match_id = create_match(client)
    ids = fill_match(client, match_id, duelists=True)

    with connect(client, match_id, ids["alpha"][0]) as (ws_a, me_a):
        with connect(client, match_id, ids["bravo"][0]) as (ws_b, me_b):
            assert me_a["status"] == "duelling"
            assert me_a["assigned_game"] == "rps_duel"

            ws_a.send_json({"type": "request_state"})
            duel = ws_a.receive_json()["state"]["duel"]
            assert duel["phase"] == "choosing" and duel["you"] == "a"
            assert duel["duellists"] == {"a": "a0", "b": "b0"}
            duel_id, round_index = duel["id"], duel["round"]

            ws_a.send_json({
                "type": "duel_choice", "duel_id": duel_id,
                "round": round_index, "choice": "rock",
            })
            # B asks for state mid-round: A's move must not be in the reply.
            ws_b.send_json({"type": "request_state"})
            for _ in range(20):
                message = ws_b.receive_json()
                if message["type"] == "state_snapshot":
                    view = message["state"]["duel"]
                    assert view["choices"] == {}
                    assert "rock" not in repr(view["choices"])
                    if view["locked"]["a"]:
                        break
            else:
                raise AssertionError("never saw A locked in")

            ws_b.send_json({
                "type": "duel_choice", "duel_id": duel_id,
                "round": round_index, "choice": "scissors",
            })
            for _ in range(20):
                message = ws_b.receive_json()
                if message["type"] == "state_snapshot":
                    view = message["state"]["duel"]
                    if view["phase"] == "reveal":
                        # Resolved: now both moves are public to both seats.
                        assert view["choices"] == {"a": "rock", "b": "scissors"}
                        assert view["wins"] == {"a": 1, "b": 0}
                        break
            else:
                raise AssertionError("round never resolved")


def test_duel_choice_is_rejected_for_outsiders(client, fake_games):
    match_id = create_match(client)
    ids = fill_match(client, match_id, duelists=True)
    with connect(client, match_id, ids["alpha"][0]) as (ws_a, _):
        ws_a.send_json({"type": "request_state"})
        duel_id = ws_a.receive_json()["state"]["duel"]["id"]
    with connect(client, match_id, ids["alpha"][1]) as (ws, me):
        assert me["status"] == "solving"  # an ordinary solver, not a Duelist
        ws.send_json({
            "type": "duel_choice", "duel_id": duel_id, "round": 1,
            "choice": "rock",
        })
        for _ in range(20):
            message = ws.receive_json()
            if message["type"] == "error":
                assert "aren't in this duel" in message["error"]
                break
        else:
            raise AssertionError("no rejection")


def test_malformed_duel_choice_is_rejected(client, fake_games):
    match_id = create_match(client)
    ids = fill_match(client, match_id, duelists=True)
    with connect(client, match_id, ids["alpha"][0]) as (ws, _):
        for bad in (
            {"type": "duel_choice", "duel_id": "d", "round": "1", "choice": "rock"},
            {"type": "duel_choice", "duel_id": 1, "round": 1, "choice": "rock"},
            {"type": "duel_choice", "duel_id": "d", "round": True, "choice": "rock"},
            {"type": "duel_choice", "duel_id": "d", "round": 1},
        ):
            ws.send_json(bad)
            for _ in range(20):
                message = ws.receive_json()
                if message["type"] == "error":
                    assert message["error"] == "Malformed message."
                    break
            else:
                raise AssertionError(f"no rejection for {bad}")


def test_a_lone_duelist_cannot_start_the_match(client, fake_games):
    """The mirror rule, enforced where the host actually presses start."""
    match_id = create_match(client)
    ids = {}
    for team_id in ("alpha", "bravo"):
        ids[f"{team_id}-lead"] = join(
            client, match_id, f"{team_id}-lead", team_id
        ).json()["player"]["id"]
        ids[team_id] = [
            join(client, match_id, f"{team_id[0]}{i}", team_id).json()["player"]["id"]
            for i in range(4)
        ]
    for team_id in ("alpha", "bravo"):
        with connect(client, match_id, ids[f"{team_id}-lead"]) as (ws, _):
            ws.send_json({"type": "lobby_action", "action": "claim_leader"})
            for i, player_id in enumerate(ids[team_id]):
                role = "duelist" if (i == 0 and team_id == "alpha") else "generalist"
                ws.send_json({
                    "type": "lobby_action", "action": "assign_role",
                    "target_id": player_id, "role_id": role,
                })
                if role == "generalist":
                    ws.send_json({
                        "type": "lobby_action", "action": "assign_game",
                        "target_id": player_id, "game_id": GAMES[i],
                    })
            ws.send_json({"type": "heartbeat"})
            ws.receive_json()
    with connect(client, match_id, ids["alpha-lead"]) as (ws, _):
        ws.send_json({"type": "lobby_action", "action": "start"})
        for _ in range(30):
            message = ws.receive_json()
            if message["type"] == "error":
                assert "Duelist" in message["error"]
                break
            assert message.get("state", {}).get("status") != "active"
        else:
            raise AssertionError("start was not refused")


@pytest.mark.parametrize("duel_id", [
    "rps_duel", "crown_duel", "number_clash", "bid_war",
])
def test_duel_renderers_are_served(client, duel_id):
    """Every registered duel needs its renderer on the page: the server picks
    the Duelist's game, so any of them can turn up at kickoff."""
    response = client.get(f"/static/duels/{duel_id}.js")
    assert response.status_code == 200
    assert "RelayDuels" in response.text
    assert f'/static/duels/{duel_id}.js' in client.get("/play").text
