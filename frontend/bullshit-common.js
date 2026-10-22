/* BULLSHIT QUIZ — the socket, and the pieces both screens draw.
 *
 * Holds no game state and decides nothing. The server (backend/bsroom.py)
 * sends each socket its own snapshot; this keeps the latest one and hands it
 * to whichever page is mounted. If a snapshot ever carries the truth before
 * the reveal, the bug is on the server, and hiding it here would not fix it.
 *
 * Every string from a player goes in through textContent, never innerHTML.
 *
 * No build step (see CLAUDE.md): a plain script hanging `BS` off `window`.
 */
(function (global) {
  "use strict";

  var COLORS = { A: "yellow", B: "cyan", C: "pink", D: "orange" };

  /* Build an element. `attrs` keys starting with "on" become listeners;
     `text` sets textContent; everything else is an attribute. */
  function h(tag, attrs) {
    var node = document.createElement(tag);
    var a = attrs || {};
    Object.keys(a).forEach(function (key) {
      var value = a[key];
      if (value === null || value === undefined || value === false) return;
      if (key === "text") node.textContent = value;
      else if (key === "class") node.className = value;
      else if (key.slice(0, 2) === "on") node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    });
    for (var i = 2; i < arguments.length; i++) {
      var child = arguments[i];
      if (child === null || child === undefined || child === false) continue;
      if (Array.isArray(child)) child.forEach(function (c) { if (c) node.appendChild(c); });
      else node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return node;
  }

  function nameOf(state, id) {
    var p = (state.players || []).filter(function (x) { return x.id === id; })[0];
    return p ? p.name : "Someone";
  }

  function signed(n) {
    return (n > 0 ? "+" : n < 0 ? "−" : "") + Math.abs(n);
  }

  /* --------------------------------------------------------------- socket -- */

  function connect(query, handlers) {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    var url = proto + "//" + location.host + "/ws/bullshit" + (query ? "?" + query : "");
    var socket = null;
    var retry = 0;
    var stopped = false;

    function open() {
      socket = new WebSocket(url);
      socket.onopen = function () { retry = 0; if (handlers.onOpen) handlers.onOpen(); };
      socket.onmessage = function (event) {
        var msg;
        try { msg = JSON.parse(event.data); } catch (e) { return; }
        if (msg.type === "bs_state") handlers.onState(msg.state);
        else if (msg.type === "error" && handlers.onError) {
          handlers.onError(msg.error || "That did not work.");
        }
      };
      socket.onclose = function (event) {
        // 4404: no seat. 4001: this seat opened somewhere newer.
        // 4403: the host took this seat away (removed, or a new game night).
        if (event.code === 4404 || event.code === 4001 || event.code === 4403) {
          stopped = true;
          if (handlers.onClosed) handlers.onClosed(event.code);
          return;
        }
        if (stopped) return;
        if (handlers.onDrop) handlers.onDrop();
        retry = Math.min(retry + 1, 6);
        setTimeout(open, 400 * retry);
      };
    }

    open();
    return {
      send: function (payload) {
        if (socket && socket.readyState === 1) socket.send(JSON.stringify(payload));
      }
    };
  }

  /* --------------------------------------------------------------- pieces -- */

  /* The options as big colour tiles, each with who is defending it.
     opts.selected: key highlighted as chosen; opts.mine: key that is yours;
     opts.onPick: makes tiles tappable; opts.truth / opts.counts: reveal. */
  function optionTiles(state, opts) {
    var o = opts || {};
    var q = state.question;
    var list = h("ol", { class: "bs-options bs-options--game" });
    q.options.forEach(function (opt) {
      var classes = "bs-option bs-option--" + COLORS[opt.key];
      if (o.selected === opt.key) classes += " is-picked";
      if (o.mine === opt.key) classes += " is-mine";
      if (o.truth) classes += o.truth === opt.key ? " is-true" : " is-bull";
      var body = h("div", { class: "bs-option__body" },
        h("span", { class: "bs-option__text", text: opt.text }),
        h("span", { class: "bs-option__speaker",
                    text: o.mine === opt.key ? "You defend this"
                      : nameOf(state, opt.speaker) + " defends this" }));
      var stamp = o.truth
        ? h("span", { class: "bs-stamp", text: o.truth === opt.key ? "TRUE" : "BULLSHIT" })
        : null;
      var count = o.counts
        ? h("span", { class: "bs-option__count", text: String(o.counts[opt.key] || 0) })
        : null;
      var inner = [h("span", { class: "bs-option__letter", text: opt.key }), body, count, stamp];
      var item = o.onPick
        ? h("li", { class: classes },
            h("button", { class: "bs-option__hit", type: "button",
                          "aria-pressed": o.selected === opt.key ? "true" : "false",
                          onclick: function () { o.onPick(opt.key); } }, inner))
        : h("li", { class: classes }, inner);
      list.appendChild(item);
    });
    return list;
  }

  /* Chips 1..max. Spent numbers are crossed out and cannot be picked. */
  function wagerPicker(max, used, selected, onPick) {
    var spent = {};
    (used || []).forEach(function (n) { spent[n] = true; });
    var grid = h("div", { class: "bs-wagers", role: "group", "aria-label": "Your wager" });
    for (var n = 1; n <= max; n++) {
      (function (value) {
        grid.appendChild(h("button", {
          class: "bs-wager" + (spent[value] ? " is-spent" : "") +
                 (selected === value ? " is-picked" : ""),
          type: "button",
          disabled: spent[value] ? true : null,
          "aria-pressed": selected === value ? "true" : "false",
          "aria-label": spent[value] ? value + ", already used" : "Wager " + value,
          text: String(value),
          onclick: function () { onPick(value); }
        }));
      })(n);
    }
    return grid;
  }

  function meter(label, done, total) {
    var pct = total ? Math.round((done / total) * 100) : 0;
    return h("div", { class: "bs-card bs-card--meter" },
      h("div", { class: "bs-meter__row" },
        h("span", { class: "bs-label", text: label }),
        h("span", { class: "bs-meter__num", text: done + " of " + total })),
      h("div", { class: "bs-meter", role: "progressbar", "aria-label": label,
                 "aria-valuemin": "0", "aria-valuemax": String(total),
                 "aria-valuenow": String(done) },
        h("span", { class: "bs-meter__fill", style: "width:" + pct + "%" })));
  }

  function leaderboard(state, title) {
    var rows = (state.players || []).slice().sort(function (a, b) {
      return b.total - a.total || a.name.localeCompare(b.name);
    });
    return h("div", { class: "bs-card bs-board" },
      h("span", { class: "bs-label", text: title || "Scores" }),
      h("ol", { class: "bs-board__list" }, rows.map(function (p) {
        return h("li", { class: p.id === state.you ? "is-you" : null },
          h("span", { class: "bs-board__name", text: p.name + (p.id === state.you ? " (you)" : "") }),
          h("span", { class: "bs-board__total", text: String(p.total) }));
      })));
  }

  function title(text, center) {
    return h("h1", { class: "bs-title" + (center ? " bs-title--center" : ""), text: text });
  }

  function sub(text, center) {
    return h("p", { class: "bs-sub" + (center ? " bs-sub--center" : ""), text: text });
  }

  function questionCard(state) {
    return h("div", { class: "bs-card bs-card--prompt" },
      h("span", { class: "bs-label", text: "Round " + state.round + " of " + state.rounds }),
      h("p", { class: "bs-prompt", text: state.question.prompt }));
  }

  /* The reveal's one-line verdict: who wrote it, who won the crowd. */
  function verdict(state) {
    var t = state.truth;
    var won = t.winner === null ? "Nobody won the crowd: it was a tie at the top."
      : t.winner === t.key ? nameOf(state, t.author) + " won the crowd with the truth."
      : nameOf(state, speakerOf(state, t.winner)) + " won the crowd with pure bullshit.";
    if (t.winner !== null && (state.tied || []).length) won += " A Kingmaker settled the tie.";
    return h("div", { class: "bs-card bs-card--verdict" },
      h("p", { class: "bs-verdict__who",
               text: "The truth was " + t.key + ". " + nameOf(state, t.author) + " wrote this one." }),
      h("p", { class: "bs-verdict__crowd", text: won }),
      t.explanation ? h("p", { class: "bs-verdict__why", text: t.explanation }) : null);
  }

  function speakerOf(state, key) {
    var o = state.question.options.filter(function (x) { return x.key === key; })[0];
    return o ? o.speaker : null;
  }

  /* "Fooled 3 (+3), lost an 8 wager (-8), Silver Tongue (+3)", as parts.
     Each perk that touched the line is its own part, even one worth 0. */
  function lineParts(line) {
    if (!line) return [];
    var out;
    if (line.role === "truthful") {
      var reward = line.delta - perkTotal(line);
      out = [line.won ? "Your truth won the crowd" : "The crowd went elsewhere",
             "worth " + reward];
    } else if (line.role === "bullshitter") {
      out = ["Fooled " + line.fooled + " (" + signed(line.fooled) + ")",
             (line.won ? "won" : "lost") + " your " + line.wager + " wager (" +
             signed(line.won ? line.wager : -line.wager) + ")"];
    } else if (line.option === null) {
      out = ["No vote this round"];
    } else {
      out = [(line.won ? "Right" : "Wrong") + " with a " + line.wager + " wager"];
    }
    (line.parts || []).forEach(function (part) {
      out.push(part[0] + " (" + signed(part[1]) + ")");
    });
    return out;
  }

  function perkTotal(line) {
    return (line.parts || []).reduce(function (sum, part) { return sum + part[1]; }, 0);
  }

  /* -------------------------------------------------------------- clocks -- */

  /* Countdowns arrive as seconds left when the snapshot was made. Each turns
     into an end time on this device's clock, and one timer repaints them
     all, so the seconds tick down without the server sending anything. The
     server still decides when time is up. */
  function clock(seconds, extra) {
    var node = h("span", { class: "bs-clock" + (extra ? " " + extra : ""),
                           role: "timer" });
    node.setAttribute("data-ends", String(Date.now() + Math.max(0, seconds || 0) * 1000));
    paintClock(node);
    return node;
  }

  function paintClock(node) {
    var left = Math.max(0, Math.ceil((Number(node.getAttribute("data-ends")) - Date.now()) / 1000));
    node.textContent = Math.floor(left / 60) + ":" + ("0" + (left % 60)).slice(-2);
  }

  setInterval(function () {
    Array.prototype.forEach.call(document.querySelectorAll("[data-ends]"), paintClock);
  }, 250);

  /* The talking clock: openings, then the open floor, then any Last Words,
     then the ballot. */
  function talkCard(state) {
    var t = state.talk;
    if (!t) return null;
    if (t.ballot) {
      return h("div", { class: "bs-card bs-talk is-open" },
        h("span", { class: "bs-label", text: "Voting is open" }),
        h("p", { class: "bs-talk__note", text: "The talking is over. Guessers lock in an answer and a wager." }));
    }
    var stage = t.left <= 0 ? ["Last words", null, "A Last Word has the floor. Voting opens after it."]
      : t.openings_left > 0 ? ["Openings", t.openings_left, "Each speaker makes their case. Voting opens when the talking stops."]
      : ["Open floor", t.left, "Questions, challenges, accusations. Voting opens when the clock runs out."];
    return h("div", { class: "bs-card bs-talk" },
      h("div", { class: "bs-talk__row" },
        h("span", { class: "bs-label", text: stage[0] }),
        stage[1] === null ? null : clock(stage[1], "bs-clock--talk")),
      h("p", { class: "bs-talk__note", text: stage[2] }));
  }

  /* ---------------------------------------------------------------- perks -- */

  var FAMILY = { wager: "yellow", side: "cyan", info: "cyan", speaker: "orange",
                 floor: "orange", power: "pink", counter: "paper" };

  function familyColour(family) { return FAMILY[family] || "paper"; }

  /* A perk as a card: the family sticker, the name, the card text. */
  function perkCard(perk, extra) {
    return h("div", { class: "bs-perk bs-perk--" + familyColour(perk.family) + (extra ? " " + extra : "") },
      h("span", { class: "bs-perk__tag", text: perk.public ? "Public" : "Secret" }),
      h("p", { class: "bs-perk__name", text: perk.name }),
      h("p", { class: "bs-perk__text", text: perk.text }));
  }

  /* The floor perk running right now, big, with its clock. */
  function floorCard(state) {
    var p = (state.perks || []).filter(function (x) { return x.status === "running"; })[0];
    if (!p) return null;
    var who = function (id) { return id === state.you ? "you" : nameOf(state, id); };
    var line = p.perk === "hot_seat"
      ? cap(who(p.by)) + " " + (p.by === state.you ? "ask" : "asks") + " " + who(p.target) + ". Only " +
        (p.target === state.you ? "you answer" : "they answer") + ", and nobody jumps in."
      : p.perk === "mute_button"
        ? cap(who(p.target)) + " " + (p.target === state.you ? "are" : "is") + " muted. No talking until the clock runs out."
        : cap(who(p.by)) + " " + (p.by === state.you ? "have" : "has") + " the last word. Nobody else talks.";
    return h("div", { class: "bs-floor" },
      h("span", { class: "bs-floor__name", text: p.name }),
      h("p", { class: "bs-floor__line", text: line }),
      clock(p.ends_in, "bs-clock--big"));
  }

  function cap(text) { return text.charAt(0).toUpperCase() + text.slice(1); }

  /* One sentence for a play, from the viewer's side: "You" and "you" where
     it is theirs or aimed at them. */
  function describe(state, p) {
    var by = p.by === state.you ? "You" : nameOf(state, p.by);
    var on = !p.target ? "" : p.target === state.you ? "you" : nameOf(state, p.target);
    var opt = p.option ? "option " + p.option : "";
    var whose = !p.target ? "" : p.target === state.you ? "your" : nameOf(state, p.target) + "'s";
    switch (p.perk) {
      case "hot_seat": return by + " put" + (by === "You" ? "" : "s") + " " + on + " in the Hot Seat.";
      case "mute_button": return by + " mute" + (by === "You" ? "" : "s") + " " + on + ".";
      case "last_word": return by + " claim" + (by === "You" ? "" : "s") + " the Last Word.";
      case "forbidden_words": return by + " ban" + (by === "You" ? "" : "s") + " three words for " + on + ".";
      case "blackmail": return by + " blackmail" + (by === "You" ? "" : "s") + " " + on + " into betting " +
                                (p.target === state.you ? "your" : "their") + " highest number.";
      case "blood_pact": return by + " bind" + (by === "You" ? "" : "s") + " " + on + " in a Blood Pact.";
      case "the_audit": return by + " audit" + (by === "You" ? "" : "s") + " " + on + ".";
      case "pickpocket": return by + " pick" + (by === "You" ? "" : "s") + " " + whose + " pocket.";
      case "kingmaker": return by + " crown" + (by === "You" ? "" : "s") + " " + opt + " the winner of the tie.";
      case "hostile_takeover": return by + " went after " + whose + " score.";
      case "false_witness": return by + " recruited " + on + " to speak up for " + opt + ".";
      case "objection": return by + " objected!";
      case "not_my_problem": return by + " sent it on to " + on + ".";
      case "damage_control": return by + " clawed back part of a lost wager.";
      case "doppelganger": return by + " copied " + whose + " score.";
      case "backup_plan": return by + " kept " + opt + " as a backup.";
      case "blind_faith": return by + " locked " + opt + " before anyone spoke.";
      default:
        return by + (on ? " picked " + on : " played it") + (opt ? ", " + opt : "") + ".";
    }
  }

  function status(state, p) {
    if (p.status === "cancelled") return "Cancelled by Objection!";
    if (p.status === "pending") {
      return h("span", { class: "bs-feed__when" },
        p.perk === "hostile_takeover" ? "Swaps in " : "Lands in ", clock(p.lands_in));
    }
    if (p.status === "queued") {
      return p.perk === "last_word" ? "Runs when the talking stops." : "Waiting for the floor.";
    }
    if (p.perk === "hostile_takeover" && p.status === "done") {
      return p.swapped ? "Scores swapped." : "No swap: wrong answer.";
    }
    if (p.perk === "the_audit" && p.exposed) {
      return "Locked " + p.exposed.option + " with a " + p.exposed.wager + ".";
    }
    if (p.perk === "inside_information" && p.seen) return "They locked " + p.seen + ".";
    if (p.perk === "forbidden_words" && p.status === "live") {
      return "Slips so far: " + p.slips + ".";
    }
    if (p.status === "live" && (p.perk === "blackmail" || p.perk === "blood_pact")) {
      return p.perk === "blackmail" ? "In force." : "Bound until the reveal.";
    }
    return null;
  }

  /* This round's perks the viewer may see: announcements while it runs,
     everything at the reveal. `opts.onSlip(id, step)` adds the host's slip
     buttons to live Forbidden Words. */
  function perkFeed(state, opts) {
    var o = opts || {};
    var plays = (state.perks || []).filter(function (p) { return p.status !== "running"; });
    if (!plays.length) return null;
    return h("div", { class: "bs-card bs-feed" },
      h("span", { class: "bs-label", text: o.title || "Perks this round" }),
      h("ol", { class: "bs-feed__list" }, plays.map(function (p) {
        var said = status(state, p);
        var words = p.words
          ? h("p", { class: "bs-feed__words" }, p.words.map(function (w) {
              return h("span", { class: "bs-word", text: w });
            }))
          : null;
        var slips = o.onSlip && p.perk === "forbidden_words" && p.status === "live"
          ? h("div", { class: "bs-feed__tools" },
              h("button", { class: "bs-btn bs-btn--inline", type: "button", text: "Slip +1",
                            onclick: function () { o.onSlip(p.id, 1); } }),
              h("button", { class: "bs-btn bs-btn--inline bs-btn--quiet", type: "button", text: "Undo",
                            onclick: function () { o.onSlip(p.id, -1); } }))
          : null;
        return h("li", { class: "bs-feed__item bs-feed__item--" + familyColour(p.family) +
                           (p.target === state.you && state.you ? " is-you" : "") +
                           (p.status === "cancelled" ? " is-off" : "") },
          h("span", { class: "bs-feed__name", text: p.name + (p.counterfeit ? " (Counterfeit)" : "") }),
          h("p", { class: "bs-feed__what", text: describe(state, p) }),
          words,
          said ? h("p", { class: "bs-feed__status" }, said) : null,
          slips);
      })));
  }

  /* ------------------------------------------------------------- history -- */

  /* Every revealed round, newest first, folded so the list stays short. The
     host's screen only: the server sends `history` to the host alone
     (backend/bsroom.py), and only finished rounds. `open` unfolds the newest. */
  function history(state, opts) {
    var rounds = (state.history || []).slice().reverse();
    if (!rounds.length) return null;
    var o = opts || {};
    return h("div", { class: "bs-card bs-history" },
      h("span", { class: "bs-label", text: o.title || "Past rounds" }),
      rounds.map(function (r, i) {
        var mine = state.you && r.deltas ? r.deltas[state.you] : undefined;
        var head = h("summary", { class: "bs-history__head" },
          h("span", { class: "bs-history__round", text: "R" + r.round }),
          h("span", { class: "bs-history__prompt", text: r.prompt }),
          mine !== undefined
            ? h("span", { class: "bs-history__delta" + (mine >= 0 ? " is-up" : " is-down"),
                          text: signed(mine) })
            : null);
        var winner = r.winner === null ? "Tie at the top: nobody won the crowd."
          : r.winner === r.truth ? "The truth won the crowd."
          : nameOf(state, optionSpeaker(r, r.winner)) + " won the crowd with bullshit.";
        var body = h("div", { class: "bs-history__body" },
          h("ol", { class: "bs-history__opts" }, r.options.map(function (opt) {
            var isTrue = opt.key === r.truth;
            return h("li", { class: isTrue ? "is-true" : null },
              h("b", { text: opt.key }),
              h("span", { class: "bs-history__text", text: opt.text }),
              h("span", { class: "bs-history__who",
                          text: nameOf(state, opt.speaker) + (isTrue ? ", truth" : ", bullshit") }),
              h("span", { class: "bs-history__votes",
                          text: (r.counts[opt.key] || 0) + " vote" + (r.counts[opt.key] === 1 ? "" : "s") }));
          })),
          h("p", { class: "bs-history__note", text: nameOf(state, r.author) + " wrote it. " + winner }),
          r.explanation ? h("p", { class: "bs-history__why", text: r.explanation }) : null);
        return h("details", { class: "bs-history__item", open: o.open && i === 0 ? true : null },
          head, body);
      }));
  }

  function optionSpeaker(round, key) {
    var opt = round.options.filter(function (x) { return x.key === key; })[0];
    return opt ? opt.speaker : null;
  }

  /* --------------------------------------------------------- sound + buzz -- */

  /* Cartoon blips synthesised with Web Audio: no files to load, nothing to
     license. Browsers only allow sound after a tap, so the context is made
     lazily and resumed on the first interaction. Muting is remembered per
     browser, a convenience and nothing more, so storage failures are fine. */
  var MUTE_KEY = "bullshit.muted";
  var audio = null;
  var muted = false;
  try { muted = localStorage.getItem(MUTE_KEY) === "1"; } catch (e) { /* fine */ }

  function ctx() {
    if (!audio) {
      var Ctor = global.AudioContext || global.webkitAudioContext;
      if (!Ctor) return null;
      audio = new Ctor();
    }
    if (audio.state === "suspended") audio.resume();
    return audio;
  }

  document.addEventListener("pointerdown", function () { if (!muted) ctx(); }, { once: true });

  /* One note: a pitch slide with a quick decay. */
  function tone(freq, endFreq, start, length, type, volume) {
    var a = ctx();
    if (!a) return;
    var t = a.currentTime + start;
    var osc = a.createOscillator();
    var gain = a.createGain();
    osc.type = type || "square";
    osc.frequency.setValueAtTime(freq, t);
    osc.frequency.exponentialRampToValueAtTime(endFreq, t + length);
    gain.gain.setValueAtTime(volume || 0.08, t);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + length);
    osc.connect(gain).connect(a.destination);
    osc.start(t);
    osc.stop(t + length + 0.02);
  }

  var SOUNDS = {
    pop: function () { tone(520, 880, 0, 0.09, "square", 0.07); },
    turn: function () { tone(660, 660, 0, 0.08, "triangle", 0.1); tone(990, 990, 0.1, 0.12, "triangle", 0.1); },
    stamp: function () { tone(140, 60, 0, 0.18, "sawtooth", 0.12); },
    right: function () { tone(784, 784, 0, 0.1, "triangle", 0.1); tone(1175, 1175, 0.11, 0.2, "triangle", 0.1); },
    wrong: function () { tone(220, 110, 0, 0.35, "sawtooth", 0.09); },
    fanfare: function () {
      [523, 659, 784, 1047].forEach(function (f, i) { tone(f, f, i * 0.12, 0.16, "square", 0.07); });
      tone(1047, 1568, 0.5, 0.4, "triangle", 0.09);
    }
  };

  function play(name) {
    if (muted || !SOUNDS[name]) return;
    try { SOUNDS[name](); } catch (e) { /* audio is decoration */ }
  }

  function buzz(pattern) {
    if (muted || !navigator.vibrate) return;
    try { navigator.vibrate(pattern || [60, 40, 60]); } catch (e) { /* fine */ }
  }

  /* A small sticker button for the top bar. */
  function muteButton() {
    var button = h("button", { class: "bs-chip bs-mute", type: "button" });
    function paint() {
      button.textContent = muted ? "Sound off" : "Sound on";
      button.setAttribute("aria-pressed", muted ? "true" : "false");
    }
    button.addEventListener("click", function () {
      muted = !muted;
      try { localStorage.setItem(MUTE_KEY, muted ? "1" : "0"); } catch (e) { /* fine */ }
      paint();
      if (!muted) play("pop");
    });
    paint();
    return button;
  }

  global.BS = {
    h: h, nameOf: nameOf, signed: signed, connect: connect,
    optionTiles: optionTiles, wagerPicker: wagerPicker, meter: meter,
    leaderboard: leaderboard, title: title, sub: sub, questionCard: questionCard,
    verdict: verdict, lineParts: lineParts, history: history,
    play: play, buzz: buzz, muteButton: muteButton,
    clock: clock, talkCard: talkCard, floorCard: floorCard, perkCard: perkCard,
    perkFeed: perkFeed, describe: describe, familyColour: familyColour
  };
})(window);
