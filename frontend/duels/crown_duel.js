// CROWN DUEL renderer — the Duelist's whole view.
//
// A Crown Duel round arrives as two: a strategy round (play normally, or spend
// the once-per-match Royal Sacrifice) and then the card round it sets up. The
// server tells this file which one is open through `payload.phase`, so the
// stage below is either the two strategy buttons, the sacrifice builder, or
// the hand.
//
// The client never learns what the opponent holds, what their sacrifice did,
// or which card they just played before the server reveals it: everything
// here renders from `duel.choices` and `duel.payload`, and the payload only
// ever carries the *viewer's* own hand plus a count of the other one. There is
// nothing extra hidden in the DOM to inspect either.
(function () {
  "use strict";

  var ART = {
    king: "👑", knight: "⚔️", guard: "🛡️", assassin: "🗡️", peasant: "🌾",
  };
  var LABEL = {
    king: "King", knight: "Knight", guard: "Guard",
    assassin: "Assassin", peasant: "Peasant",
  };
  var GUIDE = "King beats the fighters · Peasant beats King · every fighter " +
    "beats Peasant · Guard beats Knight beats Assassin beats Guard";

  var state = null;

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function button(className, label) {
    var node = el("button", className);
    node.type = "button";
    if (label !== undefined) node.textContent = label;
    return node;
  }

  // A strategy round's choice is a word, not a card: it shows as itself.
  var STRATEGY_ART = { normal: "🃏", sacrifice: "⚡" };

  function art(type) {
    return ART[type] || "❓";
  }

  function hand(choice, locked) {
    if (choice) return ART[choice] || STRATEGY_ART[choice] || "❓";
    return locked ? "🔒" : "…";
  }

  // Crowns taken, hollow for the rest of the race.
  function pips(won, needed) {
    var out = "";
    for (var i = 0; i < needed; i++) out += i < won ? "●" : "○";
    return out;
  }

  function seat(kind) {
    var root = el("div", "duel-seat duel-seat-" + kind);
    var face = el("div", "duel-hand", "…");
    var name = el("div", "duel-name", kind === "you" ? "You" : "Opponent");
    var score = el("div", "duel-pips", "");
    var cards = el("div", "duel-sub", "");
    root.appendChild(face);
    root.appendChild(name);
    root.appendChild(score);
    root.appendChild(cards);
    return { root: root, face: face, name: name, score: score, cards: cards };
  }

  function build(container) {
    var root = el("div", "duel duel-crown");
    var header = el("div", "duel-header");
    var you = seat("you");
    var them = seat("them");
    header.appendChild(you.root);
    header.appendChild(el("div", "duel-versus", "VS"));
    header.appendChild(them.root);

    var round = el("div", "duel-round", "");
    var status = el("div", "duel-status", "");
    var stage = el("div", "duel-stage");
    var guide = el("p", "duel-guide", GUIDE);

    root.appendChild(header);
    root.appendChild(round);
    root.appendChild(status);
    root.appendChild(stage);
    root.appendChild(guide);
    container.appendChild(root);
    return {
      root: root, round: round, status: status, stage: stage,
      you: you, them: them,
    };
  }

  // --- the stage -----------------------------------------------------------

  function send(duel, move) {
    // Lock the stage immediately; the server confirms with the next snapshot.
    state.sending = true;
    state.sacrifice = null;
    state.api.choose(move, duel.id, duel.round);
  }

  function renderStrategy(duel, payload) {
    var stage = state.dom.stage;
    var row = el("div", "duel-moves");

    var normal = button("duel-move", "");
    normal.appendChild(el("span", "duel-move-art", "🃏"));
    normal.appendChild(el("span", "duel-move-label", "Play normally"));
    normal.disabled = state.locked;

    var offer = button("duel-move duel-move--sacrifice", "");
    offer.appendChild(el("span", "duel-move-art", "⚡"));
    offer.appendChild(el("span", "duel-move-label", "Royal Sacrifice"));
    offer.disabled = state.locked || !payload.can_sacrifice;

    normal.addEventListener("click", function () {
      if (normal.disabled) return;
      normal.disabled = offer.disabled = true;   // never a second press
      send(duel, "normal");
      render(state.duel);
    });
    offer.addEventListener("click", function () {
      if (offer.disabled) return;
      state.sacrifice = { burn: [], target: null, type: null };
      render(state.duel);
    });
    row.appendChild(normal);
    row.appendChild(offer);
    stage.appendChild(row);

    if (!payload.can_sacrifice && !payload.sacrifice_used[duel.you]) {
      stage.appendChild(el("p", "duel-note",
        "A sacrifice costs two cards and one to play — you need three."));
    } else if (payload.sacrifice_used[duel.you]) {
      stage.appendChild(el("p", "duel-note", "Your Royal Sacrifice is spent."));
    }
  }

  // Step 1: two cards to destroy. Step 2: the card to rewrite. Step 3: what it
  // becomes. Nothing leaves the browser until the confirm.
  function renderSacrifice(duel, payload) {
    var stage = state.dom.stage;
    var plan = state.sacrifice;
    var available = payload.hand.filter(function (card) {
      return card.status === "available";
    });

    var step = plan.burn.length < 2 ? 1 : (plan.target === null ? 2 : 3);
    stage.appendChild(el("p", "duel-step-label", step === 1
      ? "1 of 3 — choose two cards to destroy"
      : step === 2
        ? "2 of 3 — choose the card to rewrite"
        : "3 of 3 — choose what it becomes"));

    if (step < 3) {
      var cards = el("div", "duel-cards");
      available.forEach(function (card) {
        var burning = plan.burn.indexOf(card.id) >= 0;
        var node = button("duel-card" + (burning ? " burning" : ""));
        node.appendChild(el("span", "duel-card-art", art(card.type)));
        node.appendChild(el("span", "duel-card-label", LABEL[card.type]));
        node.disabled = step === 2 && burning;
        node.addEventListener("click", function () {
          if (node.disabled) return;
          if (step === 1) {
            if (burning) plan.burn.splice(plan.burn.indexOf(card.id), 1);
            else plan.burn.push(card.id);
          } else {
            plan.target = card.id;
            plan.type = null;
          }
          render(state.duel);
        });
        cards.appendChild(node);
      });
      stage.appendChild(cards);
    } else {
      var target = null;
      available.forEach(function (card) {
        if (card.id === plan.target) target = card;
      });
      var types = el("div", "duel-cards");
      payload.transform_types.forEach(function (type) {
        var node = button("duel-card" + (plan.type === type ? " chosen" : ""));
        node.appendChild(el("span", "duel-card-art", art(type)));
        node.appendChild(el("span", "duel-card-label", LABEL[type]));
        // A rewrite has to rewrite something, and the crown is never minted.
        node.disabled = !!target && target.type === type;
        node.addEventListener("click", function () {
          if (node.disabled) return;
          plan.type = type;
          render(state.duel);
        });
        types.appendChild(node);
      });
      stage.appendChild(types);
      stage.appendChild(el("p", "duel-note",
        "Two cards are destroyed for good. Your opponent is told a sacrifice " +
        "happened, never what changed."));
    }

    var actions = el("div", "duel-actions");
    var confirm = button("duel-lock", "Confirm sacrifice");
    confirm.disabled = state.locked || plan.burn.length !== 2 ||
      plan.target === null || plan.type === null;
    confirm.addEventListener("click", function () {
      if (confirm.disabled) return;
      confirm.disabled = true;                   // never a second press
      send(duel, "sacrifice:" + plan.burn[0] + "+" + plan.burn[1] + ">" +
        plan.target + "=" + plan.type);
      render(state.duel);
    });
    var cancel = button("duel-cancel", "Cancel");
    cancel.addEventListener("click", function () {
      state.sacrifice = null;
      render(state.duel);
    });
    actions.appendChild(confirm);
    actions.appendChild(cancel);
    stage.appendChild(actions);
  }

  function renderCombat(duel, payload) {
    var stage = state.dom.stage;
    var cards = el("div", "duel-cards");
    payload.hand.forEach(function (card) {
      var spent = card.status !== "available";
      var node = button("duel-card" + (spent ? " spent" : ""));
      node.appendChild(el("span", "duel-card-art", art(card.type)));
      node.appendChild(el("span", "duel-card-label", LABEL[card.type]));
      if (card.origin !== card.type) {
        node.appendChild(el("span", "duel-card-note", "rewritten"));
      }
      node.disabled = spent || state.locked || !state.open;
      node.addEventListener("click", function () {
        if (node.disabled) return;
        node.disabled = true;                    // never a second press
        node.classList.add("chosen");
        send(duel, card.type);
        render(state.duel);
      });
      cards.appendChild(node);
    });
    stage.appendChild(cards);
  }

  function renderStage(duel) {
    var payload = duel.payload || {};
    var dom = state.dom;
    // Rebuild only when something the stage actually shows has changed, so a
    // re-render never steals a click mid-press.
    var signature = JSON.stringify([
      duel.id, duel.round, duel.phase, payload.phase, state.locked,
      state.open, payload.hand, payload.can_sacrifice, state.sacrifice,
    ]);
    if (state.stageSignature === signature) return;
    state.stageSignature = signature;
    dom.stage.innerHTML = "";
    if (!duel.you) return;  // a Grandmaster watches; they never get controls
    if (!state.open) return;

    if (payload.phase === "strategy") {
      if (state.sacrifice) renderSacrifice(duel, payload);
      else renderStrategy(duel, payload);
    } else {
      renderCombat(duel, payload);
    }
  }

  // --- the words -----------------------------------------------------------

  function sacrificeLine(last, you) {
    var mine = last.sacrificed[you];
    var theirs = last.sacrificed[you === "a" ? "b" : "a"];
    if (mine && theirs) return "⚡ Both hands were rewritten.";
    if (theirs) return "⚡ Your opponent performed a Royal Sacrifice.";
    if (mine) return "⚡ Your hand is rewritten. They know it happened.";
    return "Neither player sacrificed.";
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
      if (last.kind === "strategy") {
        return duel.you ? sacrificeLine(last, duel.you) : "Strategy revealed.";
      }
      if (!last.winner) return "🤝 A mirror — no Crown, both cards spent.";
      if (!duel.you) return "Round " + last.round + " decided.";
      return last.winner === duel.you
        ? "👑 You take the Crown." : "❌ They take the Crown.";
    }
    if (!duel.you) return "Round " + payload.game_round + " in progress.";
    if (state.locked) return "Locked in — waiting…";
    if (payload.phase === "strategy") {
      return state.sacrifice ? "Build your sacrifice." : "Play on, or rewrite?";
    }
    return "Choose your card.";
  }

  function roundLine(payload) {
    if (payload.sudden_death) return "SUDDEN DEATH — the first Crown takes it";
    return "Round " + payload.game_round + " of " + payload.normal_rounds +
      (payload.phase === "strategy" ? " · strategy" : " · cards");
  }

  // --- the whole view ------------------------------------------------------

  function render(duel) {
    state.duel = duel;
    var dom = state.dom;
    var payload = duel.payload || {};
    var mine = duel.you;
    var theirs = mine === "a" ? "b" : mine === "b" ? "a" : null;
    var names = duel.duellists || {};
    var choices = duel.choices || {};
    var locked = duel.locked || {};
    var crowns = payload.crowns || {};
    var left = payload.cards_left || {};
    var needed = payload.crowns_needed || 2;
    var seats = mine ? [mine, theirs] : ["a", "b"];

    state.open = duel.phase === "choosing";
    state.locked = !!(mine && locked[mine]) || (state.sending && state.open);

    dom.you.name.textContent = mine ? (names[seats[0]] || "You") : (names.a || "A");
    dom.them.name.textContent = mine
      ? (names[seats[1]] || "Opponent") : (names.b || "B");
    [dom.you, dom.them].forEach(function (panel, index) {
      var side = seats[index];
      panel.face.textContent = hand(choices[side], locked[side]);
      panel.score.textContent = pips(crowns[side] || 0, needed);
      var count = left[side];
      panel.cards.textContent = count === undefined ? "" : count + " cards" +
        (payload.sacrifice_used && payload.sacrifice_used[side] ? " · ⚡" : "");
    });

    dom.root.className = "duel duel-crown duel-" + duel.phase;
    dom.round.textContent = roundLine(payload);
    dom.status.textContent = statusLine(duel);
    renderStage(duel);
  }

  window.RelayDuels = window.RelayDuels || {};
  window.RelayDuels.crown_duel = {
    mount: function (container, duel, api) {
      state = {
        container: container, api: api, dom: build(container),
        sacrifice: null, sending: false, stageSignature: null,
      };
      render(duel);
    },
    update: function (duel) {
      if (!state) return;
      // A new round: whatever was half-built belongs to the round that ended.
      if (state.round !== duel.round) {
        state.round = duel.round;
        state.sacrifice = null;
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
