"""RPS DUEL renderer lifecycle, driven through the shipped `frontend/duels/rps_duel.js`.

The renderer runs in node against a fake DOM. What matters here is not the
pixels but the two things that would be security or correctness bugs:

  * the DOM never contains the opponent's move while the round is open — the
    server withholds it, and the renderer must not invent a placeholder for it;
  * a duel is one object across many snapshots, so update() must not remount,
    must not double-fire a choice, and unmount() must leave nothing behind.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

RENDERER = Path(__file__).parents[2] / "frontend" / "duels" / "rps_duel.js"

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

function element(tag) {
  const el = {
    tagName: tag, className: "", children: [], style: {}, parentNode: null,
    listeners: {}, html: "", textContent: "", disabled: false, type: "",
    attrs: {}, src: "", alt: "",
    classList: {
      _set: new Set(),
      add(name) { this._set.add(name); },
      remove(name) { this._set.delete(name); },
      contains(name) { return this._set.has(name); },
      toggle(name, force) {
        const on = force === undefined ? !this._set.has(name) : !!force;
        if (on) this._set.add(name); else this._set.delete(name);
        return on;
      },
    },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; },
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    click() { (this.listeners.click || []).forEach((fn) => fn()); },
    querySelector(sel) { return descend(this).find((n) => "." + n.className === sel) || null; },
    querySelectorAll(sel) {
      const tag = sel.toLowerCase();
      const out = descend(this).filter((n) => n.tagName === tag);
      out.forEach = Array.prototype.forEach.bind(out);
      return out;
    },
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

// Just the two played hands. The move *cards* name every move by design (you
// pick from them), so only the hands can carry a leak. A hand is now artwork
// rather than an emoji, so this reads everything a hand could smuggle a move
// out in: its text, its markup, and the src/alt of any image under it.
function handsOf(node) {
  return descend(node)
    .filter((n) => n.className === "dl-hand")
    .map((hand) => [hand].concat(descend(hand))
      .map((n) => [n.textContent, n.html, n.src, n.alt]
        .concat(Object.values(n.attrs || {}))
        .filter(Boolean).join(" "))
      .join(" "))
    .join(" | ");
}

const context = { window: {}, document: { createElement: element } };
context.globalThis = context;
vm.createContext(context);
// theme.js first, the way both pages load it: a renderer reads its colours
// from window.RelayTheme, so a harness without it is not the browser. Its path
// comes off the game file's own, since this script runs from a temp directory.
vm.runInContext(fs.readFileSync(
  process.argv[2].replace(/[\\/](games|duels)[\\/][^\\/]+$/, "/theme.js"),
  "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);

const renderer = context.window.RelayDuels.rps_duel;
const sent = [];
const api = { choose: (move, id, round) => sent.push([move, id, round]) };

const PAYLOAD = { moves: ["rock", "paper", "scissors"], wins_needed: 2, choice_seconds: 5 };
function duel(over) {
  return Object.assign({
    id: "d1", duel_game_id: "rps_duel", name: "Rock Paper Scissors",
    phase: "choosing", round: 1, you: "a",
    wins: { a: 0, b: 0 }, locked: { a: false, b: false }, choices: {},
    duellists: { a: "A0", b: "B0" }, history: [], payload: PAYLOAD,
    last_round: null, winner_side: null, deadline: null,
  }, over || {});
}

const report = {};
const root = element("div");
renderer.mount(root, duel(), api);

// A move is one click, and it reports the duel/round it was made for.
const buttons = () => descend(root).filter((n) => n.tagName === "button");
report.buttonCount = buttons().length;
buttons()[0].click();
buttons()[0].click();          // a second press must not double-send
report.sent = sent.slice();
report.lockedAfterClick = buttons().every((b) => b.disabled);

// Opponent has locked in but the server sent no choice: nothing may render it.
renderer.update(duel({ locked: { a: true, b: true }, choices: { a: "rock" } }));
report.openRoundHands = handsOf(root);
report.openRoundText = textOf(root);

// Reveal: both hands are public now.
renderer.update(duel({
  phase: "reveal", locked: { a: true, b: true },
  choices: { a: "rock", b: "scissors" }, wins: { a: 1, b: 0 },
  last_round: { round: 1, a: "rock", b: "scissors", winner: "a" },
}));
report.revealHands = handsOf(root);
report.revealText = textOf(root);

// A Grandmaster (you: null) gets no buttons at all.
const leaderRoot = element("div");
renderer.unmount();
renderer.mount(leaderRoot, duel({ you: null }), api);
report.leaderButtons = descend(leaderRoot).filter((n) => n.tagName === "button").length;

renderer.unmount();
report.afterUnmount = leaderRoot.children.length;
renderer.unmount();            // idempotent
report.doubleUnmountOk = true;

console.log(JSON.stringify(report));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_renderer_lifecycle_and_reveal_rule(tmp_path):
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS)
    completed = subprocess.run(
        ["node", str(harness), str(RENDERER)],
        capture_output=True, text=True, check=True,
    )
    report = json.loads(completed.stdout)

    assert report["buttonCount"] == 3
    assert report["sent"] == [["rock", "d1", 1]], "a second press must not resend"
    assert report["lockedAfterClick"] is True

    # The security-critical assertion: while the round is open the opponent has
    # locked in, but their move is nowhere in the rendered tree.
    mine, theirs = report["openRoundHands"].split("|")
    assert "rock" in mine.lower(), "your own hand should show"
    assert "locked in" in theirs, "theirs shows as locked, not as a move"
    for move in ("rock", "paper", "scissors"):
        assert move not in theirs.lower(), f"{move} leaked before the reveal"

    # Reveal: both hands are public, and each is the move that was played.
    shown_mine, shown_theirs = report["revealHands"].split("|")
    assert "rock" in shown_mine.lower()
    assert "scissors" in shown_theirs.lower()
    assert "took that round" in report["revealText"]

    assert report["leaderButtons"] == 0, "a Grandmaster cannot play the duel"
    assert report["afterUnmount"] == 0
    assert report["doubleUnmountOk"] is True
