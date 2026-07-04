// REWIRE renderer — rotate tiles until power reaches every sink.
// Implements the GAME_MODULE_SPEC §10 interface; answer = row-major
// orientations "1,0,3,2,...". Live glow is cosmetic; the server revalidates.
(function () {
  "use strict";

  var DELTAS = { 0: [-1, 0], 1: [0, 1], 2: [1, 0], 3: [0, -1] };
  var SHAPE_EDGES = { end: [0], straight: [0, 2], elbow: [0, 1], tee: [0, 1, 2] };
  var SHAPE_PATHS = {
    end: "M50 50 L50 0 M50 50 m-10 0 a10 10 0 1 0 20 0 a10 10 0 1 0 -20 0",
    straight: "M50 0 L50 100",
    elbow: "M50 0 L50 50 L100 50",
    tee: "M50 0 L50 100 M50 50 L100 50",
  };

  var state = null;

  function openEdges(shape, orient) {
    return SHAPE_EDGES[shape].map(function (e) { return (e + orient) % 4; });
  }

  function computePowered(p, orients) {
    var powered = {};
    var key = function (r, c) { return r + "," + c; };
    var frontier = [p.source];
    powered[key(p.source[0], p.source[1])] = true;
    while (frontier.length) {
      var cell = frontier.pop();
      var edges = openEdges(
        p.tiles[cell[0] * p.cols + cell[1]].shape,
        orients[cell[0] * p.cols + cell[1]]
      );
      for (var i = 0; i < edges.length; i++) {
        var d = edges[i];
        var nr = cell[0] + DELTAS[d][0], nc = cell[1] + DELTAS[d][1];
        if (nr < 0 || nr >= p.rows || nc < 0 || nc >= p.cols) continue;
        var back = (d + 2) % 4;
        var nEdges = openEdges(p.tiles[nr * p.cols + nc].shape, orients[nr * p.cols + nc]);
        if (nEdges.indexOf(back) === -1) continue;
        if (!powered[key(nr, nc)]) {
          powered[key(nr, nc)] = true;
          frontier.push([nr, nc]);
        }
      }
    }
    return powered;
  }

  function render() {
    var p = state.payload;
    state.grid.innerHTML = "";
    var powered = computePowered(p, state.orients);
    for (var r = 0; r < p.rows; r++) {
      for (var c = 0; c < p.cols; c++) {
        (function (r, c) {
          var i = r * p.cols + c;
          var tile = document.createElement("button");
          tile.type = "button";
          tile.className = "rewire-tile";
          tile.style.cssText =
            "width:64px;height:64px;border:1px solid #345;padding:0;cursor:pointer;" +
            "background:" + (powered[r + "," + c] ? "#0e2a12" : "#101820") + ";";
          var isSource = p.source[0] === r && p.source[1] === c;
          var isSink = p.sinks.some(function (s) { return s[0] === r && s[1] === c; });
          var color = powered[r + "," + c] ? "#7CFC00" : "#607080";
          if (isSource) color = "#FFD700";
          tile.innerHTML =
            '<svg viewBox="0 0 100 100" width="62" height="62" ' +
            'style="transform:rotate(' + state.orients[i] * 90 + 'deg);display:block">' +
            '<path d="' + SHAPE_PATHS[p.tiles[i].shape] + '" stroke="' + color +
            '" stroke-width="12" fill="none" stroke-linecap="round"/>' +
            (isSink ? '<circle cx="50" cy="50" r="16" fill="none" stroke="#FF6347" stroke-width="6"/>' : "") +
            "</svg>";
          tile.addEventListener("click", function () {
            state.orients[i] = (state.orients[i] + 1) % 4;
            render();
          });
          state.grid.appendChild(tile);
        })(r, c);
      }
    }
  }

  window.RelayGames = window.RelayGames || {};
  window.RelayGames["rewire"] = {
    mount: function (container, puzzle, api) {
      var p = puzzle.payload;
      state = {
        payload: p,
        orients: p.tiles.map(function (t) { return t.orient; }),
        grid: document.createElement("div"),
      };
      state.grid.style.cssText =
        "display:grid;grid-template-columns:repeat(" + p.cols + ",64px);gap:2px;" +
        "justify-content:center;margin:8px 0;";
      var hint = document.createElement("p");
      hint.textContent = "Click a tile to rotate it. Gold = source, red rings = sinks.";
      var submit = document.createElement("button");
      submit.type = "button";
      submit.textContent = "Submit wiring";
      submit.addEventListener("click", function () {
        api.submit(state.orients.join(","));
      });
      container.appendChild(hint);
      container.appendChild(state.grid);
      container.appendChild(submit);
      render();
    },
    unmount: function () {
      if (state && state.grid && state.grid.parentNode) {
        state.grid.parentNode.innerHTML = "";
      }
      state = null;
    },
  };
})();
