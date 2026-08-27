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
  var mountedDuel = null;  // { duelId, renderer, mountId }
  var duelTimerHandle = null;
  var timerHandle = null;
  var frozenHandle = null;
  var effectsHandle = null;
  var silenceHandle = null;
  var toastHandle = null;
  var overlayHandle = null;
  var reconnectDelay = 500;
  var finished = false;

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

  function sendAction(fields) {
    fields.type = "lobby_action";
    send(fields);
  }

  // Duel ids aren't in the game library (the server picks them, so the lobby
  // picker never offers them) — name them from the duel catalogue instead.
  var DUEL_NAMES = { rps_duel: "Rock Paper Scissors" };

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
    else if (message.type === "level_advanced") stageOverlay("Level " + message.level + "! 🚀");
    else if (message.type === "perk_used") perkToast(message);
    else if (message.type === "duel_result") duelToast(message);
    else if (message.type === "event") logEvent(message.event, true);
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
    if (state.status === "lobby") renderLobby(state);
    else if (state.status === "finished") renderResult(state);
    else if (state.me && state.me.is_leader) renderLeader(state);
    else renderPlay(state);
  }

  // --- lobby (teams, leader seats, game assignment) ---

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
      var joinButton = box.querySelector(".join-team-btn");
      var cap = ((serverConfig && serverConfig.players_per_team) || 4) + 1;
      var full = team.players.length >= cap;
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
          if (p.role && !p.assigned_game && !blocker) {
            blocker = team.name + "'s Grandmaster still needs to assign a game to " +
              p.name + ".";
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

  function renderStrip(state) {
    var me = state.me;
    var strip = $("team-strip");
    strip.innerHTML = "";
    var team = me && me.team_id ? state.teams[me.team_id] : null;
    if (!team) return;
    var row = document.createElement("div");
    row.className = "team-row " + team.id;
    row.innerHTML =
      '<span class="team-name">' + (team.id === "alpha" ? "🔥" : "🌊") + " " +
      team.name + "</span>" +
      '<span class="stage-tag">Level ' + team.level + "</span>" +
      '<span class="muted">' + (state.duel && state.duel.you
        ? "You are the ⚔️ Duelist"
        : "Your game: " + gameName(me.assigned_game)) + "</span>";
    strip.appendChild(row);
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
      $("puzzle-prompt").textContent = puzzle.prompt;
      mountPuzzle(puzzle);
    } else {
      unmountPuzzle();
    }
    startCountdown(me.timer_deadline, me.status);
    renderFrozen(me.frozen_until);
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
    // Only the choice window is a race; the reveal beat needs no pressure bar.
    startDuelCountdown(duel.phase === "choosing" ? duel.deadline : null, duel);
  }

  function unmountDuel() {
    if (mountedDuel) {
      mountedDuel.renderer.unmount();
      mountedDuel = null;
    }
  }

  function startDuelCountdown(deadlineIso, duel) {
    clearInterval(duelTimerHandle);
    var bar = $("duel-timer-bar");
    if (!bar) return;
    if (!deadlineIso) { bar.hidden = true; return; }
    var deadline = parseDeadline(deadlineIso);
    var total = ((duel && duel.payload && duel.payload.choice_seconds) || 5) * 1000;
    bar.hidden = false;
    var tick = function () {
      var left = Math.max(0, deadline - Date.now());
      $("duel-timer-fill").style.width = Math.min(100, (left / total) * 100) + "%";
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
    if (mounted && mounted.puzzleId === puzzle.id) return; // same instance
    unmountPuzzle();
    var renderer = window.RelayGames[puzzle.game_id] || window.RelayGames.fallback;
    var api = {
      submit: function (answer) {
        send({
          type: "submit_answer",
          puzzle_id: puzzle.id,
          answer: String(answer),
        });
      },
      setReady: function () {},
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

  // Countdown driven by timer_deadline; server stays authoritative.
  function startCountdown(deadlineIso, status) {
    clearInterval(timerHandle);
    var bar = $("timer-bar"), label = $("timer-label");
    if (!deadlineIso) { bar.hidden = true; label.hidden = true; return; }
    var deadline = parseDeadline(deadlineIso);
    var total = ((lastState && lastState.config.wait_seconds) ||
      (serverConfig && serverConfig.wait_seconds) || 180) * 1000;
    bar.hidden = false;
    label.hidden = false;
    var prefix = status === "bonus" ? "🔥 Bonus deadline: " : "⏳ Holding cleared: ";
    var tick = function () {
      var left = Math.max(0, deadline - Date.now());
      $("timer-fill").style.width = Math.min(100, (left / total) * 100) + "%";
      label.textContent = prefix + Math.ceil(left / 1000) + "s";
      if (left <= 0) {
        label.textContent = "⏳ Time's up — waiting for the server…";
        clearInterval(timerHandle);
      }
    };
    tick();
    timerHandle = setInterval(tick, 250);
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

  function statusPill(player) {
    // Silenced: the server nulls the progress fields rather than lying about
    // them, so there is genuinely nothing to show.
    if (player.status === "hidden") return ["🔇 ?", "pill"];
    if (player.green) {
      return player.role === "duelist"
        ? ["duel won ⚔️", "pill green"] : ["cleared ✅", "pill green"];
    }
    if (player.status === "bonus") return ["bonus 🔥", "pill bonus"];
    if (player.status === "duelling") return ["duelling ⚔️", "pill"];
    if (player.status === "finished") return ["done 🏁", "pill"];
    return ["solving …", "pill"];
  }

  function renderLeader(state) {
    show("view-leader");
    var me = state.me;
    var team = state.teams[me.team_id];
    $("leader-team-title").textContent =
      (team.id === "alpha" ? "🔥 " : "🌊 ") + team.name + " — Level " + team.level;
    $("leader-currency").textContent = "🪙 " + team.currency;
    var locked = team.duel_penalty_until &&
      parseDeadline(team.duel_penalty_until) > Date.now();
    var silenced = team.silenced_until &&
      parseDeadline(team.silenced_until) > Date.now();
    $("leader-status-line").textContent =
      (silenced
        ? "🔇 Silenced — you can't see who has cleared"
        : team.green_count + "/" + team.roster_size + " cleared") +
      (team.shield_active ? " · 🛡️ shield up" : "") +
      (team.reflect_active ? " · 🪞 reflect up" : "") +
      (team.insurance_active ? " · 🧾 insured" : "") +
      (team.duel_streak ? " · ⚔️ duel streak " + team.duel_streak : "") +
      (locked ? " · ⛔ duel penalty — the team can't advance yet" : "");
    watchSilence(team);
    renderDuel(state, "leader-duel-card", "leader-duel-mount");

    var roster = $("leader-roster");
    roster.innerHTML = "";
    team.players.forEach(function (player) {
      if (player.is_leader) return;
      var row = document.createElement("li");
      var name = document.createElement("span");
      name.textContent = player.name + (player.connected ? "" : " 💤") +
        (player.role ? " · " + roleName(player.role) : "") +
        " · " + gameName(player.assigned_game);
      row.appendChild(name);
      var pill = document.createElement("span");
      var pillSpec = statusPill(player);
      pill.textContent = pillSpec[0];
      pill.className = pillSpec[1];
      row.appendChild(pill);
      roster.appendChild(row);
    });

    var opponentId = me.team_id === "alpha" ? "bravo" : "alpha";
    var opponent = state.teams[opponentId];
    $("leader-opponent").textContent = opponent
      ? "🔭 " + opponent.name + ": Level " + opponent.level + " · " +
        opponent.green_count + "/" + opponent.roster_size + " cleared"
      : "";

    renderPerkGrid(state, team);
    renderHandoff(team);
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
    Object.keys(perks).forEach(function (perkId) {
      var perk = perks[perkId];
      var card = document.createElement("div");
      card.className = "perk-card " + perk.kind;
      var title = document.createElement("div");
      title.className = "perk-name";
      title.textContent = (perk.kind === "attack" ? "⚔️ " : "🛡️ ") + perk.name;
      card.appendChild(title);
      var cost = document.createElement("div");
      cost.className = "muted";
      cost.textContent = "🪙 " + perk.cost;
      card.appendChild(cost);
      if (perk.desc) {
        var desc = document.createElement("div");
        desc.className = "perk-desc";
        desc.textContent = perk.desc;
        card.appendChild(desc);
      }
      var target = null;
      if (perkId === "extend_wait") {
        target = document.createElement("select");
        target.className = "assign-select";
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
      var buy = document.createElement("button");
      buy.className = "mini-btn";
      buy.textContent = "Buy";
      buy.disabled = team.currency < perk.cost;
      buy.addEventListener("click", function () {
        var message = { type: "buy_perk", perk_id: perkId };
        if (target && target.value) message.target_id = target.value;
        send(message);
      });
      card.appendChild(buy);
      grid.appendChild(card);
    });
  }

  function renderHandoff(team) {
    var select = $("handoff-select");
    select.innerHTML = "";
    team.players.forEach(function (player) {
      if (player.is_leader) return;
      var option = document.createElement("option");
      option.value = player.id;
      option.textContent = player.name +
        (player.role ? " · " + roleName(player.role) : "") +
        " (" + gameName(player.assigned_game) + ")";
      select.appendChild(option);
    });
    $("handoff-btn").onclick = function () {
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

  function logEvent(event, fresh, feedId) {
    if (!feedId) {
      feedId = lastState && lastState.me && lastState.me.is_leader
        ? "leader-feed" : "event-feed";
    }
    var feed = $(feedId);
    var item = document.createElement("li");
    if (fresh) item.className = "fresh";
    item.textContent = event.message;
    feed.insertBefore(item, feed.firstChild);
    while (feed.children.length > 6) feed.removeChild(feed.lastChild);
  }

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
    clearInterval(frozenHandle);
    clearTimeout(silenceHandle);
    clearScreenEffects();
    $("choice-overlay").hidden = true;
    $("frozen-overlay").hidden = true;
    show("view-result");
    var mine = state.me ? state.me.team_id : null;
    var won = state.winner_team_id === mine;
    var levels = (state.config && state.config.level_count) || 10;
    $("result-emoji").textContent = won ? "🏆🎉" : "😵💨";
    $("result-title").textContent = won ? "You won!" : "You lost!";
    $("result-sub").textContent =
      "Team " + state.teams[state.winner_team_id].name +
      " cleared all " + levels + " levels first.";
  }

  // --- boot ---

  fetch("/api/config")
    .then(function (r) { return r.json(); })
    .then(function (body) {
      serverConfig = body;
      (body.library || []).forEach(function (entry) {
        gameNames[entry.id] = entry.name;
      });
    })
    .catch(function () {});
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
})();
