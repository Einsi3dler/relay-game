"""Dataclasses for Match, Team, Player, Event with `.public()` views.

Shapes are exactly docs/WEBSOCKET_PROTOCOL.md §3; field lists follow
docs/ARCHITECTURE.md §3. `.public()` must never include puzzle answers —
puzzles reach the client only via `PuzzleInstance.public()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backend.games.base import PuzzleInstance

# How many events MatchPublic carries (the "last ~30" in the protocol doc).
PUBLIC_EVENT_LIMIT = 30


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def green(player: Player) -> bool:
    """A player is green when they've cleared their main puzzle this stage."""
    return player.status in ("resting", "holding")


@dataclass
class Event:
    message: str
    kind: str = "info"  # "green" | "lost_green" | "advance" | "win" | "join" | "info"
    created_at: str = field(default_factory=utc_now)

    def public(self) -> dict[str, str]:
        return {
            "message": self.message,
            "kind": self.kind,
            "created_at": self.created_at,
        }


@dataclass
class Player:
    id: str  # long + random — it is the WS credential
    name: str
    team_id: str | None = None  # None while unassigned in the lobby
    status: str = "lobby"  # "lobby" | "solving" | "resting" | "holding" | "finished"
    connected: bool = False
    attempt: int = 0  # main-puzzle instances served this stage; feeds seed derivation
    current_main: PuzzleInstance | None = None
    current_holding: PuzzleInstance | None = None
    timer_deadline: str | None = None  # UTC ISO; drives the client countdown
    timer_kind: str | None = None  # "rest" | "holding" | None

    def current_puzzle(self) -> PuzzleInstance | None:
        """The puzzle the player should act on right now, per protocol §3."""
        if self.status == "solving":
            return self.current_main
        if self.status == "holding":
            return self.current_holding
        return None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "team_id": self.team_id,
            "status": self.status,
            "green": green(self),
            "connected": self.connected,
        }

    def private(self) -> dict[str, Any]:
        """PlayerPrivate: PlayerPublic plus the puzzle this player may see."""
        puzzle = self.current_puzzle()
        return {
            **self.public(),
            "current_puzzle": puzzle.public() if puzzle else None,
            "timer_kind": self.timer_kind,
            "timer_deadline": self.timer_deadline,
        }


@dataclass
class Team:
    id: str
    name: str
    stage: int = 1  # 1..STAGE_COUNT, independent per team
    roster_size: int = 0  # frozen at match start
    player_ids: list[str] = field(default_factory=list)
    finished: bool = False

    def public(self, players: dict[str, Player]) -> dict[str, Any]:
        members = [players[player_id] for player_id in self.player_ids]
        return {
            "id": self.id,
            "name": self.name,
            "stage": self.stage,
            "roster_size": self.roster_size,
            "finished": self.finished,
            "green_count": sum(1 for member in members if green(member)),
            "players": [member.public() for member in members],
        }


@dataclass
class Match:
    id: str
    status: str = "lobby"  # "lobby" | "active" | "finished"
    teams: dict[str, Team] = field(default_factory=dict)
    players: dict[str, Player] = field(default_factory=dict)
    host_player_id: str | None = None  # first joiner; lobby control (see docs)
    min_players: int = 0  # per-match start threshold, host-adjustable in lobby
    winner_team_id: str | None = None
    events: list[Event] = field(default_factory=list)
    config_snapshot: dict[str, Any] = field(default_factory=dict)  # frozen at start

    def unassigned(self) -> list[Player]:
        """Lobby players who haven't picked (or been given) a team yet."""
        return [p for p in self.players.values() if p.team_id is None]

    def public(self, player_id: str | None = None) -> dict[str, Any]:
        """MatchPublic; `me` is filled only for the requesting player."""
        me = self.players.get(player_id) if player_id is not None else None
        return {
            "id": self.id,
            "status": self.status,
            "host_player_id": self.host_player_id,
            "min_players": self.min_players,
            "winner_team_id": self.winner_team_id,
            "config": dict(self.config_snapshot),
            "teams": {
                team_id: team.public(self.players)
                for team_id, team in self.teams.items()
            },
            "unassigned": [player.public() for player in self.unassigned()],
            "events": [event.public() for event in self.events[-PUBLIC_EVENT_LIMIT:]],
            "me": me.private() if me else None,
        }
