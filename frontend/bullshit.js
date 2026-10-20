/* BULLSHIT QUIZ — the "write your question" screen, before the game starts.
 *
 * Screens only, like ROLL CALL was at first: nothing reaches a server yet.
 * Each invitee writes one question for tonight's bank, fills in the options
 * and marks the real one. How many options they get (2, 3 or 4) is the
 * server's call, not theirs, so this page only ever *receives* that number.
 *
 * Until the server exists, three stand-ins live here and nowhere else:
 *   - the option count comes from `?options=2|3|4`, or is picked once and
 *     remembered, so a reload does not reshuffle someone's form;
 *   - the name comes from `?name=`;
 *   - the "questions in" meter is made up (`?of=` sets the room size).
 * The draft is kept in localStorage so a reload never loses typing. When the
 * server lands, it owns all of this and the localStorage use goes away.
 *
 * No build step (see CLAUDE.md): a plain script, no framework.
 */
(function () {
  "use strict";

  var LETTERS = ["A", "B", "C", "D"];
  var QUESTION_MAX = 140;
  var OPTION_MAX = 60;
  var STORE_KEY = "bullshit.draft.v1";

  var TICK_SVG =
    '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">' +
    '<path d="M4 12.5l5 5L20 6.5" fill="none" stroke="currentColor" ' +
    'stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  var params = new URLSearchParams(window.location.search);

  /* ------------------------------------------------------------ storage -- */

  function load() {
    try {
      return JSON.parse(window.localStorage.getItem(STORE_KEY)) || null;
    } catch (e) {
      return null;
    }
  }

  function save(draft) {
    try {
      window.localStorage.setItem(STORE_KEY, JSON.stringify(draft));
    } catch (e) {
      /* Private window or blocked storage: the page still works, it just
         forgets on reload. */
    }
  }

  /* --------------------------------------------------------- stand-ins -- */

  function optionCount(saved) {
    var forced = parseInt(params.get("options"), 10);
    if (forced >= 2 && forced <= 4) return forced;
    if (saved && saved.count >= 2 && saved.count <= 4) return saved.count;
    return 2 + Math.floor(Math.random() * 3);
  }

  function blankDraft(count) {
    var options = [];
    for (var i = 0; i < count; i++) options.push("");
    return { count: count, question: "", options: options, real: null, submitted: false };
  }

  function draftFor(count) {
    var saved = load();
    if (!saved || saved.count !== count || !Array.isArray(saved.options)) {
      return blankDraft(count);
    }
    return saved;
  }

  /* -------------------------------------------------------------- state -- */

  var draft = draftFor(optionCount(load()));
  save(draft);

  var el = {
    chip: document.getElementById("me-chip"),
    sticker: document.getElementById("count-sticker"),
    question: document.getElementById("q-text"),
    qCount: document.getElementById("q-count"),
    options: document.getElementById("options"),
    submit: document.getElementById("submit"),
    missing: document.getElementById("missing"),
    write: document.getElementById("stage-write"),
    done: document.getElementById("stage-done"),
    previewQ: document.getElementById("preview-q"),
    previewOpts: document.getElementById("preview-opts"),
    meter: document.getElementById("meter"),
    meterFill: document.getElementById("meter-fill"),
    meterNum: document.getElementById("meter-num"),
    edit: document.getElementById("edit")
  };

  /* ------------------------------------------------------------ helpers -- */

  function clean(text) {
    return String(text || "").replace(/\s+/g, " ").trim();
  }

  /* What still stands between this draft and the bank, in the order a
     person would fix it. Empty means ready. */
  function whatsMissing() {
    if (!clean(draft.question)) return "Write your question first.";
    var empty = 0;
    for (var i = 0; i < draft.count; i++) {
      if (!clean(draft.options[i])) empty++;
    }
    if (empty === 1) return "One option is still empty.";
    if (empty > 1) return empty + " options are still empty.";
    var seen = {};
    for (var j = 0; j < draft.count; j++) {
      var key = clean(draft.options[j]).toLowerCase();
      if (seen[key]) return "Two options say the same thing.";
      seen[key] = true;
    }
    if (draft.real === null) return "Tap the tick on the real answer.";
    return "";
  }

  /* ------------------------------------------------------------- render -- */

  function buildOptions() {
    el.options.innerHTML = "";
    for (var i = 0; i < draft.count; i++) {
      var li = document.createElement("li");
      li.className = "bs-option";

      var letter = document.createElement("span");
      letter.className = "bs-option__letter";
      letter.textContent = LETTERS[i];

      // A textarea, not an input: a 60-character option has to wrap on a
      // phone rather than scroll out of sight.
      var input = document.createElement("textarea");
      input.className = "bs-option__input";
      input.rows = 1;
      input.maxLength = OPTION_MAX;
      input.placeholder = "Option " + LETTERS[i];
      input.value = draft.options[i] || "";
      input.setAttribute("aria-label", "Option " + LETTERS[i]);
      input.dataset.index = String(i);
      input.addEventListener("input", onOptionInput);
      input.addEventListener("keydown", noNewlines);

      var tick = document.createElement("button");
      tick.className = "bs-tick";
      tick.type = "button";
      tick.innerHTML = TICK_SVG;
      tick.dataset.index = String(i);
      tick.setAttribute("aria-label", "Option " + LETTERS[i] + " is the real answer");
      tick.addEventListener("click", onTick);

      li.appendChild(letter);
      li.appendChild(input);
      li.appendChild(tick);
      el.options.appendChild(li);
      fit(input);
    }
  }

  /* Grow a textarea to its content, so it reads like a field that wraps. */
  function fit(area) {
    area.style.height = "auto";
    area.style.height = area.scrollHeight + "px";
  }

  function paintWrite() {
    el.sticker.textContent = draft.count + " options";
    el.qCount.textContent = draft.question.length + " / " + QUESTION_MAX;

    var rows = el.options.children;
    for (var i = 0; i < rows.length; i++) {
      var isReal = draft.real === i;
      rows[i].classList.toggle("is-real", isReal);
      rows[i].querySelector(".bs-tick").setAttribute("aria-pressed", isReal ? "true" : "false");
    }

    var missing = whatsMissing();
    el.submit.disabled = missing !== "";
    el.missing.textContent = missing;
  }

  function paintDone() {
    el.previewQ.textContent = clean(draft.question);
    el.previewOpts.innerHTML = "";
    for (var i = 0; i < draft.count; i++) {
      var li = document.createElement("li");
      var b = document.createElement("b");
      b.textContent = LETTERS[i];
      var text = document.createElement("span");
      text.textContent = clean(draft.options[i]);
      li.appendChild(b);
      li.appendChild(text);
      if (draft.real === i) {
        li.className = "is-real";
        var tag = document.createElement("span");
        tag.className = "bs-preview__real";
        tag.textContent = "Real one";
        li.appendChild(tag);
      }
      el.previewOpts.appendChild(li);
    }

    var of = parseInt(params.get("of"), 10) || 12;
    var inBank = Math.min(of, Math.max(1, Math.round(of * 0.4)));
    el.meterNum.textContent = inBank + " of " + of;
    el.meter.setAttribute("aria-valuemax", String(of));
    el.meter.setAttribute("aria-valuenow", String(inBank));
    window.requestAnimationFrame(function () {
      el.meterFill.style.width = Math.round((inBank / of) * 100) + "%";
    });
  }

  function show(stage) {
    el.write.hidden = stage !== "write";
    el.done.hidden = stage !== "done";
    window.scrollTo(0, 0);
    if (stage === "done") paintDone();
    else paintWrite();
  }

  /* ------------------------------------------------------------- events -- */

  /* An option is one line of text; Enter means "next", not a line break. */
  function noNewlines(event) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    var next = el.options.querySelector(
      '.bs-option__input[data-index="' + (Number(event.target.dataset.index) + 1) + '"]');
    if (next) next.focus();
    else event.target.blur();
  }

  function onOptionInput(event) {
    // Pasted text can still carry line breaks past the keydown guard.
    event.target.value = event.target.value.replace(/[\r\n]+/g, " ");
    fit(event.target);
    draft.options[Number(event.target.dataset.index)] = event.target.value;
    save(draft);
    paintWrite();
  }

  function onTick(event) {
    var i = Number(event.currentTarget.dataset.index);
    draft.real = draft.real === i ? null : i;
    save(draft);
    paintWrite();
  }

  el.question.addEventListener("input", function () {
    draft.question = el.question.value;
    save(draft);
    paintWrite();
  });

  el.submit.addEventListener("click", function () {
    if (whatsMissing()) return;
    draft.submitted = true;
    save(draft);
    show("done");
  });

  el.edit.addEventListener("click", function () {
    draft.submitted = false;
    save(draft);
    show("write");
  });

  /* --------------------------------------------------------------- boot -- */

  var name = clean(params.get("name"));
  el.chip.textContent = name ? "Hi, " + name : "Hi there";
  el.question.maxLength = QUESTION_MAX;
  el.question.value = draft.question;
  buildOptions();
  show(draft.submitted ? "done" : "write");
})();
