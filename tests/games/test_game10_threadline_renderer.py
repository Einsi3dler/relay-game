"""THREADLINE renderer lifecycle: mount, draw the cable by tapping cells,
unmount, remount — no leaked listeners or timers, and no state carried into
the next board (game/RELAY_EXPANSION_GAMES_README.md §14, §9.9).

The real `frontend/games/threadline.js` runs in node against a fake DOM. It is
driven by **tapping grid cells**, which is the input the spec singles out
("the tap alternative is important on smaller devices", and the holding puzzle
must be completable without drag precision), with the arrow keys and UNDO
checked alongside.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.games.game10_threadline import ThreadlineGame

RENDERER = Path(__file__).parents[2] / "frontend" / "games" / "threadline.js"

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

function element(tag) {
  const el = {
    tagName: tag, children: [], style: { cssText: "" }, parentNode: null,
    listeners: {}, html: "", disabled: false, textContent: "",
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

const docListeners = {};
let pending = [];
let timersMade = 0;
const context = {
  window: {
    setTimeout: (fn) => { timersMade++; pending.push(fn); return pending.length; },
    clearTimeout: () => {},
  },
  document: {
    createElement: element,
    addEventListener(type, fn) { (docListeners[type] = docListeners[type] || []).push(fn); },
    removeEventListener(type, fn) {
      docListeners[type] = (docListeners[type] || []).filter((f) => f !== fn);
    },
  },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
const renderer = context.window.RelayGames.threadline;

const spec = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const payload = spec.payload;
const route = spec.route;

function listenerCount() {
  return Object.keys(docListeners).reduce((n, k) => n + docListeners[k].length, 0);
}
function press(key) {
  (docListeners.keydown || []).slice().forEach((fn) => fn({ key: key, preventDefault() {} }));
}
function flush() { const queued = pending; pending = []; queued.forEach((fn) => fn()); }

const report = {};
let container = element("div");
let submitted = null;
function mount() {
  submitted = null;
  renderer.mount(container, { payload: payload }, { submit: (a) => { submitted = a; } });
}
// Grid cells are the second child of the board, laid out row-major.
function grid() { return container.children[0].children[1].children[0]; }
function tap(cell) {
  const button = grid().children[cell[0] * payload.cols + cell[1]];
  button.listeners.click.forEach((fn) => fn());
}
function controls() { return container.children[0].children[2]; }

mount();
report.mountedChildren = container.children.length;
report.cellsDrawn = grid().children.length;
report.boardCells = payload.rows * payload.cols;
report.listenersAfterMount = listenerCount();
report.startReadout = container.children[0].children[0].html;

// Tapping is enough on its own: walk the whole reference route by cell. The
// finished cable submits itself one timer beat later.
route.slice(1).forEach(tap);
report.timersUsed = timersMade;
flush();
report.submittedByTapping = submitted;

// Illegal taps are refused where they happen, and never submitted.
renderer.unmount();
container = element("div");
mount();
tap(route[route.length - 1]);            // not next to the head
report.strayTapSubmitted = submitted;
report.strayTapMessage = container.children[0].children[3].textContent;

// Rewind by tapping a covered cell, then finish with the arrow keys from
// there (spec.keys[i] is the step out of route[i]).
route.slice(1, 4).forEach(tap);
tap(route[1]);
report.readoutAfterRewind = container.children[0].children[0].html;
spec.keys.slice(1).forEach(press);
flush();
report.submittedByKeyboard = submitted;

// UNDO drops the last step of a fresh board.
renderer.unmount();
container = element("div");
mount();
route.slice(1, 3).forEach(tap);
const undoBtn = controls().children[0];
undoBtn.listeners.click.forEach((fn) => fn());
report.readoutAfterUndo = container.children[0].children[0].html;

renderer.unmount();
report.listenersAfterUnmount = listenerCount();
report.containerEmptyAfterUnmount = container.children.length === 0;
renderer.unmount();                       // must be idempotent
report.doubleUnmountOk = true;

container = element("div");
mount();
report.listenersAfterRemount = listenerCount();
tap(route[1]);                            // one step is not a finished cable
report.firstTapOfRemount = submitted;
renderer.unmount();
report.listenersAtEnd = listenerCount();

process.stdout.write(JSON.stringify(report));
"""


def spec_for(puzzle) -> dict:
    """The board, its reference route, and the arrow keys that walk it."""
    route = json.loads(puzzle.answer)["path"]
    arrows = {(-1, 0): "ArrowUp", (1, 0): "ArrowDown", (0, -1): "ArrowLeft", (0, 1): "ArrowRight"}
    keys = [
        arrows[(after[0] - before[0], after[1] - before[1])]
        for before, after in zip(route, route[1:])
    ]
    return {"payload": puzzle.payload, "route": route, "keys": keys}


def run(tmp_path, puzzle) -> dict:
    harness = tmp_path / "renderer.js"
    harness.write_text(HARNESS)
    board = tmp_path / "board.json"
    board.write_text(json.dumps(spec_for(puzzle)))
    result = subprocess.run(
        ["node", str(harness), str(RENDERER), str(board)],
        capture_output=True, text=True, timeout=60, check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_renderer_draws_a_cable_and_unmounts_cleanly(tmp_path):
    game = ThreadlineGame()
    puzzle = game.generate_main(3, 1)
    report = run(tmp_path, puzzle)
    reference = json.loads(puzzle.answer)["path"]

    assert report["mountedChildren"] > 0
    assert report["cellsDrawn"] == report["boardCells"]
    # keydown, pointerup and pointercancel, and nothing else.
    assert report["listenersAfterMount"] == 3
    assert "Bends 0 / " + str(puzzle.payload["bend_cap"]) in report["startReadout"]
    assert "Anchor 1 of " + str(len(puzzle.payload["anchors"])) in report["startReadout"]

    # Tapping cell by cell completes the board and submits exactly that route.
    assert json.loads(report["submittedByTapping"]) == {"v": 1, "path": reference}
    assert report["timersUsed"] == 1            # only the submit hand-off

    # A tap that isn't next to the head is refused, not queued or submitted.
    assert report["strayTapSubmitted"] is None
    assert report["strayTapMessage"] == "Tap a cell next to the cable head."

    # Tapping a covered cell rewinds to it, and the arrow keys finish the job.
    assert "Length 1 / " in report["readoutAfterRewind"]
    assert json.loads(report["submittedByKeyboard"])["path"][-1] == puzzle.payload["end"]

    assert "Length 1 / " in report["readoutAfterUndo"]

    # Unmount removes every listener and the DOM, and is safe to call twice.
    assert report["listenersAfterUnmount"] == 0
    assert report["containerEmptyAfterUnmount"] is True
    assert report["doubleUnmountOk"] is True
    # Remounting leaves exactly one set of listeners and a fresh, empty cable.
    assert report["listenersAfterRemount"] == 3
    assert report["firstTapOfRemount"] is None
    assert report["listenersAtEnd"] == 0


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_every_holding_board_can_be_finished_by_tapping_alone(tmp_path):
    """The spec's tap-input acceptance test, on the practice boards."""
    game = ThreadlineGame()
    for seed in range(6):
        puzzle = game.generate_holding(seed)
        report = run(tmp_path, puzzle)
        assert json.loads(report["submittedByTapping"]) == {
            "v": 1, "path": json.loads(puzzle.answer)["path"],
        }
