"""RelayEngine: the pure relay rules (loop, advance check, win).

Implements docs/GAME_DESIGN.md §4 exactly. Pure/synchronous over a Match:
methods return an `EngineResult` describing what changed and which timers to
(re)schedule or cancel — the engine never sleeps and never does I/O. The
server layer (main.py + TimerService) owns the clock and the sockets.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend import config
from backend.models import Event, Match, Player, Team, green
from backend.registry import GameRegistry


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_seed() -> int:
    # Unguessable, server-side only (ARCHITECTURE.md §"Seeds").
    return secrets.randbits(63)


@dataclass
class TimerRequest:
    """Ask the server layer to schedule a deadline for a player.

    Scheduling replaces the player's previous timer (one active timer each).
    """

    player_id: str
    kind: str  # "rest" | "holding"
    deadline: str  # UTC ISO


@dataclass
class EngineResult:
    ok: bool = True
    error: str | None = None  # set when ok is False (rejected input)
    correct: bool | None = None  # set by submit_* calls
    changed: bool = False  # whether a fresh snapshot should be broadcast
    match_started: bool = False
    advanced_team_ids: list[str] = field(default_factory=list)
    winner_team_id: str | None = None
    events: list[Event] = field(default_factory=list)
    schedule: list[TimerRequest] = field(default_factory=list)
    cancel: list[str] = field(default_factory=list)  # player_ids to cancel

    @staticmethod
    def rejected(message: str) -> EngineResult:
        return EngineResult(ok=False, error=message)


class RelayEngine:
    def __init__(self, registry: GameRegistry) -> None:
        self.registry = registry

    # --- lobby (T2.1) ---

    def create_match(self) -> Match:
        teams = {
            team_id: Team(id=team_id, name=team_id.title())
            for team_id in config.TEAM_IDS
        }
        return Match(id=uuid4().hex[:8], teams=teams)

    def join_match(
        self,
        match: Match,
        name: str,
        team_id: str | None = None,
        now: datetime | None = None,
    ) -> tuple[Player, EngineResult]:
        """Add a player; auto-balance when no team given. Starts the match
        once both teams reach MIN_PLAYERS_PER_TEAM."""
        if match.status != "lobby":
            raise ValueError("match already started")
        if team_id is None:
            team = min(match.teams.values(), key=lambda t: len(t.player_ids))
        else:
            if team_id not in match.teams:
                raise ValueError(f"unknown team {team_id!r}")
            team = match.teams[team_id]
        if len(team.player_ids) >= config.PLAYERS_PER_TEAM:
            raise ValueError(f"team {team.id!r} is full")

        player = Player(
            id=f"p_{secrets.token_hex(8)}",  # long + random — the WS credential
            name=name,
            team_id=team.id,
            status="lobby",
            connected=True,
        )
        match.players[player.id] = player
        team.player_ids.append(player.id)

        result = EngineResult(changed=True)
        self._add_event(match, result, f"{player.name} joined team {team.name}.", "join")

        if all(
            len(t.player_ids) >= config.MIN_PLAYERS_PER_TEAM
            for t in match.teams.values()
        ):
            self._merge(result, self.start_match(match, now=now))
        return player, result

    def start_match(self, match: Match, now: datetime | None = None) -> EngineResult:
        """Freeze roster sizes and config, set everyone solving Stage 1."""
        match.status = "active"
        match.config_snapshot = {
            "rest_seconds": config.REST_SECONDS,
            "holding_seconds": config.HOLDING_SECONDS,
            "players_per_team": config.PLAYERS_PER_TEAM,
            "stage_count": config.STAGE_COUNT,
        }
        result = EngineResult(changed=True, match_started=True)
        for team in match.teams.values():
            team.roster_size = len(team.player_ids)
            for player_id in team.player_ids:
                self._serve_main(match, match.players[player_id])
        self._add_event(match, result, "Match started — Stage 1!", "info")
        return result

    # --- main puzzle (T2.2) ---

    def submit_main(
        self,
        match: Match,
        player_id: str,
        puzzle_id: str,
        answer: str,
        now: datetime | None = None,
    ) -> EngineResult:
        guard = self._submit_guard(match, player_id, "solving")
        if guard is not None:
            return guard
        player = match.players[player_id]
        puzzle = player.current_main
        if puzzle is None or puzzle.id != puzzle_id:
            return EngineResult.rejected("stale or unknown puzzle")

        team = match.teams[player.team_id]
        module = self.registry.for_stage(team.stage)
        if not module.check(puzzle, answer):
            # Wrong: stay solving, but on a fresh instance (new seed, attempt+1).
            self._serve_main(match, player)
            return EngineResult(correct=False, changed=True)

        result = EngineResult(correct=True, changed=True)
        self._go_resting(match, player, result, now)
        self._add_event(match, result, f"{player.name} went green.", "green")
        self._advance_check(match, team, result, now)
        return result

    # --- rest window (T2.4) ---

    def on_rest_expired(
        self, match: Match, player_id: str, now: datetime | None = None
    ) -> EngineResult:
        player = match.players.get(player_id)
        if match.status != "active" or player is None or player.status != "resting":
            return EngineResult(changed=False)  # stale timer — no-op
        team = match.teams[player.team_id]
        if self._team_all_green(match, team):
            return EngineResult(changed=False)  # advance is handling this team
        result = EngineResult(changed=True)
        module = self.registry.for_stage(team.stage)
        player.current_holding = module.generate_holding(_new_seed())
        player.status = "holding"
        self._start_timer(match, player, "holding", result, now)
        return result

    # --- holding question (T2.5, T2.6) ---

    def submit_holding(
        self,
        match: Match,
        player_id: str,
        puzzle_id: str,
        answer: str,
        now: datetime | None = None,
    ) -> EngineResult:
        guard = self._submit_guard(match, player_id, "holding")
        if guard is not None:
            return guard
        player = match.players[player_id]
        puzzle = player.current_holding
        if puzzle is None or puzzle.id != puzzle_id:
            return EngineResult.rejected("stale or unknown puzzle")

        team = match.teams[player.team_id]
        module = self.registry.for_stage(team.stage)
        if not module.check(puzzle, answer):
            result = EngineResult(correct=False, changed=True)
            self._lose_green(match, player, result)
            return result

        result = EngineResult(correct=True, changed=True)
        player.current_holding = None
        self._go_resting(match, player, result, now)
        self._advance_check(match, team, result, now)
        return result

    def on_holding_expired(
        self, match: Match, player_id: str, now: datetime | None = None
    ) -> EngineResult:
        player = match.players.get(player_id)
        if match.status != "active" or player is None or player.status != "holding":
            return EngineResult(changed=False)  # stale timer — no-op
        result = EngineResult(changed=True)
        self._lose_green(match, player, result)
        return result

    # --- reconnect / disconnect (T2.7, GAME_DESIGN §9) ---

    def on_disconnect(self, match: Match, player_id: str) -> EngineResult:
        player = match.players.get(player_id)
        if player is None:
            return EngineResult(changed=False)
        # Status and timers are untouched: green persists and decays via the
        # normal rest → holding → lost-green cascade.
        player.connected = False
        return EngineResult(changed=True)

    def on_reconnect(self, match: Match, player_id: str) -> EngineResult:
        player = match.players.get(player_id)
        if player is None:
            return EngineResult(changed=False)
        player.connected = True
        result = EngineResult(changed=True)
        if match.status == "active" and player.status == "solving":
            # Fresh instance so a watched/failed board can't be replayed (ECHO).
            self._serve_main(match, player)
        return result

    # --- internals ---

    def _submit_guard(
        self, match: Match, player_id: str, expected_status: str
    ) -> EngineResult | None:
        if match.status != "active":
            return EngineResult.rejected("match is not active")
        player = match.players.get(player_id)
        if player is None:
            return EngineResult.rejected("unknown player")
        if player.status != expected_status:
            return EngineResult.rejected(f"player is not {expected_status}")
        return None

    def _serve_main(self, match: Match, player: Player) -> None:
        """Fresh main instance for the player's team stage (new seed, attempt+1)."""
        team = match.teams[player.team_id]
        module = self.registry.for_stage(team.stage)
        player.attempt += 1
        player.current_main = module.generate_main(_new_seed())
        player.current_holding = None
        player.status = "solving"
        player.timer_kind = None
        player.timer_deadline = None

    def _go_resting(
        self, match: Match, player: Player, result: EngineResult, now: datetime | None
    ) -> None:
        player.status = "resting"
        player.current_main = None
        self._start_timer(match, player, "rest", result, now)

    def _lose_green(self, match: Match, player: Player, result: EngineResult) -> None:
        result.cancel.append(player.id)
        self._serve_main(match, player)
        self._add_event(match, result, f"{player.name} lost green.", "lost_green")

    def _start_timer(
        self, match: Match, player: Player, kind: str, result: EngineResult,
        now: datetime | None,
    ) -> None:
        seconds = match.config_snapshot[f"{kind}_seconds"]
        deadline = (now or utc_now()) + timedelta(seconds=seconds)
        player.timer_kind = kind
        player.timer_deadline = deadline.isoformat()
        result.schedule.append(
            TimerRequest(player_id=player.id, kind=kind, deadline=player.timer_deadline)
        )

    def _team_all_green(self, match: Match, team: Team) -> bool:
        members = [match.players[player_id] for player_id in team.player_ids]
        return bool(members) and all(green(member) for member in members)

    def _advance_check(
        self, match: Match, team: Team, result: EngineResult, now: datetime | None
    ) -> None:
        """GAME_DESIGN §4: runs on every green transition, not just timer fires."""
        if not self._team_all_green(match, team):
            return
        members = [match.players[player_id] for player_id in team.player_ids]
        member_ids = set(team.player_ids)
        # Timers scheduled earlier in this same result are now moot.
        result.schedule = [r for r in result.schedule if r.player_id not in member_ids]
        result.cancel.extend(team.player_ids)

        if team.stage >= match.config_snapshot["stage_count"]:
            team.finished = True
            match.status = "finished"
            match.winner_team_id = team.id
            result.winner_team_id = team.id
            for member in members:
                member.status = "finished"
                member.current_main = None
                member.current_holding = None
                member.timer_kind = None
                member.timer_deadline = None
            self._add_event(match, result, f"Team {team.name} wins!", "win")
            return

        team.stage += 1
        result.advanced_team_ids.append(team.id)
        for member in members:
            self._serve_main(match, member)
        self._add_event(
            match, result, f"Team {team.name} advances to Stage {team.stage}!", "advance"
        )

    def _add_event(
        self, match: Match, result: EngineResult, message: str, kind: str
    ) -> None:
        event = Event(message=message, kind=kind)
        match.events.append(event)
        result.events.append(event)

    def _merge(self, result: EngineResult, other: EngineResult) -> None:
        result.changed = result.changed or other.changed
        result.match_started = result.match_started or other.match_started
        result.advanced_team_ids.extend(other.advanced_team_ids)
        result.winner_team_id = result.winner_team_id or other.winner_team_id
        result.events.extend(other.events)
        result.schedule.extend(other.schedule)
        result.cancel.extend(other.cancel)
