// The Relay — frontend shell (T5.1–T5.5, host-controlled lobby).
// One page, four views, everything driven by state_snapshot (protocol §2.2:
// the snapshot alone must be enough to be correct; nudges are polish).
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  var session = null;      // { matchId, playerId, name }
  var serverConfig = null; // /api/config — caps and defaults pre-start
  var socket = null;
  var lastState = null;
  var mounted = null;      // { puzzleId, renderer }
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
    session = null;
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

  function inviteParam() {
    try {
      return new URLSearchParams(window.location.search).get("match") || "";
    } catch (e) { return ""; }
  }

  function sendAction(fields) {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    fields.type = "lobby_action";
    socket.send(JSON.stringify(fields));
  }

  // --- landing: host or join (T5.1) ---

  function bindLanding() {
    $("host-btn").addEventListener("click", function () {
      var name = requireName();
      if (!name) return;
      fetch("/api/matches", { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function (body) { joinMatch(body.match.id, name); })
        .catch(function () { showJoinError("Could not create a match."); });
    });
    $("join-btn").addEventListener("click", function () {
      $("join-code-row").hidden = false;
      $("match-input").focus();
    });
    $("join-go").addEventListener("click", function () {
      var name = requireName();
      var code = $("match-input").value.trim();
      if (!name) return;
      if (!code) { showJoinError("Enter a match code."); return; }
      joinMatch(code, name);
    });
    $("play-again").addEventListener("click", function () {
      clearSession();
      window.location.href = "/play";
    });

    // Invite link (?match=CODE) routes straight to the join flow.
    var invited = inviteParam();
    if (invited) {
      $("host-btn").hidden = true;
      $("join-btn").hidden = true;
      $("join-code-row").hidden = false;
      $("match-input").value = invited;
      $("name-input").focus();
    }
  }

  function requireName() {
    var name = $("name-input").value.trim();
    if (!name) { showJoinError("Pick a name first!"); return null; }
    return name;
  }

  function joinMatch(matchId, name) {
    fetch("/api/matches/" + encodeURIComponent(matchId) + "/join", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name }),
    })
      .then(function (response) {
        return response.json().then(function (body) {
          if (!response.ok) throw new Error(body.detail || "Could not join.");
          session = { matchId: matchId, playerId: body.player.id, name: name };
          saveSession();
          try {
            window.history.replaceState(null, "", "/play?match=" + matchId);
          } catch (e) {}
          connect();
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
      if (event.code === 4403) {       // kicked by the host
        clearSession();
        show("view-join");
        toast("You were kicked from the lobby.");
        return;
      }
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

  // --- lobby (T5.1 + host controls) ---

  function playerRow(state, player) {
    var me = player.id === session.playerId;
    var isHost = player.id === state.host_player_id;
    var iAmHost = state.host_player_id === session.playerId;
    var row = document.createElement("li");
    var label = document.createElement("span");
    label.textContent =
      player.name + (isHost ? " 🎛️" : "") + (me ? " (you)" : "") +
      (player.connected ? "" : " 💤");
    row.appendChild(label);
    if (iAmHost && !me) {
      var controls = document.createElement("span");
      controls.className = "host-controls";
      [["alpha", "🔥"], ["bravo", "🌊"]].forEach(function (pair) {
        if (player.team_id === pair[0]) return;
        var move = document.createElement("button");
        move.className = "mini-btn";
        move.title = "Move to " + pair[0];
        move.textContent = "→" + pair[1];
        move.addEventListener("click", function () {
          sendAction({ action: "move", target_id: player.id, team_id: pair[0] });
        });
        controls.appendChild(move);
      });
      var kick = document.createElement("button");
      kick.className = "mini-btn kick";
      kick.title = "Kick";
      kick.textContent = "✕";
      kick.addEventListener("click", function () {
        sendAction({ action: "kick", target_id: player.id });
      });
      controls.appendChild(kick);
      row.appendChild(controls);
    }
    return row;
  }

  function renderLobby(state) {
    show("view-lobby");
    $("lobby-code").textContent = state.id;
    var me = state.me;
    var iAmHost = state.host_player_id === session.playerId;

    var unassignedBox = $("lobby-unassigned");
    var list = unassignedBox.querySelector("ul");
    list.innerHTML = "";
    state.unassigned.forEach(function (player) {
      list.appendChild(playerRow(state, player));
    });
    unassignedBox.hidden = state.unassigned.length === 0;

    ["alpha", "bravo"].forEach(function (teamId) {
      var box = $("lobby-team-" + teamId);
      var teamList = box.querySelector("ul");
      teamList.innerHTML = "";
      var team = state.teams[teamId];
      team.players.forEach(function (player) {
        teamList.appendChild(playerRow(state, player));
      });
      var joinButton = box.querySelector(".join-team-btn");
      var cap = (serverConfig && serverConfig.players_per_team) || 4;
      var full = team.players.length >= cap;
      joinButton.hidden = !me || me.team_id === teamId || full;
      joinButton.onclick = function () {
        sendAction({ action: "set_team", team_id: teamId });
      };
    });

    var panel = $("host-panel");
    panel.hidden = !iAmHost;
    if (iAmHost) {
      $("min-value").textContent = state.min_players;
      $("min-down").onclick = function () {
        sendAction({ action: "set_min_players", value: state.min_players - 1 });
      };
      $("min-up").onclick = function () {
        sendAction({ action: "set_min_players", value: state.min_players + 1 });
      };
      var blocker = startBlocker(state);
      $("start-btn").disabled = !!blocker;
      $("start-blocker").textContent = blocker || "All set — go!";
      $("start-btn").onclick = function () { sendAction({ action: "start" }); };
    }

    // Host went missing? Anyone can claim the seat.
    var host = findPlayer(state, state.host_player_id);
    var hostGone = !host || !host.connected;
    $("claim-host").hidden = !hostGone || iAmHost;
    $("claim-host").onclick = function () { sendAction({ action: "claim_host" }); };
  }

  function findPlayer(state, playerId) {
    var found = null;
    state.unassigned.forEach(function (p) { if (p.id === playerId) found = p; });
    ["alpha", "bravo"].forEach(function (teamId) {
      state.teams[teamId].players.forEach(function (p) {
        if (p.id === playerId) found = p;
      });
    });
    return found;
  }

  function startBlocker(state) {
    if (state.unassigned.length) {
      var names = state.unassigned.map(function (p) { return p.name; }).join(", ");
      return "Everyone needs a team — waiting on " + names + ".";
    }
    var short = null;
    ["alpha", "bravo"].forEach(function (teamId) {
      if (state.teams[teamId].players.length < state.min_players) {
        short = "Team " + state.teams[teamId].name + " needs " +
          state.min_players + " player(s).";
      }
    });
    return short;
  }

  $("copy-link") && $("copy-link").addEventListener("click", function () {
    var link = window.location.origin + "/play?match=" + (lastState ? lastState.id : "");
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(link).then(function () { toast("Invite link copied!"); });
    } else {
      window.prompt("Copy the invite link:", link);
    }
  });

  // --- play (T5.2/T5.3) ---

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
  bindLanding();
  var saved = loadSession();
  var invited = inviteParam();
  // An invite for a *different* match beats a stale saved session.
  if (saved && saved.matchId && saved.playerId &&
      (!invited || invited === saved.matchId)) {
    session = saved;
    connect(); // T5.5: snapshot on connect restores the right view
  } else {
    show("view-join");
  }
})();
