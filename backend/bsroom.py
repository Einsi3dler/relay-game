"""BULLSHIT QUIZ: one room, one non-playing host, up to N rounds.

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
    waiting   too few players online to deal the next round; the host retries
    final     no playable question left: the standings and the winner(s)

The host presses every step. There are no timers.

**Leaving never stalls the game** (the owner's rule, docs/BULLSHIT_QUIZ.md
§0). Once a game has started, a player who goes offline keeps their seat and
their score but is *relegated*: a guesser for the rest of the game, never
picked to bluff again, even after they come back. Concretely:

  * Nobody waits on an offline guesser. If they come back mid-round they can
    still guess; if not, they simply cast no vote and spend no wager.
  * A question whose author is offline when its turn comes is skipped and
    stays queued. If they come back it can still be played. When every
    question left belongs to somebody offline, those are dropped for good and
    the game ends.
  * In the briefing, before the discussion opens, a speaker who leaves is
    swapped out: a bullshitter's option goes to another player who may bluff
    (or is removed, if nobody can take it), and an author's question goes
    back in the queue. Any wager locked in that round is refunded. Once the
    discussion is open the round carries on and commitments stand.

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
    commitment in disguise. The only refund is a round that never happened.
"""

from __future__ import annotations

import random
import secrets
from dataclasses import dataclass, field
from typing import Any

from backend import bsquiz, config
from backend.bsquiz import BULLSHITTER, GUESSER, TRUTHFUL
from backend.bsroster import Player

PHASES = ("lobby", "briefing", "open", "votes", "reveal", "waiting", "final")
IN_ROUND = ("briefing", "open", "votes", "reveal")


@dataclass
class Seat:
    player: Player
    connected: bool = False
    relegated: bool = False               # went offline mid-game: guesser from now on
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

    @property
    def may_bluff(self) -> bool:
        return self.connected and not self.relegated

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
    n: int = 0                                         # fixed at start
    pending: list[str] = field(default_factory=list)   # authors still to play, in order
    played: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)   # authors who never came back
    current: str | None = None                         # author of the question up
    questions: dict[str, bsquiz.Question] = field(default_factory=dict)
    panel: bsquiz.Panel | None = None
    result: bsquiz.RoundResult | None = None
    host_connected: bool = False
    rng: random.Random = field(default_factory=lambda: random.Random(secrets.randbits(64)))

    # --- queries ---------------------------------------------------------

    def question(self) -> bsquiz.Question | None:
        return self.questions.get(self.current) if self.current else None

    def role_of(self, player_id: str) -> str | None:
        return self.panel.role_of(player_id) if self.panel else None

    def speakers(self) -> list[Seat]:
        return [self.seats[p] for p in self.panel.speakers] if self.panel else []

    def guessers(self) -> list[Seat]:
        if not self.panel:
            return []
        return [s for s in self.seats.values() if self.panel.role_of(s.id) == GUESSER]

    def awaited_guessers(self) -> list[Seat]:
        """Who the lock waits on: online guessers. An offline one is never
        waited for; if they committed before leaving, their vote still counts."""
        return [s for s in self.guessers() if s.connected]

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
        return sum(1 for s in self.awaited_guessers() if self.committed(s))

    def round_number(self) -> int:
        return len(self.played) + (1 if self.phase in IN_ROUND else 0)

    def total_rounds(self) -> int:
        """N, less any question dropped because its author never came back."""
        return self.n - len(self.dropped)

    def standings(self) -> list[Seat]:
        return sorted(self.seats.values(), key=lambda s: (-s.total, s.name.lower()))

    # --- the view --------------------------------------------------------

    def public(self, viewer_id: str | None, is_host: bool = False) -> dict[str, Any]:
        """One personalised snapshot. See the module docstring before adding
        anything to it."""
        me = self.seats.get(viewer_id or "")
        live_round = self.phase in ("briefing", "open", "votes")
        speaking = set(self.panel.speakers) if self.panel and self.phase in IN_ROUND else set()

        cards = []
        for seat in self.seats.values():
            card = seat.player.card()
            card.update({"connected": seat.connected, "total": seat.total,
                         "relegated": seat.relegated})
            if self.phase == "lobby":
                card["submitted"] = seat.player.has_question
            elif live_round:
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
            "round": self.round_number(),
            "rounds": self.total_rounds(),
            "wager_max": max(0, self.n - 1),
        }

        if self.phase == "lobby":
            view["submitted"] = sum(1 for s in self.seats.values() if s.player.has_question)
            view["registered"] = len(self.seats)

        q = self.question()
        if q is not None and self.panel and self.phase in IN_ROUND:
            view["question"] = {
                "prompt": q.prompt,
                "options": [{"key": o.key, "text": o.text, "speaker": o.speaker}
                            for o in self.panel.options],
            }
            view["speakers_in"] = self.speakers_in()
            view["speakers"] = len(self.panel.speakers)
            view["guessers_in"] = self.guessers_in()
            view["guessers"] = len(self.awaited_guessers())
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
            view["relegated"] = me.relegated
            role = self.role_of(me.id) if self.phase in IN_ROUND else None
            if role is not None:
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

        if self.phase == "final":
            totals = {s.id: s.total for s in self.seats.values()}
            view["standings"] = [{"id": s.id, "name": s.name, "total": s.total}
                                 for s in self.standings()]
            view["winners"] = bsquiz.winners(totals)
            view["dropped"] = len(self.dropped)
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
    """Back online. Relegation stands: they guess for the rest of the game.
    Their queued question, if any, becomes playable again."""
    seat = room.seats.get(player_id)
    if seat is not None:
        seat.connected = True


def on_disconnect(room: BullshitRoom, player_id: str) -> None:
    seat = room.seats.get(player_id)
    if seat is None:
        return
    seat.connected = False
    if room.phase == "lobby":
        return
    seat.relegated = True
    if room.phase == "briefing" and room.panel and seat.id in room.panel.speakers:
        if seat.id == room.panel.author:
            _defer_current(room)
        else:
            _replace_bullshitter(room, seat)


def can_submit(room: BullshitRoom) -> None:
    if room.phase != "lobby":
        raise Rejected("The game has started. Questions are locked.")


def start(room: BullshitRoom, questions: dict[str, bsquiz.Question]) -> None:
    """Freeze the roster, fix N and the order, and deal the first round.

    Anybody seated but offline at this moment starts relegated, and their
    question waits for them like any other absent author's.
    """
    if room.phase != "lobby":
        raise Rejected("The game has already started.")
    if len(room.seats) < config.BS_MIN_PLAYERS:
        raise Rejected(f"Wait for at least {config.BS_MIN_PLAYERS} players to sign in.")
    missing = [s.name for s in room.seats.values() if s.id not in questions]
    if missing:
        raise Rejected("Still waiting on a question from " + ", ".join(sorted(missing)) + ".")
    if sum(1 for s in room.seats.values() if s.connected) < config.BS_MIN_PLAYERS:
        raise Rejected(f"At least {config.BS_MIN_PLAYERS} players need to be online to start.")

    room.n = len(room.seats)
    room.questions = {pid: questions[pid] for pid in room.seats}
    room.pending = bsquiz.schedule(list(room.seats), room.rng)
    room.played = []
    room.dropped = []
    for seat in room.seats.values():
        seat.total = 0
        seat.used = set()
        seat.relegated = not seat.connected
        seat.clear_round()
    _deal_next(room)


# --- dealing --------------------------------------------------------------

def _dealable_size(room: BullshitRoom, author: str) -> int:
    q = room.questions[author]
    candidates = [s for s in room.seats.values() if s.id != author and s.may_bluff]
    online_others = sum(1 for s in room.seats.values() if s.id != author and s.connected)
    return bsquiz.panel_size(len(q.decoys), len(candidates), online_others)


def _deal_next(room: BullshitRoom) -> None:
    """Deal the first queued question whose author is online.

    If every queued author is offline, the game is over and those questions
    are dropped. If some are online but too few players are, the room waits
    for the host to try again.
    """
    for seat in room.seats.values():
        seat.clear_round()
    room.panel = None
    room.result = None
    room.current = None

    online_authors = [a for a in room.pending if room.seats[a].connected]
    if not online_authors:
        room.dropped.extend(room.pending)
        room.pending = []
        room.phase = "final"
        return

    for author in online_authors:
        b = _dealable_size(room, author)
        if b >= 1:
            candidates = [s.id for s in room.seats.values() if s.id != author and s.may_bluff]
            room.panel = bsquiz.deal_panel(room.questions[author], candidates, b, room.rng)
            room.pending.remove(author)
            room.current = author
            room.phase = "briefing"
            return
    room.phase = "waiting"


def _refund_round(room: BullshitRoom) -> None:
    """A round that will not happen hands back every wager locked in it."""
    for seat in room.seats.values():
        if seat.bluff is not None:
            seat.used.discard(seat.bluff)
        if seat.pick is not None:
            seat.used.discard(seat.pick[1])
        seat.clear_round()


def _defer_current(room: BullshitRoom) -> None:
    """The author left during the briefing: their question goes back to the
    front of the queue, where it waits for them, and the next one is dealt."""
    _refund_round(room)
    room.pending.insert(0, room.current)
    _deal_next(room)


def _replace_bullshitter(room: BullshitRoom, gone: Seat) -> None:
    """A bullshitter left during the briefing. Hand their option to somebody
    who may bluff; failing that, remove the option; failing that (it was the
    only one), the round cannot run and the question is deferred."""
    if gone.bluff is not None:
        gone.used.discard(gone.bluff)
    gone.clear_round()

    speakers = set(room.panel.speakers)
    takers = [s.id for s in room.seats.values() if s.may_bluff and s.id not in speakers]
    online_guessers = sum(1 for s in room.seats.values()
                          if s.connected and s.id not in speakers)
    entries = [(o.text, o.speaker, o.correct) for o in room.panel.options]

    if takers and online_guessers - 1 >= 1:
        taker = room.rng.choice(takers)
        entries = [(t, taker if who == gone.id else who, c) for t, who, c in entries]
    elif len(room.panel.bullshitters) > 1:
        entries = [e for e in entries if e[1] != gone.id]
    else:
        _defer_current(room)
        return
    room.panel = bsquiz.panel_from(room.panel.author, entries)


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
    """Close guessing and show the headcount, not yet the truth. Waits only
    on online guessers; an offline one who never guessed casts no vote."""
    if room.phase != "open":
        raise Rejected("There is no discussion to lock.")
    waiting = len(room.awaited_guessers()) - room.guessers_in()
    if waiting:
        raise Rejected(f"Waiting on {waiting} guesser{'s' if waiting != 1 else ''}.")
    guesses = {s.id: s.pick for s in room.guessers() if s.pick is not None}
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
    """Move on from a reveal, or retry dealing after a wait."""
    if room.phase == "reveal":
        room.played.append(room.current)
    elif room.phase != "waiting":
        raise Rejected("Reveal this round first.")
    _deal_next(room)
