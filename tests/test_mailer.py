"""Outbound mail: message shape, connection modes, and the deployment guard.

The guard is the reason most of this exists. A half-configured deploy used to
be a silent failure whose only symptom was a player, days later, clicking a
password reset that pointed at their own laptop. `verify_deployment` turns that
into a server that will not start, and these tests pin the exact set of
configurations it refuses.
"""

from __future__ import annotations

import pytest

from backend import mailer

MAIL_ENV = (
    "RELAY_MAIL_BACKEND", "RELAY_BASE_URL", "RELAY_SMTP_HOST", "RELAY_SMTP_PORT",
    "RELAY_SMTP_USER", "RELAY_SMTP_PASSWORD", "RELAY_SMTP_SECURITY", "RELAY_MAIL_FROM",
)


@pytest.fixture
def env(monkeypatch):
    """A clean mail environment. Every variable is cleared, so a test says the
    whole configuration it is about and cannot inherit one from the shell."""
    for name in MAIL_ENV:
        monkeypatch.delenv(name, raising=False)
    mailer.outbox.clear()
    mailer._last_send.clear()
    return monkeypatch


def configure(env, **values):
    for name, value in values.items():
        env.setenv(name, value)


PRODUCTION = {
    "RELAY_MAIL_BACKEND": "smtp",
    "RELAY_SMTP_HOST": "smtp.example.com",
    "RELAY_BASE_URL": "https://relay.example.com",
}


# --- message shape --------------------------------------------------------

def test_message_has_date_and_message_id(env):
    """Regression: Gmail answers `550 5.7.1 Messages missing a valid
    Message-ID header are not accepted`. EmailMessage does not add one and
    neither does smtplib.send_message, so this module has to."""
    configure(env, RELAY_MAIL_FROM="The Relay <no-reply@relay.example.com>")
    message = mailer._build_message(mailer.Mail(to="ada@example.com", subject="s", body="b"))
    assert message["Message-ID"].startswith("<")
    assert message["Message-ID"].rstrip(">").endswith("@relay.example.com")
    assert message["Date"]


def test_message_id_domain_falls_back_to_the_site(env):
    """A From address without a parseable domain must still produce a
    Message-ID, not a crash on the send path."""
    configure(env, RELAY_MAIL_FROM="relay", RELAY_BASE_URL="https://relay.example.com")
    message = mailer._build_message(mailer.Mail(to="a@b.com", subject="s", body="b"))
    assert "@relay.example.com>" in message["Message-ID"]


def test_message_carries_the_obvious_headers(env):
    configure(env, RELAY_MAIL_FROM="The Relay <no-reply@relay.example.com>")
    message = mailer._build_message(
        mailer.Mail(to="ada@example.com", subject="Confirm your email", body="Hello")
    )
    assert message["To"] == "ada@example.com"
    assert message["Subject"] == "Confirm your email"
    assert message["From"] == "The Relay <no-reply@relay.example.com>"
    assert message.get_content().strip() == "Hello"


# --- base URL -------------------------------------------------------------

def test_base_url_defaults_to_dev_only(env):
    assert mailer.base_url() == mailer.DEV_BASE_URL


def test_base_url_is_configured_and_trimmed(env):
    configure(env, RELAY_BASE_URL="https://relay.example.com/")
    assert mailer.base_url() == "https://relay.example.com"


def test_links_are_built_from_the_base_url(env):
    configure(env, RELAY_BASE_URL="https://relay.example.com")
    mail = mailer.send_reset("ada@example.com", "Ada", "TOKEN")
    assert "https://relay.example.com/reset?token=TOKEN" in mail.body


@pytest.mark.parametrize("url", [
    "http://localhost:8000", "http://127.0.0.1:8000", "http://0.0.0.0:8000",
    "http://relay.local", "http://relay.internal", "http://192.168.1.50",
    "http://10.0.0.5", "http://host.docker.internal:8000", "",
])
def test_local_urls_are_recognised(url):
    assert mailer.is_local_url(url) is True


@pytest.mark.parametrize("url", [
    "https://relay.example.com", "https://play.relay.example.com", "http://relay.example.com",
])
def test_public_urls_are_not_local(url):
    assert mailer.is_local_url(url) is False


# --- the deployment guard -------------------------------------------------

def test_a_local_console_backend_is_never_blocked(env):
    """The developer default. Nothing it produces leaves the machine, so a
    localhost link in a terminal is correct rather than broken. A console
    backend on a *public* base URL is a different animal — see below."""
    configure(env, RELAY_BASE_URL="http://127.0.0.1:8000")
    assert mailer.configuration_problems() == []
    mailer.verify_deployment()


def test_a_good_production_config_passes(env):
    configure(env, **PRODUCTION, RELAY_SMTP_USER="relay@example.com",
              RELAY_SMTP_PASSWORD="hunter2")
    assert mailer.configuration_problems() == []
    mailer.verify_deployment()


@pytest.mark.parametrize("overrides,expected", [
    ({"RELAY_BASE_URL": None}, "RELAY_BASE_URL is not set"),
    ({"RELAY_BASE_URL": "http://localhost:8000"}, "which is this machine"),
    ({"RELAY_BASE_URL": "http://127.0.0.1:8000"}, "which is this machine"),
    ({"RELAY_BASE_URL": "http://192.168.1.50:8000"}, "which is this machine"),
    ({"RELAY_BASE_URL": "http://relay.local"}, "which is this machine"),
    ({"RELAY_BASE_URL": "http://relay.example.com"}, "is http"),
    ({"RELAY_BASE_URL": "relay.example.com"}, "needs a scheme"),
    ({"RELAY_SMTP_HOST": None}, "RELAY_SMTP_HOST is unset"),
])
def test_broken_production_configs_are_refused(env, overrides, expected):
    settings = dict(PRODUCTION)
    settings.update(overrides)
    configure(env, **{k: v for k, v in settings.items() if v is not None})
    for key, value in overrides.items():
        if value is None:
            env.delenv(key, raising=False)
    problems = mailer.configuration_problems()
    assert problems, f"expected {expected!r} to be refused"
    assert any(expected in problem for problem in problems), problems
    with pytest.raises(RuntimeError, match="will not work as deployed"):
        mailer.verify_deployment()


def test_plaintext_smtp_with_a_password_is_refused(env):
    """Sending credentials in clear text is worth refusing rather than warning."""
    configure(env, **PRODUCTION, RELAY_SMTP_SECURITY="none",
              RELAY_SMTP_PASSWORD="hunter2")
    assert any("clear text" in problem for problem in mailer.configuration_problems())


def test_plaintext_smtp_without_credentials_is_fine(env):
    """A local mail catcher has no password, and is the reason "none" exists."""
    configure(env, **PRODUCTION, RELAY_SMTP_SECURITY="none")
    assert mailer.configuration_problems() == []


def test_the_error_names_every_problem_at_once(env):
    """Fixing one variable only to be told about the next is a bad deploy loop."""
    configure(env, RELAY_MAIL_BACKEND="smtp")
    with pytest.raises(RuntimeError) as caught:
        mailer.verify_deployment()
    assert "RELAY_BASE_URL" in str(caught.value)
    assert "RELAY_SMTP_HOST" in str(caught.value)


# --- connection modes -----------------------------------------------------

@pytest.mark.parametrize("port,expected", [
    ("587", "starttls"),   # submission
    ("465", "ssl"),        # implicit TLS
    ("25", "starttls"),
    ("2525", "starttls"),
])
def test_security_follows_the_port(env, port, expected):
    configure(env, RELAY_SMTP_PORT=port)
    assert mailer._security() == expected


@pytest.mark.parametrize("value,expected", [
    ("starttls", "starttls"), ("ssl", "ssl"), ("tls", "ssl"), ("none", "none"),
    ("STARTTLS", "starttls"), ("nonsense", "starttls"),
])
def test_explicit_security_wins(env, value, expected):
    configure(env, RELAY_SMTP_SECURITY=value, RELAY_SMTP_PORT="587")
    assert mailer._security() == expected


def test_a_bad_port_does_not_crash(env):
    configure(env, RELAY_SMTP_PORT="not-a-number")
    assert mailer._smtp_port() == 587


# --- failure handling -----------------------------------------------------

def test_a_failed_send_never_raises(env):
    """A signup must not 500 because a mail server timed out. The caller gets
    `delivered=False` and the user can ask for another link."""
    configure(env, RELAY_MAIL_BACKEND="smtp", RELAY_SMTP_HOST="127.0.0.1",
              RELAY_SMTP_PORT="9", RELAY_BASE_URL="https://relay.example.com")
    mail = mailer.send("ada@example.com", "s", "b", kind="verify")
    assert mail.delivered is False


def test_smtp_without_a_host_fails_the_send_not_the_server(env):
    configure(env, RELAY_MAIL_BACKEND="smtp")
    assert mailer.send("ada@example.com", "s", "b").delivered is False


# --- console backend and throttling ---------------------------------------

def test_console_backend_records_the_mail(env):
    mail = mailer.send_verify("ada@example.com", "Ada", "TOKEN")
    assert mail.delivered is True
    assert mailer.outbox[-1].kind == "verify"
    assert "TOKEN" in mailer.outbox[-1].body


def test_throttle_is_per_address_and_kind(env):
    mailer.send_verify("ada@example.com", "Ada", "T1")
    assert mailer.throttled("ada@example.com", "verify") is True
    assert mailer.throttled("ada@example.com", "reset") is False
    assert mailer.throttled("someone@example.com", "verify") is False


def test_throttle_ignores_address_case(env):
    mailer.send_verify("Ada@Example.com", "Ada", "T1")
    assert mailer.throttled("ada@example.com", "verify") is True


def test_outbox_is_capped(env):
    for index in range(mailer.OUTBOX_LIMIT + 25):
        mailer.send(f"user{index}@example.com", "s", "b")
    assert len(mailer.outbox) == mailer.OUTBOX_LIMIT


# --- the refusal: deployed, but still mailing the log ----------------------
#
# The gap this closes: the console exemption used to be unconditional, which is
# right on a laptop and wrong on a deployed box. A server whose base URL is a
# public address is not developing — it is telling players their verification
# link is on its way while writing it to a log file.

def test_console_on_a_public_base_url_is_refused(env):
    configure(env, RELAY_BASE_URL="https://relay.example.com")
    problems = mailer.configuration_problems()
    assert problems, "console backend on a public origin should be refused"
    assert "never sent" in problems[0]
    with pytest.raises(RuntimeError, match="will not work as deployed"):
        mailer.verify_deployment()


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000", "http://localhost:8000", "http://relay.local",
    "http://192.168.1.50:8000",
])
def test_console_on_a_local_base_url_still_passes(env, url):
    """The developer default is untouched. Nothing is promised to anyone."""
    configure(env, RELAY_BASE_URL=url)
    assert mailer.configuration_problems() == []
    mailer.verify_deployment()


def test_console_with_no_base_url_still_passes(env):
    """Unset means development too — no public address to contradict."""
    assert mailer.configuration_problems() == []
    mailer.verify_deployment()


def test_the_console_refusal_names_the_way_out(env):
    """A refusal that does not say what to set is a worse outage."""
    configure(env, RELAY_BASE_URL="https://relay.example.com")
    message = mailer.configuration_problems()[0]
    assert "RELAY_MAIL_BACKEND=smtp" in message
    assert "unset RELAY_BASE_URL" in message


def test_delivers_mail_tracks_the_backend(env):
    assert mailer.delivers_mail() is False
    configure(env, **PRODUCTION)
    assert mailer.delivers_mail() is True
