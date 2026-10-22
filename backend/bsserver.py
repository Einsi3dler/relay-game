"""BULLSHIT QUIZ over HTTP and one WebSocket.

Everything the game needs from the web server, as one router `main.py`
includes, so the shared app file gains two lines rather than two hundred. The
rules are `backend/bsquiz.py`, the state machine `backend/bsroom.py`, the
store `backend/bsroster.py`. This file only moves messages between them.

One room at a time. People join it the way they would any web game: sign up
or sign in on the site, open Bullshit Quiz with the join code the host
shares (`/bullshit?code=K7QM`), and their account gets a seat. The code only
admits people while the lobby is open. No roster file, no email addresses,
no invitation links.

Two kinds of viewer. A player is whoever is signed in to an account that
holds a seat, so they resume from any device by signing in. The host proves
it with the host key or the cookie that key earned, and the host page asks
for that view explicitly (`?as=host`), because one browser can hold both a
host cookie and a player's sign-in.
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
import time
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
PICK = "bs_pick"              # any player, once: {perk} from their offers
PERK = "bs_perk"              # play your perk: {target?, option?, wager?, words?, play?, copy?}
HOST = "bs_host"              # host: {action, player?, play?, step?}
HOST_ACTIONS = ("start", "open", "lock", "lock_anyway", "reveal", "next",
                "reset", "undo_reset", "drop", "remove", "new_game",
                "extend", "end_talk", "floor_done", "slip")
PERK_FIELDS = ("target", "option", "wager", "words", "play", "copy")

router = APIRouter()

_room: bsroom.BullshitRoom | None = None
_lock = asyncio.Lock()
_sockets: dict[str, WebSocket] = {}
_grace: dict[str, asyncio.Task] = {}      # viewer -> pending "they left" check
_undo: tuple[bsroom.BullshitRoom, float] | None = None   # the room a reset replaced
_ticker: asyncio.Task | None = None       # lets the room's clocks run out


# --- the room -------------------------------------------------------------

def startup() -> None:
    """Open the store. Everybody in it joined through the site."""
    bsroster.connect()


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


async def _tick_forever() -> None:
    """The discussion clock, floor perks and reaction windows all run out on
    their own; this is what notices. Broadcasts only when something moved."""
    while True:
        await asyncio.sleep(config.BS_TICK_SECONDS)
        async with _lock:
            current = room()
            if bsroom.tick(current, time.monotonic()):
                await broadcast(current)


def _ensure_ticker() -> None:
    """Started by the first socket, on whichever event loop is serving it."""
    global _ticker
    loop = asyncio.get_running_loop()
    if _ticker is None or _ticker.done() or _ticker.get_loop() is not loop:
        _ticker = loop.create_task(_tick_forever())


def _cancel_grace(player_id: str) -> None:
    task = _grace.pop(player_id, None)
    if task is not None:
        task.cancel()


def reset_room() -> bsroom.BullshitRoom:
    """Back to the lobby. The old room is kept for BS_UNDO_SECONDS so a
    misclick on "Reset the room" mid-game is not the end of the evening."""
    global _room, _undo
    if _room is not None and _room.phase != "lobby":
        _undo = (_room, time.monotonic())
    # Same code: people already have it in the group chat.
    _room = bsroom.create_room(bsroster.all_players(),
                               code=_room.code if _room is not None else None)
    for viewer in _sockets:
        bsroom.on_connect(_room, viewer)
    return _room


def new_game() -> bsroom.BullshitRoom:
    """A new game night: every seat and question cleared, and a new code so
    last week's link stops letting people in. Not undoable."""
    global _room, _undo
    bsroster.clear()
    _undo = None
    _room = bsroom.create_room([])
    return _room


def can_undo_reset() -> bool:
    return _undo is not None and time.monotonic() - _undo[1] <= config.BS_UNDO_SECONDS


def undo_reset() -> bsroom.BullshitRoom:
    """Put back the room the last reset replaced, with who is here now."""
    global _room, _undo
    if not can_undo_reset():
        _undo = None
        raise bsroom.Rejected("Too late to undo the reset.")
    _room, _undo = _undo[0], None
    for seat in _room.seats.values():
        seat.connected = seat.id in _sockets
        seat.reconnecting = False
    return _room


def snapshot(current: bsroom.BullshitRoom, viewer: str) -> dict[str, Any]:
    is_host = viewer == HOST_VIEWER
    state = current.public(None if is_host else viewer, is_host=is_host)
    if is_host:
        state["can_undo_reset"] = can_undo_reset()
        state["code"] = current.code
    return {"type": STATE, "state": state}


async def _kick_seatless() -> None:
    """Close the socket of every player whose seat no longer exists (removed
    from the lobby, or a new game night). A socket is tied to the seat it
    opened with, so a player who joins again needs a fresh one; the page
    sees CLOSE_KICKED, shows the join screen, and reconnects after joining."""
    seats = {p.id for p in bsroster.all_players()}
    for viewer in [v for v in _sockets if v != HOST_VIEWER and v not in seats]:
        socket = _sockets.pop(viewer)
        _cancel_grace(viewer)
        with contextlib.suppress(Exception):
            await socket.close(code=protocol.CLOSE_KICKED)


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
async def player_page():
    """The player page. `?code=` from the host's link pre-fills the join box."""
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


# --- REST: who am I, joining, my question --------------------------------

class JoinBody(BaseModel):
    code: str


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
        "joined": True,
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
        "message": detail, "reason": reason, "account": _account(user),
        "locked": room().phase != "lobby"})


def _seat_for(user: accounts.User | None) -> bsroster.Player:
    if user is None:
        _refuse(401, "Sign in to play.", "signed_out")
    person = bsroster.by_user(user.id)
    if person is None:
        _refuse(404, "You haven't joined this game yet.", "not_joined", user)
    return person


SESSION = Cookie(default=None, alias=config.SESSION_COOKIE)


@router.get("/api/bullshit/me")
async def me(relay_session: str | None = SESSION) -> dict:
    """This account's seat, or why there isn't one (signed out, or not joined
    yet, with whether the lobby is still open)."""
    user = signed_in(relay_session)
    return _me(_seat_for(user), user)


@router.post("/api/bullshit/join")
async def join(body: JoinBody, relay_session: str | None = SESSION) -> dict:
    user = signed_in(relay_session)
    if user is None:
        _refuse(401, "Sign in to join.", "signed_out")
    async with _lock:
        current = room()
        existing = bsroster.by_user(user.id)
        if existing is not None:
            return _me(existing, user)
        if current.phase != "lobby":
            # The roster froze when the game started (handoff §3).
            raise HTTPException(status_code=400, detail="This game has already started.")
        if "".join(body.code.split()).upper() != current.code:
            raise HTTPException(status_code=400, detail="That code doesn't match tonight's game.")
        person = bsroster.join(user.id, user.first_name, user.last_name, user.username)
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
    if kind == PICK:
        return kind, {"perk": raw.get("perk")}
    if kind == PERK:
        return kind, {key: raw.get(key) for key in PERK_FIELDS}
    if kind == HOST:
        action = raw.get("action")
        if action not in HOST_ACTIONS:
            return "Unknown host action."
        return kind, {"action": action, "player": raw.get("player"),
                      "play": raw.get("play"), "step": raw.get("step", 1)}
    return "Unknown message type."


def _host_step(current: bsroom.BullshitRoom, action: str,
               fields: dict[str, Any] | None = None) -> bsroom.BullshitRoom:
    fields = fields or {}
    player = fields.get("player")
    if action == "reset":
        return reset_room()
    if action == "undo_reset":
        return undo_reset()
    if action == "new_game":
        return new_game()
    if action == "remove":
        if current.phase != "lobby":
            raise bsroom.Rejected("Players can only be removed in the lobby. Use Mark gone during a game.")
        if not isinstance(player, str) or player not in current.seats:
            raise bsroom.Rejected("That player is not in this game.")
        bsroster.remove(player)
        bsroom.refresh(current, bsroster.all_players())
        return current
    if action == "start":
        bsroom.refresh(current, bsroster.all_players())
        bsroom.start(current, bsroster.questions())
    elif action == "drop":
        if not isinstance(player, str):
            raise bsroom.Rejected("Say which player.")
        _cancel_grace(player)
        bsroom.mark_gone(current, player)
    elif action == "lock_anyway":
        bsroom.lock(current, force=True)
    elif action == "extend":
        bsroom.extend_talk(current)
    elif action == "end_talk":
        bsroom.end_talk(current)
    elif action == "floor_done":
        bsroom.floor_done(current)
    elif action == "slip":
        bsroom.slip(current, fields.get("play"), fields.get("step", 1))
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
    _ensure_ticker()

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
                        current = _host_step(current, fields["action"], fields)
                        if fields["action"] in ("remove", "new_game"):
                            await _kick_seatless()
                    elif kind in (READY, BLUFF, GUESS, PICK, PERK):
                        if person is None:
                            raise bsroom.Rejected("The host does not play.")
                        seat = current.seats.get(person.id)
                        if seat is not None and not seat.connected:
                            # Marked gone by the host while still on the page,
                            # and now acting: they are back. Still relegated,
                            # but their own question becomes playable again.
                            bsroom.on_connect(current, person.id)
                        if kind == READY:
                            bsroom.ready(current, person.id)
                        elif kind == BLUFF:
                            bsroom.bluff(current, person.id, fields["wager"])
                        elif kind == PICK:
                            bsroom.pick_perk(current, person.id, fields["perk"])
                        elif kind == PERK:
                            bsroom.play_perk(current, person.id, fields)
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
