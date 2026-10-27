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
 * The night before, the lobby is where the host prepares: how many rounds,
 * and a review of every question in the bank (tag it, tidy it) without ever
 * seeing who wrote it. The review shows every answer, so it is folded like
 * the history, and it lives outside the redraw too, so an edit in progress
 * survives somebody else joining. It disappears once the game starts.
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
  var ROUNDS_MAX = 90;        // matches BS_ROUNDS_MAX; the server has the final say
  var TAGS = [                // BS_TAGS, best first; "out" is never played
    { id: "fascinating", label: "Fascinating" },
    { id: "mid", label: "Mid ground" },
    { id: "bland", label: "Bland" },
    { id: "out", label: "Leave out" }
  ];

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
      var tag = s.phase === "lobby" ? (p.submitted ? "all written" : p.written + " of " + p.wanted)
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
              if (window.confirm("Remove " + p.name + " from the lobby? Their questions go too. " +
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
               text: "Share this in the group chat, a day early if you like: the code stays the same until you start a new game night. Players sign in to Game Night, enter the code, and write three questions each." }),
      h("div", { class: "bs-joincard__row" }, linkBox, copy));
  }

  /* -------------------------------------------------------------- phases -- */

  /* How many rounds tonight. Buttons, not a text box: the lobby redraws on
     every join, and a box would lose the cursor mid-number. */
  function roundsCard(s) {
    var n = s.rounds_target;
    function stepBy(delta) {
      var next = Math.max(1, Math.min(ROUNDS_MAX, n + delta));
      return h("button", { class: "bs-btn bs-btn--inline bs-rounds__step", type: "button",
                           text: (delta > 0 ? "+" : "−") + Math.abs(delta),
                           "aria-label": (delta > 0 ? "Add " : "Take away ") + Math.abs(delta),
                           disabled: next === n,
                           onclick: function () { step("rounds", { rounds: next }); } });
    }
    return h("div", { class: "bs-card bs-rounds" },
      h("span", { class: "bs-label", text: "Rounds tonight" }),
      h("div", { class: "bs-rounds__row" },
        stepBy(-5), stepBy(-1),
        h("span", { class: "bs-rounds__num", text: String(n), "aria-live": "polite" }),
        stepBy(1), stepBy(5)),
      h("p", { class: "bs-joincard__hint",
               text: "Set this before you share the link. The game fills the rounds with the best mix of questions it can, so some players' questions won't be played and some will be played more than once." }));
  }

  function bankCounts(s) {
    var bank = s.review || [];
    var count = function (test) { return bank.filter(test).length; };
    return {
      total: bank.length,
      untagged: count(function (q) { return !q.tag; }),
      playable: count(function (q) { return q.tag && q.tag !== "out"; })
    };
  }

  function lobby(s) {
    var bank = bankCounts(s);
    var written = s.registered >= 3 && s.submitted === s.registered;
    var ready = written && bank.untagged === 0 && bank.playable > 0;
    var waiting = s.registered - s.submitted;
    var hint = s.registered < 3 ? "You need at least 3 players."
      : !written ? "Waiting on " + waiting + " more player" + (waiting === 1 ? "" : "s") +
                   " to finish writing."
      : bank.untagged ? bank.untagged + " question" + (bank.untagged === 1 ? "" : "s") +
                        " still to review, below."
      : !bank.playable ? "Every question is marked Leave out. Tag some to play them."
      : "All reviewed. Tonight plays " + Math.min(s.rounds_target, bank.playable) + " of " +
        bank.playable + " playable questions. Starting opens the perk draft.";
    return [
      BS.title("The lobby"),
      roundsCard(s),
      joinCard(s),
      BS.meter("Players done writing", s.submitted, s.registered),
      BS.meter("Questions reviewed", bank.total - bank.untagged, bank.total),
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

  /* ------------------------------------------------------------- review -- */

  /* The question bank, for the host to tag and tidy before the night. Every
     answer is in it, so it starts folded with a warning, and it carries no
     names: the server never says who wrote what. */
  var reviewEditing = null;   // id of the question with its edit form open; closing it repaints
  var onlyToReview = false;

  function buildReviewPanel() {
    var count = h("span", { class: "bs-review__count" });
    var filter = h("input", { type: "checkbox", id: "review-filter" });
    filter.addEventListener("change", function () {
      onlyToReview = filter.checked;
      if (live) paintReview(live);
    });
    var list = h("div", { class: "bs-review__list" });
    var panel = h("details", { class: "bs-card bs-invites bs-review" },
      h("summary", { class: "bs-invites__head" },
        h("span", { class: "bs-label", text: "Review the questions" }),
        count,
        h("span", { class: "bs-invites__warn",
                    text: "Shows every answer. Don't open while screen-sharing." })),
      h("p", { class: "bs-invites__hint",
               text: "Nobody's name is on these. Tag every one: Fascinating ones get played most, Bland ones only to fill the rounds, and Leave out never. Tidy the wording or the options if they need it; the author sees your version." }),
      h("label", { class: "bs-review__filter", for: "review-filter" }, filter,
        h("span", { text: "Only show the ones still to review" })),
      list);
    panelsEl.insertBefore(panel, panelsEl.firstChild);
    return { panel: panel, list: list, count: count };
  }

  var review = buildReviewPanel();

  function tagButtons(q) {
    return h("div", { class: "bs-review__tags", role: "group", "aria-label": "Tag" },
      TAGS.map(function (tag) {
        var on = q.tag === tag.id;
        return h("button", {
          class: "bs-tagbtn bs-tagbtn--" + tag.id, type: "button", text: tag.label,
          "aria-pressed": on ? "true" : "false",
          onclick: function () { step("tag", { question: q.id, tag: on ? null : tag.id }); }
        });
      }));
  }

  function reviewItem(q) {
    return h("div", { class: "bs-review__item" + (q.tag ? "" : " is-todo") },
      h("p", { class: "bs-review__q", text: q.prompt }),
      h("ol", { class: "bs-preview__opts" },
        [q.answer].concat(q.decoys).map(function (text, i) {
          return h("li", { class: i === 0 ? "is-real" : null },
            h("span", { text: text }),
            i === 0 ? h("span", { class: "bs-preview__real", text: "Real one" }) : null);
        })),
      q.explanation ? h("p", { class: "bs-review__why", text: "Why: " + q.explanation }) : null,
      tagButtons(q),
      h("div", { class: "bs-review__foot" },
        q.edited ? h("span", { class: "bs-review__edited", text: "You tidied this" }) : h("span"),
        h("button", { class: "bs-btn bs-btn--inline", type: "button", text: "Edit",
                      onclick: function () { reviewEditing = q.id; paintReview(live); } })));
  }

  function field(tag, value, label, max) {
    var input = h(tag, { class: "bs-field", "aria-label": label, maxlength: String(max),
                         rows: tag === "textarea" ? "2" : null });
    input.value = value;
    return input;
  }

  /* The edit form: the same fields the author filled in, the same number of
     options. Saving sends it; the next snapshot shows the result. */
  function reviewForm(q) {
    var prompt = field("textarea", q.prompt, "Question", 140);
    var answer = field("input", q.answer, "Real answer", 60);
    var decoys = q.decoys.map(function (d, i) { return field("input", d, "Fake answer " + (i + 1), 60); });
    var why = field("textarea", q.explanation, "Why it's true", 280);
    function close() { reviewEditing = null; paintReview(live); }
    return h("div", { class: "bs-review__item bs-review__form" },
      h("label", { class: "bs-label", text: "Question" }), prompt,
      h("label", { class: "bs-label", text: "Real answer" }), answer,
      h("label", { class: "bs-label", text: "Fake answers" }), decoys,
      h("label", { class: "bs-label", text: "Why it's true (optional)" }), why,
      h("div", { class: "bs-review__foot" },
        h("button", { class: "bs-btn bs-btn--inline", type: "button", text: "Cancel", onclick: close }),
        h("button", { class: "bs-btn bs-btn--inline bs-btn--go", type: "button", text: "Save",
                      onclick: function () {
                        step("edit", { question: q.id, prompt: prompt.value, answer: answer.value,
                                       decoys: decoys.map(function (d) { return d.value; }),
                                       explanation: why.value });
                        close();
                      } })));
  }

  function paintReview(s) {
    review.panel.hidden = s.phase !== "lobby";
    if (s.phase !== "lobby") { reviewEditing = null; return; }
    var bank = s.review || [];
    if (reviewEditing && bank.some(function (q) { return q.id === reviewEditing; }) &&
        review.list.querySelector(".bs-review__form")) {
      // Somebody joined or wrote; don't rebuild the form under the cursor.
      return;
    }
    var counts = bankCounts(s);
    review.count.textContent = counts.untagged
      ? counts.untagged + " of " + counts.total + " to review"
      : counts.total ? "all " + counts.total + " reviewed" : "nothing written yet";
    review.list.innerHTML = "";
    var shown = bank.filter(function (q) { return !onlyToReview || !q.tag || q.id === reviewEditing; });
    shown.forEach(function (q) {
      review.list.appendChild(q.id === reviewEditing ? reviewForm(q) : reviewItem(q));
    });
    if (!shown.length) {
      review.list.appendChild(h("p", { class: "bs-invites__hint",
        text: bank.length ? "Nothing left to review." : "Questions appear here as players write them." }));
    }
  }

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
      paintReview(state);
    },
    onError: toast,
    onClosed: function (code) {
      toast(code === 4001 ? "The host screen opened in another tab." : "Sign in with the host key again.");
    }
  });
})();
