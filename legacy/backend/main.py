from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.game import POWERUPS, RelayGameEngine
from backend.models import ROLES, TEAM_IDS
from backend.state import InMemoryStateStore


ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

app = FastAPI(title="The Relay MVP")
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

engine = RelayGameEngine()
store = InMemoryStateStore()


class JoinMatchRequest(BaseModel):
    name: str
    team_id: str | None = None
    role: str | None = None


class ConnectionManager:
    def __init__(self) -> None:
        self.active: dict[str, dict[str, WebSocket]] = {}

    async def connect(self, match_id: str, player_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active.setdefault(match_id, {})[player_id] = websocket

    def disconnect(self, match_id: str, player_id: str) -> None:
        sockets = self.active.get(match_id)
        if not sockets:
            return
        sockets.pop(player_id, None)
        if not sockets:
            self.active.pop(match_id, None)

    async def send_to_player(self, match_id: str, player_id: str, payload: dict[str, Any]) -> None:
        socket = self.active.get(match_id, {}).get(player_id)
        if socket:
            await socket.send_json(payload)

    async def broadcast_state(self, match_id: str) -> None:
        match = await store.require(match_id)
        for player_id, socket in list(self.active.get(match_id, {}).items()):
            await socket.send_json(
                {"type": "state_snapshot", "state": match.public(player_id)}
            )

    async def broadcast(self, match_id: str, payload: dict[str, Any]) -> None:
        for socket in list(self.active.get(match_id, {}).values()):
            await socket.send_json(payload)


manager = ConnectionManager()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


@app.get("/api/config")
async def config() -> dict[str, Any]:
    return {
        "roles": ROLES,
        "teams": TEAM_IDS,
        "powerups": POWERUPS,
    }


@app.post("/api/matches")
async def create_match() -> dict[str, Any]:
    match = engine.create_match()
    await store.add(match)
    return {"match": match.public()}


@app.post("/api/matches/{match_id}/join")
async def join_match(match_id: str, request: JoinMatchRequest) -> dict[str, Any]:
    match = await _match_or_404(match_id)
    try:
        player = engine.join_match(match, request.name, request.team_id, request.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await manager.broadcast_state(match_id)
    return {"player": player.public(), "match": match.public(player.id)}


@app.get("/api/matches/{match_id}")
async def get_match(match_id: str) -> dict[str, Any]:
    match = await _match_or_404(match_id)
    return {"match": match.public()}


@app.websocket("/ws/matches/{match_id}")
async def match_socket(websocket: WebSocket, match_id: str, player_id: str) -> None:
    match = await store.get(match_id)
    if match is None or player_id not in match.players:
        await websocket.close(code=4404)
        return

    await manager.connect(match_id, player_id, websocket)
    result = engine.reconnect_player(match, player_id)
    await manager.send_to_player(
        match_id,
        player_id,
        {"type": "state_snapshot", "state": match.public(player_id)},
    )
    await manager.broadcast_state(match_id)
    if result.payload:
        await manager.broadcast(match_id, {"type": result.payload["type"], "player_id": player_id})

    try:
        while True:
            message = await websocket.receive_json()
            await _handle_socket_message(match_id, player_id, message)
    except WebSocketDisconnect:
        manager.disconnect(match_id, player_id)
        match = await store.require(match_id)
        result = engine.disconnect_player(match, player_id)
        await manager.broadcast_state(match_id)
        await manager.broadcast(match_id, {"type": "player_disconnected", "player_id": player_id})


async def _handle_socket_message(match_id: str, player_id: str, message: dict[str, Any]) -> None:
    match = await store.require(match_id)
    action = message.get("type")
    result = None

    try:
        if action == "submit_puzzle":
            result = engine.submit_puzzle(
                match,
                player_id,
                str(message.get("puzzle_id", "")),
                str(message.get("answer", "")),
            )
        elif action == "submit_grind":
            result = engine.submit_grind(
                match,
                player_id,
                str(message.get("puzzle_id", "")),
                str(message.get("answer", "")),
            )
        elif action == "buy_powerup":
            result = engine.buy_powerup(match, player_id, str(message.get("powerup", "")))
        elif action == "activate_shield":
            result = engine.activate_shield(match, player_id)
        elif action == "deploy_powerup":
            result = engine.deploy_powerup(
                match,
                player_id,
                str(message.get("powerup", "")),
                str(message.get("target_team_id", "")),
            )
        elif action in {"heartbeat", "request_state"}:
            await manager.send_to_player(
                match_id,
                player_id,
                {"type": "state_snapshot", "state": match.public(player_id)},
            )
            return
        else:
            await manager.send_to_player(
                match_id,
                player_id,
                {"type": "error", "error": "Unknown message type."},
            )
            return
    except ValueError as exc:
        await manager.send_to_player(match_id, player_id, {"type": "error", "error": str(exc)})
        return

    if result and not result.ok:
        await manager.send_to_player(match_id, player_id, {"type": "error", "error": result.error})
    if result and result.event:
        await manager.broadcast(match_id, {"type": "event_logged", "event": result.event.public()})
    if action == "deploy_powerup" and result and result.ok and result.payload and not result.payload.get("blocked"):
        await manager.broadcast(
            match_id,
            {
                "type": "sabotage_applied",
                "effect": result.payload["effect"],
                "target_team_id": result.payload["target_team_id"],
                "duration": result.payload["duration"],
            },
        )
    if result and result.ok and result.event and result.event.kind == "advance":
        await manager.broadcast(match_id, {"type": "level_advanced", "event": result.event.public()})
    if result and result.ok and result.event and result.event.kind == "finish":
        await manager.broadcast(match_id, {"type": "match_finished", "event": result.event.public()})
    await manager.broadcast_state(match_id)


async def _match_or_404(match_id: str):
    match = await store.get(match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found.")
    return match
