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
from backend.games.duel_base import SIDES, DuelModule, DuelState, other_side

# How many events MatchPublic carries (the "last ~30" in the protocol doc).
PUBLIC_EVENT_LIMIT = 30

# Event kinds only leaders may see (who cleared / who lost cleared status).
LEADER_ONLY_EVENT_KINDS = ("green", "lost_green")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_future(deadline: str | None) -> bool:
    """True while a UTC ISO deadline hasn't passed yet.

    View-layer deadline checks read the wall clock: unlike the engine, `public()`
    is called at broadcast time and takes no injected `now`.
    """
    return deadline is not None and datetime.fromisoformat(deadline) > datetime.now(
        timezone.utc
    )


def green(player: Player) -> bool:
    """A player is green (cleared) when they've solved their level and hold it.

    A player in `bonus` is NOT green — taking the bonus resets cleared status
    until they solve it, which is what blocks the team from advancing past them.
    Nor is a player in `duelling`: a Duelist is green only while holding a duel
    win, so a lost duel blocks their team by exactly the same mechanism.
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
class Observer:
    """A God seat: a viewer that is not a player.

    Deliberately not a `Player`. Every rule in the engine that counts, seats,
    gates or advances people reads `match.players` and `team.player_ids`, so a
    viewer kept out of both costs no seat, blocks no start, and cannot appear on
    a roster by accident. That is the whole reason God mode is a small change.

    It holds no rejoin code — `engine.rejoin` walks the players looking for one,
    and a God seat that could be bought for six characters would not be a dev
    tool. It has no `connected` flag either: nothing about a God may ever reach
    another viewer's snapshot, so there is nothing here worth broadcasting.
    """

    id: str  # "g_"-prefixed, and the WS credential, exactly like a player id
    name: str = "God"


@dataclass
class Player:
    id: str  # long + random — it is the WS credential
    name: str
    # Short, readable, and a credential all the same: it buys back `id`, so it
    # reaches only this player (`private`) and their own Grandmaster
    # (`Team.public(reveal_codes=True)`). Never broadcast.
    rejoin_code: str = ""
    team_id: str | None = None  # None while unassigned in the lobby
    # "lobby" | "solving" | "cleared" | "bonus" | "duelling" | "leading" | "finished"
    status: str = "lobby"
    connected: bool = False
    is_leader: bool = False
    role: str | None = None  # config.ROLES id given by the Grandmaster
    assigned_game: str | None = None  # game id chosen by the team leader
    attempt: int = 0  # main-puzzle instances served this level
    current_main: PuzzleInstance | None = None
    current_bonus: PuzzleInstance | None = None
    choice_pending: bool = False  # cleared and still owes a wait-or-bonus choice
    timer_deadline: str | None = None  # UTC ISO; drives the client countdown
    timer_kind: str | None = None  # "wait" | None
    # UTC ISO; set only while solving a board whose game caps it
    # (`payload["time_limit_seconds"]`). Its own deadline, on its own timer
    # scope, so it never displaces the wait countdown.
    puzzle_deadline: str | None = None
    frozen_until: str | None = None  # UTC ISO; submits rejected until then
    # Cosmetic sabotage: config.SCREEN_EFFECTS id -> UTC ISO deadline. Bounded
    # by the catalogue (an id overwrites its own entry) and never needs a timer
    # — a lapsed deadline simply stops being sent.
    screen_effects: dict[str, str] = field(default_factory=dict)
    earned_level: int = 0  # highest level base currency was paid for
    bonus_streak: int = 0  # successful bonuses this level (first pays more)
    bonus_earned: int = 0  # this level's bonus pay — forfeited on bonus failure
    # Net currency this player has put into the team purse: clears, bonuses and
    # duel wins, less what a failed bonus takes back out. It mirrors the team
    # ledger, so it can never claim credit for coins the team does not have.
    coins_earned: int = 0

    def current_puzzle(self) -> PuzzleInstance | None:
        """The puzzle the player should act on right now."""
        if self.status == "solving":
            return self.current_main
        if self.status == "bonus":
            return self.current_bonus
        return None

    def deadline_is_hidden(self) -> bool:
        """True when this board's deadline belongs to the team's Grandmaster
        rather than to the player working it (GAME_MODULE_SPEC §6).

        A visibility rule and nothing more: the deadline is the same instant
        either way, and exactly one of the two seats is ever sent it.
        """
        puzzle = self.current_puzzle()
        return bool(
            puzzle is not None
            and self.puzzle_deadline is not None
            and puzzle.payload.get("hidden_deadline")
        )

    def live_effects(self) -> dict[str, str]:
        """Screen effects still running. A lapsed one needs no cleanup — it just
        stops appearing in the view, so reconnects and level changes are free."""
        return {
            effect: deadline
            for effect, deadline in self.screen_effects.items()
            if is_future(deadline)
        }

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "team_id": self.team_id,
            "status": self.status,
            "green": green(self),
            "connected": self.connected,
            "is_leader": self.is_leader,
            "role": self.role,
            "assigned_game": self.assigned_game,
            "coins_earned": self.coins_earned,
            # Whether a game is assigned, separately from which one. The lobby
            # masks the opposing loadout but both sides still have to see that
            # the other team is ready, or the start blocker reads as a bug.
            "has_game": self.assigned_game is not None,
        }

    def private(self) -> dict[str, Any]:
        """PlayerPrivate: PlayerPublic plus the puzzle this player may see."""
        puzzle = self.current_puzzle()
        view = puzzle.public() if puzzle else None
        # Dark fuse: the clock goes to the Grandmaster instead (see
        # `Team.public`), so this seat is sent neither copy of it.
        mine = None if self.deadline_is_hidden() else self.puzzle_deadline
        if view is not None and mine is not None:
            # Also inside the puzzle, because a renderer that draws a clock of
            # its own already looks there and takes no other argument. One
            # source, two placements — they cannot disagree.
            view["deadline"] = mine
        return {
            **self.public(),
            # Your own seat, and only ever your own: this is what buys `id` back
            # after a browser is lost, so it rides the personalised `me` block.
            "rejoin_code": self.rejoin_code,
            "current_puzzle": view,
            "timer_kind": self.timer_kind,
            "timer_deadline": self.timer_deadline,
            "puzzle_deadline": mine,
            "choice_pending": self.choice_pending,
            "frozen_until": self.frozen_until,
            # Only the victim is told they're being sabotaged: fog of war means
            # the buyer never learns which opponent the server picked.
            "screen_effects": self.live_effects(),
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
    reflect_active: bool = False  # bounces the next attack back at its buyer
    insurance_active: bool = False  # the next failed bonus keeps its earnings
    silenced_until: str | None = None  # UTC ISO; this team's own Grandmaster is blind
    leader_id: str | None = None
    handoff_used_level: int = 0  # last level a mid-match leader handoff happened
    duel_streak: int = 0  # consecutive duel wins; drives the doubling payout
    duel_penalty_until: str | None = None  # UTC ISO; advance is locked until then
    duel_penalty_level: int = 0  # level the live penalty was stamped at (once each)

    def public(
        self, players: dict[str, Player], silenced: bool = False,
        hide_games: bool = False, reveal_codes: bool = False,
    ) -> dict[str, Any]:
        """Full view: own team for its leader, and everyone in the lobby.

        `hide_games` is the lobby's cross-team mask: the opposing roster and its
        roles stay visible so the sides can be balanced before the start, but
        which game each of them will actually play does not — that is the
        loadout, and scouting it before the race is not part of the game.

        `reveal_codes` adds each member's rejoin code, so a Grandmaster can read
        one back to a player who lost their browser. Off everywhere else: the
        code buys a seat, so the lobby view and the finished-match view (both of
        which every player receives) must never carry it.

        Under `silenced` (the Silence perk) the progress read-out is masked —
        `green_count` and every playing member's status go null. The shape is
        unchanged so the client can render "?" rather than break. Note the
        *enemy* leader keeps their `include_green` summary of this team: Silence
        blinds a Grandmaster to their own roster, which is the whole joke.
        """
        members = [players[player_id] for player_id in self.player_ids]
        roster = [member.public() for member in members]
        for member, view in zip(members, roster):
            # The other half of the dark-fuse rule: a board that withheld its
            # deadline from the player sends it here, to the one seat that can
            # read it out. Null on every ordinary board — the player has it.
            view["board_deadline"] = (
                member.puzzle_deadline if member.deadline_is_hidden() else None
            )
        if reveal_codes:
            for member, view in zip(members, roster):
                view["rejoin_code"] = member.rejoin_code
        if hide_games:
            for view in roster:
                view["assigned_game"] = None
        if silenced:
            for view in roster:
                if not view["is_leader"]:
                    view["green"] = None
                    view["status"] = "hidden"
                    # Earnings track clears, so leaving them visible would say
                    # who had cleared and undo the blinding.
                    view["coins_earned"] = None
                    # Silence takes the clock with everything else, or a
                    # silenced Grandmaster would still be useful.
                    view["board_deadline"] = None
        return {
            "id": self.id,
            "name": self.name,
            "level": self.level,
            "roster_size": self.roster_size,
            "finished": self.finished,
            "green_count": None if silenced else sum(
                1 for member in members if green(member)
            ),
            "currency": self.currency,
            "shield_active": self.shield_active,
            "reflect_active": self.reflect_active,
            "insurance_active": self.insurance_active,
            "silenced_until": self.silenced_until,
            "leader_id": self.leader_id,
            "duel_streak": self.duel_streak,
            "duel_penalty_until": self.duel_penalty_until,
            "players": roster,
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
            # Not progress info: a team held by a duel penalty must be able to
            # see *why* it is stuck, or the lock reads as a bug.
            "duel_penalty_until": self.duel_penalty_until,
        }
        if include_green:
            members = [players[player_id] for player_id in self.player_ids]
            view["green_count"] = sum(1 for member in members if green(member))
        return view


@dataclass
class DuelSession:
    """One duel in progress between the two teams' Duelists.

    Match-level state, not player-level: a single object both Duelists act on.
    `state.choices` is server-only while the round is open — the reveal rule is
    enforced by the module's `public()` (see duel_base.base_public), never here.
    """

    id: str
    module: DuelModule  # the singleton the server picked; not serialised
    state: DuelState
    sides: dict[str, str] = field(default_factory=dict)   # "a"/"b" -> player id
    team_of: dict[str, str] = field(default_factory=dict)  # "a"/"b" -> team id
    phase: str = "choosing"  # "choosing" | "reveal" | "done"
    deadline: str | None = None  # UTC ISO for the current phase
    last_round: dict[str, Any] | None = None  # the round that just resolved
    winner_side: str | None = None  # set once the duel is decided

    def side_of(self, player_id: str) -> str | None:
        for side, seat_player_id in self.sides.items():
            if seat_player_id == player_id:
                return side
        return None

    def player_ids(self) -> list[str]:
        return [self.sides[side] for side in SIDES if side in self.sides]

    def revealed(self) -> bool:
        """Choices become public the instant the round stops being open."""
        return self.phase != "choosing"

    def winner_team_id(self) -> str | None:
        if self.winner_side is None:
            return None
        return self.team_of.get(self.winner_side)

    def loser_team_id(self) -> str | None:
        if self.winner_side is None:
            return None
        return self.team_of.get(other_side(self.winner_side))

    def public(
        self,
        me: Player | None,
        players: dict[str, Player],
        round_seconds: int | None = None,
    ) -> dict[str, Any]:
        """The duel as `me` is allowed to see it.

        Names only, never player ids: an id is a WS credential, and the
        opponent's is not exposed anywhere else in the protocol either.

        `me` is None for a viewer holding no seat in this duel at all (a God).
        That is already the case the modules handle for a Grandmaster, whose id
        is not in `sides` either, so it needs no new branch below.

        `round_seconds` is the window this match actually runs rounds at — the
        host's override, or the module's own. The client draws its countdown
        from it, so it has to be the effective value rather than the module
        default sitting in `payload`.
        """
        side = self.side_of(me.id) if me is not None else None
        view = self.module.public(self.state, side, self.revealed())
        view.update({
            "id": self.id,
            "name": self.module.name,
            "phase": self.phase,
            "deadline": self.deadline,
            "round_seconds": round_seconds or self.module.choice_seconds,
            "last_round": dict(self.last_round) if self.last_round else None,
            "winner_side": self.winner_side,
            "team_of": dict(self.team_of),
            "duellists": {
                side: players[player_id].name
                for side, player_id in self.sides.items()
                if player_id in players
            },
        })
        return view


@dataclass
class PendingStake:
    """A staked duel (BID WAR) waiting on both Grandmasters.

    It sits on the Match rather than inside `DuelSession` on purpose: until
    both grants are in, the duel has no `DuelState` at all, because the module
    cannot open the sale without knowing what each seat can bid. This is the
    gap between "we know who is fighting and with what game" and "the duel
    exists", and nothing else in the engine has to know about that gap.
    """

    duel_game_id: str
    sides: dict[str, str] = field(default_factory=dict)     # side -> player id
    team_of: dict[str, str] = field(default_factory=dict)   # side -> team id
    asks: dict[str, int] = field(default_factory=dict)      # side -> requested
    grants: dict[str, int] = field(default_factory=dict)    # side -> granted
    deadline: str | None = None  # UTC ISO; lapsing auto-grants the default

    def settled(self) -> bool:
        """Both Grandmasters have answered, one way or the other."""
        return all(side in self.grants for side in SIDES)

    def side_of(self, player_id: str) -> str | None:
        for side, seat_player_id in self.sides.items():
            if seat_player_id == player_id:
                return side
        return None

    def public(self, me: Player, players: dict[str, Player]) -> dict[str, Any]:
        """What `me` may see of the negotiation.

        A Duelist sees their own ask and their own grant. A Grandmaster sees
        their champion's ask so they can answer it. Neither side learns what
        the *other* team staked: an opponent's purse is the one thing worth
        knowing before the first bid, and it stays hidden until the duel opens
        and the module's own payload publishes it.
        """
        mine = self.side_of(me.id)
        if mine is None and me.is_leader:
            mine = next(
                (
                    side
                    for side, team_id in self.team_of.items()
                    if team_id == me.team_id
                ),
                None,
            )
        return {
            "duel_game_id": self.duel_game_id,
            "deadline": self.deadline,
            "side": mine,
            "duellists": {
                side: players[player_id].name
                for side, player_id in self.sides.items()
                if player_id in players
            },
            "ask": self.asks.get(mine) if mine else None,
            "granted": self.grants.get(mine) if mine else None,
            "settled": mine in self.grants if mine else False,
        }

    def god_public(self, players: dict[str, Player]) -> dict[str, Any]:
        """Both sides of the negotiation, for a God.

        Its own method rather than a flag on `public()`: that one takes a
        `Player` and derives exactly one side from it, so a flag would leave
        `me` unused on half the branches — a signature that lies about what it
        needs. This is a different audience, so it is a different method.

        Every key `public()` emits is still here, so the client can read one
        shape either way; `side` is null because a God is on neither.
        """
        return {
            "duel_game_id": self.duel_game_id,
            "deadline": self.deadline,
            "side": None,
            "duellists": {
                side: players[player_id].name
                for side, player_id in self.sides.items()
                if player_id in players
            },
            "ask": None,
            "granted": None,
            "settled": self.settled(),
            # The God-only half: who is on which side, and what each of them
            # asked for and got. No seat at the table ever sees both.
            "team_of": dict(self.team_of),
            "asks": dict(self.asks),
            "grants": dict(self.grants),
        }


@dataclass
class Match:
    id: str
    status: str = "lobby"  # "lobby" | "active" | "finished"
    teams: dict[str, Team] = field(default_factory=dict)
    players: dict[str, Player] = field(default_factory=dict)
    host_player_id: str | None = None  # first joiner; lobby control (see docs)
    min_players: int = 0  # per-match start threshold, host-adjustable in lobby
    max_players: int = 0  # per-team seat cap, host-adjustable within the ceiling
    level_count: int = 0  # rounds to win, host-adjustable in the lobby
    # Host override for the duel round window, in seconds. None means every
    # duel game keeps the `choice_seconds` it declares for itself.
    duel_round_seconds: int | None = None
    ended_reason: str | None = None  # "host_ended" | "host_cancelled" | None
    winner_team_id: str | None = None
    events: list[Event] = field(default_factory=list)
    config_snapshot: dict[str, Any] = field(default_factory=dict)  # frozen at start
    duel: DuelSession | None = None  # the live cross-team duel, if any
    duels_played: int = 0  # duels finished in the current level's series
    # A staked duel that both Grandmasters still owe an answer on. Never
    # set at the same time as `duel`: one replaces the other.
    pending_stake: PendingStake | None = None
    # Dev-only God seats (backend/god.py). Kept out of `players` on purpose;
    # see the Observer docstring for why that is what keeps the feature small.
    observers: dict[str, Observer] = field(default_factory=dict)

    def unassigned(self) -> list[Player]:
        """Lobby players who haven't picked (or been given) a team yet."""
        return [p for p in self.players.values() if p.team_id is None]

    def _duel_view(
        self, me: Player | None, god: Observer | None = None
    ) -> dict[str, Any] | None:
        """The duel reaches only the two Duelists and the two Grandmasters.

        A deliberate, minimal exception to the leader-exclusive visibility rule
        (REDESIGN_PLAN locked decision #9): a Duelist must see who they are
        fighting. Ordinary solvers still learn nothing about the other team.

        A God watches it the way a Grandmaster does — as a non-combatant, so
        `side_of` is None and neither choice is revealed early. Watching the
        duel is not the same as seeing through it, and no duel module has to
        learn a new audience for this.
        """
        if self.duel is None:
            return None
        if god is not None:
            return self.duel.public(None, self.players, self.duel_window())
        if me is None:
            return None
        if not (me.is_leader or me.id in self.duel.sides.values()):
            return None
        return self.duel.public(me, self.players, self.duel_window())

    def _stake_view(
        self, me: Player | None, god: Observer | None = None
    ) -> dict[str, Any] | None:
        """The stake negotiation, for the four seats it concerns.

        Same audience rule as `_duel_view`: the two Duelists and the two
        Grandmasters. An ordinary solver never learns a duel is being funded.
        A God sees both sides of it, which no seat at the table ever does.
        """
        if self.pending_stake is None:
            return None
        if god is not None:
            return self.pending_stake.god_public(self.players)
        if me is None:
            return None
        if not (me.is_leader or me.id in self.pending_stake.sides.values()):
            return None
        return self.pending_stake.public(me, self.players)

    def duel_window(self) -> int | None:
        """The round window in force: the host's override once the match has
        started (frozen with the rest of the config), or their lobby setting
        before it. None leaves each duel game on its own `choice_seconds`."""
        if self.config_snapshot:
            return self.config_snapshot.get("duel_round_seconds")
        return self.duel_round_seconds

    def _team_view(
        self, team: Team, me: Player | None, god: Observer | None = None
    ) -> dict[str, Any]:
        # A God sees both teams whole, in every status. First, before the lobby
        # arm below: that arm asks whether the team is *mine*, and a God has no
        # team, so falling into it would mask both loadouts from the one viewer
        # who is meant to see both. Unsilenced too — Silence is an attack on a
        # Grandmaster's own read-out, not on someone watching from outside it.
        if god is not None:
            return team.public(self.players, reveal_codes=True)
        if self.status == "lobby":
            # Everyone sees both rosters and both sets of roles in the lobby —
            # that is how you tell whether the sides are fair. Only the game
            # each opponent is being handed is masked.
            mine = me is not None and team.id == me.team_id
            return team.public(self.players, hide_games=not mine)
        # The race is over. Fog of war exists so neither side can scout the
        # other while it still matters; once the match is finished there is
        # nothing left to protect, and a result screen that could not name what
        # the teams actually did would be hiding the game from the people who
        # just played it. Silence cannot outlive the match either — it is an
        # attack on a live Grandmaster, not on the scoreboard.
        if self.status == "finished":
            return team.public(self.players)
        if me is None:
            return team.summary(self.players)
        if me.is_leader:
            if team.id == me.team_id:
                # The one view that carries rejoin codes: a Grandmaster is the
                # person a stranded player asks for theirs.
                return team.public(
                    self.players,
                    silenced=is_future(team.silenced_until),
                    reveal_codes=True,
                )
            return team.summary(self.players, include_green=True)
        if team.id == me.team_id:
            return team.summary(self.players)
        return {"id": team.id, "name": team.name, "finished": team.finished}

    def public(self, viewer_id: str | None = None) -> dict[str, Any]:
        """MatchPublic; `me` is filled only for the requesting player.

        `viewer_id` is a player id or a God's observer id — the ids are
        prefix-namespaced (`p_` / `g_`) and the socket carries either in the
        same slot. A God gets `me: None` and the `god` key instead.
        """
        god = self.observers.get(viewer_id or "")
        me = self.players.get(viewer_id) if viewer_id is not None else None
        if god is not None:
            me = None
        events = self.events[-PUBLIC_EVENT_LIMIT:]
        # A silenced Grandmaster loses the who-cleared feed too, or the masked
        # roster above would be trivially reconstructed from the event log.
        my_team = self.teams.get(me.team_id or "") if me else None
        sees_progress = (
            self.status == "finished"
            or god is not None
            or (
                me is not None
                and me.is_leader
                and not (my_team is not None and is_future(my_team.silenced_until))
            )
        )
        if self.status != "lobby" and not sees_progress:
            events = [e for e in events if e.kind not in LEADER_ONLY_EVENT_KINDS]
        return {
            "id": self.id,
            "status": self.status,
            "host_player_id": self.host_player_id,
            "min_players": self.min_players,
            "max_players": self.max_players,
            "level_count": self.level_count,
            # None means "each duel game keeps its own window" — the host panel
            # shows that as a choice, not as a missing value.
            "duel_round_seconds": self.duel_round_seconds,
            "ended_reason": self.ended_reason,
            "winner_team_id": self.winner_team_id,
            "config": dict(self.config_snapshot),
            "teams": {
                team_id: self._team_view(team, me, god)
                for team_id, team in self.teams.items()
            },
            "unassigned": [player.public() for player in self.unassigned()],
            "events": [event.public() for event in events],
            "duel": self._duel_view(me, god),
            "pending_stake": self._stake_view(me, god),
            "me": me.private() if me else None,
            # Null for everyone but a God. Always present rather than appearing
            # only on the God's own snapshot: a key that comes and goes is the
            # kind of thing the shape tests exist to catch, and null says
            # nothing about whether anyone is watching.
            "god": {"id": god.id, "name": god.name} if god else None,
        }
