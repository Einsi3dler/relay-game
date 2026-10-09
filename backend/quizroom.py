"""ROLL CALL: one room, one host, one question at a time.

A room is deliberately **not** a Match, for the reason `backend/duelroom.py`
gives at length: `match.status` is read in thirty-odd places in the engine, and
a room pretending to be a match would give every one of those guards a second
meaning to get right, forever. A quiz has no teams, no levels, no currency, no
Grandmaster seat and no clock, so it shares none of that machinery. It does not
even take a `TimerService`: nothing here expires.

The one thing this file has to get right is **§6 of the handoff**: a viewer is
never told the answer to a question that is still open. `public()` is that
boundary and it is the only place a `subject` may cross it. The host screen is
projected onto a wall, which makes it the *least* trusted viewer in the room,
not the most, so it is given no more than a player.

The rules themselves are a port of the prototype in `frontend/quiz.js`, which
was played by hand until they settled. Two of them look like details and are
not:

  * **The subject of a question locks a sentinel.** They cannot guess and they
    score nothing, but they must still lock, because a projector roster missing
    exactly one person names them.
  * **A lock is final.** This did not matter when every answer was worth the
    same; it does now that being early pays.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backend import config, rollcall
from backend.rollcall import Participant, Question

PHASES = ("lobby", "question", "reveal", "final", "closed")


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def speed_bonus(rank: int) -> int:
    """What being early is worth, decaying down the order of correct answers.

    Rank, not wall-clock: there is no clock in this game, a room with nothing
    at stake will happily take a minute over a question, and an absolute decay
    curve would zero everybody out. Order always produces a spread.
    """
    return round(config.QUIZ_SPEED_MAX * (config.QUIZ_SPEED_DECAY ** rank))


@dataclass
class Seat:
    """One registered person, and how their evening is going."""
    participant: Participant
    connected: bool = False
    score: int = 0
    correct: int = 0
    answer: str | None = None       # a participant id, or QUIZ_SAT_OUT
    answered_at: int | None = None  # ms from asked_at
    gain: int = 0
    bonus: int = 0
    fast_sum: int = 0
    fast_count: int = 0

    @property
    def id(self) -> str:
        return self.participant.id

    @property
    def name(self) -> str:
        return self.participant.display_name or ""

    def mean_lock(self) -> float | None:
        """Mean ms to lock over the rounds they got *right*.

        Counting wrong answers would hand "fastest finger" to whoever tapped a
        face at random the instant the prompt appeared, which is the opposite
        of what the award is for.
        """
        return self.fast_sum / self.fast_count if self.fast_count else None


@dataclass
class QuizRoom:
    id: str
    seats: dict[str, Seat] = field(default_factory=dict)
    questions: list[Question] = field(default_factory=list)
    index: int = -1
    phase: str = "lobby"
    asked_at: int = 0
    host_connected: bool = False

    # --- queries ---------------------------------------------------------

    def question(self) -> Question | None:
        if self.index < 0 or self.index >= len(self.questions):
            return None
        return self.questions[self.index]

    def present(self) -> list[Seat]:
        """Who the room is waiting on: everyone here, the subject included.

        Not "everyone registered": somebody who never turned up is not being
        waited on. Not "everyone except the subject" either, which was the
        original bug.
        """
        return [s for s in self.seats.values() if s.connected]

    def scorable(self) -> list[Seat]:
        """Who can take points this round. A scoring question only: anything
        *drawn* from this list leaks, because the difference between it and the
        room is the answer."""
        q = self.question()
        return [s for s in self.seats.values() if not q or s.id != q.subject]

    def answered_count(self) -> int:
        return sum(1 for s in self.present() if s.answer)

    def all_answered(self) -> bool:
        here = self.present()
        return bool(here) and all(s.answer for s in here)

    def standings(self) -> list[Seat]:
        def key(s: Seat):
            mean = s.mean_lock()
            return (-s.score, -s.correct,
                    mean if mean is not None else float("inf"),
                    s.name.lower())
        return sorted(self.seats.values(), key=key)

    def awards(self) -> dict[str, Any]:
        ranked = self.standings()
        winner = ranked[0] if ranked and ranked[0].score > 0 else None
        fastest = None
        for seat in self.seats.values():
            mean = seat.mean_lock()
            if mean is None:
                continue
            if fastest is None or mean < fastest.mean_lock():
                fastest = seat
        return {
            "winner": _award(winner, f"{winner.score} points from {winner.correct} "
                                     f"right" if winner else ""),
            "fastest": _award(
                fastest,
                f"{fastest.mean_lock() / 1000:.1f}s average on the ones they got right"
                if fastest else ""),
        }

    # --- the view --------------------------------------------------------

    def public(self, viewer_id: str | None, is_host: bool = False) -> dict[str, Any]:
        """One personalised snapshot. The security boundary of this feature.

        What is NOT in here while a question is open: the subject, on any key,
        nested or otherwise; any unplayed question; and anybody else's choice.
        A host snapshot is not an exception to that — it is on a wall.
        """
        q = self.question()
        me = self.seats.get(viewer_id or "")

        cards = []
        for seat in self.seats.values():
            card = seat.participant.card()
            card.update({
                "connected": seat.connected,
                "score": seat.score,
                "correct": seat.correct,
                "answered": bool(seat.answer),
            })
            if self.phase in ("reveal", "final"):
                # Settled: the round is over and what everyone said is now the
                # whole point of the screen.
                card["answer"] = seat.answer
                card["gain"] = seat.gain
                card["bonus"] = seat.bonus
            cards.append(card)

        view: dict[str, Any] = {
            "room_id": self.id,
            "phase": self.phase,
            "you": viewer_id if me else None,
            "is_host": is_host,
            "players": cards,
            "index": self.index,
            "total": len(self.questions),
            "registered": sum(1 for s in self.seats.values()
                              if s.participant.registered),
            "here": len(self.present()),
            "locked_in": self.answered_count(),
            "waiting_on": len(self.present()),
        }

        if q is not None and self.phase in ("question", "reveal"):
            view["question"] = q.public()          # no subject in here
            if self.phase == "reveal":
                view["subject"] = q.subject        # and here it is earned
        if me is not None:
            view["your_answer"] = me.answer
            view["your_score"] = me.score
            view["you_are_subject"] = bool(q and me.id == q.subject)
        if self.phase == "final":
            view["awards"] = self.awards()
        return view


def _award(seat: Seat | None, detail: str) -> dict[str, Any] | None:
    if seat is None:
        return None
    card = seat.participant.card()
    card["detail"] = detail
    return card


# --- the rules ------------------------------------------------------------

class Rejected(Exception):
    """A move the rules do not allow. Carries what to tell the client."""


def create_room(participants: list[Participant]) -> QuizRoom:
    """A room with a seat for every registered person.

    Unregistered participants are left out: they have no name and no face, so
    there is no card to tap and nothing to guess.
    """
    room = QuizRoom(id=secrets.token_hex(4))
    for person in participants:
        if person.registered:
            room.seats[person.id] = Seat(participant=person)
    return room


def on_connect(room: QuizRoom, participant_id: str) -> None:
    seat = room.seats.get(participant_id)
    if seat is not None:
        seat.connected = True


def on_disconnect(room: QuizRoom, participant_id: str) -> None:
    """Mark them away. The quiz carries on: with no clock, waiting for a
    dropped phone would stall the room indefinitely."""
    seat = room.seats.get(participant_id)
    if seat is not None:
        seat.connected = False


def start(room: QuizRoom) -> None:
    if room.phase != "lobby":
        raise Rejected("The quiz has already started.")
    if len(room.present()) < config.QUIZ_MIN_PLAYERS:
        raise Rejected(
            f"Wait for at least {config.QUIZ_MIN_PLAYERS} people to arrive.")

    people = [s.participant for s in room.seats.values()]
    room.questions = rollcall.build_questions(people)
    if not room.questions:
        raise Rejected("Nobody has written any answers yet.")

    room.index = 0
    room.phase = "question"
    room.asked_at = _now_ms()
    for seat in room.seats.values():
        seat.score = seat.correct = seat.gain = seat.bonus = 0
        seat.fast_sum = seat.fast_count = 0
        seat.answer = None
        seat.answered_at = None


def pick(room: QuizRoom, participant_id: str, choice: str | None) -> None:
    """Lock one answer. The subject locks the sentinel instead of guessing."""
    if room.phase != "question":
        raise Rejected("There is no question open.")
    seat = room.seats.get(participant_id)
    q = room.question()
    if seat is None or q is None:
        raise Rejected("You are not in this room.")
    if seat.answer:
        raise Rejected("You have already locked one in.")

    is_subject = seat.id == q.subject
    if is_subject and choice != config.QUIZ_SAT_OUT:
        raise Rejected("This one is about you.")
    if not is_subject and choice == config.QUIZ_SAT_OUT:
        raise Rejected("You have to pick somebody.")
    if not is_subject and choice not in room.seats:
        raise Rejected("That is not somebody in this room.")
    if not is_subject and choice == seat.id:
        raise Rejected("It is never you.")

    seat.answer = choice
    seat.answered_at = max(0, _now_ms() - room.asked_at)


def reveal(room: QuizRoom) -> None:
    """Score the round and open the answer.

    Everyone correct is ranked by how quickly they locked and the bonus decays
    down that order. Ties in time break on name, so the same room scores the
    same way twice.
    """
    if room.phase != "question":
        raise Rejected("There is nothing to reveal.")
    q = room.question()
    for seat in room.seats.values():
        seat.gain = seat.bonus = 0

    right = sorted(
        (s for s in room.scorable() if q and s.answer == q.subject),
        key=lambda s: (s.answered_at if s.answered_at is not None else 1 << 30,
                       s.name.lower()),
    )
    for rank, seat in enumerate(right):
        seat.bonus = speed_bonus(rank)
        seat.gain = config.QUIZ_BASE_POINTS + seat.bonus
        seat.score += seat.gain
        seat.correct += 1
        seat.fast_sum += seat.answered_at or 0
        seat.fast_count += 1

    room.phase = "reveal"


def next_question(room: QuizRoom) -> None:
    if room.phase != "reveal":
        raise Rejected("Reveal this one first.")
    for seat in room.seats.values():
        seat.answer = None
        seat.answered_at = None
        seat.gain = seat.bonus = 0
    if room.index + 1 >= len(room.questions):
        room.phase = "final"
        return
    room.index += 1
    room.phase = "question"
    room.asked_at = _now_ms()


def finish(room: QuizRoom) -> None:
    """End early. A quiz cut short still gets an ending and a scoreboard."""
    if room.phase == "closed":
        raise Rejected("The session is closed.")
    room.phase = "final"


def close(room: QuizRoom) -> None:
    room.phase = "closed"
