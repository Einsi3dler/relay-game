// The Relay — frontend shell (v2: leaders, levels, wait/bonus, perks).
// One page, five views, everything driven by state_snapshot (protocol rule:
// the snapshot alone must be enough to be correct; nudges are polish).
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  var session = null;      // { matchId, playerId, name }
  var serverConfig = null; // /api/config — caps, perk catalogue, game library
  var gameNames = {};      // game id -> display name (from the library)
  var socket = null;
  var lastState = null;
  var mounted = null;      // { puzzleId, renderer }
  var heldSubmit = null;   // an answer held back while frozen — see mountPuzzle
  var mountedDuel = null;  // { duelId, renderer, mountId }
  var duelTimerHandle = null;
  var timerHandle = null;
  var frozenHandle = null;
  var effectsHandle = null;
  var silenceHandle = null;
  var bombClockHandle = null;
  var toastHandle = null;
  var overlayHandle = null;
  var reconnectDelay = 500;
  var heartbeatHandle = null;
  // The server evicts a match after MATCH_TTL_SECONDS with no client message.
  // A lobby waiting on the last player, or a team thinking hard, sends nothing
  // at all, so being connected is not by itself enough to stay alive. The
  // protocol has a heartbeat for exactly this; well inside the eviction window
  // so a couple of missed beats cost nothing.
  var HEARTBEAT_MS = 240000;
  var finished = false;
  var leaving = false;   // we asked to go; the close that follows is not a kick

  // --- session persistence (refresh restores the match) ---

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
    ["view-join", "view-lobby", "view-play", "view-leader", "view-result"]
      .forEach(function (id) { $(id).hidden = id !== viewId; });
    // The command dashboard is the only dark screen, so the page ground has to
    // follow it. Every other view keeps style.css's light background.
    document.body.classList.toggle("gm-active", viewId === "view-leader");
    // The two dark surfaces. The page ground follows the view rather than the
    // other way round, so the light player screens are untouched by either.
    document.body.classList.toggle("result-active", viewId === "view-result");
    document.body.classList.toggle("play-active", viewId === "view-play");
    document.body.classList.toggle("join-active", viewId === "view-join");
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

  function send(fields) {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify(fields));
  }

  // --- the design gallery ---------------------------------------------
  //
  // `/play?preview=<state>&key=<key>` renders one canned snapshot from
  // backend/preview.py and never opens a socket, so every screen that only
  // exists inside a running match can be looked at on demand. Read-only by
  // construction: `send()` above already no-ops without a socket, so the
  // controls draw and do nothing.

  function previewParam() {
    return new URLSearchParams(window.location.search).get("preview");
  }

  function startPreview() {
    fetch("/api/preview" + window.location.search)
      .then(function (response) {
        if (!response.ok) throw new Error("no preview named " + previewParam());
        return response.json();
      })
      .then(function (body) {
        var state = body.state;
        // render() and the host controls read the viewer off the session, so
        // the preview borrows the identity its snapshot was built for.
        session = {
          matchId: state.id,
          playerId: (state.me && state.me.id) || "",
          name: (state.me && state.me.name) || "",
        };
        render(state);
      })
      .catch(function (error) {
        show("view-join");
        showJoinError(error.message);
      });
  }

  function sendAction(fields) {
    fields.type = "lobby_action";
    send(fields);
  }

  function secondsOption(value, label) {
    var option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    return option;
  }

  // "Rock Paper Scissors 5s, Crown Duel 10s, ..." — read from the server's own
  // catalogue rather than restated here, so it cannot drift from the modules.
  function duelPaces() {
    var duels = (serverConfig && serverConfig.duels) || [];
    if (!duels.length) return "five to ten seconds a round";
    return duels.map(function (duel) {
      return duel.name + " " + duel.choice_seconds + "s";
    }).join(", ");
  }

  // Duel ids aren't in the game library (the server picks them, so the lobby
  // picker never offers them) — name them from the duel catalogue instead.
  var DUEL_NAMES = {
    rps_duel: "Rock Paper Scissors", crown_duel: "Crown Duel",
    number_clash: "Number Clash", bid_war: "Bid War",
  };

  function gameName(gameId) {
    return gameNames[gameId] || DUEL_NAMES[gameId] || gameId || "?";
  }

  function roleName(roleId) {
    var roles = (serverConfig && serverConfig.roles) || {};
    return (roles[roleId] && roles[roleId].name) || roleId || "?";
  }

  // Games a role may be assigned: null means the whole library (Generalist).
  function roleGames(roleId) {
    var roles = (serverConfig && serverConfig.roles) || {};
    var games = roles[roleId] ? roles[roleId].games : [];
    if (games === null) {
      return ((serverConfig && serverConfig.library) || []).map(function (entry) {
        return entry.id;
      });
    }
    return games || [];
  }

  // --- landing: host or join ---

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
    $("choice-wait").addEventListener("click", function () {
      send({ type: "choose_wait" });
      $("choice-overlay").hidden = true;
    });
    $("choice-bonus").addEventListener("click", function () {
      send({ type: "choose_bonus" });
      $("choice-overlay").hidden = true;
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

  // --- websocket lifecycle ---

  function connect() {
    var scheme = window.location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(
      scheme + "://" + window.location.host +
      "/ws/matches/" + session.matchId + "?player_id=" + session.playerId
    );
    socket.onopen = function () { reconnectDelay = 500; startHeartbeat(); };
    socket.onmessage = function (message) { handle(JSON.parse(message.data)); };
    socket.onclose = function (event) {
      clearInterval(heartbeatHandle);
      if (finished) return;
      if (event.code === 4001) return; // superseded by another tab — stand down
      if (event.code === 4403) {       // removed from the lobby
        clearSession();
        show("view-join");
        // Leaving closes the same way being kicked does, so only say "kicked"
        // when we didn't ask for it.
        toast(leaving ? "You left the lobby." : "You were kicked from the lobby.");
        leaving = false;
        return;
      }
      if (event.code === 4402) {       // the host binned the lobby
        clearSession();
        show("view-join");
        toast("The host cancelled the session.");
        return;
      }
      if (event.code === 4404) { clearSession(); show("view-join"); return; }
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 5000);
    };
  }

  function startHeartbeat() {
    clearInterval(heartbeatHandle);
    heartbeatHandle = setInterval(function () {
      send({ type: "heartbeat" });
    }, HEARTBEAT_MS);
  }

  function handle(message) {
    if (message.type === "state_snapshot") render(message.state);
    else if (message.type === "error") toast(message.error);
    else if (message.type === "level_advanced") levelOverlay(message);
    else if (message.type === "perk_used") perkToast(message);
    else if (message.type === "duel_result") duelToast(message);
    else if (message.type === "event") logEvent(message.event, true);
  }

  // The server has always said which team advanced; the overlay used to throw
  // that away and tell both teams the same thing. Name the team instead, and
  // colour it by whether it was yours, because "they moved" and "we moved" are
  // opposite pieces of news.
  function levelOverlay(message) {
    var team = lastState && lastState.teams && lastState.teams[message.team_id];
    var name = team ? team.name : message.team_id;
    var mine = !!(lastState && lastState.me &&
      lastState.me.team_id === message.team_id);
    stageOverlay(name + " progressed to Level " + message.level,
      mine ? "mine" : "rival");
  }

  function perkToast(message) {
    var perk = (lastState && lastState.config.perks || {})[message.perk_id];
    var name = perk ? perk.name : message.perk_id;
    var mine = lastState && lastState.me && lastState.me.team_id === message.by_team_id;
    toast(mine ? "🛒 Your team used " + name + "!" : "⚠️ Enemy perk: " + name + "!");
  }

  // --- rendering (all views are pure functions of the snapshot) ---

  function render(state) {
    lastState = state;
    $("match-chip").textContent = state.id;
    $("match-chip").hidden = false;
    renderHostLive(state);
    if (state.status === "lobby") renderLobby(state);
    else if (state.status === "finished") renderResult(state);
    else if (state.me && state.me.is_leader) renderLeader(state);
    else renderPlay(state);
  }

  // The one host control that outlives the lobby. A running match can only be
  // stopped by the host, so it rides along on both the play and leader views.
  function renderHostLive(state) {
    var bar = $("host-live");
    var mine = state.status === "active" &&
      state.host_player_id === session.playerId;
    bar.hidden = !mine;
    if (!mine) return;
    $("end-session").onclick = function () {
      if (!window.confirm("End the match for everyone? No winner is recorded.")) return;
      sendAction({ action: "end_session" });
    };
  }

  // --- lobby (teams, leader seats, game assignment) ---

  function teamName(state, teamId) {
    var team = state.teams[teamId];
    return (team && team.name) || teamId;
  }

  function playerRow(state, player) {
    var me = player.id === session.playerId;
    var isHost = player.id === state.host_player_id;
    var iAmHost = state.host_player_id === session.playerId;
    var row = document.createElement("li");
    var label = document.createElement("span");
    label.textContent =
      player.name + (isHost ? " 🎛️" : "") + (player.is_leader ? " 🎖️" : "") +
      (me ? " (you)" : "") + (player.connected ? "" : " 💤") +
      (player.role ? " · " + roleName(player.role) : "");
    row.appendChild(label);
    var controls = document.createElement("span");
    controls.className = "host-controls";
    var myself = state.me;
    if (myself && myself.is_leader && !me && player.team_id === myself.team_id) {
      var hand = document.createElement("button");
      hand.className = "mini-btn";
      hand.title = "Hand over the Grandmaster seat";
      hand.textContent = "🎖️→";
      hand.addEventListener("click", function () {
        send({ type: "give_leader", target_id: player.id });
      });
      controls.appendChild(hand);
    }
    if (iAmHost && !me) {
      [["alpha", "🔥"], ["bravo", "🌊"]].forEach(function (pair) {
        if (player.team_id === pair[0]) return;
        var move = document.createElement("button");
        move.className = "mini-btn";
        move.title = "Move to " + teamName(state, pair[0]);
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
    }
    if (controls.children.length) row.appendChild(controls);
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
      // The name is the host's to set, so it is read from state every render
      // rather than baked into the markup.
      box.querySelector(".team-name").textContent = team.name;
      var tag = box.querySelector(".team-tag");
      var isMine = !!me && me.team_id === teamId;
      tag.hidden = !me;
      tag.textContent = isMine ? "your squad" : "opponents";
      tag.className = "team-tag " + (isMine ? "tag-mine" : "tag-theirs");
      box.classList.toggle("is-mine", isMine);
      box.classList.toggle("is-theirs", !!me && !isMine);

      var joinButton = box.querySelector(".join-team-btn");
      // The seat count is this match's, not the global ceiling: the host may
      // have sized the table down.
      var cap = (state.max_players || 1) + 1;
      var full = team.players.length >= cap;
      joinButton.textContent = "Join " + team.name;
      joinButton.hidden = !me || me.team_id === teamId || full;
      joinButton.onclick = function () {
        sendAction({ action: "set_team", team_id: teamId });
      };
      // Leader seat: claimable by a teammate while empty or its holder is away.
      var leader = null;
      team.players.forEach(function (p) { if (p.is_leader) leader = p; });
      var claimBtn = box.querySelector(".claim-leader-btn");
      var canClaim = me && me.team_id === teamId && !me.is_leader &&
        (!leader || !leader.connected);
      claimBtn.hidden = !canClaim;
      claimBtn.onclick = function () { sendAction({ action: "claim_leader" }); };
    });

    renderAssignPanel(state);

    var panel = $("host-panel");
    panel.hidden = !iAmHost;
    if (iAmHost) {
      var ceiling = (serverConfig && serverConfig.max_players_ceiling) ||
        state.max_players;
      $("min-value").textContent = state.min_players;
      $("min-down").onclick = function () {
        sendAction({ action: "set_min_players", value: state.min_players - 1 });
      };
      $("min-up").onclick = function () {
        sendAction({ action: "set_min_players", value: state.min_players + 1 });
      };
      $("min-down").disabled = state.min_players <= 1;
      $("min-up").disabled = state.min_players >= state.max_players;

      $("max-value").textContent = state.max_players;
      $("max-down").onclick = function () {
        sendAction({ action: "set_max_players", value: state.max_players - 1 });
      };
      $("max-up").onclick = function () {
        sendAction({ action: "set_max_players", value: state.max_players + 1 });
      };
      $("max-down").disabled = state.max_players <= 1;
      $("max-up").disabled = state.max_players >= ceiling;
      // Say where the ceiling comes from, so a host who hits it knows it is a
      // rule of the game rather than an arbitrary limit.
      $("cap-note").textContent =
        "Up to " + ceiling + " per team — one seat per game, plus the Duelist.";

      // Rounds to win. A short race is a quick one, not an easy one — the
      // difficulty rungs spread so the finale is always the hardest tier.
      var lowRounds = (serverConfig && serverConfig.min_level_count) || 3;
      var highRounds = (serverConfig && serverConfig.max_level_count) || 10;
      var picker = $("level-count");
      if (picker.options.length !== highRounds - lowRounds + 1) {
        picker.innerHTML = "";
        for (var n = lowRounds; n <= highRounds; n++) {
          var opt = document.createElement("option");
          opt.value = String(n);
          opt.textContent = n + " rounds";
          picker.appendChild(opt);
        }
      }
      picker.value = String(state.level_count);
      picker.onchange = function () {
        sendAction({ action: "set_level_count", value: parseInt(picker.value, 10) });
      };
      $("round-note").textContent = state.level_count === highRounds
        ? "The full ladder, one rung a round."
        : "A shorter race, same finish — the difficulty still ends at the top.";

      // The duel round window. Every duel game declares its own — five seconds
      // to throw a hand, ten to read a hand of cards — and this one setting
      // overrides all of them, so a group can make every duel move at the pace
      // they want without the games disagreeing about it.
      var windows = (serverConfig && serverConfig.duel_round_seconds_choices) ||
        [3, 5, 8, 10, 12, 15, 20, 30];
      var clock = $("duel-seconds");
      if (clock.options.length !== windows.length + 1) {
        clock.innerHTML = "";
        clock.appendChild(secondsOption("", "Each game's own pace"));
        windows.forEach(function (seconds) {
          clock.appendChild(secondsOption(String(seconds), seconds + "s a round"));
        });
      }
      clock.value = state.duel_round_seconds ? String(state.duel_round_seconds) : "";
      clock.onchange = function () {
        sendAction({
          action: "set_duel_seconds", value: parseInt(clock.value, 10) || 0,
        });
      };
      $("duel-seconds-note").textContent = state.duel_round_seconds
        ? "Every duel runs " + state.duel_round_seconds +
          "s a round, whichever game the server picks."
        : "Each duel keeps its own: " + duelPaces() + ".";

      ["alpha", "bravo"].forEach(function (teamId) {
        var input = $("name-" + teamId);
        // Don't fight the host's cursor: only refill a box they aren't in.
        if (document.activeElement !== input) input.value = state.teams[teamId].name;
        $("rename-" + teamId).onclick = function () {
          sendAction({
            action: "set_team_name", team_id: teamId, name: input.value
          });
        };
        input.onkeydown = function (event) {
          if (event.key === "Enter") $("rename-" + teamId).click();
        };
      });

      var blocker = startBlocker(state);
      $("start-btn").disabled = !!blocker;
      $("start-blocker").textContent = blocker || "All set — go!";
      $("start-btn").onclick = function () { sendAction({ action: "start" }); };
      $("cancel-session").onclick = function () {
        if (!window.confirm("Cancel the session? Everyone is sent back.")) return;
        sendAction({ action: "cancel_session" });
      };
    }

    // Host went missing? Anyone can claim the seat.
    var host = findPlayer(state, state.host_player_id);
    var hostGone = !host || !host.connected;
    $("claim-host").hidden = !hostGone || iAmHost;
    $("claim-host").onclick = function () { sendAction({ action: "claim_host" }); };

    // Anyone may walk away, the host included — the seat passes to whoever is
    // still here, so leaving never strands the lobby.
    var leave = $("leave-lobby");
    leave.hidden = !me;
    leave.onclick = function () {
      if (!window.confirm("Leave this lobby?")) return;
      leaving = true;
      sendAction({ action: "leave" });
    };
  }

  // The Grandmaster's assignment panel: one row per playing teammate, a role
  // select first, then a game select filtered to that role's games.
  function renderAssignPanel(state) {
    var me = state.me;
    var panel = $("assign-panel");
    if (!me || !me.is_leader || !me.team_id) { panel.hidden = true; return; }
    panel.hidden = false;
    var team = state.teams[me.team_id];
    var taken = {};
    team.players.forEach(function (p) {
      if (p.assigned_game) taken[p.assigned_game] = p.id;
    });
    var rows = $("assign-rows");
    rows.innerHTML = "";
    team.players.forEach(function (player) {
      if (player.is_leader) return;
      var row = document.createElement("div");
      row.className = "assign-row";
      var label = document.createElement("span");
      label.textContent = player.name;
      row.appendChild(label);

      var roleSelect = document.createElement("select");
      roleSelect.className = "assign-select";
      var rolePlaceholder = document.createElement("option");
      rolePlaceholder.value = "";
      rolePlaceholder.textContent = "— pick a role —";
      roleSelect.appendChild(rolePlaceholder);
      var roles = (serverConfig && serverConfig.roles) || {};
      Object.keys(roles).forEach(function (roleId) {
        var games = roles[roleId].games;
        if (games !== null && !games.length) return; // reserved (no games yet)
        var option = document.createElement("option");
        option.value = roleId;
        option.textContent = roles[roleId].name;
        roleSelect.appendChild(option);
      });
      roleSelect.value = player.role || "";
      roleSelect.onchange = function () {
        if (!roleSelect.value) return;
        sendAction({
          action: "assign_role", target_id: player.id, role_id: roleSelect.value,
        });
      };
      row.appendChild(roleSelect);

      if (player.role === "duelist") {
        // The server picks a Duelist's game, so there is nothing to choose.
        var fixed = document.createElement("span");
        fixed.className = "muted";
        fixed.textContent = "⚔️ the server picks the duel";
        row.appendChild(fixed);
        rows.appendChild(row);
        return;
      }

      if (roleIsFixed(player.role)) {
        // A fixed role names its own game — you choose who holds it, not what
        // they play.
        var locked = document.createElement("span");
        locked.className = "muted";
        locked.textContent = "💣 " + gameName(player.assigned_game) +
          " — the role fixes it";
        row.appendChild(locked);
        rows.appendChild(row);
        return;
      }

      var select = document.createElement("select");
      select.className = "assign-select";
      var placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = player.role
        ? "— pick a game —" : "— assign a role first —";
      select.appendChild(placeholder);
      select.disabled = !player.role;
      var allowed = player.role ? roleGames(player.role) : [];
      ((serverConfig && serverConfig.library) || []).forEach(function (entry) {
        if (allowed.indexOf(entry.id) === -1) return; // outside the role
        var option = document.createElement("option");
        option.value = entry.id;
        option.textContent = entry.name;
        if (taken[entry.id] && taken[entry.id] !== player.id) {
          option.disabled = true; // no two teammates on one game
        }
        select.appendChild(option);
      });
      select.value = player.assigned_game || "";
      select.onchange = function () {
        if (!select.value) return;
        sendAction({
          action: "assign_game", target_id: player.id, game_id: select.value,
        });
      };
      row.appendChild(select);
      rows.appendChild(row);
    });
  }

  function findPlayer(state, playerId) {
    var found = null;
    state.unassigned.forEach(function (p) { if (p.id === playerId) found = p; });
    ["alpha", "bravo"].forEach(function (teamId) {
      (state.teams[teamId].players || []).forEach(function (p) {
        if (p.id === playerId) found = p;
      });
    });
    return found;
  }

  // Client-side mirror of engine.start_blocker, for the host button copy.
  function startBlocker(state) {
    if (state.unassigned.length) {
      var names = state.unassigned.map(function (p) { return p.name; }).join(", ");
      return "Everyone needs a team — waiting on " + names + ".";
    }
    var blocker = null;
    ["alpha", "bravo"].forEach(function (teamId) {
      if (blocker) return;
      var team = state.teams[teamId];
      var leader = null;
      var playing = [];
      team.players.forEach(function (p) {
        if (p.is_leader) leader = p;
        else playing.push(p);
      });
      if (!leader) {
        blocker = "Team " + team.name + " needs a Grandmaster.";
      } else if (playing.length < state.min_players) {
        blocker = "Team " + team.name + " needs " + state.min_players +
          " player(s) besides the Grandmaster.";
      } else {
        playing.forEach(function (p) {
          if (!p.role && !blocker) {
            blocker = team.name + "'s Grandmaster still needs to assign a role to " +
              p.name + ".";
          }
        });
        playing.forEach(function (p) {
          // `has_game`, not `assigned_game`: the other team's pick is masked
          // in the lobby, but whether they have one is public.
          if (p.role && !p.has_game && !blocker) {
            blocker = team.name + "'s Grandmaster still needs to assign a game to " +
              p.name + ".";
          }
        });
        // Required roles: the bomb is the game no team opts out of.
        requiredRoles().forEach(function (roleId) {
          if (blocker) return;
          var roleName = serverConfig.roles[roleId].name;
          var holders = playing.filter(function (p) { return p.role === roleId; });
          if (holders.length > 1) {
            blocker = "Team " + team.name + " can only field one " + roleName + ".";
          } else if (!holders.length) {
            blocker = playing.length < 2 && duelistsOf(team).length
              ? "Team " + team.name + " needs a " + roleName + ", but its only " +
                "player is a Duelist — drop the Duelist or add a player."
              : "Team " + team.name + " needs a " + roleName + ".";
          }
        });
        if (!blocker && duelistsOf(team).length > 1) {
          blocker = "Team " + team.name + " can only field one Duelist.";
        }
      }
    });
    if (blocker) return blocker;
    // Mirrored role: a duel needs two seats, so one champion forces another.
    var fielding = ["alpha", "bravo"].filter(function (teamId) {
      return duelistsOf(state.teams[teamId]).length > 0;
    });
    if (fielding.length === 1) {
      var other = fielding[0] === "alpha" ? "bravo" : "alpha";
      return "Team " + state.teams[fielding[0]].name + " has a Duelist — team " +
        state.teams[other].name + " needs one too.";
    }
    return null;
  }

  function duelistsOf(team) {
    return (team.players || []).filter(function (p) {
      return !p.is_leader && p.role === "duelist";
    });
  }

  // Both read the catalogue rather than naming roles, so a new fixed or
  // required role needs no client change (mirror of backend/config.py).
  function roleIsFixed(roleId) {
    var roles = (serverConfig && serverConfig.roles) || {};
    return !!(roleId && roles[roleId] && roles[roleId].fixed);
  }

  function requiredRoles() {
    var roles = (serverConfig && serverConfig.roles) || {};
    var library = (serverConfig && serverConfig.library) || [];
    return Object.keys(roles).filter(function (roleId) {
      if (!roles[roleId].required) return false;
      // Only a gate if this server actually ships the role's game, matching
      // RelayEngine._required_roles.
      var games = roles[roleId].games || [];
      return !games.length || games.some(function (gameId) {
        return library.some(function (entry) { return entry.id === gameId; });
      });
    });
  }

  $("copy-link") && $("copy-link").addEventListener("click", function () {
    var link = window.location.origin + "/play?match=" + (lastState ? lastState.id : "");
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(link).then(function () { toast("Invite link copied!"); });
    } else {
      window.prompt("Copy the invite link:", link);
    }
  });

  // --- play view (players: just your game and your level) ---

  function renderPlay(state) {
    show("view-play");
    renderStrip(state);
    renderMe(state);
    renderFeed(state.events, "event-feed");
  }

  // Games that keep their own design until theirs lands. Their boards sit on a
  // light plate inside the dark frame rather than half-restyled on it.
  var LEGACY_BOARDS = { bomb_defuse: true };

  // The bar: who you are, which side, and how far the team has left to run.
  // Not who has cleared — a playing member is not sent that, on purpose
  // (docs/WEBSOCKET_PROTOCOL.md, "visibility is Grandmaster-exclusive").
  function renderStrip(state) {
    var me = state.me;
    var team = me && me.team_id ? state.teams[me.team_id] : null;
    var who = $("play-identity");
    who.innerHTML = "";
    if (!team) return;
    var color = teamColor(team.id);
    who.style.setProperty("--team-color", color);
    who.appendChild(avatarNode(state, me, team.id));

    var box = el("div");
    box.appendChild(el("div", "pl-who__name", me.name));
    var tags = el("div", "pl-tags");
    tags.appendChild(el("span", "pl-tag pl-tag--team", team.name));
    var duelling = !!(state.duel && state.duel.you);
    var roleCls = "pl-tag pl-tag--role" +
      (me.role === "duelist" || duelling ? " is-duelist" : "") +
      (me.role === "defuser" ? " is-defuser" : "");
    tags.appendChild(el("span", roleCls, me.role ? roleName(me.role) : "Player"));
    box.appendChild(tags);
    who.appendChild(box);

    // The host sets the match length in the lobby, so the ceiling is on the
    // match and not on the server default.
    var levels = state.level_count || (state.config && state.config.level_count);
    $("play-level-fill").style.width =
      (levels ? Math.min(100, (team.level / levels) * 100) : 0) + "%";
    $("play-level-count").textContent =
      team.level + (levels ? " / " + levels : "");

    renderSeat(state, me, duelling);
    renderEarnings(state, me);
  }

  // What you were handed and what it means for the board in front of you.
  function renderSeat(state, me, duelling) {
    var host = $("play-role");
    host.innerHTML = "";
    host.appendChild(el("div", "pl-seat__role",
      me.role ? roleName(me.role) : "Player"));

    var game = el("div", "pl-seat__game");
    if (duelling) {
      game.appendChild(icon("duel", "gm-ic--sm"));
      game.appendChild(el("span", null, "The server picks your duel"));
    } else {
      game.appendChild(gameIcon(me.assigned_game));
      game.appendChild(el("span", null, gameName(me.assigned_game)));
    }
    host.appendChild(game);

    host.appendChild(el("p", "pl-seat__note", duelling
      ? "You never solve a puzzle. Win your duels and your team advances."
      : "Your Grandmaster picked this seat for you. Clear the board to turn "
        + "green for the team."));
  }

  // The board in front of you is worth this much to the purse. Straight off
  // the match's own currency config, so a host who retunes it is not
  // contradicted here.
  function renderEarnings(state, me) {
    var host = $("play-earnings");
    host.innerHTML = "";
    var conf = state.config || {};
    var box = el("div", "pl-pay");
    var row = function (amount, what, live) {
      var line = el("div", "pl-pay__row" + (live ? " is-live" : ""));
      var coin = el("span", "pl-pay__coin");
      coin.appendChild(icon("coin", "gm-ic--sm"));
      coin.appendChild(el("span", null, String(amount)));
      line.appendChild(coin);
      line.appendChild(el("span", "pl-pay__what", what));
      box.appendChild(line);
    };
    if (me.role === "duelist") {
      // A Duelist banks duel wins, never clears, and the payout doubles on a
      // streak up to the cap.
      row(conf.duel_win_currency, "for winning a duel round", true);
      if (conf.duel_currency_cap) {
        row(conf.duel_currency_cap, "the most one streak can pay");
      }
    } else {
      row(conf.currency_per_clear, "for clearing this level",
        me.status === "solving");
      row(conf.currency_bonus_first, "for your first bonus this level",
        me.status === "bonus");
      row(conf.currency_bonus_repeat, "for each bonus after it");
    }
    host.appendChild(box);
  }

  function renderMe(state) {
    var me = state.me;
    if (!me) return;
    var puzzle = me.current_puzzle;
    // A Duelist's green comes from the duel card, not a cleared puzzle — the
    // "Level cleared!" rest card would be a lie for them.
    var duelling = !!(state.duel && state.duel.you);
    $("cleared-card").hidden = me.status !== "cleared" || duelling;
    $("choice-overlay").hidden = !(me.status === "cleared" && me.choice_pending);
    $("bonus-badge").hidden = me.status !== "bonus";
    $("puzzle-card").hidden = !puzzle;
    if (puzzle) {
      $("play-game-name").textContent = gameName(me.assigned_game);
      $("puzzle-prompt").textContent = puzzle.prompt;
      // The bomb board brings its own look until its design lands, so it gets
      // a plate to sit on instead of the dark ground the others are drawn for.
      $("puzzle-mount").className =
        "pl-mount" + (LEGACY_BOARDS[me.assigned_game] ? " is-legacy" : "");
      mountPuzzle(puzzle);
    } else {
      unmountPuzzle();
    }
    // A solving player holds no wait timer, so the bar is free for the board's
    // own deadline where the game asks for one (docs/GAME_MODULE_SPEC.md).
    if (me.status === "solving" && me.puzzle_deadline) {
      startCountdown(me.puzzle_deadline, "puzzle", puzzle);
    } else {
      startCountdown(me.timer_deadline, me.status);
    }
    renderFrozen(me.frozen_until);
    flushHeldSubmit(me);
    renderScreenEffects(me.screen_effects);
    renderDuel(state, "duel-card", "duel-mount");
  }

  // --- duels ---
  //
  // A duel is one long-lived object that changes phase under the same id, so
  // renderers get update() as well as mount(). Only the two Duelists and the
  // two Grandmasters are sent `state.duel` at all; everyone else sees null.
  function renderDuel(state, cardId, mountId) {
    var duel = state.duel;
    var card = $(cardId);
    if (!card) return;
    if (!duel) {
      card.hidden = true;
      unmountDuel();
      startDuelCountdown(null);
      return;
    }
    card.hidden = false;
    var renderer = window.RelayDuels[duel.duel_game_id] ||
      window.RelayDuels.fallback;
    if (!mountedDuel || mountedDuel.duelId !== duel.id ||
        mountedDuel.mountId !== mountId) {
      unmountDuel();
      renderer.mount($(mountId), duel, {
        choose: function (move, duelId, round) {
          send({
            type: "duel_choice",
            duel_id: duelId,
            round: round,
            choice: String(move),
          });
        },
      });
      mountedDuel = { duelId: duel.id, renderer: renderer, mountId: mountId };
    } else {
      renderer.update(duel);
    }
    var title = $("duel-title");
    if (title) {
      title.textContent = "⚔️ " + (duel.name || "Duel") + " — round " + duel.round;
    }
    if (cardId === "leader-duel-card") {
      $("leader-duel-title").textContent =
        (duel.name || "Duel") + ", round " + duel.round;
    }
    // Only the choice window is a race; the reveal beat needs no pressure bar.
    startDuelCountdown(duel.phase === "choosing" ? duel.deadline : null, duel);
  }

  function unmountDuel() {
    if (mountedDuel) {
      mountedDuel.renderer.unmount();
      mountedDuel = null;
    }
  }

  // The shell owns the duel countdown, so every duel game gets the same one
  // (docs/DUEL_MODULE_SPEC.md §7). Both the bar and the seconds run off the
  // server's deadline, and off `round_seconds` — the window this match is
  // actually running, which is the host's override when they set one, not the
  // module default sitting in `payload`.
  var DUEL_CLOCK_IDS = ["duel-clock", "leader-duel-clock"];

  function eachDuelClock(fn) {
    DUEL_CLOCK_IDS.forEach(function (id) {
      var node = $(id);
      if (node) fn(node);
    });
  }

  function startDuelCountdown(deadlineIso, duel) {
    clearInterval(duelTimerHandle);
    var bar = $("duel-timer-bar");
    if (!bar) return;
    if (!deadlineIso) {
      bar.hidden = true;
      eachDuelClock(function (node) { node.hidden = true; });
      return;
    }
    var deadline = parseDeadline(deadlineIso);
    var seconds = (duel && duel.round_seconds) ||
      (duel && duel.payload && duel.payload.choice_seconds) || 5;
    var total = seconds * 1000;
    bar.hidden = false;
    eachDuelClock(function (node) { node.hidden = false; });
    var tick = function () {
      var left = Math.max(0, deadline - Date.now());
      $("duel-timer-fill").style.width = Math.min(100, (left / total) * 100) + "%";
      // Round up, so a clock never reads 0 while the round is still open.
      var showing = Math.ceil(left / 1000);
      eachDuelClock(function (node) {
        node.textContent = showing + "s";
        node.className = "duel-clock" + (left <= 3000 ? " urgent" : "");
      });
      if (left <= 0) clearInterval(duelTimerHandle);
    };
    tick();
    duelTimerHandle = setInterval(tick, 100);
  }

  function duelToast(message) {
    var mine = lastState && lastState.me && lastState.me.team_id;
    var won = mine && message.winner_team_id === mine;
    if (!mine) return;
    if (won) {
      toast("⚔️ " + message.winner_name + " won the duel! +" +
        message.currency + " 🪙" +
        (message.streak > 1 ? " (streak ×" + message.streak + ")" : ""));
    } else {
      toast("⚔️ " + message.loser_name + " lost the duel to " +
        message.winner_name + "." +
        (message.penalty_until ? " Your team is locked for a moment." : ""));
    }
  }

  // Mount by game_id from window.RelayGames; unmount the old first.
  function mountPuzzle(puzzle) {
    if (mounted && mounted.puzzleId === puzzle.id) {
      // Same instance — but not necessarily the same *board*: a deadline can
      // move under a live board (a Freeze pushes it out), and a renderer that
      // draws its own clock has to be told. Optional, so a renderer without
      // one is unaffected.
      if (mounted.renderer.update) mounted.renderer.update(puzzle);
      return;
    }
    unmountPuzzle();
    var renderer = window.RelayGames[puzzle.game_id] || window.RelayGames.fallback;
    var api = {
      submit: function (answer) {
        var message = {
          type: "submit_answer",
          puzzle_id: puzzle.id,
          answer: String(answer),
        };
        // A freeze makes the server refuse submits, and some games — the bomb —
        // submit exactly once, at the end. Sending it now would have the answer
        // thrown away with no way to send it again, so hold it until the freeze
        // lifts (`flushHeldSubmit`, on the next snapshot).
        if (frozenNow()) { heldSubmit = message; return; }
        send(message);
      },
      setReady: function () {},
    };
    renderer.mount($("puzzle-mount"), puzzle, api);
    mounted = { puzzleId: puzzle.id, renderer: renderer };
  }

  function frozenNow() {
    var until = lastState && lastState.me && lastState.me.frozen_until;
    return !!until && parseDeadline(until) > Date.now();
  }

  // Send a held answer once the freeze lapses — but only while the board it
  // answers is still the one being served. A board that has moved on (a
  // Scramble, a lapsed deadline) makes it stale, and the server would refuse
  // it anyway.
  function flushHeldSubmit(me) {
    if (!heldSubmit) return;
    var puzzle = me && me.current_puzzle;
    if (!puzzle || puzzle.id !== heldSubmit.puzzle_id) { heldSubmit = null; return; }
    if (frozenNow()) return;
    var message = heldSubmit;
    heldSubmit = null;
    send(message);
  }

  function unmountPuzzle() {
    if (mounted) {
      mounted.renderer.unmount();
      mounted = null;
    }
    heldSubmit = null;
    $("puzzle-mount").innerHTML = "";
  }

  // Countdown driven by a server deadline; the server stays authoritative in
  // every case — the bar says what is left, it never decides anything.
  // One clock, three meanings. The kind sits above the digits rather than in
  // front of them, so the number itself is the same size and in the same place
  // whichever deadline is running — you learn where to look once.
  var COUNTDOWN_KINDS = {
    bonus: "Bonus deadline",
    puzzle: "Board deadline",
    cleared: "Holding cleared"
  };

  function startCountdown(deadlineIso, status, puzzle) {
    clearInterval(timerHandle);
    var bar = $("timer-bar"), label = $("timer-label");
    var clock = $("play-clock");
    if (!deadlineIso) {
      bar.hidden = true;
      label.hidden = true;
      clock.hidden = true;
      clock.classList.remove("is-urgent");
      return;
    }
    var deadline = parseDeadline(deadlineIso);
    var total = countdownSeconds(status, puzzle) * 1000;
    bar.hidden = false;
    label.hidden = false;
    clock.hidden = false;
    $("play-clock-label").textContent =
      COUNTDOWN_KINDS[status] || COUNTDOWN_KINDS.cleared;
    var tick = function () {
      var left = Math.max(0, deadline - Date.now());
      var whole = Math.ceil(left / 1000);
      $("timer-fill").style.width = Math.min(100, (left / total) * 100) + "%";
      label.textContent = ("0" + Math.floor(whole / 60)).slice(-2) + ":" +
        ("0" + (whole % 60)).slice(-2);
      clock.classList.toggle("is-urgent", whole <= 15);
      if (left <= 0) {
        $("play-clock-label").textContent = "Waiting for the server";
        clearInterval(timerHandle);
      }
    };
    tick();
    timerHandle = setInterval(tick, 250);
  }

  // How long the bar's full width represents. A board deadline spans the
  // game's own limit; everything else spans the wait. The grace the server
  // adds on top is deliberately not counted — it is not the player's time.
  function countdownSeconds(status, puzzle) {
    if (status === "puzzle") {
      var limit = puzzle && puzzle.payload && puzzle.payload.time_limit_seconds;
      if (limit) return limit;
    }
    return (lastState && lastState.config.wait_seconds) ||
      (serverConfig && serverConfig.wait_seconds) || 180;
  }

  function renderFrozen(frozenIso) {
    clearInterval(frozenHandle);
    var overlay = $("frozen-overlay");
    if (!frozenIso) { overlay.hidden = true; return; }
    var until = parseDeadline(frozenIso);
    var tick = function () {
      var left = until - Date.now();
      if (left <= 0) {
        overlay.hidden = true;
        clearInterval(frozenHandle);
        return;
      }
      overlay.hidden = false;
      $("frozen-text").textContent = "🧊 Frozen for " + Math.ceil(left / 1000) + "s!";
    };
    tick();
    frozenHandle = setInterval(tick, 250);
  }

  // Cosmetic sabotage (the wobble/static/mirror/blackout perks). The server
  // sends `{effect: deadline}` and only ever to the victim; the class goes on
  // the puzzle card alone, so the countdown and currency stay readable and the
  // body-level frozen overlay stays put (a transformed ancestor would capture
  // its fixed positioning). CSS handles prefers-reduced-motion.
  var SCREEN_EFFECTS = ["wobble", "static", "mirror", "blackout"];

  function renderScreenEffects(effects) {
    clearInterval(effectsHandle);
    var card = $("puzzle-card");
    if (!card) return;
    var deadlines = effects || {};
    var tick = function () {
      var live = 0;
      SCREEN_EFFECTS.forEach(function (effect) {
        var iso = deadlines[effect];
        var on = !!iso && parseDeadline(iso) > Date.now();
        card.classList.toggle("fx-" + effect, on);
        if (on) live += 1;
      });
      if (!live) clearInterval(effectsHandle);
    };
    tick();
    // Deadline-driven, so a reconnect mid-effect resumes with the time left and
    // a backgrounded tab corrects itself on the next tick.
    effectsHandle = setInterval(tick, 200);
  }

  function clearScreenEffects() {
    clearInterval(effectsHandle);
    var card = $("puzzle-card");
    if (!card) return;
    SCREEN_EFFECTS.forEach(function (effect) {
      card.classList.remove("fx-" + effect);
    });
  }

  // --- leader dashboard ---

  // The Grandmaster's bomb console: the Expert's half of BOMB DEFUSE
  // (docs/GAME_DESIGN.md §2c). It is the manual and nothing else — no board, no
  // fuse, no bay progress — so there is nothing to keep in sync and the whole
  // thing is a page turner over frontend/games/bomb_manual.js. The Defuser says
  // what they are looking at; this says what it means.
  var bombConsole = { page: "home", mounted: false };

  // On a dark-fuse board the Defuser's face shows no number and runs no fuse of
  // its own; the server sends the deadline here instead. This is the console's
  // first live element and a deliberate departure from "the manual and nothing
  // else" — it is still not board state, just the one clock, on the one seat
  // allowed to read it out.
  function renderConsoleClock(defuser) {
    clearInterval(bombClockHandle);
    var box = $("leader-bomb-clock");
    var iso = defuser && defuser.board_deadline;
    if (!iso) { box.hidden = true; return; }
    var deadline = parseDeadline(iso);
    box.hidden = false;
    // The chip is a clock, so it reads as one. The instruction that came with
    // it — that this number reaches nobody else — moves to the title, where it
    // is still there for the asking and no longer fighting the digits.
    box.title = "Call it out: the Defuser cannot see this.";
    var tick = function () {
      var left = Math.max(0, deadline - Date.now());
      var whole = Math.max(0, Math.ceil(left / 1000));
      box.textContent = ("0" + Math.floor(whole / 60)).slice(-2) + ":" +
        ("0" + (whole % 60)).slice(-2);
      box.classList.toggle("is-urgent", whole <= 15);
      if (left <= 0) {
        box.title = "Out of time. The board is gone.";
        clearInterval(bombClockHandle);
      }
    };
    tick();
    bombClockHandle = setInterval(tick, 250);
  }

  function hideConsoleClock() {
    clearInterval(bombClockHandle);
    var box = $("leader-bomb-clock");
    if (box) {
      box.hidden = true;
      box.textContent = "";
      box.title = "";
      box.classList.remove("is-urgent");
    }
  }

  function renderBombConsole(state, team) {
    var card = $("leader-bomb-card");
    var manual = window.RelayBombManual;
    var defuser = null;
    (team.players || []).forEach(function (player) {
      if (!player.is_leader && player.assigned_game === "bomb_defuse") {
        defuser = player;
      }
    });
    // Only while the team actually fields one, and only once the match is on:
    // in the lobby there is no bomb to read for.
    if (!manual || !defuser || state.status !== "active") {
      if (bombConsole.mounted) teardownBombConsole();
      hideConsoleClock();
      card.hidden = true;
      return;
    }
    // Silence jams the manual too. A silenced Grandmaster already loses the
    // roster and the who-cleared feed; leaving them the one page that still
    // helps would make the perk a half-measure, and the Defuser can hear the
    // difference. The card stays — a console that vanished would read as a
    // bug rather than as the attack it is.
    if (team.silenced_until && parseDeadline(team.silenced_until) > Date.now()) {
      if (bombConsole.mounted) {
        // Keep the page. This lifts in seconds and the Defuser will still be
        // stood in front of the same bay when it does.
        var openPage = bombConsole.page;
        teardownBombConsole();
        bombConsole.page = openPage;
      }
      card.hidden = false;
      hideConsoleClock();     // the server already nulls it; this clears the tick
      $("leader-bomb-sub").textContent =
        "Silenced. " + defuser.name +
        " is on the bomb without you until it clears.";
      var jammed = $("leader-bomb-mount");
      // Replace the manual outright rather than covering it: nothing stale is
      // left underneath for the silence to leak.
      jammed.innerHTML = "";
      var pill = document.createElement("div");
      pill.className = "gm-jammed";
      var warn = document.createElement("i");
      warn.className = "gm-ic gm-ic--warning";
      warn.setAttribute("aria-hidden", "true");
      pill.appendChild(warn);
      pill.appendChild(el("span", null, "Signal jammed. Manual unavailable."));
      jammed.appendChild(pill);
      return;
    }
    card.hidden = false;
    $("leader-bomb-sub").textContent =
      defuser.name + " is on the bomb" + (defuser.connected ? "" : " (offline)") +
      " and cannot see this. They describe the bay; you read them the rule." +
      (defuser.board_deadline ? " Their timer is dark — you are the clock." : "");
    // Before the redraw guard: the manual holds its page across snapshots, the
    // clock has to follow every one of them.
    renderConsoleClock(defuser);

    // Re-rendering on every snapshot would fight the page you are reading, so
    // the console redraws only when its own page changes.
    if (bombConsole.mounted) return;
    var mount = $("leader-bomb-mount");
    mount.innerHTML = "";
    var frame = document.createElement("div");
    frame.style.cssText = "position:absolute;inset:0;overflow:hidden;";
    var surface = document.createElement("div");
    surface.style.cssText = "position:absolute;left:0;top:0;width:" + manual.W +
      "px;height:" + manual.H + "px;transform-origin:top left;";
    frame.appendChild(surface);
    mount.appendChild(frame);
    bombConsole.mounted = true;

    // The manual's own home page is one of the stops, so the rail and the
    // arrows walk the same list the page selector does. Read from the module's
    // PAGES rather than a copy here: a manual page added there appears in the
    // rail with no change to this file.
    var stops = ["home"].concat(manual.PAGES);

    function stopName(page) {
      return page === "home" ? "Contents" : (manual.MODULE_NAMES[page] || page);
    }

    function drawRail() {
      var rail = $("leader-bomb-pages");
      rail.innerHTML = "";
      stops.forEach(function (page) {
        var open = page === bombConsole.page;
        var tab = el("button", "gm-page-tab" + (open ? " is-open" : ""),
          stopName(page));
        tab.type = "button";
        tab.setAttribute("role", "tab");
        tab.setAttribute("aria-selected", open ? "true" : "false");
        tab.addEventListener("click", function () {
          bombConsole.page = page;
          draw();
        });
        rail.appendChild(tab);
      });
      var at = stops.indexOf(bombConsole.page);
      $("leader-bomb-count").textContent = (at + 1) + " / " + stops.length;
      $("leader-bomb-prev").disabled = at <= 0;
      $("leader-bomb-next").disabled = at >= stops.length - 1;
    }

    function step(by) {
      var at = stops.indexOf(bombConsole.page) + by;
      if (at < 0 || at >= stops.length) return;
      bombConsole.page = stops[at];
      draw();
    }

    $("leader-bomb-prev").onclick = function () { step(-1); };
    $("leader-bomb-next").onclick = function () { step(1); };

    // Fit both ways. The manual draws at a fixed 590x440 and the console now
    // lives in a fold with a ceiling, so scaling on width alone would push the
    // page out through the bottom of its own panel.
    function scaleConsole() {
      var availW = mount.clientWidth || manual.W;
      var availH = mount.clientHeight || manual.H;
      var scale = Math.max(0.5,
        Math.min(availW / manual.W, availH / manual.H, 1.4));
      surface.style.transform = "scale(" + scale + ")";
      surface.style.left = Math.max(0, (availW - manual.W * scale) / 2) + "px";
      surface.style.top = Math.max(0, (availH - manual.H * scale) / 2) + "px";
    }

    function draw() {
      manual.render(surface, {
        page: bombConsole.page,
        axis: "column",
        homeNote: "Your Defuser can flip to this too, but it costs them fuse. " +
          "Keep the page they need open and they never have to look away.",
        onNavigate: function (page) { bombConsole.page = page; draw(); },
        // Exit on the console's home page is a no-op: there is no bomb behind
        // it to go back to, and the card is not dismissible.
        onExit: function () { bombConsole.page = "home"; draw(); }
      });
      drawRail();
      scaleConsole();
    }
    draw();
    if (!bombConsole.resizeHandler) {
      bombConsole.resizeHandler = function () { scaleConsole(); };
      window.addEventListener("resize", bombConsole.resizeHandler);
    }
    // The window is not the only thing that resizes this. The console is a flex
    // child of a deck sized to the viewport: its height settles *after* this
    // mount and changes again every time the deck reflows — a duel starting, a
    // roster row arriving — with no window event at all. Measuring once is how
    // the manual ends up drawn at full size inside a short panel.
    if (window.ResizeObserver && !bombConsole.observer) {
      bombConsole.observer = new window.ResizeObserver(function () {
        scaleConsole();
      });
      bombConsole.observer.observe(mount);
    }
  }

  function teardownBombConsole() {
    if (bombConsole.resizeHandler) {
      window.removeEventListener("resize", bombConsole.resizeHandler);
      bombConsole.resizeHandler = null;
    }
    if (bombConsole.observer) {
      bombConsole.observer.disconnect();
      bombConsole.observer = null;
    }
    bombConsole.mounted = false;
    bombConsole.page = "home";
    hideConsoleClock();
    var mount = $("leader-bomb-mount");
    if (mount) mount.innerHTML = "";
    var rail = $("leader-bomb-pages");
    if (rail) rail.innerHTML = "";
    $("leader-bomb-count").textContent = "";
    $("leader-bomb-card").hidden = true;
  }

  // --- Grandmaster dashboard helpers ---

  var TEAM_COLORS = { alpha: "#ff5d5d", bravo: "#2ec4b6" };

  function teamColor(teamId) { return TEAM_COLORS[teamId] || "#7a8cff"; }

  var TEAM_LOGOS = ["knight", "rook", "bishop", "queen",
                    "bow", "skull", "campfire", "tower"];

  // A team's mark is drawn from the match id, so the pairing changes between
  // matches. Hashing each team separately would let both land on the same
  // silhouette, so the second team is stepped away from the first by a stride
  // that can never be a multiple of the list length: two teams always differ.
  // Presentation only; no logo is stored on the team.
  function teamLogo(state, teamId) {
    var seats = TEAM_COLORS.hasOwnProperty(teamId)
      ? Object.keys(TEAM_COLORS) : [teamId];
    var seat = seats.indexOf(teamId);
    if (seat < 0) seat = 0;
    var base = hashSeed(state.id) % TEAM_LOGOS.length;
    var stride = 1 + (hashSeed(state.id + "#") % (TEAM_LOGOS.length - 1));
    return TEAM_LOGOS[(base + seat * stride) % TEAM_LOGOS.length];
  }

  // Who has actually been paying for the perks. The figure is the player's net
  // contribution, so it moves with the purse: a gambler who loses a bonus gives
  // the coins back here too, and the column can never claim credit for money
  // the team no longer has.
  function renderCoinBoard(state, team) {
    var host = $("leader-coins");
    host.innerHTML = "";
    var playing = team.players.filter(function (p) { return !p.is_leader; });

    // Silence nulls the ledger along with the rest of the progress read-out.
    var blinded = playing.some(function (p) {
      return p.coins_earned === null || p.coins_earned === undefined;
    });
    if (blinded) {
      var jammed = el("li", "gm-jammed");
      jammed.appendChild(icon("warning"));
      jammed.appendChild(el("span", null, "Signal jammed. Earnings unavailable."));
      host.appendChild(jammed);
      return;
    }

    var ranked = playing.slice().sort(function (a, b) {
      return b.coins_earned - a.coins_earned;
    });
    var best = ranked.length ? ranked[0].coins_earned : 0;

    ranked.forEach(function (player, at) {
      var row = el("li", "gm-coin-row" + (at === 0 && best > 0 ? " is-top" : ""));
      row.appendChild(el("span", "gm-coin-rank", String(at + 1)));
      row.appendChild(avatarNode(state, player, team.id));
      row.appendChild(el("span", "gm-coin-name", player.name));

      // A bar makes the spread readable without reading five numbers.
      var meter = el("span", "gm-coin-meter");
      var fill = el("span", "gm-coin-meter__fill");
      fill.style.width = (best > 0 ? (player.coins_earned / best) * 100 : 0) + "%";
      meter.appendChild(fill);
      row.appendChild(meter);

      var value = el("span", "gm-coin-value");
      value.appendChild(icon("coin", "gm-ic--sm"));
      value.appendChild(el("span", null, String(player.coins_earned)));
      row.appendChild(value);
      host.appendChild(row);
    });
  }

  // The level race: one star per level of the configured match length, filled
  // to each team's level. The two rows side by side are the gap.
  function renderRace(state, mine, opponent, levels) {
    var host = $("leader-race");
    host.innerHTML = "";
    if (!levels) { $("leader-race-gap").textContent = ""; return; }

    [mine, opponent].forEach(function (team, at) {
      if (!team) return;
      var row = el("div", "gm-race__row" + (at === 0 ? " is-mine" : ""));
      row.style.setProperty("--team-color", teamColor(team.id));

      var logo = el("span", "gm-race__logo");
      logo.appendChild(icon("logo-" + teamLogo(state, team.id)));
      row.appendChild(logo);
      row.appendChild(el("span", "gm-race__name", team.name));

      var stars = el("ol", "gm-race__stars");
      stars.setAttribute("aria-label",
        team.name + " on level " + team.level + " of " + levels);
      for (var level = 1; level <= levels; level++) {
        var star = el("li", "gm-race__star" + (level <= team.level ? " is-on" : ""));
        star.appendChild(icon("star", "gm-ic--sm"));
        stars.appendChild(star);
      }
      row.appendChild(stars);
      row.appendChild(el("span", "gm-race__level", team.level + " / " + levels));
      host.appendChild(row);
    });

    var gap = $("leader-race-gap");
    if (!opponent) { gap.textContent = ""; return; }
    var lead = mine.level - opponent.level;
    // Never the colour alone: the gap is spelled out in words too.
    gap.textContent = lead === 0
      ? "Level for level"
      : (lead > 0 ? "You lead by " : opponent.name + " leads by ") +
        Math.abs(lead) + (Math.abs(lead) === 1 ? " level" : " levels");
    gap.className = "gm-panel__meta " +
      (lead > 0 ? "is-ahead" : lead < 0 ? "is-behind" : "");
  }

  function icon(name, extra) {
    var i = document.createElement("i");
    i.className = "gm-ic gm-ic--" + name + (extra ? " " + extra : "");
    i.setAttribute("aria-hidden", "true");
    return i;
  }

  // The mark for an assigned game. The id is the class, so a game registered
  // later needs a stylesheet rule and nothing here; until it has one the base
  // .gm-ic--game mask keeps the column aligned rather than painting a block.
  function gameIcon(gameId) {
    var slug = String(gameId || "").replace(/[^a-z0-9_]/gi, "");
    return icon("game", slug ? "gm-ic--game-" + slug + " gm-ic--sm" : "gm-ic--sm");
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function initials(name) {
    var parts = String(name || "?").trim().split(/\s+/);
    var first = parts[0] ? parts[0].charAt(0) : "?";
    var second = parts.length > 1 ? parts[parts.length - 1].charAt(0) : "";
    return (first + second).toUpperCase();
  }

  // Deterministic avatars without an avatar field on the player model and
  // without a network round trip. The seed is the match id plus the player id,
  // so a player keeps one face for a whole match, every client draws the same
  // one, and a blocked or offline box still shows it.
  function hashSeed(text) {
    var hash = 2166136261;
    for (var i = 0; i < text.length; i++) {
      hash ^= text.charCodeAt(i);
      hash = (hash * 16777619) >>> 0;
    }
    return hash;
  }

  // A tiny deterministic stream, so each feature of a face draws from its own
  // part of the seed instead of every avatar keying off the same low bits.
  function seedStream(seed) {
    var state = seed || 1;
    return function (n) {
      state ^= state << 13; state >>>= 0;
      state ^= state >> 17;
      state ^= state << 5; state >>>= 0;
      return state % n;
    };
  }

  var AVATAR_SKINS = ["#ffd9a8", "#f2b98c", "#d69a6a", "#a9713f", "#7a4f2b",
                      "#f7e2c8", "#c98c5a", "#8d5a34"];
  var AVATAR_BACKS = ["#2b3a7a", "#1f5b6b", "#5b2f7a", "#7a2f4d", "#2f6b45",
                      "#6b5a1f", "#3a3a6b", "#6b3a2f"];

  // Eyes and a mouth on a coloured ground: no hair, no body, nothing that
  // reads as a gender cue, and nothing derived from the player's name.
  function avatarSvg(seed) {
    var pick = seedStream(seed);
    var back = AVATAR_BACKS[pick(AVATAR_BACKS.length)];
    var skin = AVATAR_SKINS[pick(AVATAR_SKINS.length)];
    var eyeY = 7 + pick(2);
    var eyeW = 1 + pick(2);
    var browed = pick(3) === 0;
    var mouth = pick(4);
    var parts = [
      '<rect width="16" height="16" fill="' + back + '"/>',
      '<rect x="3" y="3" width="10" height="11" fill="' + skin + '"/>'
    ];
    if (browed) {
      parts.push('<rect x="4" y="' + (eyeY - 2) + '" width="3" height="1" fill="#2b2233"/>');
      parts.push('<rect x="9" y="' + (eyeY - 2) + '" width="3" height="1" fill="#2b2233"/>');
    }
    parts.push('<rect x="5" y="' + eyeY + '" width="' + eyeW + '" height="2" fill="#2b2233"/>');
    parts.push('<rect x="' + (11 - eyeW) + '" y="' + eyeY + '" width="' + eyeW +
      '" height="2" fill="#2b2233"/>');
    if (mouth === 0) parts.push('<rect x="6" y="11" width="4" height="1" fill="#2b2233"/>');
    else if (mouth === 1) parts.push('<rect x="6" y="11" width="4" height="2" fill="#2b2233"/>');
    else if (mouth === 2) {
      parts.push('<rect x="6" y="11" width="1" height="1" fill="#2b2233"/>');
      parts.push('<rect x="7" y="12" width="2" height="1" fill="#2b2233"/>');
      parts.push('<rect x="9" y="11" width="1" height="1" fill="#2b2233"/>');
    } else parts.push('<rect x="7" y="11" width="2" height="2" fill="#2b2233"/>');
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" ' +
      'shape-rendering="crispEdges" width="40" height="40">' + parts.join("") + "</svg>";
  }

  function avatarNode(state, player, teamId) {
    var wrap = el("span", "gm-avatar");
    wrap.style.setProperty("--team-color", teamColor(teamId));
    wrap.innerHTML = avatarSvg(hashSeed(state.id + ":" + player.id));
    wrap.setAttribute("role", "img");
    wrap.setAttribute("aria-label", "");
    return wrap;
  }

  // The two seats of a live duel, above whatever the duel game draws.
  //
  // Initials in the team's colour, not the seeded faces the roster uses: the
  // duel view sends the opponent's *name* and never their id, because an id is
  // a WS credential (models.DuelSession.public). Seeding a face from anything
  // else would invent a stranger here and show the other Grandmaster a
  // different one for the same player.
  function renderDuelSeats(state, duel, myTeamId) {
    var host = $("leader-duel-seats");
    if (!host) return;
    host.innerHTML = "";
    if (!duel) return;
    var names = duel.duellists || {};
    var teams = duel.team_of || {};
    // Your own champion on the left, the way the roster and the race read.
    var order = teams.b === myTeamId ? ["b", "a"] : ["a", "b"];
    order.forEach(function (side, at) {
      if (at) host.appendChild(el("span", "gm-seats__vs", "VS"));
      var teamId = teams[side];
      var team = state.teams[teamId];
      var seat = el("div", "gm-seat" + (teamId === myTeamId ? " is-mine" : ""));
      seat.style.setProperty("--team-color", teamColor(teamId));
      seat.appendChild(el("span", "gm-avatar gm-avatar--initials",
        initials(names[side] || "?")));
      seat.appendChild(el("span", "gm-seat__name", names[side] || "Empty seat"));
      seat.appendChild(el("span", "gm-seat__team", team ? team.name : teamId || ""));
      host.appendChild(seat);
    });
  }

  function statusPill(player) {
    // Silenced: the server nulls the progress fields rather than lying about
    // them, so there is genuinely nothing to show.
    if (player.status === "hidden") return ["?", "pill hidden-pill", null];
    if (player.green) {
      return player.role === "duelist"
        ? ["Duel won", "pill green", "duel"] : ["Cleared", "pill green", "cleared"];
    }
    if (player.status === "bonus") return ["Bonus", "pill bonus", null];
    if (player.status === "duelling") return ["Duelling", "pill duel", "duel"];
    if (player.status === "finished") return ["Done", "pill", null];
    return ["Solving", "pill", null];
  }

  // One command-bar chip. Only rendered when the fact is actually true, so the
  // bar stays quiet in a plain state and loud when something is happening.
  function flag(list, label, value, tone, iconName) {
    var li = el("li", "gm-flag" + (tone ? " gm-flag--" + tone : ""));
    if (iconName) li.appendChild(icon(iconName, "gm-ic--sm"));
    var box = el("span");
    box.appendChild(el("span", "gm-flag__label", label));
    box.appendChild(el("span", "gm-flag__value", value));
    li.appendChild(box);
    list.appendChild(li);
  }

  function renderLeader(state) {
    show("view-leader");
    var me = state.me;
    var team = state.teams[me.team_id];
    var color = teamColor(team.id);
    var locked = team.duel_penalty_until &&
      parseDeadline(team.duel_penalty_until) > Date.now();
    var silenced = team.silenced_until &&
      parseDeadline(team.silenced_until) > Date.now();

    // --- command bar ---
    $("leader-match-code").textContent = state.id;
    $("leader-team-badge").style.setProperty("--team-color", color);
    $("leader-team-title").textContent = team.name;
    // The host picks the match length in the lobby, so the real ceiling is on
    // the match, not the server default.
    var levels = state.level_count || (state.config && state.config.level_count);
    $("leader-level").textContent =
      "Level " + team.level + (levels ? " / " + levels : "");
    $("leader-currency").textContent = team.currency;
    $("leader-perk-purse").textContent = "You have " + team.currency;

    var flags = $("leader-status-line");
    flags.innerHTML = "";
    // Trust the snapshot over the local clock: the server nulls green_count
    // when it is blinding us, and if the two ever disagree the numbers are the
    // half that must not be invented.
    var blind = silenced || team.green_count === null ||
      team.green_count === undefined;
    // Every slot, every snapshot, in a fixed order. A bar that grew and shrank
    // moved the thing you were reading; one that dims in place does not, and
    // the Grandmaster learns where to look instead of scanning for it.
    if (blind) {
      flag(flags, "Cleared", "Silenced", "danger", "warning");
      $("leader-team-count").textContent = "? / ?";
    } else {
      flag(flags, "Cleared", team.green_count + " / " + team.roster_size,
        team.green_count >= team.roster_size ? "good" : null, "cleared");
      $("leader-team-count").textContent =
        team.green_count + " / " + team.roster_size + " cleared";
    }
    flag(flags, "Shield", team.shield_active ? "Active" : "None",
      team.shield_active ? "on" : "off", "shield");
    flag(flags, "Reflect", team.reflect_active ? "Active" : "None",
      team.reflect_active ? "on" : "off", "reflect");
    flag(flags, "Insurance", team.insurance_active ? "Active" : "None",
      team.insurance_active ? "on" : "off", "insurance");
    flag(flags, "Duel streak", team.duel_streak ? "x" + team.duel_streak : "None",
      team.duel_streak ? "good" : "off", "duel");
    flag(flags, "Duel penalty", locked ? "Cannot advance" : "None",
      locked ? "warn" : "off", "warning");

    watchSilence(team);
    renderDuel(state, "leader-duel-card", "leader-duel-mount");
    renderDuelSeats(state, state.duel, me.team_id);
    renderBombConsole(state, team);

    // --- roster ---
    var roster = $("leader-roster");
    roster.innerHTML = "";
    team.players.forEach(function (player) {
      if (player.is_leader) return;
      var row = el("li");
      if (!player.connected) row.className = "is-offline";

      row.appendChild(avatarNode(state, player, team.id));

      var who = el("div", "gm-who");
      var nameRow = el("div", "gm-who__name");
      nameRow.appendChild(el("span", null, player.name));
      if (!player.connected) {
        var off = icon("offline", "gm-ic--sm");
        off.removeAttribute("aria-hidden");
        off.setAttribute("role", "img");
        off.setAttribute("aria-label", "offline");
        nameRow.appendChild(off);
      }
      who.appendChild(nameRow);
      var roleCls = "gm-who__role" +
        (player.role === "duelist" ? " is-duelist" : "") +
        (player.role === "defuser" ? " is-defuser" : "");
      who.appendChild(el("div", roleCls, player.role ? roleName(player.role) : "Unassigned"));
      row.appendChild(who);

      var assign = el("div", "gm-assign");
      assign.appendChild(gameIcon(player.assigned_game));
      assign.appendChild(el("span", null, gameName(player.assigned_game)));
      row.appendChild(assign);

      var spec = statusPill(player);
      var pill = el("span", spec[1]);
      if (spec[2]) pill.appendChild(icon(spec[2], "gm-ic--sm"));
      pill.appendChild(el("span", null, spec[0]));
      row.appendChild(pill);

      roster.appendChild(row);
    });

    // --- opponent: only the four facts the snapshot exposes ---
    var opponentId = me.team_id === "alpha" ? "bravo" : "alpha";
    var opponent = state.teams[opponentId];
    var oppBox = $("leader-opponent");
    oppBox.innerHTML = "";
    if (opponent) {
      // A band in their colour rather than a boxed panel: it is a scoreline you
      // glance at, not a thing you work in.
      oppBox.style.setProperty("--team-color", teamColor(opponent.id));
      var mark = el("span", "gm-opp__logo");
      mark.appendChild(icon("logo-" + teamLogo(state, opponent.id)));
      oppBox.appendChild(mark);
      oppBox.appendChild(el("span", "gm-opp__label", "Opponent team"));
      oppBox.appendChild(el("span", "gm-opp__name", opponent.name));
      oppBox.appendChild(el("span", "gm-spacer"));
      var lvl = el("span", "gm-opp__stat");
      lvl.appendChild(el("strong", null, "Level " + opponent.level +
        (levels ? " / " + levels : "")));
      oppBox.appendChild(lvl);
      var cleared = el("span", "gm-opp__stat");
      cleared.appendChild(el("strong", null,
        opponent.green_count + " / " + opponent.roster_size));
      cleared.appendChild(el("span", null, " cleared"));
      oppBox.appendChild(cleared);
    }

    renderRace(state, team, opponent, levels);
    renderCoinBoard(state, team);
    renderPerkGrid(state, team);
    renderHandoff(state, team);
    renderFeed(state.events, "leader-feed");
  }

  // Silence is masked in the *view*, so nothing on the server fires when it
  // lapses. Ask for a fresh snapshot the moment it does, or the dashboard would
  // stay blind until the next unrelated broadcast.
  function watchSilence(team) {
    clearTimeout(silenceHandle);
    if (!team.silenced_until) return;
    var left = parseDeadline(team.silenced_until) - Date.now();
    if (left <= 0) return;
    silenceHandle = setTimeout(function () {
      send({ type: "request_state" });
    }, left + 250);
  }

  function renderPerkGrid(state, team) {
    var grid = $("perk-grid");
    grid.innerHTML = "";
    var perks = state.config.perks || {};
    // Which defences the team already has up, so an active one reads as active
    // rather than just another buyable card.
    var activeDefense = {
      shield: team.shield_active,
      reflect: team.reflect_active,
      insurance: team.insurance_active
    };

    // The catalogue is the source of truth: groups are derived from perk.kind,
    // so a perk added or removed in backend/config.py shows up here with no
    // change to this file.
    var groups = {};
    var order = [];
    Object.keys(perks).forEach(function (perkId) {
      var kind = perks[perkId].kind || "other";
      if (!groups[kind]) { groups[kind] = []; order.push(kind); }
      groups[kind].push(perkId);
    });
    // Attack first, defense second, anything the catalogue adds later after
    // them in whatever order it declared.
    var rank = { attack: 0, defense: 1 };
    order.sort(function (a, b) {
      var ra = rank[a] === undefined ? 2 : rank[a];
      var rb = rank[b] === undefined ? 2 : rank[b];
      return ra - rb;
    });

    order.forEach(function (kind) {
      var group = el("div", "gm-perks__group gm-perks__group--" + kind);
      var label = el("div", "gm-perks__label");
      label.appendChild(icon(kind === "defense" ? "shield" : "duel", "gm-ic--sm"));
      label.appendChild(el("span", null, kind));
      group.appendChild(label);

      var cards = el("div", "perk-grid");
      groups[kind].forEach(function (perkId) {
        cards.appendChild(perkCard(state, team, perkId, perks[perkId], activeDefense[perkId]));
      });
      group.appendChild(cards);
      grid.appendChild(group);
    });
  }

  function perkCard(state, team, perkId, perk, isActive) {
    var card = el("div", "perk-card " + (perk.kind || "") + (isActive ? " is-active" : ""));

    var title = el("div", "perk-name");
    // Files are hyphenated; perk ids are not.
    title.appendChild(icon("perk-" + perkId.replace(/_/g, "-")));
    title.appendChild(el("span", null, perk.name));
    card.appendChild(title);

    if (perk.desc) card.appendChild(el("div", "perk-desc", perk.desc));

    var target = null;
    if (perkId === "extend_wait") {
      target = document.createElement("select");
      target.className = "assign-select";
      target.setAttribute("aria-label", "Teammate to extend");
      var cleared = team.players.filter(function (p) { return p.green; });
      if (!cleared.length) {
        var none = document.createElement("option");
        none.value = "";
        none.textContent = "nobody cleared";
        target.appendChild(none);
      }
      cleared.forEach(function (p) {
        var option = document.createElement("option");
        option.value = p.id;
        option.textContent = p.name;
        target.appendChild(option);
      });
      card.appendChild(target);
    }

    // The price *is* the button. The card is the thing you read and the coin
    // chip along its foot is the thing you press, which is one control instead
    // of a chip sitting next to a button that says the same thing. The label
    // that carries the meaning moved to aria-label and title, so the shrunk
    // visible text costs a sighted reader a hover and a screen reader nothing.
    var buy = el("button", "gm-buy" + (isActive ? " is-on" : ""));
    if (isActive) {
      buy.appendChild(el("span", null, "Active"));
    } else {
      buy.appendChild(icon("coin", "gm-ic--sm"));
      buy.appendChild(el("span", null, String(perk.cost)));
    }
    var poor = team.currency < perk.cost;
    // extend_wait needs a live target; the rest are aimed by the server.
    var noTarget = target !== null && !target.value;
    buy.disabled = poor || isActive || noTarget;
    buy.setAttribute("aria-label",
      (isActive ? "Already active: " : "Buy ") + perk.name + " for " + perk.cost + " coins");
    if (poor) buy.title = "Not enough coins";
    else if (noTarget) buy.title = "No cleared teammate to target";
    else if (isActive) buy.title = perk.name + " is already active";
    else buy.title = "Buy " + perk.name + " for " + perk.cost + " coins";
    buy.addEventListener("click", function () {
      var message = { type: "buy_perk", perk_id: perkId };
      if (target && target.value) message.target_id = target.value;
      send(message);
    });
    card.appendChild(buy);
    return card;
  }

  function renderHandoff(state, team) {
    var select = $("handoff-select");
    select.innerHTML = "";
    team.players.forEach(function (player) {
      if (player.is_leader) return;
      var option = document.createElement("option");
      option.value = player.id;
      option.textContent = player.name +
        (player.role ? " · " + roleName(player.role) : "") +
        " (" + gameName(player.assigned_game) + ")" +
        (player.green ? " · cleared" : "");
      select.appendChild(option);
    });
    // The face beside the control, since a native <select> cannot carry one and
    // a custom listbox would trade working keyboard and screen-reader behaviour
    // for a picture.
    var face = $("handoff-avatar");
    var paintFace = function () {
      face.innerHTML = "";
      var pick = null;
      team.players.forEach(function (p) {
        if (!p.is_leader && p.id === select.value) pick = p;
      });
      if (!pick) return;
      face.style.setProperty("--team-color", teamColor(team.id));
      face.innerHTML = avatarSvg(hashSeed(state.id + ":" + pick.id));
    };
    select.onchange = paintFace;
    paintFace();

    var btn = $("handoff-btn");
    btn.disabled = !select.options.length;
    btn.onclick = function () {
      if (!select.value) return;
      var pick = select.options[select.selectedIndex].textContent;
      if (window.confirm("Hand the Grandmaster seat to " + pick +
          "? You take over their role and game; their cleared status is lost.")) {
        send({ type: "give_leader", target_id: select.value });
      }
    };
  }

  // --- events, overlays, result ---

  function renderFeed(events, feedId) {
    var feed = $(feedId);
    feed.innerHTML = "";
    events.slice(-5).reverse().forEach(function (event) {
      logEvent(event, false, feedId);
    });
  }

  // An icon and a tone per event kind. The kinds are the ones the engine
  // actually emits (`RelayEngine._add_event`); anything it adds later falls
  // through to the neutral row rather than vanishing.
  var EVENT_MARKS = {
    green: ["cleared", "good"],
    lost_green: ["warning", "warn"],
    advance: ["level", "on"],
    bonus: ["star", "bonus"],
    perk: ["duel", "danger"],
    join: ["team", null],
    win: ["crown", "bonus"],
    info: ["level", null]
  };

  // Wall-clock for the reader, from the server's stamp. Not "3 minutes ago":
  // a Grandmaster reads this feed against a bomb fuse and a duel clock, and a
  // relative label would be the only thing on the screen that is not a time.
  function eventTime(iso) {
    if (!iso) return "";
    var at = parseDeadline(iso);
    if (!at) return "";
    var when = new Date(at);
    var pad = function (n) { return ("0" + n).slice(-2); };
    return pad(when.getHours()) + ":" + pad(when.getMinutes()) + ":" +
      pad(when.getSeconds());
  }

  function logEvent(event, fresh, feedId) {
    if (!feedId) {
      feedId = lastState && lastState.me && lastState.me.is_leader
        ? "leader-feed" : "event-feed";
    }
    var feed = $(feedId);
    var item = document.createElement("li");
    // The player's feed is a quiet line under their board; only the command
    // board spends the room on a stamp and a mark.
    if (feedId === "leader-feed") {
      var mark = EVENT_MARKS[event.kind] || EVENT_MARKS.info;
      item.className = "gm-event" + (mark[1] ? " gm-event--" + mark[1] : "") +
        (fresh ? " fresh" : "");
      item.appendChild(el("span", "gm-event__time", eventTime(event.created_at)));
      item.appendChild(icon(mark[0], "gm-ic--sm"));
      item.appendChild(el("span", "gm-event__text", event.message));
    } else {
      if (fresh) item.className = "fresh";
      item.textContent = event.message;
    }
    feed.insertBefore(item, feed.firstChild);
    while (feed.children.length > 6) feed.removeChild(feed.lastChild);
  }

  function stageOverlay(text, tone) {
    var overlay = $("stage-overlay");
    $("stage-overlay-text").textContent = text;
    overlay.className = "stage-overlay" + (tone ? " is-" + tone : "");
    overlay.hidden = false;
    clearTimeout(overlayHandle);
    overlayHandle = setTimeout(function () { overlay.hidden = true; }, 1400);
  }

  function renderResult(state) {
    finished = true;
    unmountPuzzle();
    teardownBombConsole();
    clearInterval(timerHandle);
    clearInterval(frozenHandle);
    clearInterval(bombClockHandle);
    clearTimeout(silenceHandle);
    clearScreenEffects();
    $("choice-overlay").hidden = true;
    $("frozen-overlay").hidden = true;
    show("view-result");
    var view = $("view-result");
    var mine = state.me ? state.me.team_id : null;
    var myTeam = mine ? state.teams[mine] : null;
    var theirTeam = state.teams[mine === "alpha" ? "bravo" : "alpha"];
    var levels = state.level_count || (state.config && state.config.level_count) || 10;
    var decided = !!state.winner_team_id;
    var won = decided && state.winner_team_id === mine;

    view.className = "view" +
      (!decided ? " is-void" : won ? " is-win" : " is-loss");
    $("result-crest").innerHTML = decided ? (won ? CREST_WIN : CREST_LOSS) : "";

    if (!decided) {
      // The host stopped it. Nothing was decided, so nobody is told they lost.
      $("result-title").textContent = "Match ended";
      $("result-sub").textContent =
        "The host ended the session. No winner was recorded.";
    } else {
      $("result-title").textContent = won ? "Victory" : "Defeat";
      var champion = state.teams[state.winner_team_id];
      var ribbon = $("result-sub");
      ribbon.innerHTML = "";
      ribbon.style.setProperty("--team-color", teamColor(state.winner_team_id));
      ribbon.appendChild(el("strong", null, "Team " + champion.name));
      ribbon.appendChild(el("span", null, " cleared all " + levels +
        " levels first"));
    }

    renderResultLevels(state, myTeam, theirTeam, levels);
    renderResultTeam("result-team-mine", state, myTeam, state.winner_team_id);
    renderResultTeam("result-team-theirs", state, theirTeam, state.winner_team_id);

    $("result-table-title").textContent =
      (myTeam ? myTeam.name : "Your team") + " performance";
    $("result-opp-title").textContent =
      theirTeam ? theirTeam.name : "The other side";
    renderResultRoster("result-roster", state, myTeam, true);
    renderResultRoster("result-opp-roster", state, theirTeam, false);
    renderMvp(state, myTeam);
    renderRewards(state, myTeam);
    renderFeed(state.events, "result-feed");
  }

  // Hand-drawn, not an emoji: the crest is the loudest thing on the screen, and
  // an emoji would be whatever the reader's system font decided it was.
  var CREST_WIN =
    '<svg viewBox="0 0 120 76" fill="none" aria-hidden="true">' +
    '<path d="M14 66 L24 26 L44 44 L60 12 L76 44 L96 26 L106 66 Z" ' +
    'fill="currentColor" opacity="0.92"/>' +
    '<path d="M14 66 L24 26 L44 44 L60 12 L76 44 L96 26 L106 66 Z" ' +
    'stroke="currentColor" stroke-width="3" stroke-linejoin="round"/>' +
    '<rect x="14" y="66" width="92" height="7" rx="3" fill="currentColor"/>' +
    '<circle cx="60" cy="8" r="5" fill="currentColor"/>' +
    '<circle cx="24" cy="22" r="4" fill="currentColor"/>' +
    '<circle cx="96" cy="22" r="4" fill="currentColor"/>' +
    "</svg>";

  // The same crown, unfilled and struck through. A loss was the same race, not
  // a different one, so it is the same silhouette rather than a sad face.
  var CREST_LOSS =
    '<svg viewBox="0 0 120 76" fill="none" aria-hidden="true">' +
    '<path d="M14 66 L24 26 L44 44 L60 12 L76 44 L96 26 L106 66 Z" ' +
    'stroke="currentColor" stroke-width="3" stroke-linejoin="round" ' +
    'opacity="0.55"/>' +
    '<rect x="14" y="66" width="92" height="7" rx="3" fill="currentColor" ' +
    'opacity="0.55"/>' +
    '<path d="M52 30 L68 50 M68 30 L52 50" stroke="currentColor" ' +
    'stroke-width="4" stroke-linecap="round"/>' +
    "</svg>";

  // The scoreline the match actually kept. The Relay counts levels, not points,
  // so levels are what this prints — a score would be a number the game never
  // computed.
  function renderResultLevels(state, mine, theirs, levels) {
    var host = $("result-levels");
    host.innerHTML = "";
    [mine, theirs].forEach(function (team) {
      if (!team) return;
      if (host.children.length) {
        host.appendChild(el("span", "rs-levels__vs", "vs"));
      }
      var box = el("div", "rs-levels__box");
      box.style.setProperty("--team-color", teamColor(team.id));
      box.appendChild(el("span", "rs-levels__label", team.name));
      var value = el("span", "rs-levels__value", String(team.level));
      value.appendChild(el("span", "rs-levels__of", " / " + levels));
      box.appendChild(value);
      host.appendChild(box);
    });
  }

  function renderResultTeam(hostId, state, team, winnerId) {
    var host = $(hostId);
    host.innerHTML = "";
    if (!team) return;
    host.style.setProperty("--team-color", teamColor(team.id));

    var top = el("div", "rs-team__top");
    var logo = el("span", "rs-team__logo");
    logo.appendChild(icon("logo-" + teamLogo(state, team.id)));
    top.appendChild(logo);
    top.appendChild(el("span", "rs-team__name", team.name));
    host.appendChild(top);

    if (winnerId) {
      var champion = team.id === winnerId;
      var badge = el("span", "rs-badge " + (champion ? "rs-badge--win" : "rs-badge--out"));
      if (champion) badge.appendChild(icon("crown", "gm-ic--sm"));
      badge.appendChild(el("span", null, champion ? "Winner" : "Runner up"));
      host.appendChild(badge);
    }

    var level = el("div", "rs-stat");
    level.appendChild(el("span", null, "Reached"));
    level.appendChild(el("strong", null, "Level " + team.level));
    host.appendChild(level);

    var banked = el("div", "rs-stat rs-stat--coin");
    banked.appendChild(el("span", null, "Coins earned"));
    banked.appendChild(el("strong", null, String(teamEarnings(team))));
    host.appendChild(banked);

    // Not the cleared count: winning sets every member of the team to
    // `finished` (RelayEngine._advance_check), so the champion's green count is
    // always zero at this point and the stat would libel the team that won.
    var squad = el("div", "rs-stat");
    squad.appendChild(el("span", null, "Squad"));
    squad.appendChild(el("strong", null, team.roster_size + " playing"));
    host.appendChild(squad);
  }

  // What the team put in the purse across the match. Not `currency`: that is
  // what is *left* after the Grandmaster shopped, and a team that spent well
  // would look like it had earned nothing.
  function teamEarnings(team) {
    return (team.players || []).reduce(function (sum, player) {
      return sum + (player.coins_earned || 0);
    }, 0);
  }

  function rankedByEarnings(team) {
    return (team.players || []).slice().sort(function (a, b) {
      return (b.coins_earned || 0) - (a.coins_earned || 0);
    });
  }

  function resultRole(player) {
    if (player.is_leader) return ["Grandmaster", "is-leader"];
    if (!player.role) return ["Unassigned", ""];
    return [roleName(player.role),
      player.role === "duelist" ? "is-duelist"
        : player.role === "defuser" ? "is-defuser" : ""];
  }

  // `crown` marks your own team's top earner. The other squad's best earner is
  // their business: gilding their row here would read as an award on the wrong
  // side of the board.
  function renderResultRoster(hostId, state, team, crown) {
    var host = $(hostId);
    host.innerHTML = "";
    if (!team || !team.players) return;
    var color = teamColor(team.id);
    var total = teamEarnings(team);
    var ranked = rankedByEarnings(team);
    var best = ranked.length ? (ranked[0].coins_earned || 0) : 0;

    ranked.forEach(function (player) {
      var coins = player.coins_earned || 0;
      var row = el("li", crown && coins > 0 && coins === best ? "is-top" : "");
      row.style.setProperty("--team-color", color);
      row.appendChild(avatarNode(state, player, team.id));

      var who = el("div", "rs-who");
      who.appendChild(el("div", "rs-who__name", player.name));
      var role = resultRole(player);
      who.appendChild(el("div", "rs-who__role " + role[1], role[0]));
      row.appendChild(who);

      var game = el("div", "rs-game");
      if (player.is_leader) {
        // A Grandmaster never held a board of their own.
        game.appendChild(el("span", null, "Called the plays"));
      } else {
        game.appendChild(gameIcon(player.assigned_game));
        game.appendChild(el("span", null, gameName(player.assigned_game)));
      }
      row.appendChild(game);

      // Share of what this team earned, so the column adds up to the team and
      // not to some invented denominator.
      var share = total > 0 ? Math.round((coins / total) * 100) : 0;
      var box = el("div", "rs-share");
      box.appendChild(el("span", "rs-share__pct", share + "%"));
      var meter = el("span", "rs-share__meter");
      var fill = el("span", "rs-share__fill");
      fill.style.width = share + "%";
      meter.appendChild(fill);
      box.appendChild(meter);
      row.appendChild(box);

      var value = el("span", "rs-coins");
      value.appendChild(icon("coin", "gm-ic--sm"));
      value.appendChild(el("span", null, String(coins)));
      row.appendChild(value);

      host.appendChild(row);
    });
  }

  function renderMvp(state, team) {
    var host = $("result-mvp");
    host.innerHTML = "";
    if (!team || !team.players || !team.players.length) return;
    var ranked = rankedByEarnings(team);
    var best = ranked[0];
    var total = teamEarnings(team);
    // Nobody banked anything, so nobody is the most valuable. Handing out the
    // award anyway would be reading a ledger of zeroes as a result.
    if (!best || !(best.coins_earned > 0)) {
      host.appendChild(el("p", "rs-mvp__line",
        "No coins were banked, so there is nobody to single out."));
      return;
    }

    var card = el("div", "rs-mvp");
    card.appendChild(avatarNode(state, best, team.id));
    card.appendChild(el("div", "rs-mvp__name", best.name));
    card.appendChild(el("div", "rs-mvp__what", "Top contributor"));

    var share = total > 0 ? Math.round((best.coins_earned / total) * 100) : 0;
    var line = el("p", "rs-mvp__line");
    line.appendChild(el("strong", null, share + "%"));
    line.appendChild(el("span", null,
      " of everything " + team.name + " put in the purse"));
    card.appendChild(line);

    var second = el("p", "rs-mvp__line");
    second.appendChild(el("strong", null, String(best.coins_earned)));
    second.appendChild(el("span", null, " coins as " + resultRole(best)[0]));
    card.appendChild(second);
    host.appendChild(card);
  }

  function renderRewards(state, team) {
    var host = $("result-rewards");
    host.innerHTML = "";
    if (!team) return;
    // Earned and left over are different numbers and both are worth reading:
    // the gap between them is what the Grandmaster spent on perks.
    var earned = teamEarnings(team);
    [["Coins earned", earned],
     ["Spent on perks", Math.max(0, earned - (team.currency || 0))],
     ["Left in the purse", team.currency || 0]].forEach(function (pair) {
      var box = el("div", "rs-reward");
      box.appendChild(icon("coin"));
      var text = el("span");
      text.appendChild(el("span", "rs-reward__value", String(pair[1])));
      text.appendChild(el("span", "rs-reward__label", pair[0]));
      box.appendChild(text);
      host.appendChild(box);
    });
  }

  // --- boot ---

  var configLoaded = fetch("/api/config")
    .then(function (r) { return r.json(); })
    .then(function (body) {
      serverConfig = body;
      (body.library || []).forEach(function (entry) {
        gameNames[entry.id] = entry.name;
      });
    })
    .catch(function () {});

  if (previewParam()) {
    // The design gallery (backend/preview.py): render one canned state and
    // open no socket. Waits for the config so the panels that read the perk
    // catalogue and the role list draw properly.
    configLoaded.then(startPreview);
  } else {
    bindLanding();
    var saved = loadSession();
    var invited = inviteParam();
    // An invite for a *different* match beats a stale saved session.
    if (saved && saved.matchId && saved.playerId &&
        (!invited || invited === saved.matchId)) {
      session = saved;
      connect(); // snapshot on connect restores the right view
    } else {
      show("view-join");
    }
  }
})();
