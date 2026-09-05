"""Link duels: two people, one link, one duel, outside any match.

The four duels were the only games nobody could try. A solo board is a REST
call and a seed, but a duel needs a live opponent, so `/explore` said outright
that the duels were not there and could not be. The only way to play one was to
assemble two full teams, seat two Grandmasters, field a mirrored Duelist on
each side, and start a match.

A room is the small version: pick a duel, get a link, send it to someone, play.
No teams, no currency, no levels, no Grandmasters, nothing recorded anywhere.

**A room is deliberately not a Match.** It would be tempting — a Match rides
the existing store, locks, broadcast and eviction for free — but `match.status`
is read in thirty-four places in the engine, and a room pretending to be a
match would give every one of those guards a second meaning to get right,
forever, in the file whose behaviour is most worth freezing. `Match.public`
would also run the team view, the roster, the Silence mask and the leader-only
event filter over state that has no teams. God mode made the same call for the
same reason: an `Observer` is kept out of `match.players` so it *cannot* reach
the match rules by accident. Physical separation is the enforcement.

What is shared, on purpose:

  * **The duel modules, untouched.** They never knew about matches. A room
    passes `side` the same way and gets the same `DuelState` back.
  * **`backend/duelloop.py`** — the scoring, so a duel plays identically here
    and in a race. What a room does *not* share is the scheduling, because a
    match reads a frozen config snapshot and a host's override and a room has
    neither.
  * **`DuelSession`**, verbatim, so the client gets a byte-identical view and
    all four renderers work here with no changes at all.

Two rules that shape the rest:

  * **A seat has no name.** Every renderer already falls back to "You" and
    "Opponent", so a link duel needs no name form, and no stranger who followed
    a link is shown a string somebody else typed.
  * **A disconnect forfeits, it does not pause.** A missing choice loses
    (DUEL_MODULE_SPEC §3) — pausing would let whoever is losing freeze the duel
    by pulling the plug.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from backend import config, duelloop
from backend.engine import EngineResult, TimerRequest
from backend.games.duel_base import SIDES, DuelModule
from backend.models import DuelSession, Player, utc_now

# A room runs one clock at a time, so one scope is enough. The kinds match the
# engine's so the timer glue in main.py reads the same on both sides.
ROUND_SCOPE = "duel"


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _stakes(module: DuelModule) -> dict[str, int] | None:
    """What a staked duel is fought with in a room: the same grant to each.

    None for the three unstaked duels, which is what their `new_duel` expects.
    Written against the `staked` flag rather than against BID WAR by name, so a
    second staked duel needs nothing here.
    """
    if not getattr(module, "staked", False):
        return None
    return {side: config.DUEL_ROOM_STAKE for side in SIDES}


def _seed() -> int:
    return secrets.randbelow(2**31)


@dataclass
class DuelRoom:
    """One link, up to two seats, and whatever duel is running between them.

    Note what is absent, because each absence is load-bearing: no status field
    (it is derived from the seats and the duel), no event log, no currency, no
    level, no config snapshot, no host, no round-window override. A room that
    grew any of those would be a Match with extra steps.
    """

    id: str  # 8 hex, and it is the share link
    duel_game_id: str
    # side -> the person sitting there. Two `Player`s carrying an id and an
    # empty name: the seat id is the socket's only credential, exactly like a
    # player id, and `connected` is what the duel-opens and rematch rules read.
    # `DuelSession.public` wants `me.id` and `players[id].name`; nothing here
    # touches any other field on them.
    seats: dict[str, Player] = field(default_factory=dict)
    duel: DuelSession | None = None  # None until both seats are here
    duels_played: int = 0  # finished duels; a rematch is the next one
    created_at: str = field(default_factory=utc_now)

    # --- who is who ------------------------------------------------------

    def side_of(self, seat_id: str | None) -> str | None:
        if seat_id is None:
            return None
        for side, seat in self.seats.items():
            if seat.id == seat_id:
                return side
        return None

    def seat(self, seat_id: str | None) -> Player | None:
        side = self.side_of(seat_id)
        return self.seats[side] if side else None

    def players(self) -> dict[str, Player]:
        """Keyed by seat id, the shape `DuelSession.public` reads names from."""
        return {seat.id: seat for seat in self.seats.values()}

    def full(self) -> bool:
        return len(self.seats) >= len(SIDES)

    def both_here(self) -> bool:
        """Both seats claimed *and* connected. A duel opens on this, not on the
        join: a five-second round would be half gone before the second socket
        finished opening."""
        return self.full() and all(seat.connected for seat in self.seats.values())

    def status(self) -> str:
        """Derived, never stored — one less thing that can disagree."""
        if self.duel is None:
            return "waiting"
        return "done" if self.duel.phase == "done" else "duelling"

    # --- the view --------------------------------------------------------

    def public(self, seat_id: str | None = None) -> dict[str, Any]:
        """What one viewer sees. `seat_id` None (or unknown) is a watcher.

        `duel` is the same DuelView a match sends, built by the same code, so
        every renderer works here unchanged. `round_seconds` is passed as None
        because a room has no host override: the module's own window is the
        real one rather than a fallback.
        """
        return {
            "id": self.id,
            "duel_game_id": self.duel_game_id,
            "status": self.status(),
            "you": self.side_of(seat_id),
            "seats_taken": len(self.seats),
            "connected": {
                side: seat.connected for side, seat in self.seats.items()
            },
            "duels_played": self.duels_played,
            "duel": (
                self.duel.public(self.seat(seat_id), self.players())
                if self.duel is not None
                else None
            ),
        }


class DuelRoomStore:
    """Rooms, by id. A second dict rather than a generic store.

    `InMemoryStateStore` is thirty lines and typed to Match; making it generic
    would be core-lane surgery for tidiness, and two small dicts cannot drift
    into each other the way one shared keyspace can.
    """

    def __init__(self) -> None:
        self._rooms: dict[str, DuelRoom] = {}

    async def add(self, room: DuelRoom) -> DuelRoom:
        self._rooms[room.id] = room
        return room

    async def get(self, room_id: str) -> DuelRoom | None:
        return self._rooms.get(room_id)

    async def all(self) -> list[DuelRoom]:
        return list(self._rooms.values())

    async def remove(self, room_id: str) -> None:
        self._rooms.pop(room_id, None)


# --- the rules -----------------------------------------------------------


def create_room(duel_game_id: str) -> DuelRoom:
    """A room with its creator already in seat "a"."""
    room = DuelRoom(id=uuid4().hex[:8], duel_game_id=duel_game_id)
    room.seats["a"] = _new_seat()
    return room


def _new_seat() -> Player:
    # `s_` for a seat, beside `p_` for a player and `g_` for a God seat: one
    # namespace, so an id always says what it is.
    return Player(id=f"s_{secrets.token_hex(8)}", name="")


def claim_seat(room: DuelRoom) -> Player | None:
    """Sit the next arrival down, or None when both seats are taken.

    A None is not an error: whoever it was may still watch.
    """
    for side in SIDES:
        if side not in room.seats:
            room.seats[side] = _new_seat()
            return room.seats[side]
    return None


def open_duel(
    room: DuelRoom, module: DuelModule, now: datetime | None = None
) -> EngineResult:
    """Deal the first round, once both seats are actually here."""
    if room.duel is not None or not room.both_here():
        return EngineResult(changed=False)
    return _deal(room, module, now)


def _deal(
    room: DuelRoom, module: DuelModule, now: datetime | None
) -> EngineResult:
    duel = DuelSession(
        id=uuid4().hex[:8],
        module=module,
        state=module.new_duel(_seed(), _stakes(module)),
        sides={side: seat.id for side, seat in room.seats.items()},
        team_of={},  # a room has no teams, and nothing in the client reads this
        phase="choosing",
    )
    room.duel = duel
    result = EngineResult(changed=True)
    duel.deadline = _start_clock(
        room, "duel_round", module.choice_seconds, result, now
    )
    return result


def _start_clock(
    room: DuelRoom,
    kind: str,
    seconds: int,
    result: EngineResult,
    now: datetime | None,
) -> str:
    # A TimerRequest deadline is a UTC ISO *string*, as the engine's own
    # `_start_scope_timer` builds it — TimerService parses it back.
    deadline = (_now(now) + timedelta(seconds=seconds)).isoformat()
    result.schedule.append(
        TimerRequest(scope_id=ROUND_SCOPE, kind=kind, deadline=deadline)
    )
    return deadline


def choose(
    room: DuelRoom,
    seat_id: str,
    duel_id: str,
    round_index: int,
    choice: str,
    now: datetime | None = None,
) -> EngineResult:
    """A seat commits a move for the open round."""
    if room.duel is None:
        return EngineResult.rejected("no duel to answer")
    both_locked, error = duelloop.apply_choice(
        room.duel, seat_id, duel_id, round_index, choice
    )
    if error is not None:
        return EngineResult.rejected(error)
    result = EngineResult(changed=True)
    if both_locked:
        _resolve(room, result, now)
    return result


def _resolve(room: DuelRoom, result: EngineResult, now: datetime | None) -> None:
    """Score the round. A decided duel simply stops.

    Where a match pays the winner, stamps a penalty on the loser and re-arms
    the series, a room has nobody to pay and nothing to advance. The duel ends
    and both people look at the result until one of them asks for another.
    """
    duel = room.duel
    if duelloop.score_round(duel):
        duel.phase = "done"
        duel.deadline = None
        result.cancel.append(ROUND_SCOPE)
        return
    duel.deadline = _start_clock(
        room, "duel_reveal", config.DUEL_ROOM_REVEAL_SECONDS, result, now
    )


def on_timer(
    room: DuelRoom, scope_id: str, kind: str, now: datetime | None = None
) -> EngineResult:
    """A round window or a reveal beat lapsed. Stale timers are no-ops."""
    duel = room.duel
    if duel is None or scope_id != ROUND_SCOPE:
        return EngineResult(changed=False)
    if kind == "duel_round" and duel.phase == "choosing":
        # Whoever did not answer loses the round: a missing choice must cost
        # something, or stalling is a strategy.
        result = EngineResult(changed=True)
        _resolve(room, result, now)
        return result
    if kind == "duel_reveal" and duel.phase == "reveal":
        result = EngineResult(changed=True)
        duelloop.open_next_round(duel)
        duel.deadline = _start_clock(
            room, "duel_round", duel.module.choice_seconds, result, now
        )
        return result
    return EngineResult(changed=False)


def rematch(
    room: DuelRoom, seat_id: str, now: datetime | None = None
) -> EngineResult:
    """Run it back: a fresh duel between the same two people, same room.

    A brand-new `DuelSession` with a new id, never a reset of the old one. The
    client keys its renderer mount on the duel id, so reusing it would hand the
    new duel to a renderer still holding the last one's DOM and state — Crown
    Duel's spent cards, Number Clash's used digits, Bid War's bid stepper.
    """
    if room.duel is None or room.duel.phase != "done":
        return EngineResult.rejected("the duel is still running")
    if room.side_of(seat_id) is None:
        return EngineResult.rejected("only the two players can start another")
    if not room.both_here():
        return EngineResult.rejected("waiting for the other player")
    module = room.duel.module
    room.duels_played += 1
    return _deal(room, module, now)


def on_connect(room: DuelRoom, seat_id: str) -> None:
    seat = room.seat(seat_id)
    if seat is not None:
        seat.connected = True


def on_disconnect(room: DuelRoom, seat_id: str) -> None:
    """Mark the seat away. The duel carries on without them, on purpose."""
    seat = room.seat(seat_id)
    if seat is not None:
        seat.connected = False
