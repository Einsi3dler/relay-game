/* ROLL CALL — the host screen.
 *
 * This is the projector. One person drives it and the whole room reads it, so
 * two things hold everywhere below: it never shows anything a player should
 * not see yet (above all the subject of the live question, which is the
 * answer), and every number on it is big enough to read from the back.
 *
 * It owns no state. It renders whatever RelayQuizRoom hands it and sends
 * intents back. See quiz.js for why that seam is drawn where it is.
 */
(function () {
  "use strict";

  var Room = window.RelayQuizRoom;

  var el = {};
  ["bar-meta", "end-session", "stage-lobby", "stage-question", "stage-reveal",
   "stage-final", "stage-closed", "join-url", "join-code", "lobby-count",
   "lobby-roster", "start-quiz", "start-hint", "q-counter", "q-prompt",
   "lock-tally", "lock-fill", "lock-faces", "reveal-answer", "reveal-face",
   "reveal-name", "reveal-prompt", "reveal-right", "reveal-wrong",
   "reveal-board", "next-question", "final-podium", "final-board",
   "close-session", "new-session", "dev-fill", "dev-answer", "dev-reset"
  ].forEach(function (id) { el[id] = document.getElementById(id); });

  var STAGES = {
    lobby: el["stage-lobby"],
    question: el["stage-question"],
    reveal: el["stage-reveal"],
    final: el["stage-final"],
    closed: el["stage-closed"]
  };

  function showStage(phase) {
    Object.keys(STAGES).forEach(function (key) {
      STAGES[key].hidden = key !== phase;
    });
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  /* --------------------------------------------------------------- pieces -- */

  function rosterChip(player, dimmed) {
    var li = document.createElement("li");
    li.className = "roster__chip" + (dimmed ? " roster__chip--out" : "");
    li.appendChild(Room.faceNode(player));
    var name = document.createElement("span");
    name.textContent = player.name;
    li.appendChild(name);
    return li;
  }

  function boardRow(player, rank, showGain) {
    var li = document.createElement("li");
    li.className = "board__row";

    var rankCell = document.createElement("span");
    rankCell.className = "board__rank";
    rankCell.textContent = rank;
    li.appendChild(rankCell);

    li.appendChild(Room.faceNode(player));

    var name = document.createElement("span");
    name.textContent = player.name;
    li.appendChild(name);

    var score = document.createElement("span");
    score.className = "board__score";
    score.textContent = player.score;
    if (showGain && player.gain > 0) {
      var gain = document.createElement("span");
      gain.className = "board__gain";
      gain.textContent = "+" + player.gain;
      score.appendChild(gain);
    }
    li.appendChild(score);
    return li;
  }

  function renderBoard(node, room, showGain) {
    clear(node);
    Room.standings(room).forEach(function (player, i) {
      node.appendChild(boardRow(player, i + 1, showGain));
    });
  }

  /* ---------------------------------------------------------------- stages -- */

  function renderLobby(room) {
    el["join-code"].textContent = room.code;
    el["join-url"].textContent = location.host + "/quiz";
    el["lobby-count"].textContent = room.players.length;

    clear(el["lobby-roster"]);
    room.players.forEach(function (p) { el["lobby-roster"].appendChild(rosterChip(p)); });

    var ready = room.players.length >= 2;
    el["start-quiz"].disabled = !ready;
    el["start-hint"].textContent = ready
      ? "Everyone can still join after this."
      : "Two players minimum.";
  }

  function renderQuestion(room) {
    var q = Room.currentQuestion(room);
    if (!q) return;

    el["q-counter"].textContent = "Question " + (room.index + 1) + " of " + room.questions.length;
    el["q-prompt"].textContent = q.prompt;

    /* The subject is the answer, so nothing about them reaches this screen:
       not the name, not the face, not a gap in the row of faces below. Their
       seat is simply not among the ones being waited on. */
    var pool = Room.eligible(room);
    var locked = Room.answeredCount(room);
    el["lock-tally"].textContent = locked + " of " + pool.length;
    el["lock-fill"].style.width = pool.length ? (locked / pool.length * 100) + "%" : "0%";

    clear(el["lock-faces"]);
    pool.forEach(function (p) {
      var face = Room.faceNode(p);
      if (p.answer) face.classList.add("is-in");
      el["lock-faces"].appendChild(face);
    });

    el["reveal-answer"].textContent = Room.allAnswered(room)
      ? "Reveal the answer"
      : "Reveal the answer (" + locked + " in)";
  }

  function renderReveal(room) {
    var q = Room.currentQuestion(room);
    var subject = Room.playerById(room, q && q.subject);
    if (!q || !subject) return;

    el["reveal-face"].innerHTML = Room.faceSvg(subject);
    el["reveal-name"].textContent = subject.name;
    el["reveal-prompt"].textContent = q.prompt;

    var pool = Room.eligible(room);
    var right = pool.filter(function (p) { return p.answer === q.subject; }).length;
    el["reveal-right"].textContent = right;
    el["reveal-wrong"].textContent = pool.length - right;

    renderBoard(el["reveal-board"], room, true);

    var last = room.index + 1 >= room.questions.length;
    el["next-question"].textContent = last ? "See the final standings" : "Next question";
  }

  function renderFinal(room) {
    var ranked = Room.standings(room);
    clear(el["final-podium"]);

    /* Second, first, third, so the tallest plinth sits in the middle. */
    [1, 0, 2].forEach(function (at) {
      var player = ranked[at];
      if (!player) return;
      var spot = document.createElement("div");
      spot.className = "podium__spot podium__spot--" + (at + 1);
      spot.appendChild(Room.faceNode(player));

      var plinth = document.createElement("div");
      plinth.className = "podium__plinth";

      var medal = document.createElement("span");
      medal.className = "podium__medal";
      medal.textContent = at + 1;
      plinth.appendChild(medal);

      var name = document.createElement("span");
      name.className = "podium__name";
      name.textContent = player.name;
      plinth.appendChild(name);

      var score = document.createElement("span");
      score.className = "podium__score";
      score.textContent = player.score + (player.score === 1 ? " point" : " points");
      plinth.appendChild(document.createElement("br"));
      plinth.appendChild(score);

      spot.appendChild(plinth);
      el["final-podium"].appendChild(spot);
    });

    renderBoard(el["final-board"], room, false);
  }

  /* ----------------------------------------------------------------- draw -- */

  function render(room) {
    if (!room) { room = Room.ensure(); }

    showStage(room.phase);
    el["end-session"].hidden = room.phase === "closed" || room.phase === "final";

    el["bar-meta"].textContent = room.phase === "lobby"
      ? "Code " + room.code
      : room.players.length + (room.players.length === 1 ? " player" : " players");

    if (room.phase === "lobby") renderLobby(room);
    if (room.phase === "question") renderQuestion(room);
    if (room.phase === "reveal") renderReveal(room);
    if (room.phase === "final") renderFinal(room);
  }

  /* -------------------------------------------------------------- intents -- */

  el["start-quiz"].addEventListener("click", function () { Room.apply("start"); });
  el["reveal-answer"].addEventListener("click", function () { Room.apply("reveal"); });
  el["next-question"].addEventListener("click", function () { Room.apply("next"); });

  el["end-session"].addEventListener("click", function () {
    /* Two steps on purpose: ending drops the room to the standings so it has
       an ending, and only the second press lets everyone's screen go. */
    if (!window.confirm("End the quiz here and show the final standings?")) return;
    Room.apply("finish");
  });

  el["close-session"].addEventListener("click", function () { Room.apply("close"); });
  el["new-session"].addEventListener("click", function () { Room.apply("reset"); });

  el["dev-fill"].addEventListener("click", function () { Room.apply("seedPlayers"); });
  el["dev-answer"].addEventListener("click", function () { Room.apply("autoAnswer"); });
  el["dev-reset"].addEventListener("click", function () { Room.apply("reset"); });

  Room.ensure();
  Room.subscribe(render);
})();
