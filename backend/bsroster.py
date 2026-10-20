"""BULLSHIT QUIZ roster: who is playing, and the one question each of them wrote.

Its own small database beside ROLL CALL's, not more tables inside it. The two
games have different people, different links and different data, and two
small stores cannot drift into each other the way one shared keyspace can
(the same reasoning `backend/rollcall.py` gives for not living in accounts).

**This module does not import `backend/mailer.py`, and must never import it.**
`tests/test_bsroster_guards.py` fails if it ever does, directly or through
anything it imports. Links come out of the `links` command, which prints them
for a human to hand out. docs/ROLL_CALL_DO_NOT_SEND.md is the standing order,
and it covers this roster too.

The roster file is `var/bullshit/roster.json`, gitignored, real people's
addresses: `{"participants": [{"email": "..."}, ...]}`. Nothing in here may
copy a line of it into this repository.

A seat is claimed by a site account (backend/accounts.py): the personal link
is an invitation, used once to tie the seat to whoever is signed in. After
that the account *is* the seat, so a player resumes from any device by
signing in, and the link alone opens nothing. The in-game name comes from
the account's first name.

What a player writes is private until their round's reveal: the prompt, the
real answer, the decoys and the author's identity. `my_question` hands a
player their own and nothing else. `questions()` is for the room, which is
the only thing that decides what anybody else may see.
"""

from __future__ import annotations

import hashlib
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
ROSTER_PATH = Path(os.environ.get("RELAY_BULLSHIT_ROSTER",
                                  REPO / "var" / "bullshit" / "roster.json"))
DEFAULT_DB = REPO / "var" / "bullshit.db"
LINK_PATH = "/bullshit/"

_lock = threading.Lock()
_db: sqlite3.Connection | None = None


# --- storage --------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS player (
  id            TEXT PRIMARY KEY,
  email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
  token_hash    TEXT NOT NULL UNIQUE,
  option_count  INTEGER NOT NULL,
  display_name  TEXT,
  registered_at TEXT,
  created_at    TEXT NOT NULL,
  user_id       TEXT
);
CREATE TABLE IF NOT EXISTS question (
  player_id   TEXT PRIMARY KEY REFERENCES player(id),
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
        _db.executescript(SCHEMA)
        _migrate(_db)
        _db.commit()
        return _db


def _migrate(db: sqlite3.Connection) -> None:
    """`user_id` arrived with account sign-in. SQLite cannot add a UNIQUE
    column, so uniqueness is an index: one account, one seat."""
    have = {row["name"] for row in db.execute("PRAGMA table_info(player)")}
    if "user_id" not in have:
        db.execute("ALTER TABLE player ADD COLUMN user_id TEXT")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS player_user ON player(user_id)")


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
    """192 bits of `secrets`, so unsalted SHA-256 is right: there is no
    dictionary to precompute. The same call rollcall.py and accounts.py make."""
    return hashlib.sha256(raw.encode()).hexdigest()


def _clean(text: object) -> str:
    return " ".join(str(text or "").split())


# --- the people -----------------------------------------------------------

@dataclass
class Player:
    id: str
    email: str
    option_count: int          # what the server asked them to write: 2, 3 or 4
    display_name: str | None
    has_question: bool
    user_id: str | None = None  # the account that claimed this seat

    @property
    def registered(self) -> bool:
        """Claimed by an account, and so named. The room seats only these."""
        return bool(self.display_name and self.user_id)

    @property
    def name(self) -> str:
        return self.display_name or ""

    def card(self) -> dict[str, Any]:
        """What any viewer may know about this person at any phase. Note what
        is absent: whether their question has come up yet would narrow down
        who wrote the current one."""
        return {"id": self.id, "name": self.name, "registered": self.registered}


def _row_to_player(row: sqlite3.Row) -> Player:
    return Player(
        id=row["id"],
        email=row["email"],
        option_count=row["option_count"],
        display_name=row["display_name"],
        has_question=bool(row["has_question"]),
        user_id=row["user_id"],
    )


_PLAYER_SELECT = (
    "SELECT p.*, (q.player_id IS NOT NULL) AS has_question"
    " FROM player p LEFT JOIN question q ON q.player_id = p.id"
)


def all_players() -> list[Player]:
    rows = _conn().execute(_PLAYER_SELECT + " ORDER BY p.created_at, p.id")
    return [_row_to_player(row) for row in rows]


def by_id(player_id: str) -> Player | None:
    row = _conn().execute(_PLAYER_SELECT + " WHERE p.id = ?",
                          (player_id,)).fetchone()
    return _row_to_player(row) if row else None


def by_user(user_id: str | None) -> Player | None:
    """The seat this account claimed, if any."""
    if not user_id:
        return None
    row = _conn().execute(_PLAYER_SELECT + " WHERE p.user_id = ?",
                          (user_id,)).fetchone()
    return _row_to_player(row) if row else None


def by_token(raw_token: str) -> Player | None:
    """Whoever holds the link is that person. Right for a party game among
    people who know each other, and not authentication."""
    if not raw_token:
        return None
    row = _conn().execute(_PLAYER_SELECT + " WHERE p.token_hash = ?",
                          (_digest(raw_token),)).fetchone()
    return _row_to_player(row) if row else None


# --- import ---------------------------------------------------------------

def load_roster_file(path: Path | None = None) -> dict[str, Any]:
    target = Path(path) if path else ROSTER_PATH
    if not target.exists():
        raise FileNotFoundError(f"no roster at {target}")
    return json.loads(target.read_text(encoding="utf-8"))


def import_roster(path: Path | None = None,
                  rng: random.Random | None = None) -> dict[str, int]:
    """Read the roster file into the database, idempotently.

    Keyed on email. Re-running adds newcomers and leaves everybody else's
    token, name, option count and question alone. Nobody is deleted here.

    Each newcomer is given their option count now, from the roster's size:
    the count is the server's decision, and it must not change under somebody
    who has already written their decoys.
    """
    roster = load_roster_file(path)
    people = roster["participants"]
    shuffler = rng or random.Random(secrets.randbits(64))
    room_size = max(len(people), config.BS_MIN_PLAYERS)
    db = _conn()
    added = 0

    with _lock:
        for person in people:
            email = person["email"].strip()
            known = db.execute("SELECT id FROM player WHERE email = ?",
                               (email,)).fetchone()
            if known is not None:
                continue
            pid = f"bs_{uuid4().hex[:8]}"
            raw = secrets.token_urlsafe(config.BS_TOKEN_BYTES)
            db.execute(
                "INSERT INTO player (id, email, token_hash, option_count,"
                " created_at) VALUES (?,?,?,?,?)",
                (pid, email, _digest(raw),
                 bsquiz.option_count_for(room_size, shuffler), _now()),
            )
            _fresh_tokens[pid] = raw
            added += 1
        db.commit()

    return {"added": added, "total": len(people)}


# Raw tokens minted in this process, so `links` can print them. Deliberately
# not persisted: a stored raw token is a stored password.
_fresh_tokens: dict[str, str] = {}


def reissue(player_id: str) -> str:
    """A new link for somebody who lost theirs. The old one stops working."""
    raw = secrets.token_urlsafe(config.BS_TOKEN_BYTES)
    db = _conn()
    with _lock:
        db.execute("UPDATE player SET token_hash = ? WHERE id = ?",
                   (_digest(raw), player_id))
        db.commit()
    _fresh_tokens[player_id] = raw
    return raw


def link_for(player_id: str, base_url: str) -> str | None:
    raw = _fresh_tokens.get(player_id)
    return f"{base_url.rstrip('/')}{LINK_PATH}{raw}" if raw else None


# --- claiming a seat -----------------------------------------------------

class Refused(ValueError):
    """Something a player sent that cannot be accepted. The message is for
    them to read."""


def name_for(first_name: str, last_name: str, username: str,
             taken: set[str]) -> str:
    """The name the game shows: what people on the call know them by.

    First name; with a last initial if somebody already has that first name;
    the username as the last resort. `taken` is lower-cased names in use.
    """
    first = " ".join((first_name or "").split())[:config.BS_NAME_MAX]
    initial = (last_name or "").strip()[:1].upper()
    for candidate in (first, f"{first} {initial}." if initial else "", username):
        candidate = candidate[:config.BS_NAME_MAX]
        if candidate and candidate.lower() not in taken:
            return candidate
    return username[:config.BS_NAME_MAX]


def claim(raw_token: str, user_id: str, first_name: str, last_name: str,
          username: str) -> Player:
    """Tie the invitation's seat to a signed-in account.

    Idempotent for the same account. Refused when the seat belongs to
    another account, or this account already holds a different seat.
    Whether the game still accepts newcomers is the room's call.
    """
    person = by_token(raw_token)
    if person is None:
        raise Refused("That link is not valid.")
    if person.user_id == user_id:
        return person
    if person.user_id:
        raise Refused("This invitation has already been claimed by another account.")
    if by_user(user_id) is not None:
        raise Refused("Your account already has a seat in this game.")

    db = _conn()
    with _lock:
        taken = {row["display_name"].lower() for row in db.execute(
            "SELECT display_name FROM player WHERE display_name IS NOT NULL"
            " AND id != ?", (person.id,))}
        name = name_for(first_name, last_name, username, taken)
        try:
            db.execute("UPDATE player SET user_id = ?, display_name = ?,"
                       " registered_at = ? WHERE id = ? AND user_id IS NULL",
                       (user_id, name, _now(), person.id))
            db.commit()
        except sqlite3.IntegrityError:
            raise Refused("Your account already has a seat in this game.")
    claimed = by_id(person.id)
    if claimed.user_id != user_id:
        raise Refused("This invitation has already been claimed by another account.")
    return claimed


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
    if not person.registered:
        raise Refused("Claim your seat first.")

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
            "INSERT INTO question (player_id, prompt, answer, decoys,"
            " explanation, updated_at) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(player_id) DO UPDATE SET prompt = excluded.prompt,"
            " answer = excluded.answer, decoys = excluded.decoys,"
            " explanation = excluded.explanation,"
            " updated_at = excluded.updated_at",
            (person.id, prompt, answer, json.dumps(decoys), explanation, _now()),
        )
        db.commit()
    return my_question(person.id)


def _row_to_question(row: sqlite3.Row) -> bsquiz.Question:
    return bsquiz.Question(
        author=row["player_id"],
        prompt=row["prompt"],
        answer=row["answer"],
        decoys=tuple(json.loads(row["decoys"])),
        explanation=row["explanation"],
    )


def my_question(player_id: str) -> bsquiz.Question | None:
    row = _conn().execute("SELECT * FROM question WHERE player_id = ?",
                          (player_id,)).fetchone()
    return _row_to_question(row) if row else None


def questions() -> dict[str, bsquiz.Question]:
    """Every submitted question, by author. For the room only."""
    return {row["player_id"]: _row_to_question(row)
            for row in _conn().execute("SELECT * FROM question")}


# --- the command line -----------------------------------------------------
# `python -m backend.bsroster <command>`. Nothing here sends anything:
# `links` prints to a terminal for a human to read and hand out.

def _cli(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "help"
    base = os.environ.get("RELAY_BASE_URL", "http://localhost:8000")
    connect()

    if command == "import":
        counts = import_roster()
        print(f"roster has {counts['total']}: {counts['added']} new")
        return 0

    if command == "list":
        for person in all_players():
            state = person.display_name or "(not claimed)"
            wrote = "question in" if person.has_question else "no question yet"
            print(f"{person.id}  {person.email:34}  {state:20}  "
                  f"{person.option_count} options, {wrote}")
        return 0

    if command == "links":
        # Fresh tokens every time: the stored one is a hash. Any link handed
        # out earlier stops working.
        print("# Personal links. Each one is a seat. Do not post these in a "
              "shared channel.\n")
        for person in all_players():
            raw = reissue(person.id)
            who = person.display_name or person.email
            print(f"{who:34}  {base.rstrip('/')}{LINK_PATH}{raw}")
        print("\n# Nothing was emailed. See docs/ROLL_CALL_DO_NOT_SEND.md.")
        return 0

    print(__doc__)
    print("commands: import | list | links")
    return 1


if __name__ == "__main__":
    import sys
    raise SystemExit(_cli(sys.argv))
