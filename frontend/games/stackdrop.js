// STACKDROP renderer — pull pins in the right order and let gravity sort the
// balls into their matching containers. Tap a pin (or its numbered chip) to arm
// it, tap again / press PULL to commit; the fall is the same deterministic cell
// simulation the server runs, animated one gravity pass at a time. Restart is
// offered, Undo is not — a pulled pin is gone (spec §3).
// Answer = JSON {"v":1,"remove":["p1","p0",...]} — the server replays it.
(function () {
  "use strict";

  // Ball and container colours come from the shared piece palette; each one
  // also carries its glyph, so a ball is matched to its container by shape and
  // never by hue alone.
  var T = window.RelayTheme;

  var HAZARD = "hazard";
  // Every slanted surface and the column it rolls a ball to. Ramps are fixed
  // structure, tilt pins are removable — same rule, different permanence.
  var SLOPES = { ramp_left: -1, ramp_right: 1, tilt_left: -1, tilt_right: 1 };
  var FAIL = "fail", MOVE = "move", CONTAIN = "contain", BLOCKED = "blocked";

  // Shape carries the meaning for colour-blind players; colour reinforces it.
  var BALL_STYLE = {
    circle: { glyph: "●", colour: T.piece(0) },
    triangle: { glyph: "▲", colour: T.piece(1) },
    square: { glyph: "■", colour: T.piece(3) },
    diamond: { glyph: "◆", colour: T.piece(4) },
  };
  var PIN_GLYPH = { hold: "═", tilt_left: "╱", tilt_right: "╲" };
  var RAMP_GLYPH = { ramp_left: "◣", ramp_right: "◢" };
  var PASS_MS = 110;                 // one gravity pass per tick

  var state = null;

  function key(r, c) { return r + "," + c; }

  // --- simulation (mirror of backend/games/game7_stackdrop.py) ------------

  function chamberFrom(payload) {
    var chamber = {
      rows: payload.rows,
      cols: payload.cols,
      statics: {},
      containers: {},
      pins: {},
      pinOrder: [],
      balls: [],
    };
    payload.static_cells.forEach(function (cell) {
      chamber.statics[key(cell.r, cell.c)] = cell.type;
    });
    payload.containers.forEach(function (container) {
      container.cells.forEach(function (cell) {
        chamber.containers[key(cell[0], cell[1])] = container.kind;
      });
    });
    payload.pins.forEach(function (pin) {
      chamber.pins[pin.id] = { kind: pin.kind, cells: pin.cells };
      chamber.pinOrder.push(pin.id);
    });
    payload.balls.forEach(function (ball) {
      chamber.balls.push({ id: ball.id, kind: ball.kind, start: ball.start });
    });
    return chamber;
  }

  function pinMap(chamber, removed) {
    var map = {};
    chamber.pinOrder.forEach(function (id) {
      if (removed[id]) return;
      var pin = chamber.pins[id];
      pin.cells.forEach(function (cell) { map[key(cell[0], cell[1])] = pin.kind; });
    });
    return map;
  }

  function enter(chamber, occupied, pinned, r, c, kind) {
    if (r >= chamber.rows) return FAIL;             // out through the floor
    if (c < 0 || c >= chamber.cols) return BLOCKED; // the chamber's side walls
    var at = key(r, c);
    if (occupied[at]) return BLOCKED;               // balls rest on each other
    if (chamber.containers[at] !== undefined) {
      return chamber.containers[at] === kind ? CONTAIN : FAIL;
    }
    if (chamber.statics[at] === HAZARD) return FAIL;
    if (chamber.statics[at] !== undefined || pinned[at] !== undefined) return BLOCKED;
    return MOVE;
  }

  function slopeAt(chamber, pinned, r, c) {
    var at = key(r, c);
    if (chamber.statics[at] !== undefined) return SLOPES[chamber.statics[at]] || null;
    if (pinned[at] !== undefined) return SLOPES[pinned[at]] || null;
    return null;
  }

  function initialState(chamber) {
    return chamber.balls.map(function (ball) {
      return [ball.start[0], ball.start[1], false];
    });
  }

  // One gravity pass: each ball moves at most one cell, bottom-most first, so
  // a ball can never tunnel through one that is about to move out of the way.
  function gravityPass(chamber, pinned, balls) {
    var order = balls.map(function (_, index) { return index; });
    order.sort(function (a, b) {
      return balls[b][0] - balls[a][0] || balls[a][1] - balls[b][1];
    });
    var moved = false;
    for (var i = 0; i < order.length; i++) {
      var index = order[i];
      var ball = balls[index];
      if (ball[2]) continue;
      var occupied = {};
      balls.forEach(function (other, j) {
        if (j !== index) occupied[key(other[0], other[1])] = true;
      });
      var kind = chamber.balls[index].kind;
      var r = ball[0] + 1, c = ball[1];
      var outcome = enter(chamber, occupied, pinned, r, c, kind);
      if (outcome === BLOCKED) {
        var slope = slopeAt(chamber, pinned, r, c);
        if (slope === null) continue;               // flat: it rests here
        c = ball[1] + slope;
        outcome = enter(chamber, occupied, pinned, r, c, kind);
        if (outcome === BLOCKED) continue;          // roll-off cell is taken
      }
      if (outcome === FAIL) return { moved: moved, dead: true };
      ball[0] = r;
      ball[1] = c;
      ball[2] = outcome === CONTAIN;
      moved = true;
    }
    return { moved: moved, dead: false };
  }

  function resolve(chamber, pinned, balls) {
    for (;;) {
      var step = gravityPass(chamber, pinned, balls);
      if (step.dead) return false;
      if (!step.moved) return true;
    }
  }

  function solved(balls) {
    return balls.every(function (ball) { return ball[2]; });
  }

  // Exported for the Python/JS parity fixture test — same contract as _play().
  function replay(payload, removals) {
    var chamber = chamberFrom(payload);
    var balls = initialState(chamber);
    var removed = {};
    var alive = resolve(chamber, pinMap(chamber, removed), balls);
    for (var i = 0; alive && i < removals.length; i++) {
      removed[removals[i]] = true;
      alive = resolve(chamber, pinMap(chamber, removed), balls);
    }
    return { balls: balls, alive: alive };
  }

  // --- rendering ---------------------------------------------------------

  function cellBox(px) {
    var el = document.createElement("div");
    el.style.cssText =
      "border-radius:4px;display:flex;align-items:center;justify-content:center;" +
      "font-weight:900;line-height:1;font-size:" + Math.floor(px * 0.62) + "px;";
    return el;
  }

  function render() {
    var p = state.payload;
    var px = state.cell;
    var pinned = pinMap(state.chamber, state.removed);
    var ballAt = {};
    state.balls.forEach(function (ball, index) {
      ballAt[key(ball[0], ball[1])] = index;
    });

    state.grid.innerHTML = "";
    for (var r = 0; r < p.rows; r++) {
      for (var c = 0; c < p.cols; c++) {
        var at = key(r, c);
        var el = cellBox(px);
        var css = "background:" + T.cell + ";";
        var feature = state.chamber.statics[at];
        var container = state.chamber.containers[at];
        var pinKind = pinned[at];
        var ballIndex = ballAt[at];

        if (feature === "wall") {
          css = "background:" + T.bgDeep + ";";
        } else if (feature === HAZARD) {
          css = "background:" + T.fade(T.hazard, 0.22) + ";color:" + T.hazard + ";";
          el.textContent = "✖";
          el.title = "Hazard";
        } else if (RAMP_GLYPH[feature]) {
          css = "background:" + T.cell + ";color:" + T.text + ";";
          el.textContent = RAMP_GLYPH[feature];
          el.title = "Fixed ramp";
        } else if (container !== undefined) {
          var slot = BALL_STYLE[container];
          css = "background:" + T.bgDeep + ";border:3px dashed " + slot.colour + ";color:" +
            slot.colour + ";";
          el.textContent = slot.glyph;
          el.title = container + " container";
        }
        if (ballIndex !== undefined) {
          var ballStyle = BALL_STYLE[state.chamber.balls[ballIndex].kind];
          css += "background:" + ballStyle.colour + ";color:" + T.ink + ";" +
            "box-shadow:" + T.glow(ballStyle.colour, 10) + ";" +
            (state.balls[ballIndex][2]
              ? "outline:3px solid " + T.text + ";outline-offset:-3px;" : "");
          el.textContent = ballStyle.glyph;
        } else if (pinKind !== undefined) {
          var pinId = null;
          state.chamber.pinOrder.forEach(function (id) {
            var pin = state.chamber.pins[id];
            if (state.removed[id]) return;
            pin.cells.forEach(function (cell) {
              if (cell[0] === r && cell[1] === c) pinId = id;
            });
          });
          var armed = pinId === state.armed;
          css = "background:" + (armed ? T.goal : T.fade(T.goal, 0.6)) +
            ";color:" + T.ink + ";" +
            "cursor:pointer;" +
            (armed ? "outline:3px solid " + T.text + ";outline-offset:-3px;" : "");
          el.textContent = PIN_GLYPH[pinKind];
          el.title = "Pin " + (state.chamber.pinOrder.indexOf(pinId) + 1);
          bindPin(el, pinId);
        }
        el.style.cssText += css;
        state.grid.appendChild(el);
      }
    }

    state.chamber.pinOrder.forEach(function (id, index) {
      var chip = state.chips[index];
      var gone = !!state.removed[id];
      var armed = id === state.armed;
      chip.disabled = gone || state.busy || state.done;
      chip.textContent = (index + 1) + " " + (gone ? "·" : PIN_GLYPH[state.chamber.pins[id].kind]);
      chip.style.background = gone ? T.bgDeep : armed ? T.goal : T.cell;
      chip.style.color = armed ? T.ink : T.text;
      chip.style.borderColor = armed ? T.text : T.fade(T.goal, 0.6);
      chip.style.textDecoration = gone ? "line-through" : "none";
      chip.setAttribute("aria-pressed", armed ? "true" : "false");
    });
    state.pullBtn.disabled = !state.armed || state.busy || state.done;
    state.pullBtn.textContent = state.armed
      ? "PULL " + (state.chamber.pinOrder.indexOf(state.armed) + 1)
      : "PULL";
    state.status.textContent = state.message;
  }

  function bindPin(el, pinId) {
    el.addEventListener("click", function () { arm(pinId); });
  }

  function arm(pinId) {
    if (!state || state.busy || state.done || state.removed[pinId]) return;
    // First tap arms, second tap commits — no accidental pulls (spec §3).
    if (state.armed === pinId) {
      pull(pinId);
      return;
    }
    state.armed = pinId;
    state.message = "Pin " + (state.chamber.pinOrder.indexOf(pinId) + 1) +
      " armed — tap it again or press PULL.";
    render();
  }

  function pull(pinId) {
    if (!state || state.busy || state.done || state.removed[pinId]) return;
    state.removed[pinId] = true;
    state.sequence.push(pinId);
    state.armed = null;
    state.armedIndex = 0;
    state.message = "Gravity…";
    state.busy = true;
    render();
    if (state.reducedMotion) {
      var pinned = pinMap(state.chamber, state.removed);
      settle(resolve(state.chamber, pinned, state.balls));
      return;
    }
    state.timer = window.setInterval(function () {
      var step = gravityPass(state.chamber, pinMap(state.chamber, state.removed), state.balls);
      render();
      if (step.dead) {
        stopTimer();
        settle(false);
      } else if (!step.moved) {
        stopTimer();
        settle(true);
      }
    }, PASS_MS);
  }

  // Called once the chamber is still again: win, lost, or keep pulling.
  function settle(alive) {
    state.busy = false;
    if (alive && solved(state.balls)) {
      state.done = true;
      state.message = "🎉 All balls home — submitting…";
      render();
      state.submitTimer = window.setTimeout(submit, 350);
      return;
    }
    if (!alive) {
      state.lost = true;
      state.message = "💥 A ball was lost — press RESTART to reset the chamber.";
    } else {
      state.message = "Chamber settled. Next pin?";
    }
    render();
  }

  function submit() {
    if (!state || state.submitted) return;
    state.submitted = true;
    state.api.submit(JSON.stringify({ v: 1, remove: state.sequence }));
  }

  function restart() {
    if (!state || state.busy) return;
    stopTimer();
    state.removed = {};
    state.sequence = [];
    state.balls = initialState(state.chamber);
    state.armed = null;
    state.lost = false;
    state.done = false;
    state.message = "Chamber reset. Pulled pins are back — pick your order again.";
    render();
  }

  function stopTimer() {
    if (state && state.timer) {
      window.clearInterval(state.timer);
      state.timer = null;
    }
  }

  function makeButton(label, aria, handler) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = label;
    btn.setAttribute("aria-label", aria);
    btn.style.cssText =
      "min-width:52px;min-height:48px;font-size:1rem;font-weight:900;" +
      "border:2px solid " + T.fade(T.goal, 0.55) + ";border-radius:12px;" +
      "background:" + T.cell + ";color:" + T.text + ";cursor:pointer;";
    btn.addEventListener("click", handler);
    return btn;
  }

  window.RelayGames = window.RelayGames || {};
  window.RelayGames["stackdrop"] = {
    mount: function (container, puzzle, api) {
      var p = puzzle.payload;
      var avail = Math.min(container.clientWidth || 520, 520);
      var chamber = chamberFrom(p);
      state = {
        payload: p,
        api: api,
        chamber: chamber,
        balls: initialState(chamber),
        removed: {},
        sequence: [],
        armed: null,
        busy: false,
        done: false,
        lost: false,
        submitted: false,
        timer: null,
        submitTimer: null,
        chips: [],
        message: "Tap a pin to arm it.",
        cell: Math.max(22, Math.min(46, Math.floor((avail - 12) / p.cols) - 3)),
        reducedMotion:
          !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches),
      };

      var root = document.createElement("div");

      state.grid = document.createElement("div");
      state.grid.style.cssText =
        "display:grid;grid-template-columns:repeat(" + p.cols + "," + state.cell + "px);" +
        "grid-auto-rows:" + state.cell + "px;gap:3px;justify-content:center;margin:0 auto;";
      state.grid.setAttribute("role", "img");
      state.grid.setAttribute(
        "aria-label",
        "Drop chamber, " + p.rows + " rows by " + p.cols + " columns, " +
          p.balls.length + " balls, " + p.pins.length + " pins."
      );
      root.appendChild(state.grid);

      // Numbered pin chips: the 44px+ tap targets and the keyboard/focus path,
      // since a grid cell can be smaller than a comfortable touch target.
      var chipRow = document.createElement("div");
      chipRow.style.cssText =
        "display:flex;gap:8px;justify-content:center;margin-top:12px;flex-wrap:wrap;";
      chamber.pinOrder.forEach(function (id, index) {
        var chip = makeButton("", "Arm pin " + (index + 1), function () { arm(id); });
        chip.style.minWidth = "56px";
        state.chips.push(chip);
        chipRow.appendChild(chip);
      });
      root.appendChild(chipRow);

      var controls = document.createElement("div");
      controls.style.cssText =
        "display:flex;gap:10px;justify-content:center;margin-top:10px;flex-wrap:wrap;";
      state.pullBtn = makeButton("PULL", "Pull the armed pin", function () {
        if (state.armed) pull(state.armed);
      });
      state.pullBtn.style.cssText +=
        "background:" + T.fade(T.goal, 0.75) + ";color:" + T.ink +
        ";border-color:" + T.goal + ";min-width:96px;";
      var restartBtn = makeButton("↺ RESTART", "Reset the chamber", restart);
      var checkBtn = makeButton("CHECK", "Submit your pull order", submit);
      checkBtn.style.cssText +=
        "background:" + T.goal + ";color:" + T.ink + ";border-color:" + T.goal +
        ";box-shadow:" + T.glow(T.goal, 14) + ";";
      controls.appendChild(state.pullBtn);
      controls.appendChild(restartBtn);
      controls.appendChild(checkBtn);
      root.appendChild(controls);

      state.status = document.createElement("p");
      state.status.setAttribute("role", "status");
      state.status.style.cssText =
        "text-align:center;font-weight:700;font-size:0.9rem;margin:10px 0 0;min-height:1.2em;";
      root.appendChild(state.status);

      var hint = document.createElement("p");
      hint.textContent =
        "Pull pins so every ball lands in the container with its own shape. " +
        "═ pins hold a ball, ╱ ╲ pins and ◣ ◢ ramps roll it one cell down-slope — " +
        "so a slanted pin steers a ball while it's there and lets it drop straight " +
        "through once it's gone. Hazards (✖) and the wrong container lose the ball. " +
        "A pulled pin never comes back; RESTART resets the whole chamber.";
      hint.style.cssText =
        "color:" + T.muted + ";font-size:0.85rem;margin:8px 0 0;";
      root.appendChild(hint);

      container.appendChild(root);
      state.root = root;

      state.keyHandler = function (event) {
        if (state.busy || state.done) return;
        if (event.key === "Enter") {
          event.preventDefault();
          if (state.armed) pull(state.armed);
          return;
        }
        if (event.key === "r" || event.key === "R") {
          event.preventDefault();
          restart();
          return;
        }
        var digit = parseInt(event.key, 10);
        if (digit >= 1 && digit <= chamber.pinOrder.length) {
          event.preventDefault();
          arm(chamber.pinOrder[digit - 1]);
        }
      };
      document.addEventListener("keydown", state.keyHandler);

      render();
    },

    unmount: function () {
      if (!state) return; // idempotent
      stopTimer();
      if (state.submitTimer) window.clearTimeout(state.submitTimer);
      document.removeEventListener("keydown", state.keyHandler);
      if (state.root && state.root.parentNode) {
        state.root.parentNode.removeChild(state.root);
      }
      state = null;
    },

    // Test hook: the deterministic simulation, with no DOM involved.
    __replay: replay,
  };
})();
