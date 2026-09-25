/* ROLL CALL — the player screen.
 *
 * A phone, held in one hand, in a room where something is being read aloud.
 * Everything it does is one of two things: show the prompt, and take one tap.
 *
 * Two rules worth naming, because both were learned the hard way in the
 * design and are easy to undo by accident:
 *
 *   * A tap selects; a second, deliberate press locks. There is no timer here,
 *     so there is no reason to punish a fat thumb, and an instant lock turns
 *     every misfire into a lost round.
 *   * The person a question is about sits it out. Their own card is drawn
 *     (the room's shape should not shift round to round) but nothing is
 *     selectable, and they are not counted in "locked in" on the host screen.
 *
 * Who *this* browser is, is kept in sessionStorage: a refresh mid-quiz should
 * put you back in your seat and not at the join form.
 */
(function () {
  "use strict";

  var Room = window.RelayQuizRoom;
  var ME_KEY = "relay.quiz.me";

  var el = {};
  ["me-meta", "stage-join", "stage-waiting", "stage-question", "stage-reveal",
   "stage-final", "stage-closed", "join-form", "field-code", "field-name",
   "field-face", "shuffle-face", "join-error", "wait-face", "wait-name",
   "wait-roster", "p-counter", "p-prompt", "p-note", "p-cards", "p-callout",
   "p-callout-big", "p-callout-small", "p-answer-face", "p-answer-name",
   "p-board", "p-awards", "p-final-rank", "p-final-score", "p-final-board", "lockbar",
   "lock-in"
  ].forEach(function (id) { el[id] = document.getElementById(id); });

  var STAGES = {
    join: el["stage-join"],
    waiting: el["stage-waiting"],
    question: el["stage-question"],
    reveal: el["stage-reveal"],
    final: el["stage-final"],
    closed: el["stage-closed"]
  };

  /* Selected but not yet locked. Deliberately not in the room: a half-made
     choice is this phone's business and nobody else's. */
  var selection = null;
  var faceCode = window.RelayAvatar.encode(window.RelayAvatar.random());

  function myId() {
    try { return window.sessionStorage.getItem(ME_KEY); } catch (err) { return null; }
  }
  function setMyId(id) {
    try { window.sessionStorage.setItem(ME_KEY, id); } catch (err) { /* private window */ }
  }

  function showStage(name) {
    Object.keys(STAGES).forEach(function (key) { STAGES[key].hidden = key !== name; });
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function ordinal(n) {
    var tens = n % 100;
    if (tens >= 11 && tens <= 13) return n + "th";
    return n + (["th", "st", "nd", "rd"][n % 10] || "th");
  }

  /* ------------------------------------------------------------------ join -- */

  function drawFace() {
    el["field-face"].innerHTML = window.RelayAvatar.svg(window.RelayAvatar.decode(faceCode));
  }

  el["shuffle-face"].addEventListener("click", function () {
    faceCode = window.RelayAvatar.encode(window.RelayAvatar.random());
    drawFace();
  });

  el["join-form"].addEventListener("submit", function (event) {
    event.preventDefault();
    var room = Room.read();
    var code = el["field-code"].value.trim().toUpperCase();
    var name = el["field-name"].value.trim();

    if (!room || room.phase === "closed") {
      el["join-error"].textContent = "No session is running right now.";
      return;
    }
    if (code !== room.code) {
      el["join-error"].textContent = "That code does not match the screen.";
      return;
    }
    if (name.length < 2) {
      el["join-error"].textContent = "Give the room a name to call you.";
      return;
    }
    var taken = room.players.some(function (p) {
      return p.name.toLowerCase() === name.toLowerCase();
    });
    if (taken) {
      el["join-error"].textContent = "Somebody is already going by that. Try another.";
      return;
    }

    el["join-error"].textContent = "";
    var id = Room.makeId();
    setMyId(id);
    Room.apply("join", { id: id, name: name, avatar: faceCode });
  });

  /* --------------------------------------------------------------- pieces -- */

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

  /* --------------------------------------------------------------- stages -- */

  function renderWaiting(room, me) {
    el["wait-face"].innerHTML = Room.faceSvg(me);
    el["wait-name"].textContent = me.name;
    clear(el["wait-roster"]);
    room.players.forEach(function (p) {
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
    var q = Room.currentQuestion(room);
    if (!q) return;

    el["p-counter"].textContent = "Question " + (room.index + 1) + " of " + room.questions.length;
    el["p-prompt"].textContent = q.prompt;

    var isSubject = me.id === q.subject;
    var locked = !!me.answer;

    el["p-note"].textContent = isSubject
      ? "This one is about you. Sit this round out and keep a straight face."
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
        var chosen = locked && me.answer === player.id;
        if (chosen) card.setAttribute("aria-pressed", "true");
        else card.classList.add("pcard--dim");
      } else if (player.id === me.id) {
        /* You cannot be the answer to a question you are answering: the
           subject is always somebody else from where you are sitting. */
        card.disabled = true;
      } else {
        card.setAttribute("aria-pressed", selection === player.id ? "true" : "false");
        card.addEventListener("click", function () {
          selection = selection === player.id ? null : player.id;
          render(Room.read());
        });
      }

      li.appendChild(card);
      el["p-cards"].appendChild(li);
    });

    el["lockbar"].hidden = isSubject || locked;
    el["lock-in"].disabled = !selection;
    if (selection) {
      var pick = Room.playerById(room, selection);
      el["lock-in"].textContent = pick ? "Lock in " + pick.name : "Lock it in";
    } else {
      el["lock-in"].textContent = "Pick a face";
    }
  }

  function renderReveal(room, me) {
    var q = Room.currentQuestion(room);
    var subject = Room.playerById(room, q && q.subject);
    if (!q || !subject) return;

    var sat = me.id === q.subject;
    var right = !sat && me.answer === q.subject;

    el["p-callout"].className = "callout " +
      (sat ? "callout--idle" : right ? "callout--right" : "callout--wrong");
    el["p-callout-big"].textContent = sat ? "That was you"
      : right ? "+" + me.gain : "Not this time";
    el["p-callout-small"].textContent = sat
      ? "No points for this one, for obvious reasons."
      : right ? (me.bonus > 0
          ? Room.BASE_POINTS + " for the answer, " + me.bonus + " for being early."
          : Room.BASE_POINTS + " for the answer.")
      : (me.answer
        ? "You said " + (Room.playerById(room, me.answer) || { name: "nobody" }).name + "."
        : "You did not lock one in.");

    el["p-answer-face"].innerHTML = Room.faceSvg(subject);
    el["p-answer-name"].textContent = subject.name;

    renderBoard(el["p-board"], room, me.id);
  }

  function renderFinal(room, me) {
    var ranked = Room.standings(room);
    var place = ranked.findIndex(function (p) { return p.id === me.id; }) + 1;
    el["p-final-rank"].textContent = place > 0 ? ordinal(place) + " place" : "Thanks for playing";
    el["p-final-score"].textContent = me.score + " points, from " + me.correct +
      (me.correct === 1 ? " right answer" : " right answers") +
      " out of " + room.questions.length + ".";
    Room.renderAwards(el["p-awards"], room);
    renderBoard(el["p-final-board"], room, me.id);
  }

  /* ------------------------------------------------------------------ draw -- */

  function render(room) {
    if (!room) {
      showStage("join");
      el["lockbar"].hidden = true;
      el["me-meta"].textContent = "";
      return;
    }

    var me = Room.playerById(room, myId());

    if (room.phase === "closed") {
      showStage("closed");
      el["lockbar"].hidden = true;
      el["me-meta"].textContent = "";
      return;
    }
    if (!me) {
      showStage("join");
      el["lockbar"].hidden = true;
      el["me-meta"].textContent = "";
      return;
    }

    el["me-meta"].textContent = me.name + " · " + me.score +
      (me.score === 1 ? " pt" : " pts");

    if (room.phase === "lobby") {
      showStage("waiting");
      el["lockbar"].hidden = true;
      renderWaiting(room, me);
      return;
    }
    if (room.phase === "question") {
      showStage("question");
      renderQuestion(room, me);
      return;
    }

    /* A new question clears the pending pick, so the last round's highlight
       never carries into the next one. */
    selection = null;
    el["lockbar"].hidden = true;

    if (room.phase === "reveal") { showStage("reveal"); renderReveal(room, me); return; }
    if (room.phase === "final") { showStage("final"); renderFinal(room, me); }
  }

  el["lock-in"].addEventListener("click", function () {
    if (!selection) return;
    Room.apply("pick", { playerId: myId(), choice: selection });
    selection = null;
  });

  drawFace();
  Room.subscribe(render);
})();
