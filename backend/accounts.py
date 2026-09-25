"""Accounts: users, password hashing, sessions, and one-time email tokens.

The one part of The Relay that keeps state on disk. Everything else — matches,
duel rooms, seats — lives in memory and dies with the process, which is correct
for a match that lasts an hour. An account is not that: an account that a
deploy deletes is worse than no account at all, because a player who registered
yesterday would find themselves locked out with no rejoin code either.

So this module owns a SQLite file and nothing else does. Match state does not
come near it. `backend/auth.py` is the only caller, and it talks to plain
functions here so the rules can be tested without a web server in the way.

Three things are stored, and two of them are stored hashed:

* **users** — the profile, plus a scrypt password hash. Never the password.
* **sessions** — what a login cookie is worth. The cookie carries a random
  token; the table holds only its SHA-256. A stolen database therefore yields
  no usable cookie, and logging out can genuinely revoke one (a signed cookie
  cannot be revoked without a server-side list, so this *is* the simpler
  design, not the more complex one).
* **tokens** — single-use email links: verify, reset, magic login. Hashed for
  the same reason, single-use because each one is a way into an account that
  skips the password, and short-lived because of the same.

On validation: the only password rule is a length floor. Composition rules
("one capital, one symbol") push people towards `Passw0rd!` and away from a
long passphrase, so they are left out on purpose.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend import config

# One connection, one lock. The server runs a single uvicorn worker (see
# deploy.sh), the tables are tiny, and a local SQLite read is measured in tens
# of microseconds — so these calls run on the event loop rather than being
# pushed through `asyncio.to_thread`. If this ever grows a second worker, that
# is the line to revisit, along with WAL and a connection pool.
_conn: sqlite3.Connection | None = None
_lock = threading.Lock()

# Deliberately loose. Address syntax is far wilder than any regex people
# actually write, and the real proof that an address exists is that mail sent
# to it arrives — which is what verification is for. This only catches typos.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    username      TEXT NOT NULL UNIQUE,
    first_name    TEXT NOT NULL,
    last_name     TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    verified      INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tokens (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose    TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_tokens_user   ON tokens(user_id, purpose);
"""

PURPOSES = ("verify", "reset", "magic")


# --- plumbing -------------------------------------------------------------

def connect(path: str | None = None) -> sqlite3.Connection:
    """Open (once) the accounts database and create the schema.

    `path` may be ":memory:", which is what the tests use — one process-wide
    in-memory database that behaves exactly like the file one.
    """
    global _conn
    with _lock:
        if _conn is not None:
            return _conn
        # RELAY_DB_PATH wins over the config default, so a deployment can put
        # the file on a volume that survives a redeploy without editing code —
        # and so a test run can point the whole process at ":memory:".
        target = path or os.environ.get("RELAY_DB_PATH") or config.ACCOUNTS_DB_PATH
        if target != ":memory:":
            file = Path(target)
            if not file.is_absolute():
                file = Path(__file__).resolve().parent.parent / file
            file.parent.mkdir(parents=True, exist_ok=True)
            target = str(file)
        _conn = sqlite3.connect(target, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.executescript(SCHEMA)
        _conn.commit()
        return _conn


def close() -> None:
    """Drop the connection. Tests call this between cases; the app never does."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def _db() -> sqlite3.Connection:
    return _conn if _conn is not None else connect()


def _now() -> int:
    return int(time.time())


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _digest(raw: str) -> str:
    """What actually goes in the table for a session or email token.

    Plain SHA-256 with no salt, and that is the right call here: the input is
    256 bits of `secrets` randomness, so there is no dictionary to precompute
    and nothing for a salt to defend against. Passwords are the opposite case
    and get scrypt.
    """
    return hashlib.sha256(raw.encode()).hexdigest()


# --- passwords ------------------------------------------------------------

def hash_password(password: str) -> str:
    """scrypt, stdlib, no new dependency.

    The stored string carries its own parameters — `scrypt$n$r$p$salt$key` — so
    raising the cost later does not invalidate hashes written today. They
    verify at the cost they were written with, and get rewritten on next login
    if that ever matters.
    """
    salt = secrets.token_bytes(config.SCRYPT_SALT_BYTES)
    key = hashlib.scrypt(
        password.encode(), salt=salt,
        n=config.SCRYPT_N, r=config.SCRYPT_R, p=config.SCRYPT_P,
        dklen=config.SCRYPT_KEY_BYTES, maxmem=config.SCRYPT_MAXMEM,
    )
    return "scrypt${}${}${}${}${}".format(
        config.SCRYPT_N, config.SCRYPT_R, config.SCRYPT_P, salt.hex(), key.hex()
    )


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check against a stored hash. A malformed hash is False,
    never an exception: a corrupt row must fail the login, not the server."""
    try:
        scheme, n, r, p, salt_hex, key_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        # The limit is derived from the hash's OWN parameters, not from the
        # current config: a hash written under a heavier setting has to keep
        # verifying after the config is lowered again.
        computed = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(key_hex)),
            maxmem=max(config.SCRYPT_MAXMEM, 128 * int(n) * int(r) * 2),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(computed.hex(), key_hex)


# --- validation -----------------------------------------------------------

def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def normalise_username(username: str) -> str:
    return (username or "").strip().lower()


def validate_signup(
    first_name: str, last_name: str, email: str,
    username: str, password: str, confirm: str,
) -> dict[str, str]:
    """Check a registration and hand back the cleaned fields.

    Raises ValueError with a message meant to be read by the person who typed
    it — the route turns that straight into a 400, the same contract the engine
    already uses for `join_match` and `rejoin`.
    """
    first = (first_name or "").strip()
    last = (last_name or "").strip()
    mail = normalise_email(email)
    user = normalise_username(username)

    if not first or not last:
        raise ValueError("enter your first and last name")
    if len(first) > config.NAME_MAX or len(last) > config.NAME_MAX:
        raise ValueError(f"names cannot be longer than {config.NAME_MAX} characters")
    if not mail or len(mail) > config.EMAIL_MAX or not _EMAIL_RE.match(mail):
        raise ValueError("enter a valid email address")
    if not (config.USERNAME_MIN <= len(user) <= config.USERNAME_MAX):
        raise ValueError(
            f"username must be {config.USERNAME_MIN}-{config.USERNAME_MAX} characters"
        )
    if any(char not in config.USERNAME_ALPHABET for char in user):
        raise ValueError("username can use letters, numbers, dashes and underscores")
    if len(password or "") < config.PASSWORD_MIN:
        raise ValueError(f"password must be at least {config.PASSWORD_MIN} characters")
    if len(password) > config.PASSWORD_MAX:
        raise ValueError("that password is too long")
    if password != confirm:
        raise ValueError("passwords do not match")
    return {"first_name": first, "last_name": last, "email": mail, "username": user}


# --- users ----------------------------------------------------------------

@dataclass
class User:
    id: str
    email: str
    username: str
    first_name: str
    last_name: str
    verified: bool
    created_at: int

    @property
    def display_name(self) -> str:
        """What the game shows. A username is chosen; a legal name is not
        always something you want on a scoreboard."""
        return self.username

    def public(self) -> dict[str, Any]:
        """Safe to send to the browser. No hash, no tokens, no session."""
        return {
            "id": self.id,
            "email": self.email,
            "username": self.username,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "display_name": self.display_name,
            "verified": self.verified,
            "created_at": _iso(self.created_at),
        }


def _user(row: sqlite3.Row | None) -> User | None:
    if row is None:
        return None
    return User(
        id=row["id"], email=row["email"], username=row["username"],
        first_name=row["first_name"], last_name=row["last_name"],
        verified=bool(row["verified"]), created_at=row["created_at"],
    )


def create_user(
    first_name: str, last_name: str, email: str,
    username: str, password: str, confirm: str,
) -> User:
    """Register an account. Validates first, then takes the email and username.

    The UNIQUE constraints are the real guard, not the SELECT above them: two
    signups racing for the same username would both pass a check-then-insert.
    The check exists only to phrase the error nicely for the common case.
    """
    fields = validate_signup(first_name, last_name, email, username, password, confirm)
    db = _db()
    with _lock:
        taken = db.execute(
            "SELECT email, username FROM users WHERE email = ? OR username = ?",
            (fields["email"], fields["username"]),
        ).fetchone()
        if taken is not None:
            if taken["email"] == fields["email"]:
                raise ValueError("an account already uses that email address")
            raise ValueError("that username is taken")
        user_id = "u_" + secrets.token_urlsafe(16)
        try:
            db.execute(
                "INSERT INTO users (id, email, username, first_name, last_name,"
                " password_hash, verified, created_at) VALUES (?,?,?,?,?,?,0,?)",
                (user_id, fields["email"], fields["username"], fields["first_name"],
                 fields["last_name"], hash_password(password), _now()),
            )
            db.commit()
        except sqlite3.IntegrityError:
            raise ValueError("an account already uses that email or username")
        return _user(db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def by_id(user_id: str) -> User | None:
    return _user(_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def by_email(email: str) -> User | None:
    return _user(_db().execute(
        "SELECT * FROM users WHERE email = ?", (normalise_email(email),)
    ).fetchone())


def by_username(username: str) -> User | None:
    return _user(_db().execute(
        "SELECT * FROM users WHERE username = ?", (normalise_username(username),)
    ).fetchone())


def check_login(email: str, password: str) -> User | None:
    """Password login. None on any failure, with no hint about which one.

    An unknown address still pays for a hash. Without that, "no such user"
    returns in microseconds while a wrong password takes ~100ms, and the
    difference tells an attacker which addresses are registered.
    """
    row = _db().execute(
        "SELECT * FROM users WHERE email = ?", (normalise_email(email),)
    ).fetchone()
    if row is None:
        hash_password(password or "")
        return None
    if not verify_password(password or "", row["password_hash"]):
        return None
    return _user(row)


def set_password(user_id: str, password: str, confirm: str) -> None:
    """Change a password and end every session the account has.

    Revoking sessions is the point of a reset, not a side effect: if someone
    else was already signed in, a new password that left their cookie working
    would have fixed nothing.
    """
    if len(password or "") < config.PASSWORD_MIN:
        raise ValueError(f"password must be at least {config.PASSWORD_MIN} characters")
    if len(password) > config.PASSWORD_MAX:
        raise ValueError("that password is too long")
    if password != confirm:
        raise ValueError("passwords do not match")
    db = _db()
    with _lock:
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                   (hash_password(password), user_id))
        db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        db.commit()


def mark_verified(user_id: str) -> None:
    db = _db()
    with _lock:
        db.execute("UPDATE users SET verified = 1 WHERE id = ?", (user_id,))
        db.commit()


# --- sessions -------------------------------------------------------------

def create_session(user_id: str) -> str:
    """Mint a login session and return the raw token for the cookie.

    This is the only moment the raw token exists on the server. It is hashed
    into the table and then dropped, so the value in the cookie cannot be read
    back out of the database by anyone who steals it.
    """
    raw = secrets.token_urlsafe(config.TOKEN_BYTES)
    now = _now()
    db = _db()
    with _lock:
        db.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at)"
            " VALUES (?,?,?,?)",
            (_digest(raw), user_id, now, now + config.SESSION_TTL_SECONDS),
        )
        db.commit()
    return raw


def session_user(raw_token: str | None) -> User | None:
    """Resolve a cookie to its account, or None if it is absent, wrong or
    expired. Expired rows are swept as they are found — a session table that
    only ever grows is a slow leak."""
    if not raw_token:
        return None
    db = _db()
    row = db.execute(
        "SELECT user_id, expires_at FROM sessions WHERE token_hash = ?",
        (_digest(raw_token),),
    ).fetchone()
    if row is None:
        return None
    if row["expires_at"] <= _now():
        destroy_session(raw_token)
        return None
    return by_id(row["user_id"])


def destroy_session(raw_token: str | None) -> None:
    """Log out. Missing or unknown tokens are not an error — the caller's goal
    is 'this cookie no longer works', and that is already true."""
    if not raw_token:
        return
    db = _db()
    with _lock:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (_digest(raw_token),))
        db.commit()


# --- one-time email tokens ------------------------------------------------

def issue_token(user_id: str, purpose: str, ttl_seconds: int) -> str:
    """Mint a single-use link token and return the raw value for the email.

    Any earlier token of the same purpose is dropped first. Asking for a second
    reset link has to invalidate the first, or a forwarded old mail stays live
    for its full hour after the user has already used a newer one.
    """
    if purpose not in PURPOSES:
        raise ValueError(f"unknown token purpose: {purpose}")
    raw = secrets.token_urlsafe(config.TOKEN_BYTES)
    db = _db()
    with _lock:
        db.execute("DELETE FROM tokens WHERE user_id = ? AND purpose = ?",
                   (user_id, purpose))
        db.execute(
            "INSERT INTO tokens (token_hash, user_id, purpose, expires_at)"
            " VALUES (?,?,?,?)",
            (_digest(raw), user_id, purpose, _now() + ttl_seconds),
        )
        db.commit()
    return raw


def peek_token(raw_token: str, purpose: str) -> User | None:
    """Who a token belongs to, without spending it.

    A reset link opens a form before it does anything, and that form has to
    know whose password it is about to set. Consuming the token to draw the
    page would break it on a refresh.
    """
    if not raw_token:
        return None
    row = _db().execute(
        "SELECT user_id, expires_at FROM tokens WHERE token_hash = ? AND purpose = ?",
        (_digest(raw_token), purpose),
    ).fetchone()
    if row is None or row["expires_at"] <= _now():
        return None
    return by_id(row["user_id"])


def consume_token(raw_token: str, purpose: str) -> User | None:
    """Spend a token: resolve it and delete it in the same breath.

    Single-use is what stops a link that sat in an inbox — or in a mail
    provider's link scanner — from being replayed later.
    """
    if not raw_token:
        return None
    db = _db()
    with _lock:
        row = db.execute(
            "SELECT user_id, expires_at FROM tokens"
            " WHERE token_hash = ? AND purpose = ?",
            (_digest(raw_token), purpose),
        ).fetchone()
        if row is None:
            return None
        db.execute("DELETE FROM tokens WHERE token_hash = ?", (_digest(raw_token),))
        db.commit()
        if row["expires_at"] <= _now():
            return None
    return by_id(row["user_id"])
