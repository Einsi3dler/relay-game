/* ROLL CALL — the socket, and the drawing both screens share.
 *
 * This replaces the prototype that kept the room in `localStorage`. The room
 * now lives on the server (backend/quizroom.py) and this file holds no game
 * state at all: it owns a socket, keeps the latest snapshot, and hands it to
 * whichever view is mounted. Nothing here decides anything.
 *
 * That is the point of the swap, not a tidiness exercise. The old client held
 * every question and every subject, so a player with devtools could read the
 * whole game before it started. A snapshot now carries one question, without
 * its answer, and the answer arrives on the reveal and not one message
 * earlier. If you find yourself adding `subject` to anything the server sends
 * during a question, stop.
 *
 * No build step (see CLAUDE.md), so this is a plain script hanging one object
 * off `window`, loaded after avatar.js.
 */
(function (global) {
  "use strict";

  var SAT_OUT = null;        // what the subject sends instead of a guess
  var BASE_POINTS = 100;     // for the "100 for the answer, 50 for early" line

  /* ---------------------------------------------------------------- faces -- */

  function faceSvg(player) {
    var parts = player && player.avatar
      ? global.RelayAvatar.decode(player.avatar) : null;
    if (!parts) {
      parts = global.RelayAvatar.seeded(
        global.RelayAvatar.hashSeed(String((player && player.id) || "?")));
    }
    return global.RelayAvatar.svg(parts);
  }

  function faceNode(player, extraClass) {
    var box = document.createElement("span");
    box.className = "face" + (extraClass ? " " + extraClass : "");
    box.innerHTML = faceSvg(player);
    box.setAttribute("role", "img");
    box.setAttribute("aria-label", (player && player.name) || "player");
    return box;
  }

  function clearNode(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  /* --------------------------------------------------------------- queries -- */

  function playerById(room, id) {
    if (!room || !id) return null;
    for (var i = 0; i < room.players.length; i++) {
      if (room.players[i].id === id) return room.players[i];
    }
    return null;
  }

  function present(room) {
    return room.players.filter(function (p) { return p.connected; });
  }

  function standings(room) {
    return room.players.slice().sort(function (a, b) {
      if (b.score !== a.score) return b.score - a.score;
      if (b.correct !== a.correct) return b.correct - a.correct;
      return a.name.localeCompare(b.name);
    });
  }

  /* Who was right and who was not. Only meaningful at the reveal, which is
     the first moment `room.subject` and the others' answers exist. */
  function splitRound(room) {
    var right = [], wrong = [];
    room.players.forEach(function (p) {
      if (p.id === room.subject) return;        // they are the answer
      (p.answer === room.subject ? right : wrong).push(p);
    });
    right.sort(function (a, b) { return (b.gain || 0) - (a.gain || 0); });
    wrong.sort(function (a, b) { return a.name.localeCompare(b.name); });
    return { right: right, wrong: wrong };
  }

  /* ---------------------------------------------------------------- groups -- */

  function personRow(player, trailing, index, viewerId) {
    var li = document.createElement("li");
    li.className = "gperson";
    li.style.setProperty("--i", index);
    li.appendChild(faceNode(player));

    var name = document.createElement("span");
    name.className = "gperson__name";
    name.textContent = player.id === viewerId ? player.name + " (you)" : player.name;
    li.appendChild(name);

    if (trailing) li.appendChild(trailing);
    return li;
  }

  function renderGroups(nodes, room, viewerId) {
    var split = splitRound(room);
    clearNode(nodes.rightList);
    clearNode(nodes.wrongList);
    nodes.rightCount.textContent = split.right.length;
    nodes.wrongCount.textContent = split.wrong.length;

    split.right.forEach(function (p, i) {
      var gain = document.createElement("span");
      gain.className = "gperson__gain";
      gain.textContent = "+" + p.gain;
      nodes.rightList.appendChild(personRow(p, gain, i, viewerId));
    });

    split.wrong.forEach(function (p, i) {
      var said = document.createElement("span");
      said.className = "gperson__said";
      var picked = p.answer ? playerById(room, p.answer) : null;
      said.textContent = picked ? "said " + picked.name : "no answer";
      nodes.wrongList.appendChild(personRow(p, said, i, viewerId));
    });
  }

  /* ---------------------------------------------------------------- awards -- */

  function awardCard(kind, label, person) {
    var li = document.createElement("li");
    li.className = "award award--" + kind;
    li.appendChild(faceNode(person));

    var text = document.createElement("div");
    var lab = document.createElement("span");
    lab.className = "award__label";
    lab.textContent = label;
    text.appendChild(lab);

    var name = document.createElement("span");
    name.className = "award__name";
    name.textContent = person.name;
    text.appendChild(name);

    var val = document.createElement("span");
    val.className = "award__value";
    val.textContent = person.detail || "";
    text.appendChild(val);

    li.appendChild(text);
    return li;
  }

  function renderAwards(node, room) {
    var prizes = (room && room.awards) || {};
    clearNode(node);
    if (prizes.winner) node.appendChild(awardCard("score", "Highest score", prizes.winner));
    if (prizes.fastest) node.appendChild(awardCard("fast", "Fastest finger", prizes.fastest));
  }

  /* ------------------------------------------------------ the reveal, rolled */

  var ROLL_TICK_MS = 80;
  var ROLL_TICKS = 14;

  function rollReveal(faceBox, nameEl, room, subject, done) {
    function land() {
      faceBox.classList.remove("is-rolling");
      faceBox.innerHTML = faceSvg(subject);
      nameEl.textContent = subject.name;
      void faceBox.offsetWidth;            // restart the keyframes
      faceBox.classList.add("is-landed");
      nameEl.classList.add("is-landed");
      if (done) done();
    }

    faceBox.classList.remove("is-landed", "is-rolling");
    nameEl.classList.remove("is-landed");

    var still = global.matchMedia &&
      global.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (still || room.players.length < 2) { land(); return null; }

    faceBox.classList.add("is-rolling");
    nameEl.textContent = "···";

    var pool = room.players;
    var at = Math.floor(Math.random() * pool.length);
    var ticks = 0;
    var handle = global.setInterval(function () {
      at = (at + 1) % pool.length;
      faceBox.innerHTML = faceSvg(pool[at]);
      if (++ticks >= ROLL_TICKS) { global.clearInterval(handle); land(); }
    }, ROLL_TICK_MS);
    return handle;
  }

  /* -------------------------------------------------------------- transport -- */

  var socket = null;
  var latest = null;
  var listeners = [];
  var connectArgs = null;
  var retry = null;

  function notify(room, status) {
    latest = room;
    listeners.forEach(function (fn) { fn(room, status || "open"); });
  }

  function url() {
    var scheme = global.location.protocol === "https:" ? "wss" : "ws";
    var query = connectArgs.token
      ? "?token=" + encodeURIComponent(connectArgs.token)
      : "";
    return scheme + "://" + global.location.host + "/ws/quiz" + query;
  }

  function open() {
    socket = new global.WebSocket(url());

    socket.onmessage = function (event) {
      var message;
      try { message = JSON.parse(event.data); } catch (err) { return; }
      if (message.type === "quiz_room_state") notify(message.room);
      else if (message.type === "error" && connectArgs.onError) {
        connectArgs.onError(message.message || "Rejected.");
      }
    };

    socket.onclose = function (event) {
      /* 4004 is the server saying it does not know this token, which a retry
         will not fix. Anything else is a dropped connection: a phone in a
         pocket, a laptop lid, the host's wifi. Those come back. */
      if (event.code === 4004) { notify(latest, "rejected"); return; }
      notify(latest, "closed");
      retry = global.setTimeout(open, 1500);
    };
  }

  global.RelayQuizRoom = {
    SAT_OUT: SAT_OUT,
    BASE_POINTS: BASE_POINTS,

    connect: function (args) {
      connectArgs = args || {};
      if (retry) { global.clearTimeout(retry); retry = null; }
      open();
    },

    subscribe: function (fn) {
      listeners.push(fn);
      if (latest) fn(latest, "open");
    },

    send: function (message) {
      if (socket && socket.readyState === 1) socket.send(JSON.stringify(message));
    },

    answer: function (choice) {
      this.send({ type: "quiz_answer", choice: choice });
    },

    host: function (action) {
      this.send({ type: "quiz_host", action: action });
    },

    playerById: playerById,
    present: present,
    standings: standings,
    splitRound: splitRound,
    faceSvg: faceSvg,
    faceNode: faceNode,
    renderGroups: renderGroups,
    renderAwards: renderAwards,
    rollReveal: rollReveal
  };
})(window);
