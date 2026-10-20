"""BULLSHIT QUIZ cannot send mail, and does not store email addresses.

Players join through their site account with the host's join code; the
game keeps no roster file and no addresses. These check that none of the
game's modules reaches the mailer (by source and in a clean interpreter,
the reasoning in docs/ROLL_CALL_DO_NOT_SEND.md), and that its store has no
column an address could live in.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MODULES = ("bsquiz", "bsroster", "bsroom", "bsserver")


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
            names += [alias.name for alias in node.names]
    return names


@pytest.mark.parametrize("module", MODULES)
def test_source_does_not_import_the_mailer(module):
    path = REPO / "backend" / f"{module}.py"
    if not path.exists():
        pytest.skip(f"{module} not written yet")
    hits = [n for n in _imports(path) if "mailer" in n.split(".")]
    assert not hits, (f"backend/{module}.py reaches the mailer: {hits}. "
                      "Read docs/ROLL_CALL_DO_NOT_SEND.md.")


@pytest.mark.parametrize("module", MODULES)
def test_importing_does_not_pull_in_the_mailer(module):
    if not (REPO / "backend" / f"{module}.py").exists():
        pytest.skip(f"{module} not written yet")
    probe = (f"import sys; import backend.{module}; "
             "print('backend.mailer' in sys.modules)")
    result = subprocess.run([sys.executable, "-c", probe], cwd=REPO,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False", (
        f"importing backend.{module} drags in the mailer. "
        "Read docs/ROLL_CALL_DO_NOT_SEND.md.")


def test_the_store_keeps_no_email_addresses():
    from backend import bsroster
    db = bsroster._conn()
    for table in ("seat", "seat_question"):
        columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        assert not any("mail" in c for c in columns), (table, columns)
