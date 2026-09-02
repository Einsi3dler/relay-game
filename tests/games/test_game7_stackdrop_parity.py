"""STACKDROP: the browser simulation must agree with the server's, cell for
cell, after every removal (game/RELAY_EXPANSION_GAMES_README.md §3, §19).

`fixtures/stackdrop_cases.json` is the shared contract: one entry per chamber
and pull order, holding the ball state after each prefix of the removals. The
first test locks the Python simulator to it; the second runs the real renderer
through node and locks the JavaScript one to the same file. Regenerate the
fixture only when the rules deliberately change.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.games.game7_stackdrop import _play, chamber_from_payload

FIXTURE = Path(__file__).parent / "fixtures" / "stackdrop_cases.json"
RENDERER = Path(__file__).parents[2] / "frontend" / "games" / "stackdrop.js"

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
const replay = context.window.RelayGames.stackdrop.__replay;
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8")).cases;
const out = cases.map(function (entry) {
  return entry.removals.map(function (_, cut) {
    return replay(entry.payload, entry.removals.slice(0, cut + 1));
  });
});
process.stdout.write(JSON.stringify(out));
"""


def load_cases() -> list[dict]:
    return json.loads(FIXTURE.read_text())["cases"]


def test_fixture_covers_wins_and_losses():
    cases = load_cases()
    assert len(cases) >= 12
    outcomes = {
        (case["steps"][-1]["alive"], all(ball[2] for ball in case["steps"][-1]["balls"]))
        for case in cases
    }
    assert (True, True) in outcomes      # at least one winning order
    assert len(outcomes) > 1             # and at least one that does not win


def test_server_simulation_matches_the_fixture():
    for case in load_cases():
        chamber = chamber_from_payload(case["payload"])
        for cut, expected in enumerate(case["steps"]):
            state, alive = _play(chamber, case["removals"][:cut])
            assert [list(ball) for ball in state] == expected["balls"], case["label"]
            assert alive == expected["alive"], case["label"]


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
    for case, steps in zip(cases, produced):
        for cut, step in enumerate(steps, start=1):
            expected = case["steps"][cut]
            assert step["balls"] == expected["balls"], f"{case['label']} @{cut}"
            assert step["alive"] == expected["alive"], f"{case['label']} @{cut}"
