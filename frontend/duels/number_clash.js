// NUMBER CLASH renderer — the Duelist's whole view.
//
// Nine numbers, each spent once. The grid below is the whole interface: your
// remaining numbers are live, your spent ones are struck through, and the
// opponent's spent pile sits under their panel — every one of those was made
// public when its round resolved.
//
// The number in flight is the only secret, and the client never has it: the
// opponent's cell shows a lock until `duel.choices` carries their number,
// which the server only sends once the round is over.
(function () {
  "use strict";

  var state = null;

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function hand(number, locked) {
    if (number) return String(number);
    return locked ? "🔒" : "…";
  }

  function seat(kind) {
    var root = el("div", "duel-seat duel-seat-" + kind);
    var face = el("div", "duel-hand", "…");
    var name = el("div", "duel-name", kind === "you" ? "You" : "Opponent");
    var score = el("div", "duel-score", "0");
    var spent = el("div", "duel-sub", "");
    root.appendChild(face);
    root.appendChild(name);
    root.appendChild(score);
    root.appendChild(spent);
    return { root: root, face: face, name: name, score: score, spent: spent };
  }

  function build(container) {
    var root = el("div", "duel duel-clash");
    var header = el("div", "duel-header");
    var you = seat("you");
    var them = seat("them");
    header.appendChild(you.root);
    header.appendChild(el("div", "duel-versus", "VS"));
    header.appendChild(them.root);

    var round = el("div", "duel-round", "");
    var status = el("div", "duel-status", "");
    var grid = el("div", "duel-numbers");
    var log = el("div", "duel-log");

    root.appendChild(header);
    root.appendChild(round);
    root.appendChild(status);
    root.appendChild(grid);
    root.appendChild(log);
    container.appendChild(root);
    return {
      root: root, round: round, status: status, grid: grid, log: log,
      you: you, them: them,
    };
  }

  function renderGrid(duel) {
    var payload = duel.payload || {};
    var numbers = payload.numbers || [];
    var available = payload.available || [];
    var dom = state.dom;
    // Rebuild only when the grid actually changes, so a re-render never steals
    // a click mid-press.
    var signature = JSON.stringify([
      duel.id, duel.round, state.open, state.locked, available,
    ]);
    if (state.gridSignature === signature) return;
    state.gridSignature = signature;
    dom.grid.innerHTML = "";
    if (!duel.you) return;  // a Grandmaster watches; they never get buttons

    numbers.forEach(function (number) {
      var spent = available.indexOf(number) < 0;
      var node = el("button", "duel-number" + (spent ? " spent" : ""));
      node.type = "button";
      node.textContent = String(number);
      node.disabled = spent || state.locked || !state.open;
      node.addEventListener("click", function () {
        if (node.disabled) return;
        node.disabled = true;                    // never a second press
        node.classList.add("chosen");
        state.sending = true;
        state.api.choose(String(number), duel.id, duel.round);
        render(state.duel);
      });
      dom.grid.appendChild(node);
    });
  }

  function renderLog(payload) {
    var rounds = payload.log || [];
    var dom = state.dom;
    var signature = JSON.stringify(rounds.length);
    if (state.logSignature === signature) return;
    state.logSignature = signature;
    dom.log.innerHTML = "";
    rounds.slice(-4).forEach(function (entry) {
      var row = el("div", "duel-log-row");
      row.appendChild(el("span", "duel-log-round", "R" + entry.round));
      row.appendChild(el("span", "duel-log-pair",
        (entry.a === null ? "–" : entry.a) + " vs " +
        (entry.b === null ? "–" : entry.b)));
      row.appendChild(el("span", "duel-log-winner",
        entry.winner ? "won by " + (entry.winner === state.mine ? "you" : "them")
          : "draw"));
      dom.log.appendChild(row);
    });
  }

  function statusLine(duel) {
    var payload = duel.payload || {};
    var last = payload.last;
    if (duel.phase === "done") {
      if (!duel.you) return "Duel over.";
      return duel.winner_side === duel.you
        ? "🏆 You won the duel!" : "💥 You lost the duel.";
    }
    if (duel.phase === "reveal") {
      if (!last) return "Round over.";
      if (!last.winner) return "🤝 Level numbers — both are spent anyway.";
      if (!duel.you) return "Round " + last.round + " decided.";
      return last.winner === duel.you
        ? "✅ You take the point." : "❌ They take the point.";
    }
    if (!duel.you) return "Round " + payload.game_round + " in progress.";
    if (state.locked) return "Locked in — waiting…";
    return "Pick a number. Spending big to win small is how duels are lost.";
  }

  function roundLine(payload) {
    if (payload.sudden_death) return "SUDDEN DEATH — still first to " +
      payload.points_needed;
    return "Round " + payload.game_round + " of " + payload.normal_rounds +
      " · first to " + payload.points_needed;
  }

  function render(duel) {
    state.duel = duel;
    var dom = state.dom;
    var payload = duel.payload || {};
    var mine = duel.you;
    var theirs = mine === "a" ? "b" : mine === "b" ? "a" : null;
    var names = duel.duellists || {};
    var choices = duel.choices || {};
    var locked = duel.locked || {};
    var points = payload.points || {};
    var used = payload.used || {};
    var seats = mine ? [mine, theirs] : ["a", "b"];

    state.mine = mine;
    state.open = duel.phase === "choosing";
    state.locked = !!(mine && locked[mine]) || (state.sending && state.open);

    dom.you.name.textContent = mine ? (names[seats[0]] || "You") : (names.a || "A");
    dom.them.name.textContent = mine
      ? (names[seats[1]] || "Opponent") : (names.b || "B");
    [dom.you, dom.them].forEach(function (panel, index) {
      var side = seats[index];
      panel.face.textContent = hand(choices[side], locked[side]);
      panel.score.textContent = String(points[side] || 0);
      var pile = used[side] || [];
      panel.spent.textContent = pile.length ? "spent " + pile.join(" ") : "";
    });

    dom.root.className = "duel duel-clash duel-" + duel.phase;
    dom.round.textContent = roundLine(payload);
    dom.status.textContent = statusLine(duel);
    renderGrid(duel);
    renderLog(payload);
  }

  window.RelayDuels = window.RelayDuels || {};
  window.RelayDuels.number_clash = {
    mount: function (container, duel, api) {
      state = {
        container: container, api: api, dom: build(container),
        sending: false, gridSignature: null, logSignature: null,
      };
      render(duel);
    },
    update: function (duel) {
      if (!state) return;
      if (state.round !== duel.round) {
        state.round = duel.round;
        state.sending = false;
      }
      render(duel);
    },
    unmount: function () {
      if (state && state.container) state.container.innerHTML = "";
      state = null;
    },
  };
})();
