"""The deployment check (backend/deploycheck.py) and the templates it vouches for.

Every check is a pure function of the environment it is handed, plus the
mail rules, which read os.environ; the fixture clears every RELAY_ variable
so the developer's own shell never leaks into a result.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from backend import deploycheck
from backend.deploycheck import FAIL, PASS, WARN

REPO = Path(__file__).resolve().parent.parent
GOOD_KEYS = {
    "RELAY_BULLSHIT_HOST_KEY": "b" * 24,
    "RELAY_QUIZ_HOST_KEY": "q" * 24,
    "RELAY_GOD_KEY": "g" * 24,
    "RELAY_PREVIEW_KEY": "p" * 24,
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in [k for k in os.environ if k.startswith("RELAY_")]:
        monkeypatch.delenv(key)


def levels(found) -> list[str]:
    return [f.level for f in found]


def by_message(found, text: str):
    return [f for f in found if text in f.message]


# --- host keys ------------------------------------------------------------

def test_unset_keys_fail_except_the_preview():
    found = deploycheck.check_keys({})
    assert levels(by_message(found, "RELAY_BULLSHIT_HOST_KEY")) == [FAIL]
    assert levels(by_message(found, "RELAY_QUIZ_HOST_KEY")) == [FAIL]
    assert levels(by_message(found, "RELAY_GOD_KEY")) == [FAIL]
    assert levels(by_message(found, "RELAY_PREVIEW_KEY")) == [WARN]


@pytest.mark.parametrize("value", ["dev", "change-me", "1234567890", "replace-me", "  dev "])
def test_values_from_this_repository_count_as_unset(value):
    found = deploycheck.check_keys({**GOOD_KEYS, "RELAY_GOD_KEY": value})
    assert levels(by_message(found, "RELAY_GOD_KEY")) == [FAIL]


def test_good_keys_pass():
    assert set(levels(deploycheck.check_keys(GOOD_KEYS))) == {PASS}


def test_short_and_shared_keys_warn():
    env = {**GOOD_KEYS, "RELAY_QUIZ_HOST_KEY": "short-but-set",
           "RELAY_PREVIEW_KEY": GOOD_KEYS["RELAY_BULLSHIT_HOST_KEY"]}
    found = deploycheck.check_keys(env)
    assert levels(by_message(found, "only 13 characters")) == [WARN]
    assert levels(by_message(found, "same as RELAY_BULLSHIT_HOST_KEY")) == [WARN]


# --- the public address and mail ------------------------------------------

def test_no_base_url_and_no_mail_is_a_warning():
    assert levels(deploycheck.check_base_url_and_mail({})) == [WARN]


def test_a_public_base_url_without_mail_fails(monkeypatch):
    monkeypatch.setenv("RELAY_BASE_URL", "https://relay.example.com")
    found = deploycheck.check_base_url_and_mail({"RELAY_BASE_URL": "https://relay.example.com"})
    assert levels(found) == [FAIL]
    assert "refuses to start" in found[0].fix


def test_the_full_mail_setup_passes(monkeypatch):
    env = {"RELAY_BASE_URL": "https://relay.example.com", "RELAY_MAIL_BACKEND": "smtp",
           "RELAY_SMTP_HOST": "smtp.example.com",
           "RELAY_MAIL_FROM": "The Relay <no-reply@relay.example.com>"}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert levels(deploycheck.check_base_url_and_mail(env)) == [PASS]


# --- databases and the roster ---------------------------------------------

def test_databases_inside_the_checkout_warn():
    found = deploycheck.check_databases({}, as_root=False)
    assert levels(found) == [WARN, WARN, WARN]
    assert all("inside the checkout" in f.message for f in found)


def test_databases_outside_the_checkout_pass(tmp_path):
    env = {name: str(tmp_path / "data" / f"{i}.db")
           for i, (name, _, _) in enumerate(deploycheck.DATABASES)}
    assert levels(deploycheck.check_databases(env, as_root=False)) == [PASS, PASS, PASS]


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root can write anywhere")
def test_an_unwritable_folder_fails(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        found = deploycheck.check_databases({"RELAY_DB_PATH": str(locked / "relay.db"),
                                             "RELAY_BULLSHIT_DB": str(tmp_path / "b.db"),
                                             "RELAY_ROLLCALL_DB": str(tmp_path / "r.db")},
                                            as_root=False)
        assert levels(found) == [FAIL, PASS, PASS]
    finally:
        locked.chmod(stat.S_IRWXU)


def test_the_roster_must_be_private(tmp_path):
    roster = tmp_path / "roster.json"
    env = {"RELAY_ROLLCALL_ROSTER": str(roster)}
    assert deploycheck.check_roster(env) == []
    roster.write_text("[]")
    roster.chmod(0o644)
    assert levels(deploycheck.check_roster(env)) == [WARN]
    roster.chmod(0o600)
    assert levels(deploycheck.check_roster(env)) == [PASS]


# --- the unit -------------------------------------------------------------

@pytest.mark.parametrize("exec_start, expected", [
    ("", []),
    ("{ path=/srv/relay-game/.venv/bin/uvicorn ; argv[]=uvicorn backend.main:app --port 8000 }", [PASS]),
    ("argv[]=uvicorn backend.main:app --workers 2", [FAIL]),
    ("argv[]=uvicorn backend.main:app --workers=4 --reload", [FAIL, WARN]),
    ("argv[]=uvicorn backend.main:app --reload", [WARN]),
    ("argv[]=/srv/relay-game/run.sh", [WARN]),
])
def test_exec_start(exec_start, expected):
    assert levels(deploycheck.check_exec_start(exec_start)) == expected


def test_parse_environment_reads_the_unit_then_its_files():
    env = deploycheck.parse_environment(
        'RELAY_GOD_KEY=from-unit "RELAY_MAIL_FROM=The Relay <a@b.c>"',
        ["# a comment\n\nRELAY_GOD_KEY=from-file\nRELAY_BASE_URL=\"https://x.example\"\n"])
    assert env == {"RELAY_GOD_KEY": "from-file", "RELAY_MAIL_FROM": "The Relay <a@b.c>",
                   "RELAY_BASE_URL": "https://x.example"}


# --- the templates --------------------------------------------------------

def test_the_environment_template_can_never_pass_as_is():
    template = (REPO / "deploy" / "relay-game.env.example").read_text()
    env = deploycheck.parse_environment("", [template])
    found = deploycheck.check_keys(env)
    assert levels(found) == [FAIL, FAIL, FAIL, WARN]
    assert all(env[name].startswith("/var/lib/relay/")
               for name, _, _ in deploycheck.DATABASES)


def test_the_unit_template_runs_one_worker_without_reload():
    unit = (REPO / "deploy" / "relay-game.service").read_text().replace("\\\n", " ")
    exec_start = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
    assert levels(deploycheck.check_exec_start(exec_start)) == [PASS]
    assert "EnvironmentFile=/etc/relay-game.env" in unit
    assert "ReadWritePaths=/var/lib/relay" in unit


def test_the_whole_report_counts_failures(capsys):
    assert deploycheck.report([deploycheck.Finding(PASS, "fine")]) == 0
    assert deploycheck.report([deploycheck.Finding(WARN, "hm", "fix")]) == 0
    assert deploycheck.report([deploycheck.Finding(FAIL, "no", "fix")]) == 1
    assert "1 problem(s)" in capsys.readouterr().out
