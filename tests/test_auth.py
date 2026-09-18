"""The account routes end to end: sign up, sign in, sign out, verify, reset,
magic link.

Rides the console mail backend (backend/mailer.py), so "the user opened the
link in their email" is a matter of reading the outbox and pulling the token
out of it. That is the same path a developer uses locally, which is the point
of the console backend existing.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

import backend.main as server
from backend import accounts, auth, config, mailer

GOOD = {
    "first_name": "Ada", "last_name": "Lovelace",
    "email": "ada@example.com", "username": "ada_l",
    "password": "correct horse battery",
    "confirm_password": "correct horse battery",
}


@pytest.fixture
def client():
    """A server on a throwaway database, with the mail and throttle state that
    lives in module globals reset between cases.

    The database is opened BEFORE the TestClient: `accounts.connect` hands back
    an existing connection, so the app's own lifespan call then joins this
    in-memory one instead of creating the real file on disk.
    """
    accounts.close()
    accounts.connect(":memory:")
    mailer.outbox.clear()
    mailer._last_send.clear()
    auth._failures.clear()
    with TestClient(server.app) as test_client:
        yield test_client
    accounts.close()


def link_token(kind: str) -> str:
    """The token from the most recent mail of `kind`, as a user clicking the
    link in their inbox would supply it."""
    mails = [mail for mail in mailer.outbox if mail.kind == kind]
    assert mails, f"no {kind} mail was sent"
    match = re.search(r"token=([\w\-]+)", mails[-1].body)
    assert match, f"no token in the {kind} mail"
    return match.group(1)


def signup(client, **overrides):
    body = dict(GOOD)
    body.update(overrides)
    return client.post("/api/auth/signup", json=body)


# --- signup ---------------------------------------------------------------

def test_signup_creates_signs_in_and_mails(client):
    response = signup(client)
    assert response.status_code == 200
    user = response.json()["user"]
    assert user["username"] == "ada_l"
    assert user["verified"] is False
    # Signed in straight away: a match may be starting in five minutes and an
    # inbox round trip before you can do anything would lose the player.
    assert config.SESSION_COOKIE in response.cookies
    assert client.get("/api/auth/me").json()["user"]["email"] == "ada@example.com"
    assert [mail.kind for mail in mailer.outbox] == ["verify"]


def test_signup_never_returns_the_password(client):
    assert "correct horse battery" not in signup(client).text


@pytest.mark.parametrize("overrides,message", [
    ({"first_name": ""}, "first and last name"),
    ({"email": "nope"}, "valid email"),
    ({"username": "no"}, "characters"),
    ({"password": "short", "confirm_password": "short"}, "at least"),
    ({"confirm_password": "mismatched entirely"}, "do not match"),
])
def test_signup_validation(client, overrides, message):
    response = signup(client, **overrides)
    assert response.status_code == 400
    assert message in response.json()["detail"]
    assert mailer.outbox == []


def test_signup_rejects_duplicates(client):
    signup(client)
    assert signup(client, username="other").status_code == 400
    assert signup(client, email="other@example.com").status_code == 400


# --- login / logout -------------------------------------------------------

def test_login_and_logout(client):
    signup(client)
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json()["user"] is None

    response = client.post("/api/auth/login",
                           json={"email": "ADA@example.com", "password": GOOD["password"]})
    assert response.status_code == 200
    assert client.get("/api/auth/me").json()["user"]["username"] == "ada_l"

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json()["user"] is None


def test_logout_revokes_the_session_not_just_the_cookie(client):
    """Clearing the cookie alone would leave a live session for anyone who had
    already copied it."""
    signup(client)
    stolen = client.cookies[config.SESSION_COOKIE]
    client.post("/api/auth/logout")
    assert accounts.session_user(stolen) is None


def test_login_failures_say_nothing_useful(client):
    """A wrong address and a wrong password give the same answer, or the form
    becomes a way to test which addresses are registered."""
    signup(client)
    client.post("/api/auth/logout")
    wrong_password = client.post("/api/auth/login",
                                 json={"email": GOOD["email"], "password": "nope"})
    no_account = client.post("/api/auth/login",
                             json={"email": "nobody@example.com", "password": "nope"})
    assert wrong_password.status_code == no_account.status_code == 400
    assert wrong_password.json()["detail"] == no_account.json()["detail"]


def test_login_lockout(client, monkeypatch):
    monkeypatch.setattr(config, "LOGIN_MAX_ATTEMPTS", 3)
    signup(client)
    client.post("/api/auth/logout")
    for _ in range(3):
        client.post("/api/auth/login", json={"email": GOOD["email"], "password": "no"})
    # Even the correct password is refused once the door is shut.
    locked = client.post("/api/auth/login",
                         json={"email": GOOD["email"], "password": GOOD["password"]})
    assert locked.status_code == 429


def test_a_good_login_clears_the_failure_count(client, monkeypatch):
    monkeypatch.setattr(config, "LOGIN_MAX_ATTEMPTS", 3)
    signup(client)
    client.post("/api/auth/logout")
    for _ in range(2):
        client.post("/api/auth/login", json={"email": GOOD["email"], "password": "no"})
    assert client.post("/api/auth/login",
                       json={"email": GOOD["email"], "password": GOOD["password"]}
                       ).status_code == 200
    client.post("/api/auth/logout")
    for _ in range(2):
        client.post("/api/auth/login", json={"email": GOOD["email"], "password": "no"})
    assert client.post("/api/auth/login",
                       json={"email": GOOD["email"], "password": GOOD["password"]}
                       ).status_code == 200


def test_me_is_not_an_error_for_a_guest(client):
    """Accounts are optional and the client asks on every page load, so "nobody"
    is a normal answer rather than a 401."""
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json() == {"user": None}


# --- email verification ---------------------------------------------------

def test_verify_link_confirms_the_address(client):
    signup(client)
    assert client.get("/api/auth/me").json()["user"]["verified"] is False
    response = client.post("/api/auth/verify", json={"token": link_token("verify")})
    assert response.status_code == 200
    assert response.json()["user"]["verified"] is True
    assert client.get("/api/auth/me").json()["user"]["verified"] is True


def test_verify_link_is_single_use(client):
    signup(client)
    token = link_token("verify")
    client.post("/api/auth/verify", json={"token": token})
    assert client.post("/api/auth/verify", json={"token": token}).status_code == 400


def test_verify_signs_in_a_browser_that_was_not(client):
    """The usual case: the link is opened on a phone while the laptop holds the
    session. Ending at a dead end there would help nobody."""
    signup(client)
    token = link_token("verify")
    client.post("/api/auth/logout")
    client.post("/api/auth/verify", json={"token": token})
    assert client.get("/api/auth/me").json()["user"]["username"] == "ada_l"


def test_resend_verification(client):
    signup(client)
    mailer._last_send.clear()          # step past the one-a-minute throttle
    assert client.post("/api/auth/verify/resend").status_code == 200
    assert len([m for m in mailer.outbox if m.kind == "verify"]) == 2
    # The newest link works and the first one no longer does.
    assert client.post("/api/auth/verify", json={"token": link_token("verify")}
                       ).status_code == 200


def test_resend_is_throttled(client):
    signup(client)
    assert client.post("/api/auth/verify/resend").status_code == 429


def test_resend_needs_a_session(client):
    """Otherwise it mails an unsolicited link to anyone typed into a form."""
    assert client.post("/api/auth/verify/resend").status_code == 401


def test_resend_is_a_no_op_once_verified(client):
    signup(client)
    client.post("/api/auth/verify", json={"token": link_token("verify")})
    mailer._last_send.clear()
    response = client.post("/api/auth/verify/resend")
    assert response.status_code == 200
    assert "already confirmed" in response.json()["message"]
    assert len([m for m in mailer.outbox if m.kind == "verify"]) == 1


def test_unverified_email_blocks_nothing(client):
    """The agreed rule: a banner and a resend link, never a locked door."""
    signup(client)
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login",
                       json={"email": GOOD["email"], "password": GOOD["password"]}
                       ).status_code == 200
    assert client.post("/api/auth/forgot",
                       json={"email": GOOD["email"], "mode": "magic"}
                       ).status_code == 200


# --- forgot password ------------------------------------------------------

def test_forgot_reset_mode_sends_a_reset_link(client):
    signup(client)
    response = client.post("/api/auth/forgot", json={"email": GOOD["email"], "mode": "reset"})
    assert response.status_code == 200
    assert [mail.kind for mail in mailer.outbox] == ["verify", "reset"]


def test_forgot_magic_mode_sends_a_sign_in_link(client):
    signup(client)
    response = client.post("/api/auth/forgot", json={"email": GOOD["email"], "mode": "magic"})
    assert response.status_code == 200
    assert [mail.kind for mail in mailer.outbox] == ["verify", "magic"]


def test_forgot_defaults_to_reset(client):
    signup(client)
    client.post("/api/auth/forgot", json={"email": GOOD["email"]})
    assert mailer.outbox[-1].kind == "reset"
    client.post("/api/auth/forgot", json={"email": GOOD["email"], "mode": "nonsense"})
    assert mailer.outbox[-1].kind == "reset"


@pytest.mark.parametrize("mode", ["reset", "magic"])
def test_forgot_does_not_reveal_whether_an_account_exists(client, mode):
    """Same status, same body, for a registered address and an unknown one.
    The only difference is that no mail goes out."""
    signup(client)
    known = client.post("/api/auth/forgot", json={"email": GOOD["email"], "mode": mode})
    mailer.outbox.clear()
    unknown = client.post("/api/auth/forgot",
                          json={"email": "nobody@example.com", "mode": mode})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert mailer.outbox == []


def test_forgot_is_throttled_per_address(client):
    signup(client)
    client.post("/api/auth/forgot", json={"email": GOOD["email"], "mode": "reset"})
    client.post("/api/auth/forgot", json={"email": GOOD["email"], "mode": "reset"})
    assert len([m for m in mailer.outbox if m.kind == "reset"]) == 1


def test_throttling_one_kind_does_not_block_the_other(client):
    """Someone who asked for a reset and then decided they wanted a sign-in
    link instead should not have to wait a minute for it."""
    signup(client)
    client.post("/api/auth/forgot", json={"email": GOOD["email"], "mode": "reset"})
    client.post("/api/auth/forgot", json={"email": GOOD["email"], "mode": "magic"})
    assert mailer.outbox[-1].kind == "magic"


# --- password reset -------------------------------------------------------

def test_reset_check_describes_a_live_link(client):
    signup(client)
    client.post("/api/auth/forgot", json={"email": GOOD["email"], "mode": "reset"})
    body = client.get("/api/auth/reset/check",
                      params={"token": link_token("reset")}).json()
    assert body["valid"] is True
    assert body["email"] == GOOD["email"]


def test_reset_check_does_not_spend_the_link(client):
    """A refresh of the reset page must not break it."""
    signup(client)
    client.post("/api/auth/forgot", json={"email": GOOD["email"], "mode": "reset"})
    token = link_token("reset")
    for _ in range(3):
        assert client.get("/api/auth/reset/check", params={"token": token}).json()["valid"]
    assert client.post("/api/auth/reset", json={
        "token": token, "password": "a whole new password",
        "confirm_password": "a whole new password"}).status_code == 200


def test_reset_check_on_a_dead_link(client):
    assert client.get("/api/auth/reset/check", params={"token": "nope"}).json() == {"valid": False}


def test_reset_sets_the_password_and_signs_in(client):
    signup(client)
    client.post("/api/auth/logout")
    client.post("/api/auth/forgot", json={"email": GOOD["email"], "mode": "reset"})
    response = client.post("/api/auth/reset", json={
        "token": link_token("reset"),
        "password": "a whole new password",
        "confirm_password": "a whole new password",
    })
    assert response.status_code == 200
    # Signed in on the spot: they have just proved who they are.
    assert client.get("/api/auth/me").json()["user"]["username"] == "ada_l"
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={
        "email": GOOD["email"], "password": "a whole new password"}).status_code == 200
    assert client.post("/api/auth/login", json={
        "email": GOOD["email"], "password": GOOD["password"]}).status_code == 400


def test_reset_verifies_the_address(client):
    """Using the link proves they read mail there, which is the only question
    verification asks."""
    signup(client)
    client.post("/api/auth/forgot", json={"email": GOOD["email"], "mode": "reset"})
    response = client.post("/api/auth/reset", json={
        "token": link_token("reset"), "password": "a whole new password",
        "confirm_password": "a whole new password"})
    assert response.json()["user"]["verified"] is True


def test_reset_link_is_single_use(client):
    signup(client)
    client.post("/api/auth/forgot", json={"email": GOOD["email"], "mode": "reset"})
    token = link_token("reset")
    client.post("/api/auth/reset", json={
        "token": token, "password": "a whole new password",
        "confirm_password": "a whole new password"})
    assert client.post("/api/auth/reset", json={
        "token": token, "password": "another new password",
        "confirm_password": "another new password"}).status_code == 400


def test_reset_rejects_a_bad_new_password(client):
    signup(client)
    client.post("/api/auth/forgot", json={"email": GOOD["email"], "mode": "reset"})
    response = client.post("/api/auth/reset", json={
        "token": link_token("reset"), "password": "short", "confirm_password": "short"})
    assert response.status_code == 400
    assert "at least" in response.json()["detail"]


def test_reset_signs_out_other_devices(client):
    """Someone else signed in with the old password has to be ejected, or the
    reset fixed nothing."""
    signup(client)
    intruder = client.cookies[config.SESSION_COOKIE]
    client.post("/api/auth/forgot", json={"email": GOOD["email"], "mode": "reset"})
    client.post("/api/auth/reset", json={
        "token": link_token("reset"), "password": "a whole new password",
        "confirm_password": "a whole new password"})
    assert accounts.session_user(intruder) is None


def test_reset_with_a_junk_token(client):
    assert client.post("/api/auth/reset", json={
        "token": "nope", "password": "a whole new password",
        "confirm_password": "a whole new password"}).status_code == 400


# --- magic link -----------------------------------------------------------

def test_magic_link_signs_in(client):
    signup(client)
    client.post("/api/auth/logout")
    client.post("/api/auth/forgot", json={"email": GOOD["email"], "mode": "magic"})
    response = client.post("/api/auth/magic", json={"token": link_token("magic")})
    assert response.status_code == 200
    assert client.get("/api/auth/me").json()["user"]["username"] == "ada_l"


def test_magic_link_verifies_the_address(client):
    signup(client)
    client.post("/api/auth/forgot", json={"email": GOOD["email"], "mode": "magic"})
    assert client.post("/api/auth/magic", json={"token": link_token("magic")}
                       ).json()["user"]["verified"] is True


def test_magic_link_is_single_use(client):
    signup(client)
    client.post("/api/auth/forgot", json={"email": GOOD["email"], "mode": "magic"})
    token = link_token("magic")
    client.post("/api/auth/magic", json={"token": token})
    client.post("/api/auth/logout")
    assert client.post("/api/auth/magic", json={"token": token}).status_code == 400


def test_magic_link_leaves_the_password_alone(client):
    """A sign-in link is not a reset. The old password still has to work."""
    signup(client)
    client.post("/api/auth/forgot", json={"email": GOOD["email"], "mode": "magic"})
    client.post("/api/auth/magic", json={"token": link_token("magic")})
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={
        "email": GOOD["email"], "password": GOOD["password"]}).status_code == 200


def test_token_kinds_do_not_cross_at_the_route(client):
    signup(client)
    verify = link_token("verify")
    assert client.post("/api/auth/magic", json={"token": verify}).status_code == 400
    assert client.post("/api/auth/reset", json={
        "token": verify, "password": "a whole new password",
        "confirm_password": "a whole new password"}).status_code == 400


# --- the pages, and the promise that guests are untouched -----------------

@pytest.mark.parametrize("path", [
    "/signup", "/login", "/forgot", "/reset", "/verify", "/magic", "/account",
])
def test_account_pages_serve(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert "auth.js" in response.text


def test_emailed_links_do_not_spend_their_token_on_get(client):
    """A mail scanner that follows links must not burn the link before the
    person clicks it, so the page load only draws HTML."""
    signup(client)
    token = link_token("verify")
    client.get(f"/verify?token={token}")
    assert client.post("/api/auth/verify", json={"token": token}).status_code == 200


def test_guest_join_still_works_without_an_account(client):
    """The load-bearing promise of the whole feature: accounts are optional and
    the engine never asks who you are."""
    match_id = client.post("/api/matches").json()["match"]["id"]
    response = client.post(f"/api/matches/{match_id}/join", json={"name": "Guest"})
    assert response.status_code == 200
    assert response.json()["player"]["name"] == "Guest"
    assert client.get("/api/auth/me").json()["user"] is None


# --- the deployment guard, at the app level -------------------------------

def test_server_refuses_to_start_when_links_would_point_at_localhost(monkeypatch):
    """The whole point of `mailer.verify_deployment`, proved through the app.

    A deploy that configures real mail but forgets RELAY_BASE_URL would send
    every player a verification link to their own machine. The server does not
    come up, so deploy.sh's health check fails and can roll back.
    """
    monkeypatch.setenv("RELAY_MAIL_BACKEND", "smtp")
    monkeypatch.setenv("RELAY_SMTP_HOST", "smtp.example.com")
    monkeypatch.delenv("RELAY_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="will not work as deployed"):
        with TestClient(server.app):
            pass


def test_server_starts_with_a_sound_production_config(monkeypatch):
    monkeypatch.setenv("RELAY_MAIL_BACKEND", "smtp")
    monkeypatch.setenv("RELAY_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("RELAY_BASE_URL", "https://relay.example.com")
    with TestClient(server.app) as configured:
        assert configured.get("/api/auth/me").json() == {"user": None}


def test_the_default_dev_setup_starts_untouched(client):
    """No mail configuration at all stays the zero-friction default."""
    assert client.get("/api/auth/me").status_code == 200


def test_session_cookie_is_secure_when_the_site_is_https(monkeypatch):
    """Production hardening that rides on the same setting: RELAY_BASE_URL is
    now required to be https for real deployments, so the cookie gets Secure
    without a second knob to forget."""
    monkeypatch.setenv("RELAY_BASE_URL", "https://relay.example.com")
    accounts.close()
    accounts.connect(":memory:")
    with TestClient(server.app) as secure_client:
        response = signup(secure_client)
        header = response.headers["set-cookie"]
        assert "Secure" in header
        assert "HttpOnly" in header
        assert "SameSite=lax" in header
    accounts.close()


def test_session_cookie_is_not_secure_on_a_local_http_dev_server(client):
    """Hard-coding Secure would silently break every http://127.0.0.1 login,
    which is where all the testing happens."""
    header = signup(client).headers["set-cookie"]
    assert "Secure" not in header
    assert "HttpOnly" in header
