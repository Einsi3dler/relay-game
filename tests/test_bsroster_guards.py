"""BULLSHIT QUIZ cannot mail its roster, and its roster stays out of git.

docs/ROLL_CALL_DO_NOT_SEND.md is the standing order, and it covers this
game's roster as much as ROLL CALL's: nothing sends mail to a roster address
until the repository owner says so. The guarantee is that no code path
exists, so these check that none of the game's modules reaches the mailer,
by source and in a clean interpreter.

The privacy check derives its needles from `var/bullshit/roster.json` when it
exists (it is gitignored and absent in CI) and scans every tracked file for
them. It scans itself too, so nothing in here may be copied from the data.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MODULES = ("bsquiz", "bsroster", "bsroom", "bsserver")
ROSTER = REPO / "var" / "bullshit" / "roster.json"


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


def test_no_tracked_file_carries_a_roster_address():
    if not ROSTER.exists():
        pytest.skip("no Bullshit Quiz roster on this machine")
    people = json.loads(ROSTER.read_text(encoding="utf-8"))["participants"]
    needles = {p["email"].strip().lower() for p in people if p.get("email")}
    tracked = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True,
                             text=True, check=True).stdout.split()
    leaks = []
    for name in tracked:
        try:
            text = (REPO / name).read_text(encoding="utf-8").lower()
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        leaks += [f"{name}: {n}" for n in needles if n in text]
    assert not leaks, "roster addresses in tracked files:\n  " + "\n  ".join(leaks)
