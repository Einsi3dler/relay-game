"""The BOMB DEFUSE manual — the one copy two seats read.

`frontend/games/bomb_manual.js` is drawn by the Defuser's own manual view and by
the Grandmaster's console on the leader dashboard (docs/GAME_DESIGN.md §2c).
The static tables in it also back the browser's rules mirror, so
`test_game11_bomb_defuse_parity.py` locks them to Python; this file covers the
*pages* — that every one renders, that navigation walks both ways, and that a
click can actually reach every control on them.

The real file runs in node against a fake DOM.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.games.game11_bomb_defuse import ACCORDING_TO_NUMBER_AXIS

MANUAL = Path(__file__).parents[2] / "frontend" / "games" / "bomb_manual.js"

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

function element(tag) {
  const el = {
    tagName: tag, children: [], style: {}, parentNode: null, attrs: {},
    listeners: {}, html: "", textContent: "",
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    removeEventListener(type, fn) {
      this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn);
    },
  };
  el.style.cssText = "";
  Object.defineProperty(el, "innerHTML", {
    get() { return el.html; },
    set(value) { el.html = value; if (value === "") this.children = []; },
  });
  return el;
}

const context = { window: {}, document: { createElement: element } };
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
const manual = context.window.RelayBombManual;

function all(node, out) {
  out = out || [];
  out.push(node);
  node.children.forEach((child) => all(child, out));
  return out;
}
function texts(root) { return all(root).map((n) => n.textContent).filter(Boolean); }
function buttons(root) {
  return all(root).filter((n) => n.tagName === "button");
}
function click(root, label) {
  const hit = buttons(root).filter((n) => n.textContent === label);
  if (!hit.length) throw new Error("no button labelled " + label);
  (hit[0].listeners.click || []).forEach((fn) => fn({}));
}

// --- the same hit test the bomb face gets: can a click land? --------------
function num(css, prop) {
  const hits = [...css.matchAll(
    new RegExp("(?:^|;)\\s*" + prop + ":\\s*(-?[\\d.]+)(?:px)?\\s*(?=;|$)", "g"))];
  return hits.length ? parseFloat(hits[hits.length - 1][1]) : null;
}
function rectOf(node) {
  const css = node.style.cssText;
  const w = num(css, "width"), h = num(css, "height");
  let x = num(css, "left"), y = num(css, "top");
  if (x === null || y === null || w === null || h === null) return null;
  for (let p = node.parentNode; p; p = p.parentNode) {
    const px = num(p.style.cssText, "left"), py = num(p.style.cssText, "top");
    if (px !== null) x += px;
    if (py !== null) y += py;
  }
  return { x, y, w, h };
}
function boxOf(node) {
  for (let n = node; n; n = n.parentNode) {
    const box = rectOf(n);
    if (box) return box;
  }
  return null;
}
function reachable(root) {
  const order = all(root);
  const out = [];
  order.forEach((target, index) => {
    if (!(target.listeners.click || []).length) return;
    const box = boxOf(target);
    if (!box) return;
    const cx = box.x + box.w / 2, cy = box.y + box.h / 2;
    const kin = new Set(all(target));
    const blocked = order.slice(index + 1).some((other) => {
      if (kin.has(other)) return false;
      const over = rectOf(other);
      return over !== null && cx >= over.x && cx <= over.x + over.w &&
             cy >= over.y && cy <= over.y + over.h;
    });
    if (!blocked) out.push(target.textContent);
  });
  return out;
}

// --- scenarios ------------------------------------------------------------

const report = { pages: {}, reachable: {}, nav: [], exits: 0 };
const host = element("div");

function draw(page, axis) {
  manual.render(host, {
    page: page,
    axis: axis || "column",
    homeNote: "HOME NOTE",
    onNavigate: function (next) { report.nav.push(next); draw(next, axis); },
    onExit: function () { report.exits += 1; },
  });
}

// Every page, and what a click could reach on it.
["home"].concat(manual.PAGES).forEach(function (page) {
  draw(page);
  report.pages[page] = texts(host).join(" | ");
  report.reachable[page] = reachable(host);
});

// The selector walks in, Exit walks back out, Exit on home leaves the manual.
draw("home");
report.nav = [];
click(host, "Maze");
report.afterSelect = report.nav.slice();
click(host, "Exit");
report.afterExit = report.nav.slice();
draw("home");
click(host, "Exit");

// The axis is configurable, and the page is written from it (§62).
draw("according_to_number", "row");
report.rowAxis = texts(host).join(" | ");

// Re-rendering replaces the page instead of stacking copies of it.
draw("home");
draw("home");
report.hostChildren = host.children.length;
report.data = manual.__data;
report.pageNames = manual.PAGES;

process.stdout.write(JSON.stringify(report));
"""


@pytest.fixture(scope="module")
def report() -> dict:
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        harness = Path(tmp) / "harness.js"
        harness.write_text(HARNESS)
        finished = subprocess.run(
            ["node", str(harness), str(MANUAL)],
            capture_output=True, text=True, timeout=60,
        )
    assert finished.returncode == 0, finished.stderr
    return json.loads(finished.stdout)


def test_the_manual_lists_only_the_modules_this_version_implements(report):
    assert report["pageNames"] == [
        "maze", "simon", "according_to_number", "mini_button"
    ]
    home = report["pages"]["home"]
    assert "The Bomb:" in home
    for name in ("Maze", "Simon Says", "According to number", "The mini button"):
        assert name in home
    # bomb.md §29/§84: no dead buttons for modules whose behaviour was never
    # verified — guessing at them would be inventing a different game.
    for deferred in ("Wires", "Timer", "Memory", "Keypads", "Read and Press"):
        assert deferred not in home


def test_every_page_carries_its_rule(report):
    pages = report["pages"]
    maze = pages["maze"]
    assert "Blue is the Defuser's position" in maze
    assert "red is the way out" in maze
    assert "it is a label, not a target" in maze

    simon = pages["simon"].upper()
    for flashed, pressed in (("RED", "BLUE"), ("GREEN", "YELLOW")):
        assert flashed in simon and pressed in simon
    assert "NO STRIKES" in simon

    numbers = pages["according_to_number"]
    assert "green 1 identifies" in numbers
    assert "left = 1, middle = 2, right = 3" in numbers
    # All eight grids, every digit of them.
    assert numbers.count("| 1 |") >= 8

    mini = pages["mini_button"]
    assert "Wait for the tiny button to turn red." in mini
    assert "until it turns green" in mini
    assert "two-digit code" in mini


def test_the_page_is_written_from_the_configured_axis(report):
    # §62 is the one rule the source material never confirmed, so it is a
    # constant — and the manual has to agree with whatever it is set to.
    assert f"the answer is its {ACCORDING_TO_NUMBER_AXIS}" in \
        report["pages"]["according_to_number"]
    assert "the answer is its row" in report["rowAxis"]


def test_the_selector_walks_in_and_exit_walks_back_out(report):
    assert report["afterSelect"] == ["maze"]          # a selector entry opens it
    assert report["afterExit"] == ["maze", "home"]    # ...and Exit returns
    assert report["exits"] == 1                       # Exit on home leaves


def test_every_control_on_every_page_can_be_clicked(report):
    # The console is a new absolutely-positioned surface, and a full-surface
    # overlay silently ate every click on the bomb face once already.
    home = report["reachable"]["home"]
    for name in ("Maze", "Simon Says", "According to number", "The mini button"):
        assert name in home
    for page in report["pageNames"] + ["home"]:
        assert "Exit" in report["reachable"][page], page


def test_rendering_twice_replaces_the_page(report):
    # The console redraws in place; stacking copies would double every control.
    assert report["hostChildren"] == 1


def test_the_manual_exposes_the_shared_tables(report):
    data = report["data"]
    assert set(data) == {
        "MAZE_LAYOUTS", "NUMBER_PATTERNS", "SIMON_MAP", "SIMON_COLOURS"
    }
    # Their agreement with Python is asserted in the parity test, which is
    # where the server's own replay is locked to them too.
    assert len(data["MAZE_LAYOUTS"]) == 8
    assert len(data["NUMBER_PATTERNS"]) == 8
