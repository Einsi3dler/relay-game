/* ROLL CALL — the host screen.
 *
 * This is the projector. One person drives it and the whole room reads it,
 * which makes it the *least* trusted viewer in the building, not the most: it
 * is given exactly what a player is given, and the server enforces that.
 *
 * It owns no state. It renders the last snapshot and sends host actions back.
 */
(function (global) {
  "use strict";

  var Room = global.RelayQuizRoom;

  var el = {};
  ["bar-meta", "end-session", "stage-lobby", "stage-question", "stage-reveal",
   "stage-final", "stage-closed", "lobby-here", "lobby-total", "lobby-roster",
   "lobby-missing", "start-quiz", "start-hint", "q-counter", "q-prompt",
   "lock-tally", "lock-fill", "lock-roster", "reveal-answer", "reveal-face",
   "reveal-name", "reveal-prompt", "reveal-right", "reveal-wrong",
   "reveal-right-list", "reveal-wrong-list", "reveal-board", "next-question",
   "final-podium", "final-awards", "final-board", "close-session", "new-session"
  ].forEach(function (id) { el[id] = document.getElementById(id); });

  var STAGES = {
    lobby: el["stage-lobby"], question: el["stage-question"],
    reveal: el["stage-reveal"], final: el["stage-final"],
    closed: el["stage-closed"]
  };

  function showStage(phase) {
    Object.keys(STAGES).forEach(function (key) {
      STAGES[key].hidden = key !== phase;
    });
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  var revealKey = null;
  var rollHandle = null;
  function stopRoll() {
    if (rollHandle) { global.clearInterval(rollHandle); rollHandle = null; }
  }

  /* --------------------------------------------------------------- pieces -- */

  function chip(player, dimmed) {
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
    Room.standings(room).forEach(function (p, i) {
      node.appendChild(boardRow(p, i + 1, showGain));
    });
  }

  /* --------------------------------------------------------------- stages -- */

  function renderLobby(room) {
    el["lobby-here"].textContent = room.here;
    el["lobby-total"].textContent = room.registered;

    clear(el["lobby-roster"]);
    Room.present(room).forEach(function (p) {
      el["lobby-roster"].appendChild(chip(p));
    });

    /* Who is still missing, which is the thing the host actually needs and
       the reason there is no join code on this screen. */
    var away = room.players.filter(function (p) { return !p.connected; });
    clear(el["lobby-missing"]);
    away.forEach(function (p) { el["lobby-missing"].appendChild(chip(p, true)); });

    var ready = room.here >= 2;
    el["start-quiz"].disabled = !ready;
    el["start-hint"].textContent = ready
      ? "Anyone still on their way can join after this."
      : "Waiting for at least two people.";
  }

  function renderQuestion(room) {
    var q = room.question;
    if (!q) return;

    el["q-counter"].textContent = q.label + " · question " +
      (room.index + 1) + " of " + room.total;
    el["q-prompt"].textContent = "“" + q.body + "”";
    /* A fifty-word answer is a paragraph, not a headline. */
    el["q-prompt"].style.fontSize = q.body.length > 160 ? "1.9rem"
      : q.body.length > 80 ? "2.4rem" : "";

    /* Every seat that is here, the subject included. Leaving them out was the
       bug: a room that sees nine names on a ten-person roster has been handed
       the answer. */
    var here = Room.present(room);
    el["lock-tally"].textContent = room.locked_in + " of " + here.length;
    el["lock-fill"].style.width =
      here.length ? (room.locked_in / here.length * 100) + "%" : "0%";

    clear(el["lock-roster"]);
    here.forEach(function (p) {
      var li = document.createElement("li");
      li.className = "lockseat" + (p.answered ? " lockseat--in" : "");
      li.appendChild(Room.faceNode(p));

      var name = document.createElement("span");
      name.className = "lockseat__name";
      name.textContent = p.name;
      li.appendChild(name);

      var tick = document.createElement("span");
      tick.className = "lockseat__tick";
      tick.textContent = "✓";
      tick.setAttribute("aria-hidden", "true");
      li.appendChild(tick);

      el["lock-roster"].appendChild(li);
    });

    el["reveal-answer"].textContent = room.locked_in >= here.length
      ? "Reveal the answer"
      : "Reveal the answer (" + room.locked_in + " in)";
  }

  function renderReveal(room) {
    var subject = Room.playerById(room, room.subject);
    if (!subject || !room.question) return;

    el["reveal-prompt"].textContent = "“" + room.question.body + "”";

    var key = room.index + ":" + room.question.id;
    if (key !== revealKey) {
      revealKey = key;
      stopRoll();
      rollHandle = Room.rollReveal(
        el["reveal-face"], el["reveal-name"], room, subject,
        function () { rollHandle = null; });
      /* Built once: the round's result is settled the moment the host pressed
         reveal, and rebuilding per snapshot would restart the cascade. */
      Room.renderGroups({
        rightList: el["reveal-right-list"], wrongList: el["reveal-wrong-list"],
        rightCount: el["reveal-right"], wrongCount: el["reveal-wrong"]
      }, room, null);
    }

    renderBoard(el["reveal-board"], room, true);
    el["next-question"].textContent = room.index + 1 >= room.total
      ? "See the final standings" : "Next question";
  }

  function renderFinal(room) {
    var ranked = Room.standings(room);
    clear(el["final-podium"]);

    [1, 0, 2].forEach(function (at) {       // second, first, third
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

    Room.renderAwards(el["final-awards"], room);
    renderBoard(el["final-board"], room, false);
  }

  /* ----------------------------------------------------------------- draw -- */

  function render(room, status) {
    if (!room) return;
    if (room.phase !== "reveal") { revealKey = null; stopRoll(); }

    showStage(room.phase);
    el["end-session"].hidden = room.phase === "closed" || room.phase === "final";
    el["bar-meta"].textContent = status === "closed"
      ? "Reconnecting…"
      : room.here + " of " + room.registered + " here";

    if (room.phase === "lobby") renderLobby(room);
    if (room.phase === "question") renderQuestion(room);
    if (room.phase === "reveal") renderReveal(room);
    if (room.phase === "final") renderFinal(room);
  }

  /* -------------------------------------------------------------- intents -- */

  el["start-quiz"].addEventListener("click", function () { Room.host("start"); });
  el["reveal-answer"].addEventListener("click", function () { Room.host("reveal"); });
  el["next-question"].addEventListener("click", function () { Room.host("next"); });
  el["close-session"].addEventListener("click", function () { Room.host("close"); });
  el["new-session"].addEventListener("click", function () { Room.host("reset"); });

  el["end-session"].addEventListener("click", function () {
    /* Two steps on purpose: ending drops the room to the standings so it has
       an ending, and only the second press lets everyone's screen go. */
    if (!global.confirm("End the quiz here and show the final standings?")) return;
    Room.host("finish");
  });

  Room.subscribe(render);
  Room.connect({});          // the host cookie is the credential
})(window);
