"""Outbound email: one `send` function, two backends, and an outbox for tests.

There is no mail provider wired up yet, and the whole module is shaped around
that fact rather than pretending otherwise. `console` — the default — writes
the mail to the server log and keeps it in `outbox`. In development that is
strictly better than a real send: the verification link is right there in the
terminal you already have open, with nothing to configure and no inbox to go
and check.

Switching to real mail is meant to be an environment change, not a code change.
See `.env.example` for the full set:

    RELAY_BASE_URL=https://relay.example.com     # REQUIRED for real mail
    RELAY_MAIL_BACKEND=smtp
    RELAY_SMTP_HOST=smtp.example.com
    RELAY_SMTP_PORT=587
    RELAY_SMTP_USER=relay@example.com
    RELAY_SMTP_PASSWORD=...
    RELAY_MAIL_FROM="The Relay <relay@example.com>"

An API provider (Resend, Postmark, SendGrid) would be a third backend next to
`_send_smtp` — one function that takes the same three arguments. Nothing in
`backend/auth.py` would move.

`verify_deployment` is what stops a half-configured deploy from mailing links
that point at localhost. `backend/main.py` calls it at startup, and it raises
rather than warns: a warning in a log nobody is reading is how everybody's
password reset ends up pointing at 127.0.0.1.

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
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid

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

# A mail server that has stopped answering must not hold a signup open. Ten
# seconds is long enough for a slow provider and short enough that a dead one
# is noticed rather than waited on.
SMTP_TIMEOUT_SECONDS = 10
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


# Implicit TLS: the connection is encrypted from the first byte, so the client
# must open an SSL socket rather than upgrading a plaintext one.
IMPLICIT_TLS_PORT = 465


def _smtp_port() -> int:
    try:
        return int(os.environ.get("RELAY_SMTP_PORT", "587"))
    except ValueError:
        return 587


def _security() -> str:
    """How to encrypt the SMTP connection: "starttls", "ssl" or "none".

    Defaults by port rather than forcing one shape on every provider. Port 465
    is implicit TLS and 587 is submission-with-STARTTLS; getting these the wrong
    way round is the single most common reason a working set of credentials
    refuses to send, because the symptom is a hang rather than an error.

    "none" is for a local mail catcher (Mailpit, MailHog) and has to be asked
    for by name. It is never inferred, because silently downgrading to
    plaintext is the one failure mode you would want to be told about.
    """
    configured = os.environ.get("RELAY_SMTP_SECURITY", "").strip().lower()
    if configured in ("starttls", "ssl", "tls", "none"):
        return "ssl" if configured == "tls" else configured
    return "ssl" if _smtp_port() == IMPLICIT_TLS_PORT else "starttls"


# The dev fallback, used only when RELAY_BASE_URL is unset — which, thanks to
# `verify_deployment`, can only happen while mail is going to the console.
DEV_BASE_URL = "http://127.0.0.1:8000"

# Hosts that mean "this machine". A link to any of them is useless in an inbox:
# it resolves to the reader's own computer, not to the server that sent it.
LOCAL_HOSTS = frozenset({
    "localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]", "host.docker.internal",
})


def base_url() -> str:
    """Where the links in an email point.

    Configured rather than taken from the incoming request's Host header: that
    header is attacker-controlled, and a reset link built from it is the
    classic way to have your own server mail a working password reset to
    somebody else's domain.
    """
    return os.environ.get("RELAY_BASE_URL", "").strip().rstrip("/") or DEV_BASE_URL


def _host_of(url: str) -> str:
    """The hostname of a base URL, lowercased and without its port."""
    host = urlsplit(url).hostname or ""
    return host.lower()


def is_local_url(url: str) -> bool:
    """True when this URL points at the machine running the server.

    Used to decide whether a configuration is safe to mail from, so it is
    deliberately broad: `.local` and `.internal` names, and anything in a
    private range, are no more reachable from somebody's phone than 127.0.0.1.
    """
    host = _host_of(url)
    if not host:
        return True
    if host in LOCAL_HOSTS or host.endswith((".local", ".internal", ".localhost")):
        return True
    return host.startswith(("10.", "192.168.", "172.16.", "172.17.", "172.18."))


def delivers_mail() -> bool:
    """True when a send actually leaves this machine.

    The console backend "sends" by writing to the log. That is the right
    default for development, but it means anything that tells a player their
    mail is on its way is telling them something untrue, so the callers that
    say so ask this first.
    """
    return _backend() != "console"


def configuration_problems() -> list[str]:
    """Everything wrong with the current mail configuration, worst first.

    Split out from `verify_deployment` so it can be tested, and so a caller
    that wants to report rather than raise (deploy.sh preflight) can.

    The console backend is exempt from all of it. Nothing it produces leaves
    the machine, so a localhost link in a developer's terminal is correct
    rather than broken.
    """
    problems: list[str] = []
    configured = os.environ.get("RELAY_BASE_URL", "").strip()

    if _backend() == "console":
        # Exempt on a developer's machine: nothing leaves it, and a localhost
        # link in a terminal is correct rather than broken. Not exempt on a box
        # with a public base URL. Nothing local needs one, so a server that has
        # one is serving real people — and it is handing them a "check your
        # email" it cannot honour, with no way to confirm an address or recover
        # an account. That is worth refusing to start over.
        if configured and not is_local_url(configured.rstrip("/")):
            problems.append(
                f"RELAY_MAIL_BACKEND is unset, so mail only goes to this log, "
                f"but RELAY_BASE_URL is {configured} — a public address. "
                "Verification and reset links would be written here and never "
                "sent. Set RELAY_MAIL_BACKEND=smtp with the RELAY_SMTP_* "
                "variables and check it with `python -m backend.mailcheck`, or "
                "unset RELAY_BASE_URL if this is not a deployment."
            )
        return problems

    if not configured:
        problems.append(
            "RELAY_BASE_URL is not set, so every emailed link would point at "
            f"{DEV_BASE_URL} — the reader's own machine, not this server."
        )
    else:
        url = configured.rstrip("/")
        scheme = urlsplit(url).scheme
        if scheme not in ("http", "https"):
            problems.append(
                f"RELAY_BASE_URL ({configured!r}) needs a scheme: "
                "https://relay.example.com, not relay.example.com."
            )
        elif is_local_url(url):
            problems.append(
                f"RELAY_BASE_URL points at {_host_of(url)!r}, which is this "
                "machine. Links sent to a real inbox would be dead."
            )
        elif scheme != "https":
            problems.append(
                f"RELAY_BASE_URL ({url}) is http. Sign-in links and session "
                "cookies would travel in the clear, and the cookie would not "
                "be marked Secure. Use https."
            )

    if _backend() == "smtp" and not os.environ.get("RELAY_SMTP_HOST", "").strip():
        problems.append("RELAY_MAIL_BACKEND=smtp but RELAY_SMTP_HOST is unset.")

    if _security() == "none" and os.environ.get("RELAY_SMTP_PASSWORD", ""):
        problems.append(
            "RELAY_SMTP_SECURITY=none with a password set would send those "
            "credentials in clear text. Use starttls or ssl, or drop the "
            "password if this is a local mail catcher."
        )
    return problems


def verify_deployment() -> None:
    """Refuse to start on a configuration that would mail unusable links.

    Called from the app lifespan. Raising beats warning: deploy.sh health-checks
    the server after a restart and can roll back, so a hard failure surfaces at
    deploy time in front of somebody who can fix it. A warning surfaces days
    later, in the inbox of a player who cannot.
    """
    problems = configuration_problems()
    if not problems:
        return
    raise RuntimeError(
        "Mail configuration will not work as deployed:\n  - "
        + "\n  - ".join(problems)
        + "\n\nSee .env.example and docs/ACCOUNTS.md. To run without sending "
          "mail at all, leave RELAY_MAIL_BACKEND unset AND drop RELAY_BASE_URL: "
          "links then print to this log, which is the development default."
    )


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


def _sender_domain() -> str:
    """The domain to stamp Message-IDs with: the From address's, falling back
    to the site's own host."""
    addresses = getaddresses([_from_address()])
    if addresses and "@" in addresses[0][1]:
        return addresses[0][1].rsplit("@", 1)[1]
    return _host_of(base_url()) or "relay.local"


def _build_message(mail: Mail) -> EmailMessage:
    """Assemble the message, headers included.

    `Date` and `Message-ID` are set here because nothing else sets them.
    `EmailMessage` does not add them, and neither does `smtplib.send_message` —
    and a message without them is not merely untidy, it is refused: Gmail
    answers `550 5.7.1 Messages missing a valid Message-ID header are not
    accepted`, which is how this was found. RFC 5322 requires Date and From on
    every message, and Message-ID is what every provider and spam filter uses
    to tell one mail from a duplicate of it.
    """
    message = EmailMessage()
    message["From"] = _from_address()
    message["To"] = mail.to
    message["Subject"] = mail.subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=_sender_domain())
    message.set_content(mail.body)
    return message


def _send_smtp(mail: Mail) -> None:
    """Deliver over SMTP, in whichever of the three shapes the server speaks.

    Certificates are verified (smtplib's default context), so a self-signed or
    expired certificate fails the send rather than quietly accepting whoever
    answered. That is the correct behaviour and worth keeping.
    """
    host = os.environ.get("RELAY_SMTP_HOST", "").strip()
    if not host:
        raise RuntimeError("RELAY_MAIL_BACKEND=smtp but RELAY_SMTP_HOST is unset")
    port = _smtp_port()
    security = _security()
    user = os.environ.get("RELAY_SMTP_USER", "")
    password = os.environ.get("RELAY_SMTP_PASSWORD", "")
    message = _build_message(mail)

    if security == "ssl":
        with smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            if user:
                smtp.login(user, password)
            smtp.send_message(message)
        return

    with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
        if security == "starttls":
            smtp.starttls()
            smtp.ehlo()          # capabilities are re-read after the upgrade;
                                 # AUTH is often only advertised inside TLS
        if user:
            if security == "none":
                # Guarded here as well as in `configuration_problems`, because
                # this is the path that would actually put a password on the
                # wire in clear text.
                raise RuntimeError(
                    "refusing to send SMTP credentials over an unencrypted "
                    "connection (RELAY_SMTP_SECURITY=none)"
                )
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
