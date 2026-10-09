"""The ROLL CALL roster: who is playing, and the questions they wrote.

A participant is not a `User`. They have no password, no session and no
account; they have an email address somebody collected on a form, three
sentences they wrote about themselves, and a link. So this is its own small
database beside `backend/accounts.py` rather than three more tables inside it,
for the same reason a duel room is not a Match: two small stores cannot drift
into each other the way one shared keyspace can.

**This module does not import `backend/mailer.py`, and must never import it.**
`tests/test_rollcall_no_send.py` fails if it ever does, directly or through
anything it imports. Invitation links come out of `links`, which prints them
for a human to look at. There is deliberately no code path from here to an
envelope; see docs/ROLL_CALL_DO_NOT_SEND.md for why that is the guarantee
rather than a flag or a dry-run default.

The roster file itself is `var/rollcall/roster.json`, gitignored, real
people's data. Nothing in here may copy a line of it into this repository.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import secrets
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend import config

REPO = Path(__file__).resolve().parent.parent
ROSTER_PATH = Path(os.environ.get("RELAY_ROLLCALL_ROSTER",
                                  REPO / "var" / "rollcall" / "roster.json"))
DB_PATH = Path(os.environ.get("RELAY_ROLLCALL_DB",
                              REPO / "var" / "rollcall.db"))

# Shown above the quote, so the room knows which of the three questions this
# answer was written for. Falls back to the key if the roster omits one.
DEFAULT_CATEGORIES = {
    "fact": "A random fact",
    "career": "The most impressive thing",
    "vision": "In 10 years",
}

# The order the form asked them in. Used wherever answers are shown as a set,
# because sorting the keys alphabetically put the career answer above the
# random fact and read as though the page had shuffled their own form back at
# them. Irrelevant to the questions themselves, which are shuffled anyway.
CATEGORY_ORDER = ("fact", "career", "vision")


def category_rank(key: str) -> int:
    return CATEGORY_ORDER.index(key) if key in CATEGORY_ORDER else len(CATEGORY_ORDER)


def ordered_answers(answers: dict[str, str]) -> list[tuple[str, str]]:
    return sorted(answers.items(), key=lambda kv: (category_rank(kv[0]), kv[0]))

_lock = threading.Lock()
_db: sqlite3.Connection | None = None


# --- storage --------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS participant (
  id            TEXT PRIMARY KEY,
  email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
  token_hash    TEXT NOT NULL UNIQUE,
  display_name  TEXT,
  avatar        TEXT,
  registered_at TEXT,
  created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS answer (
  id             TEXT PRIMARY KEY,
  participant_id TEXT NOT NULL REFERENCES participant(id),
  category       TEXT NOT NULL,
  body           TEXT NOT NULL,
  UNIQUE (participant_id, category)
);
"""


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    global _db
    with _lock:
        if _db is not None:
            return _db
        target = Path(path) if path else DB_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        _db = sqlite3.connect(str(target), check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.executescript(SCHEMA)
        _db.commit()
        return _db


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


def _digest(raw: str) -> str:
    """The token is 192 bits of `secrets`, so plain SHA-256 with no salt is
    right: there is no dictionary to precompute. Same call accounts.py makes."""
    return hashlib.sha256(raw.encode()).hexdigest()


# --- the people -----------------------------------------------------------

@dataclass
class Participant:
    id: str
    email: str
    display_name: str | None
    avatar: str | None
    registered_at: str | None
    answers: dict[str, str] = field(default_factory=dict)

    @property
    def registered(self) -> bool:
        return bool(self.display_name)

    def card(self) -> dict[str, Any]:
        """What every viewer may know about this person at any phase: who they
        are, never what they wrote."""
        return {
            "id": self.id,
            "name": self.display_name or "",
            "avatar": self.avatar,
            "registered": self.registered,
        }


def _row_to_participant(row: sqlite3.Row, answers: dict[str, str]) -> Participant:
    return Participant(
        id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        avatar=row["avatar"],
        registered_at=row["registered_at"],
        answers=answers,
    )


def all_participants() -> list[Participant]:
    db = _conn()
    answers: dict[str, dict[str, str]] = {}
    for row in db.execute("SELECT participant_id, category, body FROM answer"):
        answers.setdefault(row["participant_id"], {})[row["category"]] = row["body"]
    return [
        _row_to_participant(row, answers.get(row["id"], {}))
        for row in db.execute("SELECT * FROM participant ORDER BY created_at, id")
    ]


def by_id(participant_id: str) -> Participant | None:
    db = _conn()
    row = db.execute("SELECT * FROM participant WHERE id = ?",
                     (participant_id,)).fetchone()
    if row is None:
        return None
    answers = {
        r["category"]: r["body"]
        for r in db.execute("SELECT category, body FROM answer WHERE participant_id = ?",
                            (participant_id,))
    }
    return _row_to_participant(row, answers)


def by_token(raw_token: str) -> Participant | None:
    """Whoever holds the link is that person. That is right for a party game
    among colleagues and wrong for anything else; it is not authentication."""
    if not raw_token:
        return None
    row = _conn().execute("SELECT id FROM participant WHERE token_hash = ?",
                          (_digest(raw_token),)).fetchone()
    return by_id(row["id"]) if row else None


# --- import ---------------------------------------------------------------

def load_roster_file(path: Path | None = None) -> dict[str, Any]:
    target = Path(path) if path else ROSTER_PATH
    if not target.exists():
        raise FileNotFoundError(f"no roster at {target}")
    return json.loads(target.read_text(encoding="utf-8"))


def import_roster(path: Path | None = None) -> dict[str, int]:
    """Read the roster file into the database, idempotently.

    Keyed on email: re-running updates the answers and leaves tokens, display
    names and faces alone, so it is safe to run twice five minutes before the
    session. Nobody is ever deleted here; a person removed from the file keeps
    their seat until somebody says otherwise out loud.
    """
    roster = load_roster_file(path)
    db = _conn()
    added = updated = 0

    with _lock:
        for person in roster["participants"]:
            email = person["email"].strip()
            row = db.execute("SELECT id FROM participant WHERE email = ?",
                             (email,)).fetchone()
            if row is None:
                pid = f"rc_{uuid4().hex[:8]}"
                raw = secrets.token_urlsafe(config.QUIZ_TOKEN_BYTES)
                db.execute(
                    "INSERT INTO participant (id, email, token_hash, created_at)"
                    " VALUES (?,?,?,?)",
                    (pid, email, _digest(raw), _now()),
                )
                # The raw token is written once, here, and never again. It
                # lives in the link and nowhere else; `reissue` is the only way
                # to get a fresh one.
                _fresh_tokens[pid] = raw
                added += 1
            else:
                pid = row["id"]
                updated += 1

            for category, body in person["answers"].items():
                db.execute(
                    "INSERT INTO answer (id, participant_id, category, body)"
                    " VALUES (?,?,?,?)"
                    " ON CONFLICT(participant_id, category)"
                    " DO UPDATE SET body = excluded.body",
                    (uuid4().hex[:12], pid, category, body),
                )
        db.commit()

    return {"added": added, "updated": updated,
            "total": len(roster["participants"])}


# Raw tokens minted during this process, so `links` can print them right after
# an import. Deliberately not persisted: a stored raw token is a stored
# password.
_fresh_tokens: dict[str, str] = {}


def reissue(participant_id: str) -> str:
    """A new link for somebody who lost theirs. The old one stops working."""
    raw = secrets.token_urlsafe(config.QUIZ_TOKEN_BYTES)
    db = _conn()
    with _lock:
        db.execute("UPDATE participant SET token_hash = ? WHERE id = ?",
                   (_digest(raw), participant_id))
        db.commit()
    _fresh_tokens[participant_id] = raw
    return raw


def link_for(participant_id: str, base_url: str) -> str | None:
    raw = _fresh_tokens.get(participant_id)
    return f"{base_url.rstrip('/')}/rollcall/{raw}" if raw else None


# --- registration ---------------------------------------------------------

class RegistrationError(ValueError):
    pass


def register(raw_token: str, name: str, avatar: str | None) -> Participant:
    """Attach a display name and a face to a seat.

    The two are one object from here on: together they are the card the room
    taps, and nothing ever draws one without the other.
    """
    person = by_token(raw_token)
    if person is None:
        raise RegistrationError("That link is not valid.")

    cleaned = " ".join(str(name).split())
    if not (config.QUIZ_NAME_MIN <= len(cleaned) <= config.QUIZ_NAME_MAX):
        raise RegistrationError(
            f"Pick a name between {config.QUIZ_NAME_MIN} and "
            f"{config.QUIZ_NAME_MAX} characters."
        )

    db = _conn()
    clash = db.execute(
        "SELECT id FROM participant WHERE display_name = ? COLLATE NOCASE"
        " AND id != ?", (cleaned, person.id)).fetchone()
    if clash is not None:
        raise RegistrationError("Somebody is already going by that. Try another.")

    code = avatar if _valid_avatar(avatar) else None
    with _lock:
        db.execute(
            "UPDATE participant SET display_name = ?, avatar = ?,"
            " registered_at = ? WHERE id = ?",
            (cleaned, code, _now(), person.id),
        )
        db.commit()
    return by_id(person.id)


def _valid_avatar(code: str | None) -> bool:
    """Same shape check the account page applies. An unreadable code becomes a
    seeded face at draw time rather than reaching a renderer."""
    if not isinstance(code, str) or len(code) != len(config.AVATAR_SLOTS):
        return False
    return all(ch in "0123456789abcdefghijklmnopqrstuvwxyz" for ch in code.lower())


def suggested_name(email: str) -> str:
    """A default the room can place.

    A name is only useful in this game if people recognise it: a handle nobody
    knows makes that person unguessable and quietly ruins the rounds they are
    the answer to. The local part is usually close enough to start from.
    """
    local = email.split("@", 1)[0]
    words = [w for w in local.replace("_", ".").replace("-", ".").split(".") if w]
    pretty = " ".join(w[:1].upper() + w[1:] for w in words if not w.isdigit())
    return (pretty or local)[:config.QUIZ_NAME_MAX]


# --- the questions --------------------------------------------------------

@dataclass
class Question:
    id: str
    category: str      # the key, e.g. "fact"
    label: str         # what the screen shows above the quote
    body: str          # their sentence, verbatim
    subject: str       # participant id

    def public(self) -> dict[str, Any]:
        """Everything a player may see while the question is live. Note what is
        absent: `subject`. It is the answer."""
        return {"id": self.id, "category": self.category,
                "label": self.label, "body": self.body}


def build_questions(participants: list[Participant],
                    categories: dict[str, str] | None = None,
                    rng: random.Random | None = None) -> list[Question]:
    """Every answer becomes one question, all categories shuffled together.

    Shuffled with `secrets` entropy by default, so the order is not derivable
    by a client. Every answer is used, including the two that identify nobody;
    that is a decision, not an oversight.
    """
    labels = {**DEFAULT_CATEGORIES, **(categories or {})}
    shuffler = rng or random.Random(secrets.randbits(64))

    questions = [
        Question(id=f"q_{uuid4().hex[:8]}", category=category,
                 label=labels.get(category, category),
                 body=body, subject=person.id)
        for person in participants
        for category, body in ordered_answers(person.answers)
    ]
    shuffler.shuffle(questions)
    return questions


# --- the command line -----------------------------------------------------
# `python -m backend.rollcall <command>`. Note what is not here: anything that
# sends. `links` prints to a terminal for a human to read, and that is the
# whole guarantee described in docs/ROLL_CALL_DO_NOT_SEND.md.

def _cli(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "help"
    base = os.environ.get("RELAY_BASE_URL", "http://localhost:8077")
    connect()

    if command == "import":
        counts = import_roster()
        print(f"imported {counts['total']}: {counts['added']} new, "
              f"{counts['updated']} already known")
        return 0

    if command == "list":
        for person in all_participants():
            state = person.display_name or "(not registered)"
            print(f"{person.id}  {person.email:34}  {state}")
        return 0

    if command == "links":
        # Every raw token is minted fresh here, because the stored one is a
        # hash and cannot be turned back into a link. Any link handed out
        # earlier stops working, which is the correct behaviour for "I lost
        # mine" and worth knowing before running this the day after sending.
        print("# Personal links. Each one is a seat. Do not post these in a "
              "shared channel.\n")
        for person in all_participants():
            raw = reissue(person.id)
            who = person.display_name or person.email
            print(f"{who:34}  {base.rstrip('/')}/rollcall/{raw}")
        print("\n# Nothing was emailed. See docs/ROLL_CALL_DO_NOT_SEND.md.")
        return 0

    print(__doc__)
    print("commands: import | list | links")
    return 1


if __name__ == "__main__":
    import sys
    raise SystemExit(_cli(sys.argv))
