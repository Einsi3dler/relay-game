"""Message (de)serialisation helpers and type constants for the WebSocket protocol.

v2 message set per docs/REDESIGN_PLAN.md. Builders return JSON-safe dicts;
`main.py` owns the sockets.
"""

from __future__ import annotations

from typing import Any

from backend.models import Event, Match

# Client → server
SUBMIT_ANSWER = "submit_answer"
DUEL_CHOICE = "duel_choice"
CHOOSE_WAIT = "choose_wait"
CHOOSE_BONUS = "choose_bonus"
BUY_PERK = "buy_perk"
GIVE_LEADER = "give_leader"
REQUEST_STATE = "request_state"
HEARTBEAT = "heartbeat"
LOBBY_ACTION = "lobby_action"
CLIENT_TYPES = (
    SUBMIT_ANSWER,
    DUEL_CHOICE,
    CHOOSE_WAIT,
    CHOOSE_BONUS,
    BUY_PERK,
    GIVE_LEADER,
    REQUEST_STATE,
    HEARTBEAT,
    LOBBY_ACTION,
)

# lobby_action.action values (host-controlled lobby + leader seat/assignments)
LOBBY_ACTIONS = (
    "set_team",
    "move",
    "kick",
    "set_min_players",
    "set_max_players",
    "set_level_count",
    "set_duel_seconds",
    "set_team_name",
    "start",
    "cancel_session",
    "end_session",
    "leave",
    "claim_host",
    "claim_leader",
    "assign_role",
    "assign_game",
)

# Server → client
STATE_SNAPSHOT = "state_snapshot"
ERROR = "error"
EVENT = "event"
LEVEL_ADVANCED = "level_advanced"
PERK_USED = "perk_used"
DUEL_RESULT = "duel_result"
MATCH_WON = "match_won"

# Close codes
CLOSE_UNKNOWN = 4404  # unknown match or player
CLOSE_SUPERSEDED = 4001  # a newer socket took over this player_id
CLOSE_KICKED = 4403  # removed from the lobby by the host, or left of their own
CLOSE_CANCELLED = 4402  # the host cancelled the lobby before it ever started


def state_snapshot(match: Match, player_id: str | None = None) -> dict[str, Any]:
    return {"type": STATE_SNAPSHOT, "state": match.public(player_id)}


def error_message(text: str) -> dict[str, Any]:
    return {"type": ERROR, "error": text}


def event_message(event: Event) -> dict[str, Any]:
    return {"type": EVENT, "event": event.public()}


def level_advanced(team_id: str, level: int) -> dict[str, Any]:
    return {"type": LEVEL_ADVANCED, "team_id": team_id, "level": level}


def perk_used(perk_id: str, by_team_id: str) -> dict[str, Any]:
    return {"type": PERK_USED, "perk_id": perk_id, "by_team_id": by_team_id}


def duel_result(payload: dict[str, Any]) -> dict[str, Any]:
    """A decided duel. The snapshot already carries the outcome; this is
    the nudge that lets the client play the reveal."""
    return {"type": DUEL_RESULT, **payload}


def match_won(team_id: str) -> dict[str, Any]:
    return {"type": MATCH_WON, "team_id": team_id}


def parse_client_message(raw: Any) -> tuple[str, dict[str, Any]] | str:
    """Validate a client message. Returns (type, fields) or an error string."""
    if not isinstance(raw, dict):
        return "Malformed message."
    msg_type = raw.get("type")
    if msg_type not in CLIENT_TYPES:
        return "Unknown message type."
    if msg_type == SUBMIT_ANSWER:
        puzzle_id = raw.get("puzzle_id")
        answer = raw.get("answer")
        if not isinstance(puzzle_id, str) or not isinstance(answer, str):
            return "Malformed message."
        return msg_type, {"puzzle_id": puzzle_id, "answer": answer}
    if msg_type == DUEL_CHOICE:
        duel_id = raw.get("duel_id")
        round_index = raw.get("round")
        choice = raw.get("choice")
        if not isinstance(duel_id, str) or not isinstance(choice, str):
            return "Malformed message."
        if not isinstance(round_index, int) or isinstance(round_index, bool):
            return "Malformed message."
        return msg_type, {
            "duel_id": duel_id, "round": round_index, "choice": choice,
        }
    if msg_type == BUY_PERK:
        perk_id = raw.get("perk_id")
        if not isinstance(perk_id, str):
            return "Malformed message."
        fields = {"perk_id": perk_id}
        if "target_id" in raw:
            if not isinstance(raw["target_id"], str):
                return "Malformed message."
            fields["target_id"] = raw["target_id"]
        return msg_type, fields
    if msg_type == GIVE_LEADER:
        target_id = raw.get("target_id")
        if not isinstance(target_id, str):
            return "Malformed message."
        return msg_type, {"target_id": target_id}
    if msg_type == LOBBY_ACTION:
        action = raw.get("action")
        if action not in LOBBY_ACTIONS:
            return "Unknown lobby action."
        fields = {"action": action}
        for key in ("target_id", "team_id", "game_id", "role_id", "name"):
            if key in raw:
                if not isinstance(raw[key], str):
                    return "Malformed message."
                fields[key] = raw[key]
        if "value" in raw:
            if not isinstance(raw["value"], int) or isinstance(raw["value"], bool):
                return "Malformed message."
            fields["value"] = raw["value"]
        return msg_type, fields
    return msg_type, {}  # choose_wait / choose_bonus / request_state / heartbeat
