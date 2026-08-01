// RPS DUEL renderer — the Duelist's whole view.
//
// Unlike a puzzle renderer (window.RelayGames), a duel is one long-lived
// object that changes phase under the same id, so this exposes update() as
// well as mount()/unmount(). The shell calls update() on every snapshot.
//
// The client never learns the opponent's move before the server reveals it:
// duel.choices only ever contains what the server chose to send. Everything
// here renders from that, so there is nothing to peek at in the DOM either.
(function () {
  "use strict";

  var ART = { rock: "✊", paper: "✋", scissors: "✌️" };
  var LABEL = { rock: "Rock", paper: "Paper", scissors: "Scissors" };

  var state = null;

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function hand(move, locked) {
    if (move) return ART[move] || "❓";
    return locked ? "🔒" : "…";
  }

  // Score pips: filled for wins taken, hollow for the rest of the race.
  function pips(won, needed) {
    var out = "";
    for (var i = 0; i < needed; i++) out += i < won ? "●" : "○";
    return out;
  }

  function build(container) {
    var root = el("div", "duel");

    var header = el("div", "duel-header");
    var you = el("div", "duel-seat duel-seat-you");
    you.appendChild(el("div", "duel-hand", "…"));
    you.appendChild(el("div", "duel-name", "You"));
    you.appendChild(el("div", "duel-pips", ""));

    var versus = el("div", "duel-versus", "VS");

    var them = el("div", "duel-seat duel-seat-them");
    them.appendChild(el("div", "duel-hand", "…"));
    them.appendChild(el("div", "duel-name", "Opponent"));
    them.appendChild(el("div", "duel-pips", ""));

    header.appendChild(you);
    header.appendChild(versus);
    header.appendChild(them);

    var status = el("div", "duel-status", "");
    var moves = el("div", "duel-moves");

    root.appendChild(header);
    root.appendChild(status);
    root.appendChild(moves);
    container.appendChild(root);

    return {
      root: root, status: status, moves: moves,
      you: {
        hand: you.querySelector(".duel-hand"),
        name: you.querySelector(".duel-name"),
        pips: you.querySelector(".duel-pips"),
        seat: you,
      },
      them: {
        hand: them.querySelector(".duel-hand"),
        name: them.querySelector(".duel-name"),
        pips: them.querySelector(".duel-pips"),
        seat: them,
      },
    };
  }

  function renderMoves(duel) {
    var open = duel.phase === "choosing";
    var mine = duel.you;
    var alreadyChose = !!(mine && duel.locked && duel.locked[mine]);
    var available = (duel.payload && duel.payload.moves) || [];

    // Rebuild only when the button set or its enabled-ness actually changes,
    // so a re-render never steals a click mid-press.
    var signature = available.join(",") + "|" + open + "|" + alreadyChose +
      "|" + duel.round;
    if (state.movesSignature === signature) return;
    state.movesSignature = signature;
    state.dom.moves.innerHTML = "";

    if (!mine) return; // a Grandmaster watches; they never get buttons

    available.forEach(function (move) {
      var button = el("button", "duel-move");
      button.type = "button";
      button.disabled = !open || alreadyChose;
      button.innerHTML = '<span class="duel-move-art">' + (ART[move] || "?") +
        '</span><span class="duel-move-label">' + (LABEL[move] || move) +
        "</span>";
      button.addEventListener("click", function () {
        if (button.disabled) return;
        // Lock the row immediately; the server confirms via the next snapshot.
        state.dom.moves.querySelectorAll("button").forEach(function (other) {
          other.disabled = true;
        });
        button.classList.add("chosen");
        state.api.choose(move, duel.id, duel.round);
      });
      state.dom.moves.appendChild(button);
    });
  }

  function statusLine(duel) {
    if (duel.phase === "done") {
      var won = duel.you && duel.winner_side === duel.you;
      if (!duel.you) return "Duel over.";
      return won ? "🏆 You won the duel!" : "💥 You lost the duel.";
    }
    if (duel.phase === "reveal") {
      var last = duel.last_round;
      if (!last) return "Round over.";
      if (!last.winner) return "🤝 Tie — replaying the round.";
      if (!duel.you) return "Round " + last.round + " decided.";
      return last.winner === duel.you ? "✅ You took that round."
        : "❌ They took that round.";
    }
    var mine = duel.you;
    if (mine && duel.locked && duel.locked[mine]) return "Locked in — waiting…";
    if (!mine) return "Round " + duel.round + " in progress.";
    return "Choose — fast!";
  }

  function render(duel) {
    var dom = state.dom;
    var mine = duel.you;
    var theirs = mine === "a" ? "b" : mine === "b" ? "a" : null;
    var names = duel.duellists || {};
    var choices = duel.choices || {};
    var locked = duel.locked || {};
    var wins = duel.wins || {};
    var needed = (duel.payload && duel.payload.wins_needed) || 2;

    if (mine) {
      dom.you.name.textContent = names[mine] || "You";
      dom.them.name.textContent = names[theirs] || "Opponent";
      dom.you.hand.textContent = hand(choices[mine], locked[mine]);
      dom.them.hand.textContent = hand(choices[theirs], locked[theirs]);
      dom.you.pips.textContent = pips(wins[mine] || 0, needed);
      dom.them.pips.textContent = pips(wins[theirs] || 0, needed);
    } else {
      // Grandmaster view: seats stay in a/b order, neither is "you".
      dom.you.name.textContent = names.a || "A";
      dom.them.name.textContent = names.b || "B";
      dom.you.hand.textContent = hand(choices.a, locked.a);
      dom.them.hand.textContent = hand(choices.b, locked.b);
      dom.you.pips.textContent = pips(wins.a || 0, needed);
      dom.them.pips.textContent = pips(wins.b || 0, needed);
    }

    dom.root.className = "duel duel-" + duel.phase;
    dom.status.textContent = statusLine(duel);
    renderMoves(duel);
  }

  window.RelayDuels = window.RelayDuels || {};
  window.RelayDuels.rps_duel = {
    mount: function (container, duel, api) {
      state = { container: container, api: api, dom: build(container) };
      render(duel);
    },
    update: function (duel) {
      if (state) render(duel);
    },
    unmount: function () {
      if (state && state.container) state.container.innerHTML = "";
      state = null;
    },
  };

  // The fallback keeps an unknown future duel game playable rather than blank.
  window.RelayDuels.fallback = window.RelayDuels.rps_duel;
})();
