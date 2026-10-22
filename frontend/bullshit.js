/* BULLSHIT QUIZ — the player's phone, from personal link to final score.
 *
 * The seat lives on the player's site account. They sign up or sign in
 * like on any website, open Bullshit Quiz with the host's join code
 * (`/bullshit?code=K7QM` fills it in), and tap Join. From then on plain
 * `/bullshit` finds their seat from any device. Signed out, the page sends
 * them to sign in or make an account and brings them straight back here.
 * Before the game it asks for their one question; during it, the snapshot says what
 * this player is this round (truthful, bullshitter or guesser) and this page
 * draws the matching screen. It decides nothing: every commitment goes to the
 * server, which refuses anything the rules do not allow.
 *
 * The write form is built once when the stage opens, not on every snapshot,
 * so a broadcast caused by somebody else joining never steals focus mid-word.
 * A draft is kept in localStorage per link so a reload never loses typing.
 *
 * No build step (see CLAUDE.md). Needs bullshit-common.js loaded first.
 */
(function () {
  "use strict";

  var h = BS.h;
  var LETTERS = ["A", "B", "C", "D"];
  var QUESTION_MAX = 140;
  var OPTION_MAX = 60;

  var TICK_SVG =
    '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">' +
    '<path d="M4 12.5l5 5L20 6.5" fill="none" stroke="currentColor" ' +
    'stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  var params = new URLSearchParams(location.search);
  var codeFromLink = (params.get("code") || "").replace(/\s+/g, "").toUpperCase();
  var storeKey = "bullshit.draft";

  var me = null;          // from /api/bullshit/me
  var live = null;        // latest snapshot
  var socket = null;
  var stage = null;
  var editing = false;

  // Choices in progress this round, cleared when the round or phase moves.
  var roundKey = "";
  var pickKey = null;
  var pickWager = null;
  var bluffWager = null;

  // Your perk: the offer you're eyeing, whether its form is open, and what
  // is chosen in it so far. Cleared with the rest when the round moves.
  var offerPick = null;
  var perkOpen = false;
  var perkSel = { target: null, option: null, wager: null, copy: null, play: null };
  // The three Forbidden Words boxes are built once, so a redraw caused by
  // somebody else never steals the cursor mid-word (see render).
  var wordBoxes = null;
  var redrawLater = false;

  var el = {
    chip: document.getElementById("me-chip"),
    signinGo: document.getElementById("signin-go"),
    signupGo: document.getElementById("signup-go"),
    joinForm: document.getElementById("join-form"),
    joinCode: document.getElementById("join-code"),
    joinError: document.getElementById("join-error"),
    joinSub: document.getElementById("join-sub"),
    sticker: document.getElementById("count-sticker"),
    question: document.getElementById("q-text"),
    qCount: document.getElementById("q-count"),
    why: document.getElementById("q-why"),
    options: document.getElementById("options"),
    submit: document.getElementById("submit"),
    missing: document.getElementById("missing"),
    previewQ: document.getElementById("preview-q"),
    previewOpts: document.getElementById("preview-opts"),
    lobbyMeter: document.getElementById("lobby-meter"),
    edit: document.getElementById("edit"),
    game: document.getElementById("stage-game"),
    toast: document.getElementById("toast")
  };

  document.getElementById("mute-slot").replaceWith(BS.muteButton());

  /* ------------------------------------------------------------- helpers -- */

  function show(name) {
    stage = name;
    ["loading", "signin", "join", "write", "done", "late", "game"].forEach(function (s) {
      document.getElementById("stage-" + s).hidden = s !== name;
    });
  }

  var toastTimer = null;
  function toast(text) {
    el.toast.textContent = text;
    el.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.toast.hidden = true; }, 3200);
  }

  function clean(text) {
    return String(text || "").replace(/\s+/g, " ").trim();
  }

  function api(method, path, body) {
    return fetch(path, {
      method: method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) {
          // Refusals the page turns into a screen carry a reason; others
          // are a plain message.
          var detail = data.detail || "Something went wrong.";
          var err = new Error(typeof detail === "string" ? detail : detail.message);
          err.status = res.status;
          err.reason = typeof detail === "object" ? detail.reason : null;
          err.account = typeof detail === "object" ? detail.account : null;
          err.locked = typeof detail === "object" ? !!detail.locked : false;
          throw err;
        }
        return data;
      });
    });
  }

  function setChip() {
    el.chip.hidden = !(me && me.name);
    if (me && me.name) el.chip.textContent = "Hi, " + me.name;
  }

  /* --------------------------------------------------------------- draft -- */

  var draft = null;

  function loadDraft() {
    var count = me.option_count;
    var options = [];
    for (var i = 0; i < count; i++) options.push("");
    var fresh = { question: "", options: options, real: null, why: "" };

    if (me.question) {
      // Their saved question: the real answer first, ticked. Only they see it.
      return {
        question: me.question.prompt,
        options: [me.question.answer].concat(me.question.decoys).slice(0, count),
        real: 0,
        why: me.question.explanation || ""
      };
    }
    try {
      var saved = JSON.parse(localStorage.getItem(storeKey));
      if (saved && Array.isArray(saved.options) && saved.options.length === count) return saved;
    } catch (e) { /* storage blocked: start fresh */ }
    return fresh;
  }

  function saveDraft() {
    try { localStorage.setItem(storeKey, JSON.stringify(draft)); } catch (e) { /* fine */ }
  }

  function clearDraft() {
    try { localStorage.removeItem(storeKey); } catch (e) { /* fine */ }
  }

  function whatsMissing() {
    if (!clean(draft.question)) return "Write your question first.";
    var empty = draft.options.filter(function (o) { return !clean(o); }).length;
    if (empty === 1) return "One option is still empty.";
    if (empty > 1) return empty + " options are still empty.";
    var seen = {};
    for (var i = 0; i < draft.options.length; i++) {
      var key = clean(draft.options[i]).toLowerCase();
      if (seen[key]) return "Two options say the same thing.";
      seen[key] = true;
    }
    if (draft.real === null) return "Tap the tick on the real answer.";
    return "";
  }

  /* --------------------------------------------------------------- write -- */

  function fit(area) {
    area.style.height = "auto";
    area.style.height = area.scrollHeight + "px";
  }

  function buildWrite() {
    draft = loadDraft();
    el.sticker.textContent = me.option_count + " options";
    el.question.maxLength = QUESTION_MAX;
    el.question.value = draft.question;
    el.why.value = draft.why || "";
    el.options.innerHTML = "";

    draft.options.forEach(function (value, i) {
      var input = h("textarea", {
        class: "bs-option__input", rows: "1", maxlength: String(OPTION_MAX),
        placeholder: "Option " + LETTERS[i], "aria-label": "Option " + LETTERS[i],
        "data-index": String(i)
      });
      input.value = value;
      input.addEventListener("input", function () {
        input.value = input.value.replace(/[\r\n]+/g, " ");
        fit(input);
        draft.options[i] = input.value;
        saveDraft();
        paintWrite();
      });
      input.addEventListener("keydown", function (event) {
        if (event.key !== "Enter") return;
        event.preventDefault();
        var next = el.options.querySelector('[data-index="' + (i + 1) + '"]');
        if (next) next.focus(); else input.blur();
      });
      var tick = h("button", {
        class: "bs-tick", type: "button",
        "aria-label": "Option " + LETTERS[i] + " is the real answer",
        onclick: function () {
          draft.real = draft.real === i ? null : i;
          saveDraft();
          paintWrite();
        }
      });
      tick.innerHTML = TICK_SVG;
      el.options.appendChild(h("li", { class: "bs-option" },
        h("span", { class: "bs-option__letter", text: LETTERS[i] }), input, tick));
    });
    Array.prototype.forEach.call(el.options.querySelectorAll("textarea"), fit);
    paintWrite();
  }

  function paintWrite() {
    el.qCount.textContent = el.question.value.length + " / " + QUESTION_MAX;
    Array.prototype.forEach.call(el.options.children, function (row, i) {
      var isReal = draft.real === i;
      row.classList.toggle("is-real", isReal);
      row.querySelector(".bs-tick").setAttribute("aria-pressed", isReal ? "true" : "false");
    });
    var missing = whatsMissing();
    el.submit.disabled = missing !== "";
    el.missing.textContent = missing;
  }

  el.question.addEventListener("input", function () {
    draft.question = el.question.value;
    saveDraft();
    paintWrite();
  });

  el.why.addEventListener("input", function () {
    draft.why = el.why.value;
    saveDraft();
  });

  el.submit.addEventListener("click", function () {
    if (whatsMissing()) return;
    el.submit.disabled = true;
    var answer = draft.options[draft.real];
    var decoys = draft.options.filter(function (_, i) { return i !== draft.real; });
    api("POST", "/api/bullshit/question", {
      prompt: draft.question, answer: answer,
      decoys: decoys, explanation: draft.why || ""
    }).then(function (data) {
      me = data;
      editing = false;
      clearDraft();
      render();
    }).catch(function (err) {
      toast(err.message);
      paintWrite();
    });
  });

  el.edit.addEventListener("click", function () {
    editing = true;
    render();
  });

  /* ---------------------------------------------------------------- done -- */

  function paintDone() {
    var q = me.question;
    el.previewQ.textContent = q.prompt;
    el.previewOpts.innerHTML = "";
    [q.answer].concat(q.decoys).forEach(function (text, i) {
      el.previewOpts.appendChild(h("li", { class: i === 0 ? "is-real" : null },
        h("b", { text: LETTERS[i] }),
        h("span", { text: text }),
        i === 0 ? h("span", { class: "bs-preview__real", text: "Real one" }) : null));
    });
    el.lobbyMeter.innerHTML = "";
    if (live && live.phase === "lobby") {
      el.lobbyMeter.appendChild(BS.meter("Questions in", live.submitted, live.registered));
    }
  }

  /* ------------------------------------------------------- sign in + join -- */

  function backHere() {
    return "?next=" + encodeURIComponent(location.pathname + location.search);
  }

  function showSignin() {
    el.signinGo.href = "/login" + backHere();
    el.signupGo.href = "/signup" + backHere();
    show("signin");
  }

  function showJoin(note) {
    if (codeFromLink && !el.joinCode.value) el.joinCode.value = codeFromLink;
    if (note) el.joinSub.textContent = note;
    el.joinError.textContent = "";
    show("join");
    if (!el.joinCode.value) el.joinCode.focus();
  }

  el.joinForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var button = el.joinForm.querySelector("button");
    button.disabled = true;
    el.joinError.textContent = "";
    api("POST", "/api/bullshit/join", { code: el.joinCode.value })
      .then(function (data) {
        me = data;
        // The code has done its job. Plain /bullshit finds the seat from now on.
        history.replaceState(null, "", "/bullshit");
        // A fresh socket every time: a socket is tied to the seat it opened with.
        startLive();
      })
      .catch(function (err) {
        el.joinError.textContent = err.message;
      })
      .then(function () { button.disabled = false; });
  });

  /* ---------------------------------------------------------------- game -- */

  function send(payload) {
    if (socket) socket.send(payload);
  }

  function banner(kind, text) {
    return h("div", { class: "bs-role bs-role--" + kind }, h("span", { text: text }));
  }

  /* Why a player who dropped out earlier only ever guesses now. */
  function relegatedNote(s) {
    return s.relegated
      ? h("p", { class: "bs-note", text: "You dropped out earlier, so you guess for the rest of this game. Your score still counts." })
      : null;
  }

  function lockedNote(text) {
    return h("p", { class: "bs-locked", text: text });
  }

  function who(s, id) {
    return id === s.you ? "you" : BS.nameOf(s, id);
  }

  function myPlay(s, perk) {
    return (s.perks || []).filter(function (p) { return p.by === s.you && p.perk === perk; })[0];
  }

  /* ------------------------------------------------------------- perks -- */

  /* Perks that have landed on you, or are about to: what they mean for you.
     Once the truth is out, only what is still about to land matters. */
  function aimedAtYou(s) {
    var showing = s.phase === "reveal" ? ["pending"] : ["pending", "queued", "live"];
    return (s.perks || []).filter(function (p) {
      return p.target === s.you && p.by !== s.you &&
             showing.indexOf(p.status) !== -1 && p.ref === undefined;
    }).map(function (p) {
      var by = BS.nameOf(s, p.by);
      var text = {
        blackmail: by + " is blackmailing you: your wager is your highest number left" +
                   (s.forced_wager ? ", " + s.forced_wager + "." : "."),
        blood_pact: by + " has bound you in a Blood Pact. Both right: double your wager. Either wrong: you both lose it.",
        false_witness: by + " recruited you. While the clock runs, say a few words for option " + p.option +
                       ". Keep it secret until the reveal. You still vote however you like.",
        hot_seat: by + " is putting you in the Hot Seat: 20 seconds of their questions.",
        mute_button: by + " is muting you for 20 seconds.",
        forbidden_words: "Don't say " + (p.words || []).join(", ") + ". Every slip costs you 2 points.",
        the_audit: by + " is auditing you: your answer and wager go on every screen.",
        pickpocket: by + " is picking your pocket for 3 points.",
        hostile_takeover: by + " is taking over your score: you swap totals."
      }[p.perk];
      if (!text) return null;
      var dodge = s.perk && s.perk.id === "not_my_problem" && !s.perk.used && p.status !== "live"
        ? " Not My Problem can send it on, below." : "";
      return h("div", { class: "bs-alert" + (p.status === "live" ? "" : " is-coming") },
        h("span", { class: "bs-alert__name", text: p.name }),
        h("p", { class: "bs-alert__text", text: text + dodge }),
        p.status === "pending"
          ? h("p", { class: "bs-alert__when" }, "Lands in ", BS.clock(p.lands_in))
          : null);
    });
  }

  /* Your hand: the three offers until you keep one, then your perk, and a
     form to play it when the rules allow. */
  function perkDock(s) {
    var perk = s.perk;
    if (!perk) return null;
    if (perk.offers) return offersDock(perk.offers);
    var nodes = [
      h("div", { class: "bs-dock__head" },
        h("span", { class: "bs-label", text: "Your perk" }),
        h("span", { class: "bs-dock__state" + (perk.ready && !perk.used ? " is-ready" : ""),
                    text: perk.used ? "Used" : perk.ready ? "Ready" : "Not now" })),
      BS.perkCard(perk, perk.used ? "is-used" : null)
    ];
    if (perk.used) {
      nodes.push(h("p", { class: "bs-dock__why", text: "Spent. Everyone gets one perk a game." }));
    } else if (perk.id === "counterfeit") {
      nodes = nodes.concat(copyForm(s, perk));
    } else if (perk.reactions) {
      nodes = nodes.concat(reactionForm(s, perk, null));
    } else {
      nodes = nodes.concat(playForm(s, perk, null));
    }
    return h("section", { class: "bs-card bs-dock" + (urgent(s) ? " is-urgent" : ""),
                          "aria-label": "Your perk" }, nodes);
  }

  /* A reaction is only any use inside someone else's window: pin it up top. */
  function urgent(s) {
    var perk = s.perk;
    if (!perk || perk.used || !perk.ready) return false;
    var copies = perk.copies || [];
    return !!((perk.reactions && perk.reactions.length) ||
      copies.some(function (c) { return c.reactions && c.reactions.length; }) ||
      (s.phase === "votes"));
  }

  function offersDock(offers) {
    var chosen = offers.filter(function (o) { return o.id === offerPick; })[0];
    return h("section", { class: "bs-card bs-dock bs-dock--offers", "aria-label": "Pick your perk" },
      h("span", { class: "bs-label", text: "Pick your perk" }),
      h("p", { class: "bs-dock__why", text: "Keep one for the whole game. Nobody else sees which. You can pick any time." }),
      h("div", { class: "bs-offers" + (chosen ? " has-pick" : "") }, offers.map(function (perk) {
        var picked = offerPick === perk.id;
        return h("button", {
          class: "bs-perk bs-offer bs-perk--" + BS.familyColour(perk.family) + (picked ? " is-picked" : ""),
          type: "button", "aria-pressed": picked ? "true" : "false",
          onclick: function () { offerPick = perk.id; render(); }
        },
          h("span", { class: "bs-perk__tag", text: perk.public ? "Public" : "Secret" }),
          h("span", { class: "bs-perk__name", text: perk.name }),
          h("span", { class: "bs-perk__text", text: perk.text }));
      })),
      h("button", { class: "bs-btn bs-btn--go", type: "button", disabled: !chosen,
                    text: chosen ? "Keep " + chosen.name : "Tap one to keep it",
                    onclick: function () { send({ type: "bs_pick", perk: offerPick }); } }));
  }

  function ask(text) {
    return h("p", { class: "bs-dock__ask", text: text });
  }

  function chips(s, ids, selected, onPick) {
    return h("div", { class: "bs-pickchips", role: "group" }, ids.map(function (id) {
      return h("button", { class: "bs-pickchip" + (selected === id ? " is-picked" : ""), type: "button",
                           "aria-pressed": selected === id ? "true" : "false",
                           text: BS.nameOf(s, id),
                           onclick: function () { onPick(id); } });
    }));
  }

  function letterChips(s, keys, selected, onPick) {
    var colours = { A: "yellow", B: "cyan", C: "pink", D: "orange" };
    return h("div", { class: "bs-pickchips", role: "group" }, keys.map(function (key) {
      var opt = s.question.options.filter(function (o) { return o.key === key; })[0];
      return h("button", { class: "bs-pickchip bs-pickchip--" + colours[key] + (selected === key ? " is-picked" : ""),
                           type: "button", "aria-pressed": selected === key ? "true" : "false",
                           text: key + (opt ? ": " + opt.text : ""),
                           onclick: function () { onPick(key); } });
    }));
  }

  function words() {
    if (!wordBoxes) {
      wordBoxes = [0, 1, 2].map(function (i) {
        var box = h("input", { class: "bs-field bs-word__box", type: "text", maxlength: "20",
                               autocomplete: "off", autocapitalize: "none", spellcheck: "false",
                               "aria-label": "Forbidden word " + (i + 1),
                               placeholder: ["because", "honestly", "remember"][i] });
        box.addEventListener("input", function () { paintPlayButton(); });
        box.addEventListener("blur", function () {
          // Late enough that a tap on the Play button lands before the redraw.
          setTimeout(function () { if (redrawLater && !typingWords()) render(); }, 250);
        });
        return box;
      });
    }
    return h("div", { class: "bs-words" }, wordBoxes);
  }

  function wordValues() {
    return wordBoxes ? wordBoxes.map(function (b) { return b.value.trim(); }) : [];
  }

  function typingWords() {
    return !!wordBoxes && wordBoxes.indexOf(document.activeElement) !== -1;
  }

  var playButton = null;
  var playReady = null;

  function paintPlayButton() {
    if (!playButton || !playReady) return;
    var ok = playReady();
    playButton.disabled = !ok;
  }

  /* The form for a perk that targets, names an option, takes a wager or
     takes words. `aff` is what the server says is possible right now. */
  function playForm(s, aff, copyId) {
    if (!aff.ready) return [h("p", { class: "bs-dock__why", text: aff.why || "Not right now." })];
    if (!perkOpen) {
      return [h("button", { class: "bs-btn bs-btn--go", type: "button",
                            text: "Use " + aff.name,
                            onclick: function () { perkOpen = true; render(); } })];
    }
    var needs = aff.needs;
    var wantsTarget = ["guesser", "speaker", "player", "words"].indexOf(needs) !== -1;
    var wantsOption = needs === "option" || needs === "answer";
    var nodes = [];
    if (wantsTarget) {
      nodes.push(ask({ guesser: "Which guesser?", speaker: "Which speaker?", words: "Which speaker?",
                       player: "Which player?" }[needs]));
      nodes.push(chips(s, aff.targets, perkSel.target, function (id) { perkSel.target = id; render(); }));
    }
    if (wantsOption) {
      nodes.push(ask(aff.id === "backup_plan" ? "Your backup answer" :
                     aff.id === "kingmaker" ? "Which tied answer wins the crowd?" : "Your answer"));
      nodes.push(letterChips(s, aff.options, perkSel.option, function (k) { perkSel.option = k; render(); }));
    }
    if (needs === "answer") {
      nodes.push(ask(s.forced_wager ? "Your wager is forced to " + s.forced_wager + "." : "Your wager"));
      if (!s.forced_wager) {
        nodes.push(BS.wagerPicker(s.wager_max, s.used_wagers, perkSel.wager,
                                  function (n) { perkSel.wager = n; render(); }));
      }
    }
    if (needs === "words") {
      nodes.push(ask("Three words they can't say"));
      nodes.push(words());
    }
    playReady = function () {
      return (!wantsTarget || !!perkSel.target) && (!wantsOption || !!perkSel.option) &&
        (needs !== "answer" || !!perkSel.wager || !!s.forced_wager) &&
        (needs !== "words" || wordValues().every(function (w) { return w; }));
    };
    playButton = h("button", {
      class: "bs-btn bs-btn--go", type: "button",
      text: "Play " + aff.name + (perkSel.target ? " on " + BS.nameOf(s, perkSel.target) : ""),
      onclick: function () {
        if (!playReady()) return;
        send({ type: "bs_perk", target: perkSel.target, option: perkSel.option,
               wager: s.forced_wager || perkSel.wager, words: needs === "words" ? wordValues() : null,
               copy: copyId });
      }
    });
    paintPlayButton();
    nodes.push(h("div", { class: "bs-dock__row" }, playButton,
      h("button", { class: "bs-btn bs-btn--ghost bs-btn--inline", type: "button", text: "Not yet",
                    onclick: function () { perkOpen = false; render(); } })));
    return nodes;
  }

  function reactionForm(s, aff, copyId) {
    if (!aff.ready) return [h("p", { class: "bs-dock__why", text: aff.why || "Not right now." })];
    return aff.reactions.map(function (r) {
      if (aff.id === "objection") {
        return h("button", { class: "bs-btn bs-btn--go", type: "button",
                             text: "Object to " + r.name + " from " + who(s, r.by),
                             onclick: function () { send({ type: "bs_perk", play: r.play, copy: copyId }); } });
      }
      var mine = perkSel.play === r.play ? perkSel.target : null;
      return h("div", { class: "bs-dock__react" },
        ask(r.name + " from " + BS.nameOf(s, r.by) + ". Send it to:"),
        chips(s, r.targets, mine, function (id) { perkSel.play = r.play; perkSel.target = id; render(); }),
        h("button", { class: "bs-btn bs-btn--go", type: "button", disabled: !mine,
                      text: mine ? "Send it to " + BS.nameOf(s, mine) : "Pick who gets it",
                      onclick: function () {
                        send({ type: "bs_perk", play: r.play, target: mine, copy: copyId });
                      } }));
    });
  }

  /* Counterfeit: choose which revealed perk to copy, then play it. */
  function copyForm(s, perk) {
    var copies = perk.copies || [];
    if (!copies.length) return [h("p", { class: "bs-dock__why", text: perk.why || "Nothing to copy yet." })];
    var nodes = [ask("Copy which perk?"),
      h("div", { class: "bs-pickchips", role: "group" }, copies.map(function (c) {
        return h("button", { class: "bs-pickchip" + (perkSel.copy === c.id ? " is-picked" : ""),
                             type: "button", text: c.name + (c.ready ? "" : " (not now)"),
                             onclick: function () { perkSel.copy = c.id; perkOpen = false; render(); } });
      }))];
    var chosen = copies.filter(function (c) { return c.id === perkSel.copy; })[0];
    if (chosen) {
      nodes.push(BS.perkCard(chosen, "is-copy"));
      nodes = nodes.concat(chosen.reactions ? reactionForm(s, chosen, chosen.id)
                                            : playForm(s, chosen, chosen.id));
    }
    return nodes;
  }

  /* ------------------------------------------------------------ phases -- */

  function briefing(s) {
    var nodes = aimedAtYou(s);
    if (s.role === "truthful") {
      nodes.push(banner("truth", "You're telling the truth"));
      nodes.push(BS.sub("Option " + s.your_option + " is your real answer. Get ready to defend it on the call. Nobody knows it's yours."));
      nodes.push(BS.questionCard(s), BS.optionTiles(s, { mine: s.your_option }));
      if (s.your_explanation) {
        nodes.push(h("div", { class: "bs-card bs-card--note" },
          h("span", { class: "bs-label", text: "Your reveal note" }),
          h("p", { text: s.your_explanation })));
      }
      nodes.push(s.committed
        ? lockedNote("Ready. Waiting for the other speakers.")
        : h("button", { class: "bs-btn bs-btn--go", type: "button", text: "I'm ready",
                        onclick: function () { send({ type: "bs_ready" }); } }));
    } else if (s.role === "bullshitter") {
      nodes.push(banner("bull", "You're bullshitting"));
      nodes.push(BS.sub("Option " + s.your_option + " is a fake. Sell it. Bet on winning the crowd: more votes than any other option."));
      nodes.push(BS.questionCard(s), BS.optionTiles(s, { mine: s.your_option }));
      if (s.committed) {
        nodes.push(lockedNote("Wager locked: " + s.your_wager + ". Now get your story straight."));
      } else {
        nodes.push(h("p", { class: "bs-hint", text: "Your wager. Each number works once per game." }));
        nodes.push(BS.wagerPicker(s.wager_max, s.used_wagers, bluffWager, function (n) {
          bluffWager = n; render();
        }));
        nodes.push(h("button", {
          class: "bs-btn bs-btn--go", type: "button", disabled: bluffWager === null,
          text: bluffWager === null ? "Pick a wager" : "Lock my " + bluffWager,
          onclick: function () { send({ type: "bs_bluff", wager: bluffWager }); }
        }));
      }
    } else {
      nodes.push(banner("guess", "You're guessing"));
      if (s.committed) {
        nodes.push(BS.sub("Blind Faith: you're locked in before anyone has spoken. Now see if you were right."));
        nodes.push(BS.questionCard(s), BS.optionTiles(s, { selected: s.your_pick }));
        nodes.push(lockedNote("You picked " + s.your_pick + " with a wager of " + s.your_wager + "."));
      } else {
        nodes.push(BS.sub("The speakers are getting ready. Listen to them on the call. Voting opens when the talking stops."));
        nodes.push(relegatedNote(s));
        nodes.push(BS.questionCard(s), BS.optionTiles(s, {}));
      }
    }
    nodes.push(perkDock(s));
    nodes.push(BS.perkFeed(s));
    nodes.push(BS.meter("Speakers ready", s.speakers_in, s.speakers));
    return nodes;
  }

  function ballot(s) {
    var nodes = [];
    var reading = s.read_the_room;
    if (reading && reading.waiting > 0) {
      nodes.push(BS.title("Reading the room"));
      nodes.push(BS.sub("You vote last. Waiting on " + reading.waiting + " more guesser" +
                        (reading.waiting === 1 ? "." : "s.")));
      nodes.push(BS.questionCard(s), BS.optionTiles(s, {}));
      return nodes;
    }
    var backup = myPlay(s, "backup_plan");
    nodes.push(BS.title("Who's telling the truth?"));
    nodes.push(relegatedNote(s));
    if (reading) nodes.push(BS.sub("Everyone else is in. Here's how they split. You still don't know which is true."));
    nodes.push(BS.questionCard(s));
    nodes.push(BS.optionTiles(s, { selected: pickKey, counts: reading ? reading.counts : null,
                                   onPick: function (k) { pickKey = k; render(); } }));
    if (backup) nodes.push(h("p", { class: "bs-note", text: "Your backup is " + backup.option + ". Pick a different main answer." }));
    if (s.forced_wager) {
      pickWager = s.forced_wager;
      nodes.push(h("p", { class: "bs-note", text: "Blackmailed: your wager is your highest number left, " + s.forced_wager + "." }));
    } else {
      nodes.push(h("p", { class: "bs-hint", text: "Your wager. Right adds it, wrong takes it away. Each number works once." }));
      nodes.push(BS.wagerPicker(s.wager_max, s.used_wagers, pickWager, function (n) {
        pickWager = n; render();
      }));
    }
    var ready = pickKey !== null && pickWager !== null;
    nodes.push(h("button", {
      class: "bs-btn bs-btn--go", type: "button", disabled: !ready,
      text: ready ? "Lock in " + pickKey + " for " + pickWager
        : pickKey === null ? "Pick an answer" : "Pick a wager",
      onclick: function () { send({ type: "bs_guess", option: pickKey, wager: pickWager }); }
    }));
    return nodes;
  }

  function discussion(s) {
    var nodes = [BS.floorCard(s), BS.talkCard(s)].concat(aimedAtYou(s));
    if (urgent(s)) nodes.push(perkDock(s));
    if (s.role === "guesser") {
      if (s.committed) {
        nodes.push(BS.title("Locked in"));
        nodes.push(BS.questionCard(s), BS.optionTiles(s, { selected: s.your_pick }));
        nodes.push(lockedNote("You picked " + s.your_pick + " with a wager of " + s.your_wager + "."));
      } else if (!s.talk || !s.talk.ballot) {
        nodes.push(BS.title("Listen first"));
        nodes.push(relegatedNote(s));
        nodes.push(BS.sub("Grill the speakers on the call. Voting opens when the talking stops."));
        nodes.push(BS.questionCard(s), BS.optionTiles(s, {}));
      } else {
        nodes = nodes.concat(ballot(s));
      }
    } else {
      nodes.push(banner(s.role === "truthful" ? "truth" : "bull", "Defend it on the call"));
      nodes.push(BS.sub(s.role === "truthful"
        ? "Convince them option " + s.your_option + " is the truth. It is."
        : "Convince them option " + s.your_option + " is the truth. It isn't. Your wager: " + s.your_wager + "."));
      nodes.push(BS.questionCard(s), BS.optionTiles(s, { mine: s.your_option }));
    }
    if (!urgent(s)) nodes.push(perkDock(s));
    nodes.push(BS.perkFeed(s));
    if (s.talk && s.talk.ballot) nodes.push(BS.meter("Guesses in", s.guessers_in, s.guessers));
    return nodes;
  }

  function votes(s) {
    var tied = (s.tied || []).length;
    return [
      BS.title("The votes are in"),
      BS.sub(tied ? "A tie at the top. Unless a Kingmaker settles it, nobody wins the crowd. The host reveals the truth next."
                  : "That's how the crowd split. The host reveals the truth next."),
      perkDock(s),
      BS.questionCard(s),
      BS.optionTiles(s, { counts: s.counts, mine: s.your_option, selected: s.your_pick }),
      BS.perkFeed(s)
    ];
  }

  function reveal(s) {
    var mine = (s.players || []).filter(function (p) { return p.id === s.you; })[0];
    var line = mine && mine.line;
    var nodes = [
      BS.title("The truth"),
      BS.verdict(s),
      BS.optionTiles(s, { truth: s.truth.key, counts: s.counts,
                          mine: s.your_option, selected: s.your_pick })
    ];
    nodes = nodes.concat(aimedAtYou(s));
    if (line) {
      nodes.push(h("div", { class: "bs-card bs-card--line" + (line.delta >= 0 ? " is-up" : " is-down") },
        h("span", { class: "bs-label", text: "Your round" }),
        h("p", { class: "bs-line__delta", text: BS.signed(line.delta) }),
        h("p", { class: "bs-line__parts", text: BS.lineParts(line).join(", ") })));
    }
    nodes.push(perkDock(s));
    nodes.push(BS.perkFeed(s, { title: "Every perk this round" }));
    nodes.push(BS.leaderboard(s, "Scores"));
    return nodes;
  }

  function final(s) {
    var names = (s.winners || []).map(function (id) { return BS.nameOf(s, id); });
    var won = (s.winners || []).indexOf(s.you) !== -1;
    return [
      h("div", { class: "bs-burst", "aria-hidden": "true" },
        h("span", { text: won ? "YOU WIN!" : "GAME OVER" })),
      BS.title(names.length > 1 ? "Joint winners" : "The winner", true),
      BS.sub(names.join(" and ") || "Nobody", true),
      BS.leaderboard(s, "Final scores")
    ];
  }

  function renderGame(s) {
    var key = s.round + ":" + s.phase;
    if (key !== roundKey) {
      roundKey = key;
      pickKey = pickWager = bluffWager = null;
      perkOpen = false;
      perkSel = { target: null, option: null, wager: null, copy: null, play: null };
      if (wordBoxes) wordBoxes.forEach(function (b) { b.value = ""; });
    }
    if (typingWords()) {
      // Somebody else's move would rebuild the form under the cursor.
      // Clocks keep ticking on their own; redraw once they leave the box.
      redrawLater = true;
      return;
    }
    redrawLater = false;
    var nodes = s.phase === "briefing" ? briefing(s)
      : s.phase === "open" ? discussion(s)
      : s.phase === "votes" ? votes(s)
      : s.phase === "reveal" ? reveal(s)
      : s.phase === "waiting" ? [
          BS.title("Hang tight"),
          BS.sub("Not enough players are online for the next round. The host will deal it once a few more are back."),
          perkDock(s),
          BS.leaderboard(s, "Scores")
        ]
      : final(s);
    el.game.innerHTML = "";
    nodes.forEach(function (n) { if (n) el.game.appendChild(n); });
  }

  /* -------------------------------------------------------------- render -- */

  function render() {
    if (me && live && live.phase === "lobby" && !live.you) {
      // The host took this seat out of the lobby. Joining again is allowed.
      me = null;
      showJoin("The host took you out of the lobby. Join again with the code if that was a mistake.");
      return;
    }
    if (!me) return;
    setChip();
    if (!live || live.phase === "lobby") {
      if (editing || !me.question) {
        if (stage !== "write") { show("write"); buildWrite(); }
      } else {
        show("done");
        paintDone();
      }
      return;
    }
    if (!live.you) { show("late"); return; }
    var wasGame = stage === "game";
    show("game");
    renderGame(live);
    if (!wasGame) window.scrollTo(0, 0);
  }

  /* Sound and buzz for the moments that matter to this player: their turn
     to act, their own lock-in, the reveal (and whether it went their way),
     and the end. Compared against the previous snapshot so a rebroadcast
     caused by somebody else never replays anything. */
  function cue(before, after) {
    if (!before || after.phase === "lobby") return;
    var moved = before.phase !== after.phase || before.round !== after.round;
    perkCue(before, after, moved);
    if (moved) {
      var mustAct = after.phase === "briefing" && (after.role === "truthful" || after.role === "bullshitter");
      if (mustAct && !after.committed) { BS.play("turn"); BS.buzz(); }
      if (after.phase === "reveal") {
        BS.play("stamp");
        var mine = (after.players || []).filter(function (p) { return p.id === after.you; })[0];
        if (mine && mine.line) {
          setTimeout(function () { BS.play(mine.line.delta >= 0 ? "right" : "wrong"); }, 350);
        }
      }
      if (after.phase === "final") BS.play("fanfare");
      return;
    }
    if (!before.committed && after.committed) BS.play("pop");
    var opened = after.talk && after.talk.ballot && !(before.talk && before.talk.ballot);
    if (opened && after.role === "guesser" && !after.committed) { BS.play("turn"); BS.buzz(); }
  }

  /* A perk announced: a pop for everyone, a buzz if it is aimed at you. */
  function perkCue(before, after, moved) {
    var had = {};
    if (!moved) (before.perks || []).forEach(function (p) { had[p.id] = p.status; });
    (after.perks || []).forEach(function (p) {
      if (moved && after.phase === "reveal") return;
      if (!(p.id in had) && p.by !== after.you) {
        if (p.target === after.you) { BS.play("turn"); BS.buzz([80, 40, 80, 40, 80]); }
        else BS.play("pop");
      } else if (had[p.id] !== "running" && p.status === "running") {
        BS.play("stamp");
      }
    });
  }

  function startLive() {
    render();
    socket = BS.connect("", {
      onState: function (state) {
        var phaseChanged = !live || live.phase !== state.phase || live.round !== state.round;
        if (live && live.phase === "lobby" && state.phase !== "lobby") {
          editing = false;
          me.locked = true;
        }
        cue(live, state);
        live = state;
        render();
        if (phaseChanged && stage === "game") window.scrollTo(0, 0);
      },
      onError: toast,
      onClosed: function (code) {
        if (code === 4403) {
          // Removed from the lobby, or a new game night began. Joining again
          // opens a fresh socket for the new seat.
          socket = null;
          me = null;
          live = null;
          showJoin("You're not in the lobby any more. If that was a mistake, join again with the code.");
          return;
        }
        toast(code === 4001 ? "Your seat opened in another tab." : "Sign in again to rejoin.");
      }
    });
  }

  /* ---------------------------------------------------------------- boot -- */

  api("GET", "/api/bullshit/me")
    .then(function (data) {
      me = data;
      if (codeFromLink) history.replaceState(null, "", "/bullshit");
      startLive();
    })
    .catch(function (err) {
      if (err.reason === "signed_out") { showSignin(); return; }
      if (err.reason === "not_joined") {
        if (err.locked) { show("late"); return; }
        showJoin();
        return;
      }
      showJoin();
      toast(err.message);
    });
})();
