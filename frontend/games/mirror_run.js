// MIRROR RUN renderer — one control stream, two runners; Runner B obeys a
// transformed command (mirror/rotate/invert). Input: on-screen D-pad (touch),
// swipe on the boards, and arrow keys / WASD on desktop.
// Answer = JSON {"v":1,"moves":"URDL..."}. The server replays and validates.
(function () {
  "use strict";

  var DELTAS = { U: [-1, 0], R: [0, 1], D: [1, 0], L: [0, -1] };
  var MAPPINGS = {
    mirror_x: { U: "U", D: "D", L: "R", R: "L" },
    mirror_y: { U: "D", D: "U", L: "L", R: "R" },
    invert: { U: "D", D: "U", L: "R", R: "L" },
    rotate_cw: { U: "R", R: "D", D: "L", L: "U" },
    rotate_ccw: { U: "L", L: "D", D: "R", R: "U" },
  };
  var MAPPING_LABELS = {
    mirror_x: "⇄ B mirrors LEFT/RIGHT",
    mirror_y: "⇅ B mirrors UP/DOWN",
    invert: "⤨ B inverts EVERY move",
    rotate_cw: "↻ B rotates moves CLOCKWISE",
    rotate_ccw: "↺ B rotates moves COUNTER-CLOCKWISE",
  };
  var ARROWS = { U: "▲", R: "▶", D: "▼", L: "◀" };
  var KEYS = {
    ArrowUp: "U", ArrowRight: "R", ArrowDown: "D", ArrowLeft: "L",
    w: "U", d: "R", s: "D", a: "L", W: "U", D: "R", S: "D", A: "L",
  };

  var state = null;

  function wallKey(r, c) { return r + "," + c; }

  function wallsOf(board) {
    var set = {};
    board.walls.forEach(function (w) { set[wallKey(w[0], w[1])] = true; });
    return set;
  }

  function step(pos, cmd, walls, rows, cols) {
    var r = pos[0] + DELTAS[cmd][0], c = pos[1] + DELTAS[cmd][1];
    if (r < 0 || r >= rows || c < 0 || c >= cols) return pos;
    if (walls[wallKey(r, c)]) return pos;
    return [r, c];
  }

  function positions() {
    var p = state.payload;
    var a = p.boards[0].start.slice(), b = p.boards[1].start.slice();
    var map = MAPPINGS[p.mapping_b];
    state.moves.forEach(function (cmd) {
      a = step(a, cmd, state.wallsA, p.rows, p.cols);
      b = step(b, map[cmd], state.wallsB, p.rows, p.cols);
    });
    return [a, b];
  }

  function solvedAt(pos) {
    var p = state.payload;
    return pos[0][0] === p.boards[0].exit[0] && pos[0][1] === p.boards[0].exit[1] &&
           pos[1][0] === p.boards[1].exit[0] && pos[1][1] === p.boards[1].exit[1];
  }

  function drawBoard(panel, board, walls, runner, label, runnerColour) {
    var p = state.payload;
    panel.innerHTML = "";
    var title = document.createElement("div");
    title.textContent = label;
    title.style.cssText = "font-weight:800;font-size:0.8rem;margin-bottom:4px;text-align:center;";
    panel.appendChild(title);
    var grid = document.createElement("div");
    grid.style.cssText =
      "display:grid;grid-template-columns:repeat(" + p.cols + "," + state.cell + "px);" +
      "grid-auto-rows:" + state.cell + "px;gap:2px;";
    for (var r = 0; r < p.rows; r++) {
      for (var c = 0; c < p.cols; c++) {
        var cellEl = document.createElement("div");
        var css = "border-radius:4px;display:flex;align-items:center;justify-content:center;" +
          "font-weight:900;font-size:" + Math.floor(state.cell * 0.55) + "px;";
        if (walls[wallKey(r, c)]) {
          css += "background:#2b2b33;";
        } else {
          css += "background:#f0e8dc;";
        }
        var isExit = r === board.exit[0] && c === board.exit[1];
        var isRunner = r === runner[0] && c === runner[1];
        if (isExit) {
          css += "outline:3px dashed " + runnerColour + ";outline-offset:-3px;";
          cellEl.textContent = "⚑"; // flag marks the exit (shape, not colour)
          css += "color:" + runnerColour + ";";
        }
        if (isRunner) {
          cellEl.textContent = label.charAt(label.length - 1); // "A" / "B"
          css += "background:" + runnerColour + ";color:#fff;" +
            (state.reducedMotion ? "" : "transition:background 0.12s ease;");
        }
        cellEl.style.cssText = css;
        grid.appendChild(cellEl);
      }
    }
    panel.appendChild(grid);
  }

  function render() {
    var p = state.payload;
    var pos = positions();
    drawBoard(state.panelA, p.boards[0], state.wallsA, pos[0], "Runner A", "#4e79a7");
    drawBoard(state.panelB, p.boards[1], state.wallsB, pos[1], "Runner B", "#e15759");
    state.counter.textContent = "Moves: " + state.moves.length + " / " + p.move_cap;
    state.undoBtn.disabled = !state.moves.length || state.done;
    state.restartBtn.disabled = !state.moves.length || state.done;
  }

  function flash(cmd) {
    var map = MAPPINGS[state.payload.mapping_b];
    state.flashEl.textContent = "A " + ARROWS[cmd] + "   →   B " + ARROWS[map[cmd]];
    clearTimeout(state.flashTimer);
    state.flashTimer = setTimeout(function () {
      if (state) state.flashEl.textContent = "";
    }, 900);
  }

  function tryMove(cmd) {
    if (!state || state.done) return;
    if (state.moves.length >= state.payload.move_cap) {
      state.flashEl.textContent = "Move cap reached — Undo or Restart.";
      return;
    }
    state.moves.push(cmd);
    flash(cmd);
    render();
    if (solvedAt(positions())) {
      state.done = true;
      state.api.submit(JSON.stringify({ v: 1, moves: state.moves.join("") }));
    }
  }

  function makeButton(label, aria, handler) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = label;
    btn.setAttribute("aria-label", aria);
    btn.style.cssText =
      "min-width:52px;min-height:52px;font-size:1.3rem;font-weight:900;" +
      "border:2px solid #f0e8dc;border-radius:12px;background:#fff;cursor:pointer;";
    btn.addEventListener("click", handler);
    return btn;
  }

  window.RelayGames = window.RelayGames || {};
  window.RelayGames["mirror_run"] = {
    mount: function (container, puzzle, api) {
      var p = puzzle.payload;
      var reducedMotion = window.matchMedia &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      // Fit both boards side by side down to ~320px-wide screens.
      var avail = Math.min(container.clientWidth || 640, 640);
      var cell = Math.max(18, Math.min(40, Math.floor((avail - 60) / (p.cols * 2))));
      state = {
        payload: p, api: api, moves: [], done: false, cell: cell,
        reducedMotion: reducedMotion,
        wallsA: wallsOf(p.boards[0]), wallsB: wallsOf(p.boards[1]),
        flashTimer: null,
      };

      var root = document.createElement("div");

      var badge = document.createElement("div");
      badge.textContent = MAPPING_LABELS[p.mapping_b] || p.mapping_b;
      badge.style.cssText =
        "text-align:center;font-weight:800;background:#8338ec;color:#fff;" +
        "border-radius:999px;padding:6px 14px;margin-bottom:10px;font-size:0.9rem;";
      root.appendChild(badge);

      var boardsRow = document.createElement("div");
      boardsRow.style.cssText =
        "display:flex;gap:12px;justify-content:center;flex-wrap:wrap;touch-action:none;";
      state.panelA = document.createElement("div");
      state.panelB = document.createElement("div");
      boardsRow.appendChild(state.panelA);
      boardsRow.appendChild(state.panelB);
      root.appendChild(boardsRow);

      state.flashEl = document.createElement("div");
      state.flashEl.style.cssText =
        "text-align:center;font-weight:800;min-height:1.4em;margin:6px 0;color:#8338ec;";
      root.appendChild(state.flashEl);

      state.counter = document.createElement("div");
      state.counter.style.cssText = "text-align:center;font-weight:700;font-size:0.85rem;color:#8a8a96;";
      root.appendChild(state.counter);

      // D-pad (touch-first) + Undo/Restart. Keyboard works too (arrows/WASD).
      var controls = document.createElement("div");
      controls.style.cssText =
        "display:flex;gap:18px;align-items:center;justify-content:center;margin-top:10px;flex-wrap:wrap;";
      var pad = document.createElement("div");
      pad.style.cssText =
        "display:grid;grid-template-columns:repeat(3,56px);grid-auto-rows:56px;gap:4px;justify-content:center;";
      var cellsPad = [null, "U", null, "L", null, "R", null, "D", null];
      cellsPad.forEach(function (dir) {
        if (!dir) {
          pad.appendChild(document.createElement("span"));
          return;
        }
        pad.appendChild(makeButton(ARROWS[dir], "Move " + dir, function () { tryMove(dir); }));
      });
      controls.appendChild(pad);

      var side = document.createElement("div");
      side.style.cssText = "display:flex;flex-direction:column;gap:8px;";
      state.undoBtn = makeButton("↩", "Undo last move", function () {
        if (state.moves.length && !state.done) { state.moves.pop(); render(); }
      });
      state.restartBtn = makeButton("↺", "Restart from the beginning", function () {
        if (!state.done) { state.moves = []; render(); }
      });
      side.appendChild(state.undoBtn);
      side.appendChild(state.restartBtn);
      controls.appendChild(side);
      root.appendChild(controls);

      var hint = document.createElement("p");
      hint.textContent =
        "Every move drives BOTH runners — get each onto its ⚑ at the same time. " +
        "Blocked runners stay put (use walls to split them up!). Swipe, tap the pad, or use arrow keys.";
      hint.style.cssText = "color:#8a8a96;font-size:0.85rem;margin:10px 0 0;";
      root.appendChild(hint);

      container.appendChild(root);
      state.root = root;

      // Keyboard (desktop).
      state.keyHandler = function (event) {
        var dir = KEYS[event.key];
        if (!dir) return;
        event.preventDefault();
        tryMove(dir);
      };
      document.addEventListener("keydown", state.keyHandler);

      // Swipe (phones): direction from the dominant axis of the gesture.
      state.touchStart = null;
      state.touchStartHandler = function (event) {
        if (event.touches.length === 1) {
          state.touchStart = [event.touches[0].clientX, event.touches[0].clientY];
        }
      };
      state.touchEndHandler = function (event) {
        if (!state.touchStart) return;
        var dx = event.changedTouches[0].clientX - state.touchStart[0];
        var dy = event.changedTouches[0].clientY - state.touchStart[1];
        state.touchStart = null;
        if (Math.max(Math.abs(dx), Math.abs(dy)) < 24) return; // a tap, not a swipe
        if (Math.abs(dx) > Math.abs(dy)) tryMove(dx > 0 ? "R" : "L");
        else tryMove(dy > 0 ? "D" : "U");
      };
      boardsRow.addEventListener("touchstart", state.touchStartHandler, { passive: true });
      boardsRow.addEventListener("touchend", state.touchEndHandler, { passive: true });
      state.boardsRow = boardsRow;

      render();
    },

    unmount: function () {
      if (!state) return; // idempotent
      document.removeEventListener("keydown", state.keyHandler);
      state.boardsRow.removeEventListener("touchstart", state.touchStartHandler);
      state.boardsRow.removeEventListener("touchend", state.touchEndHandler);
      clearTimeout(state.flashTimer);
      if (state.root && state.root.parentNode) {
        state.root.parentNode.removeChild(state.root);
      }
      state = null;
    },
  };
})();
