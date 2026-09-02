"""SHADOW CAST: the browser must agree with the server about the rotation
group and about every pose along the way (game/RELAY_EXPANSION_GAMES_README.md
§7 "minimum acceptance tests").

`fixtures/shadow_cast_cases.json` is the shared contract. It pins the ordered
table of 24 orientations — the table `initial_orientation` indexes into, so the
two sides would silently disagree about the whole board if it ever drifted —
and, per case, the normalised voxels and both silhouettes after every turn. The
first two tests lock the Python side to it; the third runs the real renderer
through node and locks the JavaScript one to the same file.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.games.game9_shadow_cast import ORIENTATIONS, replay

FIXTURE = Path(__file__).parent / "fixtures" / "shadow_cast_cases.json"
RENDERER = Path(__file__).parents[2] / "frontend" / "games" / "shadow_cast.js"

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
const game = context.window.RelayGames.shadow_cast;
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8")).cases;
process.stdout.write(JSON.stringify({
  orientations: game.__orientations,
  runs: cases.map(function (entry) { return game.__replay(entry.payload, entry.turns); }),
}));
"""


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def run_renderer(tmp_path) -> dict:
    harness = tmp_path / "parity.js"
    harness.write_text(HARNESS)
    result = subprocess.run(
        ["node", str(harness), str(RENDERER), str(FIXTURE)],
        capture_output=True, text=True, timeout=60, check=True,
    )
    return json.loads(result.stdout)


def test_fixture_covers_every_outcome():
    fixture = load_fixture()
    cases = fixture["cases"]
    assert len(cases) >= 40
    assert {case["order"] for case in cases} == {
        "solution", "equivalent", "wrong", "loop", "illegal",
    }
    by_order = lambda name: [case for case in cases if case["order"] == name]  # noqa: E731
    # Reference solutions and their padded-out variants land; wrong poses and
    # illegal tokens do not.
    assert all(case["matched"] for case in by_order("solution"))
    assert all(case["matched"] for case in by_order("loop"))
    assert by_order("equivalent") and all(case["matched"] for case in by_order("equivalent"))
    assert all(case["matched"] is False for case in by_order("wrong"))
    assert all(case["legal"] is False for case in by_order("illegal"))
    # Both grid sizes and the whole level span are represented.
    assert {case["payload"]["bound"] for case in cases} == {3, 4}
    assert {case["payload"]["variant"] for case in cases} == {"main", "holding"}


def test_server_orientation_table_matches_the_fixture():
    pinned = load_fixture()["orientations"]
    assert len(pinned) == 24
    assert [[list(row) for row in matrix] for matrix in ORIENTATIONS] == pinned


def test_server_simulation_matches_the_fixture():
    for case in load_fixture()["cases"]:
        walk = replay(case["payload"], case["turns"])
        assert walk["legal"] is case["legal"], case["label"]
        assert walk["matched"] is case["matched"], case["label"]
        assert len(walk["steps"]) == len(case["steps"]), case["label"]
        for turn, (step, expected) in enumerate(zip(walk["steps"], case["steps"])):
            assert step["voxels"] == expected["voxels"], f"{case['label']} @{turn}"
            assert step["front"] == expected["front"], f"{case['label']} @{turn}"
            assert step["top"] == expected["top"], f"{case['label']} @{turn}"
            assert step["matched"] is expected["matched"], f"{case['label']} @{turn}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_browser_orientation_table_matches_the_fixture(tmp_path):
    produced = run_renderer(tmp_path)
    assert produced["orientations"] == load_fixture()["orientations"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_browser_simulation_matches_the_fixture(tmp_path):
    produced = run_renderer(tmp_path)["runs"]
    cases = load_fixture()["cases"]
    assert len(produced) == len(cases)
    for case, run in zip(cases, produced):
        assert run["legal"] is case["legal"], case["label"]
        assert run["matched"] is case["matched"], case["label"]
        assert len(run["steps"]) == len(case["steps"]), case["label"]
        for turn, (step, expected) in enumerate(zip(run["steps"], case["steps"])):
            assert step["voxels"] == expected["voxels"], f"{case['label']} @{turn}"
            assert step["front"] == expected["front"], f"{case['label']} @{turn}"
            assert step["top"] == expected["top"], f"{case['label']} @{turn}"
            assert step["matched"] is expected["matched"], f"{case['label']} @{turn}"
