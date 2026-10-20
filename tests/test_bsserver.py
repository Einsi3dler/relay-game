"""BULLSHIT QUIZ through the real app: REST, the host door and the socket.

The room's rules are tested in tests/test_bsroom.py. These check the wiring:
that an invitation is claimed by a signed-in account and the account is the
seat from then on, the host key gates the host, every socket gets its own
snapshot, and nothing secret rides along before a reveal.
"""

from __future__ import annotations

import json
import random
from contextlib import ExitStack
from dataclasses import dataclass
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import backend.main as server
from backend import accounts, bsroster, bsserver, config


@dataclass
class Seated:
    person: bsroster.Player
    raw: str                 # their invitation token
    web: TestClient          # a browser signed in as their account


def account(first: str = "Player"):
    """A fresh site account and a signed-in browser for it."""
    user = accounts.create_user(first, "Tester", None, f"u{uuid4().hex[:10]}",
                                "a long enough password")
    session = accounts.create_session(user.id)
    return user, TestClient(server.app, cookies={config.SESSION_COOKIE: session})


@pytest.fixture
def roster(tmp_path):
    """Four invitations, unclaimed. Fresh store."""
    bsroster.close()
    bsroster.connect()
    bsroster._fresh_tokens.clear()
    path = tmp_path / "roster.json"
    path.write_text(json.dumps({"participants": [
        {"email": f"player{i}@example.test"} for i in range(4)]}))
    bsroster.import_roster(path, rng=random.Random(1))
    bsserver._room = None
    bsserver._sockets.clear()
    yield [(p, bsroster._fresh_tokens[p.id]) for p in bsroster.all_players()]
    bsserver._room = None
    bsserver._sockets.clear()
    bsroster.close()
    bsroster.connect()


@pytest.fixture
def players(roster):
    """Every invitation claimed by its own signed-in account."""
    out = []
    for i, (person, raw) in enumerate(roster):
        user, web = account(f"Player{i}")
        bsroster.claim(raw, user.id, user.first_name, user.last_name, user.username)
        out.append(Seated(bsroster.by_id(person.id), raw, web))
    return out


@pytest.fixture
def client(roster):
    with TestClient(server.app) as test_client:
        yield test_client


def write(seat: Seated, **overrides):
    person = seat.person
    body = {"prompt": f"A question from {person.name}?",
            "answer": f"real {person.id}",
            "decoys": [f"fake {person.id} {k}" for k in range(person.option_count - 1)],
            "explanation": f"secret-why-{person.id}"}
    body.update(overrides)
    return seat.web.post("/api/bullshit/question", json=body)


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


def reason(response) -> str:
    return response.json()["detail"]["reason"]


# --- pages ----------------------------------------------------------------

def test_pages_serve(client, roster):
    _, raw = roster[0]
    assert client.get("/bullshit").status_code == 200
    assert client.get(f"/bullshit/{raw}").status_code == 200


# --- claiming a seat ------------------------------------------------------

def test_signed_out_with_a_link_is_asked_to_sign_in(client, roster):
    _, raw = roster[0]
    response = client.get("/api/bullshit/me", params={"token": raw})
    assert response.status_code == 401 and reason(response) == "signed_out"


def test_a_bad_link_is_a_bad_link(client):
    response = client.get("/api/bullshit/me", params={"token": "nope"})
    assert response.status_code == 404 and reason(response) == "bad_link"


def test_claim_names_the_seat_from_the_account(client, roster):
    person, raw = roster[0]
    user, web = account("Grace")
    preview = web.get("/api/bullshit/me", params={"token": raw}).json()
    assert preview["claimed"] is False and preview["account"]["first_name"] == "Grace"
    claimed = web.post("/api/bullshit/claim", json={"token": raw})
    assert claimed.status_code == 200
    assert claimed.json()["name"] == "Grace" and claimed.json()["claimed"]
    # Again is harmless.
    assert web.post("/api/bullshit/claim", json={"token": raw}).status_code == 200
    # And now the account finds its seat with no link at all.
    assert web.get("/api/bullshit/me").json()["id"] == person.id


def test_same_first_names_get_an_initial(client, roster):
    (_, raw_a), (_, raw_b) = roster[0], roster[1]
    _, web_a = account("Sam")
    _, web_b = account("Sam")
    assert web_a.post("/api/bullshit/claim", json={"token": raw_a}).json()["name"] == "Sam"
    assert web_b.post("/api/bullshit/claim", json={"token": raw_b}).json()["name"] == "Sam T."


def test_nobody_else_can_take_a_claimed_seat(client, roster):
    _, raw = roster[0]
    _, owner = account("Owner")
    _, thief = account("Thief")
    owner.post("/api/bullshit/claim", json={"token": raw})
    peek = thief.get("/api/bullshit/me", params={"token": raw})
    assert peek.status_code == 403 and reason(peek) == "claimed_by_other"
    assert thief.post("/api/bullshit/claim", json={"token": raw}).status_code == 400
    # Signed out, the link alone opens nothing either.
    assert client.get("/api/bullshit/me", params={"token": raw}).status_code == 403


def test_one_account_one_seat(client, roster):
    (_, raw_a), (_, raw_b) = roster[0], roster[1]
    _, web = account()
    web.post("/api/bullshit/claim", json={"token": raw_a})
    second = web.post("/api/bullshit/claim", json={"token": raw_b})
    assert second.status_code == 400 and "already has a seat" in second.json()["detail"]


def test_signed_in_without_a_seat(client, roster):
    _, web = account()
    response = web.get("/api/bullshit/me")
    assert response.status_code == 404 and reason(response) == "no_seat"


def test_claiming_needs_a_sign_in(client, roster):
    _, raw = roster[0]
    assert client.post("/api/bullshit/claim", json={"token": raw}).status_code == 401


# --- questions ------------------------------------------------------------

def test_me_knows_only_me(client, players):
    seat = players[0]
    me = seat.web.get("/api/bullshit/me").json()
    assert me["id"] == seat.person.id
    assert me["option_count"] == seat.person.option_count
    assert me["question"] is None and me["locked"] is False


def test_write_and_rewrite_a_question(client, players):
    assert write(players[0]).status_code == 200
    again = write(players[0], prompt="Changed my mind?")
    assert again.json()["question"]["prompt"] == "Changed my mind?"


def test_writing_needs_a_seat(client, players):
    _, stranger = account()
    assert write(Seated(players[0].person, "", stranger)).status_code == 404


def test_the_option_count_is_enforced(client, players):
    seat = players[0]
    assert write(seat, decoys=["x"] * seat.person.option_count).status_code == 400


# --- the host door --------------------------------------------------------

def test_host_page_needs_the_key(client):
    assert "Host key" in client.get(bsserver.HOST_PATH).text
    wrong = client.post(bsserver.HOST_PATH, data={"key": "wrong"})
    assert wrong.status_code == 401


def test_a_socket_without_a_sign_in_or_key_is_closed(client):
    for url in ("/ws/bullshit", "/ws/bullshit?as=host"):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(url) as ws:
                ws.receive_json()


def test_a_link_alone_does_not_open_the_socket(client, players):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/bullshit?token={players[0].raw}") as ws:
            ws.receive_json()


def test_a_player_cannot_drive_the_host_steps(client, players):
    with players[0].web.websocket_connect("/ws/bullshit") as ws:
        state(ws)
        ws.send_json({"type": bsserver.HOST, "action": "start"})
        msg = ws.receive_json()
        while msg["type"] != "error":
            msg = ws.receive_json()
        assert "host" in json.dumps(msg).lower()


# --- a round over the wire ------------------------------------------------

def test_a_full_round_over_sockets(client, players):
    for seat in players:
        assert write(seat).status_code == 200
    host_client(client)

    with ExitStack() as stack:
        host = stack.enter_context(client.websocket_connect("/ws/bullshit?as=host"))
        assert state(host)["is_host"] is True
        socks = {seat.person.id: stack.enter_context(
                     seat.web.websocket_connect("/ws/bullshit"))
                 for seat in players}

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
    assert write(players[0]).status_code == 400


# --- the grace period -----------------------------------------------------

def _started_with_sockets(client, players, stack):
    for seat in players:
        assert write(seat).status_code == 200
    host_client(client)
    host = stack.enter_context(client.websocket_connect("/ws/bullshit?as=host"))
    socks = {seat.person.id: stack.enter_context(
                 seat.web.websocket_connect("/ws/bullshit"))
             for seat in players}
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
        web = next(s.web for s in players if s.person.id == guesser)
        stack.enter_context(web.websocket_connect("/ws/bullshit"))
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


# --- host: undo reset and the invitations ---------------------------------

def _host_action(host, action, **extra):
    host.send_json({"type": bsserver.HOST, "action": action, **extra})


def test_undo_reset_inside_the_window(client, players):
    with ExitStack() as stack:
        host, _ = _started_with_sockets(client, players, stack)
        before = bsserver.room()
        _host_action(host, "reset")
        view = until(host, lambda v: v["phase"] == "lobby")
        assert view["can_undo_reset"] is True
        _host_action(host, "undo_reset")
        until(host, lambda v: v["phase"] == "briefing")
        assert bsserver.room() is before


def test_undo_reset_after_the_window_is_refused(client, players, monkeypatch):
    import time
    monkeypatch.setattr(bsserver.config, "BS_UNDO_SECONDS", 0)
    with ExitStack() as stack:
        host, _ = _started_with_sockets(client, players, stack)
        _host_action(host, "reset")
        until(host, lambda v: v["phase"] == "lobby")
        time.sleep(0.05)
        _host_action(host, "undo_reset")
        msg = host.receive_json()
        while msg["type"] != "error":
            msg = host.receive_json()
        assert "too late" in msg["error"].lower()


def test_host_marks_a_player_gone(client, players):
    with ExitStack() as stack:
        host, _ = _started_with_sockets(client, players, stack)
        guesser = next(s.id for s in bsserver.room().guessers())
        _host_action(host, "drop", player=guesser)
        view = until(host, lambda v: any(p["id"] == guesser and p["relegated"]
                                         for p in v["players"]))
        assert view["phase"] == "briefing"


def test_acting_after_being_marked_gone_counts_as_back(client, players):
    with ExitStack() as stack:
        host, socks = _started_with_sockets(client, players, stack)
        current = bsserver.room()
        guesser = next(s.id for s in current.guessers())
        _host_action(host, "drop", player=guesser)
        until(host, lambda v: any(p["id"] == guesser and not p["connected"]
                                  for p in v["players"]))
        # Get to the discussion, then the "gone" guesser answers.
        for seat in list(current.speakers()):
            if current.role_of(seat.id) == "truthful":
                socks[seat.id].send_json({"type": bsserver.READY})
            else:
                socks[seat.id].send_json({"type": bsserver.BLUFF, "wager": 1})
        until(host, lambda v: v.get("speakers_in") == v.get("speakers"))
        _host_action(host, "open")
        until(host, lambda v: v["phase"] == "open")
        socks[guesser].send_json({"type": bsserver.GUESS, "option": "A", "wager": 1})
        until(host, lambda v: any(p["id"] == guesser and p["connected"]
                                  for p in v["players"]))
        seat = bsserver.room().seats[guesser]
        assert seat.connected and seat.relegated


def test_invites_are_host_only_and_list_everyone(client, roster):
    assert client.get("/api/bullshit/host/invites").status_code == 401
    host_client(client)
    invites = client.get("/api/bullshit/host/invites").json()["invites"]
    assert len(invites) == 4 and not any(i["claimed"] for i in invites)
    assert all(i["email"].endswith("@example.test") for i in invites)


def test_new_link_replaces_the_old_one(client, roster):
    person, old = roster[0]
    host_client(client)
    link = client.post(f"/api/bullshit/host/invites/{person.id}/link").json()["link"]
    new = link.rsplit("/", 1)[1]
    assert bsroster.by_token(old) is None
    assert bsroster.by_token(new).id == person.id


def test_no_new_link_for_a_claimed_seat(client, players):
    host_client(client)
    response = client.post(f"/api/bullshit/host/invites/{players[0].person.id}/link")
    assert response.status_code == 400 and "sign in" in response.json()["detail"]


def test_a_player_cannot_see_the_invites(client, players):
    assert players[0].web.get("/api/bullshit/host/invites").status_code == 401
