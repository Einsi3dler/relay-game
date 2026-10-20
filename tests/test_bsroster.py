"""BULLSHIT QUIZ seats and questions (backend/bsroster.py).

Seats come from site accounts joining with the host's code; there is no
roster file and no email address anywhere in here.
"""

from __future__ import annotations

import random
import sqlite3

import pytest

from backend import bsroster


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


def full_question(person: bsroster.Player, **overrides):
    fields = dict(prompt="What did I eat?", answer="Rice",
                  decoys=[f"Decoy {i}" for i in range(person.option_count - 1)])
    fields.update(overrides)
    return bsroster.submit_question(person.id, **fields)


# --- joining --------------------------------------------------------------

def test_join_seats_an_account_once():
    first = bsroster.join("u_grace", "  Grace ", "Hopper", "grace_h")
    again = bsroster.join("u_grace", "Grace", "Hopper", "grace_h")
    assert first.id == again.id and first.name == "Grace"
    assert bsroster.by_user("u_grace").id == first.id
    assert len(bsroster.all_players()) == 1


def test_option_counts_are_two_to_four():
    rng = random.Random(4)
    counts = {bsroster.join(f"u{i}", "A", "B", f"a{i}", rng=rng).option_count
              for i in range(40)}
    assert counts == {2, 3, 4}


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
    full_question(person)
    bsroster.remove(person.id)
    assert bsroster.by_user("user0") is None
    assert person.id not in bsroster.questions()
    assert bsroster.join("user0", "Player0", "Tester", "player_0").id != person.id


def test_clear_empties_everything():
    for person in seated(3):
        full_question(person)
    bsroster.clear()
    assert bsroster.all_players() == [] and bsroster.questions() == {}


def test_card_carries_no_question_state():
    person, *_ = seated(3)
    full_question(person)
    assert set(bsroster.by_id(person.id).card()) == {"id", "name", "registered"}


# --- questions ------------------------------------------------------------

def test_submit_and_replace():
    person, *_ = seated()
    q = full_question(person)
    assert q.author == person.id and q.answer == "Rice"
    assert len(q.decoys) == person.option_count - 1
    assert bsroster.by_id(person.id).has_question
    q2 = full_question(person, prompt="  Something   else? ")
    assert q2.prompt == "Something else?"
    assert list(bsroster.questions()) == [person.id]


def test_writing_needs_a_seat():
    with pytest.raises(bsroster.Refused, match="not in this game"):
        bsroster.submit_question("bs_nobody", "Q?", "A", ["B"])


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
        full_question(person, **overrides)


def test_the_option_count_is_the_servers():
    person, *_ = seated()
    wrong = [f"D{i}" for i in range(person.option_count)]      # one too many
    with pytest.raises(bsroster.Refused, match="asked for"):
        bsroster.submit_question(person.id, "Q?", "A", wrong)


def test_my_question_is_only_mine():
    a, b, *_ = seated()
    full_question(a)
    assert bsroster.my_question(a.id).author == a.id
    assert bsroster.my_question(b.id) is None


# --- retiring the invitation tables ---------------------------------------

LEGACY = """
CREATE TABLE player (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,
  token_hash TEXT NOT NULL UNIQUE, option_count INTEGER NOT NULL,
  display_name TEXT, registered_at TEXT, created_at TEXT NOT NULL, user_id TEXT);
CREATE TABLE question (player_id TEXT PRIMARY KEY, prompt TEXT NOT NULL,
  answer TEXT NOT NULL, decoys TEXT NOT NULL, explanation TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL);
"""


def _tables(db) -> set[str]:
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_empty_invitation_tables_are_dropped(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(LEGACY)
    old.close()
    bsroster.close()
    bsroster.connect(str(path))
    assert _tables(bsroster._conn()) == {"seat", "seat_question"}


def test_invitation_tables_with_rows_are_kept_aside(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(LEGACY)
    old.execute("INSERT INTO player VALUES ('bs_1','a@example.test','h',2,NULL,NULL,'t',NULL)")
    old.commit()
    old.close()
    bsroster.close()
    bsroster.connect(str(path))
    tables = _tables(bsroster._conn())
    assert {"legacy_player", "seat", "seat_question"} <= tables
    assert "player" not in tables and "question" not in tables
