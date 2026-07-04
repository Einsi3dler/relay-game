// The Relay — frontend shell (T5.1–T5.5).
// One page, three views, everything driven by state_snapshot (protocol §2.2:
// the snapshot alone must be enough to be correct; nudges are polish).
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  var session = null;   // { matchId, playerId, name }
  var serverConfig = null; // /api/config — team sizes before the match starts
  var socket = null;
  var lastState = null;
  var mounted = null;   // { puzzleId, renderer }
  var timerHandle = null;
  var toastHandle = null;
  var overlayHandle = null;
  var reconnectDelay = 500;
  var finished = false;

  // --- session persistence (T5.5: refresh restores the match) ---

  function saveSession() {
    try { sessionStorage.setItem("relay", JSON.stringify(session)); } catch (e) {}
  }
  function loadSession() {
    try { return JSON.parse(sessionStorage.getItem("relay")); } catch (e) { return null; }
  }
  function clearSession() {
    try { sessionStorage.removeItem("relay"); } catch (e) {}
  }

  // --- tiny ui helpers ---

  function show(viewId) {
    ["view-join", "view-lobby", "view-play", "view-result"].forEach(function (id) {
      $(id).hidden = id !== viewId;
    });
  }

  function toast(text) {
    var el = $("toast");
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toastHandle);
    toastHandle = setTimeout(function () { el.hidden = true; }, 2600);
  }

  function parseDeadline(iso) {
    // Trim sub-millisecond digits (Python microseconds) for Safari's sake.
    return Date.parse(iso.replace(/(\.\d{3})\d+/, "$1"));
  }

  // --- join flow (T5.1) ---

  function bindJoin() {
    document.querySelectorAll(".team-pick .btn").forEach(function (button) {
      button.addEventListener("click", function () {
        joinFlow(button.getAttribute("data-team") || null);
      });
    });
    $("play-again").addEventListener("click", function () {
      clearSession();
      window.location.reload();
    });
  }

  function joinFlow(teamId) {
    var name = $("name-input").value.trim();
    if (!name) { showJoinError("Pick a name first!"); return; }
    var matchId = $("match-input").value.trim();
    var start = matchId
      ? Promise.resolve(matchId)
      : fetch("/api/matches", { method: "POST" })
          .then(function (r) { return r.json(); })
          .then(function (body) { return body.match.id; });
    start
      .then(function (id) {
        return fetch("/api/matches/" + encodeURIComponent(id) + "/join", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: name, team_id: teamId }),
        }).then(function (response) {
          return response.json().then(function (body) {
            if (!response.ok) throw new Error(body.detail || "Could not join.");
            session = { matchId: id, playerId: body.player.id, name: name };
            saveSession();
            connect();
          });
        });
      })
      .catch(function (error) { showJoinError(error.message); });
  }

  function showJoinError(text) {
    var el = $("join-error");
    el.textContent = text;
    el.hidden = false;
  }

  // --- websocket lifecycle (T5.5 reconnect) ---

  function connect() {
    var scheme = window.location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(
      scheme + "://" + window.location.host +
      "/ws/matches/" + session.matchId + "?player_id=" + session.playerId
    );
    socket.onopen = function () { reconnectDelay = 500; };
    socket.onmessage = function (message) { handle(JSON.parse(message.data)); };
    socket.onclose = function (event) {
      if (finished) return;
      if (event.code === 4001) return; // superseded by another tab — stand down
      if (event.code === 4404) { clearSession(); show("view-join"); return; }
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 5000);
    };
  }

  function handle(message) {
    if (message.type === "state_snapshot") render(message.state);
    else if (message.type === "error") toast(message.error);
    else if (message.type === "stage_advanced") stageOverlay("Stage " + message.stage + "! 🚀");
    else if (message.type === "event") logEvent(message.event, true);
  }

  // --- rendering (all views are pure functions of the snapshot) ---

  function render(state) {
    lastState = state;
    $("match-chip").textContent = state.id;
    $("match-chip").hidden = false;
    if (state.status === "lobby") renderLobby(state);
    else if (state.status === "finished") renderResult(state);
    else renderPlay(state);
  }

  function renderLobby(state) {
    show("view-lobby");
    $("lobby-code").textContent = state.id;
    ["alpha", "bravo"].forEach(function (teamId) {
      var list = $("lobby-" + teamId);
      list.innerHTML = "";
      state.teams[teamId].players.forEach(function (player) {
        var item = document.createElement("li");
        item.textContent = player.name + (player.id === session.playerId ? " (you)" : "");
        list.appendChild(item);
      });
    });
    var need = state.config.players_per_team ||
      (serverConfig && serverConfig.players_per_team) || 4;
    var have = state.teams.alpha.players.length + state.teams.bravo.players.length;
    $("lobby-count").textContent = have + " joined — first to " + need + " a side starts the race.";
  }

  function renderPlay(state) {
    show("view-play");
    renderStrip(state);
    renderMe(state.me);
    renderFeed(state.events);
  }

  function renderStrip(state) {
    var strip = $("team-strip");
    strip.innerHTML = "";
    ["alpha", "bravo"].forEach(function (teamId) {
      var team = state.teams[teamId];
      var row = document.createElement("div");
      row.className = "team-row " + teamId;
      row.innerHTML =
        '<span class="team-name">' + (teamId === "alpha" ? "🔥" : "🌊") + " " +
        team.name + "</span>" +
        '<span class="stage-tag">Stage ' + team.stage + "</span>";
      team.players.forEach(function (player) {
        var dot = document.createElement("span");
        dot.className = "dot" + (player.green ? " green" : "") +
          (player.connected ? "" : " off");
        dot.appendChild(document.createTextNode(player.name));
        row.appendChild(dot);
      });
      var count = document.createElement("span");
      count.className = "muted";
      count.textContent = team.green_count + "/" + team.roster_size;
      row.appendChild(count);
      strip.appendChild(row);
    });
  }

  function renderMe(me) {
    if (!me) return;
    var puzzle = me.current_puzzle;
    $("rest-card").hidden = !(me.status === "resting");
    $("puzzle-card").hidden = !puzzle;
    if (puzzle) {
      $("puzzle-prompt").textContent = puzzle.prompt;
      mountPuzzle(puzzle);
    } else {
      unmountPuzzle();
    }
    startCountdown(me.timer_deadline, me.timer_kind);
  }

  // T5.2: mount by game_id from window.RelayGames; unmount the old first.
  function mountPuzzle(puzzle) {
    if (mounted && mounted.puzzleId === puzzle.id) return; // same instance
    unmountPuzzle();
    var renderer = window.RelayGames[puzzle.game_id] || window.RelayGames.fallback;
    var api = {
      submit: function (answer) {
        if (!socket || socket.readyState !== WebSocket.OPEN) return;
        socket.send(JSON.stringify({
          type: puzzle.kind === "holding" ? "submit_holding" : "submit_answer",
          puzzle_id: puzzle.id,
          answer: String(answer),
        }));
      },
      setReady: function () {}, // shell has no external submit button in the MVP
    };
    renderer.mount($("puzzle-mount"), puzzle, api);
    mounted = { puzzleId: puzzle.id, renderer: renderer };
  }

  function unmountPuzzle() {
    if (mounted) {
      mounted.renderer.unmount();
      mounted = null;
    }
    $("puzzle-mount").innerHTML = "";
  }

  // T5.3: countdown driven by timer_deadline; server stays authoritative.
  function startCountdown(deadlineIso, kind) {
    clearInterval(timerHandle);
    var bar = $("timer-bar"), label = $("timer-label");
    if (!deadlineIso) { bar.hidden = true; label.hidden = true; return; }
    var deadline = parseDeadline(deadlineIso);
    var total = ((lastState && lastState.config[kind + "_seconds"]) ||
      (serverConfig && serverConfig[kind + "_seconds"]) || 15) * 1000;
    bar.hidden = false;
    label.hidden = false;
    var tick = function () {
      var left = Math.max(0, deadline - Date.now());
      $("timer-fill").style.width = Math.min(100, (left / total) * 100) + "%";
      label.textContent = (kind === "rest" ? "😌 Rest: " : "⚡ Holding: ") +
        Math.ceil(left / 1000) + "s";
      if (left <= 0) {
        label.textContent = "⏳ Time's up — waiting for the server…";
        clearInterval(timerHandle);
      }
    };
    tick();
    timerHandle = setInterval(tick, 250);
  }

  function renderFeed(events) {
    var feed = $("event-feed");
    feed.innerHTML = "";
    events.slice(-5).reverse().forEach(function (event) {
      logEvent(event, false);
    });
  }

  function logEvent(event, fresh) {
    var feed = $("event-feed");
    var item = document.createElement("li");
    if (fresh) item.className = "fresh";
    item.textContent = event.message;
    feed.insertBefore(item, feed.firstChild);
    while (feed.children.length > 6) feed.removeChild(feed.lastChild);
  }

  // T5.4: stage transition + result screens.
  function stageOverlay(text) {
    var overlay = $("stage-overlay");
    $("stage-overlay-text").textContent = text;
    overlay.hidden = false;
    clearTimeout(overlayHandle);
    overlayHandle = setTimeout(function () { overlay.hidden = true; }, 1400);
  }

  function renderResult(state) {
    finished = true;
    unmountPuzzle();
    clearInterval(timerHandle);
    show("view-result");
    var mine = state.me ? state.me.team_id : null;
    var won = state.winner_team_id === mine;
    $("result-emoji").textContent = won ? "🏆🎉" : "😵💨";
    $("result-title").textContent = won ? "You won!" : "You lost!";
    $("result-sub").textContent =
      "Team " + state.teams[state.winner_team_id].name + " cleared all four games first.";
  }

  // --- boot ---

  fetch("/api/config")
    .then(function (r) { return r.json(); })
    .then(function (body) { serverConfig = body; })
    .catch(function () {});
  bindJoin();
  var saved = loadSession();
  if (saved && saved.matchId && saved.playerId) {
    session = saved;
    connect(); // T5.5: snapshot on connect restores the right view
  } else {
    show("view-join");
  }
})();
