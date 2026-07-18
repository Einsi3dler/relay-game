// SWEEP renderer — reveal safe cells, flag every mine.
// Left-click reveals (from the local clue grid; a mine click submits "BOOM"),
// right-click/long-press flags. Answer = flagged coords "r,c;r,c;...".
(function () {
  "use strict";

  var state = null;

  function key(r, c) { return r + "," + c; }

  function neighbours(p, r, c) {
    var out = [];
    for (var dr = -1; dr <= 1; dr++) {
      for (var dc = -1; dc <= 1; dc++) {
        if (dr === 0 && dc === 0) continue;
        var nr = r + dr, nc = c + dc;
        if (nr >= 0 && nr < p.rows && nc >= 0 && nc < p.cols) out.push([nr, nc]);
      }
    }
    return out;
  }

  function reveal(r, c) {
    var p = state.payload;
    if (state.revealed[key(r, c)] !== undefined || state.flags[key(r, c)]) return;
    var n = state.clues[key(r, c)];
    if (n === undefined) {
      state.api.submit("BOOM"); // stepped on a mine — instant fail of the attempt
      return;
    }
    state.revealed[key(r, c)] = n;
    if (n === 0) {
      neighbours(p, r, c).forEach(function (cell) { reveal(cell[0], cell[1]); });
    }
  }

  function updateStatus() {
    var flagged = Object.keys(state.flags).length;
    state.status.textContent = "Flags: " + flagged + " / " + state.payload.mine_count;
    if (state.api.setReady) state.api.setReady(flagged === state.payload.mine_count);
  }

  function render() {
    var p = state.payload;
    state.grid.innerHTML = "";
    for (var r = 0; r < p.rows; r++) {
      for (var c = 0; c < p.cols; c++) {
        (function (r, c) {
          var cell = document.createElement("button");
          cell.type = "button";
          var revealedN = state.revealed[key(r, c)];
          var isFlag = !!state.flags[key(r, c)];
          cell.style.cssText =
            "width:44px;height:44px;font:bold 16px sans-serif;cursor:pointer;" +
            "border:1px solid #345;background:" +
            (revealedN !== undefined ? "#182818" : "#2a3440") + ";color:#cde;";
          cell.textContent =
            revealedN !== undefined ? (revealedN || "") : isFlag ? "🚩" : "";
          cell.addEventListener("click", function () {
            reveal(r, c);
            render();
          });
          cell.addEventListener("contextmenu", function (event) {
            event.preventDefault();
            if (state.revealed[key(r, c)] !== undefined) return;
            if (state.flags[key(r, c)]) delete state.flags[key(r, c)];
            else state.flags[key(r, c)] = true;
            render();
          });
          state.grid.appendChild(cell);
        })(r, c);
      }
    }
    updateStatus();
  }

  window.RelayGames = window.RelayGames || {};
  window.RelayGames["sweep"] = {
    mount: function (container, puzzle, api) {
      var p = puzzle.payload;
      state = {
        payload: p, api: api, flags: {}, revealed: {}, clues: {},
        grid: document.createElement("div"),
        status: document.createElement("p"),
      };
      p.clues.forEach(function (cell) { state.clues[key(cell.r, cell.c)] = cell.n; });
      p.revealed.forEach(function (cell) { state.revealed[key(cell.r, cell.c)] = cell.n; });
      state.grid.style.cssText =
        "display:grid;grid-template-columns:repeat(" + p.cols + ",44px);gap:2px;" +
        "justify-content:center;margin:8px 0;";
      var hint = document.createElement("p");
      hint.textContent = "Left-click: reveal · Right-click: flag. Flag every mine.";
      var submit = document.createElement("button");
      submit.type = "button";
      submit.textContent = "Submit flags";
      submit.addEventListener("click", function () {
        var flags = Object.keys(state.flags);
        if (flags.length !== p.mine_count) return; // must flag exactly all mines
        api.submit(flags.join(";"));
      });
      container.appendChild(hint);
      container.appendChild(state.grid);
      container.appendChild(state.status);
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
