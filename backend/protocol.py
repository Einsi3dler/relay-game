"""Message (de)serialisation helpers and type constants for the WebSocket protocol.

Shapes are exactly docs/WEBSOCKET_PROTOCOL.md §2. Builders return JSON-safe
dicts; `main.py` owns the sockets.
"""

from __future__ import annotations

from typing import Any

from backend.models import Event, Match

# Client → server
SUBMIT_ANSWER = "submit_answer"
SUBMIT_HOLDING = "submit_holding"
REQUEST_STATE = "request_state"
HEARTBEAT = "heartbeat"
LOBBY_ACTION = "lobby_action"
SUBMIT_TYPES = (SUBMIT_ANSWER, SUBMIT_HOLDING)
CLIENT_TYPES = (SUBMIT_ANSWER, SUBMIT_HOLDING, REQUEST_STATE, HEARTBEAT, LOBBY_ACTION)

# lobby_action.action values (host-controlled lobby; see GAME_DESIGN §2)
LOBBY_ACTIONS = ("set_team", "move", "kick", "set_min_players", "start", "claim_host")

# Server → client
STATE_SNAPSHOT = "state_snapshot"
ERROR = "error"
EVENT = "event"
STAGE_ADVANCED = "stage_advanced"
MATCH_WON = "match_won"

# Close codes
CLOSE_UNKNOWN = 4404  # unknown match or player
CLOSE_SUPERSEDED = 4001  # a newer socket took over this player_id
CLOSE_KICKED = 4403  # removed from the lobby by the host


def state_snapshot(match: Match, player_id: str | None = None) -> dict[str, Any]:
    return {"type": STATE_SNAPSHOT, "state": match.public(player_id)}


def error_message(text: str) -> dict[str, Any]:
    return {"type": ERROR, "error": text}


def event_message(event: Event) -> dict[str, Any]:
    return {"type": EVENT, "event": event.public()}


def stage_advanced(team_id: str, stage: int) -> dict[str, Any]:
    return {"type": STAGE_ADVANCED, "team_id": team_id, "stage": stage}


def match_won(team_id: str) -> dict[str, Any]:
    return {"type": MATCH_WON, "team_id": team_id}


def parse_client_message(raw: Any) -> tuple[str, dict[str, Any]] | str:
    """Validate a client message. Returns (type, fields) or an error string."""
    if not isinstance(raw, dict):
        return "Malformed message."
    msg_type = raw.get("type")
    if msg_type not in CLIENT_TYPES:
        return "Unknown message type."
    if msg_type in SUBMIT_TYPES:
        puzzle_id = raw.get("puzzle_id")
        answer = raw.get("answer")
        if not isinstance(puzzle_id, str) or not isinstance(answer, str):
            return "Malformed message."
        return msg_type, {"puzzle_id": puzzle_id, "answer": answer}
    if msg_type == LOBBY_ACTION:
        action = raw.get("action")
        if action not in LOBBY_ACTIONS:
            return "Unknown lobby action."
        fields = {"action": action}
        for key in ("target_id", "team_id"):
            if key in raw:
                if not isinstance(raw[key], str):
                    return "Malformed message."
                fields[key] = raw[key]
        if "value" in raw:
            if not isinstance(raw["value"], int) or isinstance(raw["value"], bool):
                return "Malformed message."
            fields["value"] = raw["value"]
        return msg_type, fields
    return msg_type, {}
