// BOMB DEFUSE renderer — a bomb face, four kinds of puzzle bay, and the manual
// that explains them.
//
// The source game seats two people: one sees the bomb, the other sees the
// manual, and neither sees the other's screen. The Relay has one seat, so the
// manual is a second full-screen view of the same board — flipping to it hides
// the bomb while the fuse keeps burning. Modules open the same way: the face is
// a dashboard of bays, and working one means opening it over the face. You can
// only ever look at one thing at a time, which is what the second player used
// to cost you.
//
// Sudden death is enforced here, where the player makes the mistake: every
// action goes through `validate` — the same walk backend/games/game11_bomb_
// defuse.py runs — in `partial` mode, and anything it refuses detonates the
// bomb. The transcript of a *successful* defusal is what gets submitted;
// the server replays it and is the only authority on success. A detonation
// submits {"v":1,"failed":reason} instead, which the server rejects and the
// engine answers with a brand-new bomb.
(function () {
  "use strict";

  // --- shared data (mirror of backend/games/game11_bomb_defuse.py, locked to
  // it by tests/games/fixtures/bomb_defuse_cases.json) ---------------------

  var STEPS = { n: [-1, 0], s: [1, 0], e: [0, 1], w: [0, -1] };
  var MAZE_SIZE = 4;
  var MAX_MOVES = 200;
  var RULES_VERSION = 1;

  //   h[r][c] — wall between (r, c) and (r + 1, c);  v[r][c] — between (r, c) and (r, c + 1)
  var MAZE_LAYOUTS = [
    { tip: [0, 1], h: [[0,1,0,0],[0,0,1,0],[1,1,0,0]], v: [[1,0,0],[1,0,1],[0,1,1],[0,0,0]] },
    { tip: [0, 3], h: [[1,0,1,0],[0,1,1,0],[0,1,0,1]], v: [[0,1,0],[1,0,0],[0,1,0],[0,0,0]] },
    { tip: [1, 0], h: [[0,1,0,0],[1,1,1,0],[0,0,1,0]], v: [[1,0,0],[0,0,1],[0,0,1],[1,0,0]] },
    { tip: [1, 2], h: [[0,0,0,1],[0,1,1,0],[0,0,1,0]], v: [[1,0,0],[1,1,0],[1,0,0],[0,1,0]] },
    { tip: [2, 1], h: [[1,1,0,0],[0,0,1,0],[0,1,0,0]], v: [[0,0,1],[1,0,1],[0,1,0],[0,0,1]] },
    { tip: [2, 3], h: [[1,0,0,1],[0,0,1,0],[0,1,0,0]], v: [[0,1,0],[1,1,0],[1,0,1],[0,0,0]] },
    { tip: [3, 0], h: [[1,1,0,0],[0,1,1,0],[0,0,0,0]], v: [[0,0,1],[0,1,0],[1,0,1],[0,1,0]] },
    { tip: [3, 2], h: [[1,0,1,0],[0,1,1,0],[0,0,0,1]], v: [[0,1,0],[1,0,0],[0,1,0],[1,0,0]] }
  ];

  var SIMON_COLOURS = ["red", "blue", "green", "yellow"];
  var SIMON_MAP = { red: "blue", blue: "red", green: "yellow", yellow: "green" };

  var NUMBER_PATTERNS = [
    [[1,6,3],[8,2,4],[5,9,7]],
    [[5,7,9],[2,4,3],[6,8,1]],
    [[2,7,5],[4,3,6],[8,1,9]],
    [[5,3,9],[1,7,2],[8,6,4]],
    [[6,3,2],[8,5,4],[1,7,9]],
    [[8,2,4],[3,1,7],[6,9,5]],
    [[3,7,6],[4,8,1],[2,5,9]],
    [[6,1,4],[2,9,7],[3,8,5]]
  ];

  // §26 palette, kept as one table so a screenshot pass tunes it in one place.
  var C = {
    manualBg: "#fff5bc", black: "#000000", white: "#ffffff",
    panelGrey: "#cbcbcb", bombGreyDark: "#373737", bombGrey: "#666666",
    bombGreyLight: "#aeaeae", manualExitBlue: "#0e00a9", bgBlue: "#001777",
    shutter: "#ff6600", shutterDark: "#c95000", green: "#00ff02",
    cyan: "#00d4ff", red: "#ff0000"
  };

  var SIMON_PAINT = {
    red: ["#8b0000", "#ff3b30"], blue: ["#00308b", "#3b82ff"],
    green: ["#0a6b12", "#31d94a"], yellow: ["#8b7500", "#ffdd33"]
  };

  // Colour is never the only carrier of meaning: each pad wears a shape and its
  // name too, so the mapping is readable without seeing hue.
  var SIMON_SHAPE = { red: "▲", blue: "●", green: "■", yellow: "◆" };

  // The 590x440 logical surface (§23). Everything below is in these units and
  // the whole surface is scaled uniformly to whatever container it lands in.
  var W = 590, H = 440;
  var HOUSE = { x: 12, y: 12, w: 566, h: 376 };
  var CELL = { w: 176, h: 113 };
  var COL = [22, 206, 390];
  var ROW = [22, 143, 264];
  // Bay index -> face cell. Timer takes (0,1), OK the centre, Give Up (2,2).
  var BAY_CELLS = [[0,0],[0,2],[1,0],[1,2],[2,0],[2,1]];

  var MODULE_NAMES = {
    maze: "Maze", simon: "Simon Says",
    according_to_number: "According to number", mini_button: "The mini button"
  };

  // Why the bomb went off, in the player's words.
  var REASONS = {
    maze_wall: "You walked into a wall.",
    simon_wrong: "Wrong colour.",
    atn_wrong: "Wrong button.",
    mini_code: "The mini button was not held.",
    premature_ok: "You pressed OK with a bay still open.",
    "mini-early": "You pressed the mini button before it turned red.",
    "mini-slow": "You were too slow off the red.",
    "mini-release": "You let go before it turned green.",
    "timer-expired": "The fuse ran out.",
    "give-up": "You gave up."
  };

  // --- validation (mirror of the server's replay) --------------------------

  function wallBetween(layout, cell, side) {
    var step = STEPS[side];
    var nr = cell[0] + step[0], nc = cell[1] + step[1];
    if (nr < 0 || nr >= MAZE_SIZE || nc < 0 || nc >= MAZE_SIZE) return true;
    if (side === "n") return !!layout.h[nr][cell[1]];
    if (side === "s") return !!layout.h[cell[0]][cell[1]];
    if (side === "e") return !!layout.v[cell[0]][cell[1]];
    return !!layout.v[cell[0]][nc];
  }

  function layoutForTip(tip) {
    for (var i = 0; i < MAZE_LAYOUTS.length; i++) {
      if (MAZE_LAYOUTS[i].tip[0] === tip[0] && MAZE_LAYOUTS[i].tip[1] === tip[1]) {
        return MAZE_LAYOUTS[i];
      }
    }
    return null;
  }

  function patternForTip(tip) {
    for (var i = 0; i < NUMBER_PATTERNS.length; i++) {
      if (NUMBER_PATTERNS[i][tip[0]][tip[1]] === 1) return NUMBER_PATTERNS[i];
    }
    return null;
  }

  function numberAnswer(pattern, shown, axis) {
    for (var r = 0; r < 3; r++) {
      for (var c = 0; c < 3; c++) {
        if (pattern[r][c] === shown) return (axis === "column" ? c : r) + 1;
      }
    }
    return null;
  }

  function isNumber(value) {
    return typeof value === "number" && isFinite(value);
  }

  function initialState(payload) {
    var state = {};
    payload.modules.forEach(function (module) {
      if (module.type === "maze") {
        state[module.id] = { solved: false, cell: [module.player[0], module.player[1]] };
      } else if (module.type === "simon") {
        state[module.id] = { solved: false, stage: 0, in_stage: 0 };
      } else if (module.type === "according_to_number") {
        state[module.id] = { solved: false, stage: 0 };
      } else {
        state[module.id] = { solved: false };
      }
    });
    return state;
  }

  function allSolved(state) {
    for (var id in state) {
      if (Object.prototype.hasOwnProperty.call(state, id) && !state[id].solved) return false;
    }
    return true;
  }

  // `partial` drops the two end-of-board rules (OK was pressed, every bay shut)
  // so a half-defused bomb can be asked the same question after every action.
  function validate(payload, moves, partial) {
    var state = initialState(payload);
    var byId = {};
    payload.modules.forEach(function (module) { byId[module.id] = module; });

    function report(ok, reason, defused) {
      return { ok: ok, reason: reason, defused: !!defused, state: state };
    }

    if (!Array.isArray(moves)) return report(false, "bad_shape");
    if (moves.length > MAX_MOVES) return report(false, "too_many_moves");

    var defused = false;
    for (var i = 0; i < moves.length; i++) {
      var move = moves[i];
      if (defused) return report(false, "after_ok");
      if (typeof move !== "object" || move === null || Array.isArray(move)) {
        return report(false, "bad_shape");
      }
      var moduleId = move.m;
      if (typeof moduleId !== "string") return report(false, "bad_shape");

      if (moduleId === "ok") {
        if (!allSolved(state)) return report(false, "premature_ok");
        defused = true;
        continue;
      }

      var module = byId[moduleId];
      if (!module) return report(false, "unknown_module");
      var entry = state[moduleId];
      if (entry.solved) return report(false, "already_solved");
      var action = move.a;

      if (module.type === "maze") {
        if (typeof action !== "string" ||
            !Object.prototype.hasOwnProperty.call(STEPS, action)) {
          return report(false, "bad_action");
        }
        var layout = layoutForTip(module.tip);
        if (!layout) return report(false, "bad_shape");
        if (wallBetween(layout, entry.cell, action)) return report(false, "maze_wall");
        entry.cell = [entry.cell[0] + STEPS[action][0], entry.cell[1] + STEPS[action][1]];
        if (entry.cell[0] === module.goal[0] && entry.cell[1] === module.goal[1]) {
          entry.solved = true;
        }
      } else if (module.type === "simon") {
        if (typeof action !== "string") return report(false, "bad_action");
        if (entry.in_stage >= module.sequence.length) return report(false, "bad_shape");
        if (action !== SIMON_MAP[module.sequence[entry.in_stage]]) {
          return report(false, "simon_wrong");
        }
        entry.in_stage += 1;
        if (entry.in_stage > entry.stage) {
          entry.stage += 1;
          entry.in_stage = 0;
          if (entry.stage === module.stages) entry.solved = true;
        }
      } else if (module.type === "according_to_number") {
        if (!isNumber(action) || (action !== 1 && action !== 2 && action !== 3)) {
          return report(false, "bad_action");
        }
        var pattern = patternForTip(module.tip);
        if (!pattern || entry.stage >= module.displays.length) {
          return report(false, "bad_shape");
        }
        if (action !== numberAnswer(pattern, module.displays[entry.stage], module.axis)) {
          return report(false, "atn_wrong");
        }
        entry.stage += 1;
        if (entry.stage === module.displays.length) entry.solved = true;
      } else {
        if (!isNumber(action)) return report(false, "bad_action");
        if (action !== module.code) return report(false, "mini_code");
        entry.solved = true;
      }
    }

    if (partial) return report(true, "", defused);
    if (!defused) return report(false, "missing_ok");
    return report(true, "", true);
  }

  // --- audio (§74) ---------------------------------------------------------
  // Synthesised, so the game ships no assets and borrows no one's sounds. The
  // context is built on the first gesture because browsers refuse it earlier.

  var audio = { ctx: null, muted: false };

  function audioCtx() {
    if (audio.ctx) return audio.ctx;
    var Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) return null;
    try { audio.ctx = new Ctor(); } catch (e) { audio.ctx = null; }
    return audio.ctx;
  }

  function wake() {
    var ctx = audioCtx();
    if (ctx && ctx.state === "suspended" && ctx.resume) ctx.resume();
  }

  function tone(freq, seconds, type, gain, delay) {
    if (audio.muted) return;
    var ctx = audioCtx();
    if (!ctx) return;
    var at = ctx.currentTime + (delay || 0);
    var osc = ctx.createOscillator();
    var amp = ctx.createGain();
    osc.type = type || "square";
    osc.frequency.setValueAtTime(freq, at);
    amp.gain.setValueAtTime(0.0001, at);
    amp.gain.exponentialRampToValueAtTime(gain || 0.12, at + 0.01);
    amp.gain.exponentialRampToValueAtTime(0.0001, at + seconds);
    osc.connect(amp);
    amp.connect(ctx.destination);
    osc.start(at);
    osc.stop(at + seconds + 0.02);
  }

  function noise(seconds, gain, filterHz) {
    if (audio.muted) return;
    var ctx = audioCtx();
    if (!ctx) return;
    var frames = Math.floor(ctx.sampleRate * seconds);
    var buffer = ctx.createBuffer(1, frames, ctx.sampleRate);
    var data = buffer.getChannelData(0);
    for (var i = 0; i < frames; i++) {
      // Fade the noise out over its own length: an explosion, not a hiss.
      data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / frames, 2);
    }
    var source = ctx.createBufferSource();
    source.buffer = buffer;
    var amp = ctx.createGain();
    amp.gain.setValueAtTime(gain, ctx.currentTime);
    var low = ctx.createBiquadFilter();
    low.type = "lowpass";
    low.frequency.setValueAtTime(filterHz, ctx.currentTime);
    source.connect(low);
    low.connect(amp);
    amp.connect(ctx.destination);
    source.start();
  }

  var SOUND = {
    tick: function () { tone(1180, 0.035, "square", 0.05); },
    click: function () { tone(760, 0.045, "square", 0.07); },
    simon: function (colour) {
      tone({ red: 330, blue: 262, green: 392, yellow: 494 }[colour] || 330,
           0.32, "sine", 0.16);
    },
    // §75: the explosion has to overpower everything else on the board.
    boom: function () {
      noise(1.4, 0.9, 900);
      tone(64, 1.0, "sawtooth", 0.5);
      tone(41, 1.6, "sine", 0.45);
    },
    success: function () {
      [523, 659, 784, 1047].forEach(function (freq, index) {
        tone(freq, 0.32, "triangle", 0.16, index * 0.11);
      });
    }
  };

  // --- DOM helpers ---------------------------------------------------------

  var state = null;

  function el(tag, css, text) {
    var node = document.createElement(tag);
    if (css) node.style.cssText = css;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function at(node, x, y, w, h) {
    node.style.cssText += "position:absolute;left:" + x + "px;top:" + y + "px;" +
      "width:" + w + "px;height:" + h + "px;box-sizing:border-box;";
    return node;
  }

  function button(label, css, onClick) {
    var node = el("button", "font-family:Arial,Helvetica,sans-serif;cursor:pointer;" +
      "border-radius:0;padding:0;" + (css || ""), label);
    node.setAttribute("type", "button");
    node.addEventListener("click", function (event) {
      if (event && event.preventDefault) event.preventDefault();
      wake();
      onClick();
    });
    return node;
  }

  function clear(node) {
    if (node) node.innerHTML = "";
  }

  function reduceMotion() {
    try {
      return !!(window.matchMedia &&
                window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    } catch (e) { return false; }
  }

  // --- the bomb face -------------------------------------------------------

  function paintShutter(node) {
    // §32: bright orange, heavy black border, three darker vertical strips.
    node.style.cssText += "background:" + C.shutter + ";border:4px solid " + C.black + ";" +
      "display:flex;align-items:stretch;justify-content:space-around;padding:8px 20px;";
    for (var i = 0; i < 3; i++) {
      node.appendChild(el("div", "width:10px;background:" + C.shutterDark + ";"));
    }
  }

  function bayLabel(module, entry) {
    if (module.type === "simon") return "STAGE " + (entry.stage + 1) + "/" + module.stages;
    if (module.type === "according_to_number") {
      return entry.stage + "/" + module.displays.length + " LOCKED";
    }
    if (module.type === "maze") {
      return "AT R" + (entry.cell[0] + 1) + " C" + (entry.cell[1] + 1);
    }
    return state.mini.phase === "idle" ? "STANDBY" : "ARMED";
  }

  function renderFace() {
    clear(state.faceLayer);
    var payload = state.puzzle.payload;

    var housing = at(el("div"), HOUSE.x, HOUSE.y, HOUSE.w, HOUSE.h);
    housing.style.cssText += "background:" + C.bombGreyDark + ";border:5px solid " + C.black + ";";
    state.faceLayer.appendChild(housing);

    // Timer — top middle, big, bold, red (§7).
    var timerCell = at(el("div"), COL[1], ROW[0], CELL.w, CELL.h);
    timerCell.style.cssText += "background:" + C.black + ";border:4px solid " + C.bombGrey + ";" +
      "display:flex;align-items:center;justify-content:center;";
    state.timerText = el("div",
      "font-family:Arial,Helvetica,sans-serif;font-size:64px;font-weight:700;" +
      "color:" + C.red + ";text-align:center;line-height:1;", String(state.remaining));
    state.timerText.setAttribute("role", "timer");
    timerCell.appendChild(state.timerText);
    state.faceLayer.appendChild(timerCell);

    // The bays: shut ones wear a shutter, live ones a grey panel you can open.
    var byBay = {};
    payload.modules.forEach(function (module) { byBay[module.bay] = module; });
    for (var bay = 0; bay < payload.bays; bay++) {
      var cell = BAY_CELLS[bay];
      var x = COL[cell[1]], y = ROW[cell[0]];
      var module = byBay[bay];
      if (!module || state.result.state[module.id].solved) {
        var shut = at(el("div"), x, y, CELL.w, CELL.h);
        paintShutter(shut);
        state.faceLayer.appendChild(shut);
        continue;
      }
      state.faceLayer.appendChild(makeBayButton(module, x, y));
    }

    // OK — the centre, and the only way to finish (§15, §16).
    var ready = allSolved(state.result.state);
    state.okButton = button("OK",
      "border:5px solid " + C.black + ";background:" + (ready ? C.green : "#00a802") + ";" +
      "color:" + C.black + ";font-size:26px;font-weight:700;border-radius:50%;",
      pressOk);
    at(state.okButton, COL[1] + (CELL.w - 96) / 2, ROW[1] + (CELL.h - 96) / 2, 96, 96);
    state.okButton.setAttribute("aria-label",
      ready ? "OK — every bay is shut, press to defuse" : "OK — bays are still open");
    state.faceLayer.appendChild(state.okButton);

    // Give up — blue, bottom right (§21). No confirmation, by design.
    var giveUp = button("Give up",
      "border:4px solid " + C.black + ";background:#1f6fd0;color:" + C.white + ";" +
      "font-size:15px;font-weight:700;",
      function () { detonate("give-up"); });
    at(giveUp, COL[2] + 18, ROW[2] + 34, 140, 44);
    state.faceLayer.appendChild(giveUp);

    // Below the housing: the manual, and a mute for the ticking.
    var locked = state.mini.phase !== "idle" && state.mini.phase !== "done";
    state.manualButton = button(locked ? "📖  MANUAL (LOCKED)" : "📖  MANUAL",
      "border:4px solid " + C.black + ";background:" + (locked ? C.bombGrey : C.manualBg) + ";" +
      "color:" + (locked ? "#999" : C.black) + ";font-size:16px;font-weight:700;", openManual);
    state.manualButton.disabled = locked;
    at(state.manualButton, 12, 396, 210, 32);
    state.faceLayer.appendChild(state.manualButton);

    var muteButton = button(audio.muted ? "🔇 SOUND OFF" : "🔊 SOUND ON",
      "border:4px solid " + C.black + ";background:" + C.bombGrey + ";color:" + C.white + ";" +
      "font-size:13px;font-weight:700;", function () {
        audio.muted = !audio.muted;
        renderFace();
      });
    at(muteButton, 420, 396, 158, 32);
    state.faceLayer.appendChild(muteButton);

    var status = el("div",
      "position:absolute;left:232px;top:396px;width:180px;height:32px;color:" + C.white + ";" +
      "font-family:Arial,Helvetica,sans-serif;font-size:12px;display:flex;align-items:center;" +
      "justify-content:center;text-align:center;",
      ready ? "ALL BAYS SHUT — PRESS OK" : "TAP A BAY TO WORK IT");
    status.setAttribute("role", "status");
    state.faceLayer.appendChild(status);

    if (ready && !state.pulse && !reduceMotion()) startPulse();
  }

  function makeBayButton(module, x, y) {
    var entry = state.result.state[module.id];
    var open = button("", "border:4px solid " + C.black + ";background:" + C.panelGrey + ";" +
      "color:" + C.black + ";display:flex;flex-direction:column;align-items:center;" +
      "justify-content:center;gap:4px;text-align:center;",
      function () { openModule(module.id); });
    at(open, x, y, CELL.w, CELL.h);
    open.appendChild(el("div", "font-size:14px;font-weight:700;", MODULE_NAMES[module.type]));
    open.appendChild(el("div", "font-size:12px;color:#333;", bayLabel(module, entry)));
    open.appendChild(el("div",
      "font-size:11px;font-weight:700;background:" + C.black + ";color:" + C.shutter + ";" +
      "padding:2px 8px;", "OPEN ▸"));
    open.setAttribute("aria-label", "Open the " + MODULE_NAMES[module.type] + " bay");
    return open;
  }

  function startPulse() {
    // §16: obvious, simple, ~600ms. Nothing more elaborate than that.
    var bright = false;
    state.pulse = window.setInterval(function () {
      bright = !bright;
      if (state.okButton) {
        state.okButton.style.background = bright ? "#7dff7e" : C.green;
      }
    }, 600);
  }

  // --- module panels -------------------------------------------------------

  function panelShell(title, closable) {
    var panel = at(el("div"), HOUSE.x, HOUSE.y, HOUSE.w, HOUSE.h);
    panel.style.cssText += "background:" + C.bombGreyLight + ";border:5px solid " + C.black + ";" +
      "font-family:Arial,Helvetica,sans-serif;pointer-events:auto;";
    var head = at(el("div"), 0, 0, HOUSE.w - 10, 40);
    head.style.cssText += "background:" + C.black + ";color:" + C.white + ";display:flex;" +
      "align-items:center;justify-content:space-between;padding:0 10px;";
    head.appendChild(el("div", "font-size:17px;font-weight:700;", title));
    if (closable) {
      head.appendChild(button("✕ BOMB",
        "border:2px solid " + C.white + ";background:" + C.black + ";color:" + C.white + ";" +
        "font-size:13px;font-weight:700;padding:4px 10px;", closeModule));
    } else {
      head.appendChild(el("div",
        "font-size:12px;font-weight:700;color:" + C.shutter + ";", "ARMED — SEE IT THROUGH"));
    }
    panel.appendChild(head);
    var body = at(el("div"), 0, 40, HOUSE.w - 10, HOUSE.h - 50);
    body.style.cssText += "padding:10px;";
    panel.appendChild(body);
    return { panel: panel, body: body };
  }

  function renderPanel() {
    clear(state.panelLayer);
    if (state.openId === null) return;
    var module = moduleById(state.openId);
    var entry = state.result.state[module.id];
    var closable = module.type !== "mini_button" || state.mini.phase === "idle";
    var shell = panelShell(MODULE_NAMES[module.type], closable);
    if (module.type === "maze") renderMaze(shell.body, module, entry);
    else if (module.type === "simon") renderSimon(shell.body, module, entry);
    else if (module.type === "according_to_number") renderNumber(shell.body, module, entry);
    else renderMini(shell.body, module);
    state.panelLayer.appendChild(shell.panel);
  }

  function moduleById(id) {
    var modules = state.puzzle.payload.modules;
    for (var i = 0; i < modules.length; i++) {
      if (modules[i].id === id) return modules[i];
    }
    return null;
  }

  // MAZE (§36-§43): the bomb shows three markers and no walls. The walls are
  // the manual's business — that is the whole puzzle.
  function renderMaze(body, module, entry) {
    var size = 62;
    var grid = el("div", "position:absolute;left:14px;top:8px;width:" + (size * 4 + 8) +
      "px;height:" + (size * 4 + 8) + "px;background:" + C.black + ";padding:4px;");
    for (var row = 0; row < MAZE_SIZE; row++) {
      for (var col = 0; col < MAZE_SIZE; col++) {
        var fill = C.white, mark = "";
        if (row === module.tip[0] && col === module.tip[1]) { fill = "#17c40a"; mark = "▲"; }
        if (row === module.goal[0] && col === module.goal[1]) { fill = C.red; mark = "◎"; }
        if (row === entry.cell[0] && col === entry.cell[1]) { fill = "#1352ff"; mark = "●"; }
        var cell = el("div", "position:absolute;left:" + (4 + col * size) + "px;top:" +
          (4 + row * size) + "px;width:" + (size - 2) + "px;height:" + (size - 2) + "px;" +
          "background:" + fill + ";border:1px solid #999;display:flex;align-items:center;" +
          "justify-content:center;color:" + C.white + ";font-size:24px;", mark);
        grid.appendChild(cell);
      }
    }
    body.appendChild(grid);

    var padX = 300, padY = 26, key = 74;
    [["↑", "n", 1, 0], ["←", "w", 0, 1], ["↓", "s", 1, 1], ["→", "e", 2, 1]]
      .forEach(function (spec) {
        var arrow = button(spec[0],
          "border:4px solid " + C.black + ";background:" + C.bombGrey + ";color:" + C.white + ";" +
          "font-size:30px;font-weight:700;",
          function () { act(module.id, spec[1]); });
        arrow.setAttribute("aria-label", "Step " +
          { n: "north", s: "south", e: "east", w: "west" }[spec[1]]);
        at(arrow, padX + spec[2] * (key + 6), padY + spec[3] * (key + 6), key, key);
        body.appendChild(arrow);
      });

    body.appendChild(el("div", "position:absolute;left:290px;top:190px;width:250px;" +
      "font-size:12px;color:#222;line-height:1.45;",
      "● blue is you, ◎ red is the way out, green is the tip that names the maze. " +
      "Find that maze in the manual and follow its walls — one step into a wall " +
      "and the bomb goes off."));
  }

  // SIMON SAYS (§44-§50): the sequence flashes here; the manual says what to
  // press back. Replaying a stage costs nothing but the fuse.
  function renderSimon(body, module, entry) {
    body.appendChild(el("div", "position:absolute;left:14px;top:4px;font-size:14px;" +
      "font-weight:700;color:#111;",
      "Stage " + (entry.stage + 1) + " of " + module.stages +
      "  ·  entered " + entry.in_stage + "/" + (entry.stage + 1)));

    var size = 108, gap = 8, left = 40, top = 34;
    state.simon.pads = {};
    SIMON_COLOURS.forEach(function (colour, index) {
      var pad = button("",
        "border:5px solid " + C.black + ";background:" + SIMON_PAINT[colour][0] + ";" +
        "color:" + C.white + ";display:flex;flex-direction:column;align-items:center;" +
        "justify-content:center;gap:2px;",
        function () { act(module.id, colour); });
      pad.appendChild(el("div", "font-size:30px;line-height:1;", SIMON_SHAPE[colour]));
      pad.appendChild(el("div", "font-size:13px;font-weight:700;text-transform:uppercase;",
        colour));
      pad.setAttribute("aria-label", colour);
      at(pad, left + (index % 2) * (size + gap), top + Math.floor(index / 2) * (size + gap),
         size, size);
      state.simon.pads[colour] = pad;
      body.appendChild(pad);
    });

    var play = button(state.simon.playing ? "▶ PLAYING…" : "▶ PLAY THE FLASHES",
      "border:4px solid " + C.black + ";background:" + C.black + ";color:" + C.shutter + ";" +
      "font-size:15px;font-weight:700;", function () { playSimon(module, entry); });
    at(play, 300, 40, 230, 52);
    body.appendChild(play);

    body.appendChild(el("div", "position:absolute;left:300px;top:104px;width:230px;" +
      "font-size:12px;color:#222;line-height:1.45;",
      "Watch the flashes, then press what the manual says to press back — never " +
      "the colour you saw. One wrong pad and the bomb goes off. Each stage adds " +
      "a flash and replays from the beginning."));
  }

  function playSimon(module, entry) {
    if (state.simon.playing || state.status !== "active") return;
    state.simon.playing = true;
    renderPanel();
    var sequence = module.sequence.slice(0, entry.stage + 1);
    var step = module.flash_ms + module.gap_ms;
    sequence.forEach(function (colour, index) {
      state.simon.timers.push(window.setTimeout(function () {
        flashPad(colour, module.flash_ms);
      }, index * step));
    });
    state.simon.timers.push(window.setTimeout(function () {
      state.simon.playing = false;
      if (state.status === "active" && state.openId === module.id) renderPanel();
    }, sequence.length * step + module.input_delay_ms));
  }

  function flashPad(colour, flashMs) {
    SOUND.simon(colour);
    var pad = state.simon.pads[colour];
    if (!pad) return;
    pad.style.background = SIMON_PAINT[colour][1];
    state.simon.timers.push(window.setTimeout(function () {
      if (state.simon.pads[colour] === pad) pad.style.background = SIMON_PAINT[colour][0];
    }, flashMs));
  }

  // ACCORDING TO NUMBER (§58-§67): a cyan display, three buttons, and one
  // progress box per stage. The green dot is which manual grid to read.
  function renderNumber(body, module, entry) {
    var shown = module.displays[Math.min(entry.stage, module.displays.length - 1)];
    var display = at(el("div"), 40, 16, 190, 96);
    display.style.cssText += "background:" + C.black + ";border:5px solid " + C.bombGrey + ";" +
      "color:" + C.cyan + ";font-size:58px;font-weight:700;display:flex;align-items:center;" +
      "justify-content:center;";
    display.textContent = String(shown);
    display.setAttribute("aria-label", "The display reads " + shown);
    body.appendChild(display);

    [1, 2, 3].forEach(function (value, index) {
      var key = button(String(value),
        "border:4px solid " + C.black + ";background:" + C.panelGrey + ";color:" + C.black + ";" +
        "font-size:30px;font-weight:700;", function () { act(module.id, value); });
      at(key, 40 + index * 66, 126, 58, 58);
      body.appendChild(key);
    });

    for (var i = 0; i < module.displays.length; i++) {
      var box = at(el("div"), 250, 16 + i * 30, 26, 26);
      box.style.cssText += "border:3px solid " + C.black + ";background:" +
        (i < entry.stage ? C.green : C.bombGreyLight) + ";";
      body.appendChild(box);
    }

    // The identifying tip: the cell the green 1 sits in, as a 3x3 of dots.
    var tip = el("div", "position:absolute;left:300px;top:14px;width:86px;height:86px;" +
      "background:" + C.black + ";");
    for (var row = 0; row < 3; row++) {
      for (var col = 0; col < 3; col++) {
        var lit = row === module.tip[0] && col === module.tip[1];
        tip.appendChild(el("div", "position:absolute;left:" + (6 + col * 26) + "px;top:" +
          (6 + row * 26) + "px;width:22px;height:22px;background:" +
          (lit ? "#17c40a" : "#1d1d1d") + ";"));
      }
    }
    body.appendChild(tip);
    body.appendChild(el("div", "position:absolute;left:396px;top:14px;width:140px;" +
      "font-size:12px;font-weight:700;color:#111;line-height:1.4;",
      "The lit dot is where this grid's green 1 sits. That names the grid."));
    body.appendChild(el("div", "position:absolute;left:300px;top:112px;width:236px;" +
      "font-size:12px;color:#222;line-height:1.45;",
      "Find the displayed number in that grid and press its " +
      (module.axis === "column" ? "column" : "row") + ": left = 1, middle = 2, " +
      "right = 3. Four in a row and the bay shuts; one wrong and it does not."));
  }

  // MINI BUTTON (§51-§57): the only module with a clock of its own. Arming is
  // a commitment — the panel locks shut until it resolves, so nobody dodges the
  // reaction test by stepping away from it.
  function renderMini(body, module) {
    var phase = state.mini.phase;
    var paint = { idle: C.bombGrey, waiting: C.bombGreyLight, red: C.red,
                  holding: C.red, ready: C.green }[phase];

    body.appendChild(el("div", "position:absolute;left:14px;top:6px;font-size:14px;" +
      "font-weight:700;color:#111;", {
        idle: "STANDBY — arm it when you are ready",
        waiting: "WAITING — do not touch it yet",
        red: "RED — press and hold, now",
        holding: "HOLDING — keep holding",
        ready: "GREEN — read the code, then let go"
      }[phase]));

    if (phase === "idle") {
      var arm = button("ARM THE BUTTON",
        "border:4px solid " + C.black + ";background:" + C.shutter + ";color:" + C.black + ";" +
        "font-size:18px;font-weight:700;", armMini);
      at(arm, 40, 60, 240, 64);
      body.appendChild(arm);
    } else {
      // Deliberately tiny (§51) — but with a large invisible pad around it, or
      // the hold is a test of pointing rather than of reacting.
      var pad = el("div", "position:absolute;left:40px;top:44px;width:240px;height:110px;" +
        "background:" + C.bombGreyDark + ";border:4px solid " + C.black + ";" +
        "display:flex;align-items:center;justify-content:center;touch-action:none;cursor:pointer;");
      var dot = el("div", "width:34px;height:34px;border:3px solid " + C.black + ";" +
        "background:" + paint + ";display:flex;align-items:center;justify-content:center;" +
        "font-size:13px;font-weight:700;color:" + C.black + ";",
        phase === "ready" ? String(module.code) : "");
      pad.appendChild(dot);
      pad.setAttribute("role", "button");
      pad.setAttribute("aria-label", "The mini button — " + phase);
      pad.addEventListener("pointerdown", function (event) {
        if (event.preventDefault) event.preventDefault();
        if (event.pointerId !== undefined && pad.setPointerCapture) {
          // §73: keep the hold alive through small movements of the cursor.
          try { pad.setPointerCapture(event.pointerId); } catch (e) {}
        }
        miniDown(module);
      });
      pad.addEventListener("pointerup", function () { miniUp(module); });
      pad.addEventListener("pointercancel", function () { miniUp(module); });
      pad.setAttribute("tabindex", "0");
      pad.addEventListener("keydown", function (event) {
        if (!isHoldKey(event) || event.repeat) return;   // ignore auto-repeat
        if (event.preventDefault) event.preventDefault();
        miniDown(module);
      });
      pad.addEventListener("keyup", function (event) {
        if (!isHoldKey(event)) return;
        if (event.preventDefault) event.preventDefault();
        miniUp(module);
      });
      body.appendChild(pad);
    }

    body.appendChild(el("div", "position:absolute;left:300px;top:20px;width:236px;" +
      "font-size:12px;color:#222;line-height:1.45;",
      "Arm it, then leave it alone. It turns red without warning: press and hold " +
      "the moment it does, keep holding until it turns green, read the number it " +
      "shows and only then let go. Touching it early, reacting late, or letting " +
      "go before green all set the bomb off. On a keyboard: focus the pad and " +
      "hold space or enter."));
  }

  function isHoldKey(event) {
    return event.key === " " || event.key === "Enter" || event.key === "Spacebar";
  }

  function armMini() {
    if (state.mini.phase !== "idle" || state.status !== "active") return;
    var module = moduleById(state.openId);
    state.mini.phase = "waiting";
    renderPanel();
    renderFace();
    state.mini.timers.push(window.setTimeout(function () {
      if (state.status !== "active" || state.mini.phase !== "waiting") return;
      state.mini.phase = "red";
      SOUND.click();
      renderPanel();
      // §54: catch it inside the window or it goes off.
      state.mini.timers.push(window.setTimeout(function () {
        if (state.status === "active" && state.mini.phase === "red") detonate("mini-slow");
      }, module.reaction_window_ms));
    }, module.delay_ms));
  }

  function miniDown(module) {
    if (state.status !== "active") return;
    wake();
    if (state.mini.phase === "waiting") return detonate("mini-early");   // §56
    if (state.mini.phase !== "red") return;
    state.mini.phase = "holding";
    renderPanel();
    state.mini.timers.push(window.setTimeout(function () {
      if (state.status !== "active" || state.mini.phase !== "holding") return;
      state.mini.phase = "ready";                                        // §55
      SOUND.click();
      renderPanel();
    }, module.required_hold_ms));
  }

  function miniUp(module) {
    if (state.status !== "active") return;
    if (state.mini.phase === "holding") return detonate("mini-release"); // §56
    if (state.mini.phase !== "ready") return;
    state.mini.phase = "done";
    act(module.id, module.code);
  }

  // --- the manual (§27-§30, §68-§71) --------------------------------------

  function manualShell(title) {
    var page = at(el("div"), 0, 0, W, H);
    page.style.cssText += "background:" + C.manualBg + ";color:" + C.black + ";" +
      "font-family:Arial,Helvetica,sans-serif;pointer-events:auto;";
    page.appendChild(el("div", "position:absolute;left:20px;top:14px;font-size:28px;" +
      "font-weight:700;", title));
    var exit = button("Exit", "border:0;background:" + C.manualExitBlue + ";color:" + C.white + ";" +
      "font-size:14px;font-weight:700;", function () {
        if (state.manualPage === "home") closeManual();
        else { state.manualPage = "home"; renderManual(); }
      });
    at(exit, W - 73, 14, 53, 34);
    page.appendChild(exit);
    return page;
  }

  function renderManual() {
    clear(state.manualLayer);
    var page = manualShell(state.manualPage === "home" ? "The Bomb:"
      : MODULE_NAMES[state.manualPage]);
    var body = at(el("div"), 20, 58, W - 40, H - 78);
    page.appendChild(body);
    if (state.manualPage === "home") renderManualHome(body);
    else if (state.manualPage === "maze") renderManualMaze(body);
    else if (state.manualPage === "simon") renderManualSimon(body);
    else if (state.manualPage === "according_to_number") renderManualNumber(body);
    else renderManualMini(body);
    state.manualLayer.appendChild(page);
  }

  function renderManualHome(body) {
    // §29: a heavy black-bordered grey selector. Only the modules this version
    // actually implements are listed — no dead buttons.
    var selector = at(el("div"), 0, 0, 330, 240);
    selector.style.cssText += "background:" + C.panelGrey + ";border:6px solid " + C.black + ";" +
      "padding:10px;display:flex;flex-direction:column;gap:8px;";
    ["maze", "simon", "according_to_number", "mini_button"].forEach(function (type) {
      var entry = button(MODULE_NAMES[type],
        "border:3px solid " + C.black + ";background:" + C.white + ";color:" + C.black + ";" +
        "font-size:17px;font-weight:700;height:46px;text-align:left;padding-left:12px;",
        function () { state.manualPage = type; renderManual(); });
      selector.appendChild(entry);
    });
    body.appendChild(selector);
    body.appendChild(el("div", "position:absolute;left:346px;top:0;width:200px;font-size:13px;" +
      "line-height:1.5;",
      "The bomb is still ticking while you read this. Open the page for a bay, " +
      "learn what it wants, then go back and do it. Exit returns you to the bomb."));
  }

  function renderManualMaze(body) {
    body.appendChild(el("div", "position:absolute;left:0;top:0;width:550px;font-size:13px;" +
      "line-height:1.45;",
      "Blue is where you are. Guide it to the red destination. Green is the tip " +
      "that identifies which maze you are in — it is a label, not a target."));
    var size = 70, cell = size / 4;
    MAZE_LAYOUTS.forEach(function (layout, index) {
      var x = (index % 4) * (size + 62) + 4;
      var y = Math.floor(index / 4) * (size + 44) + 42;
      var box = el("div", "position:absolute;left:" + x + "px;top:" + y + "px;width:" + size +
        "px;height:" + size + "px;border:2px solid " + C.black + ";background:" + C.white + ";");
      // Walls in red (§38), drawn straight from the shared layout data.
      for (var r = 0; r < MAZE_SIZE; r++) {
        for (var c = 0; c < MAZE_SIZE; c++) {
          if (r < MAZE_SIZE - 1 && layout.h[r][c]) {
            box.appendChild(el("div", "position:absolute;left:" + (c * cell) + "px;top:" +
              ((r + 1) * cell - 1) + "px;width:" + cell + "px;height:2px;background:" + C.red + ";"));
          }
          if (c < MAZE_SIZE - 1 && layout.v[r][c]) {
            box.appendChild(el("div", "position:absolute;left:" + ((c + 1) * cell - 1) + "px;top:" +
              (r * cell) + "px;width:2px;height:" + cell + "px;background:" + C.red + ";"));
          }
        }
      }
      box.appendChild(el("div", "position:absolute;left:" + (layout.tip[1] * cell + 3) + "px;top:" +
        (layout.tip[0] * cell + 3) + "px;width:" + (cell - 6) + "px;height:" + (cell - 6) +
        "px;background:#17c40a;border-radius:50%;"));
      body.appendChild(box);
    });
  }

  function swatch(colour) {
    return el("div", "width:38px;height:28px;border:2px solid " + C.black + ";" +
      "background:" + SIMON_PAINT[colour][1] + ";color:" + C.black + ";font-size:18px;" +
      "display:flex;align-items:center;justify-content:center;", SIMON_SHAPE[colour]);
  }

  function renderManualSimon(body) {
    body.appendChild(el("div", "position:absolute;left:0;top:0;width:540px;font-size:14px;",
      "Press the colour opposite the one that flashed. Never the colour you saw."));
    SIMON_COLOURS.forEach(function (colour, index) {
      var row = at(el("div"), 0, 40 + index * 54, 420, 46);
      row.style.cssText += "display:flex;align-items:center;gap:14px;font-size:20px;" +
        "font-weight:700;border:3px solid " + C.black + ";background:" + C.white + ";padding:0 12px;";
      row.appendChild(swatch(colour));
      row.appendChild(el("div", "width:96px;text-transform:uppercase;", colour));
      row.appendChild(el("div", "font-size:22px;", "→"));
      row.appendChild(swatch(SIMON_MAP[colour]));
      row.appendChild(el("div", "text-transform:uppercase;", SIMON_MAP[colour]));
      body.appendChild(row);
    });
    body.appendChild(el("div", "position:absolute;left:436px;top:44px;width:110px;font-size:12px;" +
      "line-height:1.5;",
      "Each stage replays from the start and adds one flash. There are no strikes: " +
      "the first wrong press is the last one."));
  }

  function renderManualNumber(body) {
    var axis = axisLabel();
    body.appendChild(el("div", "position:absolute;left:0;top:0;width:550px;font-size:13px;" +
      "line-height:1.45;",
      "The green 1 identifies the correct number grid — match it to the lit dot on " +
      "the bay. Find the displayed number in that grid and press its " + axis +
      ": left = 1, middle = 2, right = 3."));
    NUMBER_PATTERNS.forEach(function (pattern, index) {
      var x = (index % 4) * 134 + 4;
      var y = Math.floor(index / 4) * 126 + 64;
      var box = el("div", "position:absolute;left:" + x + "px;top:" + y + "px;width:108px;" +
        "height:108px;border:3px solid " + C.black + ";background:" + C.white + ";");
      for (var r = 0; r < 3; r++) {
        for (var c = 0; c < 3; c++) {
          var value = pattern[r][c];
          box.appendChild(el("div", "position:absolute;left:" + (c * 34 + 1) + "px;top:" +
            (r * 34 + 1) + "px;width:32px;height:32px;display:flex;align-items:center;" +
            "justify-content:center;font-size:18px;font-weight:700;background:" +
            (value === 1 ? "#17c40a" : C.white) + ";border:" +
            (value === 1 ? "3px solid " + C.black : "1px solid #bbb") + ";", value));
        }
      }
      body.appendChild(box);
    });
  }

  function axisLabel() {
    var modules = state && state.puzzle ? state.puzzle.payload.modules : [];
    for (var i = 0; i < modules.length; i++) {
      if (modules[i].type === "according_to_number") {
        return modules[i].axis === "row" ? "row" : "column";
      }
    }
    return "column";
  }

  function renderManualMini(body) {
    body.appendChild(el("div", "position:absolute;left:0;top:0;width:540px;font-size:15px;" +
      "line-height:1.7;white-space:pre-line;",
      "Wait for the tiny button to turn red.\n" +
      "When it turns red, press and hold it immediately.\n" +
      "Keep holding until it turns green.\n" +
      "The green button shows a two-digit code — that code is what proves the hold.\n" +
      "Release only after it has turned green."));
    body.appendChild(el("div", "position:absolute;left:0;top:170px;width:540px;font-size:13px;" +
      "line-height:1.5;font-weight:700;",
      "Pressing it before it turns red, reacting too slowly, or letting go early " +
      "all set the bomb off. Arming it commits you: the bay will not close again " +
      "until it is done."));
  }

  // --- the round -----------------------------------------------------------

  function act(moduleId, action) {
    if (state.status !== "active") return;
    wake();
    var moves = state.moves.concat([{ m: moduleId, a: action }]);
    var result = validate(state.puzzle.payload, moves, true);
    if (!result.ok) return detonate(result.reason);   // §18: one wrong action
    state.moves = moves;
    state.result = result;
    SOUND.click();
    var entry = result.state[moduleId];
    if (entry && entry.solved) {
      closeModule();                                  // §33: the shutter falls
      return;
    }
    renderPanel();
    renderFace();
    var module = moduleById(moduleId);
    if (module.type === "simon" && entry.in_stage === 0) {
      playSimon(module, entry);                       // a stage just completed
    }
  }

  function pressOk() {
    if (state.status !== "active") return;
    var moves = state.moves.concat([{ m: "ok" }]);
    var result = validate(state.puzzle.payload, moves, false);
    if (!result.ok) return detonate(result.reason);   // §15
    state.moves = moves;
    state.result = result;
    defuse();
  }

  function openModule(moduleId) {
    if (state.status !== "active") return;
    state.openId = moduleId;
    var module = moduleById(moduleId);
    renderPanel();
    if (module.type === "simon") {
      playSimon(module, state.result.state[moduleId]);
    }
  }

  function closeModule() {
    // Arming the mini button locks the panel; every other bay closes freely.
    if (state.openId !== null) {
      var module = moduleById(state.openId);
      if (module && module.type === "mini_button" &&
          state.mini.phase !== "idle" && state.mini.phase !== "done") {
        return;
      }
    }
    state.openId = null;
    stopSimonTimers();
    renderPanel();
    renderFace();
  }

  function openManual() {
    if (state.status !== "active") return;
    // Same commitment as the panel: you cannot read your way out of an armed
    // mini button.
    if (state.mini.phase !== "idle" && state.mini.phase !== "done") return;
    state.view = "manual";
    state.manualPage = "home";
    renderManual();
    applyView();
  }

  function closeManual() {
    state.view = "bomb";
    clear(state.manualLayer);
    applyView();
  }

  function applyView() {
    var manual = state.view === "manual";
    state.faceLayer.style.display = manual ? "none" : "block";
    state.panelLayer.style.display = manual ? "none" : "block";
    state.manualLayer.style.display = manual ? "block" : "none";
  }

  // §76: every fatal path lands here, and only here.
  function detonate(reason) {
    if (state.status !== "active") return;
    state.status = "failed";
    stopClocks();
    SOUND.boom();
    clear(state.faceLayer);
    clear(state.panelLayer);
    clear(state.manualLayer);
    state.view = "bomb";
    applyView();

    var screen = at(el("div"), 0, 0, W, H);
    screen.style.cssText += "background:#000;display:flex;flex-direction:column;" +
      "align-items:center;justify-content:center;gap:14px;font-family:Arial,Helvetica,sans-serif;";
    screen.appendChild(el("div", "color:" + C.red + ";font-weight:700;font-size:64px;" +
      "text-align:center;", "MISSION FAILED"));
    screen.appendChild(el("div", "color:#ff8a8a;font-size:16px;text-align:center;",
      REASONS[reason] || "The bomb went off."));
    screen.appendChild(el("div", "color:#777;font-size:13px;text-align:center;",
      "A fresh bomb is on its way."));
    screen.setAttribute("role", "alert");
    state.faceLayer.appendChild(screen);

    // §20: five seconds of dead bomb, then the engine's brand-new one — a
    // rejected board is exactly how we ask for it.
    state.failTimer = window.setTimeout(function () {
      state.api.submit(JSON.stringify({ v: RULES_VERSION, failed: reason }));
    }, 5000);
  }

  // §77: and every winning path lands here.
  function defuse() {
    state.status = "defused";
    stopClocks();
    SOUND.success();
    clear(state.panelLayer);
    clear(state.manualLayer);
    var seconds = state.remaining;
    clear(state.faceLayer);
    var screen = at(el("div"), 0, 0, W, H);
    screen.style.cssText += "background:#062b06;display:flex;flex-direction:column;" +
      "align-items:center;justify-content:center;gap:12px;font-family:Arial,Helvetica,sans-serif;";
    screen.appendChild(el("div", "color:" + C.green + ";font-weight:700;font-size:56px;" +
      "text-align:center;", "BOMB DEFUSED"));
    screen.appendChild(el("div", "color:#bdf5bd;font-size:16px;",
      "Stopped with " + seconds + "s left."));
    screen.setAttribute("role", "status");
    state.faceLayer.appendChild(screen);
    state.api.submit(JSON.stringify({ v: RULES_VERSION, moves: state.moves }));
  }

  // --- the fuse (§8: an absolute deadline, never a frame count) ------------

  function startFuse() {
    state.deadline = Date.now() + state.puzzle.payload.fuse_seconds * 1000;
    state.clock = window.setInterval(function () {
      if (state.status !== "active") return;
      var left = Math.max(0, Math.ceil((state.deadline - Date.now()) / 1000));
      if (left === state.remaining) return;
      state.remaining = left;
      if (state.timerText) state.timerText.textContent = String(left);
      if (left <= 0) return detonate("timer-expired");   // §93
      SOUND.tick();                                      // §9: one tick a second
    }, 100);
  }

  function stopSimonTimers() {
    state.simon.timers.forEach(function (id) { window.clearTimeout(id); });
    state.simon.timers = [];
    state.simon.playing = false;
  }

  function stopClocks() {
    if (state.clock) { window.clearInterval(state.clock); state.clock = null; }
    if (state.pulse) { window.clearInterval(state.pulse); state.pulse = null; }
    state.mini.timers.forEach(function (id) { window.clearTimeout(id); });
    state.mini.timers = [];
    stopSimonTimers();
  }

  // --- scaling (§24: one uniform scale, never a rearranged bomb) -----------

  function rescale() {
    if (!state || !state.frame || !state.surface) return;
    var available = state.frame.clientWidth || W;
    var scale = Math.max(0.5, Math.min(available / W, 1.5));
    state.surface.style.transform = "scale(" + scale + ")";
    state.frame.style.height = Math.round(H * scale) + "px";
  }

  window.RelayGames = window.RelayGames || {};
  window.RelayGames["bomb_defuse"] = {
    mount: function (container, puzzle, api) {
      state = {
        puzzle: puzzle, api: api, status: "active", view: "bomb",
        manualPage: "home", openId: null, moves: [],
        result: validate(puzzle.payload, [], true),
        remaining: puzzle.payload.fuse_seconds,
        clock: null, pulse: null, failTimer: null,
        simon: { pads: {}, timers: [], playing: false },
        mini: { phase: "idle", timers: [] }
      };

      var root = el("div", "max-width:100%;margin:0 auto;");
      state.frame = el("div", "position:relative;width:100%;overflow:hidden;");
      state.surface = el("div", "position:absolute;left:0;top:0;width:" + W + "px;height:" + H +
        "px;transform-origin:top left;background:" + C.bgBlue + ";" +
        "font-family:Arial,Helvetica,sans-serif;");
      state.faceLayer = el("div", "position:absolute;left:0;top:0;width:" + W + "px;height:" + H + "px;");
      state.panelLayer = el("div", "position:absolute;left:0;top:0;width:" + W + "px;height:" +
        H + "px;pointer-events:none;");
      state.manualLayer = el("div", "position:absolute;left:0;top:0;width:" + W + "px;height:" + H +
        "px;display:none;pointer-events:none;");
      state.surface.appendChild(state.faceLayer);
      state.surface.appendChild(state.panelLayer);
      state.surface.appendChild(state.manualLayer);
      state.frame.appendChild(state.surface);
      root.appendChild(state.frame);

      var hint = el("p", "color:#8a8a96;font-size:0.85rem;margin:10px 0 0;line-height:1.5;",
        "One wrong move and the bomb goes off — there are no strikes. Tap a bay to " +
        "work it, MANUAL to look the rules up (the fuse keeps burning while you " +
        "read), and OK only once every bay has shut behind its orange shutter.");
      root.appendChild(hint);
      container.appendChild(root);
      state.root = root;

      renderFace();
      applyView();
      startFuse();
      state.resizeHandler = rescale;
      window.addEventListener("resize", state.resizeHandler);
      rescale();
    },

    unmount: function () {
      if (!state) return;                 // idempotent
      stopClocks();
      if (state.failTimer) window.clearTimeout(state.failTimer);
      window.removeEventListener("resize", state.resizeHandler);
      if (state.root && state.root.parentNode) {
        state.root.parentNode.removeChild(state.root);
      }
      state = null;
    },

    // Test hooks: the shared replay and the shared data, with no DOM involved.
    __validate: validate,
    __data: {
      MAZE_LAYOUTS: MAZE_LAYOUTS, NUMBER_PATTERNS: NUMBER_PATTERNS,
      SIMON_MAP: SIMON_MAP, SIMON_COLOURS: SIMON_COLOURS
    }
  };
})();
