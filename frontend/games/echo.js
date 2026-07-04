// ECHO renderer — watch the pads flash, then tap them back in order.
// Auto-submits once the tap count matches the sequence length.
// unmount() clears all pending animation timers (critical: a re-mounted
// puzzle must never receive stale flashes).
(function () {
  "use strict";

  var PAD_COLOURS = ["#e15759", "#4e79a7", "#f2b418", "#59a14f",
                     "#b07aa1", "#76b7b2", "#ff9da7", "#9c755f", "#bab0ac"];
  var state = null;

  function setPad(index, lit) {
    var pad = state.pads[index];
    if (pad) pad.style.opacity = lit ? "1" : "0.35";
  }

  function playSequence() {
    var p = state.payload;
    state.status.textContent = "Watch…";
    p.sequence.forEach(function (padIndex, i) {
      var onAt = i * (p.flash_ms + p.gap_ms);
      state.timers.push(setTimeout(function () { setPad(padIndex, true); }, onAt));
      state.timers.push(setTimeout(function () { setPad(padIndex, false); }, onAt + p.flash_ms));
    });
    var doneAt = p.sequence.length * (p.flash_ms + p.gap_ms);
    state.timers.push(setTimeout(function () {
      state.accepting = true;
      state.status.textContent = "Your turn — repeat the sequence.";
    }, doneAt));
  }

  window.RelayGames = window.RelayGames || {};
  window.RelayGames["echo"] = {
    mount: function (container, puzzle, api) {
      var p = puzzle.payload;
      var side = Math.round(Math.sqrt(p.pads));
      state = {
        payload: p, api: api, taps: [], timers: [], pads: [], accepting: false,
        status: document.createElement("p"),
      };
      var grid = document.createElement("div");
      grid.style.cssText =
        "display:grid;grid-template-columns:repeat(" + side + ",72px);gap:8px;" +
        "justify-content:center;margin:8px 0;";
      for (var i = 0; i < p.pads; i++) {
        (function (i) {
          var pad = document.createElement("button");
          pad.type = "button";
          pad.style.cssText =
            "width:72px;height:72px;border:none;border-radius:12px;cursor:pointer;" +
            "opacity:0.35;background:" + PAD_COLOURS[i % PAD_COLOURS.length] + ";";
          pad.addEventListener("click", function () {
            if (!state.accepting) return;
            state.taps.push(i);
            setPad(i, true);
            state.timers.push(setTimeout(function () { setPad(i, false); }, 150));
            if (state.taps.length === p.sequence.length) {
              state.accepting = false;
              api.submit(state.taps.join(","));
            }
          });
          state.pads.push(pad);
          grid.appendChild(pad);
        })(i);
      }
      state.grid = grid;
      container.appendChild(state.status);
      container.appendChild(grid);
      playSequence();
    },
    unmount: function () {
      if (state) {
        state.timers.forEach(clearTimeout);
        if (state.grid && state.grid.parentNode) state.grid.parentNode.innerHTML = "";
      }
      state = null;
    },
  };
})();
