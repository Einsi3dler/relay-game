/* ROLL CALL — shared room model for the host screen and the player screen.
 *
 * WHAT THIS IS, AND WHAT IT IS NOT
 *
 * This is a *prototype transport*. The room lives in localStorage and the two
 * pages sync through a BroadcastChannel, so you can open the host on one tab
 * and a player on another and actually play the thing. Nothing here reaches a
 * server and nothing here survives a different browser.
 *
 * It is written against the shape the real room will have (see the room object
 * below) precisely so the swap is a transport swap and not a rewrite: when the
 * backend room lands, `RelayQuizRoom.apply` becomes "send this to the server"
 * and `subscribe` becomes "the server pushed a new state". The views below it
 * never learn the difference.
 *
 * Every tab writes the room object directly, last write wins. That is wrong for
 * a network and completely fine for a human-paced quiz on one machine, and it
 * keeps the prototype small enough to throw away without regret.
 *
 * No build step (see CLAUDE.md), so this is a plain script hanging one object
 * off `window`, loaded after avatar.js.
 */
(function (global) {
  "use strict";

  var STORE_KEY = "relay.quiz.room";
  var CHANNEL = "relay-quiz-room";

  /* ------------------------------------------------------------- content -- */

  /* Placeholder bank. The real one comes from the roster data the host
     uploads, where each line already knows which person it is about; here a
     subject is drawn from whoever is in the room so the screens can be seen
     before any of that exists. */
  var PROMPTS = [
    "Who has jumped out of a perfectly good aeroplane?",
    "Who once worked a night shift in a bakery?",
    "Who can name every capital city in South America?",
    "Who has been on television?",
    "Who broke a bone doing something they refuse to explain?",
    "Who speaks three languages?",
    "Who has never once eaten a banana?",
    "Who got lost in a foreign city for a whole night?",
    "Who taught themselves an instrument during lockdown?",
    "Who has a tattoo nobody here has seen?",
    "Who was in a band that played exactly one gig?",
    "Who has met someone genuinely famous and played it cool?"
  ];

  /* Six stand-ins so the host screen can be reviewed without six phones. */
  var DEMO_NAMES = ["Amara", "Daniel", "Priya", "Tom", "Sade", "Luis"];

  /* ---------------------------------------------------------------- util -- */

  function nowStamp() { return Date.now(); }

  function makeId() {
    return Math.random().toString(36).slice(2, 10);
  }

  /* Ambiguous glyphs are out: a code is read off a projector and typed on a
     phone, and 0/O and 1/I are the two that cost a room its first minute. */
  var CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";

  function makeCode() {
    var out = "";
    for (var i = 0; i < 4; i++) {
      out += CODE_ALPHABET.charAt(Math.floor(Math.random() * CODE_ALPHABET.length));
    }
    return out;
  }

  function shuffle(list) {
    var out = list.slice();
    for (var i = out.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var swap = out[i];
      out[i] = out[j];
      out[j] = swap;
    }
    return out;
  }

  /* ---------------------------------------------------------------- faces -- */

  /* One drawing path for both pages. A stored code is trusted no further than
     avatar.js trusts it: an unreadable one falls back to a face seeded off the
     player id, so a seat is never blank. */
  function faceSvg(player) {
    var parts = player && player.avatar ? global.RelayAvatar.decode(player.avatar) : null;
    if (!parts) {
      parts = global.RelayAvatar.seeded(global.RelayAvatar.hashSeed(String(player && player.id || "?")));
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

  /* ----------------------------------------------------------------- room -- */

  function blankRoom() {
    return {
      code: makeCode(),
      phase: "lobby",          // lobby | question | reveal | final | closed
      players: [],             // { id, name, avatar, score, answer, gain }
      questions: [],           // { id, prompt, subject }  subject = player id
      index: -1,               // which question is live
      updated: nowStamp()
    };
  }

  function read() {
    try {
      var raw = global.localStorage.getItem(STORE_KEY);
      if (!raw) return null;
      var room = JSON.parse(raw);
      return room && room.code ? room : null;
    } catch (err) {
      return null;
    }
  }

  function write(room) {
    room.updated = nowStamp();
    try {
      global.localStorage.setItem(STORE_KEY, JSON.stringify(room));
    } catch (err) {
      /* Private windows and blocked site data both land here. The page still
         renders from whatever is in memory; it just will not reach the other
         tab, which is a prototype limitation and not a crash. */
    }
    try {
      if (channel) channel.postMessage({ at: room.updated });
    } catch (err) { /* channel closed */ }
    return room;
  }

  var channel = null;
  try {
    channel = new global.BroadcastChannel(CHANNEL);
  } catch (err) {
    channel = null;  // storage events alone, then
  }

  /* -------------------------------------------------------------- queries -- */

  function currentQuestion(room) {
    if (!room || room.index < 0 || room.index >= room.questions.length) return null;
    return room.questions[room.index];
  }

  function playerById(room, id) {
    if (!room || !id) return null;
    for (var i = 0; i < room.players.length; i++) {
      if (room.players[i].id === id) return room.players[i];
    }
    return null;
  }

  /* Whoever the question is about sits the round out, so "answered" is never
     waiting on a person who cannot answer. */
  function eligible(room) {
    var q = currentQuestion(room);
    return room.players.filter(function (p) { return !q || p.id !== q.subject; });
  }

  function answeredCount(room) {
    return eligible(room).filter(function (p) { return !!p.answer; }).length;
  }

  function allAnswered(room) {
    var pool = eligible(room);
    return pool.length > 0 && pool.every(function (p) { return !!p.answer; });
  }

  function standings(room) {
    return room.players.slice().sort(function (a, b) {
      if (b.score !== a.score) return b.score - a.score;
      return a.name.localeCompare(b.name);
    });
  }

  /* --------------------------------------------------------------- actions -- */
  /* Each of these is one host or player intent. In the wired version they
     become messages; the signatures are meant to survive that. */

  var actions = {
    reset: function () {
      return blankRoom();
    },

    join: function (room, fields) {
      if (room.phase === "closed") return room;
      var player = {
        id: fields.id || makeId(),
        name: fields.name,
        avatar: fields.avatar || null,
        score: 0,
        answer: null,
        gain: 0
      };
      room.players.push(player);
      return room;
    },

    /* The bank is shuffled once, at the start, and each question is pinned to
       a subject drawn from the room. Real data pins its own subjects; the
       shuffle is the part that stays. */
    start: function (room) {
      if (room.players.length < 2) return room;
      var subjects = shuffle(room.players.map(function (p) { return p.id; }));
      room.questions = shuffle(PROMPTS).map(function (prompt, i) {
        return { id: "q" + (i + 1), prompt: prompt, subject: subjects[i % subjects.length] };
      });
      room.index = 0;
      room.phase = "question";
      room.players.forEach(function (p) { p.score = 0; p.answer = null; p.gain = 0; });
      return room;
    },

    pick: function (room, fields) {
      if (room.phase !== "question") return room;
      var me = playerById(room, fields.playerId);
      var q = currentQuestion(room);
      if (!me || !q) return room;
      if (me.id === q.subject) return room;      // the subject sits it out
      me.answer = fields.choice;
      return room;
    },

    reveal: function (room) {
      if (room.phase !== "question") return room;
      var q = currentQuestion(room);
      room.players.forEach(function (p) {
        var right = !!q && p.id !== q.subject && p.answer === q.subject;
        p.gain = right ? 1 : 0;
        p.score += p.gain;
      });
      room.phase = "reveal";
      return room;
    },

    next: function (room) {
      if (room.phase !== "reveal") return room;
      room.players.forEach(function (p) { p.answer = null; p.gain = 0; });
      if (room.index + 1 >= room.questions.length) {
        room.phase = "final";
        return room;
      }
      room.index += 1;
      room.phase = "question";
      return room;
    },

    finish: function (room) {
      room.phase = "final";
      return room;
    },

    close: function (room) {
      room.phase = "closed";
      return room;
    },

    /* Prototype only: fill the room, and answer for everyone but the host's
       own seat, so one person can walk the whole flow. */
    seedPlayers: function (room) {
      DEMO_NAMES.forEach(function (name) {
        var taken = room.players.some(function (p) { return p.name === name; });
        if (taken) return;
        var id = makeId();
        room.players.push({
          id: id,
          name: name,
          avatar: global.RelayAvatar.encode(global.RelayAvatar.seeded(global.RelayAvatar.hashSeed(name))),
          score: 0,
          answer: null,
          gain: 0
        });
      });
      return room;
    },

    autoAnswer: function (room) {
      if (room.phase !== "question") return room;
      var q = currentQuestion(room);
      var ids = room.players.map(function (p) { return p.id; });
      room.players.forEach(function (p) {
        if (p.answer || (q && p.id === q.subject)) return;
        p.answer = ids[Math.floor(Math.random() * ids.length)];
      });
      return room;
    }
  };

  /* ---------------------------------------------------------------- store -- */

  var listeners = [];

  function notify() {
    var room = read();
    listeners.forEach(function (fn) { fn(room); });
  }

  global.addEventListener("storage", function (event) {
    if (event.key === STORE_KEY) notify();
  });
  if (channel) {
    channel.onmessage = function () { notify(); };
  }

  global.RelayQuizRoom = {
    PROMPTS: PROMPTS,
    DEMO_NAMES: DEMO_NAMES,

    read: read,
    makeId: makeId,

    /* The one write path. `apply("reveal")` today mutates localStorage; wired
       up, it posts `{action, fields}` and waits for the push instead. */
    apply: function (action, fields) {
      var room = read() || blankRoom();
      var fn = actions[action];
      if (!fn) return room;
      var next = fn(room, fields || {}) || room;
      write(next);
      notify();
      return next;
    },

    ensure: function () {
      var room = read();
      if (!room) { room = write(blankRoom()); }
      return room;
    },

    subscribe: function (fn) {
      listeners.push(fn);
      fn(read());
    },

    currentQuestion: currentQuestion,
    playerById: playerById,
    eligible: eligible,
    answeredCount: answeredCount,
    allAnswered: allAnswered,
    standings: standings,
    faceSvg: faceSvg,
    faceNode: faceNode
  };
})(window);
