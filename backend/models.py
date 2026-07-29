"""Dataclasses for Match, Team, Player, Event with `.public()` views.

v2 shapes per docs/REDESIGN_PLAN.md (WEBSOCKET_PROTOCOL.md is being resynced).
`.public()` must never include puzzle answers — puzzles reach the client only
via `PuzzleInstance.public()`. Progress visibility is leader-exclusive: a
playing viewer gets only their own team's level, never who has cleared.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backend.games.base import PuzzleInstance

# How many events MatchPublic carries (the "last ~30" in the protocol doc).
PUBLIC_EVENT_LIMIT = 30

# Event kinds only leaders may see (who cleared / who lost cleared status).
LEADER_ONLY_EVENT_KINDS = ("green", "lost_green")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def green(player: Player) -> bool:
    """A player is green (cleared) when they've solved their level and hold it.

    A player in `bonus` is NOT green — taking the bonus resets cleared status
    until they solve it, which is what blocks the team from advancing past them.
    """
    return player.status == "cleared"


@dataclass
class Event:
    message: str
    kind: str = "info"  # "green" | "lost_green" | "advance" | "win" | "join" | "perk" | "info"
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
    status: str = "lobby"  # "lobby" | "solving" | "cleared" | "bonus" | "leading" | "finished"
    connected: bool = False
    is_leader: bool = False
    assigned_game: str | None = None  # game id chosen by the team leader
    attempt: int = 0  # main-puzzle instances served this level
    current_main: PuzzleInstance | None = None
    current_bonus: PuzzleInstance | None = None
    choice_pending: bool = False  # cleared and still owes a wait-or-bonus choice
    timer_deadline: str | None = None  # UTC ISO; drives the client countdown
    timer_kind: str | None = None  # "wait" | None
    frozen_until: str | None = None  # UTC ISO; submits rejected until then
    earned_level: int = 0  # highest level base currency was paid for
    bonus_streak: int = 0  # successful bonuses this level (first pays more)
    bonus_earned: int = 0  # this level's bonus pay — forfeited on bonus failure

    def current_puzzle(self) -> PuzzleInstance | None:
        """The puzzle the player should act on right now."""
        if self.status == "solving":
            return self.current_main
        if self.status == "bonus":
            return self.current_bonus
        return None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "team_id": self.team_id,
            "status": self.status,
            "green": green(self),
            "connected": self.connected,
            "is_leader": self.is_leader,
            "assigned_game": self.assigned_game,
        }

    def private(self) -> dict[str, Any]:
        """PlayerPrivate: PlayerPublic plus the puzzle this player may see."""
        puzzle = self.current_puzzle()
        return {
            **self.public(),
            "current_puzzle": puzzle.public() if puzzle else None,
            "timer_kind": self.timer_kind,
            "timer_deadline": self.timer_deadline,
            "choice_pending": self.choice_pending,
            "frozen_until": self.frozen_until,
        }


@dataclass
class Team:
    id: str
    name: str
    level: int = 1  # 1..LEVEL_COUNT, independent per team
    roster_size: int = 0  # PLAYING members (leader excluded), frozen at start
    player_ids: list[str] = field(default_factory=list)  # includes the leader
    finished: bool = False
    currency: int = 0  # team pool, spent only by the leader
    shield_active: bool = False  # blocks the next incoming attack perk
    leader_id: str | None = None
    handoff_used_level: int = 0  # last level a mid-match leader handoff happened

    def public(self, players: dict[str, Player]) -> dict[str, Any]:
        """Full view: own team for its leader, and everyone in the lobby."""
        members = [players[player_id] for player_id in self.player_ids]
        return {
            "id": self.id,
            "name": self.name,
            "level": self.level,
            "roster_size": self.roster_size,
            "finished": self.finished,
            "green_count": sum(1 for member in members if green(member)),
            "currency": self.currency,
            "shield_active": self.shield_active,
            "leader_id": self.leader_id,
            "players": [member.public() for member in members],
        }

    def summary(
        self, players: dict[str, Player], include_green: bool = False
    ) -> dict[str, Any]:
        """Limited view — no roster, no currency, no shield.

        `include_green` adds the cleared-count (the opponent view a leader gets).
        """
        view: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "level": self.level,
            "roster_size": self.roster_size,
            "finished": self.finished,
        }
        if include_green:
            members = [players[player_id] for player_id in self.player_ids]
            view["green_count"] = sum(1 for member in members if green(member))
        return view


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

    def _team_view(self, team: Team, me: Player | None) -> dict[str, Any]:
        if self.status == "lobby":
            return team.public(self.players)  # lobby: full rosters for everyone
        if me is None:
            return team.summary(self.players)
        if me.is_leader:
            if team.id == me.team_id:
                return team.public(self.players)
            return team.summary(self.players, include_green=True)
        if team.id == me.team_id:
            return team.summary(self.players)
        return {"id": team.id, "name": team.name, "finished": team.finished}

    def public(self, player_id: str | None = None) -> dict[str, Any]:
        """MatchPublic; `me` is filled only for the requesting player."""
        me = self.players.get(player_id) if player_id is not None else None
        events = self.events[-PUBLIC_EVENT_LIMIT:]
        if self.status != "lobby" and (me is None or not me.is_leader):
            events = [e for e in events if e.kind not in LEADER_ONLY_EVENT_KINDS]
        return {
            "id": self.id,
            "status": self.status,
            "host_player_id": self.host_player_id,
            "min_players": self.min_players,
            "winner_team_id": self.winner_team_id,
            "config": dict(self.config_snapshot),
            "teams": {
                team_id: self._team_view(team, me)
                for team_id, team in self.teams.items()
            },
            "unassigned": [player.public() for player in self.unassigned()],
            "events": [event.public() for event in events],
            "me": me.private() if me else None,
        }
