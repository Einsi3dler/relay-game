/* BULLSHIT QUIZ — the player's phone, from personal link to final score.
 *
 * The page is a seat: `/bullshit/<token>`. Before the game it asks for a name
 * and the player's one question; during it, the server's snapshot says what
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

  var token = decodeURIComponent((location.pathname.split("/bullshit/")[1] || "").split("/")[0]);
  var storeKey = "bullshit.draft." + token.slice(0, 12);

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

  var el = {
    chip: document.getElementById("me-chip"),
    nameForm: document.getElementById("name-form"),
    nameInput: document.getElementById("name-input"),
    nameError: document.getElementById("name-error"),
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

  /* ------------------------------------------------------------- helpers -- */

  function show(name) {
    stage = name;
    ["loading", "nolink", "badlink", "name", "write", "done", "late", "game"].forEach(function (s) {
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
          var err = new Error(data.detail || "Something went wrong.");
          err.status = res.status;
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
      token: token, prompt: draft.question, answer: answer,
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

  /* ---------------------------------------------------------------- name -- */

  el.nameForm.addEventListener("submit", function (event) {
    event.preventDefault();
    el.nameError.textContent = "";
    api("POST", "/api/bullshit/register", { token: token, name: el.nameInput.value })
      .then(function (data) {
        me = data;
        setChip();
        startLive();
      })
      .catch(function (err) { el.nameError.textContent = err.message; });
  });

  /* ---------------------------------------------------------------- game -- */

  function send(payload) {
    if (socket) socket.send(payload);
  }

  function banner(kind, text) {
    return h("div", { class: "bs-role bs-role--" + kind }, h("span", { text: text }));
  }

  function lockedNote(text) {
    return h("p", { class: "bs-locked", text: text });
  }

  function briefing(s) {
    var nodes = [];
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
      nodes.push(BS.sub("The speakers are getting ready. Listen to them on the call, then pick who's telling the truth."));
      nodes.push(BS.questionCard(s), BS.optionTiles(s, {}));
    }
    nodes.push(BS.meter("Speakers ready", s.speakers_in, s.speakers));
    return nodes;
  }

  function discussion(s) {
    var nodes = [];
    if (s.role === "guesser") {
      if (s.committed) {
        nodes.push(BS.title("Locked in"));
        nodes.push(BS.questionCard(s), BS.optionTiles(s, { selected: s.your_pick }));
        nodes.push(lockedNote("You picked " + s.your_pick + " with a wager of " + s.your_wager + "."));
      } else {
        nodes.push(BS.title("Who's telling the truth?"));
        nodes.push(BS.questionCard(s));
        nodes.push(BS.optionTiles(s, { selected: pickKey, onPick: function (k) { pickKey = k; render(); } }));
        nodes.push(h("p", { class: "bs-hint", text: "Your wager. Right adds it, wrong takes it away. Each number works once." }));
        nodes.push(BS.wagerPicker(s.wager_max, s.used_wagers, pickWager, function (n) {
          pickWager = n; render();
        }));
        var ready = pickKey !== null && pickWager !== null;
        nodes.push(h("button", {
          class: "bs-btn bs-btn--go", type: "button", disabled: !ready,
          text: ready ? "Lock in " + pickKey + " for " + pickWager
            : pickKey === null ? "Pick an answer" : "Pick a wager",
          onclick: function () { send({ type: "bs_guess", option: pickKey, wager: pickWager }); }
        }));
      }
    } else {
      nodes.push(banner(s.role === "truthful" ? "truth" : "bull", "Defend it on the call"));
      nodes.push(BS.sub(s.role === "truthful"
        ? "Convince them option " + s.your_option + " is the truth. It is."
        : "Convince them option " + s.your_option + " is the truth. It isn't. Your wager: " + s.your_wager + "."));
      nodes.push(BS.questionCard(s), BS.optionTiles(s, { mine: s.your_option }));
    }
    nodes.push(BS.meter("Guesses in", s.guessers_in, s.guessers));
    return nodes;
  }

  function votes(s) {
    return [
      BS.title("The votes are in"),
      BS.sub("That's how the crowd split. The host reveals the truth next."),
      BS.questionCard(s),
      BS.optionTiles(s, { counts: s.counts, mine: s.your_option, selected: s.your_pick })
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
    if (line) {
      nodes.push(h("div", { class: "bs-card bs-card--line" + (line.delta >= 0 ? " is-up" : " is-down") },
        h("span", { class: "bs-label", text: "Your round" }),
        h("p", { class: "bs-line__delta", text: BS.signed(line.delta) }),
        h("p", { class: "bs-line__parts", text: BS.lineParts(line).join(", ") })));
    }
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
    }
    var nodes = s.phase === "briefing" ? briefing(s)
      : s.phase === "open" ? discussion(s)
      : s.phase === "votes" ? votes(s)
      : s.phase === "reveal" ? reveal(s)
      : final(s);
    el.game.innerHTML = "";
    nodes.forEach(function (n) { if (n) el.game.appendChild(n); });
  }

  /* -------------------------------------------------------------- render -- */

  function render() {
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

  function startLive() {
    render();
    socket = BS.connect("token=" + encodeURIComponent(token), {
      onState: function (state) {
        var phaseChanged = !live || live.phase !== state.phase || live.round !== state.round;
        if (live && live.phase === "lobby" && state.phase !== "lobby") {
          editing = false;
          me.locked = true;
        }
        live = state;
        render();
        if (phaseChanged && stage === "game") window.scrollTo(0, 0);
      },
      onError: toast,
      onClosed: function (code) {
        toast(code === 4001 ? "Your seat opened in another tab." : "That link does not work.");
      }
    });
  }

  /* ---------------------------------------------------------------- boot -- */

  if (!token) {
    show("nolink");
    return;
  }
  api("GET", "/api/bullshit/me?token=" + encodeURIComponent(token))
    .then(function (data) {
      me = data;
      if (!me.registered) {
        show(me.locked ? "late" : "name");
        return;
      }
      startLive();
    })
    .catch(function (err) {
      show(err.status === 404 ? "badlink" : "nolink");
      if (err.status !== 404) toast(err.message);
    });
})();
