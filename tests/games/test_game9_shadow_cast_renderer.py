"""SHADOW CAST renderer lifecycle: mount, turn the block onto its targets,
unmount, remount — no leaked listeners or timers, and no state carried into the
next board (game/RELAY_EXPANSION_GAMES_README.md §7, §9.9).

The real `frontend/games/shadow_cast.js` runs in node against a fake DOM and is
driven through its keyboard path (x/y/z turn, the uppercase letter turns the
other way, `r` restarts), so the assertions cover the shipped renderer rather
than a copy of its logic.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "shadow_cast_cases.json"
RENDERER = Path(__file__).parents[2] / "frontend" / "games" / "shadow_cast.js"

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

function element(tag) {
  const el = {
    tagName: tag, children: [], style: { cssText: "" }, parentNode: null,
    listeners: {}, html: "",
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
    get() { return el.html; },
    set(value) { el.html = value; if (value === "") this.children = []; },
  });
  return el;
}

const keydown = [];
let pending = [];
let timersMade = 0;
const context = {
  window: {
    matchMedia: () => ({ matches: true }),   // reduced motion: no turn timer
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
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
const renderer = context.window.RelayGames.shadow_cast;

const entry = JSON.parse(fs.readFileSync(process.argv[3], "utf8")).cases
  .find((c) => c.label === process.argv[4]);
const payload = entry.payload;

// "x+" is the x key; "x-" is the same key with Shift, which a browser delivers
// as the uppercase letter.
function press(key) {
  keydown.slice().forEach((fn) => fn({ key: key, shiftKey: false, preventDefault() {} }));
}
function turn(token) {
  press(token.charAt(1) === "+" ? token.charAt(0) : token.charAt(0).toUpperCase());
}
function flush() { const queued = pending; pending = []; queued.forEach((fn) => fn()); }

const report = {};
let container = element("div");
let submitted = null;
renderer.mount(container, { payload: payload }, { submit: (a) => { submitted = a; } });
report.mountedChildren = container.children.length;
report.keydownAfterMount = keydown.length;
// The isometric view is drawn from the voxel state, not a stored picture.
const stage = container.children[0].children[1];
report.facesDrawn = (stage.html.match(/<polygon/g) || []).length;
report.voxelCount = payload.voxels.length;

// Wander, then restart: the submitted turns must not carry the wandering.
turn("z+");
flush();
turn("z-");
flush();
press("r");
flush();
report.turnsAfterRestart = container.children[0].children[0].textContent;

entry.turns.forEach(turn);
flush();
report.submitted = submitted;
report.timersUnderReducedMotion = timersMade;   // only the submit hand-off
report.facesAfterSolve = (stage.html.match(/<polygon/g) || []).length;

renderer.unmount();
report.keydownAfterUnmount = keydown.length;
report.containerEmptyAfterUnmount = container.children.length === 0;
renderer.unmount();                              // must be idempotent
report.doubleUnmountOk = true;

container = element("div");
submitted = null;
renderer.mount(container, { payload: payload }, { submit: (a) => { submitted = a; } });
report.keydownAfterRemount = keydown.length;
turn(entry.turns[0]);                            // one turn is not a solved pose
flush();
report.firstTurnOfRemount = submitted;
renderer.unmount();
report.keydownAtEnd = keydown.length;

process.stdout.write(JSON.stringify(report));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_renderer_mounts_turns_and_unmounts_cleanly(tmp_path):
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
    # Three visible faces per cube, redrawn from the live voxel set each turn.
    assert report["facesDrawn"] == 3 * report["voxelCount"]
    assert report["facesAfterSolve"] == 3 * report["voxelCount"]
    # RESTART drops the turns already played, not just the pose.
    assert report["turnsAfterRestart"] == "Turn 0 / " + str(expected["payload"]["action_cap"])
    # Playing the reference turns submits exactly those turns.
    assert json.loads(report["submitted"]) == {"v": 1, "turns": expected["turns"]}
    # Reduced motion skips the rotation animation; only the submit hand-off
    # schedules a timer.
    assert report["timersUnderReducedMotion"] == 1
    # Unmount removes the listener and the DOM, and is safe to call twice.
    assert report["keydownAfterUnmount"] == 0
    assert report["containerEmptyAfterUnmount"] is True
    assert report["doubleUnmountOk"] is True
    # Remounting leaves exactly one listener and a fresh block.
    assert report["keydownAfterRemount"] == 1
    assert report["firstTurnOfRemount"] is None
    assert report["keydownAtEnd"] == 0
