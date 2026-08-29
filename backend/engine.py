"""RelayEngine: the pure v2 rules (levels, wait/bonus, currency, perks).

Implements docs/REDESIGN_PLAN.md. Pure/synchronous over a Match: methods
return an `EngineResult` describing what changed and which timers to
(re)schedule or cancel — the engine never sleeps and never does I/O. The
server layer (main.py + TimerService) owns the clock and the sockets.

Deadlines are keyed by *scope*, and a scope holds at most one at a time: a
player id owns the wait timer (which doubles as the bonus deadline), while the
cross-team duel owns DUEL_SCOPE, each team owns `_team_scope()` for its duel
penalty, and a player solving a capped board owns `_fuse_scope()` for it — a
separate scope precisely so a board deadline and a wait deadline can run at
once. Most perk deadlines are deliberately *lazy* and hold no scope at all:
freeze is checked on submit, screen effects are checked by the client, and
Silence is checked in the view layer. Any future deadline that must run
concurrently with an existing one needs its own scope, or to be lazy.

Attack perks are resolved validate-then-mutate (see `_apply_attack`): an attack
that cannot land is rejected without consuming a shield, a reflect or a coin.
"""

from __future__ import annotations

import random
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend import config
from backend.games.duel_base import SIDES, other_side
from backend.models import DuelSession, Event, Match, Player, Team, green
from backend.registry import GameRegistry


# Match-level timer scopes. Player ids are 8-char uuid hex, so these literals
# can never collide with one.
DUEL_SCOPE = "duel"


def _team_scope(team_id: str) -> str:
    return f"team:{team_id}"


def _fuse_scope(player_id: str) -> str:
    """The board deadline's own scope, so it runs alongside the wait timer
    rather than displacing it. Player ids carry a `p_` prefix, so a bare id can
    never look like one of these."""
    return f"fuse:{player_id}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_seed() -> int:
    # Unguessable, server-side only (ARCHITECTURE.md §"Seeds").
    return secrets.randbits(63)


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _extend_deadline(current: str | None, moment: datetime, seconds: int) -> str:
    """`moment + seconds`, but never earlier than a deadline already running.

    Buying the same attack twice must stack forward, never cut the first one
    short — the naive `now + seconds` would do exactly that.
    """
    deadline = moment + timedelta(seconds=seconds)
    if current is not None and _parse_iso(current) > deadline:
        return current
    return deadline.isoformat()


# Victim statuses each enforced attack can land on. A perk that is absent here
# and carries no "effect" hits the TEAM rather than a player, so it picks no
# target at all (Skim, Silence).
_ATTACK_TARGET_STATUSES: dict[str, tuple[str, ...]] = {
    "freeze": ("solving", "bonus"),
    "scramble": ("solving",),
    "clock_burn": ("cleared",),
}


def _attack_target_statuses(perk_id: str, perk: dict) -> tuple[str, ...]:
    """Which victim statuses this attack may pick from; empty for team attacks.

    A Duelist is never in one of these statuses (they sit in `duelling`), which
    is precisely why attack perks can't touch them.
    """
    effect = perk.get("effect")
    if effect is not None:
        # A screen effect only bites while a board is actually on screen — and
        # an id no renderer knows is a catalogue typo, not a legal attack.
        return ("solving", "bonus") if effect in config.SCREEN_EFFECTS else ()
    return _ATTACK_TARGET_STATUSES.get(perk_id, ())


@dataclass
class TimerRequest:
    """Ask the server layer to schedule a deadline for a scope.

    A scope is a player id (the wait timer) or a match-level scope owned by a
    cross-team mechanic: DUEL_SCOPE for the duel phase clock, `_team_scope()`
    for a team's duel penalty. Scheduling replaces that scope's previous timer
    (one active timer each).
    """

    scope_id: str
    kind: str  # "wait" | "puzzle" | "duel_round" | "duel_reveal" | "duel_next"
    #          | "duel_penalty"
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
    duel_result: dict | None = None  # a decided duel, for the reveal nudge
    events: list[Event] = field(default_factory=list)
    schedule: list[TimerRequest] = field(default_factory=list)
    cancel: list[str] = field(default_factory=list)  # timer scopes to cancel

    @staticmethod
    def rejected(message: str) -> EngineResult:
        return EngineResult(ok=False, error=message)


class RelayEngine:
    def __init__(self, registry: GameRegistry) -> None:
        self.registry = registry

    # --- lobby (host-controlled; leaders claimed per team) ---

    def max_players_ceiling(self) -> int:
        """The most playing members a team could ever hold, from the registry.

        Registering a game raises it; the host's own cap is clamped to it.
        """
        return config.max_players_per_team(self.registry.game_count())

    def create_match(self) -> Match:
        teams = {
            team_id: Team(id=team_id, name=team_id.title())
            for team_id in config.TEAM_IDS
        }
        return Match(
            id=uuid4().hex[:8],
            teams=teams,
            min_players=config.MIN_PLAYERS_PER_TEAM,
            # A fresh match opens at the ceiling; the host narrows it to the
            # table they actually have.
            max_players=self.max_players_ceiling(),
            level_count=config.LEVEL_COUNT,
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
        team_capacity = match.max_players + 1  # playing members + a leader
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
        if not isinstance(value, int) or not 1 <= value <= match.max_players:
            return EngineResult.rejected(
                f"min players must be 1..{match.max_players}"
            )
        match.min_players = value
        result = EngineResult(changed=True)
        self._add_event(
            match, result, f"Minimum players per team set to {value}.", "info"
        )
        return result

    def host_set_max_players(
        self, match: Match, host_id: str, value: int
    ) -> EngineResult:
        """The host sizes the table, within what the registry can seat.

        Never below a team that is already fuller than the new cap — the seats
        are taken, and silently over-filling the match would only surface as an
        unstartable board later. Never below `min_players` either, which would
        be a threshold no team could reach.
        """
        guard = self._host_guard(match, host_id)
        if guard is not None:
            return guard
        ceiling = self.max_players_ceiling()
        if not isinstance(value, int) or not 1 <= value <= ceiling:
            return EngineResult.rejected(
                f"max players must be 1..{ceiling} — one seat per game, "
                f"plus the Duelist"
            )
        for team in match.teams.values():
            seated = len(self._playing_members(match, team))
            if seated > value:
                return EngineResult.rejected(
                    f"team {team.name} already has {seated} players"
                )
        match.max_players = value
        result = EngineResult(changed=True)
        self._add_event(
            match, result, f"Maximum players per team set to {value}.", "info"
        )
        if match.min_players > value:
            # A minimum above the maximum is a threshold no team could reach.
            # The host is shrinking the table on purpose, so follow them down
            # rather than refusing and making them undo the minimum first.
            match.min_players = value
            self._add_event(
                match, result,
                f"Minimum players per team lowered to {value} to match.", "info",
            )
        return result

    def host_set_level_count(
        self, match: Match, host_id: str, value: int
    ) -> EngineResult:
        """The host sets how many rounds it takes to win.

        A shorter match is a quicker race, not an easier one — the difficulty
        rungs spread so the finale is always the hardest tier (see
        `config.difficulty_tier`).
        """
        guard = self._host_guard(match, host_id)
        if guard is not None:
            return guard
        low, high = config.MIN_LEVEL_COUNT, config.max_level_count()
        if not isinstance(value, int) or not low <= value <= high:
            return EngineResult.rejected(f"rounds must be {low}..{high}")
        if value == match.level_count:
            return EngineResult.rejected(f"already {value} rounds")
        match.level_count = value
        result = EngineResult(changed=True)
        self._add_event(match, result, f"The race is now {value} rounds.", "info")
        return result

    def host_set_team_name(
        self, match: Match, host_id: str, team_id: str, name: str
    ) -> EngineResult:
        """The host names a team. Lobby only — a squad does not get renamed
        out from under a race that is already being run."""
        guard = self._host_guard(match, host_id)
        if guard is not None:
            return guard
        team = match.teams.get(team_id)
        if team is None:
            return EngineResult.rejected(f"unknown team {team_id!r}")
        cleaned = " ".join(str(name).split())
        if not cleaned:
            return EngineResult.rejected("a team needs a name")
        if len(cleaned) > config.TEAM_NAME_MAX:
            return EngineResult.rejected(
                f"a team name is at most {config.TEAM_NAME_MAX} characters"
            )
        taken = [
            other for other in match.teams.values()
            if other.id != team.id and other.name.casefold() == cleaned.casefold()
        ]
        if taken:
            return EngineResult.rejected("the other team already has that name")
        was = team.name
        if cleaned == was:
            return EngineResult.rejected(f"already called {was}")
        team.name = cleaned
        result = EngineResult(changed=True)
        self._add_event(match, result, f"{was} is now {cleaned}.", "info")
        return result

    def host_cancel_session(self, match: Match, host_id: str) -> EngineResult:
        """Bin a lobby that never started. Nothing was played, so there is no
        result to show — every socket is closed and the match is dropped."""
        guard = self._is_host(match, host_id)
        if guard is not None:
            return guard
        if match.status != "lobby":
            return EngineResult.rejected(
                "the match has started — end it instead of cancelling it"
            )
        match.status = "cancelled"
        match.ended_reason = "host_cancelled"
        result = EngineResult(changed=True)
        self._add_event(match, result, "The host cancelled the session.", "info")
        return result

    def host_end_session(self, match: Match, host_id: str) -> EngineResult:
        """Stop a running match. It finishes with no winner: the race did not
        decide anything, and recording one team as champion would be a lie."""
        guard = self._is_host(match, host_id)
        if guard is not None:
            return guard
        if match.status != "active":
            return EngineResult.rejected("no match is running")
        match.status = "finished"
        match.ended_reason = "host_ended"
        result = EngineResult(changed=True)
        # Every clock in the match belongs to a player, a fuse, or the duel.
        for player in match.players.values():
            player.status = "finished"
            player.current_main = None
            player.current_bonus = None
            player.choice_pending = False
            player.timer_kind = None
            player.timer_deadline = None
            player.puzzle_deadline = None
            result.cancel.append(player.id)
            result.cancel.append(_fuse_scope(player.id))
        match.duel = None
        result.cancel.append(DUEL_SCOPE)
        result.schedule = []
        self._add_event(match, result, "The host ended the match.", "info")
        return result

    def leave_match(self, match: Match, player_id: str) -> EngineResult:
        """A player takes themselves out of the lobby.

        The host may leave like anyone else; the seat passes to whoever is
        still here rather than stranding the lobby with controls nobody holds.
        Lobby only: pulling a player out of a running match would leave their
        team racing against a roster size that counted them.
        """
        if match.status != "lobby":
            return EngineResult.rejected(
                "you can't leave a running match — ask the host to end it"
            )
        player = match.players.get(player_id)
        if player is None:
            return EngineResult.rejected("unknown player")
        if player.team_id is not None:
            team = match.teams[player.team_id]
            team.player_ids.remove(player.id)
            if team.leader_id == player.id:
                team.leader_id = None
        del match.players[player.id]
        result = EngineResult(changed=True, kicked_player_ids=[player.id])
        self._add_event(match, result, f"{player.name} left.", "info")
        if match.host_player_id == player.id:
            self._pass_host_on(match, result)
        return result

    def _pass_host_on(self, match: Match, result: EngineResult) -> None:
        """Hand the host seat to somebody still in the lobby.

        A player who has already taken a team is preferred — they are the ones
        actually here to race — and a connected seat over a dark one. An empty
        lobby simply has no host, and the next joiner picks it up.
        """
        remaining = list(match.players.values())
        if not remaining:
            match.host_player_id = None
            return
        # Connected before dark, seated before drifting, and then simply whoever
        # got here first — `match.players` keeps join order, which is a reason a
        # player can follow rather than an arbitrary pick.
        order = {player_id: i for i, player_id in enumerate(match.players)}
        successor = min(
            remaining,
            key=lambda p: (not p.connected, p.team_id is None, order[p.id]),
        )
        match.host_player_id = successor.id
        self._add_event(
            match, result, f"{successor.name} is now hosting.", "info"
        )

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
        """Take over from a host who is gone (kick-proof: only claimable while
        the current host is disconnected or missing).

        Allowed in a running match too, not just the lobby: the host holds the
        only control that can end a session, so a host who closes their tab
        mid-race must not take that with them.
        """
        if match.status not in ("lobby", "active"):
            return EngineResult.rejected("the match is over")
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
            return EngineResult.rejected("pick a team before claiming the Grandmaster seat")
        team = match.teams[player.team_id]
        current = match.players.get(team.leader_id or "")
        if current is player:
            return EngineResult.rejected("you already lead this team")
        if current is not None and current.connected:
            return EngineResult.rejected(f"team {team.name} already has a Grandmaster")
        if current is not None:
            current.is_leader = False
        player.is_leader = True
        player.role = None  # the Grandmaster seat has no playing role
        player.assigned_game = None  # leaders don't play
        team.leader_id = player.id
        result = EngineResult(changed=True)
        self._add_event(
            match,
            result,
            f"{player.name} is now team {team.name}'s Grandmaster.",
            "info",
        )
        return result

    def give_leader(
        self,
        match: Match,
        leader_id: str,
        target_id: str,
        now: datetime | None = None,
    ) -> EngineResult:
        """The Grandmaster hands the seat to a teammate.

        Lobby: just moves the flag (the new leader's role and assignment are
        cleared; the old leader becomes assignable). Active match: full swap —
        the recipient stops playing, the old leader takes over their role and
        game at the current level with a fresh, un-cleared puzzle. Once per
        team per level.
        """
        if match.status == "finished":
            return EngineResult.rejected("match is over")
        leader = match.players.get(leader_id)
        if leader is None or not leader.is_leader:
            return EngineResult.rejected("only the Grandmaster can do that")
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
            target.role = None
            target.assigned_game = None
            team.leader_id = target.id
            self._add_event(
                match,
                result,
                f"{leader.name} handed team {team.name}'s Grandmaster seat to {target.name}.",
                "info",
            )
            return result

        # Active match: full swap, once per level.
        if team.handoff_used_level == team.level:
            return EngineResult.rejected("the Grandmaster seat already changed this level")
        if config.role_is_duel(target.role):
            # The duel holds two fixed seats for the whole match; vacating one
            # mid-duel would leave the opposing Duelist with nobody to fight.
            return EngineResult.rejected(
                "the Duelist can't take the Grandmaster seat mid-match"
            )
        game_id = target.assigned_game
        if game_id is None:  # defensive: every playing member is assigned at start
            return EngineResult.rejected("target has no assigned game")

        team.handoff_used_level = team.level
        team.leader_id = target.id

        # Old leader becomes the player, inheriting the seat's economy counters
        # so the same level can't pay base currency twice.
        leader.is_leader = False
        leader.role = target.role
        leader.assigned_game = game_id
        leader.earned_level = target.earned_level
        leader.bonus_streak = target.bonus_streak
        leader.bonus_earned = target.bonus_earned
        self._serve_main(match, leader, result, now)

        # Recipient stops playing: any cleared status/timer is gone.
        target.is_leader = True
        target.status = "leading"
        target.role = None
        target.assigned_game = None
        target.current_main = None
        target.current_bonus = None
        target.choice_pending = False
        target.timer_kind = None
        target.timer_deadline = None
        self._clear_board_deadline(target, result)
        target.earned_level = 0
        target.bonus_streak = 0
        target.bonus_earned = 0
        result.cancel.append(target.id)

        self._add_event(
            match,
            result,
            f"{leader.name} handed team {team.name}'s Grandmaster seat to {target.name}.",
            "info",
        )
        self._advance_check(match, team, result, now)
        return result

    def assign_role(
        self, match: Match, leader_id: str, target_id: str, role_id: str
    ) -> EngineResult:
        """The Grandmaster gives a teammate a role (lobby only). The role
        gates which games the player may be assigned; an out-of-role game
        assignment is cleared so lobby state stays consistent."""
        if match.status != "lobby":
            return EngineResult.rejected("match already started")
        leader = match.players.get(leader_id)
        if leader is None or not leader.is_leader:
            return EngineResult.rejected("only the Grandmaster can assign roles")
        target = match.players.get(target_id)
        if target is None or target.team_id != leader.team_id:
            return EngineResult.rejected("target must be a teammate")
        if target.is_leader:
            return EngineResult.rejected("the Grandmaster doesn't take a role")
        if role_id not in config.ROLES:
            return EngineResult.rejected(f"unknown role {role_id!r}")
        if not config.role_assignable(role_id):
            return EngineResult.rejected(
                f"{config.ROLES[role_id]['name']} has no games yet"
            )
        fixed_game = config.role_fixed_game(role_id)
        if fixed_game is not None:
            # A fixed role carries one game, so a second holder would put two
            # teammates on it and break game uniqueness the moment the role
            # lands. Refuse here rather than at start: the Grandmaster gets the
            # feedback on the click that caused it.
            holder = next(
                (
                    member
                    for member in self._playing_members(
                        match, match.teams[target.team_id]
                    )
                    if member.id != target.id and member.role == role_id
                ),
                None,
            )
            if holder is not None:
                return EngineResult.rejected(
                    f"{holder.name} is already the {config.ROLES[role_id]['name']}"
                )
        target.role = role_id
        if config.role_is_duel(role_id):
            # The Duelist doesn't get a choice of game — the server picks it,
            # so the "everyone needs a game" start gate is already satisfied.
            target.assigned_game = self.registry.pick_duel(_new_seed()).id
        elif fixed_game is not None:
            # Same idea, but the role names the game itself: the Grandmaster
            # chooses who defuses, never what they play.
            target.assigned_game = fixed_game
        elif target.assigned_game is not None and not (
            # Moving *off* the duel role must also drop the server's pick: a
            # duel id is not a registered game, so the Generalist (which allows
            # every game) would otherwise inherit an unresolvable assignment.
            self.registry.has(target.assigned_game)
            and config.role_allows(role_id, target.assigned_game)
        ):
            target.assigned_game = None
        result = EngineResult(changed=True)
        self._add_event(
            match,
            result,
            f"{target.name} is now the {config.ROLES[role_id]['name']}.",
            "info",
        )
        return result

    def assign_game(
        self, match: Match, leader_id: str, target_id: str, game_id: str
    ) -> EngineResult:
        """The Grandmaster assigns a game to a teammate (lobby only). The game
        must fit the target's role, and no two teammates may play the same
        game."""
        if match.status != "lobby":
            return EngineResult.rejected("match already started")
        leader = match.players.get(leader_id)
        if leader is None or not leader.is_leader:
            return EngineResult.rejected("only the Grandmaster can assign games")
        target = match.players.get(target_id)
        if target is None or target.team_id != leader.team_id:
            return EngineResult.rejected("target must be a teammate")
        if target.is_leader:
            return EngineResult.rejected("the Grandmaster doesn't play")
        if target.role is not None and config.role_is_duel(target.role):
            return EngineResult.rejected(
                f"the server picks the {config.ROLES[target.role]['name']}'s game"
            )
        fixed_game = config.role_fixed_game(target.role)
        if fixed_game is not None:
            return EngineResult.rejected(
                f"the {config.ROLES[target.role]['name']} always plays "
                f"{self.registry.by_id(fixed_game).name}"
            )
        if not self.registry.has(game_id):
            return EngineResult.rejected(f"unknown game {game_id!r}")
        if target.role is None:
            return EngineResult.rejected(f"assign {target.name} a role first")
        if not config.role_allows(target.role, game_id):
            return EngineResult.rejected(
                f"{target.name}'s role can't play {game_id}"
            )
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

    def _required_roles(self) -> list[str]:
        """Required roles *this* registry can actually satisfy.

        A required role only gates the start if its game is registered. The
        engine validates against the library it was handed, never against a
        game id it assumes exists — so a trimmed deployment, or a test running
        on a fake library, still starts. In production the bomb is registered
        and the gate always bites.
        """
        live = []
        for role_id in config.required_roles():
            fixed = config.role_fixed_game(role_id)
            if fixed is None or self.registry.has(fixed):
                live.append(role_id)
        return live

    def start_blocker(self, match: Match) -> str | None:
        """Why the match can't start yet, or None when it can."""
        if match.unassigned():
            names = ", ".join(p.name for p in match.unassigned())
            return f"everyone needs a team (waiting on {names})"
        for team in match.teams.values():
            leader = match.players.get(team.leader_id or "")
            if leader is None or not leader.is_leader:
                return f"team {team.name} needs a Grandmaster"
            playing = self._playing_members(match, team)
            if len(playing) < match.min_players:
                return f"team {team.name} needs {match.min_players} player(s)"
            if len(playing) > match.max_players:
                return f"team {team.name} has too many players"
            unroled = [p.name for p in playing if p.role is None]
            if unroled:
                return (
                    f"team {team.name}: assign a role to {', '.join(unroled)}"
                )
            unassigned = [p.name for p in playing if p.assigned_game is None]
            if unassigned:
                return (
                    f"team {team.name}: assign a game to {', '.join(unassigned)}"
                )
            # Required roles: the bomb is the game no team opts out of, so
            # every team names exactly one Defuser or the match can't start.
            for role_id in self._required_roles():
                role_name = config.ROLES[role_id]["name"]
                holders = [p for p in playing if p.role == role_id]
                if len(holders) > 1:
                    return f"team {team.name} can only field one {role_name}"
                if not holders:
                    if len(playing) < 2 and any(
                        config.role_is_duel(p.role) for p in playing
                    ):
                        # The squeeze at small table sizes: a Duelist and a
                        # Defuser are two forced seats and this team has room
                        # for one. Say so, rather than looking like a deadlock.
                        return (
                            f"team {team.name} needs a {role_name}, but its "
                            f"only player is a Duelist — drop the Duelist or "
                            f"add a player"
                        )
                    return f"team {team.name} needs a {role_name}"
            if len(self._duelists(match, team)) > 1:
                return f"team {team.name} can only field one Duelist"
        # The Duelist is mirrored: a duel needs two seats, so one team fielding
        # a champion forces the other to answer with one.
        fielding = [
            team for team in match.teams.values() if self._duelists(match, team)
        ]
        if len(fielding) == 1:
            other = next(
                team for team in match.teams.values() if team is not fielding[0]
            )
            return (
                f"team {fielding[0].name} has a Duelist — team {other.name} "
                f"needs one too"
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
        if len(team.player_ids) >= match.max_players + 1:
            return EngineResult.rejected(f"team {team.name} is full")
        if player.team_id is not None:
            old_team = match.teams[player.team_id]
            old_team.player_ids.remove(player.id)
            if old_team.leader_id == player.id:
                old_team.leader_id = None
        # Leadership, roles, and assignments don't cross teams.
        player.is_leader = False
        player.role = None
        player.assigned_game = None
        player.team_id = team_id
        team.player_ids.append(player.id)
        result = EngineResult(changed=True)
        self._add_event(
            match, result, f"{player.name} joined team {team.name}.", "join"
        )
        return result

    def _host_guard(self, match: Match, player_id: str) -> EngineResult | None:
        """The host, in the lobby — every control that shapes a match before it
        is run. Ending a *running* match is the one host power that outlives the
        lobby, and it checks `_is_host` directly instead."""
        if match.status != "lobby":
            return EngineResult.rejected("match already started")
        return self._is_host(match, player_id)

    def _is_host(self, match: Match, player_id: str) -> EngineResult | None:
        if player_id != match.host_player_id:
            return EngineResult.rejected("only the host can do that")
        return None

    def start_match(self, match: Match, now: datetime | None = None) -> EngineResult:
        """Freeze rosters and config; leaders observe, players start Level 1."""
        match.status = "active"
        match.config_snapshot = {
            "wait_seconds": config.WAIT_SECONDS,
            "puzzle_grace_seconds": config.PUZZLE_GRACE_SECONDS,
            "level_count": match.level_count or config.LEVEL_COUNT,
            "difficulty_tiers": config.DIFFICULTY_TIERS,
            "players_per_team": match.max_players,
            "max_players_ceiling": self.max_players_ceiling(),
            "currency_per_clear": config.CURRENCY_PER_CLEAR,
            "currency_bonus_first": config.CURRENCY_BONUS_FIRST,
            "currency_bonus_repeat": config.CURRENCY_BONUS_REPEAT,
            "bonus_level_offset": config.BONUS_LEVEL_OFFSET,
            "perks": {perk_id: dict(perk) for perk_id, perk in config.PERKS.items()},
            "duels_per_level": config.DUELS_PER_LEVEL,
            "duel_next_seconds": config.DUEL_INTERVAL_SECONDS,
            "duel_reveal_seconds": config.DUEL_REVEAL_SECONDS,
            "duel_penalty_seconds": config.DUEL_PENALTY_SECONDS,
            "duel_win_currency": config.DUEL_WIN_CURRENCY,
            "duel_currency_cap": config.DUEL_CURRENCY_CAP,
        }
        result = EngineResult(changed=True, match_started=True)
        for team in match.teams.values():
            playing = self._playing_members(match, team)
            team.roster_size = len(playing)
            leader = match.players.get(team.leader_id or "")
            if leader is not None:
                leader.status = "leading"
            for player in playing:
                if config.role_is_duel(player.role):
                    continue  # champions duel instead; _start_duel seats them
                self._serve_main(match, player, result, now)
        self._add_event(match, result, "Match started — Level 1!", "info")
        self._start_duel(match, result, now)
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
            # Wrong: stay solving, but on a fresh instance (new seed,
            # attempt+1) — and a fresh deadline with it.
            wrong = EngineResult(correct=False, changed=True)
            self._serve_main(match, player, wrong, now)
            return wrong

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
            self._bonus_fail(match, player, result, now)
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
        player.choice_pending = False
        player.status = "bonus"
        # The running wait deadline stays: it is now the bonus deadline.
        player.current_bonus = module.generate_main(
            _new_seed(), level=self._bonus_level(match, team)
        )
        return EngineResult(changed=True)

    def on_wait_expired(
        self, match: Match, player_id: str, now: datetime | None = None
    ) -> EngineResult:
        player = match.players.get(player_id)
        if match.status != "active" or player is None:
            return EngineResult(changed=False)  # stale timer — no-op
        if config.role_is_duel(player.role):
            # A Duelist holds no wait timer: their green comes from the last
            # duel and only the next duel takes it away.
            return EngineResult(changed=False)
        if player.status == "cleared":
            result = EngineResult(changed=True)
            self._serve_main(match, player, result, now)
            self._add_event(
                match, result, f"{player.name} lost cleared status.", "lost_green"
            )
            return result
        if player.status == "bonus":
            result = EngineResult(changed=True)
            self._bonus_fail(match, player, result, now)
            return result
        return EngineResult(changed=False)  # stale timer — no-op

    def on_puzzle_expired(
        self, match: Match, player_id: str, now: datetime | None = None
    ) -> EngineResult:
        """A board's own deadline passed: serve a fresh one, exactly as a wrong
        answer does.

        Not a penalty beyond that — losing the board *is* the penalty, the same
        cost §20 of `bomb.md` puts on a detonation. Cleared status, currency and
        the wait timer are all untouched, because none of them were in play: a
        player only holds a board deadline while they are solving.
        """
        player = match.players.get(player_id)
        if match.status != "active" or player is None:
            return EngineResult(changed=False)  # stale timer — no-op
        if player.status != "solving" or player.puzzle_deadline is None:
            # They cleared it, took a bonus, or were handed the Grandmaster
            # seat between the deadline and this call.
            return EngineResult(changed=False)
        if _parse_iso(player.puzzle_deadline) > (now or utc_now()):
            # A timer already past its sleep cannot be cancelled, so a board
            # re-served in that window would otherwise be killed by the
            # previous board's clock. The grace makes this free: the timer
            # fires PUZZLE_GRACE_SECONDS *after* the deadline it is compared
            # to, so this can never reject a fire that is genuinely due.
            return EngineResult(changed=False)
        result = EngineResult(changed=True)
        self._serve_main(match, player, result, now)
        self._add_event(
            match, result, f"{player.name} ran out of time.", "lost_green"
        )
        return result

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
            return EngineResult.rejected("only the Grandmaster can buy perks")
        perk = match.config_snapshot["perks"].get(perk_id)
        if perk is None:
            return EngineResult.rejected(f"unknown perk {perk_id!r}")
        team = match.teams[leader.team_id]
        if team.currency < perk["cost"]:
            return EngineResult.rejected("not enough currency")

        result = EngineResult(changed=True)
        if perk["kind"] == "attack":
            applied = self._apply_attack(match, team, perk_id, perk, result, now)
        else:
            applied = self._apply_defense(match, team, perk_id, perk, target_id, result)
        if not applied.ok:
            return applied

        team.currency -= perk["cost"]
        result.perk_used = {"perk_id": perk_id, "by_team_id": team.id}
        self._add_event(
            match, result, f"Team {team.name} used {perk['name']}.", "perk"
        )
        return result

    def _apply_defense(
        self,
        match: Match,
        team: Team,
        perk_id: str,
        perk: dict,
        target_id: str | None,
        result: EngineResult,
    ) -> EngineResult:
        """Defensive perks act on the buyer's own team. The one-at-a-time perks
        hold until something consumes them — they don't lapse at a level."""
        if perk_id == "shield":
            if team.shield_active:
                return EngineResult.rejected("shield already active")
            team.shield_active = True
        elif perk_id == "reflect":
            if team.reflect_active:
                return EngineResult.rejected("reflect already active")
            team.reflect_active = True
        elif perk_id == "insurance":
            if team.insurance_active:
                return EngineResult.rejected("insurance already active")
            team.insurance_active = True
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
                    scope_id=target.id, kind="wait", deadline=target.timer_deadline
                )
            )
        else:
            return EngineResult.rejected(f"unknown perk {perk_id!r}")
        return result

    def _apply_attack(
        self,
        match: Match,
        buyer: Team,
        perk_id: str,
        perk: dict,
        result: EngineResult,
        now: datetime | None,
    ) -> EngineResult:
        """Resolve an attack bought by `buyer` against the other team.

        Ordered validate-then-mutate on purpose: an attack that can't land is
        *rejected, not wasted*, and a rejection must leave shields, reflects and
        currency exactly as it found them.
        """
        opponent = self._opponent_team(match, buyer)
        reflected = opponent.reflect_active
        if not reflected and opponent.shield_active:
            # The shield eats the attack whole — no target is ever chosen.
            opponent.shield_active = False
            self._add_event(
                match,
                result,
                f"Team {opponent.name}'s shield blocked an attack!",
                "perk",
            )
            return result

        # A reflected attack comes home to the buyer, and the buyer's own shield
        # or reflect cannot stop it. That rule is what stops two Reflects from
        # ping-ponging an attack between the teams forever.
        victim = buyer if reflected else opponent
        target: Player | None = None
        statuses = _attack_target_statuses(perk_id, perk)
        if statuses:
            candidates = [
                player
                for player in self._playing_members(match, victim)
                if player.status in statuses
            ]
            if perk_id == "clock_burn":  # needs a running wait deadline to burn
                candidates = [p for p in candidates if p.timer_deadline is not None]
            if not candidates:
                return EngineResult.rejected("no valid target right now")
            target = random.choice(candidates)  # fog of war: the server picks
        elif perk_id == "skim":
            if victim.currency <= 0:
                return EngineResult.rejected("their pool is already empty")
        elif perk_id == "silence":
            if victim.leader_id is None:
                return EngineResult.rejected("they have no Grandmaster to blind")
        else:
            return EngineResult.rejected(f"unknown perk {perk_id!r}")

        if reflected:
            opponent.reflect_active = False
            self._add_event(
                match, result, f"Team {opponent.name} reflected the attack!", "perk"
            )
        self._land_attack(match, victim, target, perk_id, perk, result, now)
        return result

    def _land_attack(
        self,
        match: Match,
        victim: Team,
        target: Player | None,
        perk_id: str,
        perk: dict,
        result: EngineResult,
        now: datetime | None,
    ) -> None:
        """Apply a validated attack. Every deadline this sets is pushed out with
        `_extend_deadline`, so buying the same attack twice can never *shorten*
        the effect already running."""
        moment = now or utc_now()
        effect = perk.get("effect")
        if effect and target is not None:
            target.screen_effects[effect] = _extend_deadline(
                target.screen_effects.get(effect), moment, perk["seconds"]
            )
        elif perk_id == "freeze" and target is not None:
            was = _parse_iso(target.frozen_until) if target.frozen_until else None
            target.frozen_until = _extend_deadline(
                target.frozen_until, moment, perk["seconds"]
            )
            if target.puzzle_deadline is not None:
                # A timed board must not burn down while its player is shut out
                # of it: the freeze costs them the input, not the board. Pushed
                # by however much the frozen window actually *grew*, so stacking
                # two freezes never pays out more time than it locks away.
                start = was if was is not None and was > moment else moment
                added = (_parse_iso(target.frozen_until) - start).total_seconds()
                target.puzzle_deadline = (
                    _parse_iso(target.puzzle_deadline) + timedelta(seconds=added)
                ).isoformat()
                self._schedule_board_deadline(match, target, result)
        elif perk_id == "scramble" and target is not None:
            # A solving player holds no wait timer, so there is nothing to
            # cancel there. The fresh board inherits the *old* board's deadline:
            # a Scramble takes your work, and on a timed game a fresh clock
            # would make the attack a gift late in a board.
            self._serve_main(match, target, result, now, keep_deadline=True)
        elif perk_id == "clock_burn" and target is not None:
            deadline = _parse_iso(target.timer_deadline) - timedelta(
                seconds=perk["seconds"]
            )
            # A burn past `now` is legal: the timer service clamps the delay to
            # zero, the wait lapses at once and the victim loses cleared status.
            target.timer_deadline = deadline.isoformat()
            result.schedule.append(
                TimerRequest(
                    scope_id=target.id, kind="wait", deadline=target.timer_deadline
                )
            )
        elif perk_id == "skim":
            taker = self._opponent_team(match, victim)
            amount = min(perk["amount"], victim.currency)
            victim.currency -= amount
            taker.currency += amount
        elif perk_id == "silence":
            victim.silenced_until = _extend_deadline(
                victim.silenced_until, moment, perk["seconds"]
            )

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

    def on_reconnect(
        self, match: Match, player_id: str, now: datetime | None = None
    ) -> EngineResult:
        player = match.players.get(player_id)
        if player is None:
            return EngineResult(changed=False)
        player.connected = True
        result = EngineResult(changed=True)
        if match.status == "active" and player.status == "solving":
            # Fresh instance so a watched/failed board can't be replayed
            # (ECHO) — and the deadline restarts with it, because a board
            # nobody could see should not have been burning down.
            self._serve_main(match, player, result, now)
        elif match.status == "active" and player.status == "bonus":
            team = match.teams[player.team_id]
            module = self.registry.by_id(player.assigned_game)
            player.current_bonus = module.generate_main(
                _new_seed(), level=self._bonus_level(match, team)
            )
        return result

    # --- internals ---

    def _difficulty_tier(self, match: Match, round_number: int) -> int:
        """The rung of the game tables this round is played at.

        `team.level` counts rounds (1..level_count); this maps that onto the
        fixed 13-row ladder, so a 3-round match still finishes at the top.
        """
        snapshot = match.config_snapshot
        rounds = snapshot.get("level_count") or match.level_count
        return config.difficulty_tier(round_number, rounds)

    def _bonus_level(self, match: Match, team: Team) -> int:
        """The level a bonus board is generated at: BONUS_LEVEL_OFFSET tiers
        above the team's own level.

        The ceiling is `level_count + bonus_level_offset` — levels 11..13 exist
        in every game's table as bonus-only tiers (V5), so a team on the last
        level still gets a board harder than the one they just cleared.
        """
        snapshot = match.config_snapshot
        return min(
            self._difficulty_tier(match, team.level) + snapshot["bonus_level_offset"],
            snapshot.get("difficulty_tiers", config.DIFFICULTY_TIERS),
        )

    def _playing_members(self, match: Match, team: Team) -> list[Player]:
        return [
            match.players[player_id]
            for player_id in team.player_ids
            if not match.players[player_id].is_leader
        ]

    def _serve_main(
        self,
        match: Match,
        player: Player,
        result: EngineResult,
        now: datetime | None = None,
        keep_deadline: bool = False,
    ) -> None:
        """Fresh main instance of the player's own game at the team's level.

        The single funnel for a solving board — a wrong answer, a Scramble, a
        reconnect, a level advance and a leader handoff all come through here —
        which is why the board deadline is armed here and nowhere else.

        `keep_deadline` hands the fresh board the clock the old one was running
        against, rather than a new one. Only a Scramble uses it: an attack that
        restarted the clock would *help* its victim on a timed game.
        """
        team = match.teams[player.team_id]
        module = self.registry.by_id(player.assigned_game)
        player.attempt += 1
        player.current_main = module.generate_main(
            _new_seed(), level=self._difficulty_tier(match, team.level)
        )
        player.current_bonus = None
        player.status = "solving"
        player.choice_pending = False
        player.timer_kind = None
        player.timer_deadline = None
        self._arm_board_deadline(match, player, result, now, keep=keep_deadline)

    def _board_limit(self, player: Player) -> float | None:
        """The board's own time limit in seconds, or None if it asks for none."""
        puzzle = player.current_main
        if puzzle is None:
            return None
        raw = puzzle.payload.get("time_limit_seconds")
        return float(raw) if isinstance(raw, (int, float)) and raw > 0 else None

    def _schedule_board_deadline(
        self, match: Match, player: Player, result: EngineResult
    ) -> None:
        """(Re)schedule the backstop for whatever `puzzle_deadline` now says.

        Separate from arming it because a deadline can *move* after it is set —
        a Freeze pushes it out — and the timer has to follow.
        """
        grace = match.config_snapshot.get(
            "puzzle_grace_seconds", config.PUZZLE_GRACE_SECONDS
        )
        result.schedule.append(
            TimerRequest(
                scope_id=_fuse_scope(player.id),
                kind="puzzle",
                deadline=(
                    _parse_iso(player.puzzle_deadline) + timedelta(seconds=grace)
                ).isoformat(),
            )
        )

    def _arm_board_deadline(
        self,
        match: Match,
        player: Player,
        result: EngineResult,
        now: datetime | None,
        keep: bool = False,
    ) -> None:
        """Give the freshly served board its deadline, if its game asks for one.

        Opt-in and generic: the engine reads `payload["time_limit_seconds"]`
        and knows nothing else about the game (docs/GAME_MODULE_SPEC.md). A
        game without the key is unlimited, which is still every game but the
        bomb.

        The deadline the player is *told* is the honest one. The timer fires
        `PUZZLE_GRACE_SECONDS` later, so an answer already in flight when the
        clock runs out is still counted — slack the player never sees and an
        honest one never needs.
        """
        limit = self._board_limit(player)
        if limit is None:
            player.puzzle_deadline = None
            result.cancel.append(_fuse_scope(player.id))
            return
        if not (keep and player.puzzle_deadline is not None):
            player.puzzle_deadline = (
                (now or utc_now()) + timedelta(seconds=limit)
            ).isoformat()
        self._schedule_board_deadline(match, player, result)

    def _clear_board_deadline(self, player: Player, result: EngineResult) -> None:
        """The player is off this board: the backstop goes with it."""
        player.puzzle_deadline = None
        result.cancel.append(_fuse_scope(player.id))

    def _go_cleared(
        self, match: Match, player: Player, result: EngineResult, now: datetime | None
    ) -> None:
        player.status = "cleared"
        player.current_main = None
        player.choice_pending = True
        self._clear_board_deadline(player, result)
        self._start_timer(match, player, "wait", result, now)

    def _bonus_fail(
        self,
        match: Match,
        player: Player,
        result: EngineResult,
        now: datetime | None = None,
    ) -> None:
        """Wrong bonus answer or bonus deadline expiry: back to solving and
        forfeit this level's bonus earnings (base clear pay stays)."""
        team = match.teams[player.team_id]
        if team.insurance_active and player.bonus_earned:
            # Insurance is only spent on a failure that would actually cost
            # something — a bonus that had earned nothing doesn't burn it.
            team.insurance_active = False
            self._add_event(
                match,
                result,
                f"Insurance covered {player.name}'s failed bonus.",
                "perk",
            )
        else:
            team.currency = max(0, team.currency - player.bonus_earned)
        player.bonus_earned = 0
        result.cancel.append(player.id)
        self._serve_main(match, player, result, now)
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
            TimerRequest(scope_id=player.id, kind=kind, deadline=player.timer_deadline)
        )

    def _start_scope_timer(
        self, match: Match, scope_id: str, kind: str, result: EngineResult,
        now: datetime | None, seconds: float | None = None,
    ) -> str:
        """Schedule a match-level deadline (duel phases, duel penalty).

        Unlike `_start_timer` this writes no `player.timer_*` fields, so a duel
        clock never displaces the wait countdown a player's client is drawing.
        `seconds` defaults to the frozen config value for `kind`; the duel round
        window passes it explicitly because it belongs to the duel module.
        Returns the deadline so the caller can store it on its own object.
        """
        if seconds is None:
            seconds = match.config_snapshot[f"{kind}_seconds"]
        deadline = ((now or utc_now()) + timedelta(seconds=seconds)).isoformat()
        result.schedule.append(
            TimerRequest(scope_id=scope_id, kind=kind, deadline=deadline)
        )
        return deadline

    def _team_all_cleared(self, match: Match, team: Team) -> bool:
        members = self._playing_members(match, team)
        return bool(members) and all(green(member) for member in members)

    def _advance_check(
        self, match: Match, team: Team, result: EngineResult, now: datetime | None
    ) -> None:
        """Runs on every cleared transition, not just timer fires."""
        if not self._team_all_cleared(match, team):
            return
        if self._duel_penalty_active(team, now):
            # Everyone is green but the team lost a duel this level and still
            # owes the lock. Their wait timers keep running — holding green
            # through the penalty is the tax. The `duel_penalty` timer calls
            # us again when it lapses.
            return
        members = self._playing_members(match, team)
        member_ids = {member.id for member in members}
        # Timers scheduled earlier in this same result are now moot. Only the
        # members' own scopes — their wait timers and their board deadlines;
        # duel/team scopes outlive an advance.
        own = member_ids | {_fuse_scope(member_id) for member_id in member_ids}
        result.schedule = [r for r in result.schedule if r.scope_id not in own]
        result.cancel.extend(own)

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
                self._clear_board_deadline(member, result)
            self._add_event(match, result, f"Team {team.name} wins!", "win")
            return

        team.level += 1
        team.duel_penalty_until = None
        team.duel_penalty_level = 0  # the new level may take its own hit
        # A new level buys a fresh duel series. Only re-open it if the last one
        # ran out; mid-series there is already a `duel_next` pending.
        if match.duel is not None and match.duel.phase == "done":
            if match.duels_played >= match.config_snapshot["duels_per_level"]:
                match.duel.deadline = self._start_scope_timer(
                    match, DUEL_SCOPE, "duel_next", result, now
                )
            match.duels_played = 0
        result.advanced_team_ids.append(team.id)
        for member in members:
            member.bonus_streak = 0
            member.bonus_earned = 0
            if config.role_is_duel(member.role):
                # The champion carries their duel win into the new level; the
                # next duel is what takes it away again.
                continue
            self._serve_main(match, member, result, now)
        self._add_event(
            match, result, f"Team {team.name} advances to Level {team.level}!", "advance"
        )

    # --- duels (the Duelist role) ---

    def _duelists(self, match: Match, team: Team) -> list[Player]:
        return [
            player
            for player in self._playing_members(match, team)
            if config.role_is_duel(player.role)
        ]

    def _duel_seats(
        self, match: Match
    ) -> list[tuple[str, Player, Team]] | None:
        """(side, Duelist, team) for each seat, or None if no duel is possible.

        Sides follow `config.TEAM_IDS` order, so a given team always occupies
        the same seat for the whole match.
        """
        seats: list[tuple[str, Player, Team]] = []
        for side, team in zip(SIDES, match.teams.values()):
            duelists = self._duelists(match, team)
            if len(duelists) != 1:
                return None
            seats.append((side, duelists[0], team))
        return seats

    def _duel_penalty_active(self, team: Team, now: datetime | None) -> bool:
        if team.duel_penalty_until is None:
            return False
        return _parse_iso(team.duel_penalty_until) > (now or utc_now())

    def _start_duel(
        self, match: Match, result: EngineResult, now: datetime | None = None
    ) -> None:
        """Seat both Duelists in a fresh duel and open the first round."""
        seats = self._duel_seats(match)
        if match.status != "active" or seats is None:
            return
        if any(team.finished for _, _, team in seats):
            return

        module = self.registry.pick_duel(_new_seed())
        duel = DuelSession(
            id=uuid4().hex[:8],
            module=module,
            state=module.new_duel(_new_seed()),
            sides={side: player.id for side, player, _ in seats},
            team_of={side: team.id for side, _, team in seats},
            phase="choosing",
        )
        match.duel = duel
        for _, player, _ in seats:
            # A duel takes green away from both champions: it has to be won
            # again, which is what makes a lost duel block a team.
            player.assigned_game = module.id
            player.status = "duelling"
            player.current_main = None
            player.current_bonus = None
            player.choice_pending = False
            player.timer_kind = None
            player.timer_deadline = None
            result.cancel.append(player.id)
        duel.deadline = self._start_scope_timer(
            match, DUEL_SCOPE, "duel_round", result, now,
            seconds=module.choice_seconds,
        )
        names = " vs ".join(player.name for _, player, _ in seats)
        self._add_event(match, result, f"Duel — {names} ({module.name}).", "info")

    def duel_choice(
        self,
        match: Match,
        player_id: str,
        duel_id: str,
        round_index: int,
        choice: str,
        now: datetime | None = None,
    ) -> EngineResult:
        """A Duelist commits a move for the open round.

        The move is recorded but never broadcast: the round resolves when both
        have committed, or when the window lapses. Choosing early therefore
        tells the opponent nothing beyond the fact that you chose.
        """
        if match.status != "active":
            return EngineResult.rejected("match is not active")
        duel = match.duel
        if duel is None or duel.id != duel_id:
            return EngineResult.rejected("no duel to answer")
        if duel.phase != "choosing":
            return EngineResult.rejected("the round is closed")
        if round_index != duel.state.round_index:
            return EngineResult.rejected("that round is over")
        side = duel.side_of(player_id)
        if side is None:
            return EngineResult.rejected("you aren't in this duel")
        if duel.state.locked(side):
            return EngineResult.rejected("you already chose this round")
        move = duel.module.normalize_choice(duel.state, choice)
        if move is None:
            return EngineResult.rejected("not a legal move")

        duel.state.choices[side] = move
        result = EngineResult(changed=True)
        if duel.state.both_locked():
            self._resolve_round(match, result, now)
        return result

    def _resolve_round(
        self, match: Match, result: EngineResult, now: datetime | None
    ) -> None:
        """Score the open round and either move on or end the duel."""
        duel = match.duel
        state = duel.state
        winner = duel.module.resolve_round(state)
        entry = {
            "round": state.round_index,
            "a": state.choices.get("a"),
            "b": state.choices.get("b"),
            "winner": winner,
        }
        state.history.append(entry)
        duel.last_round = entry
        if winner is not None:
            state.wins[winner] += 1
            if state.wins[winner] >= duel.module.wins_needed:
                duel.winner_side = winner
                self._finish_duel(match, result, now)
                return
        # Choices stay on the state through the reveal beat, then clear.
        duel.phase = "reveal"
        duel.deadline = self._start_scope_timer(
            match, DUEL_SCOPE, "duel_reveal", result, now
        )

    def _next_round(
        self, match: Match, result: EngineResult, now: datetime | None
    ) -> None:
        duel = match.duel
        duel.state.choices.clear()
        duel.state.round_index += 1
        duel.last_round = None
        duel.phase = "choosing"
        duel.deadline = self._start_scope_timer(
            match, DUEL_SCOPE, "duel_round", result, now,
            seconds=duel.module.choice_seconds,
        )

    def _finish_duel(
        self, match: Match, result: EngineResult, now: datetime | None
    ) -> None:
        """Pay the winner, lock the loser, and queue the next duel."""
        duel = match.duel
        duel.phase = "done"
        winner_side = duel.winner_side
        loser_side = other_side(winner_side)
        winner = match.players[duel.sides[winner_side]]
        loser = match.players[duel.sides[loser_side]]
        winner_team = match.teams[duel.team_of[winner_side]]
        loser_team = match.teams[duel.team_of[loser_side]]

        # The champion holds green until the next duel pulls them back in —
        # no wait timer, so `extend_wait` can't prolong a duel win either.
        winner.status = "cleared"
        winner.choice_pending = False
        winner.timer_kind = None
        winner.timer_deadline = None
        result.cancel.append(winner.id)
        loser.status = "duelling"

        winner_team.duel_streak += 1
        pay = min(
            match.config_snapshot["duel_win_currency"]
            * 2 ** (winner_team.duel_streak - 1),
            match.config_snapshot["duel_currency_cap"],
        )
        winner_team.currency += pay
        loser_team.duel_streak = 0

        # The time penalty bites once per level: losing twice at the same
        # level costs nothing extra beyond staying un-green.
        penalised = loser_team.duel_penalty_level != loser_team.level
        if penalised:
            loser_team.duel_penalty_level = loser_team.level
            loser_team.duel_penalty_until = self._start_scope_timer(
                match, _team_scope(loser_team.id), "duel_penalty", result, now
            )

        result.duel_result = {
            "duel_id": duel.id,
            "winner_team_id": winner_team.id,
            "loser_team_id": loser_team.id,
            "winner_name": winner.name,
            "loser_name": loser.name,
            "wins": dict(duel.state.wins),
            "streak": winner_team.duel_streak,
            "currency": pay,
            "penalty_until": loser_team.duel_penalty_until if penalised else None,
        }
        self._add_event(
            match,
            result,
            f"{winner.name} wins the duel for team {winner_team.name} (+{pay}).",
            "info",
        )
        match.duels_played += 1
        if match.duels_played >= match.config_snapshot["duels_per_level"]:
            # The series is over for this level: the main duel and its bonus are
            # spent, so no `duel_next` is queued and the champions stop duelling.
            # The loser goes green too — the once-per-level penalty stamped above
            # is their deficit. Leaving them un-green here would block their team
            # for good, because no later duel would come to restore it.
            duel.deadline = None
            result.cancel.append(DUEL_SCOPE)
            loser.status = "cleared"
            loser.choice_pending = False
            loser.timer_kind = None
            loser.timer_deadline = None
            result.cancel.append(loser.id)
            self._add_event(
                match,
                result,
                "The duel is done for this level — both champions stand down.",
                "info",
            )
        else:
            duel.deadline = self._start_scope_timer(
                match, DUEL_SCOPE, "duel_next", result, now
            )
        # Both champions may have just gone green, so either team may be complete.
        for team in (winner_team, loser_team):
            self._advance_check(match, team, result, now)

    def on_duel_timer(
        self,
        match: Match,
        scope_id: str,
        kind: str,
        now: datetime | None = None,
    ) -> EngineResult:
        """A duel-scoped deadline passed. Stale timers are no-ops."""
        result = EngineResult(changed=True)
        if kind == "duel_penalty":
            team = next(
                (t for t in match.teams.values() if _team_scope(t.id) == scope_id),
                None,
            )
            if team is None or team.duel_penalty_until is None:
                return EngineResult(changed=False)
            team.duel_penalty_until = None
            self._add_event(
                match, result, f"Team {team.name}'s duel penalty is over.", "info"
            )
            self._advance_check(match, team, result, now)
            return result

        duel = match.duel
        if duel is None or scope_id != DUEL_SCOPE or match.status != "active":
            return EngineResult(changed=False)
        if kind == "duel_round" and duel.phase == "choosing":
            self._resolve_round(match, result, now)
            return result
        if kind == "duel_reveal" and duel.phase == "reveal":
            self._next_round(match, result, now)
            return result
        if kind == "duel_next" and duel.phase == "done":
            self._start_duel(match, result, now)
            return result
        return EngineResult(changed=False)

    def _add_event(
        self, match: Match, result: EngineResult, message: str, kind: str
    ) -> None:
        event = Event(message=message, kind=kind)
        match.events.append(event)
        result.events.append(event)
