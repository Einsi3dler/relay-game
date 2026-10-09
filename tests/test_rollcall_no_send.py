"""ROLL CALL cannot mail the roster, and this is what makes that true.

`docs/ROLL_CALL_DO_NOT_SEND.md` is a standing order that nothing sends mail to
a roster address until the repository owner says so. A document cannot enforce
that. The absence of a code path can.

So: `backend/rollcall.py` does not reach `backend/mailer.py`, directly or
through anything it imports. If it ever does, this fails, and the right
response is to read that document rather than to delete this file.

Checked two ways on purpose. The source check names the offending line, which
is what you want when it breaks. The import check is the one that is actually
hard to fool, because it catches a mailer pulled in three modules deep by
something that looked unrelated.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODULE = REPO / "backend" / "rollcall.py"

FORBIDDEN = "mailer"


def test_rollcall_source_does_not_import_the_mailer():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"), filename=str(MODULE))
    offenders: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if FORBIDDEN in alias.name.split("."):
                    offenders.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if FORBIDDEN in module.split("."):
                offenders.append(f"line {node.lineno}: from {module} import …")
            for alias in node.names:
                if alias.name == FORBIDDEN:
                    offenders.append(
                        f"line {node.lineno}: from {module} import {alias.name}")

    assert not offenders, (
        "backend/rollcall.py reaches the mailer:\n  " + "\n  ".join(offenders)
        + "\nRead docs/ROLL_CALL_DO_NOT_SEND.md before changing this test."
    )


def test_importing_rollcall_does_not_pull_in_the_mailer():
    """Transitively, in a clean interpreter.

    A subprocess rather than checking `sys.modules` here: by the time the test
    suite runs, another test has long since imported the mailer, so an
    in-process check would pass no matter what this module does.
    """
    probe = (
        "import sys; import backend.rollcall; "
        "print('backend.mailer' in sys.modules or 'mailer' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False", (
        "importing backend.rollcall drags in the mailer through some other "
        "module. Read docs/ROLL_CALL_DO_NOT_SEND.md."
    )


def test_the_standing_order_still_exists():
    """If somebody deletes the document, the tests guarding it are next."""
    order = REPO / "docs" / "ROLL_CALL_DO_NOT_SEND.md"
    assert order.exists(), "docs/ROLL_CALL_DO_NOT_SEND.md has been deleted"
    assert "do not send" in order.read_text(encoding="utf-8").lower()
