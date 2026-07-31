// SHADOW CAST renderer — turn the block until both of its shadows land.
// Six quarter-turn buttons (X/Y/Z, each way), keyboard x/y/z with Shift for the
// inverse, `r` to restart. The isometric view is drawn from the live voxel set,
// never from a stored picture, and the two live shadow grids sit next to their
// targets so the player is always comparing like with like.
// Answer = JSON {"v":1,"turns":["x+","z-",...]} — replayed by the server, which
// is the only authority on success.
(function () {
  "use strict";

  // The six legal actions, in the fixed order that seeds the orientation table
  // (mirror of backend/games/game9_shadow_cast.py — the same order, or
  // `initial_orientation` would mean something different here).
  var TURNS = ["x+", "x-", "y+", "y-", "z+", "z-"];

  var MATRICES = {
    "x+": [[1, 0, 0], [0, 0, -1], [0, 1, 0]],
    "x-": [[1, 0, 0], [0, 0, 1], [0, -1, 0]],
    "y+": [[0, 0, 1], [0, 1, 0], [-1, 0, 0]],
    "y-": [[0, 0, -1], [0, 1, 0], [1, 0, 0]],
    "z+": [[0, -1, 0], [1, 0, 0], [0, 0, 1]],
    "z-": [[0, 1, 0], [-1, 0, 0], [0, 0, 1]],
  };

  var IDENTITY = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];

  var TURN_MS = 240;                 // brief rotation, then snap to the pose

  // Three shades per cube so the solid reads without relying on colour, plus a
  // dark outline on every face.
  var FACE = { top: "#a8ded7", front: "#2a9d8f", side: "#1d6f66" };
  var EDGE = "#123f3a";

  var state = null;

  // --- simulation (mirror of backend/games/game9_shadow_cast.py) -----------

  function turnMatrix(token) {
    // hasOwnProperty, not a truthiness test: "constructor" would otherwise
    // resolve through the prototype and hand back a function.
    return Object.prototype.hasOwnProperty.call(MATRICES, token)
      ? MATRICES[token] : null;
  }

  function mul(left, right) {
    var out = [];
    for (var row = 0; row < 3; row++) {
      var line = [];
      for (var col = 0; col < 3; col++) {
        line.push(
          left[row][0] * right[0][col] +
          left[row][1] * right[1][col] +
          left[row][2] * right[2][col]
        );
      }
      out.push(line);
    }
    return out;
  }

  function matrixKey(matrix) {
    return matrix[0].join(",") + "|" + matrix[1].join(",") + "|" + matrix[2].join(",");
  }

  // The 24 proper orientations, breadth-first from the identity over TURNS.
  // Index 0 is the identity; the payload's `initial_orientation` indexes here.
  function buildOrientations() {
    var order = [IDENTITY];
    var seen = {};
    seen[matrixKey(IDENTITY)] = true;
    var head = 0;
    while (head < order.length) {
      var current = order[head++];
      for (var i = 0; i < TURNS.length; i++) {
        var after = mul(MATRICES[TURNS[i]], current);
        var key = matrixKey(after);
        if (!seen[key]) {
          seen[key] = true;
          order.push(after);
        }
      }
    }
    return order;
  }

  var ORIENTATIONS = buildOrientations();

  // Slide into the nonnegative corner and sort — a pure translation, so two
  // orientations compare equal exactly when they are the same pose.
  function normalise(cells) {
    var lows = [Infinity, Infinity, Infinity];
    cells.forEach(function (cell) {
      for (var axis = 0; axis < 3; axis++) {
        if (cell[axis] < lows[axis]) lows[axis] = cell[axis];
      }
    });
    return cells.map(function (cell) {
      return [cell[0] - lows[0], cell[1] - lows[1], cell[2] - lows[2]];
    }).sort(function (a, b) {
      return (a[0] - b[0]) || (a[1] - b[1]) || (a[2] - b[2]);
    });
  }

  function transform(shape, matrix) {
    return normalise(shape.map(function (cell) {
      return [
        matrix[0][0] * cell[0] + matrix[0][1] * cell[1] + matrix[0][2] * cell[2],
        matrix[1][0] * cell[0] + matrix[1][1] * cell[1] + matrix[1][2] * cell[2],
        matrix[2][0] * cell[0] + matrix[2][1] * cell[1] + matrix[2][2] * cell[2],
      ];
    }));
  }

  // FRONT reads columns as x and rows bottom-to-top as z; TOP reads columns as
  // x and rows bottom-to-top as y. Same formula shape, so the two grids share
  // their column axis and neither can be transposed on its own.
  function project(shape, bound) {
    var front = [], top = [], row;
    for (row = 0; row < bound; row++) {
      front.push(new Array(bound + 1).join("0").split(""));
      top.push(new Array(bound + 1).join("0").split(""));
    }
    shape.forEach(function (cell) {
      front[bound - 1 - cell[2]][cell[0]] = "1";
      top[bound - 1 - cell[1]][cell[0]] = "1";
    });
    return {
      front: front.map(function (line) { return line.join(""); }),
      top: top.map(function (line) { return line.join(""); }),
    };
  }

  function matches(shadows, payload) {
    return shadows.front.join("/") === payload.targets.front.join("/") &&
      shadows.top.join("/") === payload.targets.top.join("/");
  }

  // Exported for the Python/JS parity fixture test — mirrors replay()'s walk.
  function replay(payload, turns) {
    var shape = payload.voxels.map(function (cell) {
      return [cell[0], cell[1], cell[2]];
    });
    var matrix = ORIENTATIONS[payload.initial_orientation];
    var steps = [];
    var legal = true;
    for (var i = 0; i < turns.length; i++) {
      var applied = turnMatrix(turns[i]);
      if (applied === null) { legal = false; break; }
      matrix = mul(applied, matrix);
      var oriented = transform(shape, matrix);
      var shadows = project(oriented, payload.bound);
      steps.push({
        voxels: oriented.map(function (cell) { return cell.slice(); }),
        front: shadows.front.slice(),
        top: shadows.top.slice(),
        matched: matches(shadows, payload),
      });
    }
    return {
      steps: steps,
      legal: legal,
      matched: !!(legal && steps.length && steps[steps.length - 1].matched),
    };
  }

  // --- rendering ---------------------------------------------------------

  // Isometric basis: +x goes right-and-down, +y goes right-and-up (away from
  // the viewer), +z goes straight up. The camera therefore sits on the object's
  // -y side, which is the same side the FRONT silhouette is read from.
  function isoPoint(x, y, z, unit, bound) {
    return [
      (x + y) * unit,
      1.5 * bound * unit + (x - y) * unit / 2 - z * unit,
    ];
  }

  function facePoints(corners, unit, bound) {
    return corners.map(function (corner) {
      var point = isoPoint(corner[0], corner[1], corner[2], unit, bound);
      return point[0].toFixed(1) + "," + point[1].toFixed(1);
    }).join(" ");
  }

  function polygon(corners, fill, unit, bound) {
    return '<polygon points="' + facePoints(corners, unit, bound) + '" fill="' + fill +
      '" stroke="' + EDGE + '" stroke-width="1.4" stroke-linejoin="round"/>';
  }

  function isoSvg(shape, bound, unit) {
    var size = 2 * bound * unit;
    // Painter's algorithm: the camera is at large x, small y, large z, so
    // bigger (x - y + z) is nearer and must be drawn last.
    var ordered = shape.slice().sort(function (a, b) {
      return (a[0] - a[1] + a[2]) - (b[0] - b[1] + b[2]);
    });
    var parts = [];
    ordered.forEach(function (cell) {
      var x = cell[0], y = cell[1], z = cell[2];
      // Only the three faces that can be seen from this camera.
      parts.push(polygon([
        [x, y, z], [x + 1, y, z], [x + 1, y, z + 1], [x, y, z + 1],
      ], FACE.front, unit, bound));
      parts.push(polygon([
        [x + 1, y, z], [x + 1, y + 1, z], [x + 1, y + 1, z + 1], [x + 1, y, z + 1],
      ], FACE.side, unit, bound));
      parts.push(polygon([
        [x, y, z + 1], [x + 1, y, z + 1], [x + 1, y + 1, z + 1], [x, y + 1, z + 1],
      ], FACE.top, unit, bound));
    });
    return '<svg viewBox="0 0 ' + size + " " + size + '" width="100%" ' +
      'style="max-width:' + size + 'px;display:block;margin:0 auto;overflow:visible" ' +
      'role="img" aria-label="The block, ' + shape.length + ' cubes.">' +
      parts.join("") + "</svg>";
  }

  // A silhouette grid. `against` (optional) is the grid this one is being
  // compared with: cells that disagree get a glyph, so a mismatch is never
  // signalled by colour alone.
  function gridHtml(rows, against, cell) {
    var bound = rows.length;
    var html = '<div style="display:grid;grid-template-columns:repeat(' + bound + "," +
      cell + 'px);grid-auto-rows:' + cell + 'px;gap:2px;justify-content:center;">';
    for (var row = 0; row < bound; row++) {
      for (var col = 0; col < bound; col++) {
        var on = rows[row].charAt(col) === "1";
        var wrong = against && against[row].charAt(col) !== rows[row].charAt(col);
        var css = "border-radius:3px;display:flex;align-items:center;justify-content:center;" +
          "font-weight:900;line-height:1;font-size:" + Math.floor(cell * 0.6) + "px;" +
          "background:" + (on ? "#2b2b33" : "#f0e8dc") + ";color:#ff6b6b;";
        if (wrong) css += "outline:2px solid #ff6b6b;outline-offset:-2px;";
        html += '<div style="' + css + '">' + (wrong ? "✕" : "") + "</div>";
      }
    }
    return html + "</div>";
  }

  function shadowPanel(title, target, live, cell) {
    var ok = target.join("/") === live.join("/");
    var off = 0;
    target.forEach(function (row, index) {
      for (var col = 0; col < row.length; col++) {
        if (row.charAt(col) !== live[index].charAt(col)) off++;
      }
    });
    var label = ok ? "✓ matched" : "✗ " + off + " cell" + (off === 1 ? "" : "s") + " off";
    return '<div style="display:flex;flex-direction:column;align-items:center;gap:4px;">' +
      '<div style="font-weight:900;font-size:0.75rem;letter-spacing:0.05em;">' + title + "</div>" +
      '<div style="font-size:0.68rem;color:#8a8a96;font-weight:700;">TARGET</div>' +
      gridHtml(target, null, cell) +
      '<div style="font-size:0.68rem;color:#8a8a96;font-weight:700;margin-top:2px;">NOW</div>' +
      gridHtml(live, target, cell) +
      '<div style="font-size:0.72rem;font-weight:800;color:' +
      (ok ? "#2a9d8f" : "#8a8a96") + ';">' + label + "</div>" +
      "</div>";
  }

  function render() {
    var payload = state.payload;
    var shape = transform(state.shape, state.matrix);
    var shadows = project(shape, state.bound);

    state.stage.innerHTML = isoSvg(shape, state.bound, state.unit);
    state.panels.innerHTML =
      shadowPanel("FRONT", payload.targets.front, shadows.front, state.gridCell) +
      shadowPanel("TOP", payload.targets.top, shadows.top, state.gridCell);

    state.buttons.forEach(function (button) {
      button.disabled = state.busy || state.done;
    });
    state.checkBtn.disabled = state.busy || state.done || state.turns.length === 0;
    state.turnLabel.textContent =
      "Turn " + state.turns.length + " / " + payload.action_cap;
    state.status.textContent = state.message;
  }

  function act(token) {
    if (!state || state.busy || state.done) return;
    if (state.turns.length >= state.payload.action_cap) return;
    state.turns.push(token);
    var after = mul(turnMatrix(token), state.matrix);

    var land = function () {
      state.busy = false;
      state.timer = null;
      state.stage.style.transform = "";
      state.stage.style.opacity = "";
      state.matrix = after;
      var shadows = project(transform(state.shape, state.matrix), state.bound);
      if (matches(shadows, state.payload)) {
        state.done = true;
        state.message = "🎉 Both shadows match — submitting…";
        render();
        state.submitTimer = window.setTimeout(submit, 350);
        return;
      }
      if (state.turns.length >= state.payload.action_cap) {
        state.done = true;
        state.message = "⏳ Out of turns — press RESTART to try again.";
      } else {
        state.message = "Keep turning.";
      }
      render();
    };

    if (state.reducedMotion) {
      land();                      // no motion, no timer — just redraw
      return;
    }
    state.busy = true;
    state.message = "Turning…";
    // Squash briefly on the old pose, then snap to the exact new orientation.
    state.stage.style.transform = "scale(0.93)";
    state.stage.style.opacity = "0.55";
    render();
    state.timer = window.setTimeout(land, TURN_MS);
  }

  function submit() {
    if (!state || state.submitted) return;
    state.submitted = true;
    state.api.submit(JSON.stringify({ v: 1, turns: state.turns }));
  }

  function restart() {
    if (!state || state.busy) return;
    stopTimers();
    // The server replays exactly what is submitted, so a restart has to drop
    // the turns already played, not just the pose.
    state.matrix = ORIENTATIONS[state.payload.initial_orientation];
    state.turns = [];
    state.done = false;
    state.message = "Back to the starting pose.";
    render();
  }

  function stopTimers() {
    if (state && state.timer) {
      window.clearTimeout(state.timer);
      state.timer = null;
    }
  }

  function makeButton(label, aria, handler) {
    var button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.setAttribute("aria-label", aria);
    button.style.cssText =
      "min-width:64px;min-height:48px;font-size:1rem;font-weight:900;" +
      "border:2px solid #2a9d8f;border-radius:12px;background:#fff;cursor:pointer;";
    button.addEventListener("click", handler);
    return button;
  }

  window.RelayGames = window.RelayGames || {};
  window.RelayGames["shadow_cast"] = {
    mount: function (container, puzzle, api) {
      var payload = puzzle.payload;
      var avail = Math.min(container.clientWidth || 520, 520);
      state = {
        payload: payload,
        api: api,
        shape: payload.voxels.map(function (cell) {
          return [cell[0], cell[1], cell[2]];
        }),
        bound: payload.bound,
        matrix: ORIENTATIONS[payload.initial_orientation],
        turns: [],
        busy: false,
        done: false,
        submitted: false,
        timer: null,
        submitTimer: null,
        buttons: [],
        message: "Match both shadows.",
        unit: Math.max(14, Math.min(30, Math.floor(avail / (2.6 * payload.bound)))),
        gridCell: Math.max(14, Math.min(22, Math.floor((avail / 2 - 40) / payload.bound))),
        reducedMotion:
          !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches),
      };

      var root = document.createElement("div");

      state.turnLabel = document.createElement("div");
      state.turnLabel.style.cssText =
        "text-align:center;font-weight:800;font-size:0.85rem;margin-bottom:6px;";
      root.appendChild(state.turnLabel);

      state.stage = document.createElement("div");
      state.stage.style.cssText =
        "margin:0 auto 12px;transition:transform 0.18s ease,opacity 0.18s ease;";
      root.appendChild(state.stage);

      state.panels = document.createElement("div");
      state.panels.style.cssText =
        "display:flex;gap:18px;justify-content:center;flex-wrap:wrap;";
      root.appendChild(state.panels);

      // Two rows of three: one axis per column, forward turn then inverse.
      var pad = document.createElement("div");
      pad.style.cssText =
        "display:grid;grid-template-columns:repeat(3,auto);gap:8px;" +
        "justify-content:center;margin-top:14px;";
      ["x", "y", "z"].forEach(function (axis) {
        [["+", "↻"], ["-", "↺"]].forEach(function (spec) {
          var token = axis + spec[0];
          var button = makeButton(
            axis.toUpperCase() + " " + spec[1],
            "Turn " + (spec[0] === "+" ? "forwards" : "backwards") +
              " around the " + axis.toUpperCase() + " axis",
            function () { act(token); }
          );
          // Column-major grid: X±, Y±, Z± read down each column.
          button.style.gridColumn = String("xyz".indexOf(axis) + 1);
          button.style.gridRow = spec[0] === "+" ? "1" : "2";
          state.buttons.push(button);
          pad.appendChild(button);
        });
      });
      root.appendChild(pad);

      var controls = document.createElement("div");
      controls.style.cssText =
        "display:flex;gap:10px;justify-content:center;margin-top:10px;flex-wrap:wrap;";
      var restartBtn = makeButton("↺ RESTART", "Back to the starting pose", restart);
      state.checkBtn = makeButton("CHECK", "Submit these turns", submit);
      state.checkBtn.style.cssText += "background:#8338ec;color:#fff;border-color:#8338ec;";
      controls.appendChild(restartBtn);
      controls.appendChild(state.checkBtn);
      root.appendChild(controls);

      state.status = document.createElement("p");
      state.status.setAttribute("role", "status");
      state.status.style.cssText =
        "text-align:center;font-weight:700;font-size:0.9rem;margin:10px 0 0;min-height:1.2em;";
      root.appendChild(state.status);

      var hint = document.createElement("p");
      hint.textContent =
        "Turn the block a quarter at a time until the shadow it casts forwards " +
        "matches the FRONT target and the shadow it casts downwards matches the " +
        "TOP target. A shadow cell fills when any cube sits behind it, so several " +
        "different poses can be right. Keys: x, y, z to turn, hold Shift for the " +
        "other way, r to start over.";
      hint.style.cssText = "color:#8a8a96;font-size:0.85rem;margin:8px 0 0;";
      root.appendChild(hint);

      container.appendChild(root);
      state.root = root;

      state.keyHandler = function (event) {
        if (state.busy || state.done) return;
        var key = typeof event.key === "string" ? event.key : "";
        if (key === "r" || key === "R") {
          event.preventDefault();
          restart();
          return;
        }
        var axis = key.length === 1 ? key.toLowerCase() : "";
        if (axis === "x" || axis === "y" || axis === "z") {
          event.preventDefault();
          var inverse = event.shiftKey || key === axis.toUpperCase();
          act(axis + (inverse ? "-" : "+"));
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

    // Test hooks: the deterministic simulation and the orientation table the
    // payload's `initial_orientation` indexes into, with no DOM involved.
    __replay: replay,
    __orientations: ORIENTATIONS,
  };
})();
