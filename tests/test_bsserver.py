"""BULLSHIT QUIZ through the real app: REST, the host door and the socket.

The room's rules are tested in tests/test_bsroom.py. These check the wiring:
that a signed-in account joins with the host's code and is the seat from
then on, the host key gates the host, every socket gets its own
snapshot, and nothing secret rides along before a reveal.
"""

from __future__ import annotations

import asyncio
import json
import random
from contextlib import ExitStack
from dataclasses import dataclass
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import backend.main as server
from backend import accounts, bsroom, bsroster, bsserver, config


@dataclass
class Seated:
    person: bsroster.Player
    web: TestClient          # a browser signed in as their account


def account(first: str = "Player"):
    """A fresh site account and a signed-in browser for it."""
    user = accounts.create_user(first, "Tester", None, f"u{uuid4().hex[:10]}",
                                "a long enough password")
    session = accounts.create_session(user.id)
    return user, TestClient(server.app, cookies={config.SESSION_COOKIE: session})


@pytest.fixture
def fresh():
    """An empty game store and a fresh room with a known code."""
    bsroster.close()
    bsroster.connect()
    bsserver._room = bsroom.create_room([], code="K7QM")
    bsserver._sockets.clear()
    # Each test client runs its sockets on event loops of its own, which the
    # real single-loop server never does. An asyncio.Lock binds to the first
    # loop it ever waits on, so a fresh one per test keeps one test's loops
    # from failing the next test's. Same for the ticker task.
    bsserver._lock = asyncio.Lock()
    bsserver._ticker = None
    yield
    bsserver._room = None
    bsserver._sockets.clear()
    bsroster.close()
    bsroster.connect()


@pytest.fixture
def client(fresh):
    with TestClient(server.app) as test_client:
        yield test_client


@pytest.fixture
def players(client):
    """Four accounts, each joined with tonight's code."""
    out = []
    for i in range(4):
        _, web = account(f"Player{i}")
        response = web.post("/api/bullshit/join", json={"code": "K7QM"})
        assert response.status_code == 200, response.text
        out.append(Seated(bsroster.by_id(response.json()["id"]), web))
    return out


def write(seat: Seated, slot: int = 0, **overrides):
    person = seat.person
    body = {"slot": slot,
            "prompt": f"Question {slot} from {person.name}?",
            "answer": f"real {person.id} {slot}",
            "decoys": [f"fake {person.id} {slot} {k}"
                       for k in range(person.option_counts[slot] - 1)],
            "explanation": f"secret-why-{person.id}-{slot}"}
    body.update(overrides)
    return seat.web.post("/api/bullshit/question", json=body)


def bank_ready(players, rounds: int = 4) -> None:
    """The night before: everybody writes every question, the host tags them
    all and sets how many rounds to play."""
    for seat in players:
        for slot in range(len(seat.person.option_counts)):
            assert write(seat, slot).status_code == 200
    for item in bsroster.review():
        bsroster.set_tag(item["id"], "mid")
    bsroster.set_rounds(rounds)


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


def run_draft(host, socks) -> dict:
    """Start the picks, have whoever is on the clock take the first perk
    left, and start the quiz. Returns the host's first briefing view."""
    host.send_json({"type": bsserver.HOST, "action": "draft_go"})
    view = until(host, lambda v: v.get("draft") and v["draft"]["stage"] != "trading")
    while view["draft"]["stage"] == "picking":
        draft = view["draft"]
        active = draft["order"][draft["current"]]
        socks[active].send_json({"type": bsserver.DRAFT_PICK, "perk": draft["available"][0],
                                 "turn": draft["turn"]})
        view = until(host, lambda v, t=draft["turn"]: (v["draft"]["turn"] != t
                                                       or v["draft"]["stage"] == "complete"))
    host.send_json({"type": bsserver.HOST, "action": "begin"})
    return until(host, lambda v: v["phase"] == "briefing")


# --- pages ----------------------------------------------------------------

def test_pages_serve(client):
    assert client.get("/bullshit").status_code == 200
    assert client.get("/bullshit?code=K7QM").status_code == 200


# --- joining --------------------------------------------------------------

def test_signed_out_is_asked_to_sign_in(client):
    response = client.get("/api/bullshit/me")
    assert response.status_code == 401 and reason(response) == "signed_out"
    assert client.post("/api/bullshit/join", json={"code": "K7QM"}).status_code == 401


def test_signed_in_but_not_joined(client):
    _, web = account()
    response = web.get("/api/bullshit/me")
    assert response.status_code == 404 and reason(response) == "not_joined"
    assert response.json()["detail"]["locked"] is False


def test_join_with_the_code(client):
    _, web = account("Grace")
    joined = web.post("/api/bullshit/join", json={"code": " k7qm "})
    assert joined.status_code == 200 and joined.json()["name"] == "Grace"
    assert web.get("/api/bullshit/me").json()["id"] == joined.json()["id"]
    # Again is harmless.
    assert web.post("/api/bullshit/join", json={"code": "K7QM"}).json()["id"] == joined.json()["id"]


def test_a_wrong_code_is_refused(client):
    _, web = account()
    response = web.post("/api/bullshit/join", json={"code": "NOPE"})
    assert response.status_code == 400 and "doesn't match" in response.json()["detail"]
    assert bsroster.all_players() == []


def test_nobody_joins_after_the_start(client, players):
    bank_ready(players)
    with ExitStack() as stack:
        host_client(client)
        host = stack.enter_context(client.websocket_connect("/ws/bullshit?as=host"))
        for seat in players:
            stack.enter_context(seat.web.websocket_connect("/ws/bullshit"))
        host.send_json({"type": bsserver.HOST, "action": "start"})
        until(host, lambda v: v["phase"] == "draft")       # the roster froze at the start
        _, late = account()
        response = late.post("/api/bullshit/join", json={"code": "K7QM"})
        assert response.status_code == 400 and "already started" in response.json()["detail"]
        me = late.get("/api/bullshit/me")
        assert reason(me) == "not_joined" and me.json()["detail"]["locked"] is True


def test_only_the_host_sees_the_code(client, players):
    host_client(client)
    with client.websocket_connect("/ws/bullshit?as=host") as host:
        assert state(host)["code"] == "K7QM"
    with players[0].web.websocket_connect("/ws/bullshit") as ws:
        assert "code" not in state(ws)


# --- questions ------------------------------------------------------------

def test_me_knows_only_me(client, players):
    seat = players[0]
    me = seat.web.get("/api/bullshit/me").json()
    assert me["id"] == seat.person.id
    assert [s["option_count"] for s in me["questions"]] == list(seat.person.option_counts)
    assert not any(s["written"] for s in me["questions"]) and me["locked"] is False


def test_write_and_rewrite_a_question(client, players):
    assert write(players[0], 1).status_code == 200
    again = write(players[0], 1, prompt="Changed my mind?").json()["questions"]
    assert [s["written"] for s in again] == [False, True, False]
    assert again[1]["prompt"] == "Changed my mind?" and "tag" not in again[1]


def test_a_slot_that_isnt_yours_is_refused(client, players):
    body = {"slot": 7, "prompt": "Q?", "answer": "A", "decoys": ["B"]}
    assert players[0].web.post("/api/bullshit/question", json=body).status_code == 400


def test_writing_needs_a_seat(client, players):
    _, stranger = account()
    assert write(Seated(players[0].person, stranger)).status_code == 404


def test_the_option_count_is_enforced(client, players):
    seat = players[0]
    assert write(seat, decoys=["x"] * seat.person.option_counts[0]).status_code == 400


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
    bank_ready(players)
    host_client(client)

    with ExitStack() as stack:
        host = stack.enter_context(client.websocket_connect("/ws/bullshit?as=host"))
        assert state(host)["is_host"] is True
        socks = {seat.person.id: stack.enter_context(
                     seat.web.websocket_connect("/ws/bullshit"))
                 for seat in players}

        host.send_json({"type": bsserver.HOST, "action": "start"})
        drafting = until(host, lambda v: v["phase"] == "draft")
        assert sorted(drafting["draft"]["order"]) == sorted(socks)
        hv = run_draft(host, socks)
        assert hv["rounds"] == 4
        assert all(p["perk"] for p in hv["players"])
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
        talking = until(host, lambda v: v["phase"] == "open")
        assert talking["talk"]["ballot"] is False
        host.send_json({"type": bsserver.HOST, "action": "end_talk"})
        until(host, lambda v: v.get("talk", {}).get("ballot"))

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
    bank_ready(players)
    host_client(client)
    host = stack.enter_context(client.websocket_connect("/ws/bullshit?as=host"))
    socks = {seat.person.id: stack.enter_context(
                 seat.web.websocket_connect("/ws/bullshit"))
             for seat in players}
    host.send_json({"type": bsserver.HOST, "action": "start"})
    until(host, lambda v: v["phase"] == "draft")
    run_draft(host, socks)
    return host, socks


def test_a_draft_trade_over_sockets(client, players):
    bank_ready(players, rounds=3)            # three rounds: cards 1, 2 and 3
    host_client(client)
    with ExitStack() as stack:
        host = stack.enter_context(client.websocket_connect("/ws/bullshit?as=host"))
        socks = {seat.person.id: stack.enter_context(seat.web.websocket_connect("/ws/bullshit"))
                 for seat in players}
        host.send_json({"type": bsserver.HOST, "action": "start"})
        order = until(host, lambda v: v["phase"] == "draft")["draft"]["order"]
        first, last = order[0], order[-1]
        socks[last].send_json({"type": bsserver.OFFER, "to": first,
                               "give": f"{last}-card-3", "take": f"{first}-card-1"})
        offers = until(socks[first], lambda v: v.get("draft_offers"))["draft_offers"]
        assert offers[0]["from"] == last and offers[0]["status"] == "pending"
        socks[first].send_json({"type": bsserver.RESPOND, "offer": offers[0]["id"],
                                "answer": "accept"})
        after = until(host, lambda v: v["draft"]["order"][0] == last)
        assert after["draft"]["order"][-1] == first
        assert [c["value"] for c in after["draft"]["hands"][first]].count(3) == 2


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


# --- host: undo reset, mark gone, remove, new game -----------------------

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
        _host_action(host, "end_talk")
        until(host, lambda v: v.get("talk", {}).get("ballot"))
        socks[guesser].send_json({"type": bsserver.GUESS, "option": "A", "wager": 1})
        until(host, lambda v: any(p["id"] == guesser and p["connected"]
                                  for p in v["players"]))
        seat = bsserver.room().seats[guesser]
        assert seat.connected and seat.relegated


def test_host_removes_a_player_in_the_lobby(client, players):
    host_client(client)
    with client.websocket_connect("/ws/bullshit?as=host") as host:
        state(host)
        gone = players[0].person.id
        _host_action(host, "remove", player=gone)
        view = until(host, lambda v: all(p["id"] != gone for p in v["players"]))
        assert len(view["players"]) == 3
    assert bsroster.by_id(gone) is None
    # They can come back with the code.
    assert players[0].web.post("/api/bullshit/join", json={"code": "K7QM"}).status_code == 200


def test_remove_is_lobby_only(client, players):
    with ExitStack() as stack:
        host, _ = _started_with_sockets(client, players, stack)
        _host_action(host, "remove", player=players[0].person.id)
        msg = host.receive_json()
        while msg["type"] != "error":
            msg = host.receive_json()
        assert "Mark gone" in msg["error"]


def test_new_game_night_clears_everyone_and_changes_the_code(client, players):
    host_client(client)
    with client.websocket_connect("/ws/bullshit?as=host") as host:
        state(host)
        _host_action(host, "new_game")
        view = until(host, lambda v: v["players"] == [])
        assert view["code"] != "K7QM" and view["can_undo_reset"] is False
    assert bsroster.all_players() == []
    old = players[0].web.post("/api/bullshit/join", json={"code": "K7QM"})
    assert old.status_code == 400


def test_a_removed_player_is_disconnected_so_they_can_rejoin(client, players):
    # Host and player on one client, so both sockets share one event loop the
    # way they do on the real server; a TestClient outside `with` gives each
    # socket its own loop, and a close across loops never completes.
    with players[0].web as web:
        host_client(web)
        with web.websocket_connect("/ws/bullshit?as=host") as host:
            state(host)
            with web.websocket_connect("/ws/bullshit") as ws:
                state(ws)
                _host_action(host, "remove", player=players[0].person.id)
                with pytest.raises(WebSocketDisconnect) as closed:
                    while True:
                        ws.receive_json()
                assert closed.value.code == 4403


# --- the night before: the host's review ------------------------------------

def _error(ws) -> str:
    msg = ws.receive_json()
    while msg["type"] != "error":
        msg = ws.receive_json()
    return msg["error"]


def test_the_host_reviews_the_bank_without_names(client, players):
    for i, seat in enumerate(players):
        count = seat.person.option_counts[0]
        assert write(seat, 0, prompt=f"Neutral question {i}?", answer=f"Answer {i}",
                     decoys=[f"Other {i}.{k}" for k in range(count - 1)],
                     explanation="").status_code == 200
    host_client(client)
    with client.websocket_connect("/ws/bullshit?as=host") as host:
        view = state(host)
        assert len(view["review"]) == 4 and view["rounds_target"] == config.BS_ROUNDS_DEFAULT
        blob = json.dumps(view["review"])
        for seat in players:
            assert seat.person.id not in blob and seat.person.name not in blob
            assert seat.person.user_id not in blob
        assert {f"Answer {i}" for i in range(4)} == {item["answer"] for item in view["review"]}


def test_players_never_see_the_bank(client, players):
    for seat in players:
        assert write(seat, 0).status_code == 200
    with players[0].web.websocket_connect("/ws/bullshit") as ws:
        view = state(ws)
        assert "review" not in view and "rounds_target" not in view
        assert [slot["written"] for slot in view["mine"]] == [True, False, False]
        blob = json.dumps(view)
        for seat in players[1:]:
            assert f"real {seat.person.id}" not in blob


def test_tag_edit_and_rounds_from_the_host_screen(client, players):
    assert write(players[0], 0).status_code == 200
    host_client(client)
    with client.websocket_connect("/ws/bullshit?as=host") as host:
        (item,) = state(host)["review"]
        _host_action(host, "tag", question=item["id"], tag="fascinating")
        until(host, lambda v: v["review"][0]["tag"] == "fascinating")
        decoys = [f"tidier {k}" for k in range(len(item["decoys"]))]
        _host_action(host, "edit", question=item["id"], prompt="Tidier question?",
                     answer=item["answer"], decoys=decoys, explanation="")
        view = until(host, lambda v: v["review"][0]["prompt"] == "Tidier question?")
        assert view["review"][0]["edited"] and view["review"][0]["tag"] == "fascinating"
        _host_action(host, "rounds", rounds=20)
        until(host, lambda v: v["rounds_target"] == 20)
        _host_action(host, "tag", question=item["id"], tag="superb")
        assert "tag" in _error(host)
        _host_action(host, "rounds", rounds=0)
        assert "rounds" in _error(host)
    # The author sees the tidied wording, since they defend it, but no tag.
    mine = players[0].web.get("/api/bullshit/me").json()["questions"][0]
    assert mine["prompt"] == "Tidier question?" and mine["edited"] is True
    assert "tag" not in mine


def test_start_waits_for_the_review(client, players):
    for seat in players:
        for slot in range(len(seat.person.option_counts)):
            assert write(seat, slot).status_code == 200
    host_client(client)
    with ExitStack() as stack:
        host = stack.enter_context(client.websocket_connect("/ws/bullshit?as=host"))
        for seat in players:
            stack.enter_context(seat.web.websocket_connect("/ws/bullshit"))
        _host_action(host, "start")
        assert "need a tag" in _error(host)
        for item in bsroster.review():
            bsroster.set_tag(item["id"], "bland")
        _host_action(host, "start")
        view = until(host, lambda v: v["phase"] == "draft")
        assert "review" not in view and "rounds_target" not in view
        _host_action(host, "tag", question=bsroster.review()[0]["id"], tag="mid")
        assert "locked" in _error(host)


def test_the_join_code_survives_a_restart(client):
    bsserver._room = None
    code = bsserver.room().code
    assert bsroster.setting(bsroster.CODE) == code
    bsserver._room = None                         # the process restarts overnight
    assert bsserver.room().code == code
    _, web = account()
    assert web.post("/api/bullshit/join", json={"code": code}).status_code == 200


def test_new_game_night_saves_its_new_code(client):
    host_client(client)
    with client.websocket_connect("/ws/bullshit?as=host") as host:
        state(host)
        _host_action(host, "new_game")
        view = until(host, lambda v: v["code"] != "K7QM")
    assert bsroster.setting(bsroster.CODE) == view["code"]
    bsserver._room = None
    assert bsserver.room().code == view["code"]
