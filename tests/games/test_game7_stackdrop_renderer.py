"""STACKDROP renderer lifecycle: mount, play, unmount, remount — no leaked
listeners and no state carried into the next board
(game/RELAY_EXPANSION_GAMES_README.md §3, §9.9).

The real `frontend/games/stackdrop.js` runs in node against a fake DOM, driven
through its keyboard path (digits arm a pin, Enter pulls it), so the assertions
cover the shipped renderer rather than a copy of its logic.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "stackdrop_cases.json"
RENDERER = Path(__file__).parents[2] / "frontend" / "games" / "stackdrop.js"

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
const pending = [];
const context = {
  window: {
    matchMedia: () => ({ matches: true }),   // reduced motion: resolve at once
    setTimeout: (fn) => { pending.push(fn); return pending.length; },
    clearTimeout: () => {},
    setInterval: () => { throw new Error("no timers in reduced-motion mode"); },
    clearInterval: () => {},
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
const renderer = context.window.RelayGames.stackdrop;

const entry = JSON.parse(fs.readFileSync(process.argv[3], "utf8")).cases
  .find((c) => c.label === process.argv[4]);
const payload = entry.payload;
const pinIds = payload.pins.map((p) => p.id);

function press(key) {
  keydown.slice().forEach((fn) => fn({ key: key, preventDefault() {} }));
}

const report = {};
let container = element("div");
let submitted = null;
renderer.mount(container, { payload: payload }, { submit: (a) => { submitted = a; } });
report.mountedChildren = container.children.length;
report.keydownAfterMount = keydown.length;

// Play the winning order: digit arms the pin, Enter pulls it.
entry.removals.forEach((id) => {
  press(String(pinIds.indexOf(id) + 1));
  press("Enter");
});
pending.splice(0).forEach((fn) => fn());        // the queued auto-submit
report.submitted = submitted;

renderer.unmount();
report.keydownAfterUnmount = keydown.length;
report.containerEmptyAfterUnmount = container.children.length === 0;
renderer.unmount();                              // must be idempotent
report.doubleUnmountOk = true;

container = element("div");
submitted = null;
renderer.mount(container, { payload: payload }, { submit: (a) => { submitted = a; } });
report.keydownAfterRemount = keydown.length;
// A fresh board must not inherit the previous attempt's pulls.
press("1");
press("Enter");
pending.splice(0).forEach((fn) => fn());
report.firstPullOfRemount = submitted;
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
    # Solving the board submits the pull order it actually played.
    assert json.loads(report["submitted"]) == {"v": 1, "remove": expected["removals"]}
    # Unmount removes the listener and the DOM, and is safe to call twice.
    assert report["keydownAfterUnmount"] == 0
    assert report["containerEmptyAfterUnmount"] is True
    assert report["doubleUnmountOk"] is True
    # Remounting leaves exactly one listener, and the board starts fresh: one
    # pull is not a solved board, so nothing is submitted yet.
    assert report["keydownAfterRemount"] == 1
    assert report["firstPullOfRemount"] is None
    assert report["keydownAtEnd"] == 0
