"""BULLSHIT QUIZ seats: who joined tonight's game, and the question each wrote.

People join through the site like any other web game: they sign up or sign
in (backend/accounts.py), open Bullshit Quiz with the host's join code, and
that account gets a seat. A seat is keyed by the account, so a player resumes
from any device by signing in. There is no roster file, no email address and
no invitation link anywhere in this module, and nothing here can send mail
(`tests/test_bsroster_guards.py` checks it never imports the mailer).

Its own small database beside the accounts one. Whether a join is allowed
(the code is right, the lobby is still open) is the room's call in
backend/bsserver.py; this module only stores.

What a player writes is private until their round's reveal: the prompt, the
real answer, the decoys and the author's identity. `my_question` hands a
player their own and nothing else. `questions()` is for the room, which is
the only thing that decides what anybody else may see.
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

_lock = threading.Lock()
_db: sqlite3.Connection | None = None


# --- storage --------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS seat (
  id            TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL UNIQUE,
  display_name  TEXT NOT NULL,
  option_count  INTEGER NOT NULL,
  joined_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seat_question (
  seat_id     TEXT PRIMARY KEY REFERENCES seat(id) ON DELETE CASCADE,
  prompt      TEXT NOT NULL,
  answer      TEXT NOT NULL,
  decoys      TEXT NOT NULL,
  explanation TEXT NOT NULL DEFAULT '',
  updated_at  TEXT NOT NULL
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
        _db.commit()
        return _db


def _retire_invitation_tables(db: sqlite3.Connection) -> None:
    """The first version seated people from a roster file of email addresses
    (tables `player` and `question`). Joining is now through accounts, so
    those tables go: dropped when empty, renamed to `legacy_*` when not, so
    no data is ever deleted silently. A `legacy_*` table can be dropped by
    hand once nobody needs it."""
    tables = {row["name"] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    for old in ("player", "question"):
        if old not in tables:
            continue
        rows = db.execute(f"SELECT COUNT(*) FROM {old}").fetchone()[0]
        if rows:
            db.execute(f"ALTER TABLE {old} RENAME TO legacy_{old}")
        else:
            db.execute(f"DROP TABLE {old}")
    db.commit()


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


# --- the people -----------------------------------------------------------

@dataclass
class Player:
    id: str
    user_id: str
    display_name: str
    option_count: int          # what the server asked them to write: 2, 3 or 4
    has_question: bool

    @property
    def registered(self) -> bool:
        """Every stored seat belongs to an account, so every one is seated."""
        return True

    @property
    def name(self) -> str:
        return self.display_name

    def card(self) -> dict[str, Any]:
        """What any viewer may know about this person at any phase. Note what
        is absent: whether their question has come up yet would narrow down
        who wrote the current one."""
        return {"id": self.id, "name": self.name, "registered": True}


_SELECT = (
    "SELECT s.*, (q.seat_id IS NOT NULL) AS has_question"
    " FROM seat s LEFT JOIN seat_question q ON q.seat_id = s.id"
)


def _row_to_player(row: sqlite3.Row) -> Player:
    return Player(id=row["id"], user_id=row["user_id"],
                  display_name=row["display_name"],
                  option_count=row["option_count"],
                  has_question=bool(row["has_question"]))


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
    """Something a player sent that cannot be accepted. The message is for
    them to read."""


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

    The option count (2, 3 or 4) is decided here, once, and never changes
    under somebody who has written their decoys. The room size is not known
    yet, so it is uniform over 2..4 and the deal caps it to what the room
    allows (bsquiz.panel_size).
    """
    existing = by_user(user_id)
    if existing is not None:
        return existing
    shuffler = rng or random.Random(secrets.randbits(64))
    count = 1 + shuffler.randint(1, config.BS_MAX_BULLSHITTERS)
    db = _conn()
    with _lock:
        taken = {row["display_name"].lower()
                 for row in db.execute("SELECT display_name FROM seat")}
        seat_id = f"bs_{uuid4().hex[:8]}"
        try:
            db.execute("INSERT INTO seat (id, user_id, display_name, option_count,"
                       " joined_at) VALUES (?,?,?,?,?)",
                       (seat_id, user_id, name_for(first_name, last_name, username, taken),
                        count, _now()))
            db.commit()
        except sqlite3.IntegrityError:
            pass        # a double tap raced us; the seat exists either way
    return by_user(user_id)


def remove(player_id: str) -> None:
    """Take a seat (and its question) away, e.g. somebody who joined by
    mistake. Their account can join again with the code."""
    db = _conn()
    with _lock:
        db.execute("DELETE FROM seat_question WHERE seat_id = ?", (player_id,))
        db.execute("DELETE FROM seat WHERE id = ?", (player_id,))
        db.commit()


def clear() -> None:
    """A new game night: every seat and every question goes."""
    db = _conn()
    with _lock:
        db.execute("DELETE FROM seat_question")
        db.execute("DELETE FROM seat")
        db.commit()


# --- the questions --------------------------------------------------------

def submit_question(player_id: str, prompt: str, answer: str,
                    decoys: list[str], explanation: str = "") -> bsquiz.Question:
    """Store or replace this player's question.

    Whether submissions are still open is the room's call, not this module's:
    the caller checks that the game has not started before calling.
    """
    person = by_id(player_id)
    if person is None:
        raise Refused("You are not in this game.")

    prompt = _clean(prompt)
    answer = _clean(answer)
    decoys = [_clean(d) for d in (decoys or [])]
    explanation = _clean(explanation)

    if not prompt:
        raise Refused("Write your question first.")
    if len(prompt) > config.BS_QUESTION_MAX:
        raise Refused(f"Keep the question under {config.BS_QUESTION_MAX} characters.")
    if len(decoys) != person.option_count - 1:
        raise Refused(f"You were asked for {person.option_count} options.")
    options = [answer] + decoys
    if not all(options):
        raise Refused("Fill in every option.")
    if any(len(o) > config.BS_OPTION_MAX for o in options):
        raise Refused(f"Keep each option under {config.BS_OPTION_MAX} characters.")
    if len({o.lower() for o in options}) != len(options):
        raise Refused("Two options say the same thing.")
    if len(explanation) > config.BS_EXPLANATION_MAX:
        raise Refused(f"Keep the explanation under {config.BS_EXPLANATION_MAX} characters.")

    db = _conn()
    with _lock:
        db.execute(
            "INSERT INTO seat_question (seat_id, prompt, answer, decoys,"
            " explanation, updated_at) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(seat_id) DO UPDATE SET prompt = excluded.prompt,"
            " answer = excluded.answer, decoys = excluded.decoys,"
            " explanation = excluded.explanation,"
            " updated_at = excluded.updated_at",
            (person.id, prompt, answer, json.dumps(decoys), explanation, _now()),
        )
        db.commit()
    return my_question(person.id)


def _row_to_question(row: sqlite3.Row) -> bsquiz.Question:
    return bsquiz.Question(
        author=row["seat_id"],
        prompt=row["prompt"],
        answer=row["answer"],
        decoys=tuple(json.loads(row["decoys"])),
        explanation=row["explanation"],
    )


def my_question(player_id: str) -> bsquiz.Question | None:
    row = _conn().execute("SELECT * FROM seat_question WHERE seat_id = ?",
                          (player_id,)).fetchone()
    return _row_to_question(row) if row else None


def questions() -> dict[str, bsquiz.Question]:
    """Every submitted question, by author. For the room only."""
    return {row["seat_id"]: _row_to_question(row)
            for row in _conn().execute("SELECT * FROM seat_question")}
