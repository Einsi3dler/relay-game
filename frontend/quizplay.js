/* ROLL CALL — the player screen.
 *
 * A phone, held in one hand, in a room where something is being read aloud.
 * It renders whatever the server last sent and sends one tap back; it holds
 * no game state and decides nothing. Every rule it appears to enforce below
 * is enforced again on the server, because a disabled button is a courtesy,
 * not a guard.
 *
 * Two of those courtesies are worth naming, because both were learned the
 * hard way and are easy to undo:
 *
 *   * A tap selects; a second, deliberate press locks. There is no timer, so
 *     there is no reason to punish a fat thumb.
 *   * The person a question is about still locks in, with a confirm button
 *     instead of the grid. If the one person who cannot answer were also the
 *     one tile that never lit up on the projector, the room would read the
 *     answer off it.
 */
(function (global) {
  "use strict";

  var Room = global.RelayQuizRoom;
  var token = new global.URLSearchParams(global.location.search).get("token") || "";

  var el = {};
  ["me-meta", "stage-nolink", "stage-waiting", "stage-question", "stage-reveal",
   "stage-final", "stage-closed", "wait-face", "wait-name", "wait-roster",
   "p-counter", "p-prompt", "p-note", "p-cards", "p-callout", "p-callout-big",
   "p-callout-small", "p-answer-face", "p-answer-name", "p-right", "p-wrong",
   "p-right-list", "p-wrong-list", "p-board", "p-awards", "p-final-rank",
   "p-final-score", "p-final-board", "lockbar", "lock-in"
  ].forEach(function (id) { el[id] = document.getElementById(id); });

  var STAGES = {
    nolink: el["stage-nolink"], waiting: el["stage-waiting"],
    question: el["stage-question"], reveal: el["stage-reveal"],
    final: el["stage-final"], closed: el["stage-closed"]
  };

  function showStage(name) {
    Object.keys(STAGES).forEach(function (key) {
      STAGES[key].hidden = key !== name;
    });
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  function ordinal(n) {
    var tens = n % 100;
    if (tens >= 11 && tens <= 13) return n + "th";
    return n + (["th", "st", "nd", "rd"][n % 10] || "th");
  }

  /* Selected but not yet locked. Deliberately not sent anywhere: a half-made
     choice is this phone's business. */
  var selection = null;
  var revealKey = null;
  var rollHandle = null;

  function stopRoll() {
    if (rollHandle) { global.clearInterval(rollHandle); rollHandle = null; }
  }

  /* ---------------------------------------------------------------- pieces -- */

  function boardRow(player, rank, meId) {
    var li = document.createElement("li");
    li.className = "board__row" + (player.id === meId ? " board__row--you" : "");

    var rankCell = document.createElement("span");
    rankCell.className = "board__rank";
    rankCell.textContent = rank;
    li.appendChild(rankCell);

    li.appendChild(Room.faceNode(player));

    var name = document.createElement("span");
    name.textContent = player.id === meId ? player.name + " (you)" : player.name;
    li.appendChild(name);

    var score = document.createElement("span");
    score.className = "board__score";
    score.textContent = player.score;
    li.appendChild(score);
    return li;
  }

  function renderBoard(node, room, meId) {
    clear(node);
    Room.standings(room).forEach(function (player, i) {
      node.appendChild(boardRow(player, i + 1, meId));
    });
  }

  /* ---------------------------------------------------------------- stages -- */

  function renderWaiting(room, me) {
    el["wait-face"].innerHTML = Room.faceSvg(me);
    el["wait-name"].textContent = me.name;
    clear(el["wait-roster"]);
    Room.present(room).forEach(function (p) {
      var li = document.createElement("li");
      li.className = "roster__chip";
      li.appendChild(Room.faceNode(p));
      var name = document.createElement("span");
      name.textContent = p.name;
      li.appendChild(name);
      el["wait-roster"].appendChild(li);
    });
  }

  function renderQuestion(room, me) {
    var q = room.question;
    if (!q) return;

    el["p-counter"].textContent = q.label + " · " +
      (room.index + 1) + " of " + room.total;
    el["p-prompt"].textContent = "“" + q.body + "”";
    /* Long answers are paragraphs, not headlines. The display size that fits
       "I play the harmonica" does not fit fifty words. */
    el["p-prompt"].style.fontSize = q.body.length > 160 ? "1.05rem"
      : q.body.length > 80 ? "1.2rem" : "";

    var isSubject = room.you_are_subject;
    var locked = !!room.your_answer;

    el["p-note"].textContent = isSubject
      ? (locked ? "Locked in. Keep a straight face."
                : "This one is about you. You cannot answer it, but confirm below so the room is not left staring at the one name that never lights up.")
      : locked ? "Locked in. Waiting for the rest of the room." : "";

    clear(el["p-cards"]);
    room.players.forEach(function (player) {
      var li = document.createElement("li");
      var card = document.createElement("button");
      card.type = "button";
      card.className = "pcard";
      card.appendChild(Room.faceNode(player));

      var name = document.createElement("span");
      name.className = "pcard__name";
      name.textContent = player.name;
      card.appendChild(name);

      if (player.id === me.id) card.classList.add("pcard--self");

      if (isSubject || locked) {
        card.disabled = true;
        if (locked && room.your_answer === player.id) {
          card.setAttribute("aria-pressed", "true");
        } else {
          card.classList.add("pcard--dim");
        }
      } else if (player.id === me.id) {
        card.disabled = true;      // it is never you
      } else {
        card.setAttribute("aria-pressed", selection === player.id ? "true" : "false");
        card.addEventListener("click", function () {
          selection = selection === player.id ? null : player.id;
          render(room);
        });
      }

      li.appendChild(card);
      el["p-cards"].appendChild(li);
    });

    el["lockbar"].hidden = locked;
    if (isSubject) {
      el["lock-in"].disabled = false;
      el["lock-in"].textContent = "Ready, my lips are sealed";
      return;
    }
    el["lock-in"].disabled = !selection;
    var pick = selection && Room.playerById(room, selection);
    el["lock-in"].textContent = pick ? "Lock in " + pick.name : "Pick a face";
  }

  function renderReveal(room, me) {
    var subject = Room.playerById(room, room.subject);
    if (!subject) return;

    var sat = me.id === room.subject;
    var right = !sat && room.your_answer === room.subject;
    var tone = sat ? "callout--idle" : right ? "callout--right" : "callout--wrong";

    el["p-callout-big"].textContent = sat ? "That was you"
      : right ? "+" + me.gain : "Not this time";
    el["p-callout-small"].textContent = sat
      ? "No points for this one, for obvious reasons."
      : right ? (me.bonus > 0
          ? Room.BASE_POINTS + " for the answer, " + me.bonus + " for being early."
          : Room.BASE_POINTS + " for the answer.")
      : (room.your_answer
          ? "You said " + ((Room.playerById(room, room.your_answer) || {}).name || "nobody") + "."
          : "You did not lock one in.");

    var key = room.index + ":" + (room.question && room.question.id);
    if (key !== revealKey) {
      revealKey = key;
      stopRoll();
      el["p-callout"].className = "callout " + tone + " is-held";
      rollHandle = Room.rollReveal(
        el["p-answer-face"], el["p-answer-name"], room, subject,
        function () {
          rollHandle = null;
          el["p-callout"].className = "callout " + tone + " is-landed";
        });
      Room.renderGroups({
        rightList: el["p-right-list"], wrongList: el["p-wrong-list"],
        rightCount: el["p-right"], wrongCount: el["p-wrong"]
      }, room, me.id);
    }

    renderBoard(el["p-board"], room, me.id);
  }

  function renderFinal(room, me) {
    var ranked = Room.standings(room);
    var place = 0;
    ranked.forEach(function (p, i) { if (p.id === me.id) place = i + 1; });
    el["p-final-rank"].textContent = place ? ordinal(place) + " place" : "Thanks for playing";
    el["p-final-score"].textContent = me.score + " points, from " + me.correct +
      (me.correct === 1 ? " right answer" : " right answers") +
      " out of " + room.total + ".";
    Room.renderAwards(el["p-awards"], room);
    renderBoard(el["p-final-board"], room, me.id);
  }

  /* ------------------------------------------------------------------ draw -- */

  function render(room, status) {
    if (!room || room.phase !== "reveal") { revealKey = null; stopRoll(); }

    if (status === "rejected" || !token) {
      showStage("nolink");
      el["lockbar"].hidden = true;
      el["me-meta"].textContent = "";
      return;
    }
    if (!room) return;

    var me = Room.playerById(room, room.you);
    if (room.phase === "closed" || !me) {
      showStage(room.phase === "closed" ? "closed" : "nolink");
      el["lockbar"].hidden = true;
      el["me-meta"].textContent = "";
      return;
    }

    el["me-meta"].textContent = me.name + " · " + me.score;

    if (room.phase === "lobby") {
      showStage("waiting"); el["lockbar"].hidden = true;
      renderWaiting(room, me); return;
    }
    if (room.phase === "question") {
      showStage("question"); renderQuestion(room, me); return;
    }

    selection = null;
    el["lockbar"].hidden = true;
    if (room.phase === "reveal") { showStage("reveal"); renderReveal(room, me); return; }
    if (room.phase === "final") { showStage("final"); renderFinal(room, me); }
  }

  el["lock-in"].addEventListener("click", function () {
    el["lock-in"].disabled = true;
    /* The subject's grid is entirely disabled, so `selection` is still null
       for them, and null is exactly how the wire says "I am sitting this one
       out". Everyone else sends the id they picked. */
    Room.answer(selection);
    selection = null;
  });

  if (!token) { showStage("nolink"); return; }
  Room.subscribe(render);
  Room.connect({ token: token });
})(window);
