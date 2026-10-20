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
        // 4404: unknown link. 4001: this seat opened somewhere newer.
        if (event.code === 4404 || event.code === 4001) {
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

  /* "Fooled 3 (+3), lost an 8 wager (-8) = -5", as parts. */
  function lineParts(line, n) {
    if (!line) return [];
    if (line.role === "truthful") {
      return [line.won ? "Your truth won the crowd" : "The crowd went elsewhere",
              "worth " + line.delta];
    }
    if (line.role === "bullshitter") {
      return ["Fooled " + line.fooled + " (" + signed(line.fooled) + ")",
              (line.won ? "won" : "lost") + " your " + line.wager + " wager (" +
              signed(line.won ? line.wager : -line.wager) + ")"];
    }
    return [(line.won ? "Right" : "Wrong") + " with a " + line.wager + " wager"];
  }

  global.BS = {
    h: h, nameOf: nameOf, signed: signed, connect: connect,
    optionTiles: optionTiles, wagerPicker: wagerPicker, meter: meter,
    leaderboard: leaderboard, title: title, sub: sub, questionCard: questionCard,
    verdict: verdict, lineParts: lineParts
  };
})(window);
