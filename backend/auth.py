"""Account routes: sign up, sign in, sign out, verify, reset, magic link.

An APIRouter rather than routes on `app`, for two reasons. The obvious one is
that `backend/main.py` is a shared file and this way it gains two lines instead
of four hundred. The better one is that accounts are genuinely separate from
the match engine: nothing here imports the engine, touches `store`, or knows a
match exists. An account is an identity; what it does with that identity in a
match is the engine's business, and keeping the seam sharp is what stops this
from growing into the match state's problem later.

`docs/ACCOUNTS.md` has the flows end to end. The rules that are easy to get
wrong, in one place:

* **Accounts are optional.** Guest join still works exactly as before. Nothing
  in this module gates gameplay, and the engine never asks who you are.
* **Never confirm whether an address is registered.** `/forgot` and
  `/magic` answer the same way for a known address and an unknown one.
  Otherwise the form becomes a way to test a list of emails against your users.
* **Email links land on a page; the page posts the token.** Consuming a
  single-use token on GET means a mail scanner that follows links — plenty of
  corporate filters do — burns the link before the human clicks it.
* **Proving the inbox verifies it.** Anyone who used a reset or magic link read
  mail at that address, which is the whole question verification asks.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Cookie, HTTPException, Response
from pydantic import BaseModel

from backend import accounts, config, mailer

router = APIRouter(prefix="/api/auth", tags=["auth"])

# email -> (failures, first failure time). Crude and in memory, which is all a
# single-worker MVP can honestly offer; it is here to blunt a password-guessing
# loop, not to survive a botnet. A restart forgives everyone, and that is an
# acceptable trade for not putting a lockout table on disk.
_failures: dict[str, tuple[int, float]] = {}

# What every "we sent you something" route says, whether or not there was an
# account to send it to. One constant, so the two routes cannot drift apart and
# accidentally become distinguishable.
SENT_MESSAGE = ("If an account exists for that address, a link is on its way. "
                "Check your inbox, and your spam folder.")


# --- request bodies -------------------------------------------------------

class SignupBody(BaseModel):
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    username: str = ""
    password: str = ""
    confirm_password: str = ""


class LoginBody(BaseModel):
    email: str = ""
    password: str = ""


class ForgotBody(BaseModel):
    email: str = ""
    # "reset" sends a set-a-new-password link, "magic" sends a one-click
    # sign-in link. The forgot-password screen asks which the user wants
    # rather than assuming: someone who has simply forgotten their password
    # tonight may not want to choose a new one they will forget again.
    mode: str = "reset"


class TokenBody(BaseModel):
    token: str = ""


class ResetBody(BaseModel):
    token: str = ""
    password: str = ""
    confirm_password: str = ""


# --- helpers --------------------------------------------------------------

def current_user(session_cookie: str | None) -> accounts.User | None:
    """The signed-in account, or None. The one way to ask."""
    return accounts.session_user(session_cookie)


def _secure_cookies() -> bool:
    """Mark cookies Secure only when the site is actually served over HTTPS.

    Hard-coding True would be the safer-sounding choice and would silently
    break every local http://127.0.0.1 login, which is where all the testing
    happens. It follows the configured base URL instead.
    """
    return mailer.base_url().startswith("https://")


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        config.SESSION_COOKIE, token,
        max_age=config.SESSION_TTL_SECONDS,
        httponly=True,        # JS cannot read it, so an XSS cannot post it onward
        samesite="lax",       # sent on normal navigation, not on cross-site posts
        secure=_secure_cookies(),
        path="/",
    )


def _sign_in(response: Response, user: accounts.User) -> dict:
    """Mint a session, set the cookie, and describe the account to the client."""
    _set_session_cookie(response, accounts.create_session(user.id))
    return {"user": user.public()}


def _locked_out(email: str) -> bool:
    record = _failures.get(email)
    if record is None:
        return False
    count, first = record
    if time.monotonic() - first > config.LOGIN_LOCKOUT_SECONDS:
        _failures.pop(email, None)
        return False
    return count >= config.LOGIN_MAX_ATTEMPTS


def _note_failure(email: str) -> None:
    count, first = _failures.get(email, (0, time.monotonic()))
    if time.monotonic() - first > config.LOGIN_LOCKOUT_SECONDS:
        count, first = 0, time.monotonic()
    _failures[email] = (count + 1, first)


def _send_verification(user: accounts.User) -> bool:
    """Mail a verification link. False when the throttle held it back.

    Throttle first, token second: minting a new token deletes the previous one,
    so doing it the other way round would let a double-click invalidate the
    link the user already has while refusing to send its replacement.
    """
    if mailer.throttled(user.email, "verify"):
        return False
    token = accounts.issue_token(user.id, "verify", config.VERIFY_TTL_SECONDS)
    mailer.send_verify(user.email, user.first_name, token)
    return True


# --- routes ---------------------------------------------------------------

@router.get("/me")
async def me(relay_session: str | None = Cookie(default=None, alias=config.SESSION_COOKIE)):
    """Who is signed in, if anyone. Never 401s — "nobody" is a normal answer
    on a site where accounts are optional, and the client asks on every load."""
    user = current_user(relay_session)
    return {"user": user.public() if user else None}


@router.post("/signup")
async def signup(body: SignupBody, response: Response):
    try:
        user = accounts.create_user(
            body.first_name, body.last_name, body.email,
            body.username, body.password, body.confirm_password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Signed in immediately, unverified. Making people go to their inbox before
    # they can do anything is how you lose someone who is registering because a
    # match starts in five minutes.
    _send_verification(user)
    return _sign_in(response, user)


@router.post("/login")
async def login(body: LoginBody, response: Response):
    email = accounts.normalise_email(body.email)
    if _locked_out(email):
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Wait 15 minutes, or use a sign-in link.",
        )
    user = accounts.check_login(email, body.password)
    if user is None:
        _note_failure(email)
        # One message for a wrong address and a wrong password alike.
        raise HTTPException(status_code=400, detail="Email or password is incorrect.")
    _failures.pop(email, None)
    return _sign_in(response, user)


@router.post("/logout")
async def logout(
    response: Response,
    relay_session: str | None = Cookie(default=None, alias=config.SESSION_COOKIE),
):
    """Revoke the session server-side, then clear the cookie. Both halves
    matter: clearing only the cookie would leave a live session behind for
    anyone who had already copied it."""
    accounts.destroy_session(relay_session)
    response.delete_cookie(config.SESSION_COOKIE, path="/")
    return {"ok": True}


@router.post("/forgot")
async def forgot(body: ForgotBody):
    """Password reset link, or a one-click sign-in link. The screen asks which.

    Answers identically for an address with no account — see the enumeration
    note at the top of the module. The work is skipped, the reply is not.
    """
    mode = body.mode if body.mode in ("reset", "magic") else "reset"
    user = accounts.by_email(body.email)
    if user is not None:
        kind = "reset" if mode == "reset" else "magic"
        if not mailer.throttled(user.email, kind):
            if mode == "reset":
                token = accounts.issue_token(user.id, "reset", config.RESET_TTL_SECONDS)
                mailer.send_reset(user.email, user.first_name, token)
            else:
                token = accounts.issue_token(user.id, "magic", config.MAGIC_TTL_SECONDS)
                mailer.send_magic(user.email, user.first_name, token)
    return {"ok": True, "message": SENT_MESSAGE, "mode": mode}


@router.get("/reset/check")
async def reset_check(token: str = ""):
    """Is this reset link still good? Drawn before the form, so a dead link
    says so instead of letting someone type a new password into nothing.

    `peek`, not `consume`: spending the token here would break the form on a
    refresh, and a refresh is exactly what someone does when a page looks odd.
    """
    user = accounts.peek_token(token, "reset")
    if user is None:
        return {"valid": False}
    return {"valid": True, "email": user.email, "first_name": user.first_name}


@router.post("/reset")
async def reset(body: ResetBody, response: Response):
    """Spend a reset link and set the new password.

    The token is consumed before the password is validated, so a weak password
    costs the user their link. That is the deliberate order: a token that
    survived a failed attempt could be retried, which is most of the value of
    single-use gone. The message says to request a fresh link.
    """
    user = accounts.consume_token(body.token, "reset")
    if user is None:
        raise HTTPException(
            status_code=400,
            detail="That reset link has expired or was already used. Request a new one.",
        )
    try:
        accounts.set_password(user.id, body.password, body.confirm_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # They read mail at that address, which is what verification asks.
    accounts.mark_verified(user.id)
    # `set_password` dropped every session, this one included, so sign them
    # back in. Landing on a login screen after proving who you are is a
    # pointless extra step.
    return _sign_in(response, accounts.by_id(user.id))


@router.post("/magic")
async def magic(body: TokenBody, response: Response):
    """Spend a sign-in link. Same inbox-proves-the-address rule as reset."""
    user = accounts.consume_token(body.token, "magic")
    if user is None:
        raise HTTPException(
            status_code=400,
            detail="That sign-in link has expired or was already used. Request a new one.",
        )
    if not user.verified:
        accounts.mark_verified(user.id)
        user = accounts.by_id(user.id)
    return _sign_in(response, user)


@router.post("/verify")
async def verify(body: TokenBody, response: Response):
    """Spend a verification link.

    Signs the browser in as well. The common path here is someone opening the
    link on their phone while signed in on a laptop, and leaving the phone at a
    "verified, now log in" dead end helps nobody.
    """
    user = accounts.consume_token(body.token, "verify")
    if user is None:
        raise HTTPException(
            status_code=400,
            detail="That link has expired or was already used. Send yourself a new one.",
        )
    accounts.mark_verified(user.id)
    return _sign_in(response, accounts.by_id(user.id))


@router.post("/verify/resend")
async def resend_verification(
    relay_session: str | None = Cookie(default=None, alias=config.SESSION_COOKIE),
):
    """Another verification mail for the signed-in account.

    Session-only: without it this would mail anyone an unsolicited link for
    typing an address into a form.
    """
    user = current_user(relay_session)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in first.")
    if user.verified:
        return {"ok": True, "message": "That address is already confirmed."}
    if not _send_verification(user):
        raise HTTPException(
            status_code=429,
            detail="A link went out in the last minute. Check your inbox and spam folder.",
        )
    return {"ok": True, "message": f"Sent. Check {user.email}."}
