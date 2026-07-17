// DECANT renderer — click a source tube, then a destination, to pour.
// Answer = the ordered move list "src>dst;src>dst;...". The client blocks
// illegal pours for UX; the server replays and enforces them anyway.
(function () {
  "use strict";

  var COLOURS = ["#e15759", "#4e79a7", "#f2b418", "#59a14f", "#b07aa1"];
  var state = null;

  function topRun(tube) {
    if (!tube.length) return 0;
    var run = 1;
    while (run < tube.length && tube[tube.length - run - 1] === tube[tube.length - 1]) run++;
    return run;
  }

  // Free-stacking rules: any tube with room is a legal target — the
  // destination's top colour does not need to match (mirrors the server).
  function legalPour(tubes, src, dst, capacity) {
    return src !== dst && tubes[src].length > 0 && tubes[dst].length < capacity;
  }

  function applyPour(tubes, src, dst, capacity) {
    var colour = tubes[src][tubes[src].length - 1];
    var amount = Math.min(topRun(tubes[src]), capacity - tubes[dst].length);
    tubes[src].length -= amount;
    for (var i = 0; i < amount; i++) tubes[dst].push(colour);
  }

  function currentTubes() {
    var tubes = state.payload.tubes.map(function (t) { return t.slice(); });
    state.moves.forEach(function (move) {
      applyPour(tubes, move[0], move[1], state.payload.capacity);
    });
    return tubes;
  }

  function isSolved(tubes, capacity) {
    return tubes.every(function (tube) {
      if (!tube.length) return true;
      if (tube.length !== capacity) return false;
      return tube.every(function (seg) { return seg === tube[0]; });
    });
  }

  function render() {
    var p = state.payload;
    var tubes = currentTubes();
    state.row.innerHTML = "";
    tubes.forEach(function (tube, index) {
      var el = document.createElement("button");
      el.type = "button";
      el.style.cssText =
        "width:52px;height:" + (p.capacity * 34 + 12) + "px;cursor:pointer;" +
        "display:flex;flex-direction:column-reverse;gap:2px;padding:4px;" +
        "border:2px solid " + (state.selected === index ? "#FFD700" : "#345") + ";" +
        "border-radius:0 0 14px 14px;background:#101820;";
      tube.forEach(function (colour) {
        var seg = document.createElement("div");
        seg.style.cssText =
          "height:30px;border-radius:4px;background:" + COLOURS[(colour - 1) % COLOURS.length] + ";";
        el.appendChild(seg);
      });
      el.addEventListener("click", function () {
        if (state.selected === null) {
          if (tube.length) state.selected = index;
        } else if (state.selected === index) {
          state.selected = null;
        } else {
          if (legalPour(tubes, state.selected, index, p.capacity)) {
            state.moves.push([state.selected, index]);
          }
          state.selected = null;
        }
        render();
      });
      state.row.appendChild(el);
    });
    state.status.textContent =
      "Moves: " + state.moves.length + (isSolved(tubes, p.capacity) ? " — solved! Submit." : "");
    if (state.api.setReady) state.api.setReady(isSolved(tubes, p.capacity));
  }

  window.RelayGames = window.RelayGames || {};
  window.RelayGames["decant"] = {
    mount: function (container, puzzle, api) {
      state = {
        payload: puzzle.payload, api: api, moves: [], selected: null,
        row: document.createElement("div"),
        status: document.createElement("p"),
      };
      state.row.style.cssText =
        "display:flex;gap:10px;justify-content:center;align-items:flex-end;margin:8px 0;";
      var hint = document.createElement("p");
      hint.textContent = "Click a tube to pick it up, another to pour. Sort every colour.";
      var undo = document.createElement("button");
      undo.type = "button";
      undo.textContent = "Undo";
      undo.style.marginRight = "8px";
      undo.addEventListener("click", function () {
        state.moves.pop();
        state.selected = null;
        render();
      });
      var submit = document.createElement("button");
      submit.type = "button";
      submit.textContent = "Submit pours";
      submit.addEventListener("click", function () {
        api.submit(state.moves.map(function (m) { return m[0] + ">" + m[1]; }).join(";"));
      });
      container.appendChild(hint);
      container.appendChild(state.row);
      container.appendChild(state.status);
      container.appendChild(undo);
      container.appendChild(submit);
      render();
    },
    unmount: function () {
      if (state && state.row && state.row.parentNode) {
        state.row.parentNode.innerHTML = "";
      }
      state = null;
    },
  };
})();
