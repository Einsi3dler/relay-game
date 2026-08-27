"""BOMB DEFUSE renderer: a whole round driven through the real
`frontend/games/bomb_defuse.js` in node, against a fake DOM and a virtual clock.

This game keeps clocks of its own — the fuse and the mini button's reaction
window — so the harness owns `Date.now` and both timer queues, and every test
below moves time by hand. What it checks is the part a fixture cannot: that
working the bays through the actual controls produces a transcript the server
accepts, that each fatal path reaches MISSION FAILED and then asks the engine
for a fresh bomb, and that mount/unmount/remount leaves nothing running
(game/RELAY_EXPANSION_GAMES_README.md §3, §9.9).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.games.game11_bomb_defuse import BombDefuseGame

FRONTEND = Path(__file__).parents[2] / "frontend" / "games"
RENDERER = FRONTEND / "bomb_defuse.js"
MANUAL = FRONTEND / "bomb_manual.js"

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

// --- fake DOM ------------------------------------------------------------

function element(tag) {
  const el = {
    tagName: tag, children: [], style: {}, parentNode: null, attrs: {},
    listeners: {}, html: "", disabled: false, textContent: "",
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; },
    removeChild(child) {
      this.children = this.children.filter((c) => c !== child);
      child.parentNode = null;
    },
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

// --- virtual clock -------------------------------------------------------

const clock = { now: 1000000, timers: new Map(), nextId: 1, made: 0 };

function schedule(fn, delay, every) {
  const id = clock.nextId++;
  clock.made++;
  clock.timers.set(id, { fn, due: clock.now + (delay || 0), every: every || null });
  return id;
}
function cancel(id) { clock.timers.delete(id); }

// Move time forward, firing whatever comes due, in order.
function advance(ms) {
  const target = clock.now + ms;
  for (let guard = 0; guard < 10000; guard++) {
    let next = null;
    clock.timers.forEach((timer, id) => {
      if (timer.due <= target && (next === null || timer.due < next.timer.due)) {
        next = { id, timer };
      }
    });
    if (next === null) break;
    clock.now = next.timer.due;
    if (next.timer.every === null) clock.timers.delete(next.id);
    else next.timer.due = clock.now + next.timer.every;
    next.timer.fn();
  }
  clock.now = target;
}

const windowListeners = {};
const context = {
  window: {
    setTimeout: (fn, ms) => schedule(fn, ms, null),
    clearTimeout: cancel,
    setInterval: (fn, ms) => schedule(fn, ms, ms),
    clearInterval: cancel,
    addEventListener(type, fn) { (windowListeners[type] = windowListeners[type] || []).push(fn); },
    removeEventListener(type, fn) {
      windowListeners[type] = (windowListeners[type] || []).filter((f) => f !== fn);
    },
  },
  document: { createElement: element },
  Date: { now: () => clock.now },
  console,
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[4], "utf8"), context);   // manual first
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
const game = context.window.RelayGames.bomb_defuse;

// --- driving the board ---------------------------------------------------

function walk(node, out) {
  out.push(node);
  node.children.forEach((child) => walk(child, out));
  return out;
}
function all(root) { return walk(root, []); }
function find(root, test) {
  const hit = all(root).filter(test);
  if (!hit.length) throw new Error("no node matched");
  return hit[0];
}
function byLabel(root, label) {
  return find(root, (n) => n.attrs["aria-label"] === label);
}
function byText(root, text) {
  return find(root, (n) => n.tagName === "button" && n.textContent === text);
}
function fire(node, type, event) {
  (node.listeners[type] || []).forEach((fn) => fn(event || {}));
}
function click(node) { fire(node, "click", {}); }
function texts(root) { return all(root).map((n) => n.textContent).filter(Boolean); }
function screen(root) { return texts(root).join(" | "); }

function mount(puzzle) {
  const container = element("div");
  const sent = [];
  game.mount(container, puzzle, { submit: (answer) => sent.push(answer), setReady() {} });
  return { container, sent };
}

// Work one bay through its real controls, from the reference transcript.
function workBay(root, module, moves) {
  click(byLabel(root, "Open the " + {
    maze: "Maze", simon: "Simon Says",
    according_to_number: "According to number", mini_button: "The mini button",
  }[module.type] + " bay"));

  if (module.type === "mini_button") {
    click(byText(root, "ARM THE BUTTON"));
    advance(module.delay_ms);                       // it turns red
    const pad = byLabel(root, "The mini button — red");
    fire(pad, "pointerdown", { pointerId: 1 });
    advance(module.required_hold_ms);               // it turns green
    fire(byLabel(root, "The mini button — ready"), "pointerup", {});
    return;
  }
  const labels = { n: "Step north", s: "Step south", e: "Step east", w: "Step west" };
  moves.filter((move) => move.m === module.id).forEach((move) => {
    if (module.type === "maze") click(byLabel(root, labels[move.a]));
    else if (module.type === "simon") click(byLabel(root, move.a));
    else click(byText(root, String(move.a)));
  });
}

function defuse(puzzle) {
  const { container, sent } = mount(puzzle);
  const moves = JSON.parse(puzzle.__reference).moves;
  puzzle.payload.modules.forEach((module) => workBay(container, module, moves));
  click(byText(container, "OK"));
  return { container, sent };
}

// --- scenarios -----------------------------------------------------------

const puzzles = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const report = {};

// 1. The face is the bomb the spec describes.
{
  const { container } = mount(puzzles.full);
  const seen = screen(container);
  report.face = {
    timer: byLabel(container, "OK — bays are still open") ? true : false,
    shows_fuse: seen.indexOf(String(puzzles.full.payload.fuse_seconds)) !== -1,
    has_give_up: seen.indexOf("Give up") !== -1,
    has_manual: seen.indexOf("📖  MANUAL") !== -1,
    bay_buttons: all(container).filter(
      (n) => (n.attrs["aria-label"] || "").indexOf("Open the ") === 0).length,
    ok_label: byText(container, "OK").attrs["aria-label"],
  };
  game.unmount();
}

// 2. A whole four-bay defusal, driven through the controls.
{
  const { container, sent } = defuse(puzzles.full);
  report.defused = { sent: sent, screen: screen(container) };
  game.unmount();
}

// 3. The fuse: it ticks down, then it goes off.
{
  const { container, sent } = mount(puzzles.full);
  advance(3000);
  const after3s = byLabel(container, "OK — bays are still open") ?
    texts(container)[0] : null;
  advance(puzzles.full.payload.fuse_seconds * 1000);
  const boom = screen(container);
  advance(5000);
  report.fuse = { after3s: after3s, screen: boom, sent: sent };
  game.unmount();
}

// 4. Every fatal path lands on the same screen and asks for a fresh bomb.
const fatal = {};
{
  // Give up (§21) — no confirmation.
  const { container, sent } = mount(puzzles.full);
  click(byText(container, "Give up"));
  advance(5000);
  fatal.give_up = { screen: screen(container), sent: sent };
  game.unmount();
}
{
  // OK with every bay still open (§15).
  const { container, sent } = mount(puzzles.full);
  click(byText(container, "OK"));
  advance(5000);
  fatal.premature_ok = { screen: screen(container), sent: sent };
  game.unmount();
}
{
  // A maze step into a wall (§42).
  const { container, sent } = mount(puzzles.maze);
  click(byLabel(container, "Open the Maze bay"));
  const labels = { n: "Step north", s: "Step south", e: "Step east", w: "Step west" };
  click(byLabel(container, labels[puzzles.maze.__blocked]));
  advance(5000);
  fatal.maze_wall = { screen: screen(container), sent: sent };
  game.unmount();
}
{
  // Echoing the flash back instead of translating it (§49).
  const { container, sent } = mount(puzzles.simon);
  click(byLabel(container, "Open the Simon Says bay"));
  click(byLabel(container, puzzles.simon.payload.modules[0].sequence[0]));
  advance(5000);
  fatal.simon = { screen: screen(container), sent: sent };
  game.unmount();
}
{
  // Touching the mini button before it turns red (§56).
  const { container, sent } = mount(puzzles.mini);
  click(byLabel(container, "Open the The mini button bay"));
  click(byText(container, "ARM THE BUTTON"));
  fire(byLabel(container, "The mini button — waiting"), "pointerdown", { pointerId: 1 });
  advance(5000);
  fatal.mini_early = { screen: screen(container), sent: sent };
  game.unmount();
}
{
  // Ignoring the red for too long (§54).
  const { container, sent } = mount(puzzles.mini);
  const module = puzzles.mini.payload.modules[0];
  click(byLabel(container, "Open the The mini button bay"));
  click(byText(container, "ARM THE BUTTON"));
  advance(module.delay_ms + module.reaction_window_ms + 1);
  advance(5000);
  fatal.mini_slow = { screen: screen(container), sent: sent };
  game.unmount();
}
{
  // Letting go before green (§56).
  const { container, sent } = mount(puzzles.mini);
  const module = puzzles.mini.payload.modules[0];
  click(byLabel(container, "Open the The mini button bay"));
  click(byText(container, "ARM THE BUTTON"));
  advance(module.delay_ms);
  fire(byLabel(container, "The mini button — red"), "pointerdown", { pointerId: 1 });
  advance(Math.floor(module.required_hold_ms / 2));
  fire(byLabel(container, "The mini button — holding"), "pointerup", {});
  advance(5000);
  fatal.mini_release = { screen: screen(container), sent: sent };
  game.unmount();
}
report.fatal = fatal;

// 5. Colour is never the only carrier, and the hold works from a keyboard.
{
  const { container } = mount(puzzles.simon);
  click(byLabel(container, "Open the Simon Says bay"));
  const pad = byLabel(container, "red");
  report.access = {
    pad_text: texts(pad).join(" "),
    manual_rows: null,
  };
  click(byText(container, "✕ BOMB"));
  click(byText(container, "📖  MANUAL"));
  click(byText(container, "Simon Says"));
  report.access.manual_rows = screen(container);
  game.unmount();
}
{
  // The mini button is a hold, so space has to hold it.
  const { container, sent } = mount(puzzles.mini);
  const module = puzzles.mini.payload.modules[0];
  click(byLabel(container, "Open the The mini button bay"));
  click(byText(container, "ARM THE BUTTON"));
  advance(module.delay_ms);
  const red = byLabel(container, "The mini button — red");
  report.keyboard = { focusable: red.attrs.tabindex };
  fire(red, "keydown", { key: " ", repeat: false });
  fire(byLabel(container, "The mini button — holding"), "keydown", { key: " ", repeat: true });
  advance(module.required_hold_ms);
  fire(byLabel(container, "The mini button — ready"), "keyup", { key: " " });
  click(byText(container, "OK"));
  report.keyboard.sent = sent;
  game.unmount();
}

// 6. An armed mini button locks the way out: no manual, no closing the bay.
{
  const { container } = mount(puzzles.mini);
  const module = puzzles.mini.payload.modules[0];
  click(byLabel(container, "Open the The mini button bay"));
  click(byText(container, "ARM THE BUTTON"));
  advance(Math.floor(module.delay_ms / 2));
  const locked = all(container).filter(
    (n) => n.textContent === "📖  MANUAL (LOCKED)");
  report.armed = {
    manual_locked: locked.length === 1 && locked[0].disabled === true,
    no_close_button: all(container).filter((n) => n.textContent === "✕ BOMB").length,
    header: screen(container).indexOf("ARMED — SEE IT THROUGH") !== -1,
  };
  game.unmount();
}

// 7. The manual: every page, and Exit walking back out.
{
  const { container } = mount(puzzles.full);
  click(byText(container, "📖  MANUAL"));
  const home = screen(container);
  const pages = {};
  ["Maze", "Simon Says", "According to number", "The mini button"].forEach((name) => {
    click(byText(container, name));
    pages[name] = screen(container);
    click(byText(container, "Exit"));           // back to the manual's home page
  });
  click(byText(container, "Exit"));             // ...and back to the bomb
  report.manual = { home: home, pages: pages, back_on_bomb: screen(container) };
  game.unmount();
}

// 8. Lifecycle: nothing left running, and a remount starts clean.
{
  const first = mount(puzzles.full);
  click(byLabel(first.container, "Open the Simon Says bay"));   // schedules flashes
  advance(500);
  game.unmount();
  const leftRunning = clock.timers.size;
  game.unmount();                                // idempotent
  const madeBefore = clock.made;

  const second = mount(puzzles.full);
  const fresh = screen(second.container);
  advance(1000);
  game.unmount();
  report.lifecycle = {
    left_running: leftRunning,
    window_listeners: (windowListeners.resize || []).length,
    detached: first.container.children.length,
    remount_is_fresh: fresh.indexOf(String(puzzles.full.payload.fuse_seconds)) !== -1,
    remount_scheduled: clock.made > madeBefore,
  };
}

// 9. Hit testing. Firing a handler on a node found by label proves the handler
// works; it does not prove a click could ever reach it. This walks the real
// geometry and paint order and asks the question a browser asks — at the centre
// of each control, what is on top? — then reports which controls a player could
// actually press in each view.
function num(css, prop) {
  // A unitless zero is still a zero: `left:0` has to parse, or a full-surface
  // layer reads as having no box at all and stops counting as a blocker.
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
// A control laid out by flex has no box of its own; stand in its container's,
// which is what decides whether something is over it anyway.
function boxOf(node) {
  for (let n = node; n; n = n.parentNode) {
    const box = rectOf(n);
    if (box) return box;
  }
  return null;
}
function chain(node) {
  const out = [];
  for (let n = node; n; n = n.parentNode) out.unshift(n);
  return out;
}
function painted(node) {
  return chain(node).every((n) => n.style.display
    ? n.style.display !== "none"                       // an assignment wins...
    : n.style.cssText.indexOf("display:none") === -1); // ...over the shorthand
}
function catchesPointer(node) {
  let on = true;                       // the default is to receive them
  chain(node).forEach((n) => {
    if (n.style.cssText.indexOf("pointer-events:none") !== -1) on = false;
    else if (n.style.cssText.indexOf("pointer-events:auto") !== -1) on = true;
  });
  return on;
}
function reachable(container) {
  const surface = find(container, (n) => n.style.cssText.indexOf("transform-origin") !== -1);
  const order = all(surface);
  const out = [];
  order.forEach((target, index) => {
    const wired = (target.listeners.click || []).length ||
                  (target.listeners.pointerdown || []).length;
    if (!wired || !painted(target) || !catchesPointer(target)) return;
    const box = boxOf(target);
    if (!box) return;
    const cx = box.x + box.w / 2, cy = box.y + box.h / 2;
    const kin = new Set(all(target));
    const blocked = order.slice(index + 1).some((other) => {
      if (kin.has(other) || !painted(other) || !catchesPointer(other)) return false;
      const over = rectOf(other);
      return over !== null && cx >= over.x && cx <= over.x + over.w &&
             cy >= over.y && cy <= over.y + over.h;
    });
    if (!blocked) out.push(target.attrs["aria-label"] || target.textContent || target.tagName);
  });
  return out;
}
{
  const live = {};
  const { container } = mount(puzzles.full);
  live.face = reachable(container);
  puzzles.full.payload.modules.forEach((module) => {
    const name = {
      maze: "Maze", simon: "Simon Says",
      according_to_number: "According to number", mini_button: "The mini button",
    }[module.type];
    click(byLabel(container, "Open the " + name + " bay"));
    live["panel:" + module.type] = reachable(container);
    if (module.type !== "mini_button") click(byText(container, "✕ BOMB"));
  });
  click(byText(container, "📖  MANUAL"));
  live["manual:home"] = reachable(container);
  ["Maze", "Simon Says", "According to number", "The mini button"].forEach((name) => {
    click(byText(container, name));
    live["manual:" + name] = reachable(container);
    click(byText(container, "Exit"));
  });
  report.reachable = live;
  game.unmount();
}

process.stdout.write(JSON.stringify(report));
"""


def board(game: BombDefuseGame, seed: int, level: int, module_type: str | None = None):
    """A generated puzzle as the renderer receives it, plus the test's own
    server-side extras under `__`-prefixed keys the renderer never reads."""
    for candidate in range(seed, seed + 500):
        puzzle = game.generate_main(candidate, level)
        types = [module["type"] for module in puzzle.payload["modules"]]
        if module_type is None or types == [module_type]:
            public = puzzle.public()
            public["__reference"] = puzzle.answer
            return public, puzzle
    raise AssertionError(f"no board found for {module_type} at level {level}")


@pytest.fixture(scope="module")
def report() -> dict:
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    game = BombDefuseGame()
    full, full_puzzle = board(game, 99, 13)               # all four bays
    maze, maze_puzzle = board(game, 1, 1, "maze")
    simon, _ = board(game, 1, 1, "simon")
    mini, mini_puzzle = board(game, 1, 1, "mini_button")

    # Which way is a wall from the maze bay's start cell?
    from backend.games.game11_bomb_defuse import MAZE_LAYOUTS, _wall_between
    module = maze["payload"]["modules"][0]
    layout = next(e for e in MAZE_LAYOUTS if list(e["tip"]) == module["tip"])
    start = (module["player"][0], module["player"][1])
    maze["__blocked"] = next(
        side for side in "nsew" if _wall_between(layout, start, side)
    )

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        harness = Path(tmp) / "harness.js"
        harness.write_text(HARNESS)
        puzzles = Path(tmp) / "puzzles.json"
        puzzles.write_text(json.dumps(
            {"full": full, "maze": maze, "simon": simon, "mini": mini}
        ))
        finished = subprocess.run(
            ["node", str(harness), str(RENDERER), str(puzzles), str(MANUAL)],
            capture_output=True, text=True, timeout=90,
        )
    assert finished.returncode == 0, finished.stderr
    out = json.loads(finished.stdout)
    out["_puzzles"] = {"full": full_puzzle, "maze": maze_puzzle,
                       "mini": mini_puzzle}
    return out


# --- the bomb face ------------------------------------------------------


def test_the_face_has_the_controls_the_spec_names(report):
    face = report["face"]
    assert face["shows_fuse"] is True            # the red countdown (§7)
    assert face["has_give_up"] is True           # §21
    assert face["has_manual"] is True
    assert face["bay_buttons"] == 4              # a level-13 board fields four
    assert face["ok_label"] == "OK — bays are still open"


# --- a real defusal -----------------------------------------------------


def test_working_every_bay_then_ok_submits_a_transcript_the_server_accepts(report):
    sent = report["defused"]["sent"]
    assert len(sent) == 1
    payload = json.loads(sent[0])
    assert payload["v"] == 1 and "failed" not in payload
    assert BombDefuseGame().check(report["_puzzles"]["full"], sent[0]) is True
    assert payload["moves"][-1] == {"m": "ok"}
    assert "BOMB DEFUSED" in report["defused"]["screen"]


# --- the fuse -----------------------------------------------------------


def test_the_fuse_counts_down_and_then_goes_off(report):
    fuse = report["fuse"]
    started = report["face"]["shows_fuse"]
    assert started is True
    # Three seconds in, the display has moved (§8: an absolute deadline).
    assert fuse["after3s"] is not None
    assert int(fuse["after3s"]) < report["_puzzles"]["full"].payload["fuse_seconds"]
    assert "MISSION FAILED" in fuse["screen"]
    assert "The fuse ran out." in fuse["screen"]
    assert json.loads(fuse["sent"][0])["failed"] == "timer-expired"


# --- sudden death -------------------------------------------------------


@pytest.mark.parametrize(
    "case,reason,words",
    [
        ("give_up", "give-up", "You gave up."),
        ("premature_ok", "premature_ok", "You pressed OK with a bay still open."),
        ("maze_wall", "maze_wall", "You walked into a wall."),
        ("simon", "simon_wrong", "Wrong colour."),
        ("mini_early", "mini-early", "before it turned red"),
        ("mini_slow", "mini-slow", "too slow off the red"),
        ("mini_release", "mini-release", "before it turned green"),
    ],
)
def test_every_fatal_path_detonates_and_asks_for_a_fresh_bomb(report, case, reason, words):
    got = report["fatal"][case]
    assert "MISSION FAILED" in got["screen"]
    assert words in got["screen"]
    assert "A fresh bomb is on its way." in got["screen"]
    # One submission, five seconds later, and it is a losing one by
    # construction — which is how the engine is asked for the next bomb.
    assert len(got["sent"]) == 1
    assert json.loads(got["sent"][0]) == {"v": 1, "failed": reason}


def test_colour_is_never_the_only_carrier_of_meaning(report):
    # game/RELAY_EXPANSION_GAMES_README.md §6 — the mapping has to be readable
    # without seeing hue, on the bomb and in the manual alike.
    pad = report["access"]["pad_text"]
    assert "▲" in pad and "red" in pad
    rows = report["access"]["manual_rows"]
    for shape in ("▲", "●", "■", "◆"):
        assert rows.count(shape) == 2      # once as a flash, once as a press


def test_the_mini_button_can_be_held_from_the_keyboard(report):
    keyboard = report["keyboard"]
    assert keyboard["focusable"] == "0"
    # Auto-repeat while holding must not read as a second press.
    assert len(keyboard["sent"]) == 1
    assert BombDefuseGame().check(report["_puzzles"]["mini"], keyboard["sent"][0]) is True


def test_arming_the_mini_button_commits_the_player(report):
    armed = report["armed"]
    assert armed["manual_locked"] is True     # no reading your way out of it
    assert armed["no_close_button"] == 0      # ...and no closing the bay either
    assert armed["header"] is True


# --- the manual ---------------------------------------------------------


def test_the_manual_lists_only_the_modules_this_version_implements(report):
    home = report["manual"]["home"]
    assert "The Bomb:" in home
    for name in ("Maze", "Simon Says", "According to number", "The mini button"):
        assert name in home
    # §29/§84: no dead buttons for modules whose behaviour was never verified.
    for deferred in ("Wires", "Memory", "Keypads", "Read and Press"):
        assert deferred not in home


def test_each_manual_page_carries_its_rule(report):
    pages = report["manual"]["pages"]
    # The manual is written in a neutral voice: the same page is read by the
    # Defuser and, on the console, by their Grandmaster.
    maze = pages["Maze"]
    assert "Blue is the Defuser's position" in maze
    assert "red is the way out" in maze
    assert "Green is the tip that identifies which maze" in maze
    simon = pages["Simon Says"]
    for flashed, pressed in (("RED", "BLUE"), ("GREEN", "YELLOW")):
        assert flashed in simon.upper() and pressed in simon.upper()
    assert "strikes" in simon                                  # ...and there are none
    numbers = pages["According to number"]
    assert "green 1 identifies" in numbers
    assert "left = 1, middle = 2, right = 3" in numbers
    mini = pages["The mini button"]
    assert "Wait for the tiny button to turn red." in mini
    assert "until it turns green" in mini
    assert "two-digit code" in mini


def test_exit_walks_back_out_of_the_manual(report):
    assert "Give up" in report["manual"]["back_on_bomb"]


# --- lifecycle ----------------------------------------------------------


def test_unmount_leaves_nothing_running_and_remounting_starts_clean(report):
    life = report["lifecycle"]
    assert life["left_running"] == 0          # fuse, flash and pulse timers gone
    assert life["window_listeners"] == 0
    assert life["detached"] == 0              # the root came out of the container
    assert life["remount_is_fresh"] is True
    assert life["remount_scheduled"] is True  # ...and a new fuse is running

# --- can a click actually land? -----------------------------------------


def test_every_control_on_the_bomb_face_can_be_clicked(report):
    """A click has to *reach* a control, not just have a handler bound to it.

    The empty panel overlay used to span the whole 590x440 surface and sat above
    the bomb face, so every click landed on the overlay instead and the bomb was
    dead to the touch. Firing handlers by label could never catch that; this
    walks the real geometry and paint order.
    """
    face = report["reachable"]["face"]
    for module in ("Maze", "Simon Says", "According to number", "The mini button"):
        assert f"Open the {module} bay" in face
    assert "OK — bays are still open" in face
    assert "Give up" in face
    assert "📖  MANUAL" in face
    assert "🔊 SOUND ON" in face


@pytest.mark.parametrize(
    "panel,controls",
    [
        ("maze", ["Step north", "Step south", "Step east", "Step west"]),
        ("simon", ["red", "blue", "green", "yellow"]),
        ("according_to_number", ["1", "2", "3"]),
        ("mini_button", ["ARM THE BUTTON"]),
    ],
)
def test_every_control_inside_an_open_bay_can_be_clicked(report, panel, controls):
    live = report["reachable"]["panel:" + panel]
    for control in controls:
        assert control in live, f"{control} is unreachable in the {panel} panel"


def test_the_simon_replay_control_can_be_clicked(report):
    # It reads "PLAYING" rather than "PLAY THE FLASHES" here, because opening
    # the bay starts the first stage.
    live = report["reachable"]["panel:simon"]
    assert any(control.startswith("▶") for control in live), live


def test_an_open_bay_covers_the_bomb_but_not_the_manual(report):
    live = report["reachable"]["panel:maze"]
    # Deliberate: the panel sits over the housing, so you close it to reach the
    # bays, OK and Give up again...
    assert "Give up" not in live
    assert "Open the Simon Says bay" not in live
    # ...but the strip under the housing stays live, so you can always go and
    # read the rules for the bay you just opened.
    assert "📖  MANUAL" in live
    assert "✕ BOMB" in live


def test_every_manual_page_can_be_navigated(report):
    home = report["reachable"]["manual:home"]
    for name in ("Maze", "Simon Says", "According to number", "The mini button"):
        assert name in home
        assert "Exit" in report["reachable"]["manual:" + name]
    assert "Exit" in home
