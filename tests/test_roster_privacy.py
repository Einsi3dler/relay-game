"""The roster must not reach the repository.

`var/rollcall/roster.json` is real people's email addresses and the personal
statements they wrote about themselves, and this repository is public. It is
gitignored, which stops the file itself being committed and does nothing at all
about the far likelier mistake: somebody pasting one of the answers into a
document, a comment or a test fixture as an example. That has already happened
once (docs/ROLL_CALL_HANDOFF.md quoted an answer verbatim, three sections below
the rule forbidding it), which is why this exists.

So the needles are derived from the data rather than written by hand, because
what you write by hand is the set of mistakes you already thought of.

They have to be things that actually identify somebody:

  * a full address,
  * the distinctive local part of one,
  * a five-word phrase from an answer, which cannot appear by coincidence.

An earlier version of this check also matched single words and duly reported
'because', 'through' and 'question' across most of the repository. A check that
cries wolf is worse than no check, because it teaches you to skip the output.

Skips when the roster is absent, which is the normal state for CI and for every
contributor who is not running the session.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ROSTER = REPO / "var" / "rollcall" / "roster.json"

PHRASE_LEN = 5
MIN_LOCAL_PART = 6      # "caroline" is worth checking, "bob" is not


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _phrases(words: list[str]) -> set[str]:
    return {
        " ".join(words[i:i + PHRASE_LEN])
        for i in range(len(words) - PHRASE_LEN + 1)
    }


def _load_needles() -> tuple[set[str], set[str]]:
    """(literal strings, phrases) drawn out of the roster.

    A plain function, deliberately not a fixture. pytest prints every fixture's
    value in the header of a failing test, so a `needles` fixture would dump
    the addresses and answer fragments into the output at exactly the moment
    the test fires -- into CI logs, into scrollback, into whatever somebody
    pastes into a bug report. The check would become the leak.
    """
    roster = json.loads(ROSTER.read_text(encoding="utf-8"))
    literals: set[str] = set()
    phrases: set[str] = set()

    for person in roster["participants"]:
        email = person["email"].lower()
        literals.add(email)
        local = email.split("@", 1)[0]
        if len(local) >= MIN_LOCAL_PART:
            literals.add(local)
        for body in person["answers"].values():
            phrases |= _phrases(_words(body))

    return literals, phrases


def _tracked_files() -> list[Path]:
    listing = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    )
    return [
        path for name in listing.stdout.split()
        if (path := REPO / name).is_file()
    ]


def test_roster_is_gitignored():
    """The file itself, before worrying about its contents."""
    if not ROSTER.exists():
        pytest.skip("no roster on this machine")
    check = subprocess.run(
        ["git", "check-ignore", str(ROSTER.relative_to(REPO))],
        cwd=REPO, capture_output=True, text=True,
    )
    assert check.returncode == 0, "roster.json is NOT gitignored"


def _scan_tracked_files() -> list[str]:
    """Do the whole comparison here and hand back only clean strings.

    The needles must never be a local of the *failing* frame. `pytest
    --showlocals` prints the locals of every frame in the traceback, so a test
    that held `literals` and `phrases` itself would print the roster the moment
    it failed. This function has returned by the time anything is raised, so
    its frame is not on the stack and its locals cannot be dumped.
    """
    literals, phrases = _load_needles()
    assert literals and phrases, "roster parsed but yielded no needles"

    hits: set[str] = set()
    for path in _tracked_files():
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        name = str(path.relative_to(REPO))
        if any(literal in text for literal in literals):
            hits.add(f"{name}: contains an address or local part")
        # Set intersection rather than a substring scan per phrase: hundreds of
        # phrases times hundreds of files is slow enough that it gets skipped.
        if _phrases(_words(text)) & phrases:
            hits.add(f"{name}: contains a verbatim answer")
    return sorted(hits)


def test_no_roster_content_in_tracked_files():
    if not ROSTER.exists():
        pytest.skip("no roster on this machine; nothing to leak")

    hits = _scan_tracked_files()
    if hits:
        # `pytest.fail`, not `assert`: an assert prints the comparison it just
        # made. The report names files and says nothing about what was found in
        # them -- whoever is fixing it has the roster on disk and can grep.
        pytest.fail(
            "roster content found in tracked files (contents withheld on "
            "purpose):\n  " + "\n  ".join(hits)
        )
