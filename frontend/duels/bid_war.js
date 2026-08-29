// BID WAR renderer — the Duelist's whole view.
//
// One lot at a time: the prize on the block, the one after it, both purses,
// both scores, and a bid you set exactly. The stepper is the input that
// matters — a slider alone cannot say "seven" — with quick bids beside it for
// the common shapes.
//
// The opponent's bid is never here before the reveal: their panel shows a lock
// until `duel.choices` carries the number, and the prizes after the next one
// are not in the payload at all, so there is nothing in the DOM to read ahead.
(function () {
  "use strict";

  var QUICK = [0, 2, 4, 6];

  var state = null;

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function button(className, label) {
    var node = el("button", className, label);
    node.type = "button";
    return node;
  }

  function hand(bid, locked) {
    if (bid !== undefined && bid !== null) return String(bid);
    return locked ? "🔒" : "…";
  }

  function seat(kind) {
    var root = el("div", "duel-seat duel-seat-" + kind);
    var face = el("div", "duel-hand", "…");
    var name = el("div", "duel-name", kind === "you" ? "You" : "Opponent");
    var score = el("div", "duel-score", "0 VP");
    var coins = el("div", "duel-sub", "");
    root.appendChild(face);
    root.appendChild(name);
    root.appendChild(score);
    root.appendChild(coins);
    return { root: root, face: face, name: name, score: score, coins: coins };
  }

  function build(container) {
    var root = el("div", "duel duel-bid");
    var header = el("div", "duel-header");
    var you = seat("you");
    var them = seat("them");
    header.appendChild(you.root);
    header.appendChild(el("div", "duel-versus", "VS"));
    header.appendChild(them.root);

    var round = el("div", "duel-round", "");
    var prizes = el("div", "duel-prizes");
    var prize = el("div", "duel-prize");
    var prizeValue = el("div", "duel-prize-value", "–");
    prize.appendChild(el("div", "duel-prize-label", "On the block"));
    prize.appendChild(prizeValue);
    var next = el("div", "duel-prize duel-prize--next");
    var nextValue = el("div", "duel-prize-value", "–");
    next.appendChild(el("div", "duel-prize-label", "Next"));
    next.appendChild(nextValue);
    prizes.appendChild(prize);
    prizes.appendChild(next);

    var status = el("div", "duel-status", "");
    var stage = el("div", "duel-stage");
    var log = el("div", "duel-log");

    root.appendChild(header);
    root.appendChild(round);
    root.appendChild(prizes);
    root.appendChild(status);
    root.appendChild(stage);
    root.appendChild(log);
    container.appendChild(root);
    return {
      root: root, round: round, status: status, stage: stage, log: log,
      prizeValue: prizeValue, next: next, nextValue: nextValue,
      you: you, them: them,
    };
  }

  function clamp(value, max) {
    if (isNaN(value)) return 0;
    return Math.max(0, Math.min(max, value));
  }

  function renderStage(duel) {
    var payload = duel.payload || {};
    var max = payload.max_bid || 0;
    var dom = state.dom;
    var signature = JSON.stringify([
      duel.id, duel.round, state.open, state.locked, max, state.bid,
    ]);
    if (state.stageSignature === signature) return;
    state.stageSignature = signature;
    dom.stage.innerHTML = "";
    if (!duel.you) return;   // a Grandmaster watches; they never get controls
    if (!state.open) return;

    var stepper = el("div", "duel-stepper");
    var down = button("duel-step", "−");
    var amount = el("div", "duel-bid-value", String(state.bid));
    var up = button("duel-step", "+");
    down.disabled = state.locked || state.bid <= 0;
    up.disabled = state.locked || state.bid >= max;
    down.addEventListener("click", function () {
      if (down.disabled) return;
      state.bid = clamp(state.bid - 1, max);
      render(state.duel);
    });
    up.addEventListener("click", function () {
      if (up.disabled) return;
      state.bid = clamp(state.bid + 1, max);
      render(state.duel);
    });
    stepper.appendChild(down);
    stepper.appendChild(amount);
    stepper.appendChild(up);
    dom.stage.appendChild(stepper);

    var quick = el("div", "duel-quick");
    QUICK.concat([max]).forEach(function (value, index) {
      if (value > max) return;
      var allIn = index === QUICK.length;
      if (allIn && max === 0) return;      // "all in" on nothing is just 0
      var node = button("duel-quick-bid" + (state.bid === value ? " chosen" : ""),
        allIn ? "All in" : String(value));
      node.disabled = state.locked;
      node.addEventListener("click", function () {
        if (node.disabled) return;
        state.bid = clamp(value, max);
        render(state.duel);
      });
      quick.appendChild(node);
    });
    dom.stage.appendChild(quick);

    var lock = button("duel-lock", "Lock bid");
    lock.disabled = state.locked;
    lock.addEventListener("click", function () {
      if (lock.disabled) return;
      lock.disabled = true;                      // never a second press
      state.sending = true;
      state.api.choose(String(state.bid), duel.id, duel.round);
      render(state.duel);
    });
    dom.stage.appendChild(lock);
    dom.stage.appendChild(el("p", "duel-note",
      "Both bids are spent — the losing one buys nothing. Coins left at the " +
      "end are worth no points."));
  }

  function renderLog(payload) {
    var lots = payload.log || [];
    var dom = state.dom;
    var signature = JSON.stringify(lots.length);
    if (state.logSignature === signature) return;
    state.logSignature = signature;
    dom.log.innerHTML = "";
    lots.slice(-4).forEach(function (entry) {
      var row = el("div", "duel-log-row");
      row.appendChild(el("span", "duel-log-round",
        (entry.overtime ? "OT" : "L" + entry.auction)));
      row.appendChild(el("span", "duel-log-pair",
        (entry.a === null ? "–" : entry.a) + " vs " +
        (entry.b === null ? "–" : entry.b)));
      row.appendChild(el("span", "duel-log-winner", entry.winner
        ? "+" + entry.prize + " VP to " +
          (entry.winner === state.mine ? "you" : "them")
        : entry.prize + " VP rolls on"));
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
      if (!last) return "Lot closed.";
      if (!last.winner) {
        return "🤝 Tied bid — nobody is paid and " + last.prize +
          " VP rolls into the next lot.";
      }
      if (!duel.you) return "Lot " + last.auction + " sold.";
      return last.winner === duel.you
        ? "💰 Sold to you — +" + last.prize + " VP."
        : "❌ Sold to them — +" + last.prize + " VP.";
    }
    if (!duel.you) return "Lot " + payload.auction + " under the hammer.";
    if (state.locked) return "Bid locked — waiting…";
    return "Place your bid.";
  }

  function roundLine(payload) {
    if (payload.overtime) return "OVERTIME — a fresh, equal purse decides it";
    return "Lot " + payload.auction + " of " + payload.auctions;
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
    var coins = payload.overtime ? (payload.overtime_coins || {})
      : (payload.coins || {});
    var vp = payload.vp || {};
    var seats = mine ? [mine, theirs] : ["a", "b"];

    state.mine = mine;
    state.open = duel.phase === "choosing";
    state.locked = !!(mine && locked[mine]) || (state.sending && state.open);
    if (state.bid === null || state.bid > (payload.max_bid || 0)) {
      state.bid = clamp(state.bid || 0, payload.max_bid || 0);
    }

    dom.you.name.textContent = mine ? (names[seats[0]] || "You") : (names.a || "A");
    dom.them.name.textContent = mine
      ? (names[seats[1]] || "Opponent") : (names.b || "B");
    [dom.you, dom.them].forEach(function (panel, index) {
      var side = seats[index];
      panel.face.textContent = hand(choices[side], locked[side]);
      panel.score.textContent = (vp[side] || 0) + " VP";
      panel.coins.textContent = (coins[side] === undefined ? 0 : coins[side]) +
        (payload.overtime ? " overtime coins" : " coins");
    });

    dom.prizeValue.textContent = (payload.prize || 0) + " VP";
    dom.next.hidden = payload.next_prize === null ||
      payload.next_prize === undefined;
    dom.nextValue.textContent = (payload.next_prize || 0) + " VP";

    dom.root.className = "duel duel-bid duel-" + duel.phase;
    dom.round.textContent = roundLine(payload);
    dom.status.textContent = statusLine(duel);
    renderStage(duel);
    renderLog(payload);
  }

  window.RelayDuels = window.RelayDuels || {};
  window.RelayDuels.bid_war = {
    mount: function (container, duel, api) {
      state = {
        container: container, api: api, dom: build(container),
        bid: 0, sending: false, stageSignature: null, logSignature: null,
      };
      render(duel);
    },
    update: function (duel) {
      if (!state) return;
      if (state.round !== duel.round) {
        state.round = duel.round;
        state.sending = false;
        state.bid = 0;      // a new lot starts from nothing, not the last bid
      }
      render(duel);
    },
    unmount: function () {
      if (state && state.container) state.container.innerHTML = "";
      state = null;
    },
  };
})();
