"""BOMB DEFUSE: the browser must judge an action exactly as the server does.

Sudden death is enforced in the renderer — a wrong maze step blows the bomb up
the instant the player takes it, not when they submit — which is only honest if
the renderer's rules are the server's rules. `fixtures/bomb_defuse_cases.json`
is the shared contract: hand-built boards that trip one rule each, real
generated boards at five levels, and for every case the verdict plus the
per-bay progress the face draws from. The first tests lock the Python side to
it; the last runs the real `frontend/games/bomb_defuse.js` through node and
locks the JavaScript one to the same file.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.games.game11_bomb_defuse import (
    MAZE_LAYOUTS, NUMBER_PATTERNS, SIMON_COLOURS, SIMON_MAP, validate,
)

FIXTURE = Path(__file__).parent / "fixtures" / "bomb_defuse_cases.json"
FRONTEND = Path(__file__).parents[2] / "frontend" / "games"
RENDERER = FRONTEND / "bomb_defuse.js"
MANUAL = FRONTEND / "bomb_manual.js"

# A bare `window` is all either module body touches; `document` only appears
# inside mount() and the manual's render(), neither of which this harness calls.
# The manual loads first because the bomb binds to its tables at module scope.
HARNESS = """
const fs = require("fs");
const vm = require("vm");
const context = { window: {} };
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[4], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
const game = context.window.RelayGames.bomb_defuse;
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8")).cases;
process.stdout.write(JSON.stringify({
  verdicts: cases.map(function (entry) {
    return game.__validate(entry.payload, entry.moves, entry.partial);
  }),
  data: game.__data,
}));
"""


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def test_fixture_covers_every_rule():
    cases = load_fixture()["cases"]
    assert len(cases) >= 60
    # Every verdict the replay can report has at least one case.
    reasons = {case["expected"]["reason"] for case in cases}
    assert reasons == {
        "", "bad_shape", "bad_action", "too_many_moves", "unknown_module",
        "already_solved", "maze_wall", "simon_wrong", "atn_wrong", "mini_code",
        "premature_ok", "after_ok", "missing_ok",
    }
    # Both halves of the partial contract, and real boards alongside the
    # hand-built ones.
    assert {case["partial"] for case in cases} == {True, False}
    assert any(case["expected"]["defused"] for case in cases)
    assert any(case["name"].startswith("generated") for case in cases)
    # Every module type is exercised.
    types = {
        module["type"]
        for case in cases
        for module in case["payload"]["modules"]
    }
    assert types == {"maze", "simon", "according_to_number", "mini_button"}


def test_python_matches_the_fixture():
    for case in load_fixture()["cases"]:
        got = validate(case["payload"], case["moves"], case["partial"])
        assert got == case["expected"], case["name"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_javascript_matches_the_fixture(tmp_path):
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS)
    finished = subprocess.run(
        ["node", str(harness), str(RENDERER), str(FIXTURE), str(MANUAL)],
        capture_output=True, text=True, timeout=60,
    )
    assert finished.returncode == 0, finished.stderr
    result = json.loads(finished.stdout)

    for case, got in zip(load_fixture()["cases"], result["verdicts"]):
        assert got == case["expected"], case["name"]

    # Both seats' manuals and the browser's rules mirror are drawn from one
    # table, so a drift here would quietly show someone the wrong walls.
    data = result["data"]
    assert data["SIMON_MAP"] == SIMON_MAP
    assert data["SIMON_COLOURS"] == list(SIMON_COLOURS)
    assert data["NUMBER_PATTERNS"] == [
        [list(row) for row in pattern] for pattern in NUMBER_PATTERNS
    ]
    assert data["MAZE_LAYOUTS"] == [
        {
            "tip": list(layout["tip"]),
            "h": [list(row) for row in layout["h"]],
            "v": [list(row) for row in layout["v"]],
        }
        for layout in MAZE_LAYOUTS
    ]
