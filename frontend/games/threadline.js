// THREADLINE renderer — draw one cable from START to END through the numbered
// anchors in order, inside the bend budget.
// Three ways in, because a route is fiddly on a phone: tap a neighbouring cell
// to extend, drag across the grid to trace, or use the arrow keys. Tapping a
// cell the cable already covers rewinds to it; UNDO drops the last step.
// Every extension is checked through `validate` — the same walk the server
// runs, mirrored here so an illegal step is refused where it happens rather
// than at submit time. Answer = JSON {"v":1,"path":[[r,c],...]}; the server
// re-walks it and is the only authority on success.
(function () {
  "use strict";

  var T = window.RelayTheme;

  // Sides of a cell and the step that crosses each one (mirror of
  // backend/games/game10_threadline.py — same names, same meaning).
  var STEPS = { n: [-1, 0], s: [1, 0], e: [0, 1], w: [0, -1] };
  var OPPOSITE = { n: "s", s: "n", e: "w", w: "e" };

  var COLOR = {
    cable: T.accent, cableSoft: T.fade(T.accent, 0.45),
    cell: T.cell, grid: T.grid,
    blocked: T.bgDeep, anchor: T.goal, done: T.good, socket: T.sideA,
    port: T.hazard,
  };

  // Why a step was refused, in the player's words. Keyed by the reason strings
  // `validate` returns.
  var REFUSALS = {
    blocked: "That cell is blocked.",
    revisit: "The cable can't cross itself.",
    not_adjacent: "Tap a cell next to the cable head.",
    out_of_bounds: "That's off the board.",
    too_many_bends: "No bends left — rewind and straighten a corner.",
    too_long: "The cable is at its full length.",
    anchor_out_of_order: "Anchors have to be taken in order.",
    anchor_port: "That anchor has to be entered or left through its marked side.",
    bad_start: "The cable starts at START.",
    bad_shape: "That isn't a route.",
  };

  var state = null;

  // --- validation (mirror of backend/games/game10_threadline.py) ------------

  function sideOfStep(from, to) {
    var dr = to[0] - from[0], dc = to[1] - from[1];
    for (var side in STEPS) {
      if (Object.prototype.hasOwnProperty.call(STEPS, side) &&
          STEPS[side][0] === dr && STEPS[side][1] === dc) {
        return side;
      }
    }
    return null;
  }

  function keyOf(cell) { return cell[0] + "," + cell[1]; }

  function asCells(path) {
    if (!Array.isArray(path) || path.length === 0) return null;
    var cells = [];
    for (var i = 0; i < path.length; i++) {
      var item = path[i];
      if (!Array.isArray(item) || item.length !== 2) return null;
      if (typeof item[0] !== "number" || typeof item[1] !== "number") return null;
      if (item[0] !== Math.floor(item[0]) || item[1] !== Math.floor(item[1])) return null;
      cells.push([item[0], item[1]]);
    }
    return cells;
  }

  // `partial` drops the two end-of-route rules (finish on END, visit every
  // anchor) so a half-drawn cable can be asked the same question.
  function validate(payload, path, partial) {
    var cells = asCells(path);
    if (cells === null) {
      return { ok: false, reason: "bad_shape", edges: 0, bends: 0, anchors_visited: 0 };
    }
    var edges = cells.length - 1;
    var bends = 0, visited = 0;

    function report(ok, reason) {
      return { ok: ok, reason: reason, edges: edges, bends: bends, anchors_visited: visited };
    }

    var blocked = {};
    payload.blocked_cells.forEach(function (cell) { blocked[keyOf(cell)] = true; });
    var ordered = payload.anchors.slice().sort(function (a, b) { return a.order - b.order; });
    var anchorAt = {};
    ordered.forEach(function (anchor) { anchorAt[keyOf(anchor.cell)] = anchor; });

    if (edges > payload.edge_cap) return report(false, "too_long");
    if (keyOf(cells[0]) !== keyOf(payload.start)) return report(false, "bad_start");

    var seen = {};
    var heading = null;       // side crossed by the previous step
    var pendingExit = null;   // port the current anchor must be left by
    for (var index = 0; index < cells.length; index++) {
      var cell = cells[index];
      if (cell[0] < 0 || cell[0] >= payload.rows || cell[1] < 0 || cell[1] >= payload.cols) {
        return report(false, "out_of_bounds");
      }
      if (blocked[keyOf(cell)]) return report(false, "blocked");
      // One rule for three failures: self-crossing, edge reuse and the
      // 180-degree reversal are all a cell visited twice.
      if (seen[keyOf(cell)]) return report(false, "revisit");
      seen[keyOf(cell)] = true;

      var entry = null;
      if (index) {
        var side = sideOfStep(cells[index - 1], cell);
        if (side === null) return report(false, "not_adjacent");
        if (pendingExit !== null && side !== pendingExit) return report(false, "anchor_port");
        if (heading !== null && side !== heading) {
          bends += 1;
          if (bends > payload.bend_cap) return report(false, "too_many_bends");
        }
        pendingExit = null;
        heading = side;
        entry = OPPOSITE[side];
      }

      var anchor = anchorAt[keyOf(cell)];
      if (anchor) {
        if (anchor.order !== visited) return report(false, "anchor_out_of_order");
        if (anchor.entry !== null && anchor.entry !== entry) return report(false, "anchor_port");
        visited += 1;
        pendingExit = anchor.exit;
      }
    }

    if (partial) return report(true, "");
    if (pendingExit !== null) return report(false, "anchor_port");
    if (keyOf(cells[cells.length - 1]) !== keyOf(payload.end)) return report(false, "not_at_end");
    if (visited !== ordered.length) return report(false, "missing_anchor");
    return report(true, "");
  }

  // --- board -------------------------------------------------------------

  function cellRole(payload, cell) {
    var key = keyOf(cell);
    if (key === keyOf(payload.start)) return { kind: "start", label: "S" };
    if (key === keyOf(payload.end)) return { kind: "end", label: "E" };
    var found = null;
    payload.anchors.forEach(function (anchor) {
      if (keyOf(anchor.cell) === key) found = anchor;
    });
    if (found) {
      return { kind: "anchor", label: String(found.order + 1), anchor: found };
    }
    for (var i = 0; i < payload.blocked_cells.length; i++) {
      if (keyOf(payload.blocked_cells[i]) === key) return { kind: "blocked", label: "✕" };
    }
    return { kind: "open", label: "" };
  }

  // A port marker: a bar drawn on the side of the anchor the cable must cross.
  function portBar(side, into) {
    var bar = document.createElement("div");
    var thick = "4px";
    var css = "position:absolute;background:" + COLOR.port + ";border-radius:2px;";
    if (side === "n") css += "top:0;left:18%;right:18%;height:" + thick + ";";
    if (side === "s") css += "bottom:0;left:18%;right:18%;height:" + thick + ";";
    if (side === "w") css += "left:0;top:18%;bottom:18%;width:" + thick + ";";
    if (side === "e") css += "right:0;top:18%;bottom:18%;width:" + thick + ";";
    bar.style.cssText = css;
    bar.setAttribute("aria-hidden", "true");
    bar.setAttribute("title", (into ? "enter" : "leave") + " through this side");
    return bar;
  }

  function describe(payload, cell, role) {
    var where = "row " + (cell[0] + 1) + " column " + (cell[1] + 1);
    if (role.kind === "start") return "Start socket, " + where;
    if (role.kind === "end") return "End socket, " + where;
    if (role.kind === "blocked") return "Blocked, " + where;
    if (role.kind === "anchor") {
      var text = "Anchor " + (role.anchor.order + 1) + ", " + where;
      if (role.anchor.entry) text += ", enter through the " + role.anchor.entry + " side";
      if (role.anchor.exit) text += ", leave through the " + role.anchor.exit + " side";
      return text;
    }
    return "Empty cell, " + where;
  }

  // --- drawing -----------------------------------------------------------

  function cableSvg() {
    var unit = state.unit;
    var points = state.path.map(function (cell) {
      return ((cell[1] + 0.5) * unit).toFixed(1) + "," + ((cell[0] + 0.5) * unit).toFixed(1);
    }).join(" ");
    var head = state.path[state.path.length - 1];
    return '<svg viewBox="0 0 ' + state.payload.cols * unit + " " + state.payload.rows * unit +
      '" width="100%" height="100%" style="position:absolute;inset:0;pointer-events:none;" ' +
      'aria-hidden="true">' +
      (state.path.length > 1
        ? '<polyline points="' + points + '" fill="none" stroke="' + COLOR.cable +
          '" stroke-width="' + (unit * 0.34).toFixed(1) +
          '" stroke-linejoin="round" stroke-linecap="round" opacity="0.85"/>'
        : "") +
      '<circle cx="' + ((head[1] + 0.5) * unit).toFixed(1) + '" cy="' +
      ((head[0] + 0.5) * unit).toFixed(1) + '" r="' + (unit * 0.2).toFixed(1) +
      '" fill="' + COLOR.cable + '"/></svg>';
  }

  function render() {
    var payload = state.payload;
    var walk = validate(payload, state.path, true);
    var onPath = {};
    state.path.forEach(function (cell, index) { onPath[keyOf(cell)] = index; });
    var head = state.path[state.path.length - 1];

    state.cells.forEach(function (button) {
      var role = button._role;
      var covered = onPath[keyOf(button._cell)] !== undefined;
      var isHead = keyOf(button._cell) === keyOf(head);
      var background = COLOR.cell;
      if (role.kind === "blocked") background = COLOR.blocked;
      else if (covered) background = COLOR.cableSoft;
      else if (role.kind === "anchor") {
        background = role.anchor.order < walk.anchors_visited ? COLOR.done : COLOR.anchor;
      } else if (role.kind === "start" || role.kind === "end") background = COLOR.socket;
      button.style.cssText = state.cellCss +
        "background:" + background + ";" +
        "color:" + (role.kind === "blocked" ? T.muted
          : role.kind === "start" || role.kind === "end" ? T.ink
          : T.ink) + ";" +
        (isHead ? "box-shadow:inset 0 0 0 3px " + COLOR.cable + ";" : "");
      button.disabled = state.done;
    });

    state.cable.innerHTML = cableSvg();

    var next = walk.anchors_visited < payload.anchors.length
      ? "Anchor " + (walk.anchors_visited + 1) + " of " + payload.anchors.length
      : "END socket";
    state.readout.innerHTML =
      '<span style="font-weight:900;">Next: ' + next + "</span>" +
      '<span style="margin-left:14px;">Bends ' + walk.bends + " / " + payload.bend_cap + "</span>" +
      '<span style="margin-left:14px;">Length ' + walk.edges + " / " + payload.edge_cap + "</span>";
    state.undoBtn.disabled = state.done || state.path.length < 2;
    state.restartBtn.disabled = state.done;
    state.checkBtn.disabled = state.done || state.path.length < 2;
    state.status.textContent = state.message;
  }

  // --- interaction -------------------------------------------------------

  function finished() {
    return validate(state.payload, state.path, false).ok;
  }

  function extend(cell) {
    if (state.done) return;
    var ahead = state.path.concat([[cell[0], cell[1]]]);
    var walk = validate(state.payload, ahead, true);
    if (!walk.ok) {
      state.message = REFUSALS[walk.reason] || "That step isn't allowed.";
      render();
      return;
    }
    state.path = ahead;
    if (finished()) {
      state.done = true;
      state.message = "🎉 Cable complete — submitting…";
      render();
      state.submitTimer = window.setTimeout(submit, 300);
      return;
    }
    state.message = "";
    render();
  }

  // Tapping a cell the cable already covers rewinds to it — the spec's local
  // backtracking, and the only way to undo by touch alone.
  function rewindTo(index) {
    if (state.done || index < 0 || index >= state.path.length - 1) return;
    state.path = state.path.slice(0, index + 1);
    state.message = "Rewound.";
    render();
  }

  function touch(cell) {
    var at = -1;
    state.path.forEach(function (had, index) {
      if (keyOf(had) === keyOf(cell)) at = index;
    });
    if (at !== -1) {
      rewindTo(at);
      return;
    }
    extend(cell);
  }

  function step(side) {
    var head = state.path[state.path.length - 1];
    touch([head[0] + STEPS[side][0], head[1] + STEPS[side][1]]);
  }

  function undo() {
    if (state.done || state.path.length < 2) return;
    state.path = state.path.slice(0, state.path.length - 1);
    state.message = "Rewound one step.";
    render();
  }

  function restart() {
    if (state.done) return;
    state.path = [[state.payload.start[0], state.payload.start[1]]];
    state.message = "Back to START.";
    render();
  }

  function submit() {
    if (!state || state.submitted) return;
    state.submitted = true;
    state.api.submit(JSON.stringify({ v: 1, path: state.path }));
  }

  // --- lifecycle ---------------------------------------------------------

  function makeButton(label, aria, handler) {
    var button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.setAttribute("aria-label", aria);
    button.style.cssText =
      "min-width:84px;min-height:48px;font-size:0.95rem;font-weight:900;" +
      "border:2px solid " + COLOR.cable + ";border-radius:12px;" +
      "background:" + T.cell + ";color:" + T.text + ";cursor:pointer;";
    button.addEventListener("click", handler);
    return button;
  }

  window.RelayGames = window.RelayGames || {};
  window.RelayGames["threadline"] = {
    mount: function (container, puzzle, api) {
      var payload = puzzle.payload;
      var avail = Math.min(container.clientWidth || 460, 460);
      var unit = Math.max(30, Math.min(52, Math.floor(avail / payload.cols)));
      state = {
        payload: payload,
        api: api,
        path: [[payload.start[0], payload.start[1]]],
        cells: [],
        unit: unit,
        done: false,
        submitted: false,
        dragging: false,
        submitTimer: null,
        message: "",
        cellCss:
          "position:relative;width:" + unit + "px;height:" + unit + "px;padding:0;" +
          "border:1px solid " + COLOR.grid + ";border-radius:6px;font-weight:900;" +
          "font-size:" + Math.floor(unit * 0.42) + "px;line-height:1;cursor:pointer;" +
          "touch-action:none;",
      };

      var root = document.createElement("div");

      state.readout = document.createElement("div");
      state.readout.style.cssText =
        "text-align:center;font-weight:700;font-size:0.85rem;margin-bottom:8px;";
      root.appendChild(state.readout);

      // The grid holds the cells; the cable is one SVG laid over the top, so a
      // redraw never has to reason about which cell owns which segment.
      var board = document.createElement("div");
      board.style.cssText = "position:relative;width:" + unit * payload.cols +
        "px;margin:0 auto;";
      var grid = document.createElement("div");
      grid.style.cssText = "display:grid;grid-template-columns:repeat(" +
        payload.cols + "," + unit + "px);gap:0;";
      for (var row = 0; row < payload.rows; row++) {
        for (var col = 0; col < payload.cols; col++) {
          var cell = [row, col];
          var role = cellRole(payload, cell);
          var button = document.createElement("button");
          button.type = "button";
          button.textContent = role.label;
          button.setAttribute("aria-label", describe(payload, cell, role));
          button._cell = cell;
          button._role = role;
          if (role.kind === "anchor") {
            if (role.anchor.entry) button.appendChild(portBar(role.anchor.entry, true));
            if (role.anchor.exit) button.appendChild(portBar(role.anchor.exit, false));
          }
          (function (target) {
            button.addEventListener("click", function () { touch(target); });
            button.addEventListener("pointerdown", function (event) {
              if (event && event.preventDefault) event.preventDefault();
              state.dragging = true;
              touch(target);
            });
            button.addEventListener("pointerenter", function () {
              if (state.dragging) touch(target);
            });
          })(cell);
          state.cells.push(button);
          grid.appendChild(button);
        }
      }
      board.appendChild(grid);
      state.cable = document.createElement("div");
      state.cable.style.cssText = "position:absolute;inset:0;pointer-events:none;";
      board.appendChild(state.cable);
      root.appendChild(board);

      var controls = document.createElement("div");
      controls.style.cssText =
        "display:flex;gap:10px;justify-content:center;margin-top:12px;flex-wrap:wrap;";
      state.undoBtn = makeButton("↶ UNDO", "Remove the last step", undo);
      state.restartBtn = makeButton("↺ RESTART", "Back to the start socket", restart);
      state.checkBtn = makeButton("CHECK", "Submit this cable", submit);
      state.checkBtn.style.cssText += "background:" + T.goal + ";color:" + T.ink +
        ";border-color:" + T.goal + ";box-shadow:" + T.glow(T.goal, 14) + ";";
      controls.appendChild(state.undoBtn);
      controls.appendChild(state.restartBtn);
      controls.appendChild(state.checkBtn);
      root.appendChild(controls);

      state.status = document.createElement("p");
      state.status.setAttribute("role", "status");
      state.status.style.cssText =
        "text-align:center;font-weight:700;font-size:0.9rem;margin:10px 0 0;min-height:1.2em;";
      root.appendChild(state.status);

      var hint = document.createElement("p");
      hint.textContent =
        "Run the cable from S to E through the numbered anchors in order. Tap a " +
        "neighbouring cell to extend, drag to trace, or use the arrow keys; tap a " +
        "cell the cable already covers to rewind to it. The cable can't cross " +
        "itself or enter a blocked cell, and every corner spends one of your " +
        "bends. A red bar on an anchor is a port: the cable has to cross that " +
        "side. Keys: arrows to draw, Backspace to undo, r to start over.";
      hint.style.cssText =
        "color:" + T.muted + ";font-size:0.85rem;margin:8px 0 0;";
      root.appendChild(hint);

      container.appendChild(root);
      state.root = root;

      state.keyHandler = function (event) {
        if (state.done) return;
        var key = typeof event.key === "string" ? event.key : "";
        var arrows = { ArrowUp: "n", ArrowDown: "s", ArrowLeft: "w", ArrowRight: "e" };
        if (arrows[key]) {
          event.preventDefault();
          step(arrows[key]);
        } else if (key === "Backspace" || key === "Delete") {
          event.preventDefault();
          undo();
        } else if (key === "r" || key === "R") {
          event.preventDefault();
          restart();
        }
      };
      state.upHandler = function () { state.dragging = false; };
      document.addEventListener("keydown", state.keyHandler);
      document.addEventListener("pointerup", state.upHandler);
      document.addEventListener("pointercancel", state.upHandler);

      render();
    },

    unmount: function () {
      if (!state) return; // idempotent
      if (state.submitTimer) window.clearTimeout(state.submitTimer);
      document.removeEventListener("keydown", state.keyHandler);
      document.removeEventListener("pointerup", state.upHandler);
      document.removeEventListener("pointercancel", state.upHandler);
      if (state.root && state.root.parentNode) {
        state.root.parentNode.removeChild(state.root);
      }
      state = null;
    },

    // Test hook: the shared route walk, with no DOM involved.
    __validate: validate,
  };
})();
