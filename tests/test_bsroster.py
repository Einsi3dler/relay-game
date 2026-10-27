"""BULLSHIT QUIZ seats and the question bank (backend/bsroster.py).

Seats come from site accounts joining with the host's code; there is no
roster file and no email address anywhere in here. Each seat writes
BS_QUESTIONS_PER_PLAYER questions; the host reviews them without ever
learning who wrote which.
"""

from __future__ import annotations

import json
import random
import sqlite3

import pytest

from backend import bsroster, config

WANTED = config.BS_QUESTIONS_PER_PLAYER


@pytest.fixture(autouse=True)
def fresh_db():
    bsroster.close()
    bsroster.connect()
    yield
    bsroster.close()
    bsroster.connect()


def seated(count: int = 5) -> list[bsroster.Player]:
    rng = random.Random(2)
    return [bsroster.join(f"user{i}", f"Player{i}", "Tester", f"player_{i}", rng=rng)
            for i in range(count)]


def write(person: bsroster.Player, slot: int = 0, **overrides) -> None:
    fields = dict(prompt=f"What did I eat? ({slot})", answer="Rice",
                  decoys=[f"Decoy {i}" for i in range(person.option_counts[slot] - 1)])
    fields.update(overrides)
    bsroster.submit_question(person.id, slot, **fields)


def write_all(person: bsroster.Player) -> None:
    for slot in range(WANTED):
        write(person, slot)


def only_question():
    (q,) = bsroster.questions().values()
    return q


# --- joining --------------------------------------------------------------

def test_join_seats_an_account_once():
    first = bsroster.join("u_grace", "  Grace ", "Hopper", "grace_h")
    again = bsroster.join("u_grace", "Grace", "Hopper", "grace_h")
    assert first.id == again.id and first.name == "Grace"
    assert bsroster.by_user("u_grace").id == first.id
    assert len(bsroster.all_players()) == 1


def test_every_seat_is_asked_for_several_questions():
    person = bsroster.join("u1", "A", "B", "a1")
    assert len(person.option_counts) == WANTED and person.wanted == WANTED


def test_option_counts_are_two_to_four_drawn_per_question():
    rng = random.Random(4)
    people = [bsroster.join(f"u{i}", "A", "B", f"a{i}", rng=rng) for i in range(40)]
    assert {c for p in people for c in p.option_counts} == {2, 3, 4}
    assert any(len(set(p.option_counts)) > 1 for p in people)


def test_same_first_names_get_an_initial():
    assert bsroster.join("u1", "Sam", "Tester", "sam1").name == "Sam"
    assert bsroster.join("u2", "Sam", "Tester", "sam2").name == "Sam T."


@pytest.mark.parametrize("taken, expected", [
    (set(), "Sam"),
    ({"sam"}, "Sam T."),
    ({"sam", "sam t."}, "samt"),
])
def test_name_for(taken, expected):
    assert bsroster.name_for("Sam", "Tester", "samt", taken) == expected


def test_remove_and_rejoin():
    person, *_ = seated(2)
    write_all(person)
    bsroster.remove(person.id)
    assert bsroster.by_user("user0") is None
    assert bsroster.questions() == {}
    assert bsroster.join("user0", "Player0", "Tester", "player_0").id != person.id


def test_clear_empties_everything_but_the_settings():
    for person in seated(3):
        write_all(person)
    bsroster.set_rounds(7)
    bsroster.clear()
    assert bsroster.all_players() == [] and bsroster.questions() == {}
    assert bsroster.rounds() == 7


def test_card_carries_no_question_state():
    person, *_ = seated(3)
    write_all(person)
    assert set(bsroster.by_id(person.id).card()) == {"id", "name", "registered"}


# --- writing --------------------------------------------------------------

def test_done_writing_means_every_question():
    person, *_ = seated()
    for slot in range(WANTED):
        assert not bsroster.by_id(person.id).has_question
        write(person, slot)
        assert bsroster.by_id(person.id).written == slot + 1
    assert bsroster.by_id(person.id).has_question
    assert len(bsroster.questions()) == WANTED


def test_submit_and_replace_one_slot():
    person, *_ = seated()
    write(person, 1)
    q = only_question()
    assert q.author == person.id and q.answer == "Rice" and q.tag == ""
    assert len(q.decoys) == person.option_counts[1] - 1
    write(person, 1, prompt="  Something   else? ")
    again = only_question()
    assert again.prompt == "Something else?" and again.id == q.id


@pytest.mark.parametrize("slot", [-1, WANTED, "0", None, True])
def test_only_your_own_slots(slot):
    person, *_ = seated()
    with pytest.raises(bsroster.Refused, match="one of your questions"):
        bsroster.submit_question(person.id, slot, "Q?", "A", ["B"])


def test_writing_needs_a_seat():
    with pytest.raises(bsroster.Refused, match="not in this game"):
        bsroster.submit_question("bs_nobody", 0, "Q?", "A", ["B"])


@pytest.mark.parametrize("overrides, message", [
    ({"prompt": "   "}, "question"),
    ({"prompt": "x" * 141}, "140"),
    ({"answer": ""}, "every option"),
    ({"answer": "y" * 61}, "60"),
    ({"answer": "decoy 0"}, "same thing"),
    ({"explanation": "z" * 281}, "280"),
])
def test_bad_questions_are_refused(overrides, message):
    person, *_ = seated()
    with pytest.raises(bsroster.Refused, match=message):
        write(person, 0, **overrides)


def test_the_option_count_is_the_servers():
    person, *_ = seated()
    wrong = [f"D{i}" for i in range(person.option_counts[0])]      # one too many
    with pytest.raises(bsroster.Refused, match="options"):
        bsroster.submit_question(person.id, 0, "Q?", "A", wrong)


def test_mine_is_only_mine():
    a, b, *_ = seated()
    write(a, 2)
    assert list(bsroster.mine(a.id)) == [2]
    assert bsroster.mine(a.id)[2].question.author == a.id
    assert bsroster.mine(b.id) == {}


def test_an_authors_change_sends_it_back_for_review():
    person, *_ = seated()
    write(person, 0)
    q = only_question()
    bsroster.set_tag(q.id, "fascinating")
    write(person, 0)                                   # saved unchanged
    assert only_question().tag == "fascinating"
    write(person, 0, answer="Noodles")
    assert only_question().tag == ""


# --- the host's review ----------------------------------------------------

def test_the_review_never_says_who_wrote_it():
    people = seated(4)
    for person in people:
        write_all(person)
    listing = bsroster.review()
    assert len(listing) == 4 * WANTED
    assert all(set(item) == {"id", "prompt", "answer", "decoys", "explanation", "tag", "edited"}
               for item in listing)
    blob = json.dumps(listing)
    for person in people:
        assert person.id not in blob and person.name not in blob and person.user_id not in blob


def test_the_review_order_is_not_the_writing_order():
    people = seated(6)
    written = []
    for slot in range(WANTED):
        for person in people:
            write(person, slot, prompt=f"{person.id}-{slot}?")
            written.append(f"{person.id}-{slot}?")
    shown = [item["prompt"] for item in bsroster.review()]
    assert sorted(shown) == sorted(written) and shown != written
    assert shown == [item["prompt"] for item in bsroster.review()]      # but stable


def test_tags():
    person, *_ = seated()
    write(person, 0)
    q = only_question()
    for tag in config.BS_TAGS:
        bsroster.set_tag(q.id, tag)
        assert only_question().tag == tag
    bsroster.set_tag(q.id, None)
    assert only_question().tag == ""
    with pytest.raises(bsroster.Refused, match="tag"):
        bsroster.set_tag(q.id, "amazing")
    with pytest.raises(bsroster.Refused, match="bank"):
        bsroster.set_tag("q_nope", "mid")


def test_the_host_tidies_a_question_and_its_author_sees_it():
    person, *_ = seated()
    write(person, 0)
    q = only_question()
    bsroster.set_tag(q.id, "mid")
    decoys = [f"Better {i}" for i in range(len(q.decoys))]
    bsroster.host_edit(q.id, "What did I eat for breakfast?", "Rice", decoys, "Every day.")
    tidied = only_question()
    assert tidied.prompt == "What did I eat for breakfast?" and list(tidied.decoys) == decoys
    assert tidied.tag == "mid"                           # the host's own tag stands
    mine = bsroster.mine(person.id)[0]
    assert mine.host_edited and mine.question.explanation == "Every day."
    assert bsroster.review()[0]["edited"] is True


def test_the_host_keeps_the_number_of_options():
    person, *_ = seated()
    write(person, 0)
    q = only_question()
    with pytest.raises(bsroster.Refused, match="options"):
        bsroster.host_edit(q.id, q.prompt, q.answer, list(q.decoys) + ["One more"])
    with pytest.raises(bsroster.Refused, match="same thing"):
        bsroster.host_edit(q.id, q.prompt, q.answer, [q.answer] * len(q.decoys))


# --- settings -------------------------------------------------------------

def test_rounds_default_and_bounds():
    assert bsroster.rounds() == config.BS_ROUNDS_DEFAULT
    assert bsroster.set_rounds(20) == 20 and bsroster.rounds() == 20
    for bad in (0, config.BS_ROUNDS_MAX + 1, "5", 2.5, True):
        with pytest.raises(bsroster.Refused, match="rounds"):
            bsroster.set_rounds(bad)
    assert bsroster.rounds() == 20


def test_settings_survive_a_restart(tmp_path):
    path = tmp_path / "bs.db"
    bsroster.close()
    bsroster.connect(str(path))
    bsroster.set_setting(bsroster.CODE, "K7QM")
    bsroster.set_rounds(15)
    bsroster.close()
    bsroster.connect(str(path))
    assert bsroster.setting(bsroster.CODE) == "K7QM" and bsroster.rounds() == 15


# --- upgrading older databases --------------------------------------------

LEGACY = """
CREATE TABLE player (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,
  token_hash TEXT NOT NULL UNIQUE, option_count INTEGER NOT NULL,
  display_name TEXT, registered_at TEXT, created_at TEXT NOT NULL, user_id TEXT);
CREATE TABLE question (player_id TEXT PRIMARY KEY, prompt TEXT NOT NULL,
  answer TEXT NOT NULL, decoys TEXT NOT NULL, explanation TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL);
"""

ONE_QUESTION = """
CREATE TABLE seat (id TEXT PRIMARY KEY, user_id TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL, option_count INTEGER NOT NULL, joined_at TEXT NOT NULL);
CREATE TABLE seat_question (seat_id TEXT PRIMARY KEY REFERENCES seat(id) ON DELETE CASCADE,
  prompt TEXT NOT NULL, answer TEXT NOT NULL, decoys TEXT NOT NULL,
  explanation TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);
INSERT INTO seat VALUES ('bs_old', 'u_old', 'Old Timer', 3, '2026-10-20T10:00:00');
INSERT INTO seat_question VALUES ('bs_old', 'Kept?', 'Yes', '["No", "Maybe"]', 'Why', 't');
"""

TABLES = {"seat", "bank_question", "setting"}


def _tables(db) -> set[str]:
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _open(tmp_path, script: str) -> None:
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(script)
    old.commit()
    old.close()
    bsroster.close()
    bsroster.connect(str(path))


def test_empty_invitation_tables_are_dropped(tmp_path):
    _open(tmp_path, LEGACY)
    assert _tables(bsroster._conn()) == TABLES


def test_invitation_tables_with_rows_are_kept_aside(tmp_path):
    _open(tmp_path, LEGACY + "INSERT INTO player VALUES"
                             " ('bs_1','a@example.test','h',2,NULL,NULL,'t',NULL);")
    tables = _tables(bsroster._conn())
    assert {"legacy_player"} | TABLES <= tables
    assert "player" not in tables and "question" not in tables


def test_a_one_question_database_moves_into_the_bank(tmp_path):
    _open(tmp_path, ONE_QUESTION)
    assert _tables(bsroster._conn()) == TABLES
    person = bsroster.by_user("u_old")
    assert person.option_counts[0] == 3 and len(person.option_counts) == WANTED
    assert person.written == 1 and not person.has_question
    q = only_question()
    assert (q.author, q.prompt, q.decoys, q.tag) == ("bs_old", "Kept?", ("No", "Maybe"), "")
    assert list(bsroster.mine("bs_old")) == [0]
    # And it can carry on: the new seats and questions work beside it.
    write(person, 1)
    assert bsroster.by_user("u_old").written == 2
    assert bsroster.join("u_new", "New", "One", "new1").written == 0
