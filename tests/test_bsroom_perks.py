"""BULLSHIT QUIZ perks in the room (backend/bsroom.py): the offers, who may
play what and when, who sees it, the discussion clock, reaction windows and
reveal-time perks.

What each perk does to the points is tested in tests/test_bsperks.py. Time
is passed in explicitly: the room keeps no clock.
"""

from __future__ import annotations

import json

import pytest

from backend import bsperks, bsroom, config
from backend.bsquiz import BULLSHITTER, GUESSER, TRUTHFUL
from tests.test_bsroom import SECRET_EXPLANATION, free_wager, started

WINDOW = config.BS_REACTION_SECONDS
FLOOR = config.BS_FLOOR_SECONDS


def give(room, seat_id: str, perk: str) -> None:
    """Hand somebody a perk directly, skipping the offers."""
    seat = room.seats[seat_id]
    seat.perk = perk
    seat.perk_used = False


def commit_speakers(room) -> None:
    for seat in room.speakers():
        if room.role_of(seat.id) == TRUTHFUL:
            bsroom.ready(room, seat.id)
        elif seat.bluff is None:
            bsroom.bluff(room, seat.id, free_wager(room, seat.id))


def to_ballot(room, now: float = 0) -> None:
    commit_speakers(room)
    bsroom.open_discussion(room, now=now)
    bsroom.end_talk(room, now=now)
    assert room.ballot


def guess_rest(room, key: str | None = None) -> None:
    for seat in room.guessers():
        if seat.pick is None:
            bsroom.guess(room, seat.id, key or room.panel.correct_key, free_wager(room, seat.id))


def ids(room, role: str) -> list[str]:
    return [s for s in room.seats if room.role_of(s) == role]


def wrong_key(room) -> str:
    return next(o.key for o in room.panel.options if not o.correct)


def blob(view) -> str:
    return json.dumps(view)


def played(view) -> str:
    """The plays a viewer can see. Who *holds* each perk is public after the
    draft; what must stay hidden is a secret perk being played."""
    return json.dumps(view.get("perks", []))


# --- holding and spending -------------------------------------------------

def test_everybody_drafted_a_different_perk():
    room = started(8)
    perks = [seat.perk for seat in room.seats.values()]
    assert None not in perks and len(set(perks)) == len(perks)


def test_who_holds_what_is_public_but_when_it_is_played_is_not():
    room = started(6, seed=3)
    g = ids(room, GUESSER)[0]
    give(room, g, "call_bullshit")
    other = next(s for s in room.seats if s != g)
    card = next(c for c in room.public(other, now=0)["players"] if c["id"] == g)
    assert card["perk"] == "call_bullshit" and card["perk_spent"] is False
    bsroom.play_perk(room, g, {"target": room.panel.author}, now=0)
    card = next(c for c in room.public(other, now=0)["players"] if c["id"] == g)
    assert card["perk_spent"] is False                  # secret until the reveal
    assert next(c for c in room.public(g, now=0)["players"] if c["id"] == g)["perk_spent"] is False
    to_ballot(room)
    guess_rest(room)
    bsroom.lock(room)
    bsroom.reveal(room)
    card = next(c for c in room.public(other, now=0)["players"] if c["id"] == g)
    assert card["perk_spent"] is True


def test_a_perk_is_spent_once():
    room = started(6)
    g = ids(room, GUESSER)[0]
    give(room, g, "call_bullshit")
    bsroom.play_perk(room, g, {"target": room.panel.author}, now=0)
    with pytest.raises(bsroom.Rejected, match="already used"):
        bsroom.play_perk(room, g, {"target": room.panel.author}, now=0)


def test_the_wrong_role_is_refused():
    room = started(6)
    g, t = ids(room, GUESSER)[0], room.panel.author
    give(room, g, "silver_tongue")
    with pytest.raises(bsroom.Rejected, match="speaker"):
        bsroom.play_perk(room, g, {}, now=0)
    give(room, t, "insurance_fraud")
    with pytest.raises(bsroom.Rejected, match="no wager"):
        bsroom.play_perk(room, t, {}, now=0)


# --- the discussion clock -------------------------------------------------

def test_the_ballot_opens_when_the_clock_runs_out():
    room = started(6)
    commit_speakers(room)
    bsroom.open_discussion(room, now=100)
    g = ids(room, GUESSER)[0]
    with pytest.raises(bsroom.Rejected, match="Voting opens"):
        bsroom.guess(room, g, "A", 1)
    talk = config.BS_TALK_SECONDS_PER_SPEAKER * len(room.panel.speakers)
    assert not bsroom.tick(room, now=100 + talk - 1)
    assert bsroom.tick(room, now=100 + talk)
    bsroom.guess(room, g, "A", 1)


def test_the_host_can_add_time_or_end_the_talking():
    room = started(6)
    commit_speakers(room)
    bsroom.open_discussion(room, now=0)
    before = room.talk_ends
    bsroom.extend_talk(room, now=10)
    assert room.talk_ends == before + config.BS_TALK_EXTEND_SECONDS
    bsroom.end_talk(room, now=20)
    assert room.ballot
    with pytest.raises(bsroom.Rejected):
        bsroom.extend_talk(room, now=21)


def test_lock_waits_for_the_ballot():
    room = started(6)
    commit_speakers(room)
    bsroom.open_discussion(room, now=0)
    with pytest.raises(bsroom.Rejected, match="hasn't opened"):
        bsroom.lock(room, force=True)


# --- secrecy --------------------------------------------------------------

def test_a_bluffers_wager_perk_is_seen_by_nobody_else():
    room = started(6, seed=2)
    liar = ids(room, BULLSHITTER)[0]
    give(room, liar, "insurance_fraud")
    bsroom.play_perk(room, liar, {}, now=0)
    for viewer in [*(s for s in room.seats if s != liar), None]:
        view = room.public(viewer, is_host=viewer is None, now=0)
        assert "insurance_fraud" not in played(view)
        card = next(c for c in view["players"] if c["id"] == liar)
        assert card["perk_spent"] is False
    assert "insurance_fraud" in played(room.public(liar, now=0))


def test_perks_never_carry_the_truth_before_the_reveal():
    room = started(7, seed=5)
    g1, g2, g3 = ids(room, GUESSER)[:3]
    give(room, g1, "blackmail")
    give(room, g2, "call_bullshit")
    give(room, room.panel.author, "target_audience")
    bsroom.play_perk(room, g1, {"target": g3}, now=0)
    bsroom.play_perk(room, g2, {"target": room.panel.author}, now=0)
    bsroom.play_perk(room, room.panel.author, {"target": g2}, now=0)
    for viewer in [*(s for s in room.seats if s != room.panel.author), None]:
        text = blob(room.public(viewer, is_host=viewer is None, now=0))
        assert '"truth"' not in text and SECRET_EXPLANATION not in text
        assert '"author"' not in text and '"correct"' not in text


def test_a_blind_faith_lock_stays_dark_until_the_ballot():
    room = started(7, seed=5)
    g = next(s for s in ids(room, GUESSER))
    give(room, g, "blind_faith")
    bsroom.play_perk(room, g, {"option": "A", "wager": 3}, now=0)
    assert room.seats[g].pick == ("A", 3)
    other = next(s for s in ids(room, GUESSER) if s != g)
    card = next(c for c in room.public(other, now=0)["players"] if c["id"] == g)
    assert card["committed"] is False
    assert room.public(None, is_host=True, now=0)["guessers_in"] == 0
    to_ballot(room)
    card = next(c for c in room.public(other, now=0)["players"] if c["id"] == g)
    assert card["committed"] is True


def test_false_witness_is_told_to_the_recruit_and_the_host_only():
    room = started(6, seed=3)
    speaker = room.panel.author
    recruit, outsider = ids(room, GUESSER)[:2]
    give(room, speaker, "false_witness")
    bsroom.play_perk(room, speaker, {"target": recruit}, now=0)
    assert "false_witness" in blob(room.public(recruit, now=0)["perks"])
    assert "false_witness" not in played(room.public(outsider, now=0))
    host = room.public(None, is_host=True, now=0)
    assert host["host_secrets"] and "false_witness" not in blob(host["perks"])


def test_everything_comes_out_at_the_reveal():
    room = started(6, seed=3)
    g = ids(room, GUESSER)[0]
    give(room, g, "call_bullshit")
    bsroom.play_perk(room, g, {"target": room.panel.author}, now=0)
    to_ballot(room)
    guess_rest(room)
    bsroom.lock(room)
    other = next(s for s in room.seats if s != g)
    assert "call_bullshit" not in played(room.public(other, now=0))
    bsroom.reveal(room)
    assert "call_bullshit" in blob(room.public(other, now=0)["perks"])


# --- floor perks and reactions --------------------------------------------

def test_a_floor_perk_waits_out_its_window_then_runs_then_frees_the_ballot():
    room = started(6, seed=4)
    commit_speakers(room)
    bsroom.open_discussion(room, now=0)
    g = ids(room, GUESSER)[0]
    give(room, g, "hot_seat")
    bsroom.play_perk(room, g, {"target": room.panel.speakers[0]}, now=1)
    play = room.plays[0]
    assert play.status == "pending"
    bsroom.tick(room, now=1 + WINDOW)
    assert play.status == "running"
    bsroom.end_talk(room, now=1 + WINDOW + 1)
    assert not room.ballot                      # the Hot Seat still has the floor
    bsroom.tick(room, now=1 + WINDOW + FLOOR)
    assert play.status == "done" and room.ballot


def test_last_words_run_after_the_clock():
    room = started(6, seed=4)
    commit_speakers(room)
    bsroom.open_discussion(room, now=0)
    g = ids(room, GUESSER)[0]
    give(room, g, "last_word")
    bsroom.play_perk(room, g, {}, now=1)
    bsroom.tick(room, now=5)
    assert room.plays[0].status == "queued"
    bsroom.end_talk(room, now=6)
    assert room.plays[0].status == "running" and not room.ballot
    bsroom.floor_done(room, now=7)
    assert room.ballot


def test_mute_button_waits_for_the_openings():
    room = started(6, seed=4)
    commit_speakers(room)
    bsroom.open_discussion(room, now=0)
    g = ids(room, GUESSER)[0]
    give(room, g, "mute_button")
    with pytest.raises(bsroom.Rejected, match="openings"):
        bsroom.play_perk(room, g, {"target": room.panel.author}, now=1)
    bsroom.play_perk(room, g, {"target": room.panel.author}, now=room.openings_end)


def test_objection_cancels_a_floor_perk_and_both_are_spent():
    room = started(6, seed=4)
    commit_speakers(room)
    bsroom.open_discussion(room, now=0)
    g1, g2 = ids(room, GUESSER)[:2]
    give(room, g1, "hot_seat")
    give(room, g2, "objection")
    bsroom.play_perk(room, g1, {"target": room.panel.author}, now=1)
    reactions = room.public(g2, now=2)["perk"]["reactions"]
    assert reactions and reactions[0]["play"] == room.plays[0].id
    bsroom.play_perk(room, g2, {"play": room.plays[0].id}, now=2)
    assert room.plays[0].status == "cancelled"
    assert room.seats[g1].perk_used and room.seats[g2].perk_used
    bsroom.end_talk(room, now=3)
    assert room.ballot


def test_objection_only_answers_floor_perks():
    room = started(6, seed=4)
    g1, g2 = ids(room, GUESSER)[:2]
    give(room, g1, "blackmail")
    give(room, room.panel.author, "objection")
    bsroom.play_perk(room, g1, {"target": g2}, now=0)
    with pytest.raises(bsroom.Rejected, match="speaking"):
        bsroom.play_perk(room, room.panel.author, {"play": room.plays[0].id}, now=1)


def test_blackmail_forces_the_highest_number_left():
    room = started(6, seed=4)
    g1, g2 = ids(room, GUESSER)[:2]
    give(room, g1, "blackmail")
    next(c for c in room.hand(g2) if c.value == room.n - 1).spent = True
    bsroom.play_perk(room, g1, {"target": g2}, now=0)
    assert room.public(g2, now=0)["forced_wager"] == room.n - 2
    to_ballot(room, now=WINDOW)
    bsroom.guess(room, g2, "A", 1)
    assert room.seats[g2].pick == ("A", room.n - 2)


def test_not_my_problem_sends_it_on_once():
    room = started(7, seed=4)
    g1, g2, g3 = ids(room, GUESSER)[:3]
    give(room, g1, "blackmail")
    give(room, g2, "not_my_problem")
    bsroom.play_perk(room, g1, {"target": g2}, now=0)
    options = room.public(g2, now=1)["perk"]["reactions"][0]["targets"]
    assert g1 in options and g3 in options and g2 not in options
    bsroom.play_perk(room, g2, {"play": room.plays[0].id, "target": g3}, now=1)
    assert room.plays[0].target == g3 and room.plays[0].redirected
    give(room, g3, "not_my_problem")
    with pytest.raises(bsroom.Rejected, match="once"):
        bsroom.play_perk(room, g3, {"play": room.plays[0].id, "target": g1}, now=2)


def test_not_my_problem_is_too_late_once_it_lands():
    room = started(6, seed=4)
    g1, g2 = ids(room, GUESSER)[:2]
    give(room, g1, "blackmail")
    give(room, g2, "not_my_problem")
    bsroom.play_perk(room, g1, {"target": g2}, now=0)
    bsroom.tick(room, now=WINDOW)
    with pytest.raises(bsroom.Rejected):
        bsroom.play_perk(room, g2, {"play": room.plays[0].id, "target": g1}, now=WINDOW)


def test_forbidden_words_are_public_and_the_host_counts_slips():
    room = started(6, seed=4)
    g = ids(room, GUESSER)[0]
    speaker = room.panel.speakers[0]
    give(room, g, "forbidden_words")
    bsroom.play_perk(room, g, {"target": speaker, "words": ["I", "remember", "because"]}, now=0)
    play = room.plays[0]
    assert play.words == ("i", "remember", "because")
    bsroom.tick(room, now=WINDOW)
    for _ in range(9):
        bsroom.slip(room, play.id)
    assert play.slips == config.BS_FORBIDDEN_MAX_SLIPS
    to_ballot(room, now=WINDOW)
    guess_rest(room)
    bsroom.lock(room)
    fine = config.BS_FORBIDDEN_MAX_SLIPS * config.BS_PERK_POINTS["forbidden_words"]
    assert ("Forbidden Words", -fine) in room.result.lines[speaker].parts


# --- information ----------------------------------------------------------

def test_read_the_room_votes_last_and_sees_the_counts():
    room = started(7, seed=6)
    reader, *rest = ids(room, GUESSER)
    give(room, reader, "read_the_room")
    bsroom.play_perk(room, reader, {}, now=0)
    to_ballot(room)
    with pytest.raises(bsroom.Rejected, match="Read the Room"):
        bsroom.guess(room, reader, "A", 1)
    for g in rest:
        bsroom.guess(room, g, "B", free_wager(room, g))
    seen = room.public(reader, now=0)["read_the_room"]
    assert seen["waiting"] == 0 and seen["counts"]["B"] == len(rest)
    bsroom.guess(room, reader, "B", 1)


def test_inside_information_sees_one_locked_answer():
    room = started(6, seed=6)
    spy, mark = ids(room, GUESSER)[:2]
    give(room, spy, "inside_information")
    to_ballot(room)
    bsroom.guess(room, mark, "B", 1)
    bsroom.play_perk(room, spy, {"target": mark}, now=0)
    mine = [p for p in room.public(spy, now=0)["perks"] if p["perk"] == "inside_information"]
    assert mine[0]["seen"] == "B"
    assert "inside_information" not in played(room.public(mark, now=0))


def test_the_audit_shows_a_locked_vote_to_everyone_after_its_window():
    room = started(6, seed=6)
    auditor, mark = ids(room, GUESSER)[:2]
    give(room, auditor, "the_audit")
    to_ballot(room)
    bsroom.guess(room, mark, "B", 2)
    bsroom.play_perk(room, auditor, {"target": mark}, now=0)
    assert "exposed" not in blob(room.public(None, is_host=True, now=0)["perks"])
    bsroom.tick(room, now=WINDOW)
    play = room.public(None, is_host=True, now=WINDOW)["perks"][0]
    assert play["exposed"] == {"option": "B", "wager": 2}


# --- the headcount and the reveal -----------------------------------------

def test_kingmaker_settles_a_tie_at_the_headcount():
    room = started(6, seed=7)
    to_ballot(room)
    keys = [o.key for o in room.panel.options]
    for i, seat in enumerate(room.guessers()):
        bsroom.guess(room, seat.id, keys[i % 2], 1)
    bsroom.lock(room)
    tied = room.public(None, is_host=True)["tied"]
    if not tied:
        pytest.skip("this seed did not tie")
    crowner = ids(room, GUESSER)[0]
    give(room, crowner, "kingmaker")
    bsroom.play_perk(room, crowner, {"option": tied[1]}, now=0)
    assert room.result.winner == tied[1]
    bsroom.reveal(room)
    assert room.public(crowner)["truth"]["winner"] == tied[1]


def test_damage_control_wins_some_of_it_back():
    room = started(6, seed=7)
    loser = ids(room, GUESSER)[0]
    give(room, loser, "damage_control")
    to_ballot(room)
    bsroom.guess(room, loser, wrong_key(room), room.n - 1)
    guess_rest(room)
    bsroom.lock(room)
    bsroom.reveal(room)
    before = room.seats[loser].total
    bsroom.play_perk(room, loser, {}, now=0)
    assert room.seats[loser].total == before + 5
    assert ("Damage Control", 5) in room.result.lines[loser].parts


def test_pickpocket_lands_after_its_window_and_can_be_sent_back():
    room = started(7, seed=7)
    thief, victim = ids(room, GUESSER)[:2]
    give(room, thief, "pickpocket")
    give(room, victim, "not_my_problem")
    to_ballot(room)
    bsroom.guess(room, thief, wrong_key(room), 1)
    bsroom.guess(room, victim, wrong_key(room), 1)
    guess_rest(room)
    bsroom.lock(room)
    bsroom.reveal(room, now=0)
    t0, v0 = room.seats[thief].total, room.seats[victim].total
    bsroom.play_perk(room, thief, {"target": victim}, now=0)
    bsroom.play_perk(room, victim, {"play": room.plays[0].id, "target": thief}, now=1)
    bsroom.tick(room, now=1 + WINDOW)
    # Sent back to the thief, who guessed wrong too: they rob themselves.
    assert room.seats[thief].total == t0 and room.seats[victim].total == v0


def test_hostile_takeover_swaps_totals_after_a_right_answer():
    room = started(6, seed=8)
    raider = ids(room, GUESSER)[0]
    mark = room.panel.author
    give(room, raider, "hostile_takeover")
    bsroom.play_perk(room, raider, {"target": mark}, now=0)
    assert "hostile_takeover" not in played(room.public(mark, now=0))
    to_ballot(room)
    guess_rest(room)
    bsroom.lock(room)
    bsroom.reveal(room, now=0)
    raider_total, mark_total = room.seats[raider].total, room.seats[mark].total
    assert room.plays[0].status == "pending"
    bsroom.tick(room, now=WINDOW)
    assert (room.seats[raider].total, room.seats[mark].total) == (mark_total, raider_total)


def test_no_hostile_takeover_in_the_last_round():
    room = started(4, seed=8)
    while room.pending:
        to_ballot(room)
        guess_rest(room)
        bsroom.lock(room)
        bsroom.reveal(room)
        bsroom.next_round(room)
    g = ids(room, GUESSER)[0]
    give(room, g, "hostile_takeover")
    with pytest.raises(bsroom.Rejected, match="last round"):
        bsroom.play_perk(room, g, {"target": room.panel.author}, now=0)


def test_counterfeit_copies_a_revealed_perk():
    room = started(6, seed=3)
    g1, g2 = ids(room, GUESSER)[:2]
    give(room, g1, "call_bullshit")
    give(room, g2, "counterfeit")
    assert room.public(g2, now=0)["perk"]["ready"] is False
    bsroom.play_perk(room, g1, {"target": room.panel.author}, now=0)
    with pytest.raises(bsroom.Rejected, match="Copy"):
        bsroom.play_perk(room, g2, {"copy": "call_bullshit"}, now=0)     # still secret
    to_ballot(room)
    guess_rest(room)
    bsroom.lock(room)
    bsroom.reveal(room)
    bsroom.next_round(room)
    # Whoever guesses this round, other than the player who used it first.
    copier = next(s for s in ids(room, GUESSER) if s != g1)
    give(room, copier, "counterfeit")
    copies = room.public(copier, now=0)["perk"]["copies"]
    assert [c["id"] for c in copies] == ["call_bullshit"]
    bsroom.play_perk(room, copier, {"copy": "call_bullshit", "target": room.panel.author}, now=0)
    assert room.seats[copier].perk_used and room.plays[-1].counterfeit


# --- refunds --------------------------------------------------------------

def test_a_round_that_never_happens_hands_perks_back():
    room = started(6, seed=1)
    g = ids(room, GUESSER)[0]
    give(room, g, "blind_faith")
    bsroom.play_perk(room, g, {"option": "A", "wager": 4}, now=0)
    assert room.seats[g].perk_used and 4 not in room.left(g)
    bsroom.on_disconnect(room, room.panel.author)
    assert not room.seats[g].perk_used and 4 in room.left(g)
    assert room.plays == []


def test_lone_wolf_needs_three_guessers():
    room = started(4, seed=2)
    g = ids(room, GUESSER)[0]
    give(room, g, "lone_wolf")
    if len(room.guessers()) >= 3:
        pytest.skip("this deal has three guessers")
    with pytest.raises(bsroom.Rejected, match="three guessers"):
        bsroom.play_perk(room, g, {}, now=0)


def test_reveal_lines_carry_perk_parts():
    room = started(6, seed=3)
    g = ids(room, GUESSER)[0]
    give(room, g, "double_or_nothing")
    bsroom.play_perk(room, g, {}, now=0)
    to_ballot(room)
    bsroom.guess(room, g, room.panel.correct_key, 3)
    guess_rest(room)
    bsroom.lock(room)
    bsroom.reveal(room)
    card = next(c for c in room.public(g)["players"] if c["id"] == g)
    assert card["line"]["delta"] == 6
    assert ["Double or Nothing", 3] in card["line"]["parts"]


def test_a_small_game_only_drafts_perks_it_can_play():
    room = started(3)
    for seat in room.seats.values():
        assert bsperks.PERK[seat.perk].min_players <= 3
