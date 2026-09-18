"""Outbound email: one `send` function, two backends, and an outbox for tests.

There is no mail provider wired up yet, and the whole module is shaped around
that fact rather than pretending otherwise. `console` — the default — writes
the mail to the server log and keeps it in `outbox`. In development that is
strictly better than a real send: the verification link is right there in the
terminal you already have open, with nothing to configure and no inbox to go
and check.

Switching to real mail is meant to be an environment change, not a code change:

    RELAY_MAIL_BACKEND=smtp
    RELAY_SMTP_HOST=smtp.example.com
    RELAY_SMTP_PORT=587
    RELAY_SMTP_USER=relay@example.com
    RELAY_SMTP_PASSWORD=...
    RELAY_MAIL_FROM="The Relay <relay@example.com>"

An API provider (Resend, Postmark, SendGrid) would be a third backend next to
`_send_smtp` — one function that takes the same three arguments. Nothing in
`backend/auth.py` would move.

A note on failure: `send` never raises. A signup that succeeds and then dies on
a mail server timeout would leave an account created, a 500 on screen, and a
user with no idea which of the two happened. The send is logged as failed, the
route carries on, and the user can ask for another link.
"""

from __future__ import annotations

import logging
import os
import smtplib
import sys
import time
from dataclasses import dataclass, field
from email.message import EmailMessage

from backend import config

log = logging.getLogger("relay.mail")

# Give the logger its own handler on stdout.
#
# Without this the console backend is silent and useless: uvicorn configures
# only the `uvicorn.*` loggers, the root logger defaults to WARNING, and an
# INFO record from a logger nobody set up is dropped on the floor. The whole
# reason the console backend exists is that the developer sees the link in the
# terminal they already have open, so "the mail was sent and you cannot read
# it" is the one outcome it must not produce.
#
# `propagate = False` because this handler is the delivery, not a debug echo:
# leaving it on would print every mail twice the moment anything configures
# the root logger.
if not log.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
    log.propagate = False

# Every mail the console backend has "sent", newest last. The tests read it to
# pull a link out; a dev can read it through God mode's log. Capped so a long
# run cannot grow it without bound.
OUTBOX_LIMIT = 200
outbox: list["Mail"] = []

# (address, kind) -> monotonic time of the last send. The throttle that stops a
# refresh loop from turning us into somebody's inbox problem. In memory on
# purpose: it is a courtesy limit, and forgetting it on restart is harmless.
_last_send: dict[tuple[str, str], float] = {}


@dataclass
class Mail:
    to: str
    subject: str
    body: str
    kind: str = "generic"
    sent_at: float = field(default_factory=time.time)
    delivered: bool = True   # False when a real backend refused it


def _backend() -> str:
    return os.environ.get("RELAY_MAIL_BACKEND", "console").strip().lower()


def _from_address() -> str:
    return os.environ.get("RELAY_MAIL_FROM", "The Relay <no-reply@relay.local>")


def base_url() -> str:
    """Where the links in an email point.

    Configured rather than taken from the incoming request's Host header: that
    header is attacker-controlled, and a reset link built from it is the
    classic way to have your own server mail a working password reset to
    somebody else's domain.
    """
    return os.environ.get("RELAY_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def throttled(to: str, kind: str) -> bool:
    """True when this address has had this kind of mail too recently.

    Checked by the caller before minting a token, so a throttled request does
    not silently invalidate the link the user is already holding.
    """
    last = _last_send.get((to.lower(), kind))
    if last is None:
        return False
    return (time.monotonic() - last) < config.EMAIL_SEND_MIN_INTERVAL_SECONDS


def send(to: str, subject: str, body: str, kind: str = "generic") -> Mail:
    """Send one mail. Never raises; check `.delivered` if the caller cares."""
    mail = Mail(to=to, subject=subject, body=body, kind=kind)
    try:
        if _backend() == "smtp":
            _send_smtp(mail)
        else:
            _send_console(mail)
    except Exception as exc:                      # noqa: BLE001 - see docstring
        mail.delivered = False
        log.warning("mail to %s failed (%s): %s", to, kind, exc)
    _last_send[(to.lower(), kind)] = time.monotonic()
    outbox.append(mail)
    del outbox[:-OUTBOX_LIMIT]
    return mail


def _send_console(mail: Mail) -> None:
    log.info(
        "\n--- mail (%s) ---\nTo: %s\nSubject: %s\n\n%s\n--- end mail ---",
        mail.kind, mail.to, mail.subject, mail.body,
    )


def _send_smtp(mail: Mail) -> None:
    host = os.environ.get("RELAY_SMTP_HOST", "")
    if not host:
        raise RuntimeError("RELAY_MAIL_BACKEND=smtp but RELAY_SMTP_HOST is unset")
    port = int(os.environ.get("RELAY_SMTP_PORT", "587"))
    user = os.environ.get("RELAY_SMTP_USER", "")
    password = os.environ.get("RELAY_SMTP_PASSWORD", "")

    message = EmailMessage()
    message["From"] = _from_address()
    message["To"] = mail.to
    message["Subject"] = mail.subject
    message.set_content(mail.body)

    with smtplib.SMTP(host, port, timeout=10) as smtp:
        smtp.starttls()
        if user:
            smtp.login(user, password)
        smtp.send_message(message)


# --- the three mails an account can produce -------------------------------
#
# Plain text, and short. Every one of them exists to carry a single link, and a
# wall of marketing copy around that link only makes it harder to find.

def send_verify(to: str, name: str, token: str) -> Mail:
    link = f"{base_url()}/verify?token={token}"
    return send(
        to,
        "Confirm your email for The Relay",
        f"Hi {name},\n\n"
        f"Confirm this address to secure your account:\n\n{link}\n\n"
        f"The link works for 24 hours. If you did not sign up for The Relay, "
        f"ignore this message and nothing will happen.\n",
        kind="verify",
    )


def send_reset(to: str, name: str, token: str) -> Mail:
    link = f"{base_url()}/reset?token={token}"
    return send(
        to,
        "Reset your Relay password",
        f"Hi {name},\n\n"
        f"Set a new password here:\n\n{link}\n\n"
        f"The link works for 1 hour and can be used once. If you did not ask "
        f"for a reset, ignore this message: your password has not changed.\n",
        kind="reset",
    )


def send_magic(to: str, name: str, token: str) -> Mail:
    link = f"{base_url()}/magic?token={token}"
    return send(
        to,
        "Your sign-in link for The Relay",
        f"Hi {name},\n\n"
        f"Sign in here, no password needed:\n\n{link}\n\n"
        f"The link works for 15 minutes and can be used once. If you did not "
        f"ask to sign in, ignore this message.\n",
        kind="magic",
    )
