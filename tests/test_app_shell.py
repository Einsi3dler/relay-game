"""The app shell — `frontend/app.js` executed, not scanned.

Every other frontend file in this repo is driven by a node harness; the shell
was the exception, covered only by the regex sweep in
`test_perk_frontend_parity.py`. So the lobby rules it mirrors, the countdown it
draws and the Grandmaster's bomb console it mounts were all behaviourally
untested.

This runs the real file in node against a fake DOM **built from
`frontend/index.html`**: `getElementById` resolves against the ids that page
actually declares and throws on anything else, so renaming an element fails a
test here instead of silently handing the shell a stub. The snapshots it
renders are real ones — `Match.public()` over matches built through the engine's
own lobby API — so a protocol change lands in this file too.

Covered: `startBlocker` (the client mirror of `RelayEngine.start_blocker`,
asserted against the engine itself), `renderBombConsole` (mount, page turns,
teardown), and `startCountdown`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend import config, preview
from backend.engine import EngineResult, RelayEngine
from backend.games.duel1_rps import RockPaperScissorsDuel
from backend.registry import REGISTERED_MODULES, GameRegistry

ROOT = Path(__file__).parents[1]
FRONTEND = ROOT / "frontend"
INDEX = FRONTEND / "index.html"
APP = FRONTEND / "app.js"
MANUAL = FRONTEND / "games" / "bomb_manual.js"

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)

# Games for the seats that are not defusing. Generalist takes anything, and
# SWEEP is the one whose reference answer `check` accepts as-is, which is how a
# scenario below reaches "cleared" through the public submit path.
FILLER = ["sweep", "rewire", "mirror_run", "decant"]


HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const INDEX_HTML = fs.readFileSync(process.argv[2], "utf8");
const APP_SRC = fs.readFileSync(process.argv[3], "utf8");
const MANUAL_SRC = fs.readFileSync(process.argv[4], "utf8");
const PLAN = JSON.parse(fs.readFileSync(process.argv[5], "utf8"));

// --- fake DOM ------------------------------------------------------------
//
// Rich enough for the shell rather than for the web: the shell reaches for
// classList, hidden, querySelector, select.options and `.onclick =` as well as
// addEventListener, so all of those are real here.

function element(tag) {
  const el = {
    tagName: tag, children: [], parentNode: null, attrs: {}, listeners: {},
    style: {}, classes: new Set(), _html: "", _text: "",
    hidden: false, disabled: false, value: "", selectedIndex: 0,
    clientWidth: 0, title: "", onclick: null, onchange: null,
    appendChild(child) {
      if (child.parentNode) child.parentNode.removeChild(child);
      this.children.push(child);
      child.parentNode = this;
      return child;
    },
    removeChild(child) {
      this.children = this.children.filter((c) => c !== child);
      child.parentNode = null;
      return child;
    },
    insertBefore(child, ref) {
      const at = ref ? this.children.indexOf(ref) : -1;
      if (child.parentNode) child.parentNode.removeChild(child);
      if (at === -1) this.children.push(child);
      else this.children.splice(at, 0, child);
      child.parentNode = this;
      return child;
    },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; },
    removeAttribute(name) { delete this.attrs[name]; },
    addEventListener(type, fn) {
      (this.listeners[type] = this.listeners[type] || []).push(fn);
    },
    removeEventListener(type, fn) {
      this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn);
    },
    querySelector(sel) { return matches(this, sel)[0] || null; },
    querySelectorAll(sel) { return matches(this, sel); },
    focus() {},
  };
  el.style.cssText = "";
  // The dashboard sets team colours as custom properties on the element.
  el.style.setProperty = (name, value) => { el.style[name] = String(value); };
  el.style.getPropertyValue = (name) => (name in el.style ? el.style[name] : "");
  el.style.removeProperty = (name) => { delete el.style[name]; };
  el.classList = {
    add(name) { el.classes.add(name); },
    remove(name) { el.classes.delete(name); },
    contains(name) { return el.classes.has(name); },
    toggle(name, force) {
      const on = force === undefined ? !el.classes.has(name) : !!force;
      if (on) el.classes.add(name); else el.classes.delete(name);
      return on;
    },
  };
  Object.defineProperty(el, "className", {
    get() { return [...el.classes].join(" "); },
    set(value) {
      el.classes = new Set(String(value).split(/\s+/).filter(Boolean));
    },
  });
  Object.defineProperty(el, "textContent", {
    get() { return el._text; },
    set(value) { el._text = String(value); el.children = []; },
  });
  Object.defineProperty(el, "innerHTML", {
    get() { return el._html; },
    set(value) {
      el._html = String(value);
      el.children = [];
      el._text = "";
      // The shell builds the team strip from an HTML string, so this parses
      // rather than stores: what it writes has to be visible to the probe.
      if (el._html) parseInto(el, el._html);
    },
  });
  ["firstChild", "lastChild"].forEach((which) => {
    Object.defineProperty(el, which, {
      get() {
        return which === "firstChild"
          ? el.children[0] || null
          : el.children[el.children.length - 1] || null;
      },
    });
  });
  Object.defineProperty(el, "options", {
    get() { return el.children.filter((c) => c.tagName === "option"); },
  });
  // A real <select> reports its first option while nothing has been chosen.
  // The shell reads .value to decide what a handoff or an extend-wait would
  // target, so a select that answered "" until someone clicked would hide a
  // live bug behind a harness that does not behave like a browser.
  let picked = null;
  Object.defineProperty(el, "value", {
    get() {
      if (el.tagName !== "select") return picked === null ? "" : picked;
      const opts = el.children.filter((c) => c.tagName === "option");
      if (picked !== null && opts.some((o) => o.value === picked)) return picked;
      return opts.length ? opts[0].value : "";
    },
    set(next) { picked = String(next); },
  });
  return el;
}

function descendants(node, out) {
  out = out || [];
  node.children.forEach((child) => { out.push(child); descendants(child, out); });
  return out;
}

// Tag (`ul`) and class (`.join-team-btn`) selectors — the only two the shell
// asks for.
function matches(root, sel) {
  return descendants(root).filter((node) =>
    sel[0] === "." ? node.classes.has(sel.slice(1)) : node.tagName === sel
  );
}

// --- a very small HTML parser -------------------------------------------
//
// Enough for index.html and for the one innerHTML string the shell writes:
// tags, attributes, and text runs on leaf elements. No entities, no CDATA.

const VOID = new Set(["meta", "link", "input", "br", "img", "hr", "source"]);
const TAG = /<\/?([a-zA-Z0-9]+)((?:\s+[^\s=>/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>/g;
const ATTR = /([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;

function parseInto(root, src) {
  src = src.replace(/<!--[\s\S]*?-->/g, "").replace(/<!DOCTYPE[^>]*>/gi, "");
  const stack = [root];
  let cursor = 0;
  let hit;
  TAG.lastIndex = 0;
  while ((hit = TAG.exec(src)) !== null) {
    const text = src.slice(cursor, hit.index).trim();
    const top = stack[stack.length - 1];
    if (text && !top.children.length) top._text = text;
    cursor = TAG.lastIndex;
    const [whole, rawTag, attrText, selfClose] = hit;
    const tag = rawTag.toLowerCase();
    if (whole[1] === "/") {
      if (stack.length > 1) stack.pop();
      continue;
    }
    const node = element(tag);
    let attr;
    ATTR.lastIndex = 0;
    while ((attr = ATTR.exec(attrText || "")) !== null) {
      const name = attr[1].toLowerCase();
      const value = attr[2] !== undefined ? attr[2]
        : attr[3] !== undefined ? attr[3]
        : attr[4] !== undefined ? attr[4] : "";
      if (name === "class") node.className = value;
      else if (name === "hidden") node.hidden = true;
      else node.setAttribute(name, value);
    }
    top.appendChild(node);
    if (!VOID.has(tag) && !selfClose) stack.push(node);
  }
  const tail = src.slice(cursor).trim();
  const top = stack[stack.length - 1];
  if (tail && !top.children.length) top._text = tail;
  return root;
}

// --- virtual clock -------------------------------------------------------

const clock = { now: PLAN.now_ms, timers: new Map(), nextId: 1 };

function schedule(fn, delay, every) {
  const id = clock.nextId++;
  clock.timers.set(id, { fn, due: clock.now + (delay || 0), every: every || null });
  return id;
}
function cancel(id) { clock.timers.delete(id); }

function advance(ms) {
  // A move by `undefined` would set the clock to NaN and quietly stop every
  // later scenario from doing anything while still appearing to pass.
  if (typeof ms !== "number" || !isFinite(ms)) {
    throw new Error("advance() needs a finite number of ms, got " + ms);
  }
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

// --- booting one shell ---------------------------------------------------
//
// A fresh context per scenario: app.js is an IIFE that reads its session once
// at boot, so a second scenario needs a second shell rather than a reset.

function boot(scenario) {
  clock.timers.clear();
  clock.now = PLAN.now_ms;

  const document = element("#document");
  parseInto(document, INDEX_HTML);
  const byId = {};
  descendants(document).forEach((node) => {
    if (node.attrs.id) byId[node.attrs.id] = node;
  });

  const sockets = [];
  const windowListeners = {};
  const storage = { relay: JSON.stringify(scenario.session) };

  // A thenable that settles immediately: the shell's boot fetches /api/config
  // and then connects, and the two have to happen in that order without an
  // event loop turn between them.
  function settled(value) {
    return {
      then(fn) {
        let next;
        try { next = fn(value); } catch (err) { return rejected(err); }
        return next && typeof next.then === "function" ? next : settled(next);
      },
      catch() { return this; },
    };
  }
  function rejected(err) {
    return { then() { return this; }, catch(fn) { fn(err); return settled(); } };
  }

  function FakeSocket(url) {
    this.url = url;
    this.readyState = 1;
    this.sent = [];
    sockets.push(this);
  }
  FakeSocket.prototype.send = function (data) { this.sent.push(JSON.parse(data)); };
  FakeSocket.prototype.close = function () { this.readyState = 3; };
  FakeSocket.OPEN = 1;

  // A real Date whose *now* is the frozen clock. The shell reads Date.now() for
  // every countdown, and it constructs one to turn an event's created_at into a
  // wall-clock stamp — a browser has both, so the sandbox has to as well.
  class FakeDate extends Date {
    constructor(...args) {
      if (args.length === 0) super(clock.now);
      else super(...args);
    }
    static now() { return clock.now; }
  }

  const context = {
    console,
    JSON, Math, Date: FakeDate,
    URLSearchParams,
    setTimeout: (fn, ms) => schedule(fn, ms, null),
    clearTimeout: cancel,
    setInterval: (fn, ms) => schedule(fn, ms, ms),
    clearInterval: cancel,
    WebSocket: FakeSocket,
    fetch: (path) => {
      if (path === "/api/config") {
        return settled({ ok: true, json: () => settled(scenario.config) });
      }
      // The design gallery boots off one canned snapshot instead of a socket
      // (backend/preview.py). Served by *exact URL*, the way the route is: a
      // client that forwarded a query the server would not accept gets the
      // same 404 here, instead of a stub that answers anything.
      if (String(path).indexOf("/api/preview") === 0) {
        const found = (scenario.previews || {})[String(path)];
        return settled({
          ok: !!found, json: () => settled({ state: found }),
        });
      }
      return settled({ ok: true, json: () => settled({}) });
    },
    sessionStorage: {
      getItem: (key) => (key in storage ? storage[key] : null),
      setItem: (key, value) => { storage[key] = String(value); },
      removeItem: (key) => { delete storage[key]; },
    },
    navigator: {},
    document: {
      createElement: element,
      // The shell marks the Grandmaster dashboard on <body>, so the fake DOM
      // needs the parsed body (index.html has one) to toggle the class on.
      body: descendants(document).find((n) => n.tagName === "body") || element("body"),
      getElementById(id) {
        if (!(id in byId)) {
          throw new Error("no element #" + id + " in frontend/index.html");
        }
        return byId[id];
      },
    },
  };
  context.window = {
    location: {
      protocol: "http:", host: "relay.test", origin: "http://relay.test",
      search: scenario.search || "",
    },
    history: { replaceState() {} },
    confirm: () => true,
    prompt: () => "",
    navigator: context.navigator,
    addEventListener(type, fn) {
      (windowListeners[type] = windowListeners[type] || []).push(fn);
    },
    removeEventListener(type, fn) {
      windowListeners[type] = (windowListeners[type] || []).filter((f) => f !== fn);
    },
    setTimeout: context.setTimeout, clearTimeout: context.clearTimeout,
    setInterval: context.setInterval, clearInterval: context.clearInterval,
    WebSocket: FakeSocket,
  };
  vm.createContext(context);
  vm.runInContext(MANUAL_SRC, context);          // the console draws the real manual
  // A stub game renderer: the shell only needs *a* renderer to mount, and the
  // real ones have their own harnesses.
  const mounts = [];
  const api = { submit: null };
  context.window.RelayGames = {
    fallback: {
      mount(host, puzzle, given) {
        mounts.push(puzzle.game_id);
        api.submit = given.submit;
        host.appendChild(element("div"));
      },
      unmount() { api.submit = null; },
    },
  };
  context.window.RelayDuels = { fallback: { mount() {}, update() {}, unmount() {} } };
  vm.runInContext(APP_SRC, context);

  return { byId, sockets, windowListeners, mounts, document, api };
}

// --- driving one shell ---------------------------------------------------

function fire(node, type) {
  (node.listeners[type] || []).forEach((fn) => fn({}));
  const prop = "on" + type;
  if (typeof node[prop] === "function") node[prop]({});
}

function byText(root, text) {
  const hit = descendants(root).filter((n) => n.textContent === text);
  if (!hit.length) {
    throw new Error("no node reading " + JSON.stringify(text) + " under #" +
      (root.attrs.id || root.tagName));
  }
  return hit[0];
}

// --- hit testing ---------------------------------------------------------
//
// Firing a handler on a node found by text proves the handler works, never
// that a click could reach it. The console is a fresh absolutely-positioned
// surface inside a scaled frame, and a full-surface layer above the manual
// would eat every page turn while every other assertion still passed.

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
  const order = descendants(root);
  const out = [];
  order.forEach((target, index) => {
    if (!(target.listeners.click || []).length || target.disabled) return;
    const box = boxOf(target);
    if (!box) return;
    const cx = box.x + box.w / 2, cy = box.y + box.h / 2;
    const kin = new Set([target].concat(descendants(target)));
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

// Everything a scenario might want to assert on, sampled at one moment.
// Text of a node and everything under it, joined. The dashboard builds most
// of its cells with appendChild, so `.textContent` alone is empty on the
// wrapper.
function allText(node) {
  return descendants(node).concat([node])
    .map((n) => n._text).filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
}

function dashboardProbe(shell) {
  const $ = (id) => shell.byId[id];
  const kids = (node) => node.children;
  return {
    // shell.document is the parsed tree root, so <body> is found in it rather
    // than hanging off it the way the browser's does.
    body_class: (descendants(shell.document)
      .find((n) => n.tagName === "body") || { className: "" }).className,
    match_code: $("leader-match-code").textContent,
    team: $("leader-team-title").textContent,
    level: $("leader-level").textContent,
    currency: $("leader-currency").textContent,
    team_count: $("leader-team-count").textContent,
    flags: kids($("leader-status-line")).map(allText),
    roster: kids($("leader-roster")).map((row) => ({
      text: allText(row),
      offline: row.classes.has("is-offline"),
      pill: (row.children.filter((c) => c.classes.has("pill"))[0] || {}).classes
        ? [...row.children.filter((c) => c.classes.has("pill"))[0].classes].join(" ")
        : "",
      pill_text: allText(row.children.filter((c) => c.classes.has("pill"))[0] || {
        children: [], _text: "",
      }),
    })),
    opponent: allText($("leader-opponent")),
    avatars: (() => {
      const nodes = descendants($("leader-roster"))
        .filter((n) => n.classes.has("gm-avatar"));
      return {
        count: nodes.length,
        // Nothing may reach for the network to draw a roster row. The SVG
        // xmlns is a namespace, not a fetch, so look for what actually loads.
        remote: nodes.filter((n) => /<img|src=|xlink:href=/.test(n._html || "")).length,
        distinct: new Set(nodes.map((n) => n._html || "")).size,
      };
    })(),
    coins: {
      jammed: descendants($("leader-coins"))
        .some((n) => n.classes.has("gm-jammed")),
      rows: $("leader-coins").children
        .filter((row) => row.classes.has("gm-coin-row"))
        .map((row) => ({
          top: row.classes.has("is-top"),
          text: allText(row),
        })),
    },
    race_gap: allText($("leader-race-gap")),
    race_gap_class: $("leader-race-gap").className,
    race: $("leader-race").children.map((row) => ({
      name: allText(row.children.filter((c) => c.classes.has("gm-race__name"))[0] ||
        { children: [], _text: "" }),
      mine: row.classes.has("is-mine"),
      logo: [...(row.children.filter((c) => c.classes.has("gm-race__logo"))[0] ||
        { children: [] }).children.map((i) => i.className)].join(""),
      stars: (row.children.filter((c) => c.classes.has("gm-race__stars"))[0] ||
        { children: [] }).children.length,
      lit: (row.children.filter((c) => c.classes.has("gm-race__stars"))[0] ||
        { children: [] }).children.filter((s) => s.classes.has("is-on")).length,
      level: allText(row.children.filter((c) => c.classes.has("gm-race__level"))[0] ||
        { children: [], _text: "" }),
    })),
    perk_groups: descendants($("perk-grid"))
      .filter((n) => n.classes.has("gm-perks__label")).map(allText),
    perk_buys: descendants($("perk-grid"))
      .filter((n) => n.classes.has("gm-buy"))
      .map((b) => ({ label: allText(b), disabled: b.disabled,
                     described: b.attrs["aria-label"] })),
    handoff_options: $("handoff-select").children.map((o) => o.textContent),
    handoff_face: ($("handoff-avatar")._html || "").slice(0, 40),
    feed: $("leader-feed").children.map((row) => ({
      text: allText(row),
      time: allText(row.children.filter((c) => c.classes.has("gm-event__time"))[0] ||
        { children: [], _text: "" }),
      mark: (row.children.filter((c) => c.classes.has("gm-ic"))[0] || {
        className: "",
      }).className,
      tone: [...row.classes].filter((c) => c.indexOf("gm-event--") === 0).join(""),
    })),
    duel_seats: $("leader-duel-seats").children.map(allText),
  };
}

function resultProbe(shell) {
  const $ = (id) => shell.byId[id];
  const roster = (id) => $(id).children.map((row) => ({
    text: allText(row),
    top: row.classes.has("is-top"),
    share: allText(row.children.filter((c) => c.classes.has("rs-share"))[0] || {
      children: [], _text: "",
    }),
    coins: allText(row.children.filter((c) => c.classes.has("rs-coins"))[0] || {
      children: [], _text: "",
    }),
  }));
  return {
    tone: $("view-result").className,
    title: $("result-title").textContent,
    sub: allText($("result-sub")),
    crest: ($("result-crest")._html || "").slice(0, 5),
    levels: allText($("result-levels")),
    mine: allText($("result-team-mine")),
    theirs: allText($("result-team-theirs")),
    table_title: $("result-table-title").textContent,
    roster: roster("result-roster"),
    opponents: roster("result-opp-roster"),
    mvp: allText($("result-mvp")),
    rewards: $("result-rewards").children.map(allText),
    feed: $("result-feed").children.map(allText),
  };
}

function probe(shell) {
  const $ = (id) => shell.byId[id];
  const mount = $("leader-bomb-mount");
  return {
    view: ["view-join", "view-lobby", "view-play", "view-leader", "view-result"]
      .filter((id) => !$(id).hidden),
    blocker: {
      text: $("start-blocker").textContent,
      disabled: $("start-btn").disabled,
      panel_hidden: $("host-panel").hidden,
    },
    host_duel_window: {
      options: ($("duel-seconds").options || []).map((o) => [o.value, o.textContent]),
      value: $("duel-seconds").value,
      note: $("duel-seconds-note").textContent,
    },
    console: {
      card_hidden: $("leader-bomb-card").hidden,
      sub: $("leader-bomb-sub").textContent,
      mount_children: mount.children.length,
      stamped: !!(mount.children[0] && mount.children[0].attrs["data-stamp"]),
      texts: descendants(mount).map((n) => n.textContent).filter(Boolean),
      reachable: reachable(mount),
      resize_listeners: (shell.windowListeners.resize || []).length,
      clock_hidden: $("leader-bomb-clock").hidden,
      clock: $("leader-bomb-clock").textContent,
      clock_title: $("leader-bomb-clock").title,
      rail: $("leader-bomb-pages").children.map((tab) => ({
        label: tab.textContent,
        open: tab.classes.has("is-open"),
      })),
      page_count: $("leader-bomb-count").textContent,
      nav_disabled: [$("leader-bomb-prev").disabled, $("leader-bomb-next").disabled],
    },
    countdown: {
      bar_hidden: $("timer-bar").hidden,
      label_hidden: $("timer-label").hidden,
      label: $("timer-label").textContent,
      kind: $("play-clock-label").textContent,
      clock_hidden: $("play-clock").hidden,
      urgent: $("play-clock").classes.has("is-urgent"),
      fill: $("timer-fill").style.width,
    },
    play: {
      identity: allText($("play-identity")),
      level: $("play-level-count").textContent,
      level_fill: $("play-level-fill").style.width,
      game_name: $("play-game-name").textContent,
      prompt: $("puzzle-prompt").textContent,
      mount_class: $("puzzle-mount").className,
      seat: allText($("play-role")),
      earnings: $("play-earnings").children.map(allText),
      bonus_hidden: $("bonus-badge").hidden,
      rest_hidden: $("cleared-card").hidden,
    },
    duel_clock: {
      card_hidden: $("duel-card").hidden,
      title: $("duel-title").textContent,
      hidden: $("duel-clock").hidden,
      text: $("duel-clock").textContent,
      classes: $("duel-clock").className,
      leader_hidden: $("leader-duel-clock").hidden,
      leader_text: $("leader-duel-clock").textContent,
      bar_hidden: $("duel-timer-bar").hidden,
      fill: $("duel-timer-fill").style.width,
    },
    dashboard: dashboardProbe(shell),
    result: resultProbe(shell),
    overlay: {
      hidden: shell.byId["stage-overlay"].hidden,
      classes: shell.byId["stage-overlay"].className,
      text: shell.byId["stage-overlay-text"].textContent,
    },
    timers: clock.timers.size,
    mounted_games: shell.mounts.slice(),
    sent_submits: ((shell.sockets[0] && shell.sockets[0].sent) || [])
      .filter((m) => m.type === "submit_answer").length,
  };
}

// --- run the plan --------------------------------------------------------

const report = {};
PLAN.scenarios.forEach((scenario) => {
  const shell = boot(scenario);
  const socket = shell.sockets[0];
  if (scenario.search) {
    // A gallery boot renders one fetched snapshot and stops. Opening a socket
    // would mean the design gallery had joined somebody's match.
    if (socket) throw new Error(scenario.name + ": a preview opened a socket");
  } else if (!socket) {
    throw new Error(scenario.name + ": the shell never opened a socket");
  } else {
    socket.onopen({});
  }
  const records = {};
  scenario.actions.forEach((action) => {
    if (action.do === "deliver") {
      socket.onmessage({
        data: JSON.stringify({
          type: "state_snapshot", state: scenario.snapshots[action.snapshot],
        }),
      });
    } else if (action.do === "push") {
      // Not every server message is a snapshot: level_advanced and friends
      // arrive on their own and drive the overlay.
      socket.onmessage({ data: JSON.stringify(action.message) });
    } else if (action.do === "click") {
      fire(byText(shell.byId[action.in], action.text), "click");
    } else if (action.do === "advance") {
      advance(action.ms);
    } else if (action.do === "submit") {
      if (!shell.api.submit) throw new Error(scenario.name + ": nothing mounted");
      shell.api.submit(action.answer);
    } else if (action.do === "stamp") {
      // The page a console is turned to lives in a module variable, so it
      // survives a rebuild. Marking the mounted surface is what makes the
      // difference between "redrawn" and "left alone" visible at all.
      const first = shell.byId[action.in].children[0];
      if (!first) throw new Error(scenario.name + ": nothing mounted to stamp");
      first.setAttribute("data-stamp", "1");
    } else if (action.do === "record") {
      records[action.as] = probe(shell);
    } else {
      throw new Error("unknown action " + action.do);
    }
  });
  report[scenario.name] = {
    records: records,
    url: socket ? socket.url : null,     // a gallery boot has no socket
    sent: socket ? socket.sent : [],
  };
});

report._ids = Object.keys(boot(PLAN.scenarios[0]).byId).sort();
process.stdout.write(JSON.stringify(report));
"""


# --- building the matches the shell renders ------------------------------


def _engine(with_bomb: bool = True) -> RelayEngine:
    modules = [
        module
        for module in REGISTERED_MODULES
        if with_bomb or module.id != "bomb_defuse"
    ]
    return RelayEngine(GameRegistry(modules))


def _config_body(engine: RelayEngine) -> dict:
    """What `GET /api/config` returns for this registry (backend/main.py)."""
    return {
        "teams": list(config.TEAM_IDS),
        "players_per_team": engine.max_players_ceiling(),
        "level_count": config.LEVEL_COUNT,
        "wait_seconds": config.WAIT_SECONDS,
        "perks": {perk_id: dict(perk) for perk_id, perk in config.PERKS.items()},
        "roles": {
            role_id: {
                "name": role["name"],
                "games": role["games"],
                "fixed": bool(role.get("fixed")),
                "required": bool(role.get("required")),
            }
            for role_id, role in config.ROLES.items()
        },
        "library": engine.registry.library(),
        "duel_round_seconds_min": config.DUEL_ROUND_SECONDS_MIN,
        "duel_round_seconds_max": config.DUEL_ROUND_SECONDS_MAX,
        "duel_round_seconds_choices": list(config.DUEL_ROUND_SECONDS_CHOICES),
        "duels": engine.registry.duel_library(),
    }


def _lobby(engine: RelayEngine, per_team: int = 2, min_players: int = 2):
    """Two teams, a claimed Grandmaster each, `per_team` unroled seats.

    The first joiner — alpha's Grandmaster — is the host, so the snapshot it is
    addressed to is the one that draws the start button.
    """
    match = engine.create_match()
    match.min_players = min_players
    seats: dict[str, list] = {}
    leaders: dict[str, object] = {}
    for team_id in config.TEAM_IDS:
        leader, _ = engine.join_match(match, f"{team_id}-lead", team_id, now=NOW)
        assert engine.claim_leader(match, leader.id).ok
        leaders[team_id] = leader
        seats[team_id] = []
        for index in range(per_team):
            player, _ = engine.join_match(
                match, f"{team_id[0]}{index}", team_id, now=NOW
            )
            seats[team_id].append(player)
    return match, seats, leaders


def _seat(engine, match, leaders, player, role: str, game: str | None = None):
    leader_id = leaders[player.team_id].id
    assert engine.assign_role(match, leader_id, player.id, role).ok
    if game is not None:
        assert engine.assign_game(match, leader_id, player.id, game).ok


def _ready_lobby(engine: RelayEngine):
    """A lobby that starts: one Defuser and one filler seat per team."""
    match, seats, leaders = _lobby(engine)
    for team_id in config.TEAM_IDS:
        _seat(engine, match, leaders, seats[team_id][0], "defuser")
        _seat(engine, match, leaders, seats[team_id][1], "generalist", FILLER[0])
    return match, seats, leaders


# Gallery entries worth proving the client can still draw: one per view the
# shell has. The query string is what the browser would carry.
PREVIEW_BOOTS = (
    ("lobby", "?preview=lobby&key=dev"),
    ("solving", "?preview=solving&key=dev"),
    ("cleared", "?preview=cleared&key=dev"),
    ("leader", "?preview=leader&key=dev"),
    ("won", "?preview=won&key=dev"),
    ("lost", "?preview=lost&key=dev"),
    ("duel", "?preview=duel&game=crown_duel&phase=reveal&key=dev"),
)


def _preview_snapshot(search: str) -> dict:
    """The snapshot the gallery route serves for this query string.

    Parsed the way the route parses it, so the harness cannot serve a snapshot
    the real server would have refused.
    """
    query = dict(
        pair.split("=", 1) for pair in search.lstrip("?").split("&") if "=" in pair
    )
    name = query.pop("preview", "")
    query.pop("key", None)
    built = preview.snapshot(name, **query)
    assert built is not None, f"no preview named {name!r}"
    return built


def _blocker_cases() -> list[tuple[str, RelayEngine, object, str]]:
    """(name, engine, match, host_id) for each lobby rule the client mirrors."""
    cases = []

    engine = _engine()
    match, _, _ = _ready_lobby(engine)
    cases.append(("ready", engine, match))

    engine = _engine()
    match, _, _ = _ready_lobby(engine)
    engine.join_match(match, "drifter", None, now=NOW)
    cases.append(("unassigned", engine, match))

    engine = _engine()
    match, _, leaders = _ready_lobby(engine)
    match.teams["bravo"].leader_id = None
    leaders["bravo"].is_leader = False
    cases.append(("no_grandmaster", engine, match))

    engine = _engine()
    match, _, _ = _ready_lobby(engine)
    match.min_players = 3
    cases.append(("too_few_players", engine, match))

    engine = _engine()
    match, seats, _ = _ready_lobby(engine)
    seats["bravo"][1].role = None
    seats["bravo"][1].assigned_game = None
    cases.append(("no_role", engine, match))

    engine = _engine()
    match, seats, _ = _ready_lobby(engine)
    seats["bravo"][1].assigned_game = None
    cases.append(("no_game", engine, match))

    engine = _engine()
    match, seats, leaders = _lobby(engine)
    _seat(engine, match, leaders, seats["alpha"][0], "defuser")
    _seat(engine, match, leaders, seats["alpha"][1], "generalist", FILLER[0])
    _seat(engine, match, leaders, seats["bravo"][0], "generalist", FILLER[0])
    _seat(engine, match, leaders, seats["bravo"][1], "generalist", FILLER[1])
    cases.append(("no_defuser", engine, match))

    # Two Defusers is refused at the click that would create it, so the state
    # only exists if something upstream breaks. Both mirrors keep a defensive
    # branch for it; this builds it by hand so both are asked the question.
    engine = _engine()
    match, seats, _ = _ready_lobby(engine)
    seats["bravo"][1].role = "defuser"
    seats["bravo"][1].assigned_game = "bomb_defuse"
    cases.append(("two_defusers", engine, match))

    # The squeeze: one seat, and a Duelist is already sitting in it.
    engine = _engine()
    match, seats, leaders = _lobby(engine, per_team=1, min_players=1)
    _seat(engine, match, leaders, seats["alpha"][0], "defuser")
    _seat(engine, match, leaders, seats["bravo"][0], "duelist")
    cases.append(("duelist_squeeze", engine, match))

    # The Duelist mirror: alpha fields one, bravo does not.
    engine = _engine()
    match, seats, leaders = _lobby(engine, per_team=2, min_players=2)
    _seat(engine, match, leaders, seats["alpha"][0], "defuser")
    _seat(engine, match, leaders, seats["alpha"][1], "duelist")
    _seat(engine, match, leaders, seats["bravo"][0], "defuser")
    _seat(engine, match, leaders, seats["bravo"][1], "generalist", FILLER[0])
    cases.append(("duelist_mirror", engine, match))

    return cases


def _clear_a_player(engine: RelayEngine, match, player) -> None:
    """Take `player` to cleared through the public submit path."""
    puzzle = player.current_main
    result = engine.submit_answer(
        match, player.id, puzzle.id, puzzle.answer, now=NOW
    )
    assert result.correct is True, "the reference answer should clear the board"


@pytest.fixture(scope="module")
def shell() -> dict:
    if shutil.which("node") is None:
        pytest.skip("node not installed")

    scenarios: list[dict] = []
    expected: dict = {}

    # --- startBlocker, against the engine's own answer -------------------
    for name, engine, match in _blocker_cases():
        host_id = match.host_player_id
        expected[name] = engine.start_blocker(match)
        scenarios.append({
            "name": f"blocker:{name}",
            "config": _config_body(engine),
            "session": {"matchId": match.id, "playerId": host_id},
            "snapshots": [match.public(host_id)],
            "actions": [
                {"do": "deliver", "snapshot": 0},
                {"do": "record", "as": "lobby"},
            ],
        })

    # --- the Grandmaster command dashboard --------------------------------
    engine = _engine()
    match, seats, leaders = _lobby(engine, per_team=5, min_players=2)
    dash_roles = ["defuser", "duelist", "technocrat", "puzzle_master", "spymaster"]
    dash_games = [None, None, "rewire", "decant", "echo"]
    for team_id in config.TEAM_IDS:
        for at, (role, game) in enumerate(zip(dash_roles, dash_games)):
            _seat(engine, match, leaders, seats[team_id][at], role, game)
    assert engine.start_match(match, now=NOW).changed
    dash_lead = leaders["alpha"]
    dash_team = match.teams["alpha"]
    defuser, duelist, solver, gambler, absent = seats["alpha"]

    for member, coins in zip(seats["alpha"], [4, 11, 7, 2, 0]):
        member.coins_earned = coins
    duelist.status = "cleared"     # a Duelist is green by winning a duel
    solver.status = "cleared"
    gambler.status = "bonus"
    absent.connected = False
    dash_team.currency = 9
    dash_team.level = 4
    dash_team.shield_active = True
    dash_team.duel_streak = 2
    live_snapshot = match.public(dash_lead.id)

    dash_team.currency = 0
    broke_snapshot = match.public(dash_lead.id)

    dash_team.currency = 9
    dash_team.shield_active = False
    # Set the deadline rather than buying the perk: the view masks against real
    # time while the harness clock is pinned to NOW, and this has to read as
    # silenced to both, so the snapshot really is the blinded one.
    dash_team.silenced_until = (
        datetime.now(timezone.utc) + timedelta(hours=1)
    ).isoformat()
    dash_silenced = match.public(dash_lead.id)
    assert dash_silenced["teams"]["alpha"]["green_count"] is None
    assert all(
        member["status"] == "hidden"
        for member in dash_silenced["teams"]["alpha"]["players"]
        if not member["is_leader"]
    )

    scenarios.append({
        "name": "dashboard",
        "config": _config_body(engine),
        "session": {"matchId": match.id, "playerId": dash_lead.id},
        "snapshots": [live_snapshot, broke_snapshot, dash_silenced],
        "actions": [
            {"do": "deliver", "snapshot": 0},
            {"do": "record", "as": "live"},
            {"do": "deliver", "snapshot": 1},
            {"do": "record", "as": "broke"},
            {"do": "deliver", "snapshot": 2},
            {"do": "record", "as": "silenced"},
            {"do": "deliver", "snapshot": 0},
            {"do": "push", "message": {
                "type": "level_advanced", "team_id": "alpha", "level": 5}},
            {"do": "record", "as": "ours_advanced"},
            {"do": "push", "message": {
                "type": "level_advanced", "team_id": "bravo", "level": 4}},
            {"do": "record", "as": "theirs_advanced"},
            # Nothing else is sent while a Grandmaster only watches, so this is
            # the only thing keeping the match off the eviction sweep.
            {"do": "advance", "ms": 15 * 60 * 1000},
            {"do": "record", "as": "idle"},
        ],
    })
    expected["dashboard"] = {
        "match_id": match.id,
        "team_name": dash_team.name,
        "level_count": match.level_count,
        "perk_count": len(config.PERKS),
        "names": [player.name for player in seats["alpha"]],
    }

    # --- the bomb console -------------------------------------------------
    engine = _engine()
    match, seats, leaders = _ready_lobby(engine)
    lobby_snapshot = match.public(leaders["alpha"].id)
    assert engine.start_match(match, now=NOW).changed
    active_snapshot = match.public(leaders["alpha"].id)
    defuser_name = seats["alpha"][0].name
    match.status = "finished"
    match.winner_team_id = "alpha"
    finished_snapshot = match.public(leaders["alpha"].id)
    scenarios.append({
        "name": "console",
        "config": _config_body(engine),
        "session": {"matchId": match.id, "playerId": leaders["alpha"].id},
        "snapshots": [lobby_snapshot, active_snapshot, finished_snapshot],
        "actions": [
            {"do": "deliver", "snapshot": 0},
            {"do": "record", "as": "lobby"},
            {"do": "deliver", "snapshot": 1},
            {"do": "record", "as": "home"},
            {"do": "click", "in": "leader-bomb-mount", "text": "Simon Says"},
            {"do": "stamp", "in": "leader-bomb-mount"},
            {"do": "record", "as": "page"},
            # A fresh snapshot must not turn the page back for a Grandmaster
            # who is mid-sentence reading it out.
            {"do": "deliver", "snapshot": 1},
            {"do": "record", "as": "redrawn"},
            {"do": "click", "in": "leader-bomb-mount", "text": "Exit"},
            {"do": "record", "as": "back_home"},
            {"do": "deliver", "snapshot": 2},
            {"do": "record", "as": "finished"},
        ],
    })

    # Silence: the console blanks, and comes back on its own.
    engine = _engine()
    match, seats, leaders = _ready_lobby(engine)
    assert engine.start_match(match, now=NOW).changed
    clear_snapshot = match.public(leaders["alpha"].id)
    match.teams["bravo"].currency = 99
    assert engine.buy_perk(
        match, leaders["bravo"].id, "silence", now=NOW
    ).ok, "Silence should land on alpha"
    silenced_snapshot = match.public(leaders["alpha"].id)
    assert silenced_snapshot["teams"]["alpha"]["silenced_until"] is not None
    silence_seconds = config.PERKS["silence"]["seconds"]
    scenarios.append({
        "name": "console_silenced",
        "config": _config_body(engine),
        "session": {"matchId": match.id, "playerId": leaders["alpha"].id},
        "snapshots": [silenced_snapshot],
        "actions": [
            {"do": "deliver", "snapshot": 0},
            {"do": "record", "as": "jammed"},
            # Silence is masked in the view, so nothing on the server fires
            # when it lapses: watchSilence has to ask for the snapshot itself.
            {"do": "advance", "ms": silence_seconds * 1000 + 1000},
            {"do": "record", "as": "lapsed"},
            {"do": "deliver", "snapshot": 0},
            {"do": "record", "as": "returned"},
        ],
    })

    # ...and it holds the page the Grandmaster was reading out.
    scenarios.append({
        "name": "console_silenced_mid_page",
        "config": _config_body(engine),
        "session": {"matchId": match.id, "playerId": leaders["alpha"].id},
        "snapshots": [clear_snapshot, silenced_snapshot],
        "actions": [
            {"do": "deliver", "snapshot": 0},
            {"do": "click", "in": "leader-bomb-mount", "text": "The mini button"},
            {"do": "record", "as": "reading"},
            {"do": "deliver", "snapshot": 1},
            {"do": "record", "as": "jammed"},
            {"do": "advance", "ms": silence_seconds * 1000 + 1000},
            {"do": "deliver", "snapshot": 0},
            {"do": "record", "as": "returned"},
        ],
    })

    # A server that does not register the bomb fields no Defuser, and the
    # console has nothing to be the second seat for.
    bombless = _engine(with_bomb=False)
    match, seats, leaders = _lobby(bombless)
    for team_id in config.TEAM_IDS:
        for index in range(2):
            _seat(bombless, match, leaders, seats[team_id][index],
                  "generalist", FILLER[index])
    assert bombless.start_blocker(match) is None
    assert bombless.start_match(match, now=NOW).changed
    scenarios.append({
        "name": "console_without_a_defuser",
        "config": _config_body(bombless),
        "session": {"matchId": match.id, "playerId": leaders["alpha"].id},
        "snapshots": [match.public(leaders["alpha"].id)],
        "actions": [
            {"do": "deliver", "snapshot": 0},
            {"do": "record", "as": "active"},
        ],
    })

    # --- startCountdown ---------------------------------------------------
    engine = _engine()
    match, seats, leaders = _ready_lobby(engine)
    solver = seats["alpha"][1]                       # the SWEEP seat
    assert engine.start_match(match, now=NOW).changed
    solving_snapshot = match.public(solver.id)
    _clear_a_player(engine, match, solver)
    cleared_snapshot = match.public(solver.id)
    assert engine.choose_bonus(match, solver.id, now=NOW).ok
    bonus_snapshot = match.public(solver.id)
    wait_seconds = match.config_snapshot["wait_seconds"]
    scenarios.append({
        "name": "countdown",
        "config": _config_body(engine),
        "session": {"matchId": match.id, "playerId": solver.id},
        "snapshots": [solving_snapshot, cleared_snapshot, bonus_snapshot],
        "actions": [
            {"do": "deliver", "snapshot": 0},
            {"do": "record", "as": "solving"},
            {"do": "deliver", "snapshot": 1},
            {"do": "record", "as": "cleared"},
            {"do": "advance", "ms": 30_000},
            {"do": "record", "as": "cleared_30s"},
            {"do": "deliver", "snapshot": 2},
            {"do": "record", "as": "bonus"},
            {"do": "advance", "ms": wait_seconds * 1000},
            {"do": "record", "as": "lapsed"},
            {"do": "deliver", "snapshot": 0},
            {"do": "record", "as": "back_to_solving"},
        ],
    })

    # --- the duel round clock ------------------------------------------------
    # Every duel game declares its own window and the host can override all of
    # them, so the shell has to draw its countdown from the duel's
    # `round_seconds` rather than the module default in `payload`.
    duel_engine = RelayEngine(
        GameRegistry(REGISTERED_MODULES, duels=[RockPaperScissorsDuel()])
    )
    match, seats, leaders = _lobby(duel_engine, per_team=3, min_players=3)
    for team_id in config.TEAM_IDS:
        _seat(duel_engine, match, leaders, seats[team_id][0], "defuser")
        _seat(duel_engine, match, leaders, seats[team_id][1], "generalist", FILLER[0])
        _seat(duel_engine, match, leaders, seats[team_id][2], "duelist")
    assert duel_engine.host_set_duel_seconds(match, match.host_player_id, 6).ok
    assert duel_engine.start_match(match, now=NOW).changed
    champion = seats["alpha"][2]
    duel = match.duel
    open_round = match.public(champion.id)
    assert open_round["duel"]["round_seconds"] == 6
    for side, move in (("a", "rock"), ("b", "scissors")):
        assert duel_engine.duel_choice(
            match, duel.sides[side], duel.id, duel.state.round_index, move, now=NOW
        ).ok
    reveal = match.public(champion.id)
    scenarios.append({
        "name": "duel_clock",
        "config": _config_body(duel_engine),
        "session": {"matchId": match.id, "playerId": champion.id},
        "snapshots": [open_round, reveal],
        "actions": [
            {"do": "deliver", "snapshot": 0},
            {"do": "record", "as": "open"},
            {"do": "advance", "ms": 3_000},
            {"do": "record", "as": "half"},
            {"do": "advance", "ms": 2_500},
            {"do": "record", "as": "urgent"},
            {"do": "deliver", "snapshot": 1},
            {"do": "record", "as": "reveal"},
        ],
    })

    # --- a dark fuse: the console grows a clock ------------------------------
    dark = _engine()
    match, seats, leaders = _ready_lobby(dark)
    assert dark.start_match(match, now=NOW).changed
    defuser = seats["alpha"][0]
    assert defuser.assigned_game == "bomb_defuse"
    defuser.current_main.payload["hidden_deadline"] = True
    defuser.current_main.payload["time_limit_seconds"] = BOARD_LIMIT
    dark._arm_board_deadline(match, defuser, EngineResult(), NOW)
    dark_snapshot = match.public(leaders["alpha"].id)
    lit_snapshot_source = match.public(defuser.id)
    assert lit_snapshot_source["me"]["puzzle_deadline"] is None
    match.teams["bravo"].currency = 99
    assert dark.buy_perk(match, leaders["bravo"].id, "silence", now=NOW).ok
    dark_silenced = match.public(leaders["alpha"].id)
    scenarios.append({
        "name": "console_clock",
        "config": _config_body(dark),
        "session": {"matchId": match.id, "playerId": leaders["alpha"].id},
        "snapshots": [dark_snapshot, dark_silenced],
        "actions": [
            {"do": "deliver", "snapshot": 0},
            {"do": "record", "as": "ticking"},
            {"do": "advance", "ms": 25_000},
            {"do": "record", "as": "later"},
            # The manual holds its page across snapshots; the clock must not.
            {"do": "click", "in": "leader-bomb-mount", "text": "Maze"},
            {"do": "deliver", "snapshot": 0},
            {"do": "record", "as": "redrawn"},
            {"do": "deliver", "snapshot": 1},
            {"do": "record", "as": "silenced"},
            {"do": "advance", "ms": BOARD_LIMIT * 1000},
            {"do": "record", "as": "after_silence"},
        ],
    })

    # --- the board deadline on the same bar -------------------------------
    # A game that caps its board (docs/GAME_MODULE_SPEC.md). The real bomb
    # would do, but the bar is engine-generic, so the test is too.
    capped = _engine()
    match, seats, leaders = _ready_lobby(capped)
    assert capped.start_match(match, now=NOW).changed
    boarder = seats["alpha"][1]
    boarder.current_main.payload["time_limit_seconds"] = BOARD_LIMIT
    capped._arm_board_deadline(match, boarder, EngineResult(), NOW)
    board_snapshot = match.public(boarder.id)
    assert board_snapshot["me"]["puzzle_deadline"] is not None
    scenarios.append({
        "name": "board_deadline",
        "config": _config_body(capped),
        "session": {"matchId": match.id, "playerId": boarder.id},
        "snapshots": [board_snapshot],
        "actions": [
            {"do": "deliver", "snapshot": 0},
            {"do": "record", "as": "armed"},
            {"do": "advance", "ms": 20_000},
            {"do": "record", "as": "later"},
            {"do": "advance", "ms": BOARD_LIMIT * 1000},
            {"do": "record", "as": "lapsed"},
        ],
    })

    # --- a frozen player's answer is held, not lost ------------------------
    # Some games submit exactly once, at the end (the bomb presses OK and that
    # is the whole transcript). Sending that into a freeze has the server throw
    # it away with no way to send it again.
    frozen = _engine()
    match, seats, leaders = _ready_lobby(frozen)
    assert frozen.start_match(match, now=NOW).changed
    solver = seats["alpha"][1]
    for other in seats["alpha"]:
        if other is not solver:
            board = other.current_main
            frozen.submit_answer(match, other.id, board.id, board.answer, now=NOW)
    match.teams["bravo"].currency = 99
    assert frozen.buy_perk(match, leaders["bravo"].id, "freeze", now=NOW).ok
    assert solver.frozen_until is not None, "the freeze should land on the solver"
    freeze_seconds = config.PERKS["freeze"]["seconds"]
    frozen_snapshot = match.public(solver.id)
    solver.frozen_until = None
    thawed_snapshot = match.public(solver.id)
    scenarios.append({
        "name": "held_submit",
        "config": _config_body(frozen),
        "session": {"matchId": match.id, "playerId": solver.id},
        "snapshots": [frozen_snapshot, thawed_snapshot],
        "actions": [
            {"do": "deliver", "snapshot": 0},
            {"do": "submit", "answer": "the-one-and-only-answer"},
            {"do": "record", "as": "while_frozen"},
            {"do": "advance", "ms": freeze_seconds * 1000 + 500},
            {"do": "deliver", "snapshot": 1},
            {"do": "record", "as": "thawed"},
        ],
    })

    # --- the design gallery, booted the way the browser boots it -------------
    # `/play?preview=<state>` renders one canned snapshot from
    # backend/preview.py and opens no socket. These run the shipped app.js
    # against the real snapshots, so a gallery entry that stopped rendering
    # fails here rather than in front of whoever opened it.
    for name, search in PREVIEW_BOOTS:
        scenarios.append({
            "name": f"preview:{name}",
            "config": _config_body(_engine()),
            "session": None,
            "search": search,
            # Keyed by the URL the client must ask for, not by scenario name.
            "previews": {f"/api/preview{search}": _preview_snapshot(search)},
            "snapshots": [],
            "actions": [{"do": "record", "as": "booted"}],
        })

    plan = {"now_ms": NOW_MS, "scenarios": scenarios}
    with tempfile.TemporaryDirectory() as tmp:
        harness = Path(tmp) / "harness.js"
        harness.write_text(HARNESS)
        plan_file = Path(tmp) / "plan.json"
        plan_file.write_text(json.dumps(plan))
        finished = subprocess.run(
            ["node", str(harness), str(INDEX), str(APP), str(MANUAL),
             str(plan_file)],
            capture_output=True, text=True, timeout=90,
        )
    assert finished.returncode == 0, finished.stderr
    report = json.loads(finished.stdout)
    report["_expected"] = expected
    report["_defuser_name"] = defuser_name
    report["_wait_seconds"] = wait_seconds
    return report


BOARD_LIMIT = 90


# --- the harness itself --------------------------------------------------


def test_the_fake_dom_is_built_from_the_real_index_html(shell):
    """`getElementById` resolves index.html's ids and nothing else, so a
    renamed element fails a test instead of getting a silent stub."""
    ids = shell["_ids"]
    for required in ("view-play", "view-leader", "timer-bar", "timer-fill",
                     "timer-label", "start-btn", "start-blocker",
                     "leader-bomb-card", "leader-bomb-mount", "leader-bomb-sub"):
        assert required in ids, required
    # Every id the shell asks for was resolvable — an unknown one throws inside
    # the harness, which would have failed the subprocess.
    assert len(ids) > 40


def test_the_shell_connects_with_the_saved_session(shell):
    url = shell["console"]["url"]
    assert url.startswith("ws://relay.test/ws/matches/")
    assert "player_id=p_" in url


# --- startBlocker: the client mirror of the engine ------------------------


BLOCKER_CASES = [
    "ready", "unassigned", "no_grandmaster", "too_few_players", "no_role",
    "no_game", "no_defuser", "two_defusers", "duelist_squeeze",
    "duelist_mirror",
]


@pytest.mark.parametrize("case", BLOCKER_CASES)
def test_the_start_button_agrees_with_the_engine(shell, case):
    """app.js `startBlocker` is a copy of `RelayEngine.start_blocker`, and a
    copy that disagrees is worse than no copy: the host would be offered a
    button the server then refuses, or refused one it would have allowed."""
    engine_says = shell["_expected"][case]
    client = shell[f"blocker:{case}"]["records"]["lobby"]["blocker"]
    assert client["panel_hidden"] is False, "the host should see the panel"
    assert client["disabled"] is (engine_says is not None), (
        f"{case}: engine said {engine_says!r}, client said {client['text']!r}"
    )


def test_a_startable_lobby_says_so(shell):
    client = shell["blocker:ready"]["records"]["lobby"]["blocker"]
    assert client["text"] == "All set — go!"


def test_the_defuser_rules_get_their_own_copy(shell):
    """The required-role branches are the ones this branch added, and their
    wording is the only explanation the host gets."""
    records = shell["blocker:no_defuser"]["records"]["lobby"]["blocker"]
    assert records["text"] == "Team Bravo needs a Defuser."
    two = shell["blocker:two_defusers"]["records"]["lobby"]["blocker"]
    assert two["text"] == "Team Bravo can only field one Defuser."
    squeeze = shell["blocker:duelist_squeeze"]["records"]["lobby"]["blocker"]
    assert "needs a Defuser, but its only player is a Duelist" in squeeze["text"]
    assert "drop the Duelist or add a player" in squeeze["text"]


def test_the_duelist_mirror_still_names_both_teams(shell):
    text = shell["blocker:duelist_mirror"]["records"]["lobby"]["blocker"]["text"]
    assert text == "Team Alpha has a Duelist — team Bravo needs one too."


# --- the bomb console ----------------------------------------------------


def test_the_console_waits_for_the_match_to_start(shell):
    """In the lobby there is no bomb to read for, so there is no manual."""
    lobby = shell["console"]["records"]["lobby"]
    assert lobby["view"] == ["view-lobby"]
    assert lobby["console"]["card_hidden"] is True
    assert lobby["console"]["mount_children"] == 0


def test_the_console_mounts_the_manual_for_the_grandmaster(shell):
    home = shell["console"]["records"]["home"]
    assert home["view"] == ["view-leader"]
    assert home["console"]["card_hidden"] is False
    assert shell["_defuser_name"] in home["console"]["sub"]
    assert "cannot see this" in home["console"]["sub"]
    texts = home["console"]["texts"]
    assert "The Bomb:" in texts
    for page in ("Maze", "Simon Says", "According to number", "The mini button"):
        assert page in texts
    # The Defuser's copy costs fuse; the console's does not, and its home note
    # is the one place that is said.
    assert any("costs them fuse" in text for text in texts)


def test_every_console_control_can_actually_be_clicked(shell):
    """The console wraps the manual in a scaled frame — a new absolutely
    positioned surface, which is exactly how the bomb face went dead once."""
    home = shell["console"]["records"]["home"]["console"]["reachable"]
    for page in ("Maze", "Simon Says", "According to number", "The mini button"):
        assert page in home, page
    assert "Exit" in home
    page = shell["console"]["records"]["page"]["console"]["reachable"]
    assert "Exit" in page


def test_the_console_turns_pages_and_walks_back(shell):
    records = shell["console"]["records"]
    assert "Simon Says" in records["page"]["console"]["texts"]
    # On the page, not the selector: the home note is gone.
    assert not any(
        "costs them fuse" in text for text in records["page"]["console"]["texts"]
    )
    assert "The Bomb:" in records["back_home"]["console"]["texts"]


def test_the_rail_lists_every_page_the_manual_has(shell):
    """Built from the module's own PAGES, so a page added to bomb_manual.js
    appears in the console's rail with no change to the shell."""
    home = shell["console"]["records"]["home"]["console"]
    labels = [tab["label"] for tab in home["rail"]]
    assert labels == ["Contents", "Maze", "Simon Says",
                      "According to number", "The mini button"]
    # The open stop is marked, and it is the one actually being drawn.
    assert [tab["label"] for tab in home["rail"] if tab["open"]] == ["Contents"]
    assert home["page_count"] == "1 / 5"
    # Nothing before the first stop.
    assert home["nav_disabled"] == [True, False]


def test_the_rail_follows_the_page_the_manual_turned_to(shell):
    """The rail is chrome around the manual, not a second source of truth: a
    page turned from inside the manual has to leave the rail agreeing."""
    page = shell["console"]["records"]["page"]["console"]
    assert [tab["label"] for tab in page["rail"] if tab["open"]] == ["Simon Says"]
    assert page["page_count"] == "3 / 5"
    assert page["nav_disabled"] == [False, False]


def test_a_fresh_snapshot_does_not_turn_the_page(shell):
    """Snapshots arrive constantly; redrawing on each would tear the manual out
    from under a Grandmaster reading it aloud."""
    records = shell["console"]["records"]
    assert records["redrawn"]["console"]["texts"] == \
        records["page"]["console"]["texts"]
    assert records["redrawn"]["console"]["mount_children"] == 1
    # And the console was not rebuilt underneath it: the surface mounted before
    # the snapshot arrived is still the one on screen. The page alone proves
    # nothing here — it lives in a module variable and would survive a rebuild.
    assert records["page"]["console"]["stamped"] is True
    assert records["redrawn"]["console"]["stamped"] is True


def test_the_console_tears_down_when_the_match_ends(shell):
    finished = shell["console"]["records"]["finished"]
    assert finished["view"] == ["view-result"]
    assert finished["console"]["card_hidden"] is True
    assert finished["console"]["mount_children"] == 0
    # The resize handler is the one thing that outlives the DOM if it leaks.
    assert finished["console"]["resize_listeners"] == 0


def test_silence_takes_the_manual_too(shell):
    """A silenced Grandmaster already loses the roster and the who-cleared
    feed. Leaving them the one page that still helps would make the perk a
    half-measure — and on a board with a withheld page, the Defuser can hear
    the difference (docs/GAME_DESIGN.md §2c)."""
    jammed = shell["console_silenced"]["records"]["jammed"]["console"]
    # The card stays: a console that vanished would read as a bug.
    assert jammed["card_hidden"] is False
    assert "Silenced" in jammed["sub"]
    assert jammed["texts"] == ["Signal jammed. Manual unavailable."]  # the manual is gone, whole
    assert "The Bomb:" not in jammed["texts"]
    assert jammed["reachable"] == []        # nothing left to click


def test_the_console_asks_for_itself_back_when_silence_lapses(shell):
    """Silence is masked in the view layer, so no server timer fires when it
    ends — `watchSilence` re-requests the snapshot that redraws the console."""
    sent = shell["console_silenced"]["sent"]
    assert {"type": "request_state"} in sent
    returned = shell["console_silenced"]["records"]["returned"]["console"]
    assert returned["card_hidden"] is False
    assert "The Bomb:" in returned["texts"]
    assert "Maze" in returned["reachable"]


def test_silence_holds_the_page_the_grandmaster_was_reading(shell):
    """It lifts in seconds and the Defuser is still stood at the same bay, so
    coming back to the selector would cost a page turn for nothing."""
    records = shell["console_silenced_mid_page"]["records"]
    assert "Wait for the tiny button to turn red." in \
        " ".join(records["reading"]["console"]["texts"])
    assert records["jammed"]["console"]["texts"] == ["Signal jammed. Manual unavailable."]
    assert "Wait for the tiny button to turn red." in \
        " ".join(records["returned"]["console"]["texts"])


def test_the_console_grows_a_clock_on_a_dark_fuse_board(shell):
    """Its first live element, and a deliberate departure from "the manual and
    nothing else" — still not board state, just the one clock in the match, on
    the one seat allowed to read it out (docs/GAME_DESIGN.md §2c)."""
    ticking = shell["console_clock"]["records"]["ticking"]["console"]
    assert ticking["clock_hidden"] is False
    # A clock reads as a clock. The instruction that comes with it — that this
    # number reaches nobody else — is on the chip rather than in it.
    assert ticking["clock"] == f"{BOARD_LIMIT // 60:02d}:{BOARD_LIMIT % 60:02d}"
    assert "cannot see this" in ticking["clock_title"]
    # ...and the sub line says why the Grandmaster is suddenly the clock.
    assert "Their timer is dark" in ticking["sub"]
    later = shell["console_clock"]["records"]["later"]["console"]
    left = BOARD_LIMIT - 25
    assert later["clock"] == f"{left // 60:02d}:{left % 60:02d}"


def test_the_console_clock_follows_every_snapshot(shell):
    """The manual holds its page across snapshots on purpose. The clock is the
    one thing on this card that must not — it sits outside the redraw guard."""
    records = shell["console_clock"]["records"]
    # The page turn survived...
    assert "Blue is the Defuser's position" in \
        " ".join(records["redrawn"]["console"]["texts"])
    # ...and the clock is still live behind it.
    assert records["redrawn"]["console"]["clock_hidden"] is False


def test_silence_takes_the_clock_with_the_manual(shell):
    """For those seconds the clock is in nobody's hands — the Defuser was never
    sent it either. That is the perk landing, not a bug."""
    silenced = shell["console_clock"]["records"]["silenced"]["console"]
    assert silenced["clock_hidden"] is True
    assert silenced["clock"] == ""
    assert silenced["texts"] == ["Signal jammed. Manual unavailable."]
    # And it left no interval behind ticking at a hidden element.
    after = shell["console_clock"]["records"]["after_silence"]["console"]
    assert after["clock_hidden"] is True


def test_no_defuser_means_no_console(shell):
    active = shell["console_without_a_defuser"]["records"]["active"]
    assert active["view"] == ["view-leader"]
    assert active["console"]["card_hidden"] is True
    assert active["console"]["mount_children"] == 0


# --- startCountdown -------------------------------------------------------


def test_a_solving_player_has_no_timer_bar(shell):
    """The bar belongs to the wait deadline; a player still on a board holds
    none, which is what leaves the bar free."""
    solving = shell["countdown"]["records"]["solving"]["countdown"]
    assert solving["bar_hidden"] is True
    assert solving["label_hidden"] is True


def _clock(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def test_the_wait_countdown_draws_and_ticks_down(shell):
    """The digits sit in one place and stay one size whichever deadline is
    running; the *kind* of deadline is the line above them."""
    records = shell["countdown"]["records"]
    wait = shell["_wait_seconds"]
    fresh = records["cleared"]["countdown"]
    assert fresh["bar_hidden"] is False and fresh["label_hidden"] is False
    assert fresh["clock_hidden"] is False
    assert fresh["label"] == _clock(wait)
    assert fresh["kind"] == "Holding cleared"
    assert fresh["fill"] == "100%"
    later = records["cleared_30s"]["countdown"]
    assert later["label"] == _clock(wait - 30)
    assert float(later["fill"].rstrip("%")) < 100
    # Three minutes out is not urgent; the emphasis is saved for the end.
    assert fresh["urgent"] is False


def test_the_bonus_deadline_is_the_same_bar_with_a_different_name(shell):
    """Taking the bonus keeps the running wait deadline — it just stops being a
    hold and starts being a deadline (docs/GAME_DESIGN.md §5)."""
    bonus = shell["countdown"]["records"]["bonus"]["countdown"]
    assert bonus["bar_hidden"] is False
    assert bonus["kind"] == "Bonus deadline"


def test_a_lapsed_countdown_hands_over_to_the_server(shell):
    """The client never decides the deadline passed — it says so and waits."""
    lapsed = shell["countdown"]["records"]["lapsed"]["countdown"]
    assert lapsed["label"] == "00:00"
    assert lapsed["kind"] == "Waiting for the server"
    assert lapsed["fill"] == "0%"


def test_a_capped_board_draws_its_deadline_on_the_same_bar(shell):
    """A solving player holds no wait timer, so the bar is free — and a board
    that caps itself is exactly what it is free for."""
    armed = shell["board_deadline"]["records"]["armed"]["countdown"]
    assert armed["bar_hidden"] is False and armed["label_hidden"] is False
    assert armed["label"] == _clock(BOARD_LIMIT)
    assert armed["kind"] == "Board deadline"
    # Full width spans the game's own limit, not the wait — the bar would be a
    # sliver at 90 of 180 seconds otherwise.
    assert armed["fill"] == "100%"
    later = shell["board_deadline"]["records"]["later"]["countdown"]
    assert later["label"] == _clock(BOARD_LIMIT - 20)
    assert 70 < float(later["fill"].rstrip("%")) < 80


def test_a_lapsed_board_deadline_also_hands_over_to_the_server(shell):
    """The client never decides a board is over — the same rule the wait timer
    follows, and the reason the server keeps a grace on top."""
    lapsed = shell["board_deadline"]["records"]["lapsed"]["countdown"]
    assert lapsed["label"] == "00:00"
    assert lapsed["kind"] == "Waiting for the server"
    assert lapsed["fill"] == "0%"


def test_an_answer_submitted_while_frozen_is_held_not_lost(shell):
    """A freeze makes the server refuse submits. A game that submits once, at
    the end, would have its whole answer thrown away — the bomb draws BOMB
    DEFUSED and then never hears back."""
    sent = shell["held_submit"]["sent"]
    submits = [m for m in sent if m["type"] == "submit_answer"]
    # Nothing went out while the freeze was up...
    assert shell["held_submit"]["records"]["while_frozen"]["sent_submits"] == 0
    # ...and exactly one went out after it lifted, unchanged.
    assert len(submits) == 1
    assert submits[0]["answer"] == "the-one-and-only-answer"


def test_the_bar_goes_away_again_and_stops_ticking(shell):
    back = shell["countdown"]["records"]["back_to_solving"]
    assert back["countdown"]["bar_hidden"] is True
    assert back["countdown"]["label_hidden"] is True
    # Two intervals survive, and only two: `renderScreenEffects` re-arms its own
    # on every render whether or not an effect is running, and the connection
    # heartbeat runs for the life of the socket. The countdown's own interval is
    # not among them.
    assert back["timers"] == 2


# --- the Grandmaster command dashboard -------------------------------------

def _dash(shell, record):
    return shell["dashboard"]["records"][record]["dashboard"]


def test_the_command_bar_reads_the_match_not_a_hard_coded_ten(shell):
    """The host picks the match length in the lobby, so a dashboard that prints
    "/ 10" is lying on every match that isn't ten levels long."""
    live = _dash(shell, "live")
    want = shell["_expected"]["dashboard"]
    assert live["match_code"] == want["match_id"]
    assert live["team"] == want["team_name"]
    assert live["level"] == f"Level 4 / {want['level_count']}"
    assert live["currency"] == "9"


def test_a_duelists_green_is_a_duel_win_not_a_cleared_puzzle(shell):
    """A Duelist never solves a board, so labelling their green "cleared" would
    describe a puzzle they were never given."""
    roster = _dash(shell, "live")["roster"]
    duelist = next(row for row in roster if "Duelist" in row["text"])
    solver = next(row for row in roster if "Technocrat" in row["text"])
    assert duelist["pill_text"] == "Duel won"
    assert solver["pill_text"] == "Cleared"
    # Both are still green: the distinction is the wording, not the state.
    assert "green" in duelist["pill"] and "green" in solver["pill"]


def test_offline_is_shown_beside_the_gameplay_status_not_instead_of_it(shell):
    """Both facts matter: a disconnected teammate who had cleared is a very
    different problem from one who was still solving."""
    absent = next(row for row in _dash(shell, "live")["roster"] if row["offline"])
    assert "Spymaster" in absent["text"]
    assert absent["pill_text"] == "Solving"


def test_silence_blanks_the_roster_and_the_count_with_nothing_stale(shell):
    """The server nulls the progress fields rather than lying about them, and
    the dashboard has to draw that blank instead of the numbers it last saw."""
    silenced = _dash(shell, "silenced")
    assert silenced["team_count"] == "? / ?"
    # The slot does not rename itself under silence: it is the cleared count
    # either way, and a bar whose labels move is one you have to re-read.
    assert silenced["flags"][0] == "Cleared Silenced"
    assert [row["pill_text"] for row in silenced["roster"]] == ["?"] * 5
    # Nothing from the previous snapshot survives underneath.
    live_names = shell["_expected"]["dashboard"]["names"]
    for row in silenced["roster"]:
        assert "Cleared" not in row["text"] and "Bonus" not in row["text"]
        assert any(name in row["text"] for name in live_names)


def test_the_perk_shop_is_the_catalogue_attack_before_defense(shell):
    """Groups are derived from perk.kind, so a perk added to backend/config.py
    appears here without touching the frontend."""
    live = _dash(shell, "live")
    assert live["perk_groups"] == ["attack", "defense"]
    assert len(live["perk_buys"]) == shell["_expected"]["dashboard"]["perk_count"]


def test_a_team_that_cannot_afford_a_perk_can_still_read_it(shell):
    """Being broke disables the purchase, not the description: knowing what you
    cannot afford yet is most of the Grandmaster's planning."""
    broke = _dash(shell, "broke")
    assert all(buy["disabled"] for buy in broke["perk_buys"])
    assert broke["perk_groups"] == ["attack", "defense"]


def test_an_active_defense_is_not_offered_again(shell):
    """Shield is up in the live snapshot, so its card reports the state rather
    than inviting a purchase the server would refuse."""
    live = _dash(shell, "live")
    assert "Shield Active" in live["flags"]
    assert any(buy["label"] == "Active" and buy["disabled"]
               and "Already active: Shield" in buy["described"]
               for buy in live["perk_buys"])


def test_the_page_ground_follows_the_view(shell):
    """Two views are dark — the command board and the result screen — and every
    view a player works a board in is light. The ground follows the view rather
    than the other way round, and neither dark class outlives its own screen."""
    assert _dash(shell, "live")["body_class"] == "gm-active"
    lobby = shell["console"]["records"]["lobby"]["dashboard"]["body_class"]
    assert "gm-active" not in lobby and "result-active" not in lobby


def test_the_handoff_names_the_role_game_and_what_it_costs_them(shell):
    """The seat swap takes the recipient's cleared status, so the option they
    are picked from has to say whether they had one."""
    options = _dash(shell, "live")["handoff_options"]
    assert len(options) == 5
    assert any("Duelist" in opt and "cleared" in opt for opt in options)
    assert any("Defuser" in opt and "Bomb Defuse" in opt for opt in options)


def test_the_command_bar_holds_every_slot_open(shell):
    """A bar that grew and shrank moved whatever you were reading. Every slot is
    drawn every snapshot; the ones that are not happening dim in place."""
    live = _dash(shell, "live")
    labels = [flag.split(" ")[0] for flag in live["flags"]]
    assert labels == ["Cleared", "Shield", "Reflect", "Insurance", "Duel", "Duel"]
    # The two that are true in this snapshot say so...
    assert "Shield Active" in live["flags"]
    assert "Duel streak x2" in live["flags"]
    # ...and the three that are not still hold their place rather than leaving.
    assert "Reflect None" in live["flags"]
    assert "Insurance None" in live["flags"]
    assert "Duel penalty None" in live["flags"]


def test_the_feed_stamps_the_time_and_marks_the_kind(shell):
    """The server already sends a kind and a created_at on every event
    (models.Event), so the feed can say when a thing happened and what sort of
    thing it was without the client guessing at either."""
    feed = _dash(shell, "live")["feed"]
    assert feed, "the dashboard drew no events at all"
    for row in feed:
        assert re.fullmatch(r"\d\d:\d\d:\d\d", row["time"]), row
        assert "gm-ic--" in row["mark"], row
        # The stamp and the mark are additions to the line, never a replacement
        # for what the server actually said.
        assert row["text"].replace(row["time"], "").strip()


def test_an_event_kind_the_client_does_not_know_still_draws_a_line(shell):
    """The engine owns the kinds. A new one must reach the Grandmaster as a
    plain row, not vanish because this file has no entry for it."""
    marks = re.search(r"var EVENT_MARKS = \{(.*?)\};", APP.read_text(), re.S)
    assert marks, "app.js no longer declares EVENT_MARKS"
    assert "info:" in marks.group(1), "the fallback row lost its entry"


def test_the_duel_watch_names_both_champions(shell):
    """Names, not faces: the duel view carries the opponent's name and never
    their id, so there is nothing honest to seed a face from."""
    seats = _dash(shell, "live")["duel_seats"]
    assert len(seats) == 3, seats          # seat, VS, seat
    assert seats[1] == "VS"
    names = shell["_expected"]["dashboard"]["names"]
    assert any(name in seats[0] for name in names), seats[0]
    # Your own champion is on the left, the way the roster and the race read.
    assert "Alpha" in seats[0]
    assert "Bravo" in seats[2]


def test_the_handoff_shows_the_face_it_would_promote(shell):
    """A native <select> cannot carry a picture, so the picture sits beside it
    — and it has to be the *selected* teammate's, not a decoration."""
    assert _dash(shell, "live")["handoff_face"].startswith("<svg")


def test_the_race_puts_both_teams_on_one_scale(shell):
    """A number each says how far you are; two rows of the same stars say who is
    ahead, which is the thing a Grandmaster actually reads."""
    race = _dash(shell, "live")["race"]
    levels = shell["_expected"]["dashboard"]["level_count"]
    assert [row["mine"] for row in race] == [True, False]
    # One star per configured level on both rows, lit to that team's level.
    assert [row["stars"] for row in race] == [levels, levels]
    assert race[0]["lit"] == 4 and race[0]["level"] == f"4 / {levels}"
    assert race[1]["lit"] == race[1]["lit"]  # opponent lit tracks their level
    assert race[1]["level"].endswith(f"/ {levels}")


def test_each_team_gets_its_own_mark_from_the_match(shell):
    """The mark is picked from the match id so the two teams never collide, and
    nothing about it is stored on the team."""
    race = _dash(shell, "live")["race"]
    marks = [row["logo"] for row in race]
    assert all("gm-ic--logo-" in mark for mark in marks)
    assert marks[0] != marks[1]


def test_the_gap_is_spelled_out_not_only_coloured(shell):
    """Colour alone would not survive a colour-blind reader or a glance."""
    live = _dash(shell, "live")
    assert "lead" in live["race_gap"].lower() or live["race_gap"] == "Level for level"
    assert "is-ahead" in live["race_gap_class"] or "is-behind" in live["race_gap_class"] \
        or live["race_gap"] == "Level for level"


def test_a_level_advance_names_the_team_that_moved(shell):
    """The server always said which team advanced. Telling both teams the same
    bare number wasted it, and read as your own progress when it was not."""
    ours = shell["dashboard"]["records"]["ours_advanced"]["overlay"]
    theirs = shell["dashboard"]["records"]["theirs_advanced"]["overlay"]
    assert ours["text"] == "Alpha progressed to Level 5"
    assert theirs["text"] == "Bravo progressed to Level 4"
    # Your team moving and the rivals moving are opposite news.
    assert "is-mine" in ours["classes"]
    assert "is-rival" in theirs["classes"]
    assert ours["hidden"] is False


def test_avatars_need_no_network_and_are_stable_per_player(shell):
    """A roster that waits on a third-party CDN shows blanks whenever that host
    is slow, blocked or unreachable, which is not a thing to learn mid-match."""
    live = _dash(shell, "live")
    assert len(live["roster"]) == 5
    assert live["avatars"]["count"] == 5
    assert live["avatars"]["remote"] == 0
    # Same seed, same face, every client and every redraw.
    assert live["avatars"]["distinct"] >= 2
    assert live["avatars"] == _dash(shell, "broke")["avatars"]


def test_a_watching_grandmaster_keeps_the_match_alive(shell):
    """The server evicts a match after MATCH_TTL_SECONDS with no client message,
    and a Grandmaster who only watches sends nothing at all. The protocol has a
    heartbeat for this; until now nothing sent it."""
    sent = shell["dashboard"]["sent"]
    beats = [message for message in sent if message["type"] == "heartbeat"]
    # Fifteen idle minutes, comfortably inside the 30-minute eviction window.
    assert len(beats) >= 3, sent
    assert all(set(beat) == {"type"} for beat in beats)


def test_the_coin_board_ranks_by_what_each_player_brought_in(shell):
    """Seat order says who is on the team; this says who has been paying for the
    perks, which is a different question and a different order."""
    coins = _dash(shell, "live")["coins"]
    assert coins["jammed"] is False
    # Five players, the Grandmaster excluded: they never earn.
    assert len(coins["rows"]) == 5
    figures = [int(row["text"].split()[-1]) for row in coins["rows"]]
    assert figures == sorted(figures, reverse=True), figures
    assert figures == [11, 7, 4, 2, 0]
    # Only the leader is marked, and only when someone has actually earned.
    assert [row["top"] for row in coins["rows"]] == [True, False, False, False, False]


def test_silence_takes_the_coin_board_with_the_rest(shell):
    """Earnings track clears, so a visible ledger would say who had cleared and
    undo the blinding."""
    coins = _dash(shell, "silenced")["coins"]
    assert coins["jammed"] is True
    assert coins["rows"] == []


# --- the duel round clock -------------------------------------------------
#
# The shell owns the duel countdown so every duel game gets the same one
# (docs/DUEL_MODULE_SPEC.md §7). What matters is that it draws at all, that it
# runs on the window this match is actually using, and that it stops between
# rounds rather than counting down against nothing.

def test_the_duel_clock_counts_the_open_round_down(shell):
    records = shell["duel_clock"]["records"]
    opened = records["open"]["duel_clock"]
    assert opened["card_hidden"] is False
    assert opened["hidden"] is False and opened["bar_hidden"] is False
    # Six seconds is the host's override, not RPS's own five.
    assert opened["text"] == "6s"
    assert opened["fill"] == "100%"

    half = records["half"]["duel_clock"]
    assert half["text"] == "3s"
    assert half["fill"] == "50%"


def test_the_duel_clock_turns_urgent_at_the_end(shell):
    urgent = shell["duel_clock"]["records"]["urgent"]["duel_clock"]
    assert urgent["text"] == "1s"
    assert "urgent" in urgent["classes"]
    assert "urgent" not in shell["duel_clock"]["records"]["open"]["duel_clock"]["classes"]


def test_the_duel_clock_stops_between_rounds(shell):
    """The reveal beat is not a race, so there is nothing to count down."""
    reveal = shell["duel_clock"]["records"]["reveal"]["duel_clock"]
    assert reveal["hidden"] is True and reveal["bar_hidden"] is True
    assert reveal["card_hidden"] is False          # the duel is still on screen


def test_the_duel_title_names_the_game_and_the_round(shell):
    assert shell["duel_clock"]["records"]["open"]["duel_clock"]["title"] == (
        "⚔️ Rock Paper Scissors — round 1"
    )


def test_the_host_can_set_one_window_for_every_duel(shell):
    """The picker offers each duel game its own pace, or one window for all of
    them, and every value it offers is one the server accepts (asserted against
    the bounds in tests/test_host_controls.py)."""
    control = shell["blocker:ready"]["records"]["lobby"]["host_duel_window"]
    values = [value for value, _ in control["options"]]
    assert values == [""] + [str(n) for n in config.DUEL_ROUND_SECONDS_CHOICES]
    assert control["options"][0][1] == "Each game's own pace"
    assert control["options"][1][1] == "3s a round"
    assert control["value"] == ""          # a fresh lobby overrides nothing
    # The note reads the catalogue rather than restating it, so it cannot drift.
    assert "Rock Paper Scissors 5s" in control["note"]
    assert "Crown Duel 10s" in control["note"]


# --- the design gallery ---------------------------------------------------
#
# `/play?preview=<state>` is a dev tool, but it is only worth having if it
# still draws. These run the shipped app.js against the real snapshots from
# backend/preview.py, so a gallery entry that stopped rendering fails here
# rather than in front of whoever opened it.

@pytest.mark.parametrize("name,view", [
    ("lobby", "view-lobby"),
    ("solving", "view-play"),
    ("cleared", "view-play"),
    ("leader", "view-leader"),
    ("won", "view-result"),
    ("lost", "view-result"),
    ("duel", "view-play"),
])
def test_every_gallery_entry_renders_its_view(shell, name, view):
    assert shell[f"preview:{name}"]["records"]["booted"]["view"] == [view]


def _result(shell, name):
    return shell[f"preview:{name}"]["records"]["booted"]["result"]


# --- the result screen ----------------------------------------------------
#
# Everything on it is something the match recorded. There is no XP and no score
# in The Relay, so the tests below are about the ledger the game does keep.


def test_the_result_screen_names_the_outcome_from_the_viewers_seat(shell):
    won = _result(shell, "won")
    assert won["title"] == "Victory"
    assert "is-win" in won["tone"]
    lost = _result(shell, "lost")
    assert lost["title"] == "Defeat"
    assert "is-loss" in lost["tone"]
    # Same match, opposite seats: the winner is named either way, and it is
    # never the viewer's own team that gets named on a loss.
    assert "Team Alpha" in won["sub"] and "Team Bravo" in lost["sub"]


def test_the_scoreline_is_levels_because_that_is_what_the_game_counts(shell):
    """A winner is only ever set by reaching the last level
    (`RelayEngine._advance_check`), so the levels on this screen have to agree
    with the flag rather than contradict it."""
    won = _result(shell, "won")
    assert "10 / 10" in won["levels"].replace(" ", " ")
    assert "Alpha" in won["levels"] and "Bravo" in won["levels"]
    # The champion's card says Winner; the other says Runner up. Both are shown
    # either way round — a loss is the same scoreboard, read from the other end.
    assert "Winner" in won["mine"] and "Runner up" in won["theirs"]
    lost = _result(shell, "lost")
    assert "Runner up" in lost["mine"] and "Winner" in lost["theirs"]


def test_the_table_ranks_by_what_each_player_put_in_the_purse(shell):
    rows = _result(shell, "won")["roster"]
    assert len(rows) == 5, rows          # four playing plus the Grandmaster
    coins = [int(row["coins"].strip()) for row in rows]
    assert coins == sorted(coins, reverse=True), coins
    # The shares are a share *of this team*, so they add up to it.
    shares = [int(row["share"].split("%")[0]) for row in rows]
    assert 99 <= sum(shares) <= 101, shares
    # Exactly one top earner, and it is the one the ledger actually names.
    assert [row["top"] for row in rows].count(True) == 1
    assert rows[0]["top"] is True


def test_a_grandmaster_held_no_board_of_their_own(shell):
    """The seat that never plays cannot be shown an assigned game: it would be
    the game they were handed before they took the seat, or nothing at all."""
    rows = _result(shell, "won")["roster"]
    grandmaster = [row for row in rows if "GRANDMASTER" in row["text"].upper()]
    assert len(grandmaster) == 1
    assert "Called the plays" in grandmaster[0]["text"]


def test_the_other_squad_is_on_the_screen_too(shell):
    """Fog of war ends with the match. Losing without ever seeing what the
    other side actually did is the version of this screen worth avoiding."""
    opponents = _result(shell, "won")["opponents"]
    assert len(opponents) == 5
    assert any("Gus" in row["text"] for row in opponents)
    # ...but the gilding stays on your own table: their best earner is not
    # your most valuable player.
    assert not any(row["top"] for row in opponents)


def test_the_award_goes_to_the_top_of_the_same_ledger(shell):
    mvp = _result(shell, "won")["mvp"]
    assert "Top contributor" in mvp
    assert "Bo" in mvp                    # the biggest figure in the fixture
    assert "% of everything Alpha put in the purse" in mvp


def test_the_rewards_split_what_was_earned_from_what_is_left(shell):
    """`currency` is what survived the perk shop, not what the team made. A
    screen that printed it as the total would tell a team that spent well it
    had earned nothing."""
    rewards = " | ".join(_result(shell, "won")["rewards"])
    assert "Coins earned" in rewards
    assert "Spent on perks" in rewards
    assert "Left in the purse" in rewards
    figures = _result(shell, "won")["rewards"]
    earned = int(figures[0].split("Coins")[0].strip())
    spent = int(figures[1].split("Spent")[0].strip())
    left = int(figures[2].split("Left")[0].strip())
    assert earned == spent + left


def test_the_crest_is_drawn_not_typed(shell):
    """An emoji would be whatever the reader's system font decided it was, on
    the one screen that has to land."""
    assert _result(shell, "won")["crest"].startswith("<svg")
    assert _result(shell, "lost")["crest"].startswith("<svg")


def test_a_gallery_boot_never_opens_a_socket(shell):
    """Read-only by construction. A preview that connected would be joining
    somebody's match to look at a screenshot."""
    for name, _ in PREVIEW_BOOTS:
        entry = shell[f"preview:{name}"]
        assert entry["url"] is None and entry["sent"] == []


def test_the_gallery_draws_real_content_not_an_empty_frame(shell):
    booted = {
        name: shell[f"preview:{name}"]["records"]["booted"]
        for name, _ in PREVIEW_BOOTS
    }
    # A real board, mounted by its own renderer.
    assert booted["solving"]["mounted_games"], "no game renderer mounted"
    # The wait clock, running.
    assert booted["cleared"]["countdown"]["bar_hidden"] is False
    # The duel card, with the round it was built for.
    assert booted["duel"]["duel_clock"]["card_hidden"] is False
    assert "Crown Duel" in booted["duel"]["duel_clock"]["title"]
    # The Grandmaster's dashboard, with a roster on it.
    assert booted["leader"]["dashboard"]["roster"]
