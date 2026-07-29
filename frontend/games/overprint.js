// OVERPRINT renderer — transform transparent layers until their OR-composite
// matches the target. Select a layer chip, then move it with the D-pad /
// arrow keys (or tap a workspace cell to drop its top-left corner there),
// rotate with ⟳ / R, flip with ⇄ / F where the layer allows it, and press
// CHECK to submit. Layers are told apart by glyph + colour (never colour
// alone). Transform order matches the server: flip → rotate → normalise →
// translate. Answer = JSON {"v":1,"layers":[{id,r,c,rot,fx,fy},...]}.
(function () {
  "use strict";

  // Glyph + colour per layer index — shape carries the meaning for
  // colour-blind players; colours are just reinforcement.
  var LAYER_STYLE = [
    { glyph: "●", colour: "#4e79a7" },
    { glyph: "▲", colour: "#e15759" },
    { glyph: "■", colour: "#59a14f" },
    { glyph: "◆", colour: "#8338ec" },
  ];
  var KEYS = {
    ArrowUp: ["move", -1, 0], ArrowDown: ["move", 1, 0],
    ArrowLeft: ["move", 0, -1], ArrowRight: ["move", 0, 1],
    w: ["move", -1, 0], s: ["move", 1, 0], a: ["move", 0, -1], d: ["move", 0, 1],
    W: ["move", -1, 0], S: ["move", 1, 0], A: ["move", 0, -1], D: ["move", 0, 1],
    r: ["rotate"], R: ["rotate"], f: ["flip"], F: ["flip"],
  };

  var state = null;

  function transform(pattern, rot, fx, fy) {
    // Mirror of the backend: flip_x (negate col), flip_y (negate row), then
    // `rot` clockwise quarter-turns ((r,c) -> (c,-r)), then normalise to 0,0.
    var cells = pattern.map(function (cell) { return [cell[0], cell[1]]; });
    if (fx) cells = cells.map(function (p) { return [p[0], -p[1]]; });
    if (fy) cells = cells.map(function (p) { return [-p[0], p[1]]; });
    for (var k = 0; k < (rot % 4); k++) {
      cells = cells.map(function (p) { return [p[1], -p[0]]; });
    }
    var minR = Infinity, minC = Infinity;
    cells.forEach(function (p) {
      minR = Math.min(minR, p[0]);
      minC = Math.min(minC, p[1]);
    });
    return cells.map(function (p) { return [p[0] - minR, p[1] - minC]; });
  }

  function dims(shape) {
    var h = 0, w = 0;
    shape.forEach(function (p) {
      h = Math.max(h, p[0] + 1);
      w = Math.max(w, p[1] + 1);
    });
    return [h, w];
  }

  function placedCells(index) {
    var layer = state.payload.layers[index];
    var pose = state.poses[index];
    return transform(layer.pattern, pose.rot, pose.fx, pose.fy).map(function (p) {
      return [p[0] + pose.r, p[1] + pose.c];
    });
  }

  function clampPose(index) {
    var layer = state.payload.layers[index];
    var pose = state.poses[index];
    var hw = dims(transform(layer.pattern, pose.rot, pose.fx, pose.fy));
    pose.r = Math.max(0, Math.min(state.payload.rows - hw[0], pose.r));
    pose.c = Math.max(0, Math.min(state.payload.cols - hw[1], pose.c));
  }

  function cellKey(r, c) { return r + "," + c; }

  function drawGrid(host, rows, cols, cellPx, fill) {
    host.innerHTML = "";
    var grid = document.createElement("div");
    grid.style.cssText =
      "display:grid;grid-template-columns:repeat(" + cols + "," + cellPx + "px);" +
      "grid-auto-rows:" + cellPx + "px;gap:2px;";
    for (var r = 0; r < rows; r++) {
      for (var c = 0; c < cols; c++) {
        grid.appendChild(fill(r, c, cellPx));
      }
    }
    host.appendChild(grid);
    return grid;
  }

  function render() {
    var p = state.payload;

    // Target panel: neutral dark marks, read-only.
    drawGrid(state.targetHost, p.rows, p.cols, state.targetCell, function (r, c) {
      var el = document.createElement("div");
      var marked = p.target[r].charAt(c) === "1";
      el.style.cssText =
        "border-radius:3px;background:" + (marked ? "#2b2b33" : "#f0e8dc") + ";";
      return el;
    });

    // Workspace: every layer's glyphs, selected layer outlined and on top.
    var byCell = {};
    p.layers.forEach(function (layer, index) {
      placedCells(index).forEach(function (cell) {
        var key = cellKey(cell[0], cell[1]);
        (byCell[key] = byCell[key] || []).push(index);
      });
    });
    drawGrid(state.workHost, p.rows, p.cols, state.workCell, function (r, c, px) {
      var el = document.createElement("div");
      var here = byCell[cellKey(r, c)] || [];
      var css = "border-radius:4px;background:#f0e8dc;cursor:pointer;" +
        "display:flex;align-items:center;justify-content:center;" +
        "font-weight:900;font-size:" + Math.floor(px * 0.62) + "px;";
      if (here.length) {
        var top = here.indexOf(state.selected) !== -1
          ? state.selected
          : here[here.length - 1];
        var style = LAYER_STYLE[top % LAYER_STYLE.length];
        el.textContent = here.length > 1 ? "✚" : style.glyph;
        css += "background:" + style.colour + (here.length > 1 ? "" : "22") + ";" +
          "color:" + (here.length > 1 ? "#fff" : style.colour) + ";";
        if (here.indexOf(state.selected) !== -1) {
          css += "outline:3px solid " + LAYER_STYLE[state.selected % LAYER_STYLE.length].colour +
            ";outline-offset:-3px;";
        }
      }
      el.style.cssText = css;
      el.addEventListener("click", function () {
        // Tap-to-place: drop the selected layer's top-left on this cell
        // (44px cells keep this comfortable on phones; no dragging needed).
        if (state.done) return;
        state.poses[state.selected].r = r;
        state.poses[state.selected].c = c;
        clampPose(state.selected);
        render();
      });
      return el;
    });

    // Layer chips + per-layer transform buttons reflect the selection.
    state.chips.forEach(function (chip, index) {
      var style = LAYER_STYLE[index % LAYER_STYLE.length];
      var on = index === state.selected;
      chip.style.borderColor = style.colour;
      chip.style.background = on ? style.colour : "#fff";
      chip.style.color = on ? "#fff" : style.colour;
      chip.setAttribute("aria-pressed", on ? "true" : "false");
    });
    var layer = p.layers[state.selected];
    state.rotateBtn.disabled = state.done || !layer.allow_rot;
    state.flipBtn.disabled =
      state.done || !(layer.allow_flip_x || layer.allow_flip_y);
  }

  function act(action) {
    if (!state || state.done) return;
    var pose = state.poses[state.selected];
    var layer = state.payload.layers[state.selected];
    if (action[0] === "move") {
      pose.r += action[1];
      pose.c += action[2];
    } else if (action[0] === "rotate" && layer.allow_rot) {
      pose.rot = (pose.rot + 1) % 4;
    } else if (action[0] === "flip") {
      if (layer.allow_flip_x) pose.fx = !pose.fx;
      else if (layer.allow_flip_y) pose.fy = !pose.fy;
    } else {
      return;
    }
    clampPose(state.selected);
    render();
  }

  function submit() {
    if (!state || state.done) return;
    state.done = true;
    var answer = {
      v: 1,
      layers: state.payload.layers.map(function (layer, index) {
        var pose = state.poses[index];
        return { id: layer.id, r: pose.r, c: pose.c, rot: pose.rot, fx: pose.fx, fy: pose.fy };
      }),
    };
    state.api.submit(JSON.stringify(answer));
  }

  function makeButton(label, aria, handler) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = label;
    btn.setAttribute("aria-label", aria);
    btn.style.cssText =
      "min-width:52px;min-height:52px;font-size:1.2rem;font-weight:900;" +
      "border:2px solid #f0e8dc;border-radius:12px;background:#fff;cursor:pointer;";
    btn.addEventListener("click", handler);
    return btn;
  }

  window.RelayGames = window.RelayGames || {};
  window.RelayGames["overprint"] = {
    mount: function (container, puzzle, api) {
      var p = puzzle.payload;
      // Two grids side by side down to ~320px; workspace gets the space.
      var avail = Math.min(container.clientWidth || 640, 640);
      var workCell = Math.max(26, Math.min(44, Math.floor((avail - 40) * 0.62 / p.cols)));
      var targetCell = Math.max(14, Math.min(26, Math.floor(workCell * 0.55)));
      state = {
        payload: p,
        api: api,
        done: false,
        selected: 0,
        workCell: workCell,
        targetCell: targetCell,
        poses: p.initial.map(function (pose) {
          return { r: pose.r, c: pose.c, rot: pose.rot, fx: !!pose.fx, fy: !!pose.fy };
        }),
        chips: [],
      };

      var root = document.createElement("div");

      var panels = document.createElement("div");
      panels.style.cssText =
        "display:flex;gap:16px;justify-content:center;align-items:flex-start;flex-wrap:wrap;";
      function panel(titleText) {
        var wrap = document.createElement("div");
        var title = document.createElement("div");
        title.textContent = titleText;
        title.style.cssText =
          "font-weight:800;font-size:0.8rem;margin-bottom:4px;text-align:center;";
        wrap.appendChild(title);
        var host = document.createElement("div");
        wrap.appendChild(host);
        panels.appendChild(wrap);
        return host;
      }
      state.targetHost = panel("Target");
      state.workHost = panel("Your print");
      root.appendChild(panels);

      // Layer chips: pick which stamp the controls drive.
      var chipRow = document.createElement("div");
      chipRow.style.cssText =
        "display:flex;gap:8px;justify-content:center;margin-top:10px;flex-wrap:wrap;";
      p.layers.forEach(function (layer, index) {
        var style = LAYER_STYLE[index % LAYER_STYLE.length];
        var extras =
          (layer.allow_rot ? " ⟳" : "") +
          (layer.allow_flip_x || layer.allow_flip_y ? " ⇄" : "");
        var chip = makeButton(
          style.glyph + " " + (index + 1) + extras,
          "Select layer " + (index + 1),
          function () {
            state.selected = index;
            render();
          }
        );
        chip.style.minWidth = "64px";
        chip.style.fontSize = "1rem";
        state.chips.push(chip);
        chipRow.appendChild(chip);
      });
      root.appendChild(chipRow);

      // D-pad + rotate/flip + CHECK. Keyboard: arrows/WASD, R, F, Enter.
      var controls = document.createElement("div");
      controls.style.cssText =
        "display:flex;gap:18px;align-items:center;justify-content:center;margin-top:10px;flex-wrap:wrap;";
      var pad = document.createElement("div");
      pad.style.cssText =
        "display:grid;grid-template-columns:repeat(3,56px);grid-auto-rows:56px;gap:4px;justify-content:center;";
      [
        [null, ["▲", "Move up", ["move", -1, 0]], null],
        [["◀", "Move left", ["move", 0, -1]], null, ["▶", "Move right", ["move", 0, 1]]],
        [null, ["▼", "Move down", ["move", 1, 0]], null],
      ].forEach(function (row) {
        row.forEach(function (spec) {
          if (!spec) {
            pad.appendChild(document.createElement("span"));
            return;
          }
          pad.appendChild(makeButton(spec[0], spec[1], function () { act(spec[2]); }));
        });
      });
      controls.appendChild(pad);

      var side = document.createElement("div");
      side.style.cssText = "display:flex;flex-direction:column;gap:8px;";
      state.rotateBtn = makeButton("⟳", "Rotate layer clockwise", function () { act(["rotate"]); });
      state.flipBtn = makeButton("⇄", "Flip layer", function () { act(["flip"]); });
      var checkBtn = makeButton("CHECK", "Submit your print", submit);
      checkBtn.style.cssText +=
        "background:#8338ec;color:#fff;border-color:#8338ec;font-size:0.95rem;";
      side.appendChild(state.rotateBtn);
      side.appendChild(state.flipBtn);
      side.appendChild(checkBtn);
      controls.appendChild(side);
      root.appendChild(controls);

      var hint = document.createElement("p");
      hint.textContent =
        "Every layer prints onto the same sheet — match the target exactly (extra marks fail too). " +
        "Pick a layer chip, then move it with the pad or arrow keys, or tap a cell to drop it there. " +
        "⟳/R rotates, ⇄/F flips (marked layers only). Press CHECK when it matches.";
      hint.style.cssText = "color:#8a8a96;font-size:0.85rem;margin:10px 0 0;";
      root.appendChild(hint);

      container.appendChild(root);
      state.root = root;

      state.keyHandler = function (event) {
        if (event.key === "Enter") {
          event.preventDefault();
          submit();
          return;
        }
        var digit = parseInt(event.key, 10);
        if (digit >= 1 && digit <= p.layers.length) {
          state.selected = digit - 1;
          render();
          return;
        }
        var action = KEYS[event.key];
        if (!action) return;
        event.preventDefault();
        act(action);
      };
      document.addEventListener("keydown", state.keyHandler);

      render();
    },

    unmount: function () {
      if (!state) return; // idempotent
      document.removeEventListener("keydown", state.keyHandler);
      if (state.root && state.root.parentNode) {
        state.root.parentNode.removeChild(state.root);
      }
      state = null;
    },
  };
})();
