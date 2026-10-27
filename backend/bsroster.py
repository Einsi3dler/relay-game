"""BULLSHIT QUIZ seats: who joined tonight's game, and the questions each wrote.

People join through the site like any other web game: they sign up or sign
in (backend/accounts.py), open Bullshit Quiz with the host's join code, and
that account gets a seat. A seat is keyed by the account, so a player resumes
from any device by signing in. There is no roster file, no email address and
no invitation link anywhere in this module, and nothing here can send mail
(`tests/test_bsroster_guards.py` checks it never imports the mailer).

Its own small database beside the accounts one. Whether a join is allowed
(the code is right, the lobby is still open) is the room's call in
backend/bsserver.py; this module only stores.

**The question bank.** Everybody writes BS_QUESTIONS_PER_PLAYER questions,
each with its own option count (2, 3 or 4, drawn when they join). The host
reviews every one before the night: tags it (BS_TAGS) and may tidy its
wording. The host never learns who wrote which. `review()` carries no seat,
no name and no slot, and lists questions by their random id, so neither the
order nor the payload says anything about the author.

What a player writes is private until their round's reveal: the prompt, the
real answer, the decoys and the author's identity. `mine` hands a player
their own and nothing else. `questions()` is for the room, which is the only
thing that decides what anybody else may see.

**Settings survive a restart**: the join code and the number of rounds.
The host shares the link a day ahead, and a deploy overnight must not
change the code under it.
"""

from __future__ import annotations

import json
import os
import random
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend import bsquiz, config

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO / "var" / "bullshit.db"

CODE = "join_code"
ROUNDS = "rounds"

_lock = threading.Lock()
_db: sqlite3.Connection | None = None


# --- storage --------------------------------------------------------------

# `seat.option_count` is from the one-question version. It still holds the
# first question's count; `option_counts` (added by _upgrade) holds them all.
SCHEMA = """
CREATE TABLE IF NOT EXISTS seat (
  id            TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL UNIQUE,
  display_name  TEXT NOT NULL,
  option_count  INTEGER NOT NULL,
  joined_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bank_question (
  id           TEXT PRIMARY KEY,
  seat_id      TEXT NOT NULL REFERENCES seat(id) ON DELETE CASCADE,
  slot         INTEGER NOT NULL,
  prompt       TEXT NOT NULL,
  answer       TEXT NOT NULL,
  decoys       TEXT NOT NULL,
  explanation  TEXT NOT NULL DEFAULT '',
  tag          TEXT,
  host_edited  INTEGER NOT NULL DEFAULT 0,
  updated_at   TEXT NOT NULL,
  UNIQUE (seat_id, slot)
);
CREATE TABLE IF NOT EXISTS setting (
  key    TEXT PRIMARY KEY,
  value  TEXT NOT NULL
);
"""


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    global _db
    with _lock:
        if _db is not None:
            return _db
        # Read at connect time, not import time, so the test suite's
        # ":memory:" survives a close() and never falls back to the file.
        target = str(path or os.environ.get("RELAY_BULLSHIT_DB") or DEFAULT_DB)
        if target != ":memory:":
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        _db = sqlite3.connect(target, check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.execute("PRAGMA foreign_keys = ON")
        _retire_invitation_tables(_db)
        _db.executescript(SCHEMA)
        _upgrade(_db)
        _db.commit()
        return _db


def _retire_invitation_tables(db: sqlite3.Connection) -> None:
    """The first version seated people from a roster file of email addresses
    (tables `player` and `question`). Joining is now through accounts, so
    those tables go: dropped when empty, renamed to `legacy_*` when not, so
    no data is ever deleted silently. A `legacy_*` table can be dropped by
    hand once nobody needs it."""
    for old in ("player", "question"):
        if old not in _tables(db):
            continue
        rows = db.execute(f"SELECT COUNT(*) FROM {old}").fetchone()[0]
        if rows:
            db.execute(f"ALTER TABLE {old} RENAME TO legacy_{old}")
        else:
            db.execute(f"DROP TABLE {old}")
    db.commit()


def _upgrade(db: sqlite3.Connection) -> None:
    """From one question per seat to a bank of them, keeping what is there.

    Every seat gets an option count per question: its old one for the first,
    fresh draws for the rest. Each old question moves into the bank as that
    seat's first, untagged, so the host reviews it like any other."""
    columns = {row["name"] for row in db.execute("PRAGMA table_info(seat)")}
    if "option_counts" not in columns:
        db.execute("ALTER TABLE seat ADD COLUMN option_counts TEXT NOT NULL DEFAULT '[]'")
    shuffler = random.Random(secrets.randbits(64))
    for row in db.execute("SELECT id, option_count FROM seat WHERE option_counts = '[]'").fetchall():
        counts = [row["option_count"]] + _draw_counts(shuffler)[1:]
        db.execute("UPDATE seat SET option_counts = ? WHERE id = ?",
                   (json.dumps(counts), row["id"]))
    if "seat_question" in _tables(db):
        for row in db.execute("SELECT * FROM seat_question").fetchall():
            db.execute(
                "INSERT OR IGNORE INTO bank_question (id, seat_id, slot, prompt, answer,"
                " decoys, explanation, updated_at) VALUES (?,?,0,?,?,?,?,?)",
                (_new_id(), row["seat_id"], row["prompt"], row["answer"], row["decoys"],
                 row["explanation"], row["updated_at"]))
        db.execute("DROP TABLE seat_question")


def _tables(db: sqlite3.Connection) -> set[str]:
    return {row["name"] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}


def close() -> None:
    global _db
    with _lock:
        if _db is not None:
            _db.close()
            _db = None


def _conn() -> sqlite3.Connection:
    return _db if _db is not None else connect()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(text: object) -> str:
    return " ".join(str(text or "").split())


def _new_id() -> str:
    """Random, so sorting by it says nothing about who wrote what, or when."""
    return f"q_{uuid4().hex[:12]}"


def _draw_counts(rng: random.Random) -> list[int]:
    """How many options each question asks for: the real answer plus one
    decoy per bullshitter, 2..4, drawn independently. The room size is not
    known yet, so the deal caps it later (bsquiz.panel_size)."""
    return [1 + rng.randint(1, config.BS_MAX_BULLSHITTERS)
            for _ in range(config.BS_QUESTIONS_PER_PLAYER)]


# --- the people -----------------------------------------------------------

@dataclass
class Player:
    id: str
    user_id: str
    display_name: str
    option_counts: tuple[int, ...] = ()   # what the server asked for, per question
    written: int = 0                      # how many of those are in the bank

    @property
    def registered(self) -> bool:
        """Every stored seat belongs to an account, so every one is seated."""
        return True

    @property
    def name(self) -> str:
        return self.display_name

    @property
    def wanted(self) -> int:
        return len(self.option_counts) or config.BS_QUESTIONS_PER_PLAYER

    @property
    def has_question(self) -> bool:
        """Done writing: every question they were asked for is in."""
        return self.written >= self.wanted

    def card(self) -> dict[str, Any]:
        """What any viewer may know about this person at any phase. Note what
        is absent: whether their question has come up yet would narrow down
        who wrote the current one."""
        return {"id": self.id, "name": self.name, "registered": True}


_SELECT = (
    "SELECT s.*, (SELECT COUNT(*) FROM bank_question q WHERE q.seat_id = s.id)"
    " AS written FROM seat s"
)


def _row_to_player(row: sqlite3.Row) -> Player:
    return Player(id=row["id"], user_id=row["user_id"],
                  display_name=row["display_name"],
                  option_counts=tuple(json.loads(row["option_counts"])),
                  written=row["written"])


def all_players() -> list[Player]:
    rows = _conn().execute(_SELECT + " ORDER BY s.joined_at, s.id")
    return [_row_to_player(row) for row in rows]


def by_id(player_id: str) -> Player | None:
    row = _conn().execute(_SELECT + " WHERE s.id = ?", (player_id,)).fetchone()
    return _row_to_player(row) if row else None


def by_user(user_id: str | None) -> Player | None:
    """The seat this account holds, if any."""
    if not user_id:
        return None
    row = _conn().execute(_SELECT + " WHERE s.user_id = ?", (user_id,)).fetchone()
    return _row_to_player(row) if row else None


# --- joining --------------------------------------------------------------

class Refused(ValueError):
    """Something a player (or the host) sent that cannot be accepted. The
    message is for them to read."""


def name_for(first_name: str, last_name: str, username: str,
             taken: set[str]) -> str:
    """The name the game shows: what people on the call know them by.

    First name; with a last initial if somebody already has that first name;
    the username as the last resort. `taken` is lower-cased names in use.
    """
    first = _clean(first_name)[:config.BS_NAME_MAX]
    initial = (last_name or "").strip()[:1].upper()
    for candidate in (first, f"{first} {initial}." if initial else "", username):
        candidate = candidate[:config.BS_NAME_MAX]
        if candidate and candidate.lower() not in taken:
            return candidate
    return username[:config.BS_NAME_MAX]


def join(user_id: str, first_name: str, last_name: str, username: str,
         rng: random.Random | None = None) -> Player:
    """Give this account a seat, or hand back the one it already has.

    The option counts are decided here, once, and never change under
    somebody who has written their decoys.
    """
    existing = by_user(user_id)
    if existing is not None:
        return existing
    counts = _draw_counts(rng or random.Random(secrets.randbits(64)))
    db = _conn()
    with _lock:
        taken = {row["display_name"].lower()
                 for row in db.execute("SELECT display_name FROM seat")}
        seat_id = f"bs_{uuid4().hex[:8]}"
        try:
            db.execute("INSERT INTO seat (id, user_id, display_name, option_count,"
                       " option_counts, joined_at) VALUES (?,?,?,?,?,?)",
                       (seat_id, user_id, name_for(first_name, last_name, username, taken),
                        counts[0], json.dumps(counts), _now()))
            db.commit()
        except sqlite3.IntegrityError:
            pass        # a double tap raced us; the seat exists either way
    return by_user(user_id)


def remove(player_id: str) -> None:
    """Take a seat (and its questions) away, e.g. somebody who joined by
    mistake. Their account can join again with the code."""
    db = _conn()
    with _lock:
        db.execute("DELETE FROM bank_question WHERE seat_id = ?", (player_id,))
        db.execute("DELETE FROM seat WHERE id = ?", (player_id,))
        db.commit()


def clear() -> None:
    """A new game night: every seat and every question goes. Settings stay;
    the caller sets a new code."""
    db = _conn()
    with _lock:
        db.execute("DELETE FROM bank_question")
        db.execute("DELETE FROM seat")
        db.commit()


# --- writing questions ----------------------------------------------------

def _validated(prompt: object, answer: object, decoys: object, explanation: object,
               option_count: int) -> tuple[str, str, list[str], str]:
    """The cleaned fields, or Refused saying what is wrong. The same rules for
    the author writing it and the host tidying it."""
    prompt = _clean(prompt)
    answer = _clean(answer)
    decoys = [_clean(d) for d in (decoys if isinstance(decoys, (list, tuple)) else [])]
    explanation = _clean(explanation)

    if not prompt:
        raise Refused("Write the question first.")
    if len(prompt) > config.BS_QUESTION_MAX:
        raise Refused(f"Keep the question under {config.BS_QUESTION_MAX} characters.")
    if len(decoys) != option_count - 1:
        raise Refused(f"This question has {option_count} options.")
    options = [answer] + decoys
    if not all(options):
        raise Refused("Fill in every option.")
    if any(len(o) > config.BS_OPTION_MAX for o in options):
        raise Refused(f"Keep each option under {config.BS_OPTION_MAX} characters.")
    if len({o.lower() for o in options}) != len(options):
        raise Refused("Two options say the same thing.")
    if len(explanation) > config.BS_EXPLANATION_MAX:
        raise Refused(f"Keep the explanation under {config.BS_EXPLANATION_MAX} characters.")
    return prompt, answer, decoys, explanation


def _same(row: sqlite3.Row, prompt: str, answer: str, decoys: list[str],
          explanation: str) -> bool:
    return (row["prompt"], row["answer"], json.loads(row["decoys"]), row["explanation"]) \
        == (prompt, answer, decoys, explanation)


def submit_question(player_id: str, slot: object, prompt: str, answer: str,
                    decoys: list[str], explanation: str = "") -> None:
    """Store or replace one of this player's questions.

    A change sends it back for review: whatever the host tagged, they tagged
    the old wording. Saving it unchanged keeps the tag. Whether submissions
    are still open is the room's call: the caller checks that the game has
    not started.
    """
    person = by_id(player_id)
    if person is None:
        raise Refused("You are not in this game.")
    if isinstance(slot, bool) or not isinstance(slot, int) \
            or not 0 <= slot < len(person.option_counts):
        raise Refused("That isn't one of your questions.")
    fields = _validated(prompt, answer, decoys, explanation, person.option_counts[slot])
    prompt, answer, decoys, explanation = fields

    db = _conn()
    with _lock:
        row = db.execute("SELECT * FROM bank_question WHERE seat_id = ? AND slot = ?",
                         (person.id, slot)).fetchone()
        if row is None:
            db.execute(
                "INSERT INTO bank_question (id, seat_id, slot, prompt, answer, decoys,"
                " explanation, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (_new_id(), person.id, slot, prompt, answer, json.dumps(decoys),
                 explanation, _now()))
        elif not _same(row, *fields):
            db.execute(
                "UPDATE bank_question SET prompt = ?, answer = ?, decoys = ?,"
                " explanation = ?, tag = NULL, host_edited = 0, updated_at = ?"
                " WHERE id = ?",
                (prompt, answer, json.dumps(decoys), explanation, _now(), row["id"]))
        db.commit()


@dataclass(frozen=True)
class MyQuestion:
    """One of a player's own questions, as their page shows it."""
    slot: int
    question: bsquiz.Question
    host_edited: bool


def mine(player_id: str) -> dict[int, MyQuestion]:
    """This player's questions by slot. Never their tags: what the host
    thought of a question is between the host and the game."""
    rows = _conn().execute("SELECT * FROM bank_question WHERE seat_id = ?", (player_id,))
    return {row["slot"]: MyQuestion(row["slot"], _row_to_question(row), bool(row["host_edited"]))
            for row in rows}


# --- the host's review ----------------------------------------------------

def _question_row(question_id: object) -> sqlite3.Row:
    row = _conn().execute("SELECT * FROM bank_question WHERE id = ?",
                          (str(question_id),)).fetchone()
    if row is None:
        raise Refused("That question isn't in the bank any more.")
    return row


def review() -> list[dict[str, Any]]:
    """Every question for the host to read, tag and tidy. No author, no slot,
    no timestamps, and ordered by the random id: nothing here says who wrote
    it. Host only, and only before the game (backend/bsserver.py)."""
    rows = _conn().execute("SELECT * FROM bank_question ORDER BY id")
    return [{"id": row["id"], "prompt": row["prompt"], "answer": row["answer"],
             "decoys": json.loads(row["decoys"]), "explanation": row["explanation"],
             "tag": row["tag"], "edited": bool(row["host_edited"])}
            for row in rows]


def host_edit(question_id: object, prompt: str, answer: str, decoys: list[str],
              explanation: str = "") -> None:
    """The host tidies a question: same rules, same number of options. The
    tag stands, since the host is the one who set it. Its author sees the new
    wording on their page, because they defend it."""
    row = _question_row(question_id)
    fields = _validated(prompt, answer, decoys, explanation, 1 + len(json.loads(row["decoys"])))
    if _same(row, *fields):
        return
    prompt, answer, decoys, explanation = fields
    db = _conn()
    with _lock:
        db.execute(
            "UPDATE bank_question SET prompt = ?, answer = ?, decoys = ?, explanation = ?,"
            " host_edited = 1, updated_at = ? WHERE id = ?",
            (prompt, answer, json.dumps(decoys), explanation, _now(), row["id"]))
        db.commit()


def set_tag(question_id: object, tag: object) -> None:
    """One of BS_TAGS, or None to send it back to "to review"."""
    if tag is not None and tag not in config.BS_TAGS:
        raise Refused("That isn't a tag.")
    row = _question_row(question_id)
    db = _conn()
    with _lock:
        db.execute("UPDATE bank_question SET tag = ? WHERE id = ?", (tag, row["id"]))
        db.commit()


def _row_to_question(row: sqlite3.Row) -> bsquiz.Question:
    return bsquiz.Question(
        author=row["seat_id"],
        prompt=row["prompt"],
        answer=row["answer"],
        decoys=tuple(json.loads(row["decoys"])),
        explanation=row["explanation"],
        id=row["id"],
        tag=row["tag"] or "",
    )


def questions() -> dict[str, bsquiz.Question]:
    """Every question in the bank, by id. For the room only."""
    return {row["id"]: _row_to_question(row)
            for row in _conn().execute("SELECT * FROM bank_question")}


# --- settings -------------------------------------------------------------

def setting(key: str) -> str | None:
    row = _conn().execute("SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    db = _conn()
    with _lock:
        db.execute("INSERT INTO setting (key, value) VALUES (?, ?)"
                   " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        db.commit()


def rounds() -> int:
    """How many rounds the host wants tonight."""
    saved = setting(ROUNDS)
    return int(saved) if saved and saved.isdigit() else config.BS_ROUNDS_DEFAULT


def set_rounds(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) \
            or not 1 <= value <= config.BS_ROUNDS_MAX:
        raise Refused(f"Pick between 1 and {config.BS_ROUNDS_MAX} rounds.")
    set_setting(ROUNDS, str(value))
    return value
