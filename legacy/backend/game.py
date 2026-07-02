from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from backend.models import (
    MAX_PLAYERS_PER_TEAM,
    Event,
    Match,
    Player,
    Puzzle,
    ROLES,
    TEAM_IDS,
)
from backend.puzzles import PuzzleRegistry, normalize_answer


POWERUPS = {
    "blur": {"cost": 40, "duration": 5000},
    "shake": {"cost": 30, "duration": 3500},
    "scramble": {"cost": 35, "duration": 6000},
    "dim": {"cost": 25, "duration": 4500},
    "shield": {"cost": 30, "duration": 0},
}


@dataclass
class GameResult:
    ok: bool
    event: Event | None = None
    payload: dict | None = None
    error: str | None = None


class RelayGameEngine:
    def __init__(self, puzzles: PuzzleRegistry | None = None) -> None:
        self.puzzles = puzzles or PuzzleRegistry()

    def create_match(self) -> Match:
        match = Match(id=uuid4().hex[:8])
        self._log(match, "Match created. Teams are waiting at the start line.", "system")
        return match

    def join_match(
        self,
        match: Match,
        name: str,
        team_id: str | None = None,
        role: str | None = None,
    ) -> Player:
        selected_team_id = self._select_team(match, team_id)
        selected_role = self._select_role(match, selected_team_id, role)
        player = Player(
            id=uuid4().hex[:10],
            name=name.strip()[:32] or f"Player {len(match.players) + 1}",
            team_id=selected_team_id,
            role=selected_role,
            connected=True,
        )
        team = match.teams[selected_team_id]
        team.player_ids.append(player.id)
        match.players[player.id] = player
        self._assign_level_puzzle(match, player)
        self._log(
            match,
            f"{player.name} joined Team {team.name} as {player.role}.",
            "join",
        )
        return player

    def reconnect_player(self, match: Match, player_id: str) -> GameResult:
        player = match.players.get(player_id)
        if player is None:
            return GameResult(False, error="Unknown player.")
        was_dormant = player.status == "dormant"
        player.connected = True
        if was_dormant:
            player.status = "backlog"
            player.backlog_puzzle = self.puzzles.create_backlog_puzzle(
                player.role, player.id, match.teams[player.team_id].level
            )
            event = self._log(match, f"{player.name} reconnected and entered backlog sync.", "reconnect")
            return GameResult(True, event=event, payload={"type": "player_reconnected"})
        event = self._log(match, f"{player.name} connected.", "connect")
        return GameResult(True, event=event, payload={"type": "player_reconnected"})

    def disconnect_player(self, match: Match, player_id: str) -> GameResult:
        player = match.players.get(player_id)
        if player is None:
            return GameResult(False, error="Unknown player.")
        player.connected = False
        player.status = "dormant"
        event = self._log(
            match,
            f"{player.name} became a dormant node. Team difficulty increased.",
            "disconnect",
        )
        self._check_team_advance(match, player.team_id)
        return GameResult(True, event=event, payload={"type": "player_disconnected"})

    def submit_puzzle(self, match: Match, player_id: str, puzzle_id: str, answer: str) -> GameResult:
        player = self._require_player(match, player_id)
        if player.status == "backlog":
            return self._submit_backlog(match, player, puzzle_id, answer)
        puzzle = player.current_puzzle
        if puzzle is None or puzzle.id != puzzle_id:
            return GameResult(False, error="Puzzle is no longer active.")
        if normalize_answer(answer) != normalize_answer(puzzle.answer):
            player.attempts += 1
            player.status = "stuck" if player.attempts >= 3 else "active"
            event = self._log(match, f"{player.name} missed a {player.role} puzzle attempt.", "fail")
            return GameResult(False, event=event, error="Incorrect answer.")

        player.status = "grinding"
        player.completed_level = match.teams[player.team_id].level
        player.attempts = 0
        player.current_puzzle = None
        player.current_grind = self.puzzles.create_grind_task(player.role, player.id, player.completed_level)
        event = self._log(match, f"{player.name} cleared the Level {player.completed_level} bottleneck.", "pass")
        advance_event = self._check_team_advance(match, player.team_id)
        return GameResult(True, event=advance_event or event, payload={"mode": player.status})

    def submit_grind(self, match: Match, player_id: str, puzzle_id: str, answer: str) -> GameResult:
        player = self._require_player(match, player_id)
        if player.status != "grinding":
            return GameResult(False, error="Player is not in Grind mode.")
        grind = player.current_grind
        if grind is None or grind.id != puzzle_id:
            return GameResult(False, error="Grind task is no longer active.")
        if normalize_answer(answer) != normalize_answer(grind.answer):
            event = self._log(match, f"{player.name} dropped a grind pulse.", "fail")
            return GameResult(False, event=event, error="Incorrect grind answer.")

        reward = int(grind.payload.get("reward", 10))
        team = match.teams[player.team_id]
        team.points += reward
        next_count = team.points + player.completed_level
        player.current_grind = self.puzzles.create_grind_task(player.role, player.id, next_count)
        event = self._log(match, f"{player.name} earned {reward} team points.", "points")
        return GameResult(True, event=event, payload={"reward": reward})

    def buy_powerup(self, match: Match, player_id: str, powerup: str) -> GameResult:
        player = self._require_player(match, player_id)
        if powerup not in POWERUPS:
            return GameResult(False, error="Unknown power-up.")
        team = match.teams[player.team_id]
        cost = POWERUPS[powerup]["cost"]
        if team.points < cost:
            return GameResult(False, error="Not enough team points.")
        team.points -= cost
        team.inventory[powerup] = team.inventory.get(powerup, 0) + 1
        event = self._log(match, f"Team {team.name} bought {powerup}.", "economy")
        return GameResult(True, event=event)

    def activate_shield(self, match: Match, player_id: str) -> GameResult:
        player = self._require_player(match, player_id)
        team = match.teams[player.team_id]
        if team.inventory.get("shield", 0) <= 0:
            return GameResult(False, error="No shield in inventory.")
        team.inventory["shield"] -= 1
        team.shield_charges += 1
        event = self._log(match, f"Team {team.name} armed a shield charge.", "shield")
        return GameResult(True, event=event)

    def deploy_powerup(
        self, match: Match, player_id: str, powerup: str, target_team_id: str
    ) -> GameResult:
        player = self._require_player(match, player_id)
        source_team = match.teams[player.team_id]
        if powerup not in POWERUPS or powerup == "shield":
            return GameResult(False, error="Unknown attack.")
        if target_team_id not in match.teams or target_team_id == player.team_id:
            return GameResult(False, error="Invalid target team.")
        if source_team.inventory.get(powerup, 0) <= 0:
            return GameResult(False, error="Power-up is not in inventory.")

        source_team.inventory[powerup] -= 1
        target_team = match.teams[target_team_id]
        if target_team.shield_charges > 0:
            target_team.shield_charges -= 1
            event = self._log(
                match,
                f"Team {target_team.name} shielded against Team {source_team.name}'s {powerup}.",
                "shield",
            )
            return GameResult(True, event=event, payload={"blocked": True})

        event = self._log(
            match,
            f"Team {source_team.name} deployed {powerup} against Team {target_team.name}.",
            "sabotage",
        )
        return GameResult(
            True,
            event=event,
            payload={
                "blocked": False,
                "effect": powerup,
                "target_team_id": target_team_id,
                "duration": POWERUPS[powerup]["duration"],
            },
        )

    def _submit_backlog(self, match: Match, player: Player, puzzle_id: str, answer: str) -> GameResult:
        puzzle = player.backlog_puzzle
        if puzzle is None or puzzle.id != puzzle_id:
            return GameResult(False, error="Backlog puzzle is no longer active.")
        if normalize_answer(answer) != normalize_answer(puzzle.answer):
            player.attempts += 1
            event = self._log(match, f"{player.name} missed a backlog sync.", "fail")
            return GameResult(False, event=event, error="Incorrect backlog answer.")
        player.status = "active"
        player.attempts = 0
        player.backlog_puzzle = None
        self._assign_level_puzzle(match, player)
        event = self._log(match, f"{player.name} cleared backlog sync and returned to the relay.", "reconnect")
        return GameResult(True, event=event)

    def _check_team_advance(self, match: Match, team_id: str) -> Event | None:
        team = match.teams[team_id]
        if team.finished or not team.player_ids:
            return None
        blockers = [
            match.players[player_id]
            for player_id in team.player_ids
            if match.players[player_id].status not in {"grinding", "dormant"}
        ]
        if blockers:
            return None
        if team.level >= match.max_level:
            team.finished = True
            return self._log(match, f"Team {team.name} finished The Relay.", "finish")
        team.level += 1
        for player_id in team.player_ids:
            player = match.players[player_id]
            if player.status == "dormant":
                continue
            player.status = "active"
            player.completed_level = team.level - 1
            self._assign_level_puzzle(match, player)
        return self._log(match, f"Team {team.name} advanced to Level {team.level}.", "advance")

    def _assign_level_puzzle(self, match: Match, player: Player) -> Puzzle:
        level = match.teams[player.team_id].level
        player.current_puzzle = self.puzzles.create_role_puzzle(player.role, level, player.id)
        player.current_grind = None
        return player.current_puzzle

    def _select_team(self, match: Match, requested_team_id: str | None) -> str:
        if requested_team_id in TEAM_IDS:
            team = match.teams[requested_team_id]
            if len(team.player_ids) < MAX_PLAYERS_PER_TEAM:
                return requested_team_id
            raise ValueError("Requested team is full.")
        available = sorted(
            match.teams.values(),
            key=lambda team: (len(team.player_ids), team.id),
        )
        for team in available:
            if len(team.player_ids) < MAX_PLAYERS_PER_TEAM:
                return team.id
        raise ValueError("Match is full.")

    def _select_role(self, match: Match, team_id: str, requested_role: str | None) -> str:
        taken = {match.players[player_id].role for player_id in match.teams[team_id].player_ids}
        if requested_role:
            if requested_role not in ROLES:
                raise ValueError("Unknown role.")
            if requested_role in taken:
                raise ValueError("Role is already taken on that team.")
            return requested_role
        for role in ROLES:
            if role not in taken:
                return role
        raise ValueError("No roles left on that team.")

    def _require_player(self, match: Match, player_id: str) -> Player:
        player = match.players.get(player_id)
        if player is None:
            raise ValueError("Unknown player.")
        return player

    def _log(self, match: Match, message: str, kind: str = "info") -> Event:
        event = Event(message=message, kind=kind)
        match.events.append(event)
        match.events = match.events[-80:]
        return event
