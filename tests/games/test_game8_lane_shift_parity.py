"""LANE SHIFT: the browser simulation must agree with the server's, turn for
turn (game/RELAY_EXPANSION_GAMES_README.md §2 "minimum acceptance tests").

`fixtures/lane_shift_cases.json` is the shared contract: one entry per board
and schedule, holding the full state — tick, packet positions, junction
settings and hold charges — after every turn, including the turn a run dies on.
The first test locks the Python simulator to it; the second runs the real
renderer through node and locks the JavaScript one to the same file.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.games.game8_lane_shift import (
    _initial_state,
    _solved,
    _step,
    board_from_payload,
)

FIXTURE = Path(__file__).parent / "fixtures" / "lane_shift_cases.json"
RENDERER = Path(__file__).parents[2] / "frontend" / "games" / "lane_shift.js"

# A bare `window` is all the renderer's module body touches; `document` only
# appears inside mount(), which the parity harness never calls.
HARNESS = """
const fs = require("fs");
const vm = require("vm");
const context = { window: {} };
vm.createContext(context);
// theme.js first, the way both pages load it: a renderer reads its colours
// from window.RelayTheme, so a harness without it is not the browser. Its path
// comes off the game file's own, since this script runs from a temp directory.
vm.runInContext(fs.readFileSync(
  process.argv[2].replace(/[\\/](games|duels)[\\/][^\\/]+$/, "/theme.js"),
  "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
const replay = context.window.RelayGames.lane_shift.__replay;
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8")).cases;
const out = cases.map(function (entry) { return replay(entry.payload, entry.actions); });
process.stdout.write(JSON.stringify(out));
"""


def load_cases() -> list[dict]:
    return json.loads(FIXTURE.read_text())["cases"]


def test_fixture_covers_wins_losses_and_short_runs():
    cases = load_cases()
    assert len(cases) >= 16
    assert {case["order"] for case in cases} == {
        "solution", "all-pass", "truncated", "shifted",
    }
    assert [case["solved"] for case in cases if case["order"] == "solution"]
    assert all(
        case["solved"] is False for case in cases if case["order"] in {"all-pass", "truncated"}
    )
    died = [case for case in cases if case["steps"][-1]["alive"] is False]
    assert died, "no fixture case exercises a failed run"


def test_server_simulation_matches_the_fixture():
    for case in load_cases():
        board = board_from_payload(case["payload"])
        state = _initial_state(board)
        for turn, expected in enumerate(case["steps"]):
            state = _step(board, state, tuple(case["actions"][turn]))
            if expected["alive"] is False:
                assert state is None, f"{case['label']} @{turn}"
                break
            assert state is not None, f"{case['label']} @{turn}"
            assert state[0] == expected["tick"]
            assert [list(p) for p in state[1]] == expected["positions"]
            assert list(state[2]) == expected["switchIndex"]
            assert list(state[3]) == expected["charges"]
            if _solved(state):
                break


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_browser_simulation_matches_the_fixture(tmp_path):
    harness = tmp_path / "parity.js"
    harness.write_text(HARNESS)
    result = subprocess.run(
        ["node", str(harness), str(RENDERER), str(FIXTURE)],
        capture_output=True, text=True, timeout=60, check=True,
    )
    produced = json.loads(result.stdout)
    cases = load_cases()
    assert len(produced) == len(cases)
    for case, run in zip(cases, produced):
        assert len(run["steps"]) == len(case["steps"]), case["label"]
        for turn, (step, expected) in enumerate(zip(run["steps"], case["steps"])):
            assert step["alive"] == expected["alive"], f"{case['label']} @{turn}"
            if not expected["alive"]:
                continue
            assert step["tick"] == expected["tick"], f"{case['label']} @{turn}"
            assert step["positions"] == expected["positions"], f"{case['label']} @{turn}"
            assert step["switchIndex"] == expected["switchIndex"], f"{case['label']} @{turn}"
            assert step["charges"] == expected["charges"], f"{case['label']} @{turn}"
        # Both simulators must agree on the verdict, not just the states.
        assert run["solved"] is case["solved"], case["label"]
