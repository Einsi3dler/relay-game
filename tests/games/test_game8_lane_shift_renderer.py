"""LANE SHIFT renderer lifecycle: mount, play a whole schedule, unmount,
remount — no leaked listeners or timers, and no state carried into the next
board (game/RELAY_EXPANSION_GAMES_README.md §2, §9.9).

The real `frontend/games/lane_shift.js` runs in node against a fake DOM and is
driven through its keyboard path (digits toggle junctions, `p` passes), so the
assertions cover the shipped renderer rather than a copy of its logic.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "lane_shift_cases.json"
RENDERER = Path(__file__).parents[2] / "frontend" / "games" / "lane_shift.js"

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

function element(tag) {
  const el = {
    tagName: tag, children: [], style: { cssText: "" }, parentNode: null,
    listeners: {},
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; },
    removeChild(child) {
      this.children = this.children.filter((c) => c !== child);
      child.parentNode = null;
    },
    setAttribute() {},
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    removeEventListener(type, fn) {
      this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn);
    },
  };
  Object.defineProperty(el, "innerHTML", {
    get() { return ""; },
    set(value) { if (value === "") this.children = []; },
  });
  return el;
}

const keydown = [];
let pending = [];
let timersMade = 0;
const context = {
  window: {
    matchMedia: () => ({ matches: true }),   // reduced motion: no tick timer
    setTimeout: (fn) => { timersMade++; pending.push(fn); return pending.length; },
    clearTimeout: () => {},
  },
  document: {
    createElement: element,
    addEventListener(type, fn) { if (type === "keydown") keydown.push(fn); },
    removeEventListener(type, fn) {
      if (type === "keydown") {
        const at = keydown.indexOf(fn);
        if (at !== -1) keydown.splice(at, 1);
      }
    },
  },
};
vm.createContext(context);
// theme.js first, the way both pages load it: a renderer reads its colours
// from window.RelayTheme, so a harness without it is not the browser. Its path
// comes off the game file's own, since this script runs from a temp directory.
vm.runInContext(fs.readFileSync(
  process.argv[2].replace(/[\\/](games|duels)[\\/][^\\/]+$/, "/theme.js"),
  "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
const renderer = context.window.RelayGames.lane_shift;

const entry = JSON.parse(fs.readFileSync(process.argv[3], "utf8")).cases
  .find((c) => c.label === process.argv[4]);
const payload = entry.payload;
const switchIds = payload.switches.map((s) => s.id);

function press(key) {
  keydown.slice().forEach((fn) => fn({ key: key, preventDefault() {} }));
}
function flush() { const queued = pending; pending = []; queued.forEach((fn) => fn()); }

const report = {};
let container = element("div");
let submitted = null;
renderer.mount(container, { payload: payload }, { submit: (a) => { submitted = a; } });
report.mountedChildren = container.children.length;
report.keydownAfterMount = keydown.length;

// Play the reference schedule: a digit toggles that junction, `p` passes.
// Holds are driven through the chip the renderer enables for them.
entry.actions.forEach((action) => {
  if (action[0] === "pass") press("p");
  else if (action[0] === "toggle") press(String(switchIds.indexOf(action[1]) + 1));
  else press("h");
  flush();
});
flush();
report.submitted = submitted;
report.timersUnderReducedMotion = timersMade;   // only the submit hand-off

renderer.unmount();
report.keydownAfterUnmount = keydown.length;
report.containerEmptyAfterUnmount = container.children.length === 0;
renderer.unmount();                              // must be idempotent
report.doubleUnmountOk = true;

container = element("div");
submitted = null;
renderer.mount(container, { payload: payload }, { submit: (a) => { submitted = a; } });
report.keydownAfterRemount = keydown.length;
press("p");                                      // one turn is not a solved belt
flush();
report.firstTurnOfRemount = submitted;
renderer.unmount();
report.keydownAtEnd = keydown.length;

process.stdout.write(JSON.stringify(report));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_renderer_mounts_plays_and_unmounts_cleanly(tmp_path):
    case = "main-l1-s0-solution"
    harness = tmp_path / "renderer.js"
    harness.write_text(HARNESS)
    result = subprocess.run(
        ["node", str(harness), str(RENDERER), str(FIXTURE), case],
        capture_output=True, text=True, timeout=60, check=True,
    )
    report = json.loads(result.stdout)
    expected = next(
        entry for entry in json.loads(FIXTURE.read_text())["cases"]
        if entry["label"] == case
    )

    assert report["mountedChildren"] > 0
    assert report["keydownAfterMount"] == 1
    # Playing the reference schedule submits exactly the actions it played.
    assert json.loads(report["submitted"]) == {"v": 1, "actions": expected["actions"]}
    # Reduced motion skips the movement animation; only the submit hand-off
    # schedules a timer.
    assert report["timersUnderReducedMotion"] == 1
    # Unmount removes the listener and the DOM, and is safe to call twice.
    assert report["keydownAfterUnmount"] == 0
    assert report["containerEmptyAfterUnmount"] is True
    assert report["doubleUnmountOk"] is True
    # Remounting leaves exactly one listener and a fresh belt.
    assert report["keydownAfterRemount"] == 1
    assert report["firstTurnOfRemount"] is None
    assert report["keydownAtEnd"] == 0
