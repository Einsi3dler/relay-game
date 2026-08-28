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

from backend.games.game11_bomb_defuse import (
    RULES_VERSION,
    WITHHELD_PAGES,
    BombDefuseGame,
)

SERVED_AGO_MS = 12_000   # the gap between the server serving and this mount

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
  Date: { now: () => clock.now, parse: Date.parse },
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

// Work every bank in turn: its bays, then OK — which arms the next bank, or
// on the last one defuses the bomb.
function defuse(puzzle) {
  const { container, sent } = mount(puzzle);
  const moves = JSON.parse(puzzle.__reference).moves;
  puzzle.payload.banks.forEach((bank) => {
    bank.modules.forEach((module) => workBay(container, module, moves));
    click(byText(container, "OK"));
  });
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
    // A level-13 board is blacked out, so its readout is dark by design;
    // `bankedFace` below is where the lit one is checked.
    readout: texts(container)[0],
    timer_label: find(container, (n) => n.attrs.role === "timer")
      .attrs["aria-label"],
    has_give_up: seen.indexOf("Give up") !== -1,
    has_manual: seen.indexOf("📖  MANUAL") !== -1,
    bay_buttons: all(container).filter(
      (n) => (n.attrs["aria-label"] || "").indexOf("Open the ") === 0).length,
    ok_label: byText(container, "OK").attrs["aria-label"],
  };
  game.unmount();
}

// 1b. The same face on a board whose clock is still the player's.
{
  const { container } = mount(puzzles.banked);
  report.bankedFace = {
    readout: texts(container)[0],
    fuse: puzzles.banked.payload.banks[0].fuse_seconds,
    timer_label: find(container, (n) => n.attrs.role === "timer")
      .attrs["aria-label"],
  };
  game.unmount();
}

// 2. A whole four-bay defusal, driven through the controls.
{
  const { container, sent } = defuse(puzzles.full);
  report.defused = { sent: sent, screen: screen(container) };
  game.unmount();
}

// 2b. Shutting a bank arms the next one: fresh fuse, fresh bays, a banner.
{
  const { container, sent } = mount(puzzles.banked);
  const banks = puzzles.banked.payload.banks;
  const moves = JSON.parse(puzzles.banked.__reference).moves;
  advance(4000);                                   // burn some of the first fuse
  const beforeFuse = texts(container)[0];
  banks[0].modules.forEach((module) => workBay(container, module, moves));
  click(byText(container, "OK"));
  report.bankTurn = {
    banks: banks.length,
    submitted_yet: sent.length,                    // the board is not over
    banner: screen(container),
    fuse_before: beforeFuse,
    fuse_after: texts(container)[0],
    // The second bank's bays are the ones on the face now.
    open_labels: all(container)
      .map((n) => n.attrs["aria-label"] || "")
      .filter((label) => label.indexOf("Open the ") === 0),
  };
  banks[1].modules.forEach((module) => workBay(container, module, moves));
  click(byText(container, "OK"));
  report.bankTurn.after_last_ok = { sent: sent.length, screen: screen(container) };
  game.unmount();
}

// 3. The fuse: it ticks down, then it goes off.
{
  const { container, sent } = mount(puzzles.banked);
  advance(3000);
  const after3s = byLabel(container, "OK — bays are still open") ?
    texts(container)[0] : null;
  advance(puzzles.banked.payload.banks[0].fuse_seconds * 1000);
  const boom = screen(container);
  advance(5000);
  report.fuse = { after3s: after3s, screen: boom, sent: sent };
  game.unmount();
}

// 3b. A blacked-out board runs no clock of its own: time passing does not end
// it, because on this board that is the server's call.
{
  const { container, sent } = mount(puzzles.full);
  const budget = puzzles.full.payload.time_limit_seconds;
  advance((budget + 120) * 1000);
  report.darkFuse = {
    readout: texts(container)[0],
    screen: screen(container),
    sent: sent.length,
  };
  game.unmount();
  report.darkFuse.left_running = clock.timers.size;
}

// 3c. ...and shutting a bank does not leak the next fuse either.
{
  const { container } = mount(puzzles.full);
  const banks = puzzles.full.payload.banks;
  const moves = JSON.parse(puzzles.full.__reference).moves;
  banks[0].modules.forEach((module) => workBay(container, module, moves));
  click(byText(container, "OK"));
  report.darkFuseBank = {
    banner: screen(container),
    readout: texts(container)[0],
    // No number anywhere on the banner, from either bank.
    leaks: banks.map((bank) => String(bank.fuse_seconds))
      .filter((seconds) => screen(container).indexOf(seconds) !== -1),
  };
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
  click(byLabel(container, puzzles.simon.payload.banks[0].modules[0].sequence[0]));
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
  const module = puzzles.mini.payload.banks[0].modules[0];
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
  const module = puzzles.mini.payload.banks[0].modules[0];
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
  const module = puzzles.mini.payload.banks[0].modules[0];
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
  const module = puzzles.mini.payload.banks[0].modules[0];
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

// 7. The manual: every page, and Exit walking back out. On a shallow board —
// a deep one comes a page short (§2c) and that is scenario 7b.
{
  const { container } = mount(puzzles.maze);
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

// 7b. The withheld page: on a deep board the Defuser's own copy is missing
// one, and the only copy of it in the match is on the Grandmaster's console.
{
  const { container } = mount(puzzles.full);
  click(byText(container, "📖  MANUAL"));
  const withheld = puzzles.full.payload.withheld_pages;
  const names = {
    maze: "Maze", simon: "Simon Says",
    according_to_number: "According to number", mini_button: "The mini button",
  };
  report.withheld = {
    pages: withheld,
    screen: screen(container),
    // The entry is not a button at all: there is nothing here to press.
    buttons: all(container)
      .filter((n) => n.tagName === "button")
      .map((n) => n.textContent),
    reachable: reachable(container),
  };
  // The pages it *does* have still open and still walk back.
  const open = Object.keys(names).filter((t) => withheld.indexOf(t) === -1);
  report.withheld.still_navigable = open.map((type) => {
    click(byText(container, names[type]));
    const seen = screen(container);
    click(byText(container, "Exit"));
    return seen.indexOf(names[type]) !== -1;
  });
  report.withheld.name_of_missing = names[withheld[0]];
  game.unmount();
}

// 8. Lifecycle: nothing left running, and a remount starts clean.
{
  const first = mount(puzzles.banked);
  click(byLabel(first.container, "Open the Simon Says bay"));   // schedules flashes
  advance(500);
  game.unmount();
  const leftRunning = clock.timers.size;
  game.unmount();                                // idempotent
  const madeBefore = clock.made;

  const second = mount(puzzles.banked);
  const fresh = screen(second.container);
  advance(1000);
  game.unmount();
  report.lifecycle = {
    left_running: leftRunning,
    window_listeners: (windowListeners.resize || []).length,
    detached: first.container.children.length,
    remount_is_fresh: fresh.indexOf(String(puzzles.banked.payload.banks[0].fuse_seconds)) !== -1,
    remount_scheduled: clock.made > madeBefore,
  };
}

// 8b. The server's board deadline. On a single-bank board it *is* the fuse,
// so the face counts the server's clock rather than one it started itself.
{
  const single = JSON.parse(JSON.stringify(puzzles.served));
  const fuse = single.payload.banks[0].fuse_seconds;
  const agoMs = single.__servedAgo;
  // Built against the harness clock *now*, not against the clock's starting
  // value: earlier scenarios have moved it, and a deadline baked in Python
  // would already be in the past by the time this runs.
  single.deadline = new Date(
    clock.now - agoMs + fuse * 1000
  ).toISOString().replace("Z", "+00:00");     // as Python emits it

  const { container } = mount(single);
  report.served = {
    fuse: fuse,
    // The board was served `agoMs` before this mount, so an unanchored face
    // reads the whole fuse and an anchored one reads that much less.
    served_ago_s: agoMs / 1000,
    at_mount: texts(container)[0],
  };
  advance(5000);
  report.served.after5s = texts(container)[0];
  game.unmount();

  // Same board, no deadline: the face falls back to its own clock, which is
  // what a practice board does.
  const loose = JSON.parse(JSON.stringify(single));
  delete loose.deadline;
  const second = mount(loose);
  report.served.without_deadline = texts(second.container)[0];
  game.unmount();
}

// 8c. A Freeze pushes the server's deadline out under a live board. The shell
// hands the same puzzle back with the new instant; the face has to follow it,
// or it detonates on a deadline the server has already moved.
{
  const board = JSON.parse(JSON.stringify(puzzles.served));
  const fuse = board.payload.banks[0].fuse_seconds;
  const first = clock.now + fuse * 1000;
  board.deadline = new Date(first).toISOString().replace("Z", "+00:00");
  const { container } = mount(board);
  const before = texts(container)[0];
  advance(4000);
  const burned = texts(container)[0];

  const frozen = JSON.parse(JSON.stringify(board));
  frozen.deadline = new Date(first + 10000).toISOString().replace("Z", "+00:00");
  game.update(frozen);
  report.frozenBoard = {
    at_mount: before,
    after4s: burned,
    after_freeze: texts(container)[0],
  };
  advance(1000);
  report.frozenBoard.still_ticking = texts(container)[0];
  // ...and the same update arriving twice does not pay out twice.
  game.update(frozen);
  report.frozenBoard.repeated = texts(container)[0];
  game.unmount();
}

// 8d. A dark-fuse board keeps no clock, so an update has nothing to move.
{
  const { container } = mount(puzzles.full);
  const moved = JSON.parse(JSON.stringify(puzzles.full));
  moved.deadline = new Date(clock.now + 999000).toISOString().replace("Z", "+00:00");
  game.update(moved);
  report.frozenDarkFuse = { readout: texts(container)[0] };
  game.unmount();
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
  puzzles.full.payload.banks[0].modules.forEach((module) => {
    const name = {
      maze: "Maze", simon: "Simon Says",
      according_to_number: "According to number", mini_button: "The mini button",
    }[module.type];
    click(byLabel(container, "Open the " + name + " bay"));
    live["panel:" + module.type] = reachable(container);
    if (module.type !== "mini_button") click(byText(container, "✕ BOMB"));
  });
  game.unmount();

  // The manual's own pages, from a board whose copy is whole.
  const whole = mount(puzzles.maze);
  click(byText(whole.container, "📖  MANUAL"));
  live["manual:home"] = reachable(whole.container);
  ["Maze", "Simon Says", "According to number", "The mini button"].forEach((name) => {
    click(byText(whole.container, name));
    live["manual:" + name] = reachable(whole.container);
    click(byText(whole.container, "Exit"));
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
        banks = puzzle.payload["banks"]
        if module_type is None:
            # No type asked for: any board, and at a bonus tier that is
            # deliberately a multi-bank one.
            public = puzzle.public()
            public["__reference"] = puzzle.answer
            return public, puzzle
        # A single-bay scenario wants exactly that bay and no bank behind it.
        if len(banks) == 1 and [m["type"] for m in banks[0]["modules"]] == [module_type]:
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

    # A single-bank board as the engine hands it over: the deadline the server
    # published, stamped into the puzzle view exactly as `Player.private` does,
    # for a board served SERVED_AGO ago.
    # A multi-bank board whose clock is still the player's. Dark fuse and banks
    # both start at the bonus-only tiers, so no *generated* board is one and
    # not the other — but the authored practice missions are exactly that, and
    # they are the boards these scenarios were always really about.
    banked_puzzle = game.generate_mission("second_bank")
    banked = banked_puzzle.public()
    banked["__reference"] = banked_puzzle.answer
    assert len(banked["payload"]["banks"]) == 2
    assert banked["payload"]["hidden_deadline"] is False

    served, _ = board(game, 3, 10)
    assert len(served["payload"]["banks"]) == 1, "levels 1-10 are single-bank"
    assert served["payload"]["time_limit_seconds"] == \
        served["payload"]["banks"][0]["fuse_seconds"]
    served["__servedAgo"] = SERVED_AGO_MS   # the harness dates it from this

    # Which way is a wall from the maze bay's start cell?
    from backend.games.game11_bomb_defuse import MAZE_LAYOUTS, _wall_between
    module = maze["payload"]["banks"][0]["modules"][0]
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
        puzzles.write_text(json.dumps({
            "full": full, "maze": maze, "simon": simon, "mini": mini,
            "served": served, "banked": banked,
        }))
        finished = subprocess.run(
            ["node", str(harness), str(RENDERER), str(puzzles), str(MANUAL)],
            capture_output=True, text=True, timeout=90,
        )
    assert finished.returncode == 0, finished.stderr
    out = json.loads(finished.stdout)
    out["_puzzles"] = {"full": full_puzzle, "maze": maze_puzzle,
                       "mini": mini_puzzle, "banked": banked_puzzle}
    return out


# --- the bomb face ------------------------------------------------------


def test_the_face_has_the_controls_the_spec_names(report):
    face = report["face"]
    assert face["has_give_up"] is True           # §21
    assert face["has_manual"] is True
    assert face["bay_buttons"] == 4              # a level-13 board fields four
    assert face["ok_label"] == "OK — bays are still open"


def test_the_red_countdown_is_the_seconds_left(report):
    """§7's red countdown, on a board whose clock is still the player's."""
    banked = report["bankedFace"]
    assert banked["readout"] == str(banked["fuse"])
    assert banked["timer_label"] == "Seconds left on the fuse"


def test_a_dark_fuse_board_shows_no_number_at_all(report):
    """The bonus-only tiers hand the clock to the Grandmaster. "--" rather than
    a blank cell: a dark readout is the bomb refusing to say, and an empty box
    would read as broken."""
    face = report["face"]
    assert face["readout"] == "--"
    assert face["timer_label"] == "The timer is dark — ask your Grandmaster"


# --- a real defusal -----------------------------------------------------


def test_working_every_bay_then_ok_submits_a_transcript_the_server_accepts(report):
    sent = report["defused"]["sent"]
    assert len(sent) == 1
    payload = json.loads(sent[0])
    assert payload["v"] == RULES_VERSION and "failed" not in payload
    assert BombDefuseGame().check(report["_puzzles"]["full"], sent[0]) is True
    assert payload["moves"][-1] == {"m": "ok"}
    assert "BOMB DEFUSED" in report["defused"]["screen"]


def test_shutting_a_bank_arms_the_next_one(report):
    """A bank is not the board: OK shuts one and the next comes up behind it."""
    turn = report["bankTurn"]
    banked = report["_puzzles"]["banked"].payload
    assert turn["banks"] == 2, "this board should come in two banks"
    # Nothing is submitted until the *last* bank is shut.
    assert turn["submitted_yet"] == 0
    assert "BANK 2 ARMED" in turn["banner"]
    # The new bank brings its own fuse, and the face shows it straight away
    # rather than a stale second of the old one.
    assert int(turn["fuse_after"]) != int(turn["fuse_before"])
    assert int(turn["fuse_after"]) == banked["banks"][1]["fuse_seconds"]
    assert f"{banked['banks'][1]['fuse_seconds']}s on the new fuse" in turn["banner"]
    # ...and the bays on the face are the new bank's.
    second = banked["banks"][1]["modules"]
    assert len(turn["open_labels"]) == len(second)
    # Only the final OK ends the bomb.
    assert turn["after_last_ok"]["sent"] == 1
    assert "BOMB DEFUSED" in turn["after_last_ok"]["screen"]


# --- the fuse -----------------------------------------------------------


def test_the_fuse_counts_down_and_then_goes_off(report):
    fuse = report["fuse"]
    # Three seconds in, the display has moved (§8: an absolute deadline).
    assert fuse["after3s"] is not None
    opening = report["_puzzles"]["banked"].payload["banks"][0]["fuse_seconds"]
    assert int(fuse["after3s"]) < opening
    assert "MISSION FAILED" in fuse["screen"]
    assert "The fuse ran out." in fuse["screen"]
    assert json.loads(fuse["sent"][0])["failed"] == "timer-expired"


def test_a_dark_fuse_board_never_ends_itself_on_time(report):
    """A fuse running where nobody can see it would still be the client
    deciding when the board ends. On a blacked-out board the server's deadline
    is the only thing that does — so the client keeps no clock at all."""
    dark = report["darkFuse"]
    assert "MISSION FAILED" not in dark["screen"]
    assert dark["sent"] == 0            # nothing submitted, nothing failed
    assert dark["readout"] == "--"      # still dark, two minutes on
    assert dark["left_running"] == 0


def test_a_dark_fuse_board_does_not_leak_the_next_fuse(report):
    """The bank banner names the seconds on an ordinary board. Here it must
    not, or the number the face is hiding arrives by the back door."""
    bank = report["darkFuseBank"]
    assert "BANK 2 ARMED" in bank["banner"]
    assert "A fresh fuse you cannot see" in bank["banner"]
    assert bank["leaks"] == []
    assert bank["readout"] == "--"


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
    assert json.loads(got["sent"][0]) == {"v": RULES_VERSION, "failed": reason}


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


# --- the withheld page (§2c) --------------------------------------------


def test_a_deep_board_hands_the_defuser_a_manual_with_a_page_missing(report):
    """From WITHHOLD_FROM_LEVEL up, the Grandmaster stops being a speed
    advantage and becomes the only copy of one page in the match."""
    withheld = report["withheld"]
    assert len(withheld["pages"]) == WITHHELD_PAGES
    assert withheld["name_of_missing"] + " — ask your Grandmaster" in \
        withheld["screen"]
    # ...and the home note says the copy is short before they go looking.
    assert "This copy is not complete" in withheld["screen"]


def test_the_withheld_entry_is_not_a_control(report):
    """A disabled button is still something to click at while the fuse burns.
    There is no page behind this one, so there is nothing to press."""
    withheld = report["withheld"]
    missing = withheld["name_of_missing"]
    assert missing not in withheld["buttons"]
    assert missing + " — ask your Grandmaster" not in withheld["buttons"]
    assert not any(
        label.startswith(missing) for label in withheld["reachable"]
    ), withheld["reachable"]


def test_the_pages_the_board_did_leave_still_work(report):
    """One page short is meant to slow a lone Defuser down, not stop them: a
    deep board fields three bays of distinct types, so two stay readable."""
    withheld = report["withheld"]
    assert len(withheld["still_navigable"]) == 3
    assert all(withheld["still_navigable"])
    assert "Exit" in withheld["reachable"]


# --- the server's board deadline ----------------------------------------


def test_the_face_counts_the_servers_clock_not_its_own(report):
    """Levels 1-10 are single-bank, so the board budget the server publishes is
    the bank fuse exactly. Anchoring the face to it means the number the
    Defuser reads is the number the server is counting down — a face that
    started its own clock at mount would be ahead by the whole serve gap."""
    served = report["served"]
    started_at = served["fuse"] - served["served_ago_s"]
    assert served["at_mount"] == str(int(started_at))
    assert served["after5s"] == str(int(started_at) - 5)


def test_a_board_with_no_server_deadline_keeps_its_own_clock(report):
    """Practice has no engine behind it, so there is nothing to anchor to and
    the fuse works exactly as it always did."""
    served = report["served"]
    assert served["without_deadline"] == str(served["fuse"])


def test_the_face_follows_a_deadline_the_server_moves(report):
    """A Freeze pushes the board deadline out — it costs the player their input
    for those seconds, not the board. The face has to follow, or it detonates
    on an instant the server has already moved past."""
    moved = report["frozenBoard"]
    assert int(moved["after4s"]) == int(moved["at_mount"]) - 4
    # Ten seconds handed back, all at once.
    assert int(moved["after_freeze"]) == int(moved["after4s"]) + 10
    assert int(moved["still_ticking"]) == int(moved["after_freeze"]) - 1


def test_the_same_update_twice_pays_out_once(report):
    """Snapshots arrive constantly and carry the same deadline every time. The
    face shifts by how much the deadline *grew*, so a repeat is a no-op."""
    moved = report["frozenBoard"]
    assert moved["repeated"] == moved["still_ticking"]


def test_an_update_moves_nothing_on_a_dark_fuse_board(report):
    """There is no clock here to move: the server owns it outright."""
    assert report["frozenDarkFuse"]["readout"] == "--"


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
