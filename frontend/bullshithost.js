/* BULLSHIT QUIZ — the host's screen.
 *
 * The host runs the game and never plays. They are on the group call too, and
 * probably sharing this screen, so the server gives it exactly what a guesser
 * sees (backend/bsroom.py) and nothing more: no answer key, no author, no
 * roles until the reveal. This page only adds the buttons that move the game
 * on, one big one per phase.
 *
 * No build step (see CLAUDE.md). Needs bullshit-common.js loaded first.
 */
(function () {
  "use strict";

  var h = BS.h;
  var root = document.getElementById("host");
  var toastEl = document.getElementById("toast");
  var socket = null;
  var live = null;

  var toastTimer = null;
  function toast(text) {
    toastEl.textContent = text;
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.hidden = true; }, 3200);
  }

  function step(action) {
    socket.send({ type: "bs_host", action: action });
  }

  function bigButton(text, action, enabled) {
    return h("button", { class: "bs-btn bs-btn--go", type: "button", disabled: !enabled,
                         text: text, onclick: function () { step(action); } });
  }

  /* Who is here, and in a round who has committed. Public information: the
     speakers are named on the options anyway, and every speaker commits the
     same way, truthful or not. */
  function roster(s) {
    return h("ul", { class: "bs-roster" }, (s.players || []).map(function (p) {
      var tag = s.phase === "lobby" ? (p.submitted ? "wrote it" : "writing")
        : p.speaker ? (p.committed ? "speaker, ready" : "speaker")
        : s.phase === "open" || s.phase === "votes" ? (p.committed ? "locked" : "thinking")
        : p.relegated ? "guessing only"
        : "";
      var done = s.phase === "lobby" ? p.submitted : p.committed;
      return h("li", { class: "bs-roster__item" + (done ? " is-done" : "") + (p.connected ? "" : " is-away") },
        h("span", { class: "bs-roster__dot", "aria-hidden": "true" }),
        h("span", { class: "bs-roster__name", text: p.name }),
        tag || !p.connected
          ? h("span", { class: "bs-roster__tag", text: p.connected ? tag : "away" }) : null);
    }));
  }

  function lobby(s) {
    var ready = s.registered >= 3 && s.submitted === s.registered;
    var hint = s.registered < 3 ? "You need at least 3 players."
      : !ready ? "Waiting on " + (s.registered - s.submitted) + " more question" +
                 (s.registered - s.submitted === 1 ? "." : "s.")
      : "Everyone's question is in. " + s.registered + " players means " + s.registered + " rounds.";
    return [
      BS.title("The lobby"),
      BS.sub("Players sign in from their own links and write one question each."),
      BS.meter("Questions in", s.submitted, s.registered),
      roster(s),
      bigButton("Start the game", "start", ready),
      h("p", { class: "bs-missing", text: hint })
    ];
  }

  function round(s) {
    var nodes = [BS.questionCard(s)];
    var opts = {};
    if (s.phase === "votes" || s.phase === "reveal") opts.counts = s.counts;
    if (s.phase === "reveal") opts.truth = s.truth.key;
    if (s.phase === "reveal") nodes.unshift(BS.verdict(s));
    nodes.push(BS.optionTiles(s, opts));

    if (s.phase === "briefing") {
      nodes.push(BS.meter("Speakers ready", s.speakers_in, s.speakers));
      nodes.push(bigButton("Open the discussion", "open", s.speakers_in === s.speakers));
      nodes.push(h("p", { class: "bs-missing",
        text: "Speakers are reading their roles. Bullshitters lock their wagers now." }));
    } else if (s.phase === "open") {
      nodes.push(BS.meter("Guesses in", s.guessers_in, s.guessers));
      nodes.push(bigButton("Lock the votes", "lock", s.guessers_in === s.guessers));
      nodes.push(h("p", { class: "bs-missing",
        text: "Let the speakers make their case on the call." }));
    } else if (s.phase === "votes") {
      nodes.push(bigButton("Reveal the truth", "reveal", true));
    } else if (s.phase === "reveal") {
      nodes.push(BS.leaderboard(s, "Scores"));
      nodes.push(bigButton(s.round >= s.rounds ? "Final scores" : "Next round", "next", true));
    }
    nodes.push(roster(s));
    return nodes;
  }

  /* Too few players online to deal anything. Players drift back by
     reopening their links; the host retries when they have. */
  function waiting(s) {
    return [
      BS.title("Waiting for players"),
      BS.sub("Not enough players are online to deal the next round. Ask people to reopen their links, then try again."),
      roster(s),
      bigButton("Try again", "next", true)
    ];
  }

  function final(s) {
    var names = (s.winners || []).map(function (id) { return BS.nameOf(s, id); });
    return [
      h("div", { class: "bs-burst", "aria-hidden": "true" }, h("span", { text: "GAME OVER" })),
      BS.title(names.length > 1 ? "Joint winners" : "The winner", true),
      BS.sub(names.join(" and ") || "Nobody", true),
      s.dropped ? BS.sub(s.dropped + " question" + (s.dropped === 1 ? " was" : "s were") +
        " dropped because " + (s.dropped === 1 ? "its author" : "their authors") +
        " never came back.", true) : null,
      BS.leaderboard(s, "Final scores")
    ];
  }

  function resetButton() {
    return h("button", { class: "bs-btn bs-btn--ghost bs-btn--small", type: "button",
      text: "Reset the room",
      onclick: function () {
        if (window.confirm("Reset the room? Scores are lost. Questions are kept.")) step("reset");
      } });
  }

  function render() {
    var s = live;
    var nodes = s.phase === "lobby" ? lobby(s) : s.phase === "final" ? final(s)
      : s.phase === "waiting" ? waiting(s) : round(s);
    if (s.phase !== "lobby" && s.phase !== "waiting") {
      nodes.unshift(h("div", { class: "bs-head" },
        h("span", { class: "bs-sticker", text: s.phase === "final" ? "Done" : "Round " + s.round + " of " + s.rounds })));
    }
    nodes.push(resetButton());
    root.innerHTML = "";
    nodes.forEach(function (n) { if (n) root.appendChild(n); });
  }

  socket = BS.connect("", {
    onState: function (state) {
      var moved = !live || live.phase !== state.phase || live.round !== state.round;
      live = state;
      render();
      if (moved) window.scrollTo(0, 0);
    },
    onError: toast,
    onClosed: function (code) {
      toast(code === 4001 ? "The host screen opened in another tab." : "Sign in with the host key again.");
    }
  });
})();
