"""BULLSHIT QUIZ room (backend/bsroom.py): the state machine and its view.

The leak tests matter most. Until a round's reveal, no snapshot (player or
host) may carry the truth, the author or the bullshitters, and a speaker may
learn only their own role.
"""

from __future__ import annotations

import json
import random

import pytest

from backend import bsquiz, bsroom
from backend.bsquiz import BULLSHITTER, GUESSER, TRUTHFUL
from backend.bsroster import Player

SECRET_EXPLANATION = "zebra-explanation-marker"


def player(i: int) -> Player:
    return Player(id=f"p{i}", email=f"p{i}@example.test", option_count=4,
                  display_name=f"Player {i}", has_question=True)


def questions_for(players: list[Player]) -> dict[str, bsquiz.Question]:
    return {
        p.id: bsquiz.Question(
            author=p.id, prompt=f"Question by {p.name}?",
            answer=f"real-{p.id}",
            decoys=tuple(f"fake-{p.id}-{k}" for k in range(3)),
            explanation=f"{SECRET_EXPLANATION}-{p.id}")
        for p in players
    }


def make_room(n: int = 6, seed: int = 1):
    players = [player(i) for i in range(n)]
    room = bsroom.create_room(players, rng=random.Random(seed))
    for p in players:
        bsroom.on_connect(room, p.id)
    return room, questions_for(players)


def started(n: int = 6, seed: int = 1):
    room, qs = make_room(n, seed)
    bsroom.start(room, qs)
    return room


def free_wager(room, seat_id: str) -> int:
    seat = room.seats[seat_id]
    return min(w for w in bsquiz.wager_values(room.n) if w not in seat.used)


def brief(room) -> None:
    """Every speaker commits, then the host opens the discussion."""
    for seat in room.speakers():
        if room.role_of(seat.id) == TRUTHFUL:
            bsroom.ready(room, seat.id)
        else:
            bsroom.bluff(room, seat.id, free_wager(room, seat.id))
    bsroom.open_discussion(room)


def guess_all(room, key: str | None = None) -> None:
    for seat in room.guessers():
        bsroom.guess(room, seat.id, key or room.panel.correct_key,
                     free_wager(room, seat.id))


def play_round(room, key: str | None = None) -> None:
    brief(room)
    guess_all(room, key)
    bsroom.lock(room)
    bsroom.reveal(room)


# --- the whole game -------------------------------------------------------

def test_n_rounds_each_player_truthful_once():
    room = started(6)
    authors = []
    while room.phase != "final":
        assert room.phase == "briefing"
        authors.append(room.panel.author)
        play_round(room)
        bsroom.next_round(room)
    assert sorted(authors) == sorted(room.seats)
    assert len(authors) == room.n == 6


def test_every_wager_number_is_spent_exactly_once():
    room = started(5)
    while room.phase != "final":
        play_round(room)
        bsroom.next_round(room)
    for seat in room.seats.values():
        assert seat.used == set(range(1, room.n))


def test_totals_are_the_sum_of_round_lines():
    room = started(5, seed=9)
    expected = {pid: 0 for pid in room.seats}
    while room.phase != "final":
        # Everyone picks the first option, true or not, to vary outcomes.
        play_round(room, key="A")
        for pid, line in room.result.lines.items():
            expected[pid] += line.delta
        bsroom.next_round(room)
    assert {pid: s.total for pid, s in room.seats.items()} == expected
    view = room.public(None, is_host=True)
    assert view["winners"] == bsquiz.winners(expected)
    assert [row["total"] for row in view["standings"]] == sorted(expected.values(), reverse=True)


def test_reveal_applies_scores_once():
    room = started(5)
    play_round(room)
    totals = {pid: s.total for pid, s in room.seats.items()}
    with pytest.raises(bsroom.Rejected):
        bsroom.reveal(room)
    room.public("p0")
    assert {pid: s.total for pid, s in room.seats.items()} == totals


# --- what each viewer sees ------------------------------------------------

def _blob(view) -> str:
    return json.dumps(view)


@pytest.mark.parametrize("phase", ["briefing", "open", "votes"])
def test_nothing_leaks_before_the_reveal(phase):
    room = started(7, seed=3)
    if phase in ("open", "votes"):
        brief(room)
    if phase == "votes":
        guess_all(room)
        bsroom.lock(room)
    assert room.phase == phase

    truth = room.panel.option(room.panel.correct_key)
    q = room.question()
    for viewer in [None, *room.seats]:
        view = room.public(viewer, is_host=viewer is None)
        blob = _blob(view)
        for key in ("truth", "correct", "author", "bullshitters", "line"):
            assert f'"{key}"' not in blob, (viewer, key)
        assert '"winner"' not in blob
        if viewer != room.panel.author:
            assert q.explanation not in blob
        # The option list says who defends what, never which is real.
        for option in view["question"]["options"]:
            assert set(option) == {"key", "text", "speaker"}
        role = room.role_of(viewer) if viewer else None
        if viewer is None:
            assert "role" not in view and "your_option" not in view
        else:
            assert view["role"] == role
            if role == GUESSER:
                assert "your_option" not in view
        # Every speaker's tile reads the same once they have committed.
        speaker_cards = [c for c in view["players"] if c["speaker"]]
        assert len({c["committed"] for c in speaker_cards}) == 1
    assert truth.speaker == room.panel.author


def test_the_host_sees_no_more_than_a_guesser():
    room = started(6, seed=4)
    brief(room)
    guesser = room.guessers()[0].id
    host = room.public(None, is_host=True)
    seen = room.public(guesser)
    for key in ("role", "committed", "your_wager", "your_pick", "used_wagers",
                "your_total", "you"):
        host.pop(key, None)
        seen.pop(key, None)
    host.pop("is_host")
    seen.pop("is_host")
    assert host == seen


def test_votes_shows_counts_but_not_the_truth():
    room = started(6, seed=5)
    brief(room)
    guess_all(room)
    bsroom.lock(room)
    view = room.public(None, is_host=True)
    assert sum(view["counts"].values()) == len(room.guessers())
    assert "truth" not in view


def test_the_reveal_earns_the_truth():
    room = started(6, seed=6)
    play_round(room)
    view = room.public(room.guessers()[0].id)
    assert view["truth"]["key"] == room.panel.correct_key
    assert view["truth"]["author"] == room.panel.author
    assert sorted(view["truth"]["bullshitters"]) == sorted(room.panel.bullshitters)
    assert all(card["line"] for card in view["players"])


def test_a_speaker_learns_only_their_own_role():
    room = started(6, seed=7)
    liar = room.panel.bullshitters[0]
    view = room.public(liar)
    assert view["role"] == BULLSHITTER
    assert view["your_option"] == room.panel.option_of(liar).key
    assert "your_explanation" not in view
    author = room.public(room.panel.author)
    assert author["role"] == TRUTHFUL
    assert author["your_explanation"].startswith(SECRET_EXPLANATION)


def test_lobby_cards_show_who_has_written_and_nothing_else():
    room, _ = make_room(4)
    view = room.public("p0")
    assert view["submitted"] == 4 and view["registered"] == 4
    assert all(card["submitted"] for card in view["players"])
    assert "question" not in view


# --- commitments ----------------------------------------------------------

def test_the_truthful_player_confirms_and_cannot_wager():
    room = started(5)
    author = room.panel.author
    with pytest.raises(bsroom.Rejected):
        bsroom.bluff(room, author, 1)
    bsroom.ready(room, author)
    with pytest.raises(bsroom.Rejected):
        bsroom.ready(room, author)


def test_only_bullshitters_bluff_and_only_in_the_briefing():
    room = started(6)
    guesser = room.guessers()[0].id
    with pytest.raises(bsroom.Rejected):
        bsroom.bluff(room, guesser, 1)
    with pytest.raises(bsroom.Rejected):
        bsroom.guess(room, guesser, "A", 1)        # not open yet
    brief(room)
    with pytest.raises(bsroom.Rejected):
        bsroom.bluff(room, room.panel.bullshitters[0], 2)


def test_speakers_never_guess():
    room = started(6)
    brief(room)
    for speaker in room.panel.speakers:
        with pytest.raises(bsroom.Rejected):
            bsroom.guess(room, speaker, "A", 3)


def test_a_commitment_is_final_and_spends_its_number():
    room = started(6)
    brief(room)
    g = room.guessers()[0].id
    bsroom.guess(room, g, "A", 3)
    with pytest.raises(bsroom.Rejected):
        bsroom.guess(room, g, "B", 4)
    assert room.seats[g].used == {3}


def test_a_spent_number_cannot_be_used_again_later():
    room = started(6, seed=2)
    brief(room)
    g = room.guessers()[0].id
    bsroom.guess(room, g, "A", 5)
    guess_all_remaining(room)
    bsroom.lock(room)
    bsroom.reveal(room)
    bsroom.next_round(room)
    role = room.role_of(g)
    if role == BULLSHITTER:
        with pytest.raises(bsroom.Rejected, match="already used 5"):
            bsroom.bluff(room, g, 5)
    elif role == GUESSER:
        brief(room)
        with pytest.raises(bsroom.Rejected, match="already used 5"):
            bsroom.guess(room, g, "A", 5)


def guess_all_remaining(room) -> None:
    for seat in room.guessers():
        if seat.pick is None:
            bsroom.guess(room, seat.id, "A", free_wager(room, seat.id))


@pytest.mark.parametrize("bad", [0, 6, -1, 2.5, "3", None, True])
def test_illegal_wagers(bad):
    room = started(6)
    brief(room)
    with pytest.raises(bsroom.Rejected):
        bsroom.guess(room, room.guessers()[0].id, "A", bad)


def test_a_guess_must_name_a_real_option():
    room = started(6)
    brief(room)
    with pytest.raises(bsroom.Rejected):
        bsroom.guess(room, room.guessers()[0].id, "Z", 1)


# --- the host waits for everybody -----------------------------------------

def test_the_discussion_waits_for_every_speaker():
    room = started(6)
    bsroom.ready(room, room.panel.author)
    with pytest.raises(bsroom.Rejected, match="Waiting on"):
        bsroom.open_discussion(room)


def test_locking_waits_for_every_guesser_even_if_away():
    room = started(6)
    brief(room)
    away = room.guessers()[0].id
    bsroom.on_disconnect(room, away)
    for seat in room.guessers()[1:]:
        bsroom.guess(room, seat.id, "A", 1)
    with pytest.raises(bsroom.Rejected, match="Waiting on 1 guesser"):
        bsroom.lock(room)


def test_steps_out_of_order_are_refused():
    room = started(5)
    for step in (bsroom.lock, bsroom.reveal, bsroom.next_round):
        with pytest.raises(bsroom.Rejected):
            step(room)


# --- the lobby ------------------------------------------------------------

def test_start_needs_three_players():
    room, qs = make_room(2)
    with pytest.raises(bsroom.Rejected, match="at least 3"):
        bsroom.start(room, qs)


def test_start_waits_for_every_question():
    room, qs = make_room(4)
    del qs["p2"]
    with pytest.raises(bsroom.Rejected, match="Player 2"):
        bsroom.start(room, qs)


def test_roster_and_submissions_freeze_at_start():
    room = started(4)
    bsroom.refresh(room, [player(i) for i in range(6)])
    assert len(room.seats) == 4 and room.n == 4
    with pytest.raises(bsroom.Rejected):
        bsroom.can_submit(room)


def test_unregistered_players_get_no_seat():
    unnamed = Player(id="pX", email="x@example.test", option_count=2,
                     display_name=None, has_question=False)
    room = bsroom.create_room([player(0), unnamed])
    assert list(room.seats) == ["p0"]
