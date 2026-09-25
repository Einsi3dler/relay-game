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
    "Who has met someone genuinely famous and played it cool?",
    "Who has read the same book more than five times?",
    "Who once got a standing ovation?",
    "Who can solve a Rubik's cube without looking it up?",
    "Who has slept through an entire flight, take-off included?",
    "Who used to have a paper round?",
    "Who has been stung by a jellyfish?",
    "Who can still recite something they learned aged seven?",
    "Who has cooked for more than twenty people at once?",
    "Who owns a musical instrument they cannot play?",
    "Who has driven across a border?",
    "Who once queued overnight for something?",
    "Who has a middle name they never use?",
    "Who has run a race longer than ten kilometres?",
    "Who has been quoted in a newspaper?",
    "Who learned to swim as an adult?",
    "Who has kept a plant alive for over five years?",
    "Who once won something in a raffle?",
    "Who has a scar with a good story behind it?"
  ];

  /* Scoring. A correct guess is worth BASE; being early is worth a little on
     top of it, decaying down the order of correct answers. The ceiling is
     half the base on purpose: knowing the room should beat having quick
     thumbs, and the bonus only ever breaks a tie between people who were both
     right. Rank, not wall-clock, because there is no clock to measure against
     -- a room with nothing at stake will happily take a minute over a
     question, and an absolute decay curve would zero everybody out. */
  var BASE_POINTS = 100;
  var SPEED_MAX = 50;
  var SPEED_DECAY = 0.8;

  function speedBonus(rank) {
    return Math.round(SPEED_MAX * Math.pow(SPEED_DECAY, rank));
  }

  /* What the subject of a question locks in instead of a guess.
     
     They have to lock something. The projector shows who the room is still
     waiting on, and if the one person who cannot answer were left out of that
     list, or simply never lit up, the room would read the answer straight off
     the missing name. So the subject confirms in the same beat as everybody
     else, their tile behaves identically, and the tally reaches everyone. It
     is a sentinel rather than an empty string because every "have they
     answered" test in here is a truthiness test. */
  var SAT_OUT = "__sat_out";

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

  function clearNode(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function awardCard(kind, label, player, value) {
    var li = document.createElement("li");
    li.className = "award award--" + kind;
    li.appendChild(faceNode(player));

    var text = document.createElement("div");

    var lab = document.createElement("span");
    lab.className = "award__label";
    lab.textContent = label;
    text.appendChild(lab);

    var name = document.createElement("span");
    name.className = "award__name";
    name.textContent = player.name;
    text.appendChild(name);

    var val = document.createElement("span");
    val.className = "award__value";
    val.textContent = value;
    text.appendChild(val);

    li.appendChild(text);
    return li;
  }

  function renderAwards(node, room) {
    var prizes = awards(room);
    clearNode(node);
    if (prizes.winner) {
      node.appendChild(awardCard("score", "Highest score", prizes.winner,
        prizes.winner.score + " points from " + prizes.winner.correct +
        (prizes.winner.correct === 1 ? " right answer" : " right answers")));
    }
    if (prizes.fastest) {
      node.appendChild(awardCard("fast", "Fastest finger", prizes.fastest,
        (prizes.fastestMean / 1000).toFixed(1) + "s average on the ones they got right"));
    }
  }

  /* ------------------------------------------------- the reveal, staged -- */

  /* Who was right and who was not, for the two lists under the answer. The
     subject is in neither: they are the answer, shown above it.
     
     The right-hand list is ordered fastest first, which is the order the speed
     bonus was handed out in, so the list and the numbers beside it tell the
     same story. */
  function splitRound(room) {
    var q = currentQuestion(room);
    var right = [];
    var wrong = [];
    scorable(room).forEach(function (p) {
      if (q && p.answer === q.subject) right.push(p);
      else wrong.push(p);
    });
    right.sort(function (a, b) { return (a.answeredAt || 0) - (b.answeredAt || 0); });
    wrong.sort(function (a, b) { return a.name.localeCompare(b.name); });
    return { right: right, wrong: wrong };
  }

  function personRow(player, trailing, index, viewerId) {
    var li = document.createElement("li");
    li.className = "gperson";
    li.style.setProperty("--i", index);
    li.appendChild(faceNode(player));

    var name = document.createElement("span");
    name.className = "gperson__name";
    name.textContent = player.id === viewerId
      ? player.name + " (you)"
      : player.name;
    li.appendChild(name);

    if (trailing) li.appendChild(trailing);
    return li;
  }

  /* Fills the got-it / missed-it lists. `viewerId` is only used to say "you",
     so the same code serves the projector (no viewer) and a phone. */
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

  /* The slot machine. Rolls through the room, lands on the subject, calls
     back.
     
     Decoration only: `land` writes the correct face and name whether or not a
     single frame ran, and the caller gets the handle so it can cut the roll
     short if the host moves on mid-spin. Reduced motion skips straight to the
     landing. */
  var ROLL_TICK_MS = 80;
  var ROLL_TICKS = 14;

  function rollReveal(faceBox, nameEl, room, subject, done) {
    function land() {
      faceBox.classList.remove("is-rolling");
      faceBox.innerHTML = faceSvg(subject);
      nameEl.textContent = subject.name;
      void faceBox.offsetWidth;          // restart the keyframes
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
    nameEl.textContent = "\u00b7\u00b7\u00b7";

    var pool = room.players;
    var at = Math.floor(Math.random() * pool.length);
    var ticks = 0;
    var handle = global.setInterval(function () {
      at = (at + 1) % pool.length;
      faceBox.innerHTML = faceSvg(pool[at]);
      if (++ticks >= ROLL_TICKS) {
        global.clearInterval(handle);
        land();
      }
    }, ROLL_TICK_MS);
    return handle;
  }

  /* ----------------------------------------------------------------- room -- */

  function blankRoom() {
    return {
      code: makeCode(),
      phase: "lobby",          // lobby | question | reveal | final | closed
      players: [],             // { id, name, avatar, score, answer, gain }
      questions: [],           // { id, prompt, subject }  subject = player id
      index: -1,               // which question is live
      askedAt: 0,              // when the live question went up, for the speed bonus
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

  /* Who can actually score this round: everyone but the subject. This is a
     scoring question only. Nothing on a screen may be drawn from it, because
     the difference between this list and the room is the answer. */
  function scorable(room) {
    var q = currentQuestion(room);
    return room.players.filter(function (p) { return !q || p.id !== q.subject; });
  }

  /* What the projector counts, and it is everybody. The subject locks a
     SAT_OUT like everyone else locks a guess. */
  function answeredCount(room) {
    return room.players.filter(function (p) { return !!p.answer; }).length;
  }

  function allAnswered(room) {
    return room.players.length > 0 && room.players.every(function (p) {
      return !!p.answer;
    });
  }

  /* Mean ms to lock, counting only the rounds a player got right. Counting
     wrong answers too would hand "fastest finger" to whoever tapped a face at
     random the instant the prompt appeared, which is the opposite of the
     thing the award is for. A seat with nothing correct has no time. */
  function meanLock(player) {
    if (!player || !player.fastCount) return null;
    return player.fastSum / player.fastCount;
  }

  function standings(room) {
    return room.players.slice().sort(function (a, b) {
      if (b.score !== a.score) return b.score - a.score;
      if (b.correct !== a.correct) return b.correct - a.correct;
      var fa = meanLock(a), fb = meanLock(b);
      if (fa !== null && fb !== null && fa !== fb) return fa - fb;
      if (fa === null) return 1;
      if (fb === null) return -1;
      return a.name.localeCompare(b.name);
    });
  }

  /* The two things called out at the end. They are separate on purpose: the
     winner is whoever knew the room best, and fastest finger is a second way
     to leave with something, which usually goes to a different person. */
  function awards(room) {
    var ranked = standings(room);
    var winner = ranked.length && ranked[0].score > 0 ? ranked[0] : null;

    var fastest = null;
    room.players.forEach(function (p) {
      var mean = meanLock(p);
      if (mean === null) return;
      if (!fastest || mean < meanLock(fastest)) fastest = p;
    });

    return {
      winner: winner,
      fastest: fastest,
      fastestMean: meanLock(fastest)
    };
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
        answeredAt: null,   // ms from askedAt to the lock, this round
        gain: 0,
        bonus: 0,
        correct: 0,         // rounds got right, over the whole quiz
        fastSum: 0,         // total ms to lock, counting correct rounds only
        fastCount: 0
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
      room.askedAt = nowStamp();
      room.players.forEach(function (p) {
        p.score = 0; p.answer = null; p.answeredAt = null;
        p.gain = 0; p.bonus = 0; p.correct = 0; p.fastSum = 0; p.fastCount = 0;
      });
      return room;
    },

    pick: function (room, fields) {
      if (room.phase !== "question") return room;
      var me = playerById(room, fields.playerId);
      var q = currentQuestion(room);
      if (!me || !q) return room;
      if (me.answer) return room;                // a lock is final
      /* The subject confirms rather than guesses, and a real guess from them
         is refused: the one thing they must not be able to do is score. */
      if (me.id === q.subject && fields.choice !== SAT_OUT) return room;
      if (me.id !== q.subject && fields.choice === SAT_OUT) return room;
      me.answer = fields.choice;
      me.answeredAt = Math.max(0, nowStamp() - (room.askedAt || nowStamp()));
      return room;
    },

    /* Everyone who got it right is ranked by how quickly they locked, and the
       bonus decays down that order. Ties in time are broken by name so the
       same room always scores the same way twice. */
    reveal: function (room) {
      if (room.phase !== "question") return room;
      var q = currentQuestion(room);

      room.players.forEach(function (p) { p.gain = 0; p.bonus = 0; });

      var right = room.players.filter(function (p) {
        return !!q && p.id !== q.subject && p.answer === q.subject;
      }).sort(function (a, b) {
        if (a.answeredAt !== b.answeredAt) return a.answeredAt - b.answeredAt;
        return a.name.localeCompare(b.name);
      });

      right.forEach(function (p, rank) {
        p.bonus = speedBonus(rank);
        p.gain = BASE_POINTS + p.bonus;
        p.score += p.gain;
        p.correct += 1;
        p.fastSum += p.answeredAt || 0;
        p.fastCount += 1;
      });

      room.phase = "reveal";
      return room;
    },

    next: function (room) {
      if (room.phase !== "reveal") return room;
      room.players.forEach(function (p) {
        p.answer = null; p.answeredAt = null; p.gain = 0; p.bonus = 0;
      });
      if (room.index + 1 >= room.questions.length) {
        room.phase = "final";
        return room;
      }
      room.index += 1;
      room.phase = "question";
      room.askedAt = nowStamp();
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
          answeredAt: null,
          gain: 0,
          bonus: 0,
          correct: 0,
          fastSum: 0,
          fastCount: 0
        });
      });
      return room;
    },

    autoAnswer: function (room) {
      if (room.phase !== "question") return room;
      var q = currentQuestion(room);
      var ids = room.players.map(function (p) { return p.id; });
      room.players.forEach(function (p) {
        if (p.answer) return;
        if (q && p.id === q.subject) {
          p.answer = SAT_OUT;
        } else {
          /* Never themselves: the real grid does not offer you your own card,
             so a stand-in that picks itself produces a "said Amara" against
             Amara's own name and makes the reveal look broken. */
          var pick = ids.filter(function (id) { return id !== p.id; });
          p.answer = pick[Math.floor(Math.random() * pick.length)];
        }
        p.answeredAt = 700 + Math.floor(Math.random() * 11000);
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

    SAT_OUT: SAT_OUT,
    currentQuestion: currentQuestion,
    playerById: playerById,
    scorable: scorable,
    answeredCount: answeredCount,
    allAnswered: allAnswered,
    standings: standings,
    awards: awards,
    meanLock: meanLock,
    BASE_POINTS: BASE_POINTS,
    faceSvg: faceSvg,
    faceNode: faceNode,
    renderAwards: renderAwards,
    splitRound: splitRound,
    renderGroups: renderGroups,
    rollReveal: rollReveal
  };
})(window);
