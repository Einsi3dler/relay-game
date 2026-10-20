"""Accounts for the whole site: the fields the owner asked for.

First name, last name, username and password are required. Email is optional
(it is only for news about upcoming game nights), so signing in is by
username, or by email for people who gave one. See docs/ACCOUNTS.md.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

import backend.main as server
from backend import accounts, auth, mailer

BASIC = {"first_name": "Grace", "last_name": "Hopper",
         "username": "grace_h", "password": "a long enough password"}


@pytest.fixture
def client():
    accounts.close()
    accounts.connect(":memory:")
    mailer.outbox.clear()
    mailer._last_send.clear()
    auth._failures.clear()
    with TestClient(server.app) as test_client:
        yield test_client
    accounts.close()
    accounts.connect()


def test_signup_without_email_works_and_mails_nothing(client):
    response = client.post("/api/auth/signup", json=BASIC)
    assert response.status_code == 200, response.text
    user = response.json()["user"]
    assert user["email"] is None and user["username"] == "grace_h"
    assert mailer.outbox == []
    assert client.get("/api/auth/me").json()["user"]["id"] == user["id"]


def test_two_accounts_without_email_can_coexist(client):
    assert client.post("/api/auth/signup", json=BASIC).status_code == 200
    other = dict(BASIC, username="someone_else")
    assert client.post("/api/auth/signup", json=other).status_code == 200


def test_signup_with_email_still_verifies(client):
    body = dict(BASIC, email="grace@example.com")
    assert client.post("/api/auth/signup", json=body).status_code == 200
    assert [m.kind for m in mailer.outbox] == ["verify"]


def test_a_bad_email_is_refused_but_blank_is_fine(client):
    bad = client.post("/api/auth/signup", json=dict(BASIC, email="not-an-email"))
    assert bad.status_code == 400
    blank = client.post("/api/auth/signup", json=dict(BASIC, email="   "))
    assert blank.status_code == 200


def test_no_confirm_field_needed_but_a_wrong_one_is_refused(client):
    wrong = client.post("/api/auth/signup", json=dict(BASIC, confirm_password="nope"))
    assert wrong.status_code == 400


@pytest.mark.parametrize("field", ["first_name", "last_name", "username", "password"])
def test_the_required_fields(client, field):
    body = dict(BASIC)
    body[field] = ""
    assert client.post("/api/auth/signup", json=body).status_code == 400


def test_login_by_username(client):
    client.post("/api/auth/signup", json=BASIC)
    client.post("/api/auth/logout")
    ok = client.post("/api/auth/login", json={"login": "Grace_H", "password": BASIC["password"]})
    assert ok.status_code == 200
    bad = client.post("/api/auth/login", json={"login": "grace_h", "password": "wrong one!"})
    assert bad.status_code == 400 and "Username or password" in bad.json()["detail"]


def test_login_by_email_when_there_is_one(client):
    client.post("/api/auth/signup", json=dict(BASIC, email="grace@example.com"))
    client.post("/api/auth/logout")
    ok = client.post("/api/auth/login",
                     json={"login": "GRACE@example.com", "password": BASIC["password"]})
    assert ok.status_code == 200


def test_old_login_field_still_works(client):
    client.post("/api/auth/signup", json=dict(BASIC, email="grace@example.com"))
    client.post("/api/auth/logout")
    ok = client.post("/api/auth/login",
                     json={"email": "grace@example.com", "password": BASIC["password"]})
    assert ok.status_code == 200


def test_resend_without_an_email_says_so(client):
    client.post("/api/auth/signup", json=BASIC)
    response = client.post("/api/auth/verify/resend")
    assert response.status_code == 400
    assert mailer.outbox == []


def test_forgot_with_no_email_sends_nothing(client):
    client.post("/api/auth/signup", json=BASIC)
    assert client.post("/api/auth/forgot", json={"email": ""}).status_code == 200
    assert mailer.outbox == []


# --- the migration ---------------------------------------------------------

OLD_SCHEMA = """
CREATE TABLE users (
    id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, username TEXT NOT NULL UNIQUE,
    first_name TEXT NOT NULL, last_name TEXT NOT NULL, password_hash TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL
);
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE tokens (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose TEXT NOT NULL, expires_at INTEGER NOT NULL
);
"""


def test_an_old_database_is_upgraded_without_losing_anyone(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(OLD_SCHEMA)
    old.execute("INSERT INTO users VALUES ('u_1','old@example.com','olduser','Old',"
                "'Timer',?,1,100)", (accounts.hash_password("an old password"),))
    old.execute("INSERT INTO sessions VALUES ('sess-hash','u_1',100,9999999999)")
    old.commit()
    old.close()

    accounts.close()
    try:
        accounts.connect(str(path))
        db = accounts._db()
        email = next(r for r in db.execute("PRAGMA table_info(users)") if r["name"] == "email")
        assert email["notnull"] == 0
        user = accounts.check_login("olduser", "an old password")
        assert user and user.email == "old@example.com" and user.verified
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        accounts.create_user("New", "Person", None, "newbie", "a long enough password")
    finally:
        accounts.close()
        accounts.connect()
