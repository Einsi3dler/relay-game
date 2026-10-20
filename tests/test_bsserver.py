"""BULLSHIT QUIZ through the real app: REST, the host door and the socket.

The room's rules are tested in tests/test_bsroom.py. These check the wiring:
that a link identifies its player, the host key gates the host, every socket
gets its own snapshot, and nothing secret rides along before a reveal.
"""

from __future__ import annotations

import json
import random
from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import backend.main as server
from backend import bsroster, bsserver


@pytest.fixture
def players(tmp_path):
    """Four registered players, each with their raw token. Fresh store."""
    bsroster.close()
    bsroster.connect()
    bsroster._fresh_tokens.clear()
    path = tmp_path / "roster.json"
    path.write_text(json.dumps({"participants": [
        {"email": f"player{i}@example.test"} for i in range(4)]}))
    bsroster.import_roster(path, rng=random.Random(1))
    out = []
    for i, person in enumerate(bsroster.all_players()):
        raw = bsroster._fresh_tokens[person.id]
        bsroster.register(raw, f"Player {i}")
        out.append((bsroster.by_id(person.id), raw))
    bsserver._room = None
    bsserver._sockets.clear()
    yield out
    bsserver._room = None
    bsserver._sockets.clear()
    bsroster.close()
    bsroster.connect()


@pytest.fixture
def client(players):
    with TestClient(server.app) as test_client:
        yield test_client


def write(client, person, raw, **overrides):
    body = {"token": raw, "prompt": f"A question from {person.name}?",
            "answer": f"real {person.id}",
            "decoys": [f"fake {person.id} {k}" for k in range(person.option_count - 1)],
            "explanation": f"secret-why-{person.id}"}
    body.update(overrides)
    return client.post("/api/bullshit/question", json=body)


def host_client(client):
    response = client.post(bsserver.HOST_PATH, data={"key": bsserver.HOST_KEY},
                           follow_redirects=False)
    assert response.status_code == 303
    return client


def state(ws) -> dict:
    """Read until the next snapshot, skipping anything else."""
    while True:
        msg = ws.receive_json()
        if msg["type"] == bsserver.STATE:
            return msg["state"]


def until(ws, check, limit: int = 50) -> dict:
    """Read snapshots until one passes `check`. Every connect and every move
    broadcasts to every socket, so a socket can have several queued."""
    for _ in range(limit):
        view = state(ws)
        if check(view):
            return view
    raise AssertionError("expected snapshot never arrived")


# --- pages and REST -------------------------------------------------------

def test_pages_serve(client, players):
    _, raw = players[0]
    assert client.get("/bullshit").status_code == 200
    assert client.get(f"/bullshit/{raw}").status_code == 200


def test_me_knows_only_me(client, players):
    person, raw = players[0]
    assert client.get("/api/bullshit/me", params={"token": "nope"}).status_code == 404
    me = client.get("/api/bullshit/me", params={"token": raw}).json()
    assert me["id"] == person.id
    assert me["option_count"] == person.option_count
    assert me["question"] is None and me["locked"] is False


def test_write_and_rewrite_a_question(client, players):
    person, raw = players[0]
    assert write(client, person, raw).status_code == 200
    again = write(client, person, raw, prompt="Changed my mind?")
    assert again.json()["question"]["prompt"] == "Changed my mind?"


def test_the_option_count_is_enforced(client, players):
    person, raw = players[0]
    bad = write(client, person, raw, decoys=["x"] * person.option_count)
    assert bad.status_code == 400


# --- the host door --------------------------------------------------------

def test_host_page_needs_the_key(client):
    assert "Host key" in client.get(bsserver.HOST_PATH).text
    wrong = client.post(bsserver.HOST_PATH, data={"key": "wrong"})
    assert wrong.status_code == 401


def test_a_socket_without_a_link_or_key_is_closed(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/bullshit") as ws:
            ws.receive_json()


def test_a_player_cannot_drive_the_host_steps(client, players):
    _, raw = players[0]
    with client.websocket_connect(f"/ws/bullshit?token={raw}") as ws:
        state(ws)
        ws.send_json({"type": bsserver.HOST, "action": "start"})
        msg = ws.receive_json()
        while msg["type"] != "error":
            msg = ws.receive_json()
        assert "host" in json.dumps(msg).lower()


# --- a round over the wire ------------------------------------------------

def test_a_full_round_over_sockets(client, players):
    for person, raw in players:
        assert write(client, person, raw).status_code == 200
    host_client(client)

    with ExitStack() as stack:
        host = stack.enter_context(client.websocket_connect("/ws/bullshit"))
        assert state(host)["is_host"] is True
        socks = {person.id: stack.enter_context(
                     client.websocket_connect(f"/ws/bullshit?token={raw}"))
                 for person, raw in players}

        host.send_json({"type": bsserver.HOST, "action": "start"})
        hv = until(host, lambda v: v["phase"] == "briefing")
        assert hv["rounds"] == 4
        views = {pid: until(ws, lambda v: v["phase"] == "briefing")
                 for pid, ws in socks.items()}

        # Before the reveal nothing on any wire names the truth or the author.
        current = bsserver.room()
        author = current.panel.author
        for pid, view in [*views.items(), ("host", hv)]:
            blob = json.dumps(view)
            assert '"truth"' not in blob and '"author"' not in blob
            if pid != author:
                assert f"secret-why-{author}" not in blob

        # Speakers commit, the host opens, guessers guess, lock, reveal.
        for pid, view in views.items():
            if view["role"] == "truthful":
                socks[pid].send_json({"type": bsserver.READY})
            elif view["role"] == "bullshitter":
                socks[pid].send_json({"type": bsserver.BLUFF, "wager": 1})
        until(host, lambda v: v.get("speakers_in") == v.get("speakers"))
        host.send_json({"type": bsserver.HOST, "action": "open"})
        until(host, lambda v: v["phase"] == "open")

        for pid, view in views.items():
            if view["role"] == "guesser":
                socks[pid].send_json({"type": bsserver.GUESS,
                                      "option": current.panel.correct_key, "wager": 2})
        until(host, lambda v: v.get("guessers_in") == v.get("guessers"))
        host.send_json({"type": bsserver.HOST, "action": "lock"})
        votes = until(host, lambda v: v["phase"] == "votes")
        assert "truth" not in votes
        host.send_json({"type": bsserver.HOST, "action": "reveal"})
        revealed = until(host, lambda v: v["phase"] == "reveal")
        assert revealed["truth"]["author"] == author
        assert revealed["truth"]["key"] == current.panel.correct_key

    # Questions lock once the game is under way.
    person, raw = players[0]
    assert write(client, person, raw).status_code == 400


# --- the grace period -----------------------------------------------------

def _started_with_sockets(client, players, stack):
    for person, raw in players:
        assert write(client, person, raw).status_code == 200
    host_client(client)
    host = stack.enter_context(client.websocket_connect("/ws/bullshit"))
    socks = {person.id: stack.enter_context(
                 client.websocket_connect(f"/ws/bullshit?token={raw}"))
             for person, raw in players}
    host.send_json({"type": bsserver.HOST, "action": "start"})
    until(host, lambda v: v["phase"] == "briefing")
    return host, socks


def test_reconnecting_inside_the_grace_period_changes_nothing(client, players, monkeypatch):
    import time
    monkeypatch.setattr(bsserver.config, "BS_GRACE_SECONDS", 0.3)
    with ExitStack() as stack:
        host, socks = _started_with_sockets(client, players, stack)
        guesser = next(s.id for s in bsserver.room().guessers())
        socks.pop(guesser).close()
        until(host, lambda v: any(p["id"] == guesser and p["reconnecting"]
                                  for p in v["players"]))
        raw = next(r for p, r in players if p.id == guesser)
        stack.enter_context(client.websocket_connect(f"/ws/bullshit?token={raw}"))
        time.sleep(0.6)
        seat = bsserver.room().seats[guesser]
        assert seat.connected and not seat.reconnecting and not seat.relegated


def test_staying_away_past_the_grace_period_relegates(client, players, monkeypatch):
    import time
    monkeypatch.setattr(bsserver.config, "BS_GRACE_SECONDS", 0.2)
    with ExitStack() as stack:
        host, socks = _started_with_sockets(client, players, stack)
        guesser = next(s.id for s in bsserver.room().guessers())
        socks.pop(guesser).close()
        time.sleep(0.6)
        seat = bsserver.room().seats[guesser]
        assert not seat.connected and seat.relegated
