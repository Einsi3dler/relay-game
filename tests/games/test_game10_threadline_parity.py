"""THREADLINE: the browser must judge a route exactly as the server does
(game/RELAY_EXPANSION_GAMES_README.md §14 "minimum acceptance tests").

The renderer refuses an illegal step where the player makes it, which is only
honest if its rules are the server's rules. `fixtures/threadline_cases.json` is
the shared contract: hand-built boards that trip one rule each, real generated
boards, and for every case the verdict plus the bend/edge/anchor counters the
readout shows. The first test locks the Python side to it; the second runs the
real `frontend/games/threadline.js` through node and locks the JavaScript one
to the same file.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.games.game10_threadline import validate

FIXTURE = Path(__file__).parent / "fixtures" / "threadline_cases.json"
RENDERER = Path(__file__).parents[2] / "frontend" / "games" / "threadline.js"

# A bare `window` is all the renderer's module body touches; `document` only
# appears inside mount(), which the parity harness never calls.
HARNESS = """
const fs = require("fs");
const vm = require("vm");
const context = { window: {} };
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
const game = context.window.RelayGames.threadline;
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8")).cases;
process.stdout.write(JSON.stringify(cases.map(function (entry) {
  return game.__validate(entry.payload, entry.path, entry.partial);
})));
"""


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def test_fixture_covers_every_rule():
    cases = load_fixture()["cases"]
    assert len(cases) >= 40
    # Every failure the walk can report has at least one case.
    reasons = {case["expected"]["reason"] for case in cases}
    assert reasons == {
        "", "bad_shape", "bad_start", "not_adjacent", "out_of_bounds", "blocked",
        "revisit", "too_long", "too_many_bends", "anchor_out_of_order",
        "anchor_port", "not_at_end", "missing_anchor",
    }
    # Both halves of the partial contract, and real boards alongside the
    # hand-built ones.
    assert {case["partial"] for case in cases} == {True, False}
    assert any(case["ok"] for case in (c["expected"] for c in cases))
    assert {case["payload"]["variant"] for case in cases} == {"main", "holding"}
    assert any(case["label"].startswith("main-l10") for case in cases)


def test_server_walk_matches_the_fixture():
    for case in load_fixture()["cases"]:
        walk = validate(case["payload"], case["path"], case["partial"])
        assert walk == case["expected"], case["label"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_browser_walk_matches_the_fixture(tmp_path):
    harness = tmp_path / "parity.js"
    harness.write_text(HARNESS)
    result = subprocess.run(
        ["node", str(harness), str(RENDERER), str(FIXTURE)],
        capture_output=True, text=True, timeout=60, check=True,
    )
    produced = json.loads(result.stdout)
    cases = load_fixture()["cases"]
    assert len(produced) == len(cases)
    for case, walk in zip(cases, produced):
        assert walk == case["expected"], case["label"]
