"""FastAPI app: REST routes, WebSocket endpoint, ConnectionManager.

Glue only (docs/ARCHITECTURE.md §2): calls the engine on incoming messages,
hands timer scheduling to TimerService, broadcasts a fresh state_snapshot
after every change, and evicts stale matches (T3.6). All match mutations run
under the per-match lock (T3.2).
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import config, protocol
from backend.engine import EngineResult, RelayEngine
from backend.models import LEADER_ONLY_EVENT_KINDS, Match
from backend.registry import REGISTERED_MODULES, GameRegistry
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


async def _timer_fired(match_id: str, scope_id: str, kind: str) -> None:
    match = await store.get(match_id)
    if match is None:
        return
    async with locks.for_match(match_id):
        touch(match_id)
        if kind.startswith("duel_"):
            result = engine.on_duel_timer(match, scope_id, kind)
        elif kind == "puzzle":
            # A board deadline, on its own `fuse:<player_id>` scope so it can
            # run alongside a wait timer. The engine owns the scope's shape;
            # this only has to hand back the player id.
            result = engine.on_puzzle_expired(match, scope_id.split(":", 1)[1])
        else:
            result = engine.on_wait_expired(match, scope_id)
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
        payload = protocol.event_message(event)
        if match.status != "lobby" and event.kind in LEADER_ONLY_EVENT_KINDS:
            # Who cleared / who lost cleared status is leader-only knowledge.
            for player_id, socket in manager.match_sockets(match.id):
                player = match.players.get(player_id)
                if player is not None and player.is_leader:
                    await manager.send(socket, payload)
        else:
            await manager.broadcast(match.id, payload)
    for team_id in result.advanced_team_ids:
        await manager.broadcast(
            match.id, protocol.level_advanced(team_id, match.teams[team_id].level)
        )
    if result.perk_used:
        await manager.broadcast(
            match.id,
            protocol.perk_used(
                result.perk_used["perk_id"], result.perk_used["by_team_id"]
            ),
        )
    if result.duel_result:
        # Both teams watched the same duel resolve, so the outcome is public.
        await manager.broadcast(match.id, protocol.duel_result(result.duel_result))
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


def _serve_page(filename: str):
    page = FRONTEND_DIR / filename
    if page.exists():
        return FileResponse(page)
    return HTMLResponse(f"<h1>The Relay</h1><p>{filename} not found.</p>")


@app.get("/", response_model=None)
async def landing():
    return _serve_page("landing.html")


@app.get("/play", response_model=None)
async def play_app():
    return _serve_page("index.html")


@app.get("/explore", response_model=None)
async def explore_page():
    return _serve_page("explore.html")


@app.get("/games", response_model=None)
async def games_page():
    return _serve_page("games.html")


@app.get("/api/config")
async def get_config() -> dict:
    return {
        "teams": list(config.TEAM_IDS),
        # The ceiling the registry implies, not a hand-kept number: the client
        # clamps the host's max-players stepper to it.
        "players_per_team": engine.max_players_ceiling(),
        "max_players_ceiling": engine.max_players_ceiling(),
        "min_players_default": config.MIN_PLAYERS_PER_TEAM,
        "min_level_count": config.MIN_LEVEL_COUNT,
        "max_level_count": config.max_level_count(),
        # The host's duel-round picker: the bounds it clamps to, and the values
        # it offers inside them. `null` in the picker means each duel game
        # keeps the window it declares for itself.
        "duel_round_seconds_min": config.DUEL_ROUND_SECONDS_MIN,
        "duel_round_seconds_max": config.DUEL_ROUND_SECONDS_MAX,
        "duel_round_seconds_choices": list(config.DUEL_ROUND_SECONDS_CHOICES),
        "team_name_max": config.TEAM_NAME_MAX,
        "level_count": config.LEVEL_COUNT,
        "wait_seconds": config.WAIT_SECONDS,
        "perks": {perk_id: dict(perk) for perk_id, perk in config.PERKS.items()},
        # `fixed`/`required` reach the client because the lobby mirrors both
        # rules: a fixed role shows no game picker, and a missing required role
        # is one of the reasons the start button stays disabled.
        "roles": {
            role_id: {
                "name": role["name"],
                "games": role["games"],
                "fixed": bool(role.get("fixed")),
                "required": bool(role.get("required")),
            }
            for role_id, role in config.ROLES.items()
        },
        "library": engine.registry.library(),
        # The duel catalogue, for the host's round-timer note: the Grandmaster
        # never picks a duel, but the host is setting the clock for all of them.
        "duels": engine.registry.duel_library(),
    }


# --- Practice mode (/explore) ---
# Stateless: the client keeps the seed and sends it back with the answer; the
# server regenerates the (deterministic) puzzle to validate. Nothing is stored,
# and `public()` still strips the answer from what the client sees.

PRACTICE_MODULES = {module.id: module for module in REGISTERED_MODULES}


class PracticeCheckBody(BaseModel):
    seed: int
    kind: str = "main"
    answer: str


def _missions(module) -> list[dict]:
    """A game's authored practice boards, or [] if it offers none.

    Duck-typed on purpose: `missions`/`generate_mission` are not part of the
    GameModule contract, so a game that wants a training ladder can add one
    without every other game growing a method it has no use for.
    """
    catalogue = getattr(module, "missions", None)
    return list(catalogue()) if callable(catalogue) else []


def _practice_puzzle(game_id: str, kind: str, seed: int):
    module = PRACTICE_MODULES.get(game_id)
    if module is None:
        raise HTTPException(status_code=404, detail=f"unknown game '{game_id}'")
    if kind in ("main", "holding"):
        generate = module.generate_main if kind == "main" else module.generate_holding
        return module, generate(seed)
    if kind in {mission["id"] for mission in _missions(module)}:
        # An authored board: the same bomb every time, which is why these are
        # practice-only and never reach a match.
        return module, module.generate_mission(kind, seed)
    raise HTTPException(
        status_code=400, detail=f"unknown practice kind '{kind}' for '{game_id}'"
    )


@app.get("/api/practice/{game_id}/missions")
async def practice_missions(game_id: str) -> dict:
    module = PRACTICE_MODULES.get(game_id)
    if module is None:
        raise HTTPException(status_code=404, detail=f"unknown game '{game_id}'")
    return {"missions": _missions(module)}


@app.post("/api/practice/{game_id}")
async def practice_new(game_id: str, kind: str = "main") -> dict:
    seed = random.randrange(2**31)
    _, puzzle = _practice_puzzle(game_id, kind, seed)
    return {"seed": seed, "kind": kind, "puzzle": puzzle.public()}


@app.post("/api/practice/{game_id}/check")
async def practice_check(game_id: str, body: PracticeCheckBody) -> dict:
    module, puzzle = _practice_puzzle(game_id, body.kind, body.seed)
    return {"correct": module.check(puzzle, body.answer)}


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

def _run_lobby_action(match: Match, player_id: str, fields: dict) -> EngineResult:
    action = fields["action"]
    if action == "set_team":
        return engine.set_team(match, player_id, fields.get("team_id", ""))
    if action == "move":
        return engine.host_move(
            match, player_id, fields.get("target_id", ""), fields.get("team_id", "")
        )
    if action == "kick":
        return engine.host_kick(match, player_id, fields.get("target_id", ""))
    if action == "set_min_players":
        return engine.host_set_min_players(match, player_id, fields.get("value", 0))
    if action == "set_max_players":
        return engine.host_set_max_players(match, player_id, fields.get("value", 0))
    if action == "set_level_count":
        return engine.host_set_level_count(match, player_id, fields.get("value", 0))
    if action == "set_duel_seconds":
        return engine.host_set_duel_seconds(match, player_id, fields.get("value", 0))
    if action == "set_team_name":
        return engine.host_set_team_name(
            match, player_id, fields.get("team_id", ""), fields.get("name", "")
        )
    if action == "start":
        return engine.host_start(match, player_id)
    if action == "cancel_session":
        return engine.host_cancel_session(match, player_id)
    if action == "end_session":
        return engine.host_end_session(match, player_id)
    if action == "leave":
        return engine.leave_match(match, player_id)
    if action == "claim_leader":
        return engine.claim_leader(match, player_id)
    if action == "assign_role":
        return engine.assign_role(
            match, player_id, fields.get("target_id", ""), fields.get("role_id", "")
        )
    if action == "assign_game":
        return engine.assign_game(
            match, player_id, fields.get("target_id", ""), fields.get("game_id", "")
        )
    return engine.claim_host(match, player_id)  # claim_host


async def _shutter_cancelled(match_id: str) -> None:
    """Close every socket of a cancelled lobby and drop the match.

    The same teardown `evict_stale` does, with the close code that tells the
    client this was the host's doing rather than a lost match.
    """
    timers.cancel_match(match_id)
    for open_socket in manager.drop_match(match_id):
        with contextlib.suppress(Exception):
            await open_socket.close(code=protocol.CLOSE_CANCELLED)
    await store.remove(match_id)
    locks.discard(match_id)
    last_activity.pop(match_id, None)


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

            if msg_type == protocol.LOBBY_ACTION:
                async with locks.for_match(match_id):
                    touch(match_id)
                    result = _run_lobby_action(match, player_id, fields)
                    if not result.ok:
                        await manager.send(
                            socket, protocol.error_message(result.error or "Rejected.")
                        )
                        continue
                    for kicked_id in result.kicked_player_ids:
                        kicked_socket = manager.get(match_id, kicked_id)
                        if kicked_socket is not None:
                            manager.unregister(match_id, kicked_id, kicked_socket)
                            with contextlib.suppress(Exception):
                                await kicked_socket.close(code=protocol.CLOSE_KICKED)
                    await apply_and_broadcast(match, result)
                    if match.status == "cancelled":
                        # The lobby is gone. Everyone has the snapshot saying
                        # so; close them out and drop the match rather than
                        # leaving a dead code someone can still join.
                        await _shutter_cancelled(match_id)
                        return
            elif msg_type == protocol.SUBMIT_ANSWER:
                if _too_fast(match_id, player_id):
                    await manager.send(socket, protocol.error_message("Too fast."))
                    continue
                async with locks.for_match(match_id):
                    touch(match_id)
                    result = engine.submit_answer(
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
            elif msg_type == protocol.DUEL_CHOICE:
                if _too_fast(match_id, player_id):
                    await manager.send(socket, protocol.error_message("Too fast."))
                    continue
                async with locks.for_match(match_id):
                    touch(match_id)
                    result = engine.duel_choice(
                        match, player_id, fields["duel_id"],
                        fields["round"], fields["choice"],
                    )
                    if not result.ok:
                        await manager.send(
                            socket, protocol.error_message(result.error or "Rejected.")
                        )
                        continue
                    await apply_and_broadcast(match, result)
            elif msg_type in (
                protocol.CHOOSE_WAIT,
                protocol.CHOOSE_BONUS,
                protocol.BUY_PERK,
                protocol.GIVE_LEADER,
            ):
                async with locks.for_match(match_id):
                    touch(match_id)
                    if msg_type == protocol.CHOOSE_WAIT:
                        result = engine.choose_wait(match, player_id)
                    elif msg_type == protocol.CHOOSE_BONUS:
                        result = engine.choose_bonus(match, player_id)
                    elif msg_type == protocol.BUY_PERK:
                        result = engine.buy_perk(
                            match,
                            player_id,
                            fields["perk_id"],
                            fields.get("target_id"),
                        )
                    else:
                        result = engine.give_leader(
                            match, player_id, fields["target_id"]
                        )
                    if not result.ok:
                        await manager.send(
                            socket, protocol.error_message(result.error or "Rejected.")
                        )
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
