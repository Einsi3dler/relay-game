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
SUBMIT_TYPES = (SUBMIT_ANSWER, SUBMIT_HOLDING)
CLIENT_TYPES = (SUBMIT_ANSWER, SUBMIT_HOLDING, REQUEST_STATE, HEARTBEAT)

# Server → client
STATE_SNAPSHOT = "state_snapshot"
ERROR = "error"
EVENT = "event"
STAGE_ADVANCED = "stage_advanced"
MATCH_WON = "match_won"

# Close codes
CLOSE_UNKNOWN = 4404  # unknown match or player
CLOSE_SUPERSEDED = 4001  # a newer socket took over this player_id


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


def parse_client_message(raw: Any) -> tuple[str, dict[str, str]] | str:
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
    return msg_type, {}
