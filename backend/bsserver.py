"""BULLSHIT QUIZ over HTTP and one WebSocket.

Everything the game needs from the web server, as one router `main.py`
includes, so the shared app file gains two lines rather than two hundred. The
rules are `backend/bsquiz.py`, the state machine `backend/bsroom.py`, the
store `backend/bsroster.py`. This file only moves messages between them.

One room at a time, like ROLL CALL: the roster is a single list of people.

Two kinds of viewer. A player is whoever is signed in to the site account
that claimed a seat: the personal link (`/bullshit/<token>`) is only used
once, to claim it, so after that they resume from any device by signing in.
The host proves it with the host key or the cookie that key earned, and the
host page asks for that view explicitly (`?as=host`), because one browser
can hold both a host cookie and a player's sign-in.
The host never plays, and `bsroom.public` gives them no more than a guesser,
because on a group call the host is the one most likely sharing a screen.

**The default host key is "dev", in a public repository.** Set
`RELAY_BULLSHIT_HOST_KEY` before running a session with real people.

This module does not import `backend/mailer.py`
(tests/test_bsroster_guards.py checks).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import (APIRouter, Cookie, HTTPException, Query, Request, WebSocket,
                     WebSocketDisconnect)
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel

from backend import accounts, bsroom, bsroster, config, devgate, protocol

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

HOST_KEY = os.environ.get("RELAY_BULLSHIT_HOST_KEY", "dev")
HOST_PATH = "/bullshithost"
COOKIE_NAME = "relay_bullshithost"
SCOPE = "bullshithost"
HOST_VIEWER = "host"

# Messages on /ws/bullshit. Its own small dialect, so the match protocol's
# message set stays closed (the reason ROLL CALL gives in protocol.py).
STATE = "bs_state"
READY = "bs_ready"            # truthful player, in the briefing
BLUFF = "bs_bluff"            # bullshitter: {wager}
GUESS = "bs_guess"            # guesser: {option, wager}
HOST = "bs_host"              # host: {action}
HOST_ACTIONS = ("start", "open", "lock", "reveal", "next", "reset")

router = APIRouter()

_room: bsroom.BullshitRoom | None = None
_lock = asyncio.Lock()
_sockets: dict[str, WebSocket] = {}
_grace: dict[str, asyncio.Task] = {}      # viewer -> pending "they left" check


# --- the room -------------------------------------------------------------

def startup() -> None:
    """Open the store and import the roster if this machine has one. The
    import is idempotent, so doing it on every boot is safe."""
    bsroster.connect()
    with contextlib.suppress(FileNotFoundError):
        bsroster.import_roster()


def room() -> bsroom.BullshitRoom:
    global _room
    if _room is None:
        _room = bsroom.create_room(bsroster.all_players())
    return _room


async def _leave_after_grace(player_id: str) -> None:
    """The strict rule, applied only if they have not come back in time."""
    await asyncio.sleep(config.BS_GRACE_SECONDS)
    async with _lock:
        _grace.pop(player_id, None)
        if player_id in _sockets:
            return
        current = room()
        bsroom.on_disconnect(current, player_id)
        await broadcast(current)


def _cancel_grace(player_id: str) -> None:
    task = _grace.pop(player_id, None)
    if task is not None:
        task.cancel()


def reset_room() -> bsroom.BullshitRoom:
    global _room
    _room = bsroom.create_room(bsroster.all_players())
    for viewer in _sockets:
        bsroom.on_connect(_room, viewer)
    return _room


def snapshot(current: bsroom.BullshitRoom, viewer: str) -> dict[str, Any]:
    is_host = viewer == HOST_VIEWER
    return {"type": STATE,
            "state": current.public(None if is_host else viewer, is_host=is_host)}


async def _send(socket: WebSocket, payload: dict) -> None:
    with contextlib.suppress(Exception):
        await socket.send_json(payload)


async def broadcast(current: bsroom.BullshitRoom) -> None:
    """One personalised snapshot per socket, never one shared payload."""
    for viewer, socket in list(_sockets.items()):
        await _send(socket, snapshot(current, viewer))


# --- the host door --------------------------------------------------------

def host_authorised(key: str | None, cookie: str | None) -> bool:
    return devgate.authorised(key, cookie, SCOPE, HOST_KEY)


def _with_host_cookie(response):
    response.set_cookie(COOKIE_NAME, devgate.cookie_token(SCOPE, HOST_KEY),
                        max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax")
    return response


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta name="theme-color" content="#6a3cf5">
<title>Bullshit Quiz host</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Lilita+One&family=Nunito:wght@700;800;900&display=swap">
<link rel="stylesheet" href="/static/toon.css">
<link rel="stylesheet" href="/static/bullshit.css">
</head>
<body class="bs">
<main class="bs-main">
  <h1 class="bs-title">Host the game</h1>
  <p class="bs-sub">Players join from their own links. This door is for the host.</p>
  <form class="bs-card" method="post" action="{path}">
    <label class="bs-label" for="key">Host key</label>
    <input class="bs-field" id="key" type="password" name="key" autofocus
           autocomplete="current-password">
    {error}
    <button class="bs-btn bs-btn--go" type="submit">Open the room</button>
  </form>
</main>
</body>
</html>
"""


def login_html(failed: bool = False) -> str:
    error = '<p class="bs-error">That is not the host key.</p>' if failed else ""
    return LOGIN_HTML.replace("{path}", HOST_PATH).replace("{error}", error)


def _page(filename: str):
    page = FRONTEND_DIR / filename
    if page.exists():
        return FileResponse(page)
    return HTMLResponse(f"<h1>Bullshit Quiz</h1><p>{filename} not found.</p>")


# --- pages ----------------------------------------------------------------

@router.get("/bullshit", response_model=None)
async def player_page_without_link():
    """Opened without a personal link: the page says to use the invitation."""
    return _page("bullshit.html")


@router.get("/bullshit/{token}", response_model=None)
async def player_page(token: str):
    """A player's personal link. The page reads the token from its own path."""
    return _page("bullshit.html")


@router.get(HOST_PATH, response_model=None)
async def host_page(key: str | None = None,
                    relay_bullshithost: str | None = Cookie(default=None)):
    if not host_authorised(key, relay_bullshithost):
        return HTMLResponse(login_html())
    response = _page("bullshithost.html")
    # A key on the URL must leave a cookie: the page opens a WebSocket at
    # once, and a query string does not ride along on that.
    return _with_host_cookie(response) if key and not relay_bullshithost else response


@router.post(HOST_PATH, response_model=None)
async def host_login(request: Request):
    """Trade the key for a cookie, then redirect so a refresh does not
    re-post it. Parsed by hand, as /quizhost does."""
    body = (await request.body()).decode("utf-8", "replace")
    key = parse_qs(body).get("key", [""])[0]
    if not devgate.enabled(key, HOST_KEY):
        return HTMLResponse(login_html(failed=True), status_code=401)
    return _with_host_cookie(RedirectResponse(HOST_PATH, status_code=303))


# --- REST: who am I, claiming a seat, my question ------------------------

class ClaimBody(BaseModel):
    token: str


class QuestionBody(BaseModel):
    prompt: str
    answer: str
    decoys: list[str]
    explanation: str = ""


def signed_in(session: str | None) -> accounts.User | None:
    """The site account behind this request's session cookie, if any."""
    return accounts.session_user(session)


def _account(user: accounts.User | None) -> dict[str, Any] | None:
    return None if user is None else {
        "first_name": user.first_name, "username": user.username}


def _me(person: bsroster.Player, user: accounts.User | None) -> dict[str, Any]:
    """What the player's own page may know before the game: themselves and
    their own question. Never anybody else's."""
    q = bsroster.my_question(person.id)
    return {
        "id": person.id,
        "name": person.display_name,
        "claimed": person.registered,
        "account": _account(user),
        "option_count": person.option_count,
        "question": None if q is None else {
            "prompt": q.prompt, "answer": q.answer,
            "decoys": list(q.decoys), "explanation": q.explanation,
        },
        "locked": room().phase != "lobby",
    }


def _refuse(status: int, detail: str, reason: str, user: accounts.User | None = None):
    """An error the page turns into a screen, so it carries a `reason`."""
    raise HTTPException(status_code=status, detail={
        "message": detail, "reason": reason, "account": _account(user)})


def _seat_for(user: accounts.User | None) -> bsroster.Player:
    if user is None:
        _refuse(401, "Sign in to play.", "signed_out")
    person = bsroster.by_user(user.id)
    if person is None:
        _refuse(404, "Your account has no seat in this game yet. Open your "
                "invitation link once to claim it.", "no_seat", user)
    return person


SESSION = Cookie(default=None, alias=config.SESSION_COOKIE)


@router.get("/api/bullshit/me")
async def me(token: str = "", relay_session: str | None = SESSION) -> dict:
    """Who this is, from the invitation and the sign-in together.

    With a link: the seat it opens, if it is unclaimed (the page offers to
    claim it) or claimed by this account. Without one: this account's seat.
    """
    user = signed_in(relay_session)
    if not token:
        return _me(_seat_for(user), user)
    person = bsroster.by_token(token)
    if person is None:
        _refuse(404, "That link is not valid.", "bad_link", user)
    if person.user_id and (user is None or user.id != person.user_id):
        _refuse(403, "This invitation has already been claimed. Sign in with "
                "the account that claimed it.", "claimed_by_other", user)
    if not person.user_id and user is None:
        _refuse(401, "Sign in or make an account to claim your seat.",
                "signed_out")
    return _me(person, user)


@router.post("/api/bullshit/claim")
async def claim(body: ClaimBody, relay_session: str | None = SESSION) -> dict:
    user = signed_in(relay_session)
    if user is None:
        _refuse(401, "Sign in to claim your seat.", "signed_out")
    async with _lock:
        current = room()
        person = bsroster.by_token(body.token)
        if current.phase != "lobby" and not (person and person.user_id == user.id):
            # The roster froze when the game started (handoff §3).
            raise HTTPException(status_code=400, detail="The game has already started.")
        try:
            person = bsroster.claim(body.token, user.id, user.first_name,
                                    user.last_name, user.username)
        except bsroster.Refused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        bsroom.refresh(current, bsroster.all_players())
        await broadcast(current)
    return _me(person, user)


@router.post("/api/bullshit/question")
async def submit_question(body: QuestionBody,
                          relay_session: str | None = SESSION) -> dict:
    user = signed_in(relay_session)
    person = _seat_for(user)
    async with _lock:
        current = room()
        try:
            bsroom.can_submit(current)
            bsroster.submit_question(person.id, body.prompt, body.answer,
                                     body.decoys, body.explanation)
        except (bsroom.Rejected, bsroster.Refused) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        bsroom.refresh(current, bsroster.all_players())
        await broadcast(current)
    return _me(bsroster.by_id(person.id), user)


# --- the socket -----------------------------------------------------------

def _parse(raw: Any) -> tuple[str, dict[str, Any]] | str:
    if not isinstance(raw, dict):
        return "Messages are JSON objects."
    kind = raw.get("type")
    if kind in (READY, protocol.REQUEST_STATE, protocol.HEARTBEAT):
        return kind, {}
    if kind == BLUFF:
        return kind, {"wager": raw.get("wager")}
    if kind == GUESS:
        return kind, {"option": raw.get("option"), "wager": raw.get("wager")}
    if kind == HOST:
        action = raw.get("action")
        if action not in HOST_ACTIONS:
            return "Unknown host action."
        return kind, {"action": action}
    return "Unknown message type."


def _host_step(current: bsroom.BullshitRoom, action: str) -> bsroom.BullshitRoom:
    if action == "reset":
        return reset_room()
    if action == "start":
        bsroom.refresh(current, bsroster.all_players())
        bsroom.start(current, bsroster.questions())
    else:
        {"open": bsroom.open_discussion, "lock": bsroom.lock,
         "reveal": bsroom.reveal, "next": bsroom.next_round}[action](current)
    return current


@router.websocket("/ws/bullshit")
async def socket_endpoint(socket: WebSocket, host_key: str = "", as_: str = Query("", alias="as")):
    await socket.accept()

    person = None
    if as_ == "host":
        if not host_authorised(host_key or None, socket.cookies.get(COOKIE_NAME)):
            await socket.close(code=protocol.CLOSE_UNKNOWN)
            return
    else:
        user = signed_in(socket.cookies.get(config.SESSION_COOKIE))
        person = bsroster.by_user(user.id) if user else None
        if person is None or not person.registered:
            await socket.close(code=protocol.CLOSE_UNKNOWN)
            return
    viewer = person.id if person is not None else HOST_VIEWER
    _cancel_grace(viewer)

    old = _sockets.get(viewer)
    _sockets[viewer] = socket        # publish first, or the old one's finally
    if old is not None:              # marks this viewer away
        with contextlib.suppress(Exception):
            await old.close(code=protocol.CLOSE_SUPERSEDED)

    try:
        async with _lock:
            current = room()
            bsroom.refresh(current, bsroster.all_players())
            if person is not None:
                bsroom.on_connect(current, person.id)
            await broadcast(current)

        while True:
            parsed = _parse(await socket.receive_json())
            if isinstance(parsed, str):
                await _send(socket, protocol.error_message(parsed))
                continue
            kind, fields = parsed

            async with _lock:
                current = room()
                try:
                    if kind == HOST:
                        if viewer != HOST_VIEWER:
                            raise bsroom.Rejected("Only the host can do that.")
                        current = _host_step(current, fields["action"])
                    elif kind in (READY, BLUFF, GUESS):
                        if person is None:
                            raise bsroom.Rejected("The host does not play.")
                        if kind == READY:
                            bsroom.ready(current, person.id)
                        elif kind == BLUFF:
                            bsroom.bluff(current, person.id, fields["wager"])
                        else:
                            bsroom.guess(current, person.id, fields["option"],
                                         fields["wager"])
                    else:                       # request_state / heartbeat
                        await _send(socket, snapshot(current, viewer))
                        continue
                except bsroom.Rejected as exc:
                    await _send(socket, protocol.error_message(str(exc)))
                    continue
                await broadcast(current)
    except WebSocketDisconnect:
        pass
    finally:
        if _sockets.get(viewer) is socket:
            del _sockets[viewer]
            async with _lock:
                current = room()
                if person is not None:
                    # Not gone yet: a locked phone screen drops the socket.
                    bsroom.on_drop(current, person.id)
                    _cancel_grace(person.id)
                    _grace[person.id] = asyncio.create_task(
                        _leave_after_grace(person.id))
                await broadcast(current)
