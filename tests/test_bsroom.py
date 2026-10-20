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
                  display_name=f"Player {i}", has_question=True, user_id=f"u{i}")


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
                "your_total", "you", "relegated", "history"):
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


def test_locking_does_not_wait_for_an_offline_guesser():
    room = started(6)
    brief(room)
    away = room.guessers()[0].id
    bsroom.on_disconnect(room, away)
    for seat in room.guessers()[1:]:
        bsroom.guess(room, seat.id, "A", 1)
    bsroom.lock(room)
    # They cast no vote, spent nothing, and have no line this round.
    assert away not in room.result.lines
    assert room.seats[away].used == set()
    assert sum(room.result.counts.values()) == len(room.guessers()) - 1


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


# --- leaving the game -----------------------------------------------------

def bullshitter_count(room) -> int:
    return len(room.panel.bullshitters)


def test_an_offline_guessers_earlier_vote_still_counts():
    room = started(6)
    brief(room)
    g = room.guessers()[0].id
    bsroom.guess(room, g, room.panel.correct_key, 2)
    bsroom.on_disconnect(room, g)
    guess_all_remaining(room)
    bsroom.lock(room)
    assert room.result.lines[g].delta == 2


def test_a_returning_guesser_can_still_vote():
    room = started(6)
    brief(room)
    g = room.guessers()[0].id
    bsroom.on_disconnect(room, g)
    bsroom.on_connect(room, g)
    assert len(room.awaited_guessers()) == len(room.guessers())
    bsroom.guess(room, g, "A", 1)


def test_relegation_is_permanent_and_never_bluffs_again():
    room = started(7, seed=12)
    gone = room.guessers()[0].id
    bsroom.on_disconnect(room, gone)
    bsroom.on_connect(room, gone)
    assert room.seats[gone].relegated
    while room.phase not in ("final", "waiting"):
        assert gone not in room.panel.bullshitters
        play_round(room)
        bsroom.next_round(room)
    assert room.phase == "final"


def test_offline_authors_question_is_skipped_then_played_on_return():
    room = started(6, seed=3)
    queued = room.pending[0]
    bsroom.on_disconnect(room, queued)
    play_round(room)
    bsroom.next_round(room)
    assert room.current != queued and queued in room.pending
    bsroom.on_connect(room, queued)
    authors = [room.current]
    while room.phase != "final":
        play_round(room)
        bsroom.next_round(room)
        if room.current:
            authors.append(room.current)
    assert queued in room.played and room.dropped == []


def test_questions_from_players_who_never_return_are_dropped():
    room = started(6, seed=4)
    gone = [room.pending[0], room.pending[1]]
    for g in gone:
        bsroom.on_disconnect(room, g)
    while room.phase != "final":
        play_round(room)
        bsroom.next_round(room)
    assert sorted(room.dropped) == sorted(gone)
    assert len(room.played) == 4
    view = room.public(None, is_host=True)
    assert view["rounds"] == 4 and view["dropped"] == 2


def test_author_leaving_the_briefing_defers_and_refunds():
    room = started(6, seed=5)
    author = room.panel.author
    liar = room.panel.bullshitters[0]
    bsroom.bluff(room, liar, 3)
    bsroom.on_disconnect(room, author)
    assert room.pending[0] == author
    assert room.panel.author != author
    assert room.phase == "briefing"
    assert 3 not in room.seats[liar].used


def test_bullshitter_leaving_the_briefing_is_replaced():
    room = started(7, seed=6)
    liar = room.panel.bullshitters[0]
    key = room.panel.option_of(liar).key
    text = room.panel.option(key).text
    bsroom.bluff(room, liar, 4)
    bsroom.on_disconnect(room, liar)
    assert liar not in room.panel.speakers
    assert 4 not in room.seats[liar].used
    taker = room.panel.option(key).speaker
    assert room.panel.option(key).text == text
    assert room.seats[taker].may_bluff and room.role_of(taker) == BULLSHITTER


def test_bullshitter_leaving_with_nobody_to_take_over():
    """Four players: author, two bullshitters, one guesser. A liar leaves;
    the lone guesser cannot take over, so that option is removed."""
    room = started(4, seed=1)
    while bullshitter_count(room) < 2:
        room = started(4, seed=room.rng.randint(0, 10**6))
    liar = room.panel.bullshitters[0]
    bsroom.on_disconnect(room, liar)
    assert bullshitter_count(room) == 1
    assert [o.key for o in room.panel.options] == ["A", "B"]


def test_last_bullshitter_leaving_defers_the_question():
    room = started(3, seed=2)       # author, one liar, one guesser
    author = room.panel.author
    liar = room.panel.bullshitters[0]
    bsroom.on_disconnect(room, liar)
    # Only two online now: nothing can be dealt, the host waits.
    assert room.phase == "waiting"
    assert author in room.pending
    bsroom.on_connect(room, liar)
    bsroom.next_round(room)
    assert room.phase == "briefing"
    assert liar not in room.panel.bullshitters     # relegated for good


def test_speakers_leaving_after_the_discussion_opens_keep_the_round():
    room = started(6, seed=7)
    brief(room)
    for speaker in room.panel.speakers:
        bsroom.on_disconnect(room, speaker)
    assert room.phase == "open"
    guess_all_remaining(room)
    bsroom.lock(room)
    bsroom.reveal(room)
    assert room.result.lines[room.panel.author].role == TRUTHFUL


def test_offline_at_start_means_relegated():
    room, qs = make_room(6)
    bsroom.on_disconnect(room, "p5")
    bsroom.start(room, qs)
    assert room.seats["p5"].relegated
    assert room.current != "p5"


def test_start_needs_three_online():
    room, qs = make_room(4)
    bsroom.on_disconnect(room, "p0")
    bsroom.on_disconnect(room, "p1")
    with pytest.raises(bsroom.Rejected, match="online"):
        bsroom.start(room, qs)


def test_lobby_disconnects_do_not_relegate():
    room, qs = make_room(5)
    bsroom.on_disconnect(room, "p1")
    bsroom.on_connect(room, "p1")
    bsroom.start(room, qs)
    assert not room.seats["p1"].relegated


# --- the grace period -----------------------------------------------------

def test_a_dropped_socket_is_not_leaving():
    room = started(6)
    brief(room)
    g = room.guessers()[0].id
    bsroom.on_drop(room, g)
    seat = room.seats[g]
    assert seat.reconnecting and seat.connected and not seat.relegated
    # Still waited on: the lock does not go through without them.
    guess_all_remaining_except(room, g)
    with pytest.raises(bsroom.Rejected, match="Waiting on 1"):
        bsroom.lock(room)
    bsroom.on_connect(room, g)
    assert not room.seats[g].reconnecting


def test_a_dropped_speaker_is_not_swapped_out():
    room = started(6)
    liar = room.panel.bullshitters[0]
    bsroom.on_drop(room, liar)
    assert liar in room.panel.bullshitters


def guess_all_remaining_except(room, skip: str) -> None:
    for seat in room.guessers():
        if seat.id != skip and seat.pick is None:
            bsroom.guess(room, seat.id, "A", free_wager(room, seat.id))


# --- round history --------------------------------------------------------

def test_history_records_each_round_at_its_reveal():
    room = started(5, seed=3)
    assert room.public(None, is_host=True)["history"] == []
    brief(room)
    guess_all(room)
    bsroom.lock(room)
    assert room.public(None, is_host=True)["history"] == []    # votes: not yet
    bsroom.reveal(room)
    entry = room.public(None, is_host=True)["history"][0]
    assert entry["round"] == 1
    assert entry["truth"] == room.panel.correct_key
    assert entry["author"] == room.panel.author
    assert sum(entry["deltas"].values()) == sum(l.delta for l in room.result.lines.values())
    bsroom.next_round(room)
    # The new round's briefing still shows only the finished one.
    assert len(room.public(None, is_host=True)["history"]) == 1


def test_history_never_carries_the_round_in_play():
    room = started(6, seed=8)
    play_round(room)
    bsroom.next_round(room)
    current = room.question()
    blob = json.dumps(room.public(None, is_host=True)["history"])
    assert current.prompt not in blob


def test_players_never_receive_the_history():
    """Host only (the owner's call). Checked in every phase of a whole game,
    for every seat, on the raw snapshot rather than the screen."""
    room = started(5, seed=4)
    while True:
        for viewer in room.seats:
            assert "history" not in room.public(viewer)
        if room.phase == "final":
            break
        play_round(room)
        for viewer in room.seats:
            assert "history" not in room.public(viewer)
        bsroom.next_round(room)
    assert room.public(None, is_host=True)["history"]


def test_history_is_a_full_recap_at_the_end():
    room = started(4, seed=2)
    while room.phase != "final":
        play_round(room)
        bsroom.next_round(room)
    assert [h["round"] for h in room.public(None, is_host=True)["history"]] == [1, 2, 3, 4]


# --- host safety controls -------------------------------------------------

def test_lock_anyway_gives_a_stalled_guesser_no_vote():
    room = started(6)
    brief(room)
    stalled = room.guessers()[0].id
    guess_all_remaining_except(room, stalled)
    with pytest.raises(bsroom.Rejected):
        bsroom.lock(room)
    bsroom.lock(room, force=True)
    assert stalled not in room.result.lines
    assert room.seats[stalled].used == set()


def test_mark_gone_applies_the_leaving_rule_now():
    room = started(7, seed=6)
    liar = room.panel.bullshitters[0]
    bsroom.mark_gone(room, liar)
    assert room.seats[liar].relegated
    assert liar not in room.panel.speakers


def test_mark_gone_is_refused_in_the_lobby_and_for_strangers():
    room, _ = make_room(4)
    with pytest.raises(bsroom.Rejected):
        bsroom.mark_gone(room, "p0")
    room = started(4)
    with pytest.raises(bsroom.Rejected):
        bsroom.mark_gone(room, "nobody")
