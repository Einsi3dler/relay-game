// RPS DUEL renderer — the Duelist's whole view.
//
// Unlike a puzzle renderer (window.RelayGames), a duel is one long-lived
// object that changes phase under the same id, so this exposes update() as
// well as mount()/unmount(). The shell calls update() on every snapshot.
//
// The client never learns the opponent's move before the server reveals it:
// duel.choices only ever contains what the server chose to send. Everything
// here renders from that, so there is nothing to peek at in the DOM either.
//
// The card art lives in /static/assets/duels/*.svg, one file per move, drawn
// rather than generated. Swapping a file for a richer illustration needs no
// change here: the slot is an <img> and the art is whatever that file is.
(function () {
  "use strict";

  var LABEL = { rock: "Rock", paper: "Paper", scissors: "Scissors" };
  var ART = "/static/assets/duels/";

  var state = null;

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function initials(name) {
    return String(name || "?").trim().charAt(0).toUpperCase() || "?";
  }

  // What this move does to the one it beats, straight off the payload the
  // server sends (`BEATS` in backend/games/duel1_rps.py). Not flavour text
  // invented here: a card that stated the rules wrongly would be worse than a
  // card that stated nothing.
  var VERB = { rock: "Crushes", paper: "Covers", scissors: "Cuts" };

  function subtitle(move, beats) {
    var loser = beats && beats[move];
    if (!loser) return "";
    return (VERB[move] || "Beats") + " " + (LABEL[loser] || loser).toLowerCase();
  }

  // The hand a seat is showing: the played move once the server reveals it, a
  // lock while it is committed but secret, and nothing at all before that.
  function handNode(move, locked) {
    if (move) {
      var art = document.createElement("img");
      art.src = ART + move + ".svg";
      art.alt = LABEL[move] || move;
      return art;
    }
    var dots = el("span", "dl-hand__wait", locked ? "●" : "…");
    dots.setAttribute("aria-label", locked ? "locked in" : "still choosing");
    return dots;
  }

  function pipRow(host, won, needed) {
    host.innerHTML = "";
    for (var i = 0; i < needed; i++) {
      host.appendChild(el("span", "dl-pip" + (i < won ? " is-won" : "")));
    }
    host.setAttribute("aria-label", won + " of " + needed + " rounds won");
  }

  function seat(side) {
    var box = el("div", "dl-seat dl-seat--" + side);
    var frame = el("span", "dl-portrait");
    frame.appendChild(el("span", "dl-portrait__mark", "?"));
    box.appendChild(frame);
    var body = el("span", "dl-seat__body");
    body.appendChild(el("span", "dl-hand"));
    body.appendChild(el("span", "dl-seat__name", ""));
    body.appendChild(el("span", "dl-pips"));
    box.appendChild(body);
    return box;
  }

  function build(container) {
    var root = el("div", "dl");

    var seats = el("div", "dl-seats");
    var left = seat("left");
    var versus = el("div", "dl-vs");
    versus.appendChild(el("span", null, "VS"));
    var right = seat("right");
    seats.appendChild(left);
    seats.appendChild(versus);
    seats.appendChild(right);
    root.appendChild(seats);

    var call = el("div", "dl-call");
    call.appendChild(el("span", "dl-call__text", ""));
    root.appendChild(call);

    var cards = el("div", "dl-cards");
    root.appendChild(cards);
    container.appendChild(root);

    var parts = function (box) {
      return {
        seat: box,
        mark: box.querySelector(".dl-portrait__mark"),
        portrait: box.querySelector(".dl-portrait"),
        hand: box.querySelector(".dl-hand"),
        name: box.querySelector(".dl-seat__name"),
        pips: box.querySelector(".dl-pips"),
      };
    };
    return {
      root: root, cards: cards,
      call: call, callText: call.querySelector(".dl-call__text"),
      you: parts(left), them: parts(right),
    };
  }

  function renderMoves(duel) {
    var open = duel.phase === "choosing";
    var mine = duel.you;
    var alreadyChose = !!(mine && duel.locked && duel.locked[mine]);
    var available = (duel.payload && duel.payload.moves) || [];
    var beats = (duel.payload && duel.payload.beats) || null;

    // Rebuild only when the button set or its enabled-ness actually changes,
    // so a re-render never steals a click mid-press.
    var signature = available.join(",") + "|" + open + "|" + alreadyChose +
      "|" + duel.round;
    if (state.movesSignature === signature) return;
    state.movesSignature = signature;
    state.dom.cards.innerHTML = "";

    if (!mine) return; // a Grandmaster watches; they never get buttons

    available.forEach(function (move) {
      var card = el("button", "dl-card dl-card--" + move);
      card.type = "button";
      card.disabled = !open || alreadyChose;

      var art = document.createElement("img");
      art.src = ART + move + ".svg";
      art.alt = "";
      art.className = "dl-card__art";
      card.appendChild(art);

      var plate = el("span", "dl-card__plate");
      plate.appendChild(el("span", "dl-card__name", LABEL[move] || move));
      var sub = subtitle(move, beats);
      if (sub) plate.appendChild(el("span", "dl-card__sub", sub));
      card.appendChild(plate);

      card.addEventListener("click", function () {
        if (card.disabled) return;
        // Lock the row immediately; the server confirms via the next snapshot.
        var all = state.dom.cards.querySelectorAll("button");
        Array.prototype.forEach.call(all, function (other) {
          other.disabled = true;
          other.classList.add("is-spent");
        });
        card.classList.remove("is-spent");
        card.classList.add("is-chosen");
        state.api.choose(move, duel.id, duel.round);
      });
      state.dom.cards.appendChild(card);
    });
  }

  function statusLine(duel) {
    if (duel.phase === "done") {
      if (!duel.you) return "Duel over.";
      return duel.winner_side === duel.you
        ? "You won the duel" : "You lost the duel";
    }
    if (duel.phase === "reveal") {
      var last = duel.last_round;
      if (!last) return "Round over.";
      if (!last.winner) return "A tie. Replaying the round.";
      if (!duel.you) return "Round " + last.round + " decided.";
      return last.winner === duel.you
        ? "You took that round" : "They took that round";
    }
    var mine = duel.you;
    if (mine && duel.locked && duel.locked[mine]) return "Locked in. Waiting.";
    if (!mine) return "Round " + duel.round + " in progress.";
    return "Choose fast";
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
    // Seats stay in a/b order for a watching Grandmaster: neither one is "you",
    // so calling one of them yours would be a lie about whose duel this is.
    var here = mine || "a";
    var there = theirs || "b";

    [[dom.you, here], [dom.them, there]].forEach(function (pair) {
      var box = pair[0], side = pair[1];
      var who = names[side] || (side === here && mine ? "You" : "Opponent");
      box.name.textContent = who;
      box.mark.textContent = initials(who);
      box.hand.innerHTML = "";
      box.hand.appendChild(handNode(choices[side], locked[side]));
      pipRow(box.pips, wins[side] || 0, needed);
      box.seat.classList.toggle("is-mine", !!mine && side === mine);
      box.seat.classList.toggle("is-locked", !!locked[side] && !choices[side]);
      box.seat.classList.toggle(
        "is-victor", duel.phase === "done" && duel.winner_side === side);
    });

    dom.root.className = "dl dl--" + duel.phase;
    dom.callText.textContent = statusLine(duel);
    dom.call.classList.toggle(
      "is-urgent", duel.phase === "choosing" && !(mine && locked[mine]));
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
