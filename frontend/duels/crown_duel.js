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

  // One portrait per character, drawn rather than typed. Swapping a file for a
  // richer illustration needs no change here: the src is built from the type.
  var ART_PATH = "/static/assets/duels/";
  var LABEL = {
    king: "King", knight: "Knight", guard: "Guard",
    assassin: "Assassin", peasant: "Peasant",
  };

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

  // A strategy round's choice is a word, not a card, so it shows as a word.
  var STRATEGY_LABEL = { normal: "Played on", sacrifice: "Sacrificed" };

  function art(type) {
    var img = document.createElement("img");
    img.src = ART_PATH + type + ".svg";
    img.alt = "";
    img.className = "cd-art";
    return img;
  }

  // What a seat is showing. Before the reveal that is nothing at all, which is
  // the whole point of the game: never a placeholder that hints at a card.
  function fillHand(host, choice, locked) {
    host.innerHTML = "";
    if (choice && LABEL[choice]) {
      host.appendChild(art(choice));
      host.appendChild(el("span", "cd-pick__name", LABEL[choice]));
      return;
    }
    if (choice) {
      host.appendChild(el("span", "cd-pick__word", STRATEGY_LABEL[choice] || choice));
      return;
    }
    host.appendChild(el("span", "cd-pick__back" + (locked ? " is-locked" : "")));
    host.appendChild(el("span", "cd-pick__name",
      locked ? "Locked in" : "Choosing"));
  }

  // Crowns taken, hollow for the rest of the race.
  function pips(host, won, needed) {
    host.innerHTML = "";
    for (var i = 0; i < needed; i++) {
      host.appendChild(el("span", "cd-crown" + (i < won ? " is-won" : "")));
    }
    host.setAttribute("aria-label", won + " of " + needed + " crowns");
  }

  // A scoreboard seat: who, how many crowns, and how much hand is left. The
  // card they are holding is never here — only what the server has revealed.
  function seat(kind) {
    var root = el("div", "cd-seat cd-seat--" + kind);
    var body = el("div", "cd-seat__body");
    var name = el("div", "cd-seat__name", kind === "you" ? "You" : "Opponent");
    var score = el("div", "cd-crowns");
    var cards = el("div", "cd-seat__sub", "");
    body.appendChild(name);
    body.appendChild(score);
    body.appendChild(cards);
    var shield = el("span", "cd-shield");
    if (kind === "you") { root.appendChild(shield); root.appendChild(body); }
    else { root.appendChild(body); root.appendChild(shield); }
    return { root: root, name: name, score: score, cards: cards };
  }

  function pickSlot(kind, label) {
    var box = el("div", "cd-pick cd-pick--" + kind);
    var face = el("div", "cd-pick__face");
    box.appendChild(face);
    box.appendChild(el("span", "cd-pick__label", label));
    return { root: box, face: face };
  }

  function build(container) {
    var root = el("div", "cd");

    var top = el("div", "cd-top");
    var you = seat("you");
    var them = seat("them");
    var title = el("div", "cd-title");
    title.appendChild(el("h2", null, "Crown Duel"));
    var round = el("div", "cd-round", "");
    title.appendChild(round);
    top.appendChild(you.root);
    top.appendChild(title);
    top.appendChild(them.root);
    root.appendChild(top);

    var body = el("div", "cd-body");
    var rules = el("aside", "cd-rules");
    body.appendChild(rules);

    var arena = el("div", "cd-arena");
    var status = el("div", "cd-status", "");
    arena.appendChild(status);
    var duelRow = el("div", "cd-versus");
    var yourPick = pickSlot("mine", "Your pick");
    var theirPick = pickSlot("theirs", "Their pick");
    duelRow.appendChild(yourPick.root);
    duelRow.appendChild(el("span", "cd-versus__mark", "VS"));
    duelRow.appendChild(theirPick.root);
    arena.appendChild(duelRow);
    body.appendChild(arena);

    var aside = el("aside", "cd-watch");
    body.appendChild(aside);
    root.appendChild(body);

    var stage = el("div", "cd-stage");
    root.appendChild(stage);
    container.appendChild(root);
    return {
      root: root, round: round, status: status, stage: stage,
      rules: rules, watch: aside,
      yourPick: yourPick, theirPick: theirPick,
      you: you, them: them,
    };
  }

  // The counter table, built from the payload's own `beats` map. Not a
  // sentence written here: a hand-written summary of the rules is one balance
  // change away from lying, and this one cannot be.
  function renderRules(payload) {
    var host = state.dom.rules;
    var beats = payload.beats || {};
    var types = payload.types || [];
    var signature = JSON.stringify([types, beats]);
    if (state.rulesSignature === signature) return;
    state.rulesSignature = signature;
    host.innerHTML = "";

    host.appendChild(el("h3", "cd-rules__head", "How it works"));
    var list = el("ul", "cd-rules__list");
    types.forEach(function (type) {
      var row = el("li", "cd-rules__row cd-rules__row--" + type);
      row.appendChild(art(type));
      row.appendChild(el("span", "cd-rules__who", LABEL[type] || type));
      row.appendChild(el("span", "cd-rules__gt", "beats"));
      var prey = el("span", "cd-rules__prey");
      (beats[type] || []).forEach(function (loser) {
        var mark = art(loser);
        mark.title = LABEL[loser] || loser;
        prey.appendChild(mark);
      });
      row.appendChild(prey);
      list.appendChild(row);
    });
    host.appendChild(list);
    host.appendChild(el("p", "cd-rules__note",
      "Only the same card against itself draws."));
  }

  // --- the stage -----------------------------------------------------------

  function send(duel, move) {
    // Lock the stage immediately; the server confirms with the next snapshot.
    state.sending = true;
    state.sacrifice = null;
    state.pick = null;
    state.api.choose(move, duel.id, duel.round);
  }

  function renderStrategy(duel, payload) {
    var stage = state.dom.stage;
    var row = el("div", "cd-strategy");

    var normal = button("cd-choice", "");
    normal.appendChild(el("strong", null, "Play on"));
    normal.appendChild(el("span", null, "Keep your hand as it is"));
    normal.disabled = state.locked;

    var offer = button("cd-choice cd-choice--sacrifice", "");
    offer.appendChild(el("strong", null, "Royal Sacrifice"));
    offer.appendChild(el("span", null, "Burn two cards to rewrite a third"));
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
      stage.appendChild(el("p", "cd-note",
        "A sacrifice costs two cards and one to play — you need three."));
    } else if (payload.sacrifice_used[duel.you]) {
      stage.appendChild(el("p", "cd-note", "Your Royal Sacrifice is spent."));
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
    stage.appendChild(el("p", "cd-step", step === 1
      ? "1 of 3 — choose two cards to destroy"
      : step === 2
        ? "2 of 3 — choose the card to rewrite"
        : "3 of 3 — choose what it becomes"));

    if (step < 3) {
      var cards = el("div", "cd-hand");
      available.forEach(function (card) {
        var burning = plan.burn.indexOf(card.id) >= 0;
        var node = button("cd-card cd-card--" + card.type +
          (burning ? " is-burning" : ""));
        node.appendChild(art(card.type));
        node.appendChild(el("span", "cd-card__name", LABEL[card.type]));
        node.appendChild(el("span", "cd-card__plate",
          burning ? "Destroying" : "Keep"));
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
      var types = el("div", "cd-hand");
      payload.transform_types.forEach(function (type) {
        var node = button("cd-card cd-card--" + type +
          (plan.type === type ? " is-picked" : ""));
        node.appendChild(art(type));
        node.appendChild(el("span", "cd-card__name", LABEL[type]));
        node.appendChild(el("span", "cd-card__plate",
          plan.type === type ? "Becomes this" : "Rewrite into"));
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
      stage.appendChild(el("p", "cd-note",
        "Two cards are destroyed for good. Your opponent is told a sacrifice " +
        "happened, never what changed."));
    }

    var actions = el("div", "cd-actions");
    var confirm = button("cd-lock", "Confirm sacrifice");
    confirm.disabled = state.locked || plan.burn.length !== 2 ||
      plan.target === null || plan.type === null;
    confirm.addEventListener("click", function () {
      if (confirm.disabled) return;
      confirm.disabled = true;                   // never a second press
      send(duel, "sacrifice:" + plan.burn[0] + "+" + plan.burn[1] + ">" +
        plan.target + "=" + plan.type);
      render(state.duel);
    });
    var cancel = button("cd-clear", "Cancel");
    cancel.addEventListener("click", function () {
      state.sacrifice = null;
      render(state.duel);
    });
    actions.appendChild(confirm);
    actions.appendChild(cancel);
    stage.appendChild(actions);
  }

  // Pick, then lock. The commit is one message either way, but a duel round is
  // ten seconds and a mis-tap used to be the whole round — so the card only
  // leaves the browser when the second button is pressed.
  function renderCombat(duel, payload) {
    var stage = state.dom.stage;
    var cards = el("div", "cd-hand");
    payload.hand.forEach(function (card) {
      var spent = card.status !== "available";
      var picked = state.pick === card.id;
      var node = button("cd-card cd-card--" + card.type +
        (spent ? " is-spent" : "") + (picked ? " is-picked" : ""));
      node.appendChild(art(card.type));
      node.appendChild(el("span", "cd-card__name", LABEL[card.type]));
      var plate = el("span", "cd-card__plate");
      plate.appendChild(el("span", "cd-card__state",
        spent ? "Used" : picked ? "Picked" : "Available"));
      if (card.origin !== card.type) {
        plate.appendChild(el("span", "cd-card__note", "rewritten"));
      }
      node.appendChild(plate);
      node.disabled = spent || state.locked || !state.open;
      node.addEventListener("click", function () {
        if (node.disabled) return;
        state.pick = state.pick === card.id ? null : card.id;
        render(state.duel);
      });
      cards.appendChild(node);
    });
    stage.appendChild(cards);

    var chosen = null;
    payload.hand.forEach(function (card) {
      if (card.id === state.pick && card.status === "available") chosen = card;
    });

    var actions = el("div", "cd-actions");
    var lock = button("cd-lock", "");
    lock.appendChild(el("strong", null, "Lock in"));
    lock.appendChild(el("span", null, chosen
      ? "Commit " + LABEL[chosen.type] : "Pick a card first"));
    lock.disabled = !chosen || state.locked;
    lock.addEventListener("click", function () {
      if (lock.disabled) return;
      lock.disabled = true;                      // never a second press
      send(duel, chosen.type);
      render(state.duel);
    });
    actions.appendChild(lock);

    var clear = button("cd-clear", "Change pick");
    clear.disabled = !chosen || state.locked;
    clear.addEventListener("click", function () {
      if (clear.disabled) return;
      state.pick = null;
      render(state.duel);
    });
    actions.appendChild(clear);
    stage.appendChild(actions);
  }

  function renderStage(duel) {
    var payload = duel.payload || {};
    var dom = state.dom;
    // Rebuild only when something the stage actually shows has changed, so a
    // re-render never steals a click mid-press.
    var signature = JSON.stringify([
      duel.id, duel.round, duel.phase, payload.phase, state.locked,
      state.open, payload.hand, payload.can_sacrifice, state.sacrifice,
      state.pick,
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
      pips(panel.score, crowns[side] || 0, needed);
      var count = left[side];
      var spent = payload.sacrifice_used && payload.sacrifice_used[side];
      panel.cards.textContent = count === undefined ? ""
        : count + " cards left" + (spent ? ", sacrifice spent" : "");
    });

    // The two picks facing each other. Neither carries a card until the server
    // has revealed one.
    fillHand(dom.yourPick.face, choices[seats[0]], locked[seats[0]]);
    fillHand(dom.theirPick.face, choices[seats[1]], locked[seats[1]]);

    // What the other side is doing, in the panel the mockup keeps for it.
    dom.watch.innerHTML = "";
    var theirLocked = !!locked[seats[1]];
    var watch = el("div", "cd-watch__box" + (theirLocked ? " is-ready" : ""));
    watch.appendChild(el("span", "cd-watch__ring"));
    watch.appendChild(el("strong", null, theirLocked
      ? "Their card is in" : "They are deciding"));
    watch.appendChild(el("span", null, theirLocked
      ? "Both cards land when the round resolves."
      : "You will never see what they picked before the reveal."));
    dom.watch.appendChild(watch);

    dom.root.className = "cd cd--" + duel.phase +
      (payload.phase ? " cd--" + payload.phase : "");
    dom.round.textContent = roundLine(payload);
    dom.status.textContent = statusLine(duel);
    renderRules(payload);
    renderStage(duel);
  }

  window.RelayDuels = window.RelayDuels || {};
  window.RelayDuels.crown_duel = {
    mount: function (container, duel, api) {
      state = {
        container: container, api: api, dom: build(container),
        sacrifice: null, sending: false, stageSignature: null, pick: null,
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
        state.pick = null;
      }
      render(duel);
    },
    unmount: function () {
      if (state && state.container) state.container.innerHTML = "";
      state = null;
    },
  };
})();
