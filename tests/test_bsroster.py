"""BULLSHIT QUIZ roster and question store (backend/bsroster.py).

Addresses here are on example.test, a reserved domain: no fixture in this
repository may be taken from the real roster (tests/test_bsroster_guards.py).
"""

from __future__ import annotations

import json
import random

import pytest

from backend import bsroster


@pytest.fixture(autouse=True)
def fresh_db():
    bsroster.close()
    bsroster.connect()
    bsroster._fresh_tokens.clear()
    yield
    bsroster.close()
    bsroster.connect()


def write_roster(tmp_path, count: int):
    path = tmp_path / "roster.json"
    path.write_text(json.dumps({"participants": [
        {"email": f"player{i}@example.test"} for i in range(count)]}))
    return path


def seated(tmp_path, count: int = 5) -> list[tuple[bsroster.Player, str]]:
    """Import, register everybody, and return (player, raw token) pairs."""
    bsroster.import_roster(write_roster(tmp_path, count), rng=random.Random(2))
    out = []
    for i, person in enumerate(bsroster.all_players()):
        raw = bsroster._fresh_tokens[person.id]
        out.append((bsroster.claim(raw, f"user{i}", f"Player{i}", "Tester", f"player_{i}"), raw))
    return out


def full_question(person: bsroster.Player, raw: str, **overrides):
    fields = dict(prompt="What did I eat?", answer="Rice",
                  decoys=[f"Decoy {i}" for i in range(person.option_count - 1)])
    fields.update(overrides)
    return bsroster.submit_question(person.id, **fields)


# --- import ---------------------------------------------------------------

def test_import_is_idempotent_and_keeps_option_counts(tmp_path):
    path = write_roster(tmp_path, 6)
    assert bsroster.import_roster(path) == {"added": 6, "total": 6}
    before = {p.id: p.option_count for p in bsroster.all_players()}
    assert bsroster.import_roster(path) == {"added": 0, "total": 6}
    after = {p.id: p.option_count for p in bsroster.all_players()}
    assert before == after


def test_option_counts_are_two_to_four(tmp_path):
    bsroster.import_roster(write_roster(tmp_path, 30), rng=random.Random(4))
    assert {p.option_count for p in bsroster.all_players()} == {2, 3, 4}


def test_a_tiny_roster_still_gets_a_legal_count(tmp_path):
    bsroster.import_roster(write_roster(tmp_path, 2))
    assert {p.option_count for p in bsroster.all_players()} == {2}


def test_links_open_their_own_seat(tmp_path):
    bsroster.import_roster(write_roster(tmp_path, 3))
    for person in bsroster.all_players():
        raw = bsroster._fresh_tokens[person.id]
        assert bsroster.by_token(raw).id == person.id
    assert bsroster.by_token("nope") is None
    assert bsroster.by_token("") is None


def test_reissue_kills_the_old_link(tmp_path):
    bsroster.import_roster(write_roster(tmp_path, 1))
    person = bsroster.all_players()[0]
    old = bsroster._fresh_tokens[person.id]
    new = bsroster.reissue(person.id)
    assert bsroster.by_token(old) is None
    assert bsroster.by_token(new).id == person.id
    assert bsroster.link_for(person.id, "https://x.test/").endswith(f"/bullshit/{new}")


# --- claiming a seat ------------------------------------------------------

def test_claim_ties_a_seat_to_an_account(tmp_path):
    bsroster.import_roster(write_roster(tmp_path, 2))
    raw = bsroster._fresh_tokens[bsroster.all_players()[0].id]
    seat = bsroster.claim(raw, "u_grace", "  Grace ", "Hopper", "grace_h")
    assert seat.name == "Grace" and seat.user_id == "u_grace" and seat.registered
    assert bsroster.by_user("u_grace").id == seat.id
    assert bsroster.claim(raw, "u_grace", "Grace", "Hopper", "grace_h").id == seat.id


def test_claim_refusals(tmp_path):
    bsroster.import_roster(write_roster(tmp_path, 2))
    a, b = (bsroster._fresh_tokens[p.id] for p in bsroster.all_players())
    bsroster.claim(a, "u1", "Ann", "One", "ann")
    with pytest.raises(bsroster.Refused, match="another account"):
        bsroster.claim(a, "u2", "Bob", "Two", "bob")
    with pytest.raises(bsroster.Refused, match="already has a seat"):
        bsroster.claim(b, "u1", "Ann", "One", "ann")
    with pytest.raises(bsroster.Refused, match="not valid"):
        bsroster.claim("bad", "u3", "Cy", "Three", "cy")


@pytest.mark.parametrize("taken, expected", [
    (set(), "Sam"),
    ({"sam"}, "Sam T."),
    ({"sam", "sam t."}, "samt"),
])
def test_name_for(taken, expected):
    assert bsroster.name_for("Sam", "Tester", "samt", taken) == expected


def test_unclaimed_seats_are_not_registered(tmp_path):
    bsroster.import_roster(write_roster(tmp_path, 1))
    assert not bsroster.all_players()[0].registered


def test_user_id_column_is_added_to_an_old_store(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE player (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,
          token_hash TEXT NOT NULL UNIQUE, option_count INTEGER NOT NULL,
          display_name TEXT, registered_at TEXT, created_at TEXT NOT NULL);""")
    old.execute("INSERT INTO player VALUES ('bs_1','a@example.test','h',2,NULL,NULL,'t')")
    old.commit()
    old.close()
    bsroster.close()
    bsroster.connect(str(path))
    assert bsroster.by_id("bs_1").user_id is None


def test_card_carries_no_question_state(tmp_path):
    (person, raw), *_ = seated(tmp_path, 3)
    full_question(person, raw)
    assert set(bsroster.by_id(person.id).card()) == {"id", "name", "registered"}


# --- questions ------------------------------------------------------------

def test_submit_and_replace(tmp_path):
    (person, raw), *_ = seated(tmp_path)
    q = full_question(person, raw)
    assert q.author == person.id and q.answer == "Rice"
    assert len(q.decoys) == person.option_count - 1
    assert bsroster.by_id(person.id).has_question

    q2 = full_question(person, raw, prompt="  Something   else? ")
    assert q2.prompt == "Something else?"
    assert list(bsroster.questions()) == [person.id]


def test_must_claim_before_writing(tmp_path):
    bsroster.import_roster(write_roster(tmp_path, 3))
    person = bsroster.all_players()[0]
    with pytest.raises(bsroster.Refused, match="Claim"):
        bsroster.submit_question(person.id, "Q?", "A", ["B"])


@pytest.mark.parametrize("overrides, message", [
    ({"prompt": "   "}, "question"),
    ({"prompt": "x" * 141}, "140"),
    ({"answer": ""}, "every option"),
    ({"answer": "y" * 61}, "60"),
    ({"answer": "decoy 0"}, "same thing"),
    ({"explanation": "z" * 281}, "280"),
])
def test_bad_questions_are_refused(tmp_path, overrides, message):
    (person, raw), *_ = seated(tmp_path)
    with pytest.raises(bsroster.Refused, match=message):
        full_question(person, raw, **overrides)


def test_the_option_count_is_the_servers(tmp_path):
    (person, raw), *_ = seated(tmp_path)
    wrong = [f"D{i}" for i in range(person.option_count)]      # one too many
    with pytest.raises(bsroster.Refused, match="asked for"):
        bsroster.submit_question(person.id, "Q?", "A", wrong)


def test_my_question_is_only_mine(tmp_path):
    (a, raw_a), (b, raw_b), *_ = seated(tmp_path)
    full_question(a, raw_a)
    assert bsroster.my_question(a.id).author == a.id
    assert bsroster.my_question(b.id) is None
