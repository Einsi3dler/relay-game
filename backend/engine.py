"""RelayEngine: the pure v2 rules (levels, wait/bonus, currency, perks).

Implements docs/REDESIGN_PLAN.md. Pure/synchronous over a Match: methods
return an `EngineResult` describing what changed and which timers to
(re)schedule or cancel — the engine never sleeps and never does I/O. The
server layer (main.py + TimerService) owns the clock and the sockets.

The only scheduled deadline is the per-player wait timer (which doubles as
the bonus deadline). Freeze is a lazy deadline checked on submit. Any future
second concurrent deadline must be lazy too, or the timer key must grow.
"""

from __future__ import annotations

import random
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


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass
class TimerRequest:
    """Ask the server layer to schedule a deadline for a player.

    Scheduling replaces the player's previous timer (one active timer each).
    """

    player_id: str
    kind: str  # "wait"
    deadline: str  # UTC ISO


@dataclass
class EngineResult:
    ok: bool = True
    error: str | None = None  # set when ok is False (rejected input)
    correct: bool | None = None  # set by submit calls
    changed: bool = False  # whether a fresh snapshot should be broadcast
    match_started: bool = False
    advanced_team_ids: list[str] = field(default_factory=list)
    winner_team_id: str | None = None
    kicked_player_ids: list[str] = field(default_factory=list)  # sockets to close
    perk_used: dict | None = None  # {"perk_id", "by_team_id"} for the nudge
    events: list[Event] = field(default_factory=list)
    schedule: list[TimerRequest] = field(default_factory=list)
    cancel: list[str] = field(default_factory=list)  # player_ids to cancel

    @staticmethod
    def rejected(message: str) -> EngineResult:
        return EngineResult(ok=False, error=message)


class RelayEngine:
    def __init__(self, registry: GameRegistry) -> None:
        self.registry = registry

    # --- lobby (host-controlled; leaders claimed per team) ---

    def create_match(self) -> Match:
        teams = {
            team_id: Team(id=team_id, name=team_id.title())
            for team_id in config.TEAM_IDS
        }
        return Match(
            id=uuid4().hex[:8],
            teams=teams,
            min_players=config.MIN_PLAYERS_PER_TEAM,
        )

    def join_match(
        self,
        match: Match,
        name: str,
        team_id: str | None = None,
        now: datetime | None = None,
    ) -> tuple[Player, EngineResult]:
        """Add a player to the lobby — unassigned unless a team is given
        explicitly. The first joiner becomes host; the host starts the match."""
        if match.status != "lobby":
            raise ValueError("match already started")
        team_capacity = config.PLAYERS_PER_TEAM + 1  # playing members + a leader
        if len(match.players) >= team_capacity * len(match.teams):
            raise ValueError("match is full")
        team: Team | None = None
        if team_id is not None:
            if team_id not in match.teams:
                raise ValueError(f"unknown team {team_id!r}")
            team = match.teams[team_id]
            if len(team.player_ids) >= team_capacity:
                raise ValueError(f"team {team.id!r} is full")

        player = Player(
            id=f"p_{secrets.token_hex(8)}",  # long + random — the WS credential
            name=name,
            team_id=team.id if team else None,
            status="lobby",
            connected=True,
        )
        match.players[player.id] = player
        if team is not None:
            team.player_ids.append(player.id)

        result = EngineResult(changed=True)
        if match.host_player_id is None:
            match.host_player_id = player.id
            self._add_event(match, result, f"{player.name} is hosting.", "join")
        else:
            self._add_event(match, result, f"{player.name} joined.", "join")
        return player, result

    def set_team(
        self, match: Match, player_id: str, team_id: str
    ) -> EngineResult:
        """A lobby player picks (or switches) their own team."""
        if match.status != "lobby":
            return EngineResult.rejected("match already started")
        player = match.players.get(player_id)
        if player is None:
            return EngineResult.rejected("unknown player")
        return self._assign_team(match, player, team_id)

    def host_move(
        self, match: Match, host_id: str, target_id: str, team_id: str
    ) -> EngineResult:
        """Host drags any lobby player onto a team."""
        guard = self._host_guard(match, host_id)
        if guard is not None:
            return guard
        target = match.players.get(target_id)
        if target is None:
            return EngineResult.rejected("unknown player")
        return self._assign_team(match, target, team_id)

    def host_kick(self, match: Match, host_id: str, target_id: str) -> EngineResult:
        guard = self._host_guard(match, host_id)
        if guard is not None:
            return guard
        if target_id == host_id:
            return EngineResult.rejected("the host cannot kick themselves")
        target = match.players.get(target_id)
        if target is None:
            return EngineResult.rejected("unknown player")
        if target.team_id is not None:
            team = match.teams[target.team_id]
            team.player_ids.remove(target.id)
            if team.leader_id == target.id:
                team.leader_id = None
        del match.players[target.id]
        result = EngineResult(changed=True, kicked_player_ids=[target.id])
        self._add_event(match, result, f"{target.name} was kicked.", "info")
        return result

    def host_set_min_players(
        self, match: Match, host_id: str, value: int
    ) -> EngineResult:
        guard = self._host_guard(match, host_id)
        if guard is not None:
            return guard
        if not isinstance(value, int) or not 1 <= value <= config.PLAYERS_PER_TEAM:
            return EngineResult.rejected(
                f"min players must be 1..{config.PLAYERS_PER_TEAM}"
            )
        match.min_players = value
        result = EngineResult(changed=True)
        self._add_event(
            match, result, f"Minimum players per team set to {value}.", "info"
        )
        return result

    def host_start(
        self, match: Match, host_id: str, now: datetime | None = None
    ) -> EngineResult:
        guard = self._host_guard(match, host_id)
        if guard is not None:
            return guard
        reason = self.start_blocker(match)
        if reason is not None:
            return EngineResult.rejected(reason)
        return self.start_match(match, now=now)

    def claim_host(self, match: Match, player_id: str) -> EngineResult:
        """Take over a lobby whose host is gone (kick-proof: only claimable
        while the current host is disconnected or missing)."""
        if match.status != "lobby":
            return EngineResult.rejected("match already started")
        player = match.players.get(player_id)
        if player is None:
            return EngineResult.rejected("unknown player")
        host = match.players.get(match.host_player_id or "")
        if host is not None and host.connected:
            return EngineResult.rejected("the host is still here")
        match.host_player_id = player.id
        result = EngineResult(changed=True)
        self._add_event(match, result, f"{player.name} is now hosting.", "info")
        return result

    def claim_leader(self, match: Match, player_id: str) -> EngineResult:
        """A lobby player claims the leader seat of their team. Claimable only
        while the seat is empty or its holder is disconnected."""
        if match.status != "lobby":
            return EngineResult.rejected("match already started")
        player = match.players.get(player_id)
        if player is None:
            return EngineResult.rejected("unknown player")
        if player.team_id is None:
            return EngineResult.rejected("pick a team before claiming leader")
        team = match.teams[player.team_id]
        current = match.players.get(team.leader_id or "")
        if current is player:
            return EngineResult.rejected("you already lead this team")
        if current is not None and current.connected:
            return EngineResult.rejected(f"team {team.name} already has a leader")
        if current is not None:
            current.is_leader = False
        player.is_leader = True
        player.assigned_game = None  # leaders don't play
        team.leader_id = player.id
        result = EngineResult(changed=True)
        self._add_event(
            match, result, f"{player.name} now leads team {team.name}.", "info"
        )
        return result

    def give_leader(
        self,
        match: Match,
        leader_id: str,
        target_id: str,
        now: datetime | None = None,
    ) -> EngineResult:
        """The leader hands the seat to a teammate.

        Lobby: just moves the flag (the new leader's assignment is cleared; the
        old leader becomes assignable). Active match: full swap — the recipient
        stops playing, the old leader takes over their game at the current
        level with a fresh, un-cleared puzzle. Once per team per level.
        """
        if match.status == "finished":
            return EngineResult.rejected("match is over")
        leader = match.players.get(leader_id)
        if leader is None or not leader.is_leader:
            return EngineResult.rejected("only the team leader can do that")
        target = match.players.get(target_id)
        if target is None or target.team_id != leader.team_id:
            return EngineResult.rejected("target must be a teammate")
        if target.id == leader.id:
            return EngineResult.rejected("you already lead this team")
        team = match.teams[leader.team_id]

        result = EngineResult(changed=True)
        if match.status == "lobby":
            leader.is_leader = False
            target.is_leader = True
            target.assigned_game = None
            team.leader_id = target.id
            self._add_event(
                match,
                result,
                f"{leader.name} handed leadership of team {team.name} to {target.name}.",
                "info",
            )
            return result

        # Active match: full swap, once per level.
        if team.handoff_used_level == team.level:
            return EngineResult.rejected("leadership already changed this level")
        game_id = target.assigned_game
        if game_id is None:  # defensive: every playing member is assigned at start
            return EngineResult.rejected("target has no assigned game")

        team.handoff_used_level = team.level
        team.leader_id = target.id

        # Old leader becomes the player, inheriting the seat's economy counters
        # so the same level can't pay base currency twice.
        leader.is_leader = False
        leader.assigned_game = game_id
        leader.earned_level = target.earned_level
        leader.bonus_streak = target.bonus_streak
        leader.bonus_earned = target.bonus_earned
        self._serve_main(match, leader)

        # Recipient stops playing: any cleared status/timer is gone.
        target.is_leader = True
        target.status = "leading"
        target.assigned_game = None
        target.current_main = None
        target.current_bonus = None
        target.choice_pending = False
        target.timer_kind = None
        target.timer_deadline = None
        target.earned_level = 0
        target.bonus_streak = 0
        target.bonus_earned = 0
        result.cancel.append(target.id)

        self._add_event(
            match,
            result,
            f"{leader.name} handed leadership of team {team.name} to {target.name}.",
            "info",
        )
        self._advance_check(match, team, result, now)
        return result

    def assign_game(
        self, match: Match, leader_id: str, target_id: str, game_id: str
    ) -> EngineResult:
        """The leader assigns a game to a teammate (lobby only). No two
        teammates may play the same game."""
        if match.status != "lobby":
            return EngineResult.rejected("match already started")
        leader = match.players.get(leader_id)
        if leader is None or not leader.is_leader:
            return EngineResult.rejected("only the team leader can assign games")
        target = match.players.get(target_id)
        if target is None or target.team_id != leader.team_id:
            return EngineResult.rejected("target must be a teammate")
        if target.is_leader:
            return EngineResult.rejected("the leader doesn't play")
        if not self.registry.has(game_id):
            return EngineResult.rejected(f"unknown game {game_id!r}")
        team = match.teams[leader.team_id]
        for member_id in team.player_ids:
            member = match.players[member_id]
            if member.id != target.id and member.assigned_game == game_id:
                return EngineResult.rejected(
                    f"{member.name} already plays {game_id}"
                )
        target.assigned_game = game_id
        result = EngineResult(changed=True)
        module = self.registry.by_id(game_id)
        self._add_event(
            match, result, f"{target.name} will play {module.name}.", "info"
        )
        return result

    def start_blocker(self, match: Match) -> str | None:
        """Why the match can't start yet, or None when it can."""
        if match.unassigned():
            names = ", ".join(p.name for p in match.unassigned())
            return f"everyone needs a team (waiting on {names})"
        for team in match.teams.values():
            leader = match.players.get(team.leader_id or "")
            if leader is None or not leader.is_leader:
                return f"team {team.name} needs a leader"
            playing = self._playing_members(match, team)
            if len(playing) < match.min_players:
                return f"team {team.name} needs {match.min_players} player(s)"
            if len(playing) > config.PLAYERS_PER_TEAM:
                return f"team {team.name} has too many players"
            unassigned = [p.name for p in playing if p.assigned_game is None]
            if unassigned:
                return (
                    f"team {team.name}: assign a game to {', '.join(unassigned)}"
                )
        return None

    def _assign_team(
        self, match: Match, player: Player, team_id: str
    ) -> EngineResult:
        if team_id not in match.teams:
            return EngineResult.rejected(f"unknown team {team_id!r}")
        team = match.teams[team_id]
        if player.team_id == team_id:
            return EngineResult.rejected(f"already on team {team.name}")
        if len(team.player_ids) >= config.PLAYERS_PER_TEAM + 1:
            return EngineResult.rejected(f"team {team.name} is full")
        if player.team_id is not None:
            old_team = match.teams[player.team_id]
            old_team.player_ids.remove(player.id)
            if old_team.leader_id == player.id:
                old_team.leader_id = None
        # Leadership and assignments don't cross teams.
        player.is_leader = False
        player.assigned_game = None
        player.team_id = team_id
        team.player_ids.append(player.id)
        result = EngineResult(changed=True)
        self._add_event(
            match, result, f"{player.name} joined team {team.name}.", "join"
        )
        return result

    def _host_guard(self, match: Match, player_id: str) -> EngineResult | None:
        if match.status != "lobby":
            return EngineResult.rejected("match already started")
        if player_id != match.host_player_id:
            return EngineResult.rejected("only the host can do that")
        return None

    def start_match(self, match: Match, now: datetime | None = None) -> EngineResult:
        """Freeze rosters and config; leaders observe, players start Level 1."""
        match.status = "active"
        match.config_snapshot = {
            "wait_seconds": config.WAIT_SECONDS,
            "level_count": config.LEVEL_COUNT,
            "players_per_team": config.PLAYERS_PER_TEAM,
            "currency_per_clear": config.CURRENCY_PER_CLEAR,
            "currency_bonus_first": config.CURRENCY_BONUS_FIRST,
            "currency_bonus_repeat": config.CURRENCY_BONUS_REPEAT,
            "bonus_level_offset": config.BONUS_LEVEL_OFFSET,
            "perks": {perk_id: dict(perk) for perk_id, perk in config.PERKS.items()},
        }
        result = EngineResult(changed=True, match_started=True)
        for team in match.teams.values():
            playing = self._playing_members(match, team)
            team.roster_size = len(playing)
            leader = match.players.get(team.leader_id or "")
            if leader is not None:
                leader.status = "leading"
            for player in playing:
                self._serve_main(match, player)
        self._add_event(match, result, "Match started — Level 1!", "info")
        return result

    # --- the level loop ---

    def submit_answer(
        self,
        match: Match,
        player_id: str,
        puzzle_id: str,
        answer: str,
        now: datetime | None = None,
    ) -> EngineResult:
        if match.status != "active":
            return EngineResult.rejected("match is not active")
        player = match.players.get(player_id)
        if player is None:
            return EngineResult.rejected("unknown player")
        if player.status not in ("solving", "bonus"):
            return EngineResult.rejected("nothing to submit right now")
        if player.frozen_until is not None:
            if (now or utc_now()) < _parse_iso(player.frozen_until):
                return EngineResult.rejected("You are frozen")
            player.frozen_until = None  # lazy cleanup once the freeze lapses

        if player.status == "solving":
            return self._submit_solving(match, player, puzzle_id, answer, now)
        return self._submit_bonus(match, player, puzzle_id, answer, now)

    def _submit_solving(
        self,
        match: Match,
        player: Player,
        puzzle_id: str,
        answer: str,
        now: datetime | None,
    ) -> EngineResult:
        puzzle = player.current_main
        if puzzle is None or puzzle.id != puzzle_id:
            return EngineResult.rejected("stale or unknown puzzle")
        module = self.registry.by_id(puzzle.game_id)
        if not module.check(puzzle, answer):
            # Wrong: stay solving, but on a fresh instance (new seed, attempt+1).
            self._serve_main(match, player)
            return EngineResult(correct=False, changed=True)

        team = match.teams[player.team_id]
        result = EngineResult(correct=True, changed=True)
        self._go_cleared(match, player, result, now)
        if team.level > player.earned_level:  # base pay: first clear of a level only
            player.earned_level = team.level
            team.currency += match.config_snapshot["currency_per_clear"]
        self._add_event(
            match, result, f"{player.name} cleared Level {team.level}.", "green"
        )
        self._advance_check(match, team, result, now)
        return result

    def _submit_bonus(
        self,
        match: Match,
        player: Player,
        puzzle_id: str,
        answer: str,
        now: datetime | None,
    ) -> EngineResult:
        puzzle = player.current_bonus
        if puzzle is None or puzzle.id != puzzle_id:
            return EngineResult.rejected("stale or unknown puzzle")
        module = self.registry.by_id(puzzle.game_id)
        team = match.teams[player.team_id]
        if not module.check(puzzle, answer):
            result = EngineResult(correct=False, changed=True)
            self._bonus_fail(match, player, result)
            return result

        player.bonus_streak += 1
        pay_key = "currency_bonus_first" if player.bonus_streak == 1 else "currency_bonus_repeat"
        pay = match.config_snapshot[pay_key]
        team.currency += pay
        player.bonus_earned += pay
        player.current_bonus = None
        result = EngineResult(correct=True, changed=True)
        self._go_cleared(match, player, result, now)  # fresh wait timer + new choice
        self._add_event(
            match, result, f"{player.name} nailed a bonus (+{pay}).", "green"
        )
        self._advance_check(match, team, result, now)
        return result

    def choose_wait(self, match: Match, player_id: str) -> EngineResult:
        player = match.players.get(player_id)
        if match.status != "active" or player is None:
            return EngineResult.rejected("match is not active")
        if player.status != "cleared" or not player.choice_pending:
            return EngineResult.rejected("no choice to make")
        player.choice_pending = False
        return EngineResult(changed=True)

    def choose_bonus(
        self, match: Match, player_id: str, now: datetime | None = None
    ) -> EngineResult:
        player = match.players.get(player_id)
        if match.status != "active" or player is None:
            return EngineResult.rejected("match is not active")
        if player.status != "cleared" or not player.choice_pending:
            return EngineResult.rejected("no choice to make")
        team = match.teams[player.team_id]
        module = self.registry.by_id(player.assigned_game)
        level = min(
            team.level + match.config_snapshot["bonus_level_offset"],
            match.config_snapshot["level_count"],
        )
        player.choice_pending = False
        player.status = "bonus"
        # The running wait deadline stays: it is now the bonus deadline.
        player.current_bonus = module.generate_main(_new_seed(), level=level)
        return EngineResult(changed=True)

    def on_wait_expired(
        self, match: Match, player_id: str, now: datetime | None = None
    ) -> EngineResult:
        player = match.players.get(player_id)
        if match.status != "active" or player is None:
            return EngineResult(changed=False)  # stale timer — no-op
        if player.status == "cleared":
            result = EngineResult(changed=True)
            self._serve_main(match, player)
            self._add_event(
                match, result, f"{player.name} lost cleared status.", "lost_green"
            )
            return result
        if player.status == "bonus":
            result = EngineResult(changed=True)
            self._bonus_fail(match, player, result)
            return result
        return EngineResult(changed=False)  # stale timer — no-op

    # --- perks ---

    def buy_perk(
        self,
        match: Match,
        leader_id: str,
        perk_id: str,
        target_id: str | None = None,
        now: datetime | None = None,
    ) -> EngineResult:
        if match.status != "active":
            return EngineResult.rejected("match is not active")
        leader = match.players.get(leader_id)
        if leader is None or not leader.is_leader:
            return EngineResult.rejected("only the team leader can buy perks")
        perk = match.config_snapshot["perks"].get(perk_id)
        if perk is None:
            return EngineResult.rejected(f"unknown perk {perk_id!r}")
        team = match.teams[leader.team_id]
        if team.currency < perk["cost"]:
            return EngineResult.rejected("not enough currency")

        result = EngineResult(changed=True)
        if perk["kind"] == "attack":
            opponent = self._opponent_team(match, team)
            applied = self._apply_attack(match, opponent, perk_id, perk, result, now)
            if not applied.ok:
                return applied
        elif perk_id == "shield":
            if team.shield_active:
                return EngineResult.rejected("shield already active")
            team.shield_active = True
        elif perk_id == "extend_wait":
            target = match.players.get(target_id or "")
            if target is None or target.team_id != team.id:
                return EngineResult.rejected("target must be a teammate")
            if target.status != "cleared" or target.timer_deadline is None:
                return EngineResult.rejected("target isn't holding cleared status")
            deadline = _parse_iso(target.timer_deadline) + timedelta(
                seconds=perk["seconds"]
            )
            target.timer_deadline = deadline.isoformat()
            result.schedule.append(
                TimerRequest(
                    player_id=target.id, kind="wait", deadline=target.timer_deadline
                )
            )
        else:
            return EngineResult.rejected(f"unknown perk {perk_id!r}")

        team.currency -= perk["cost"]
        result.perk_used = {"perk_id": perk_id, "by_team_id": team.id}
        self._add_event(
            match, result, f"Team {team.name} used {perk['name']}.", "perk"
        )
        return result

    def _apply_attack(
        self,
        match: Match,
        opponent: Team,
        perk_id: str,
        perk: dict,
        result: EngineResult,
        now: datetime | None,
    ) -> EngineResult:
        if opponent.shield_active:
            opponent.shield_active = False  # the shield eats the attack
            self._add_event(
                match,
                result,
                f"Team {opponent.name}'s shield blocked an attack!",
                "perk",
            )
            return result
        statuses = ("solving", "bonus") if perk_id == "freeze" else ("solving",)
        candidates = [
            p
            for p in self._playing_members(match, opponent)
            if p.status in statuses
        ]
        if not candidates:
            return EngineResult.rejected("no valid target right now")
        target = random.choice(candidates)  # fog of war: the server picks
        if perk_id == "freeze":
            deadline = (now or utc_now()) + timedelta(seconds=perk["seconds"])
            target.frozen_until = deadline.isoformat()
        else:  # scramble: forced reroll
            self._serve_main(match, target)
        return result

    def _opponent_team(self, match: Match, team: Team) -> Team:
        return next(t for t in match.teams.values() if t.id != team.id)

    # --- reconnect / disconnect (GAME_DESIGN §9) ---

    def on_disconnect(self, match: Match, player_id: str) -> EngineResult:
        player = match.players.get(player_id)
        if player is None:
            return EngineResult(changed=False)
        # Status and timers are untouched: cleared status persists and decays
        # via the normal wait-expiry cascade.
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
        elif match.status == "active" and player.status == "bonus":
            team = match.teams[player.team_id]
            module = self.registry.by_id(player.assigned_game)
            level = min(
                team.level + match.config_snapshot["bonus_level_offset"],
                match.config_snapshot["level_count"],
            )
            player.current_bonus = module.generate_main(_new_seed(), level=level)
        return result

    # --- internals ---

    def _playing_members(self, match: Match, team: Team) -> list[Player]:
        return [
            match.players[player_id]
            for player_id in team.player_ids
            if not match.players[player_id].is_leader
        ]

    def _serve_main(self, match: Match, player: Player) -> None:
        """Fresh main instance of the player's own game at the team's level."""
        team = match.teams[player.team_id]
        module = self.registry.by_id(player.assigned_game)
        player.attempt += 1
        player.current_main = module.generate_main(_new_seed(), level=team.level)
        player.current_bonus = None
        player.status = "solving"
        player.choice_pending = False
        player.timer_kind = None
        player.timer_deadline = None

    def _go_cleared(
        self, match: Match, player: Player, result: EngineResult, now: datetime | None
    ) -> None:
        player.status = "cleared"
        player.current_main = None
        player.choice_pending = True
        self._start_timer(match, player, "wait", result, now)

    def _bonus_fail(
        self, match: Match, player: Player, result: EngineResult
    ) -> None:
        """Wrong bonus answer or bonus deadline expiry: back to solving and
        forfeit this level's bonus earnings (base clear pay stays)."""
        team = match.teams[player.team_id]
        team.currency = max(0, team.currency - player.bonus_earned)
        player.bonus_earned = 0
        result.cancel.append(player.id)
        self._serve_main(match, player)
        self._add_event(
            match, result, f"{player.name} failed the bonus.", "lost_green"
        )

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

    def _team_all_cleared(self, match: Match, team: Team) -> bool:
        members = self._playing_members(match, team)
        return bool(members) and all(green(member) for member in members)

    def _advance_check(
        self, match: Match, team: Team, result: EngineResult, now: datetime | None
    ) -> None:
        """Runs on every cleared transition, not just timer fires."""
        if not self._team_all_cleared(match, team):
            return
        members = self._playing_members(match, team)
        member_ids = {member.id for member in members}
        # Timers scheduled earlier in this same result are now moot.
        result.schedule = [r for r in result.schedule if r.player_id not in member_ids]
        result.cancel.extend(member_ids)

        if team.level >= match.config_snapshot["level_count"]:
            team.finished = True
            match.status = "finished"
            match.winner_team_id = team.id
            result.winner_team_id = team.id
            for player_id in team.player_ids:  # leader included
                member = match.players[player_id]
                member.status = "finished"
                member.current_main = None
                member.current_bonus = None
                member.choice_pending = False
                member.timer_kind = None
                member.timer_deadline = None
            self._add_event(match, result, f"Team {team.name} wins!", "win")
            return

        team.level += 1
        result.advanced_team_ids.append(team.id)
        for member in members:
            member.bonus_streak = 0
            member.bonus_earned = 0
            self._serve_main(match, member)
        self._add_event(
            match, result, f"Team {team.name} advances to Level {team.level}!", "advance"
        )

    def _add_event(
        self, match: Match, result: EngineResult, message: str, kind: str
    ) -> None:
        event = Event(message=message, kind=kind)
        match.events.append(event)
        result.events.append(event)
