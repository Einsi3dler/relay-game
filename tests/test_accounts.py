"""The account store: validation, password hashing, sessions, email tokens.

No web server here. Everything in backend/accounts.py is a plain function over
a SQLite connection, and the point of that shape is that the rules can be
tested without a request in the way. The routes are tests/test_auth.py.
"""

from __future__ import annotations

import time

import pytest

from backend import accounts, config


@pytest.fixture
def db():
    """A fresh in-memory database per test.

    `connect` is process-wide and returns an existing connection if there is
    one, so the close has to come first or a test inherits the last one's rows.
    """
    accounts.close()
    accounts.connect(":memory:")
    yield
    accounts.close()


def make_user(db_unused=None, **overrides):
    fields = {
        "first_name": "Ada", "last_name": "Lovelace",
        "email": "ada@example.com", "username": "ada_l",
        "password": "correct horse battery",
        "confirm": "correct horse battery",
    }
    fields.update(overrides)
    return accounts.create_user(**fields)


# --- passwords ------------------------------------------------------------

def test_password_round_trip():
    stored = accounts.hash_password("correct horse battery")
    assert accounts.verify_password("correct horse battery", stored)
    assert not accounts.verify_password("Correct horse battery", stored)
    assert not accounts.verify_password("", stored)


def test_hash_is_salted():
    """Two accounts on the same password must not share a hash, or one crack
    would be worth two accounts."""
    assert accounts.hash_password("same") != accounts.hash_password("same")


def test_stored_hash_carries_its_parameters():
    """Cost lives in the hash, not only in config, so raising SCRYPT_N later
    cannot invalidate every password written before it."""
    scheme, n, r, p, salt, key = accounts.hash_password("x" * 12).split("$")
    assert scheme == "scrypt"
    assert (int(n), int(r), int(p)) == (config.SCRYPT_N, config.SCRYPT_R, config.SCRYPT_P)
    assert len(bytes.fromhex(salt)) == config.SCRYPT_SALT_BYTES


def test_malformed_hash_fails_closed():
    """A corrupt row fails the login. It must never raise and take the server
    with it."""
    for junk in ("", "nonsense", "scrypt$bad$rows", "bcrypt$1$2$3$ab$cd"):
        assert accounts.verify_password("anything", junk) is False


# --- validation -----------------------------------------------------------

@pytest.mark.parametrize("field,value,message", [
    ("first_name", "", "first and last name"),
    ("last_name", "   ", "first and last name"),
    ("email", "not-an-email", "valid email"),
    ("email", "no@domain", "valid email"),
    ("username", "ab", "characters"),
    ("username", "x" * 40, "characters"),
    ("username", "has spaces", "letters, numbers"),
    ("username", "bang!", "letters, numbers"),
    ("password", "short", "at least"),
])
def test_signup_rejects(db, field, value, message):
    kwargs = {field: value}
    if field == "password":
        kwargs["confirm"] = value
    with pytest.raises(ValueError, match=message):
        make_user(**kwargs)


def test_confirm_must_match(db):
    with pytest.raises(ValueError, match="do not match"):
        make_user(confirm="something else")


def test_email_and_username_are_normalised(db):
    user = make_user(email="  Ada@Example.COM ", username="Ada_L")
    assert user.email == "ada@example.com"
    assert user.username == "ada_l"
    # And so is the lookup, so signing in with the address as typed works.
    assert accounts.by_email("ADA@EXAMPLE.com").id == user.id


def test_names_keep_their_case(db):
    """Normalising an address is a lookup concern. Doing it to a person's name
    would be a bug."""
    user = make_user(first_name="  Ada ", last_name="de Lovelace")
    assert (user.first_name, user.last_name) == ("Ada", "de Lovelace")


def test_duplicate_email_and_username(db):
    make_user()
    with pytest.raises(ValueError, match="already uses that email"):
        make_user(username="someone_else")
    with pytest.raises(ValueError, match="username is taken"):
        make_user(email="other@example.com")
    # Case does not buy a second account on the same address.
    with pytest.raises(ValueError, match="already uses that email"):
        make_user(email="ADA@EXAMPLE.COM", username="third")


def test_new_accounts_start_unverified(db):
    assert make_user().verified is False


def test_public_view_leaks_nothing(db):
    """The one view that reaches the browser. A password hash here would be a
    slow-motion breach, so this asserts on the exact key set."""
    view = make_user().public()
    assert set(view) == {
        "id", "email", "username", "first_name", "last_name",
        "display_name", "verified", "created_at",
    }
    assert "correct horse battery" not in repr(view)


# --- login ----------------------------------------------------------------

def test_check_login(db):
    user = make_user()
    assert accounts.check_login("ada@example.com", "correct horse battery").id == user.id
    assert accounts.check_login("ada@example.com", "wrong") is None
    assert accounts.check_login("nobody@example.com", "correct horse battery") is None


def test_unknown_address_still_costs_a_hash(db):
    """Otherwise the response time says whether an address is registered."""
    make_user()
    start = time.monotonic()
    accounts.check_login("nobody@example.com", "whatever")
    assert time.monotonic() - start > 0.01


# --- sessions -------------------------------------------------------------

def test_session_lifecycle(db):
    user = make_user()
    token = accounts.create_session(user.id)
    assert accounts.session_user(token).id == user.id
    accounts.destroy_session(token)
    assert accounts.session_user(token) is None


def test_session_token_is_not_stored_raw(db):
    """A stolen database must not hand over working cookies."""
    user = make_user()
    token = accounts.create_session(user.id)
    rows = accounts.connect().execute("SELECT token_hash FROM sessions").fetchall()
    assert rows and all(row["token_hash"] != token for row in rows)


def test_bad_and_missing_session_tokens(db):
    assert accounts.session_user(None) is None
    assert accounts.session_user("") is None
    assert accounts.session_user("not-a-real-token") is None


def test_expired_session_is_rejected_and_swept(db, monkeypatch):
    monkeypatch.setattr(config, "SESSION_TTL_SECONDS", -1)
    token = accounts.create_session(make_user().id)
    assert accounts.session_user(token) is None
    assert accounts.connect().execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"] == 0


def test_sessions_are_independent(db):
    """Signing out on a phone must not sign you out on a laptop."""
    user = make_user()
    phone, laptop = accounts.create_session(user.id), accounts.create_session(user.id)
    accounts.destroy_session(phone)
    assert accounts.session_user(laptop).id == user.id


def test_set_password_revokes_every_session(db):
    """The whole point of a reset: whoever else was signed in is now out."""
    user = make_user()
    intruder = accounts.create_session(user.id)
    accounts.set_password(user.id, "a brand new password", "a brand new password")
    assert accounts.session_user(intruder) is None
    assert accounts.check_login("ada@example.com", "a brand new password") is not None
    assert accounts.check_login("ada@example.com", "correct horse battery") is None


def test_set_password_validates(db):
    user = make_user()
    with pytest.raises(ValueError, match="at least"):
        accounts.set_password(user.id, "short", "short")
    with pytest.raises(ValueError, match="do not match"):
        accounts.set_password(user.id, "long enough here", "different one here")


# --- one-time tokens ------------------------------------------------------

@pytest.mark.parametrize("purpose", ["verify", "reset", "magic"])
def test_token_round_trip(db, purpose):
    user = make_user()
    token = accounts.issue_token(user.id, purpose, 60)
    assert accounts.peek_token(token, purpose).id == user.id   # peek does not spend
    assert accounts.peek_token(token, purpose).id == user.id
    assert accounts.consume_token(token, purpose).id == user.id
    assert accounts.consume_token(token, purpose) is None       # single use


def test_token_purposes_do_not_cross(db):
    """A verification link must not be spendable as a password reset."""
    token = accounts.issue_token(make_user().id, "verify", 60)
    assert accounts.consume_token(token, "reset") is None
    assert accounts.consume_token(token, "magic") is None
    assert accounts.consume_token(token, "verify") is not None


def test_unknown_purpose_rejected(db):
    with pytest.raises(ValueError, match="unknown token purpose"):
        accounts.issue_token(make_user().id, "something_else", 60)


def test_expired_token_is_refused(db):
    user = make_user()
    assert accounts.peek_token(accounts.issue_token(user.id, "reset", -1), "reset") is None
    assert accounts.consume_token(accounts.issue_token(user.id, "reset", -1), "reset") is None


def test_reissue_invalidates_the_previous_link(db):
    """Asking for a second reset mail has to kill the first, or a forwarded old
    message stays live for its full hour."""
    user = make_user()
    first = accounts.issue_token(user.id, "reset", 60)
    second = accounts.issue_token(user.id, "reset", 60)
    assert accounts.consume_token(first, "reset") is None
    assert accounts.consume_token(second, "reset").id == user.id


def test_token_is_not_stored_raw(db):
    token = accounts.issue_token(make_user().id, "magic", 60)
    rows = accounts.connect().execute("SELECT token_hash FROM tokens").fetchall()
    assert rows and all(row["token_hash"] != token for row in rows)


def test_missing_token_values(db):
    assert accounts.consume_token("", "verify") is None
    assert accounts.peek_token("", "verify") is None
