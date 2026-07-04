"""FastAPI app: REST routes, WebSocket endpoint, ConnectionManager.

Glue only (docs/ARCHITECTURE.md §2): calls the engine on incoming messages,
hands timer scheduling to TimerService, broadcasts a fresh state_snapshot
after every change, and evicts stale matches (T3.6). All match mutations run
under the per-match lock (T3.2).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import config, protocol
from backend.engine import EngineResult, RelayEngine
from backend.models import Match
from backend.registry import GameRegistry
from backend.state import InMemoryStateStore, MatchLocks
from backend.timers import TimerService

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
EVICTION_SWEEP_SECONDS = 60


class ConnectionManager:
    """One live socket per (match_id, player_id); fanout of personalised snapshots."""

    def __init__(self) -> None:
        self._sockets: dict[tuple[str, str], WebSocket] = {}

    def get(self, match_id: str, player_id: str) -> WebSocket | None:
        return self._sockets.get((match_id, player_id))

    def register(self, match_id: str, player_id: str, socket: WebSocket) -> None:
        self._sockets[(match_id, player_id)] = socket

    def unregister(self, match_id: str, player_id: str, socket: WebSocket) -> bool:
        """Remove the entry only if `socket` is still current (a superseded
        socket must not knock out its replacement). Returns True if removed."""
        if self._sockets.get((match_id, player_id)) is socket:
            del self._sockets[(match_id, player_id)]
            return True
        return False

    def match_sockets(self, match_id: str) -> list[tuple[str, WebSocket]]:
        return [
            (player_id, socket)
            for (mid, player_id), socket in list(self._sockets.items())
            if mid == match_id
        ]

    def drop_match(self, match_id: str) -> list[WebSocket]:
        sockets = [socket for _, socket in self.match_sockets(match_id)]
        self._sockets = {
            key: socket for key, socket in self._sockets.items() if key[0] != match_id
        }
        return sockets

    async def send(self, socket: WebSocket, payload: dict) -> None:
        try:
            await socket.send_json(payload)
        except Exception:
            pass  # a dying socket must never take the match down

    async def broadcast_state(self, match: Match) -> None:
        """Personalised snapshot to every connected player of the match."""
        for player_id, socket in self.match_sockets(match.id):
            await self.send(socket, protocol.state_snapshot(match, player_id))

    async def broadcast(self, match_id: str, payload: dict) -> None:
        for _, socket in self.match_sockets(match_id):
            await self.send(socket, payload)


async def _timer_fired(match_id: str, player_id: str, kind: str) -> None:
    match = await store.get(match_id)
    if match is None:
        return
    async with locks.for_match(match_id):
        touch(match_id)
        hook = {"rest": engine.on_rest_expired, "holding": engine.on_holding_expired}[kind]
        result = hook(match, player_id)
        if result.changed:
            await apply_and_broadcast(match, result)


store = InMemoryStateStore()
locks = MatchLocks()
engine = RelayEngine(GameRegistry())
manager = ConnectionManager()
timers = TimerService(_timer_fired)
last_activity: dict[str, float] = {}
_last_submit: dict[tuple[str, str], float] = {}


def touch(match_id: str) -> None:
    last_activity[match_id] = time.monotonic()


async def apply_and_broadcast(match: Match, result: EngineResult) -> None:
    """Apply an EngineResult's timer instructions and fan out the change."""
    timers.apply_result(match.id, result)
    if result.winner_team_id:
        timers.cancel_match(match.id)
    await manager.broadcast_state(match)
    for event in result.events:
        await manager.broadcast(match.id, protocol.event_message(event))
    for team_id in result.advanced_team_ids:
        await manager.broadcast(
            match.id, protocol.stage_advanced(team_id, match.teams[team_id].stage)
        )
    if result.winner_team_id:
        await manager.broadcast(match.id, protocol.match_won(result.winner_team_id))


async def evict_stale(now: float | None = None) -> list[str]:
    """Evict matches with no activity for MATCH_TTL_SECONDS (T3.6).

    Timer fires and messages both refresh activity, so live matches survive;
    finished and abandoned ones age out.
    """
    now = time.monotonic() if now is None else now
    evicted = []
    for match in await store.all():
        if now - last_activity.get(match.id, now) > config.MATCH_TTL_SECONDS:
            timers.cancel_match(match.id)
            for socket in manager.drop_match(match.id):
                with contextlib.suppress(Exception):
                    await socket.close(code=protocol.CLOSE_UNKNOWN)
            await store.remove(match.id)
            locks.discard(match.id)
            last_activity.pop(match.id, None)
            evicted.append(match.id)
    return evicted


async def _eviction_loop() -> None:
    while True:
        await asyncio.sleep(EVICTION_SWEEP_SECONDS)
        await evict_stale()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    sweeper = asyncio.create_task(_eviction_loop())
    yield
    sweeper.cancel()


app = FastAPI(title="The Relay", lifespan=lifespan)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# --- REST (T3.3) ---

class JoinBody(BaseModel):
    name: str
    team_id: str | None = None


@app.get("/", response_model=None)
async def index():
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse("<h1>The Relay</h1><p>Frontend lands in Phase 5.</p>")


@app.get("/api/config")
async def get_config() -> dict:
    return {
        "teams": list(config.TEAM_IDS),
        "rest_seconds": config.REST_SECONDS,
        "holding_seconds": config.HOLDING_SECONDS,
        "players_per_team": config.PLAYERS_PER_TEAM,
        "stage_count": config.STAGE_COUNT,
    }


@app.post("/api/matches")
async def create_match() -> dict:
    match = engine.create_match()
    await store.add(match)
    touch(match.id)
    return {"match": match.public()}


async def _require_match(match_id: str) -> Match:
    match = await store.get(match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found.")
    return match


@app.post("/api/matches/{match_id}/join")
async def join_match(match_id: str, body: JoinBody) -> dict:
    match = await _require_match(match_id)
    async with locks.for_match(match_id):
        touch(match_id)
        try:
            player, result = engine.join_match(match, body.name, body.team_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        await apply_and_broadcast(match, result)
    return {"player": player.public(), "match": match.public()}


@app.get("/api/matches/{match_id}")
async def get_match(match_id: str) -> dict:
    match = await _require_match(match_id)
    return {"match": match.public()}


# --- WebSocket (T3.4) ---

def _too_fast(match_id: str, player_id: str) -> bool:
    now = time.monotonic()
    last = _last_submit.get((match_id, player_id), 0.0)
    if (now - last) * 1000 < config.SUBMIT_MIN_INTERVAL_MS:
        return True
    _last_submit[(match_id, player_id)] = now
    return False


@app.websocket("/ws/matches/{match_id}")
async def websocket_endpoint(socket: WebSocket, match_id: str, player_id: str = ""):
    await socket.accept()
    match = await store.get(match_id)
    if match is None or player_id not in match.players:
        await socket.close(code=protocol.CLOSE_UNKNOWN)
        return

    # One socket per player: the new connection supersedes the old.
    old = manager.get(match_id, player_id)
    if old is not None:
        with contextlib.suppress(Exception):
            await old.close(code=protocol.CLOSE_SUPERSEDED)
    manager.register(match_id, player_id, socket)

    async with locks.for_match(match_id):
        touch(match_id)
        player = match.players[player_id]
        if not player.connected:
            # True reconnect: resume resting/holding; fresh main while solving.
            result = engine.on_reconnect(match, player_id)
            await apply_and_broadcast(match, result)
        else:
            await manager.broadcast_state(match)
        await manager.send(socket, protocol.state_snapshot(match, player_id))

    try:
        while True:
            raw = await socket.receive_json()
            parsed = protocol.parse_client_message(raw)
            if isinstance(parsed, str):
                await manager.send(socket, protocol.error_message(parsed))
                continue
            msg_type, fields = parsed

            if msg_type in protocol.SUBMIT_TYPES:
                if _too_fast(match_id, player_id):
                    await manager.send(socket, protocol.error_message("Too fast."))
                    continue
                submit = (
                    engine.submit_main
                    if msg_type == protocol.SUBMIT_ANSWER
                    else engine.submit_holding
                )
                async with locks.for_match(match_id):
                    touch(match_id)
                    result = submit(
                        match, player_id, fields["puzzle_id"], fields["answer"]
                    )
                    if not result.ok:
                        # Protocol §2.1 wording for the stale-puzzle case.
                        text = result.error or "Rejected."
                        if text == "stale or unknown puzzle":
                            text = "Puzzle is no longer active"
                        await manager.send(socket, protocol.error_message(text))
                        continue
                    await apply_and_broadcast(match, result)
            else:  # request_state / heartbeat
                async with locks.for_match(match_id):
                    touch(match_id)
                    await manager.send(socket, protocol.state_snapshot(match, player_id))
    except WebSocketDisconnect:
        pass
    finally:
        if manager.unregister(match_id, player_id, socket):
            if await store.get(match_id) is not None:
                async with locks.for_match(match_id):
                    result = engine.on_disconnect(match, player_id)
                    if result.changed:
                        await manager.broadcast_state(match)
