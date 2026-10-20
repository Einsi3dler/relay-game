"""BULLSHIT QUIZ: one room, one non-playing host, N rounds.

A room is deliberately not a Match, for the reasons `backend/quizroom.py` and
`backend/duelroom.py` give: no teams, no levels, no currency, no clock. The
rules live in `backend/bsquiz.py`; this file is the state machine around them
and the one place that decides what each viewer may see.

The phases:

    lobby     players register and write their question; nothing is dealt
    briefing  a question is up and its panel dealt. Each speaker privately
              learns their role. Bullshitters lock a wager; the truthful
              player confirms "ready" in the same beat (see below)
    open      the discussion on the call. Guessers lock an answer and a wager
    votes     locked. The headcount per option is shown, the truth is not
    reveal    the truth, the roles and every score change, applied once
    final     after N rounds: the standings and the winner(s)

The host presses every step. There are no timers, and a step that needs
everyone's commitment waits for everyone (docs/BULLSHIT_QUIZ.md §11): no
guess or wager is ever invented for somebody who is away.

**`public()` is the security boundary.** Until a round's reveal, no viewer is
told which option is true, who wrote the question, or which speakers are
bullshitters, on any key, nested or otherwise. Each speaker learns only their
own role. The host gets no more than a guesser: they are likely sharing their
screen on the call.

Two rules here look like details and are not:

  * **The truthful player locks too.** In the briefing every speaker's tile
    lights up when they commit. If only bullshitters committed, the one
    speaker who never lit up would be the author. Guess Who shipped that bug
    first (docs/QUIZ_ROLL_CALL.md §2).
  * **A commitment is final, and spends its wager number at once.** Wagers
    are single-use, so a number taken back and reused would be a second
    commitment in disguise.
"""

from __future__ import annotations

import random
import secrets
from dataclasses import dataclass, field
from typing import Any

from backend import bsquiz, config
from backend.bsquiz import BULLSHITTER, GUESSER, TRUTHFUL
from backend.bsroster import Player

PHASES = ("lobby", "briefing", "open", "votes", "reveal", "final")


@dataclass
class Seat:
    player: Player
    connected: bool = False
    total: int = 0
    used: set[int] = field(default_factory=set)
    ready: bool = False                   # truthful: confirmed in the briefing
    bluff: int | None = None              # bullshitter: their wager
    pick: tuple[str, int] | None = None   # guesser: (option key, wager)

    @property
    def id(self) -> str:
        return self.player.id

    @property
    def name(self) -> str:
        return self.player.name

    def clear_round(self) -> None:
        self.ready = False
        self.bluff = None
        self.pick = None


class Rejected(Exception):
    """A move the rules do not allow. Carries what to tell the client."""


@dataclass
class BullshitRoom:
    id: str
    seats: dict[str, Seat] = field(default_factory=dict)
    phase: str = "lobby"
    n: int = 0                                     # fixed at start
    order: list[str] = field(default_factory=list)  # authors, play order
    index: int = -1
    questions: dict[str, bsquiz.Question] = field(default_factory=dict)
    panel: bsquiz.Panel | None = None
    result: bsquiz.RoundResult | None = None
    host_connected: bool = False
    rng: random.Random = field(default_factory=lambda: random.Random(secrets.randbits(64)))

    # --- queries ---------------------------------------------------------

    def question(self) -> bsquiz.Question | None:
        if not (0 <= self.index < len(self.order)):
            return None
        return self.questions[self.order[self.index]]

    def role_of(self, player_id: str) -> str | None:
        return self.panel.role_of(player_id) if self.panel else None

    def speakers(self) -> list[Seat]:
        return [self.seats[p] for p in self.panel.speakers] if self.panel else []

    def guessers(self) -> list[Seat]:
        if not self.panel:
            return []
        return [s for s in self.seats.values() if self.panel.role_of(s.id) == GUESSER]

    def committed(self, seat: Seat) -> bool:
        """Has this seat done what this round asks of them? Deliberately the
        same question for every speaker, truthful or not."""
        role = self.role_of(seat.id)
        if role == TRUTHFUL:
            return seat.ready
        if role == BULLSHITTER:
            return seat.bluff is not None
        if role == GUESSER:
            return seat.pick is not None
        return False

    def speakers_in(self) -> int:
        return sum(1 for s in self.speakers() if self.committed(s))

    def guessers_in(self) -> int:
        return sum(1 for s in self.guessers() if self.committed(s))

    def missing_questions(self) -> list[Seat]:
        return [s for s in self.seats.values() if not s.player.has_question]

    def standings(self) -> list[Seat]:
        return sorted(self.seats.values(), key=lambda s: (-s.total, s.name.lower()))

    # --- the view --------------------------------------------------------

    def public(self, viewer_id: str | None, is_host: bool = False) -> dict[str, Any]:
        """One personalised snapshot. See the module docstring before adding
        anything to it."""
        me = self.seats.get(viewer_id or "")
        settled = self.phase in ("reveal", "final")
        speaking = set(self.panel.speakers) if self.panel and self.phase != "lobby" else set()

        cards = []
        for seat in self.seats.values():
            card = seat.player.card()
            card.update({"connected": seat.connected, "total": seat.total})
            if self.phase == "lobby":
                card["submitted"] = seat.player.has_question
            elif self.phase in ("briefing", "open", "votes"):
                card["speaker"] = seat.id in speaking
                card["committed"] = self.committed(seat)
            if self.phase == "reveal" and self.result:
                line = self.result.lines.get(seat.id)
                card["line"] = _line(line) if line else None
            cards.append(card)

        view: dict[str, Any] = {
            "room_id": self.id,
            "phase": self.phase,
            "you": me.id if me else None,
            "is_host": is_host,
            "players": cards,
            "here": sum(1 for s in self.seats.values() if s.connected),
            "round": self.index + 1 if self.phase != "lobby" else 0,
            "rounds": self.n,
            "wager_max": max(0, self.n - 1),
        }

        if self.phase == "lobby":
            view["submitted"] = sum(1 for s in self.seats.values() if s.player.has_question)
            view["registered"] = len(self.seats)

        q = self.question()
        if q is not None and self.panel and self.phase in ("briefing", "open", "votes", "reveal"):
            view["question"] = {
                "prompt": q.prompt,
                "options": [{"key": o.key, "text": o.text, "speaker": o.speaker}
                            for o in self.panel.options],
            }
            view["speakers_in"] = self.speakers_in()
            view["speakers"] = len(self.panel.speakers)
            view["guessers_in"] = self.guessers_in()
            view["guessers"] = len(self.guessers())
            if self.phase in ("votes", "reveal") and self.result:
                view["counts"] = dict(self.result.counts)
            if self.phase == "reveal" and self.result:
                # Earned: the round is scored, and this is the point of it.
                view["truth"] = {
                    "key": self.panel.correct_key,
                    "author": self.panel.author,
                    "explanation": q.explanation,
                    "bullshitters": list(self.panel.bullshitters),
                    "winner": self.result.winner,
                }

        if me is not None:
            view["your_total"] = me.total
            view["used_wagers"] = sorted(me.used)
            role = self.role_of(me.id) if self.phase != "lobby" else None
            if role is not None and self.phase in ("briefing", "open", "votes", "reveal"):
                view["role"] = role
                if role in (TRUTHFUL, BULLSHITTER):
                    view["your_option"] = self.panel.option_of(me.id).key
                if role == TRUTHFUL and q is not None:
                    view["your_explanation"] = q.explanation
                view["committed"] = self.committed(me)
                view["your_wager"] = me.bluff if role == BULLSHITTER else (
                    me.pick[1] if me.pick else None)
                if role == GUESSER and me.pick:
                    view["your_pick"] = me.pick[0]

        if settled and self.phase == "final":
            totals = {s.id: s.total for s in self.seats.values()}
            view["standings"] = [{"id": s.id, "name": s.name, "total": s.total}
                                 for s in self.standings()]
            view["winners"] = bsquiz.winners(totals)
        return view


def _line(line: bsquiz.Line) -> dict[str, Any]:
    return {"role": line.role, "option": line.option, "wager": line.wager,
            "fooled": line.fooled, "won": line.won, "delta": line.delta}


# --- lifecycle ------------------------------------------------------------

def create_room(players: list[Player], rng: random.Random | None = None) -> BullshitRoom:
    room = BullshitRoom(id=secrets.token_hex(4))
    if rng is not None:
        room.rng = rng
    refresh(room, players)
    return room


def refresh(room: BullshitRoom, players: list[Player]) -> None:
    """Pick up registrations and new questions. Lobby only: once the game
    starts the roster and N are fixed (handoff §3)."""
    if room.phase != "lobby":
        return
    for person in players:
        if not person.registered:
            continue
        seat = room.seats.get(person.id)
        if seat is None:
            room.seats[person.id] = Seat(player=person)
        else:
            seat.player = person


def on_connect(room: BullshitRoom, player_id: str) -> None:
    seat = room.seats.get(player_id)
    if seat is not None:
        seat.connected = True


def on_disconnect(room: BullshitRoom, player_id: str) -> None:
    """Marked away. The round waits for them; nothing is invented."""
    seat = room.seats.get(player_id)
    if seat is not None:
        seat.connected = False


def can_submit(room: BullshitRoom) -> None:
    if room.phase != "lobby":
        raise Rejected("The game has started. Questions are locked.")


def start(room: BullshitRoom, questions: dict[str, bsquiz.Question]) -> None:
    """Freeze the roster, fix N and the order, and deal the first round."""
    if room.phase != "lobby":
        raise Rejected("The game has already started.")
    if len(room.seats) < config.BS_MIN_PLAYERS:
        raise Rejected(f"Wait for at least {config.BS_MIN_PLAYERS} players to sign in.")
    missing = [s.name for s in room.seats.values() if s.id not in questions]
    if missing:
        raise Rejected("Still waiting on a question from " + ", ".join(sorted(missing)) + ".")

    room.n = len(room.seats)
    room.questions = {pid: questions[pid] for pid in room.seats}
    room.order = bsquiz.schedule(list(room.seats), room.rng)
    for seat in room.seats.values():
        seat.total = 0
        seat.used = set()
        seat.clear_round()
    room.index = 0
    _deal(room)


def _deal(room: BullshitRoom) -> None:
    room.panel = bsquiz.deal_panel(room.question(), list(room.seats), room.rng)
    room.result = None
    for seat in room.seats.values():
        seat.clear_round()
    room.phase = "briefing"


# --- commitments ----------------------------------------------------------

def _seat(room: BullshitRoom, player_id: str) -> Seat:
    seat = room.seats.get(player_id)
    if seat is None:
        raise Rejected("You are not in this game.")
    return seat


def _spend(room: BullshitRoom, seat: Seat, wager: object) -> int:
    problem = bsquiz.wager_problem(room.n, wager, seat.used)
    if problem:
        raise Rejected(problem)
    seat.used.add(wager)            # type: ignore[arg-type]
    return wager                    # type: ignore[return-value]


def ready(room: BullshitRoom, player_id: str) -> None:
    """The truthful player's commitment in the briefing. No wager."""
    seat = _seat(room, player_id)
    if room.phase != "briefing":
        raise Rejected("Nothing to confirm right now.")
    if room.role_of(seat.id) != TRUTHFUL:
        raise Rejected("Only the truthful player confirms. Lock a wager instead.")
    if seat.ready:
        raise Rejected("You have already confirmed.")
    seat.ready = True


def bluff(room: BullshitRoom, player_id: str, wager: object) -> None:
    """A bullshitter's wager on winning the crowd, before the discussion."""
    seat = _seat(room, player_id)
    if room.phase != "briefing":
        raise Rejected("Bullshitters wager before the discussion opens.")
    if room.role_of(seat.id) != BULLSHITTER:
        raise Rejected("You are not bluffing this round.")
    if seat.bluff is not None:
        raise Rejected("Your wager is locked.")
    seat.bluff = _spend(room, seat, wager)


def guess(room: BullshitRoom, player_id: str, key: object, wager: object) -> None:
    """A guesser's answer and wager, during the discussion."""
    seat = _seat(room, player_id)
    if room.phase != "open":
        raise Rejected("Guesses open once the discussion starts.")
    if room.role_of(seat.id) != GUESSER:
        raise Rejected("Speakers do not guess.")
    if seat.pick is not None:
        raise Rejected("Your answer is locked.")
    if not isinstance(key, str) or room.panel.option(key) is None:
        raise Rejected("Pick one of the options.")
    seat.pick = (key, _spend(room, seat, wager))


# --- host steps -----------------------------------------------------------

def open_discussion(room: BullshitRoom) -> None:
    if room.phase != "briefing":
        raise Rejected("The discussion is not waiting to open.")
    waiting = len(room.speakers()) - room.speakers_in()
    if waiting:
        raise Rejected(f"Waiting on {waiting} speaker{'s' if waiting != 1 else ''}.")
    room.phase = "open"


def lock(room: BullshitRoom) -> None:
    """Close guessing and show the headcount, not yet the truth."""
    if room.phase != "open":
        raise Rejected("There is no discussion to lock.")
    waiting = len(room.guessers()) - room.guessers_in()
    if waiting:
        raise Rejected(f"Waiting on {waiting} guesser{'s' if waiting != 1 else ''}.")
    guesses = {s.id: s.pick for s in room.guessers()}
    bluffs = {s.id: s.bluff for s in room.speakers() if s.bluff is not None}
    room.result = bsquiz.score_round(room.n, room.panel, guesses, bluffs)
    room.phase = "votes"


def reveal(room: BullshitRoom) -> None:
    """Show the truth and apply every score change, exactly once: only this
    transition adds to totals, and it can only happen from `votes`."""
    if room.phase != "votes" or room.result is None:
        raise Rejected("Lock the votes first.")
    for player_id, line in room.result.lines.items():
        room.seats[player_id].total += line.delta
    room.phase = "reveal"


def next_round(room: BullshitRoom) -> None:
    if room.phase != "reveal":
        raise Rejected("Reveal this round first.")
    if room.index + 1 >= len(room.order):
        room.panel = None
        room.result = None
        for seat in room.seats.values():
            seat.clear_round()
        room.phase = "final"
        return
    room.index += 1
    _deal(room)
