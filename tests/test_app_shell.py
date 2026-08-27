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
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend import config
from backend.engine import RelayEngine
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

  const context = {
    console,
    JSON, Math, Date: { now: () => clock.now, parse: Date.parse },
    URLSearchParams,
    setTimeout: (fn, ms) => schedule(fn, ms, null),
    clearTimeout: cancel,
    setInterval: (fn, ms) => schedule(fn, ms, ms),
    clearInterval: cancel,
    WebSocket: FakeSocket,
    fetch: (path) => settled({
      ok: true,
      json: () => settled(path === "/api/config" ? scenario.config : {}),
    }),
    sessionStorage: {
      getItem: (key) => (key in storage ? storage[key] : null),
      setItem: (key, value) => { storage[key] = String(value); },
      removeItem: (key) => { delete storage[key]; },
    },
    navigator: {},
    document: {
      createElement: element,
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
      search: "",
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
  context.window.RelayGames = {
    fallback: {
      mount(host, puzzle) { mounts.push(puzzle.game_id); host.appendChild(element("div")); },
      unmount() {},
    },
  };
  context.window.RelayDuels = { fallback: { mount() {}, update() {}, unmount() {} } };
  vm.runInContext(APP_SRC, context);

  return { byId, sockets, windowListeners, mounts, document };
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
    console: {
      card_hidden: $("leader-bomb-card").hidden,
      sub: $("leader-bomb-sub").textContent,
      mount_children: mount.children.length,
      stamped: !!(mount.children[0] && mount.children[0].attrs["data-stamp"]),
      texts: descendants(mount).map((n) => n.textContent).filter(Boolean),
      reachable: reachable(mount),
      resize_listeners: (shell.windowListeners.resize || []).length,
    },
    countdown: {
      bar_hidden: $("timer-bar").hidden,
      label_hidden: $("timer-label").hidden,
      label: $("timer-label").textContent,
      fill: $("timer-fill").style.width,
    },
    timers: clock.timers.size,
    mounted_games: shell.mounts.slice(),
  };
}

// --- run the plan --------------------------------------------------------

const report = {};
PLAN.scenarios.forEach((scenario) => {
  const shell = boot(scenario);
  const socket = shell.sockets[0];
  if (!socket) throw new Error(scenario.name + ": the shell never opened a socket");
  socket.onopen({});
  const records = {};
  scenario.actions.forEach((action) => {
    if (action.do === "deliver") {
      socket.onmessage({
        data: JSON.stringify({
          type: "state_snapshot", state: scenario.snapshots[action.snapshot],
        }),
      });
    } else if (action.do === "click") {
      fire(byText(shell.byId[action.in], action.text), "click");
    } else if (action.do === "advance") {
      advance(action.ms);
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
  report[scenario.name] = { records: records, url: socket.url, sent: socket.sent };
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
        "players_per_team": config.PLAYERS_PER_TEAM,
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
    assert "🔇" in jammed["sub"] and "jammed" in jammed["sub"]
    assert jammed["texts"] == ["🔇 ?"]      # the manual is gone, whole
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
    assert records["jammed"]["console"]["texts"] == ["🔇 ?"]
    assert "Wait for the tiny button to turn red." in \
        " ".join(records["returned"]["console"]["texts"])


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


def test_the_wait_countdown_draws_and_ticks_down(shell):
    records = shell["countdown"]["records"]
    wait = shell["_wait_seconds"]
    fresh = records["cleared"]["countdown"]
    assert fresh["bar_hidden"] is False and fresh["label_hidden"] is False
    assert fresh["label"] == f"⏳ Holding cleared: {wait}s"
    assert fresh["fill"] == "100%"
    later = records["cleared_30s"]["countdown"]
    assert later["label"] == f"⏳ Holding cleared: {wait - 30}s"
    assert float(later["fill"].rstrip("%")) < 100


def test_the_bonus_deadline_is_the_same_bar_with_a_different_name(shell):
    """Taking the bonus keeps the running wait deadline — it just stops being a
    hold and starts being a deadline (docs/GAME_DESIGN.md §5)."""
    bonus = shell["countdown"]["records"]["bonus"]["countdown"]
    assert bonus["bar_hidden"] is False
    assert bonus["label"].startswith("🔥 Bonus deadline: ")


def test_a_lapsed_countdown_hands_over_to_the_server(shell):
    """The client never decides the deadline passed — it says so and waits."""
    lapsed = shell["countdown"]["records"]["lapsed"]["countdown"]
    assert lapsed["label"] == "⏳ Time's up — waiting for the server…"
    assert lapsed["fill"] == "0%"


def test_the_bar_goes_away_again_and_stops_ticking(shell):
    back = shell["countdown"]["records"]["back_to_solving"]
    assert back["countdown"]["bar_hidden"] is True
    assert back["countdown"]["label_hidden"] is True
    # One interval survives, and only one: `renderScreenEffects` re-arms its
    # heartbeat on every render (app.js) whether or not an effect is running.
    # The countdown's own interval is not among them.
    assert back["timers"] == 1
