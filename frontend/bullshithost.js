/* BULLSHIT QUIZ — the host's screen.
 *
 * The host runs the game and never plays. They are on the group call too, and
 * probably sharing this screen, so the server gives it exactly what a guesser
 * sees (backend/bsroom.py) and nothing more: no answer key, no author, no
 * roles until the reveal. This page adds the buttons that move the game on,
 * one big one per phase, and a few safety valves:
 *
 *   - Mark gone: apply the leaving rule to somebody now, not after the grace
 *     period, for a player who has stopped answering.
 *   - Lock anyway: close the votes while a guesser is still thinking. They
 *     cast no vote; nothing is guessed for them.
 *   - Undo reset: for BS_UNDO_SECONDS after "Reset the room".
 *   - Remove (lobby only) and New game night.
 *   - The talking clock: +30 s, End the talking, and Done for a floor perk
 *     that has said its piece. A Forbidden Words slip counter per speaker.
 *
 * Perks show here as the room sees them: public ones when played, secret
 * ones at the reveal. The one exception is False Witness, which the host
 * referees, so it sits in a folded host-only panel like the round history.
 *
 * Players join with the code shown in the lobby; there is no invite list.
 * The round history panel sits outside the part of the page that redraws on
 * every update, so it does not snap shut whenever somebody joins.
 *
 * No build step (see CLAUDE.md). Needs bullshit-common.js loaded first.
 */
(function () {
  "use strict";

  var h = BS.h;
  var root = document.getElementById("host");
  var toastEl = document.getElementById("toast");
  var panelsEl = document.getElementById("panels");
  var socket = null;
  var live = null;
  var undoUntil = 0;          // client clock: when the Undo reset bar disappears
  var UNDO_MS = 10000;        // matches BS_UNDO_SECONDS; the server has the final say

  document.getElementById("mute-slot").replaceWith(BS.muteButton());

  var toastTimer = null;
  function toast(text) {
    toastEl.textContent = text;
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.hidden = true; }, 3200);
  }

  function step(action, extra) {
    var msg = { type: "bs_host", action: action };
    if (extra) Object.keys(extra).forEach(function (k) { msg[k] = extra[k]; });
    socket.send(msg);
  }

  function bigButton(text, action, enabled) {
    return h("button", { class: "bs-btn bs-btn--go", type: "button", disabled: !enabled,
                         text: text, onclick: function () { step(action); } });
  }

  /* ------------------------------------------------------------- roster -- */

  /* Who is here, and in a round who has committed. Public information: the
     speakers are named on the options anyway, and every speaker commits the
     same way, truthful or not. During a game each chip also carries Mark
     gone, except for people already relegated. */
  function roster(s) {
    var inGame = s.phase !== "lobby" && s.phase !== "final";
    return h("ul", { class: "bs-roster" }, (s.players || []).map(function (p) {
      var tag = s.phase === "lobby" ? (p.submitted ? "wrote it" : "writing")
        : p.speaker ? (p.committed ? "speaker, ready" : "speaker")
        : s.phase === "open" || s.phase === "votes" ? (p.committed ? "locked" : "thinking")
        : p.relegated ? "guessing only"
        : "";
      var done = s.phase === "lobby" ? p.submitted : p.committed;
      var remove = s.phase === "lobby"
        ? h("button", {
            class: "bs-roster__gone", type: "button", text: "Remove",
            "aria-label": "Remove " + p.name + " from the lobby",
            onclick: function () {
              if (window.confirm("Remove " + p.name + " from the lobby? Their question goes too. " +
                                 "They can join again with the code.")) {
                step("remove", { player: p.id });
              }
            }
          })
        : null;
      var gone = inGame && !p.relegated
        ? h("button", {
            class: "bs-roster__gone", type: "button", text: "Mark gone",
            "aria-label": "Mark " + p.name + " as gone",
            onclick: function () {
              if (window.confirm("Mark " + p.name + " as gone? They keep their score but only " +
                                 "guess for the rest of the game. Use this if they've stopped responding.")) {
                step("drop", { player: p.id });
              }
            }
          })
        : null;
      return h("li", { class: "bs-roster__item" + (done ? " is-done" : "") +
                         (p.connected && !p.reconnecting ? "" : " is-away") },
        h("span", { class: "bs-roster__dot", "aria-hidden": "true" }),
        h("span", { class: "bs-roster__name", text: p.name }),
        tag || !p.connected || p.reconnecting
          ? h("span", { class: "bs-roster__tag",
                        text: p.reconnecting ? "reconnecting" : p.connected ? tag : "away" })
          : null,
        gone, remove);
    }));
  }

  /* ----------------------------------------------------------- join code -- */

  /* The code and link the host drops in the group chat. The code only lets
     people in while the lobby is open. */
  function joinCard(s) {
    var link = location.origin + "/bullshit?code=" + s.code;
    var copy = h("button", { class: "bs-btn bs-btn--inline", type: "button", text: "Copy link" });
    copy.addEventListener("click", function () {
      var done = function () { copy.textContent = "Copied"; };
      if (navigator.clipboard) {
        navigator.clipboard.writeText(link).then(done, function () { linkBox.select(); });
      } else {
        linkBox.select();
      }
    });
    var linkBox = h("input", { class: "bs-field bs-joincard__link", type: "text", readonly: true,
                               "aria-label": "Join link" });
    linkBox.value = link;
    return h("div", { class: "bs-card bs-joincard" },
      h("span", { class: "bs-label", text: "Join code" }),
      h("p", { class: "bs-joincard__code", text: s.code || "...." }),
      h("p", { class: "bs-joincard__hint",
               text: "Share this in the group chat. Players sign in to Game Night, enter the code, and write one question each." }),
      h("div", { class: "bs-joincard__row" }, linkBox, copy));
  }

  /* -------------------------------------------------------------- phases -- */

  function lobby(s) {
    var ready = s.registered >= 3 && s.submitted === s.registered;
    var hint = s.registered < 3 ? "You need at least 3 players."
      : !ready ? "Waiting on " + (s.registered - s.submitted) + " more question" +
                 (s.registered - s.submitted === 1 ? "." : "s.")
      : "Everyone's question is in. " + s.registered + " players means " + s.registered +
        " rounds. Starting opens the perk draft.";
    return [
      BS.title("The lobby"),
      joinCard(s),
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
        text: s.speakers_in === s.speakers
          ? "Every speaker is ready."
          : "Speakers are reading their roles. If one has stopped responding, use Mark gone below." }));
    } else if (s.phase === "open" && !(s.talk && s.talk.ballot)) {
      nodes.unshift(BS.talkCard(s));
      var floor = BS.floorCard(s);
      if (floor) {
        nodes.unshift(floor);
        nodes.splice(1, 0, h("button", { class: "bs-btn bs-btn--ghost bs-btn--small bs-btn--floor",
          type: "button", text: "Done, next", onclick: function () { step("floor_done"); } }));
      }
      nodes.push(h("div", { class: "bs-hostclock" },
        h("button", { class: "bs-btn bs-btn--ghost", type: "button", text: "+30 s",
                      onclick: function () { step("extend"); } }),
        h("button", { class: "bs-btn bs-btn--go", type: "button",
                      text: s.talk && s.talk.left <= 0 ? "Waiting on Last Words" : "End the talking",
                      disabled: !!(s.talk && s.talk.left <= 0),
                      onclick: function () { step("end_talk"); } })));
      nodes.push(h("p", { class: "bs-missing",
        text: "Let the speakers make their case. Voting opens when the clock runs out, or when you end the talking." }));
    } else if (s.phase === "open") {
      var waitingOn = s.guessers - s.guessers_in;
      nodes.unshift(BS.talkCard(s));
      nodes.push(BS.meter("Guesses in", s.guessers_in, s.guessers));
      nodes.push(bigButton("Lock the votes", "lock", waitingOn === 0));
      if (waitingOn > 0) {
        nodes.push(h("button", {
          class: "bs-btn bs-btn--ghost", type: "button",
          text: "Lock anyway (" + waitingOn + " still thinking)",
          onclick: function () {
            if (window.confirm("Lock the votes now? " + waitingOn + " guesser" +
                               (waitingOn === 1 ? " hasn't" : "s haven't") +
                               " answered. They get no vote this round and lose nothing.")) {
              step("lock_anyway");
            }
          }
        }));
      }
      nodes.push(h("p", { class: "bs-missing",
        text: "Voting is open. Lock once everyone is in." }));
    } else if (s.phase === "votes") {
      if ((s.tied || []).length) {
        nodes.push(h("p", { class: "bs-missing",
          text: "A tie at the top. Anyone holding Kingmaker can settle it now, before you reveal." }));
      }
      nodes.push(bigButton("Reveal the truth", "reveal", true));
    } else if (s.phase === "reveal") {
      nodes.push(BS.leaderboard(s, "Scores"));
      nodes.push(bigButton(s.round >= s.rounds ? "Final scores" : "Next round", "next", true));
    }
    nodes.push(BS.perkFeed(s, {
      title: s.phase === "reveal" ? "Every perk this round" : "Perks this round",
      onSlip: function (id, delta) { step("slip", { play: id, step: delta }); }
    }));
    nodes.push(roster(s));
    return nodes;
  }

  /* The perk draft: the order, whose turn it is, what is left. The host
     starts the picks and, once every perk is taken, the quiz. */
  function draft(s) {
    var d = s.draft;
    var active = d.stage === "picking" ? d.order[d.current] : null;
    var nodes = [BS.title("The perk draft")];
    if (d.stage === "trading") {
      nodes.push(BS.sub("Players can trade places now: they swap positions and a wager card each. Start the picks when the haggling's done."));
      nodes.push(bigButton("Start the picks", "draft_go", true));
    } else if (d.stage === "picking") {
      nodes.push(h("div", { class: "bs-draftturn" },
        h("span", { class: "bs-draftturn__label", text: BS.nameOf(s, active) + " is picking" }),
        BS.clock(d.left, "bs-clock--big"),
        h("p", { class: "bs-draftturn__note",
                 text: "Each pick gets " + d.pick_seconds + " seconds. Out of time means a random perk." })));
    } else {
      nodes.push(BS.sub("Every perk is taken. Who holds what is public from here on."));
      nodes.push(bigButton("Start the quiz", "begin", true));
    }
    nodes.push(BS.draftOrder(s));
    if (d.stage !== "complete") {
      nodes.push(h("p", { class: "bs-hint", text: "Perks left (" + d.available.length + ")" }));
      nodes.push(BS.draftPool(s));
    }
    nodes.push(BS.draftLog(s));
    nodes.push(roster(s));
    return nodes;
  }

  /* Too few players online to deal anything. Players drift back by
     signing in again; the host retries when they have. */
  function waiting(s) {
    return [
      BS.title("Waiting for players"),
      BS.sub("Not enough players are online to deal the next round. Ask people to sign back in, then try again."),
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

  /* ---------------------------------------------------------- reset/undo -- */

  function newGameButton() {
    return h("button", { class: "bs-btn bs-btn--ghost bs-btn--small", type: "button",
      text: "New game night",
      onclick: function () {
        if (window.confirm("Start a new game night? Everyone is taken out and every question " +
                           "is deleted, and there's a new join code. This can't be undone.")) {
          step("new_game");
        }
      } });
  }

  function resetButton() {
    return h("button", { class: "bs-btn bs-btn--ghost bs-btn--small", type: "button",
      text: "Reset the room",
      onclick: function () {
        if (window.confirm("Reset the room? Scores are lost. Questions are kept. " +
                           "You'll have 10 seconds to undo.")) step("reset");
      } });
  }

  function undoBar() {
    if (Date.now() > undoUntil) return null;
    return h("div", { class: "bs-undo", role: "status" },
      h("span", { text: "The room was reset." }),
      h("button", { class: "bs-btn bs-btn--inline", type: "button", text: "Undo",
                    onclick: function () { undoUntil = 0; step("undo_reset"); } }));
  }

  /* ---------------------------------------------------------- invitations -- */

  /* Round history: host only, and collapsed, because the host may be sharing
     this screen on the call and players are not meant to see a running
     record of who bluffed when. Its open/closed state survives redraws for
     the same reason the invitations panel's does. */
  function buildHistoryPanel() {
    var body = h("div", { class: "bs-roundlog__body" },
      h("p", { class: "bs-invites__hint", text: "Rounds appear here as they are revealed." }));
    var panel = h("details", { class: "bs-card bs-invites bs-roundlog" },
      h("summary", { class: "bs-invites__head" },
        h("span", { class: "bs-label", text: "Round history" }),
        h("span", { class: "bs-invites__warn", text: "Host only. Don't open while screen-sharing." })),
      body);
    panelsEl.appendChild(panel);
    return { panel: panel, body: body };
  }

  var roundlog = buildHistoryPanel();

  /* False Witness: who recruited whom. Host only and folded, for the same
     reason as the history: the host may be sharing this screen. */
  function buildSecretsPanel() {
    var body = h("div", { class: "bs-roundlog__body" });
    var panel = h("details", { class: "bs-card bs-invites bs-roundlog" },
      h("summary", { class: "bs-invites__head" },
        h("span", { class: "bs-label", text: "Secret perks to referee" }),
        h("span", { class: "bs-invites__warn", text: "Host only. Don't open while screen-sharing." })),
      body);
    panelsEl.insertBefore(panel, panelsEl.firstChild);
    return { panel: panel, body: body };
  }

  var secrets = buildSecretsPanel();

  function paintSecrets(s) {
    var list = s.host_secrets || [];
    secrets.panel.hidden = !list.length;
    secrets.body.innerHTML = "";
    list.forEach(function (p) {
      secrets.body.appendChild(h("p", { class: "bs-invites__hint",
        text: "False Witness: " + BS.nameOf(s, p.by) + " recruited " + BS.nameOf(s, p.target) +
              " to say a few words for option " + p.option + " during the talking." }));
    });
  }

  function paintHistory(s) {
    var count = (s.history || []).length;
    roundlog.panel.hidden = s.phase === "lobby";
    roundlog.body.innerHTML = "";
    var list = count ? BS.history(s, { title: count + " round" + (count === 1 ? "" : "s") + " revealed" }) : null;
    roundlog.body.appendChild(list || h("p", { class: "bs-invites__hint",
      text: "Rounds appear here as they are revealed." }));
  }

  /* -------------------------------------------------------------- render -- */

  function render() {
    var s = live;
    var nodes = s.phase === "lobby" ? lobby(s) : s.phase === "final" ? final(s)
      : s.phase === "waiting" ? waiting(s) : s.phase === "draft" ? draft(s) : round(s);
    if (s.phase !== "lobby" && s.phase !== "waiting") {
      nodes.unshift(h("div", { class: "bs-head" },
        h("span", { class: "bs-sticker", text: s.phase === "final" ? "Done"
          : s.phase === "draft" ? "Draft" : "Round " + s.round + " of " + s.rounds })));
    }
    if (s.phase !== "lobby" && s.phase !== "draft" && s.phase !== "final") nodes.push(BS.tablePerks(s));
    nodes.unshift(undoBar());
    nodes.push(h("div", { class: "bs-hostfoot" },
      s.phase === "lobby" ? null : resetButton(), newGameButton()));
    root.innerHTML = "";
    nodes.forEach(function (n) { if (n) root.appendChild(n); });
  }

  function cue(before, after) {
    if (!before) return;
    if (before.phase !== after.phase) {
      if (after.phase === "reveal") BS.play("stamp");
      if (after.phase === "final") BS.play("fanfare");
      return;
    }
    var had = {};
    (before.perks || []).forEach(function (p) { had[p.id] = p.status; });
    (after.perks || []).forEach(function (p) {
      if (!(p.id in had)) BS.play("pop");
      else if (had[p.id] !== "running" && p.status === "running") BS.play("stamp");
    });
  }

  socket = BS.connect("as=host", {
    onState: function (state) {
      var moved = !live || live.phase !== state.phase || live.round !== state.round;
      if (state.can_undo_reset && !(live && live.can_undo_reset)) {
        undoUntil = Date.now() + UNDO_MS;
        setTimeout(function () { if (live) render(); }, UNDO_MS + 50);
      }
      if (!state.can_undo_reset) undoUntil = 0;
      cue(live, state);
      live = state;
      render();
      if (moved) window.scrollTo(0, 0);
      paintHistory(state);
      paintSecrets(state);
    },
    onError: toast,
    onClosed: function (code) {
      toast(code === 4001 ? "The host screen opened in another tab." : "Sign in with the host key again.");
    }
  });
})();
