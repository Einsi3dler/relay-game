"""ROLL CALL's room rules, and the one property everything else rests on.

Invented people throughout. The real roster is gitignored and
`tests/test_roster_privacy.py` makes sure it stays out of this repository, so
nothing here may be copied from it.
"""

from __future__ import annotations

import json
import random

import pytest

from backend import config, quizroom, rollcall
from backend.rollcall import Participant


def person(pid: str, name: str, **answers) -> Participant:
    return Participant(id=pid, email=f"{pid}@example.invalid", display_name=name,
                       avatar=None, registered_at="now", answers=answers)


@pytest.fixture
def cast() -> list[Participant]:
    return [
        person("rc_a", "Ada", fact="I keep bees", career="I shipped a compiler"),
        person("rc_b", "Bo", fact="I have never flown", career="I ran a marathon"),
        person("rc_c", "Cleo", fact="I can juggle", career="I built a bridge"),
        person("rc_d", "Dev", fact="I play the tuba", career="I taught myself Welsh"),
    ]


@pytest.fixture
def room(cast):
    r = quizroom.create_room(cast)
    for seat in r.seats.values():
        seat.connected = True
    # A fixed shuffle so a failure is reproducible.
    people = [s.participant for s in r.seats.values()]
    r.questions = rollcall.build_questions(people, rng=random.Random(7))
    r.index = 0
    r.phase = "question"
    r.asked_at = quizroom._now_ms()
    return r


def others(room, question):
    return [s for s in room.seats.values() if s.id != question.subject]


# --- the one that matters -------------------------------------------------

def _walk(value):
    """Every string anywhere in a nested structure."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, inner in value.items():
            yield str(key)
            yield from _walk(inner)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            yield from _walk(inner)


def test_an_open_question_never_carries_its_answer(room):
    """No snapshot during `question` may let a viewer pick the subject out.

    Note what this does *not* assert: that the subject's id is absent. It is
    present, legitimately, because they are a card like everybody else, and
    leaving them out of the card list is the exact bug this game shipped once
    already. The property is **indistinguishability**: nothing in the snapshot
    may separate their card from the other nine.
    """
    q = room.question()
    viewers = [(s.id, False) for s in room.seats.values()] + [(None, True)]

    for viewer_id, is_host in viewers:
        view = room.public(viewer_id, is_host=is_host)
        who = "host" if is_host else viewer_id

        assert "subject" not in view, f"{who} got a `subject` key"
        assert q.subject not in json.dumps(view["question"]), \
            f"{who} was handed the subject inside the question"

        cards = {c["id"]: c for c in view["players"]}
        assert set(cards) == set(room.seats), f"{who} saw a roster with a gap"

        shapes = {frozenset(c) for c in view["players"]}
        assert len(shapes) == 1, (
            f"{who} saw one card shaped differently from the rest, which is "
            f"all it takes to name the subject"
        )

        subject_card = cards[q.subject]
        twin = next(c for pid, c in cards.items() if pid != q.subject)
        differing = {k for k in subject_card
                     if k not in ("id", "name", "avatar") and subject_card[k] != twin[k]}
        assert not differing, f"{who} could spot the subject by {sorted(differing)}"


def test_the_host_screen_learns_no_more_than_a_player(room):
    """It is projected onto a wall, so it is the least trusted viewer here."""
    q = room.question()
    host = room.public(None, is_host=True)
    player = room.public(next(iter(room.seats)), is_host=False)
    assert "subject" not in host
    assert q.subject not in json.dumps(host["question"])
    assert set(host["question"]) == set(player["question"])
    assert len(host["players"]) == len(player["players"]) == len(room.seats)


def test_unplayed_questions_never_reach_a_client(room):
    view = room.public(next(iter(room.seats)))
    blob = json.dumps(view)
    assert "questions" not in view
    for other in room.questions[1:]:
        assert other.body not in blob
        assert other.id not in blob


def test_nobody_elses_choice_shows_before_the_reveal(room):
    q = room.question()
    for seat in others(room, q):
        quizroom.pick(room, seat.id, q.subject)
    quizroom.pick(room, q.subject, config.QUIZ_SAT_OUT)

    me, *rest = [s for s in room.seats.values() if s.id != q.subject]
    view = room.public(me.id)
    for card in view["players"]:
        assert "answer" not in card, "a choice leaked before the reveal"
    assert view["your_answer"] == q.subject       # but you see your own
    assert all(card["answered"] for card in view["players"])


def test_the_reveal_is_when_the_subject_is_earned(room):
    q = room.question()
    for seat in others(room, q):
        quizroom.pick(room, seat.id, q.subject)
    quizroom.pick(room, q.subject, config.QUIZ_SAT_OUT)
    quizroom.reveal(room)

    view = room.public(None, is_host=True)
    assert view["subject"] == q.subject
    assert all("answer" in card for card in view["players"])


# --- the rules ------------------------------------------------------------

def test_the_subject_cannot_guess_and_scores_nothing(room):
    q = room.question()
    with pytest.raises(quizroom.Rejected):
        quizroom.pick(room, q.subject, q.subject)

    quizroom.pick(room, q.subject, config.QUIZ_SAT_OUT)
    for seat in others(room, q):
        quizroom.pick(room, seat.id, q.subject)
    quizroom.reveal(room)
    assert room.seats[q.subject].gain == 0
    assert room.seats[q.subject].correct == 0


def test_only_the_subject_may_sit_out(room):
    q = room.question()
    someone = others(room, q)[0]
    with pytest.raises(quizroom.Rejected):
        quizroom.pick(room, someone.id, config.QUIZ_SAT_OUT)


def test_everybody_locks_so_the_roster_has_no_gap(room):
    """The projector waits on the whole room, subject included."""
    q = room.question()
    for seat in others(room, q):
        quizroom.pick(room, seat.id, q.subject)
    assert not room.all_answered(), "the subject has not locked yet"
    quizroom.pick(room, q.subject, config.QUIZ_SAT_OUT)
    assert room.all_answered()
    assert room.answered_count() == len(room.present())


def test_a_lock_is_final(room):
    q = room.question()
    first, second = others(room, q)[:2]
    quizroom.pick(room, first.id, q.subject)
    with pytest.raises(quizroom.Rejected):
        quizroom.pick(room, first.id, second.id)
    assert room.seats[first.id].answer == q.subject


def test_you_are_never_the_answer_to_your_own_question(room):
    q = room.question()
    someone = others(room, q)[0]
    with pytest.raises(quizroom.Rejected):
        quizroom.pick(room, someone.id, someone.id)


def test_the_bonus_ladder(room):
    q = room.question()
    for offset, seat in enumerate(others(room, q)):
        quizroom.pick(room, seat.id, q.subject)
        room.seats[seat.id].answered_at = 1000 * (offset + 1)   # fix the order
    quizroom.pick(room, q.subject, config.QUIZ_SAT_OUT)
    quizroom.reveal(room)

    gains = [room.seats[s.id].gain for s in others(room, q)]
    assert gains == [150, 140, 132]


def test_a_round_clears_before_the_next_one(room):
    q = room.question()
    for seat in others(room, q):
        quizroom.pick(room, seat.id, q.subject)
    quizroom.pick(room, q.subject, config.QUIZ_SAT_OUT)
    quizroom.reveal(room)
    quizroom.next_question(room)
    assert room.phase == "question"
    assert all(s.answer is None and s.gain == 0 for s in room.seats.values())


def test_fastest_finger_ignores_wrong_answers(room):
    """Otherwise it goes to whoever taps a face at random the instant the
    prompt lands, which is the opposite of the thing it is for."""
    q = room.question()
    slow_but_right, fast_but_wrong, _ = others(room, q)
    quizroom.pick(room, slow_but_right.id, q.subject)
    room.seats[slow_but_right.id].answered_at = 9000
    quizroom.pick(room, fast_but_wrong.id, slow_but_right.id)
    room.seats[fast_but_wrong.id].answered_at = 10
    quizroom.pick(room, q.subject, config.QUIZ_SAT_OUT)
    quizroom.reveal(room)
    quizroom.finish(room)

    fastest = room.awards()["fastest"]
    assert fastest is not None
    assert fastest["id"] == slow_but_right.id


def test_an_absent_person_is_not_waited_on(room):
    q = room.question()
    absent = others(room, q)[0]
    absent.connected = False
    for seat in others(room, q)[1:]:
        quizroom.pick(room, seat.id, q.subject)
    quizroom.pick(room, q.subject, config.QUIZ_SAT_OUT)
    assert room.all_answered(), "the room stalled on somebody who is not here"


def test_a_closed_room_refuses_everything(room):
    quizroom.close(room)
    assert room.phase == "closed"
    with pytest.raises(quizroom.Rejected):
        quizroom.pick(room, next(iter(room.seats)), "rc_a")
    with pytest.raises(quizroom.Rejected):
        quizroom.finish(room)


def test_every_answer_becomes_a_question(cast):
    questions = rollcall.build_questions(cast, rng=random.Random(1))
    assert len(questions) == sum(len(p.answers) for p in cast)
    assert {q.subject for q in questions} == {p.id for p in cast}
    assert all(q.label for q in questions)
    assert "subject" not in questions[0].public()
