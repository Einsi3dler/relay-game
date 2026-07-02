from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


MAX_LEVEL = 10
MAX_PLAYERS_PER_TEAM = 10
TEAM_IDS = ("alpha", "bravo")
ROLES = (
    "Terminal",
    "Architect",
    "Vault",
    "Oracle",
    "Wordsmith",
    "Quant",
    "Maestro",
    "Wildcard",
    "Saboteur",
    "Warden",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Puzzle:
    id: str
    role: str
    kind: str
    prompt: str
    answer: str
    level: int
    payload: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "kind": self.kind,
            "prompt": self.prompt,
            "level": self.level,
            "payload": self.payload,
        }


@dataclass
class Event:
    message: str
    kind: str = "info"
    created_at: str = field(default_factory=utc_now)

    def public(self) -> dict[str, str]:
        return {
            "message": self.message,
            "kind": self.kind,
            "created_at": self.created_at,
        }


@dataclass
class Player:
    id: str
    name: str
    team_id: str
    role: str
    status: str = "active"
    attempts: int = 0
    connected: bool = False
    current_puzzle: Puzzle | None = None
    current_grind: Puzzle | None = None
    backlog_puzzle: Puzzle | None = None
    completed_level: int = 0

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "team_id": self.team_id,
            "role": self.role,
            "status": self.status,
            "attempts": self.attempts,
            "connected": self.connected,
            "completed_level": self.completed_level,
        }


@dataclass
class Team:
    id: str
    name: str
    level: int = 1
    points: int = 0
    shield_charges: int = 0
    inventory: dict[str, int] = field(default_factory=dict)
    player_ids: list[str] = field(default_factory=list)
    finished: bool = False

    def public(self, players: dict[str, Player]) -> dict[str, Any]:
        dormant_count = sum(
            1 for player_id in self.player_ids if players[player_id].status == "dormant"
        )
        return {
            "id": self.id,
            "name": self.name,
            "level": self.level,
            "points": self.points,
            "shield_charges": self.shield_charges,
            "inventory": dict(self.inventory),
            "finished": self.finished,
            "difficulty_multiplier": round(1 + dormant_count * 0.2, 2),
            "players": [players[player_id].public() for player_id in self.player_ids],
        }


@dataclass
class Match:
    id: str
    created_at: str = field(default_factory=utc_now)
    max_level: int = MAX_LEVEL
    teams: dict[str, Team] = field(
        default_factory=lambda: {
            "alpha": Team(id="alpha", name="Alpha"),
            "bravo": Team(id="bravo", name="Bravo"),
        }
    )
    players: dict[str, Player] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)

    def public(self, player_id: str | None = None) -> dict[str, Any]:
        me = self.players.get(player_id) if player_id else None
        return {
            "id": self.id,
            "created_at": self.created_at,
            "max_level": self.max_level,
            "teams": {
                team_id: team.public(self.players)
                for team_id, team in self.teams.items()
            },
            "events": [event.public() for event in self.events[-30:]],
            "me": self._player_private(me) if me else None,
        }

    def _player_private(self, player: Player | None) -> dict[str, Any] | None:
        if player is None:
            return None
        data = player.public()
        data["current_puzzle"] = (
            player.current_puzzle.public() if player.current_puzzle else None
        )
        data["current_grind"] = (
            player.current_grind.public() if player.current_grind else None
        )
        data["backlog_puzzle"] = (
            player.backlog_puzzle.public() if player.backlog_puzzle else None
        )
        return data
