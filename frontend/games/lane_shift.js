// LANE SHIFT renderer — one action per turn, then the whole belt advances.
// Toggle a junction (cell or numbered chip), spend a hold pad, or PASS; the
// movement tick then plays as a short animation with the controls disabled.
// Each packet shows only its *next* edge, never a longer preview (spec §2).
// Restart is offered, Undo deliberately is not.
// Answer = JSON {"v":1,"actions":[["toggle","s0"],["pass"],...]} — replayed by
// the server, which is the only authority on success.
(function () {
  "use strict";

  // Packet and exit colours come from the shared piece palette; every packet
  // also wears its glyph, so a packet is matched to its exit by shape.
  var T = window.RelayTheme;

  var DELTAS = { straight: 0, down: 1, up: -1 };
  var STATE_ARROW = { straight: "→", down: "↘", up: "↗" };
  // Shape carries the meaning for colour-blind players; colour reinforces it.
  var KIND_STYLE = {
    circle: { glyph: "●", colour: T.piece(0) },
    triangle: { glyph: "▲", colour: T.piece(1) },
    square: { glyph: "■", colour: T.piece(3) },
    diamond: { glyph: "◆", colour: T.piece(4) },
  };
  var TICK_MS = 260;                 // action shown, then the belt advances

  var state = null;

  function key(r, c) { return r + "," + c; }

  // --- simulation (mirror of backend/games/game8_lane_shift.py) ------------

  function boardFrom(payload) {
    var board = {
      lanes: payload.lanes,
      columns: payload.columns,
      turnCap: payload.turn_cap,
      switches: payload.switches.map(function (s) {
        return { id: s.id, cell: s.cell, states: s.states, initial: s.states.indexOf(s.initial) };
      }),
      holds: payload.holds.map(function (h) {
        return { id: h.id, cell: h.cell, charges: h.charges };
      }),
      blockers: {},
      packets: payload.packets.map(function (p) {
        return { id: p.id, kind: p.kind, start: p.start, spawn: p.spawn_tick };
      }),
      exits: [],
      switchAt: {},
      holdAt: {},
    };
    payload.blockers.forEach(function (cell) { board.blockers[key(cell[0], cell[1])] = true; });
    payload.exits.forEach(function (exit) { board.exits[exit.lane] = exit.kind; });
    board.switches.forEach(function (s, index) { board.switchAt[key(s.cell[0], s.cell[1])] = index; });
    board.holds.forEach(function (h, index) { board.holdAt[key(h.cell[0], h.cell[1])] = index; });
    return board;
  }

  function initialState(board) {
    return {
      tick: 0,
      positions: board.packets.map(function () { return ["wait"]; }),
      switchIndex: board.switches.map(function (s) { return s.initial; }),
      charges: board.holds.map(function (h) { return h.charges; }),
    };
  }

  function clone(state) {
    return {
      tick: state.tick,
      positions: state.positions.map(function (p) { return p.slice(); }),
      switchIndex: state.switchIndex.slice(),
      charges: state.charges.slice(),
    };
  }

  // One whole turn: spawn, apply the action, advance everything at once.
  // Returns null when the attempt has failed.
  function step(board, state, action) {
    var next = clone(state);
    var i;

    var occupied = {};
    next.positions.forEach(function (p) {
      if (p[0] === "on") occupied[key(p[1], p[2])] = true;
    });
    for (i = 0; i < board.packets.length; i++) {
      var packet = board.packets[i];
      if (next.positions[i][0] === "wait" && packet.spawn === next.tick) {
        if (occupied[key(packet.start[0], packet.start[1])]) return null;
        next.positions[i] = ["on", packet.start[0], packet.start[1]];
        occupied[key(packet.start[0], packet.start[1])] = true;
      }
    }

    var held = -1;
    if (action[0] === "pass" && action.length === 1) {
      // nothing to do
    } else if (action[0] === "toggle" && action.length === 2) {
      var at = -1;
      board.switches.forEach(function (s, index) { if (s.id === action[1]) at = index; });
      if (at === -1) return null;
      next.switchIndex[at] = (next.switchIndex[at] + 1) % board.switches[at].states.length;
    } else if (action[0] === "hold" && action.length === 2) {
      var pad = -1;
      board.holds.forEach(function (h, index) { if (h.id === action[1]) pad = index; });
      if (pad === -1 || next.charges[pad] <= 0) return null;
      var cell = board.holds[pad].cell;
      for (i = 0; i < next.positions.length; i++) {
        var here = next.positions[i];
        if (here[0] === "on" && here[1] === cell[0] && here[2] === cell[1]) held = i;
      }
      if (held === -1) return null;    // nothing on the pad: an illegal hold
      next.charges[pad] -= 1;
    } else {
      return null;
    }

    var moved = next.positions.map(function (p) { return p.slice(); });
    for (i = 0; i < next.positions.length; i++) {
      var position = next.positions[i];
      if (position[0] !== "on" || i === held) continue;
      var row = position[1], column = position[2];
      var switchIndex = board.switchAt[key(row, column)];
      var delta = switchIndex === undefined ? 0
        : DELTAS[board.switches[switchIndex].states[next.switchIndex[switchIndex]]];
      row += delta;
      column += 1;
      if (row < 0 || row >= board.lanes) return null;
      if (column === board.columns) {
        if (board.exits[row] !== board.packets[i].kind) return null;
        moved[i] = ["done", row];
        continue;
      }
      if (board.blockers[key(row, column)]) return null;
      moved[i] = ["on", row, column];
    }

    var seen = {};
    for (i = 0; i < moved.length; i++) {
      if (moved[i][0] !== "on") continue;
      var at2 = key(moved[i][1], moved[i][2]);
      if (seen[at2]) return null;      // two packets on one cell: a collision
      seen[at2] = true;
    }
    next.positions = moved;
    next.tick += 1;
    return next;
  }

  function solved(state) {
    return state.positions.every(function (p) { return p[0] === "done"; });
  }

  // Exported for the Python/JS parity fixture test — mirrors _replay()'s walk.
  function replay(payload, actions) {
    var board = boardFrom(payload);
    var current = initialState(board);
    var steps = [];
    for (var i = 0; i < actions.length; i++) {
      current = step(board, current, actions[i]);
      if (current === null) {
        steps.push({ alive: false });
        return { steps: steps, alive: false, solved: false };
      }
      steps.push({
        alive: true, tick: current.tick,
        positions: current.positions.map(function (p) { return p.slice(); }),
        charges: current.charges.slice(),
        switchIndex: current.switchIndex.slice(),
      });
      if (solved(current)) return { steps: steps, alive: true, solved: true };
    }
    return { steps: steps, alive: true, solved: solved(current) };
  }

  // --- rendering ---------------------------------------------------------

  // The belt as the player should see it *now*: a turn spawns before the
  // player acts, so packets due this turn are already on the board when the
  // decision is made. Display only — the canonical state still spawns inside
  // step(), which skips packets that are already placed.
  function withDueSpawns(board, current) {
    var view = clone(current);
    var occupied = {};
    view.positions.forEach(function (p) {
      if (p[0] === "on") occupied[key(p[1], p[2])] = true;
    });
    board.packets.forEach(function (packet, index) {
      var at = key(packet.start[0], packet.start[1]);
      if (view.positions[index][0] === "wait" && packet.spawn === view.tick && !occupied[at]) {
        view.positions[index] = ["on", packet.start[0], packet.start[1]];
        occupied[at] = true;
      }
    });
    return view;
  }

  function nextArrow(index) {
    var position = state.view.positions[index];
    if (position[0] !== "on") return "";
    var at = state.board.switchAt[key(position[1], position[2])];
    if (at === undefined) return STATE_ARROW.straight;
    return STATE_ARROW[state.board.switches[at].states[state.view.switchIndex[at]]];
  }

  function render() {
    var board = state.board;
    var px = state.cell;
    state.view = withDueSpawns(board, state.current);
    var here = {};
    state.view.positions.forEach(function (position, index) {
      if (position[0] === "on") here[key(position[1], position[2])] = index;
    });

    state.grid.innerHTML = "";
    for (var row = 0; row < board.lanes; row++) {
      for (var column = -1; column <= board.columns; column++) {
        var el = document.createElement("div");
        var css = "border-radius:4px;display:flex;align-items:center;justify-content:center;" +
          "font-weight:900;line-height:1;font-size:" + Math.floor(px * 0.5) + "px;" +
          "background:" + T.cell + ";";

        if (column === -1) {
          // Staging: what is still queued for this lane, and on which turn it
          // arrives. Knowing what is coming is half the scheduling problem.
          var queued = [];
          board.packets.forEach(function (packet, index) {
            if (state.view.positions[index][0] === "wait" && packet.start[0] === row) {
              queued.push(packet);
            }
          });
          queued.sort(function (a, b) { return a.spawn - b.spawn; });
          el.style.cssText +=
            "display:flex;flex-direction:column;align-items:flex-end;justify-content:center;" +
            "gap:1px;font-weight:900;line-height:1.05;font-size:" +
            Math.max(9, Math.floor(px * 0.28)) + "px;background:transparent;";
          queued.forEach(function (packet) {
            var line = document.createElement("span");
            var queuedStyle = KIND_STYLE[packet.kind];
            line.textContent = queuedStyle.glyph + "t" + packet.spawn;
            line.style.cssText = "color:" + queuedStyle.colour + ";opacity:0.8;";
            line.title = packet.kind + " joins this lane on turn " + packet.spawn;
            el.appendChild(line);
          });
          state.grid.appendChild(el);
          continue;
        }

        if (column === board.columns) {
          // The exit column: what this lane accepts.
          var exitStyle = KIND_STYLE[board.exits[row]];
          css = css.replace("background:" + T.cell + ";",
            "background:" + T.bgDeep + ";") +
            "border:3px dashed " + exitStyle.colour + ";color:" + exitStyle.colour + ";";
          el.textContent = exitStyle.glyph;
          el.title = board.exits[row] + " exit";
        } else if (board.blockers[key(row, column)]) {
          css += "background:" + T.fade(T.hazard, 0.22) + ";color:" + T.hazard + ";";
          el.textContent = "✖";
          el.title = "Blocker";
        } else {
          var switchIndex = board.switchAt[key(row, column)];
          var padIndex = board.holdAt[key(row, column)];
          if (switchIndex !== undefined) {
            var switchSpec = board.switches[switchIndex];
            css += "background:" + T.fade(T.goal, 0.22) + ";color:" + T.goal +
              ";cursor:pointer;border:2px solid " + T.goal + ";";
            el.textContent = STATE_ARROW[switchSpec.states[state.view.switchIndex[switchIndex]]];
            el.title = "Junction " + (switchIndex + 1);
            bindAction(el, ["toggle", switchSpec.id]);
          } else if (padIndex !== undefined) {
            css += "background:" + T.fade(T.wall, 0.14) + ";color:" + T.text +
              ";border:2px dashed " + T.wall + ";";
            el.textContent = state.view.charges[padIndex] > 0 ? "⏸" : "·";
            el.title = "Hold pad " + board.holds[padIndex].id;
            bindAction(el, ["hold", board.holds[padIndex].id]);
          }
        }

        var packetIndex = here[key(row, column)];
        if (packetIndex !== undefined && column < board.columns) {
          var packetStyle = KIND_STYLE[board.packets[packetIndex].kind];
          css += "background:" + packetStyle.colour + ";color:" + T.ink +
            ";box-shadow:" + T.glow(packetStyle.colour, 10) + ";";
          // Keep a junction readable when a packet is standing on it — the
          // gold frame says "this arrow is one you can flip".
          if (board.switchAt[key(row, column)] !== undefined) {
            css += "border:3px solid " + T.goal + ";cursor:pointer;";
          }
          // The packet's own next edge — one tick only, never a longer preview.
          el.textContent = packetStyle.glyph + nextArrow(packetIndex);
          el.style.fontSize = Math.floor(px * 0.36) + "px";
        }
        el.style.cssText += css;
        state.grid.appendChild(el);
      }
    }

    state.board.switches.forEach(function (switchSpec, index) {
      var chip = state.chips[index];
      chip.textContent = (index + 1) + " " +
        STATE_ARROW[switchSpec.states[state.view.switchIndex[index]]];
      chip.disabled = state.busy || state.done || state.lost;
    });
    state.holdChips.forEach(function (chip, index) {
      var pad = state.board.holds[index];
      var position = key(pad.cell[0], pad.cell[1]);
      chip.disabled = state.busy || state.done || state.lost ||
        state.view.charges[index] <= 0 || here[position] === undefined;
      chip.textContent = "⏸" + state.view.charges[index];
    });
    state.passBtn.disabled = state.busy || state.done || state.lost;
    state.turnLabel.textContent =
      "Turn " + state.current.tick + " / " + board.turnCap;
    state.status.textContent = state.message;
  }

  function bindAction(el, action) {
    el.addEventListener("click", function () { act(action); });
  }

  function act(action) {
    if (!state || state.busy || state.done || state.lost) return;
    var after = step(state.board, state.current, action);
    state.actions.push(action);
    state.busy = true;
    // Phase rhythm: show the action landing, then play the movement tick.
    if (action[0] === "toggle") {
      var at = -1;
      state.board.switches.forEach(function (s, index) { if (s.id === action[1]) at = index; });
      if (at !== -1) {
        state.current.switchIndex[at] =
          (state.current.switchIndex[at] + 1) % state.board.switches[at].states.length;
      }
    }
    state.message = action[0] === "pass" ? "Belt advancing…" : "Locked in — belt advancing…";
    render();
    var advance = function () {
      state.busy = false;
      if (after === null) {
        state.lost = true;
        state.message = "💥 Packet lost — press RESTART to reset the belt.";
        render();
        return;
      }
      state.current = after;
      if (solved(after)) {
        state.done = true;
        state.message = "🎉 All packets sorted — submitting…";
        render();
        state.submitTimer = window.setTimeout(submit, 350);
        return;
      }
      if (after.tick >= state.board.turnCap) {
        state.lost = true;
        state.message = "⏳ Out of turns — press RESTART to try another order.";
      } else {
        state.message = "Your move.";
      }
      render();
    };
    if (state.reducedMotion) advance();
    else state.timer = window.setTimeout(advance, TICK_MS);
  }

  function submit() {
    if (!state || state.submitted) return;
    state.submitted = true;
    state.api.submit(JSON.stringify({ v: 1, actions: state.actions }));
  }

  function restart() {
    if (!state || state.busy) return;
    stopTimers();
    state.current = initialState(state.board);
    state.actions = [];
    state.done = false;
    state.lost = false;
    state.message = "Belt reset — the junctions are back where they started.";
    render();
  }

  function stopTimers() {
    if (state && state.timer) {
      window.clearTimeout(state.timer);
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
  window.RelayGames["lane_shift"] = {
    mount: function (container, puzzle, api) {
      var payload = puzzle.payload;
      var board = boardFrom(payload);
      var avail = Math.min(container.clientWidth || 520, 520);
      state = {
        payload: payload,
        board: board,
        api: api,
        current: initialState(board),
        view: initialState(board),
        actions: [],
        busy: false,
        done: false,
        lost: false,
        submitted: false,
        timer: null,
        submitTimer: null,
        chips: [],
        holdChips: [],
        message: "Your move.",
        cell: Math.max(22, Math.min(46, Math.floor((avail - 12) / (board.columns + 2)) - 3)),
        reducedMotion:
          !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches),
      };

      var root = document.createElement("div");

      state.turnLabel = document.createElement("div");
      state.turnLabel.style.cssText =
        "text-align:center;font-weight:800;font-size:0.85rem;margin-bottom:6px;";
      root.appendChild(state.turnLabel);

      state.grid = document.createElement("div");
      state.grid.style.cssText =
        "display:grid;grid-template-columns:repeat(" + (board.columns + 2) + "," +
        state.cell + "px);grid-auto-rows:" + state.cell +
        "px;gap:3px;justify-content:center;margin:0 auto;";
      state.grid.setAttribute("role", "img");
      state.grid.setAttribute(
        "aria-label",
        "Conveyor, " + board.lanes + " lanes by " + board.columns + " columns, " +
          board.packets.length + " packets, " + board.switches.length + " junctions."
      );
      root.appendChild(state.grid);

      // Numbered chips are the 44px+ tap targets and the keyboard path, since a
      // belt cell can be smaller than a comfortable touch target.
      var chipRow = document.createElement("div");
      chipRow.style.cssText =
        "display:flex;gap:8px;justify-content:center;margin-top:12px;flex-wrap:wrap;";
      board.switches.forEach(function (switchSpec, index) {
        var chip = makeButton("", "Toggle junction " + (index + 1), function () {
          act(["toggle", switchSpec.id]);
        });
        chip.style.minWidth = "60px";
        state.chips.push(chip);
        chipRow.appendChild(chip);
      });
      board.holds.forEach(function (pad, index) {
        var chip = makeButton("", "Hold the packet on pad " + (index + 1), function () {
          act(["hold", pad.id]);
        });
        chip.style.minWidth = "60px";
        chip.style.borderColor = T.wall;
        state.holdChips.push(chip);
        chipRow.appendChild(chip);
      });
      root.appendChild(chipRow);

      var controls = document.createElement("div");
      controls.style.cssText =
        "display:flex;gap:10px;justify-content:center;margin-top:10px;flex-wrap:wrap;";
      state.passBtn = makeButton("PASS ▶", "Pass this turn", function () { act(["pass"]); });
      state.passBtn.style.cssText +=
        "background:" + T.fade(T.goal, 0.75) + ";color:" + T.ink +
        ";border-color:" + T.goal + ";min-width:104px;";
      var restartBtn = makeButton("↺ RESTART", "Reset the belt", restart);
      var checkBtn = makeButton("CHECK", "Submit your schedule", submit);
      checkBtn.style.cssText += "background:" + T.goal + ";color:" + T.ink +
        ";border-color:" + T.goal + ";box-shadow:" + T.glow(T.goal, 14) + ";";
      controls.appendChild(state.passBtn);
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
        "Every turn you take one action — flip a junction (→ ↘ ↗), spend a hold pad (⏸), " +
        "or PASS — and then every packet advances one cell. Each packet must reach the " +
        "exit with its own shape. Two packets landing on one cell, a blocker (✖), the " +
        "wrong exit or falling off the belt all lose the run. Packets waiting to the left " +
        "show the turn they join the belt, and the little arrow on a packet shows only its " +
        "next step. No undo — RESTART resets the whole belt.";
      hint.style.cssText =
        "color:" + T.muted + ";font-size:0.85rem;margin:8px 0 0;";
      root.appendChild(hint);

      container.appendChild(root);
      state.root = root;

      state.keyHandler = function (event) {
        if (state.busy || state.done || state.lost) return;
        if (event.key === "Enter" || event.key === " " || event.key === "p") {
          event.preventDefault();
          act(["pass"]);
          return;
        }
        if (event.key === "r" || event.key === "R") {
          event.preventDefault();
          restart();
          return;
        }
        if (event.key === "h" || event.key === "H") {
          event.preventDefault();
          var pad = board.holds.find(function (hold, index) {
            return !state.holdChips[index].disabled;
          });
          if (pad) act(["hold", pad.id]);
          return;
        }
        var digit = parseInt(event.key, 10);
        if (digit >= 1 && digit <= board.switches.length) {
          event.preventDefault();
          act(["toggle", board.switches[digit - 1].id]);
        }
      };
      document.addEventListener("keydown", state.keyHandler);

      render();
    },

    unmount: function () {
      if (!state) return; // idempotent
      stopTimers();
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
