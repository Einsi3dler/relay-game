"""A node harness for duel renderers, shared by the three stateful duels.

`tests/games/test_duel1_rps_renderer.py` carries its own copy of this: RPS
predates the others and its renderer needs almost none of the DOM below. The
three duels added with the duel-mode expansion drive real controls — a card
hand, a nine-cell grid, a bid stepper — so they need the same slightly larger
fake DOM, and one copy of it is easier to trust than three.

What every renderer test built on this is really asserting is not the pixels:

  * the opponent's choice is **not in the rendered tree** while the round is
    open — the server withholds it, and the renderer must not invent a
    placeholder that stands in for it;
  * a duel is one object across many snapshots, so `update()` must not remount,
    must not double-send a choice, and `unmount()` must leave nothing behind.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

FRONTEND = Path(__file__).parents[2] / "frontend" / "duels"

# The fake DOM, plus the walk helpers a test script uses to inspect the tree.
# Everything after this is written per test file.
PRELUDE = r"""
const fs = require("fs");
const vm = require("vm");

function element(tag) {
  const el = {
    tagName: tag, className: "", children: [], style: {}, parentNode: null,
    listeners: {}, html: "", textContent: "", disabled: false, type: "",
    hidden: false, attrs: {},
    classList: {
      _set: new Set(),
      add(name) { this._set.add(name); },
      remove(name) { this._set.delete(name); },
      contains(name) { return this._set.has(name); },
    },
    appendChild(child) {
      this.children.push(child); child.parentNode = this; return child;
    },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    addEventListener(type, fn) {
      (this.listeners[type] = this.listeners[type] || []).push(fn);
    },
    click() { (this.listeners.click || []).forEach((fn) => fn()); },
  };
  Object.defineProperty(el, "innerHTML", {
    get() { return el.html; },
    set(value) { el.html = value; if (value === "") this.children = []; },
  });
  return el;
}

function descend(node) {
  const out = [];
  (function walk(n) { n.children.forEach((c) => { out.push(c); walk(c); }); })(node);
  return out;
}

// Every string rendered anywhere in the tree.
function textOf(node) {
  return descend(node)
    .map((n) => String(n.textContent || "") + " " + String(n.html || ""))
    .join(" ");
}

// Nodes carrying exactly this class, by their rendered text.
function textsOf(node, className) {
  return descend(node)
    .filter((n) => String(n.className || "").split(" ").indexOf(className) >= 0)
    .map((n) => String(n.textContent || ""));
}

function buttons(node) {
  return descend(node).filter((n) => n.tagName === "button");
}

// A button by its label — the whole subtree's text, so a card built from an
// icon span plus a label span is found by the label.
function labelled(node, text) {
  return buttons(node).find(
    (b) => (String(b.textContent || "") + textOf(b)).indexOf(text) >= 0
  );
}

const context = { window: {}, document: { createElement: element }, console, JSON };
context.globalThis = context;
vm.createContext(context);
// theme.js first, the way both pages load it: a renderer reads its colours
// from window.RelayTheme, so a harness without it is not the browser. Its path
// comes off the game file's own, since this script runs from a temp directory.
vm.runInContext(fs.readFileSync(
  process.argv[2].replace(/[\\/](games|duels)[\\/][^\\/]+$/, "/theme.js"),
  "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);

const sent = [];
const api = { choose: (move, id, round) => sent.push([move, id, round]) };
const report = {};
"""


def run(renderer_name: str, script: str) -> dict:
    """Drive `frontend/duels/<renderer_name>.js` through `script` in node.

    The script runs with `element`, `descend`, `textOf`, `textsOf`, `buttons`,
    `labelled`, `context`, `api`, `sent` and `report` in scope, and should fill
    `report`; this returns it parsed.
    """
    source = PRELUDE + script + "\nconsole.log(JSON.stringify(report));\n"
    with tempfile.TemporaryDirectory() as tmp:
        harness = Path(tmp) / "harness.js"
        harness.write_text(source)
        completed = subprocess.run(
            ["node", str(harness), str(FRONTEND / f"{renderer_name}.js")],
            capture_output=True, text=True, check=True,
        )
    return json.loads(completed.stdout)
