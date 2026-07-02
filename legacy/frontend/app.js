const state = {
  config: null,
  match: null,
  player: null,
  socket: null,
};

const els = {
  createMatchBtn: document.getElementById("createMatchBtn"),
  joinMatchBtn: document.getElementById("joinMatchBtn"),
  matchIdInput: document.getElementById("matchIdInput"),
  nameInput: document.getElementById("nameInput"),
  teamSelect: document.getElementById("teamSelect"),
  roleSelect: document.getElementById("roleSelect"),
  joinPanel: document.getElementById("joinPanel"),
  playPanel: document.getElementById("playPanel"),
  matchLabel: document.getElementById("matchLabel"),
  track: document.getElementById("track"),
  teamStatus: document.getElementById("teamStatus"),
  roleName: document.getElementById("roleName"),
  playerStatus: document.getElementById("playerStatus"),
  difficultyBadge: document.getElementById("difficultyBadge"),
  puzzleCard: document.getElementById("puzzleCard"),
  answerForm: document.getElementById("answerForm"),
  answerInput: document.getElementById("answerInput"),
  feedback: document.getElementById("feedback"),
  pointBank: document.getElementById("pointBank"),
  powerups: document.getElementById("powerups"),
  eventLog: document.getElementById("eventLog"),
};

async function boot() {
  state.config = await fetchJson("/api/config");
  populateSelectors();
  els.createMatchBtn.addEventListener("click", createMatch);
  els.joinMatchBtn.addEventListener("click", joinMatch);
  els.answerForm.addEventListener("submit", submitAnswer);
}

function populateSelectors() {
  state.config.teams.forEach((team) => {
    const option = document.createElement("option");
    option.value = team;
    option.textContent = team === "alpha" ? "Team Alpha" : "Team Bravo";
    els.teamSelect.appendChild(option);
  });
  state.config.roles.forEach((role) => {
    const option = document.createElement("option");
    option.value = role;
    option.textContent = role;
    els.roleSelect.appendChild(option);
  });
}

async function createMatch() {
  const data = await fetchJson("/api/matches", { method: "POST", body: "{}" });
  els.matchIdInput.value = data.match.id;
  setFeedback(`Created match ${data.match.id}.`);
}

async function joinMatch() {
  const matchId = els.matchIdInput.value.trim();
  if (!matchId) {
    setFeedback("Create or enter a match ID first.");
    return;
  }
  const payload = {
    name: els.nameInput.value.trim() || "Runner",
    team_id: els.teamSelect.value || null,
    role: els.roleSelect.value || null,
  };
  const data = await fetchJson(`/api/matches/${matchId}/join`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  state.player = data.player;
  state.match = data.match;
  els.joinPanel.classList.add("hidden");
  els.playPanel.classList.remove("hidden");
  connectSocket(matchId, state.player.id);
  render();
}

function connectSocket(matchId, playerId) {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  state.socket = new WebSocket(`${scheme}://${window.location.host}/ws/matches/${matchId}?player_id=${playerId}`);
  state.socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "state_snapshot") {
      state.match = message.state;
      state.player = message.state.me || state.player;
      render();
    }
    if (message.type === "sabotage_applied" && state.player && message.target_team_id === state.player.team_id) {
      applySabotage(message.effect, message.duration);
    }
    if (message.type === "error") {
      setFeedback(message.error);
    }
  });
  state.socket.addEventListener("open", () => {
    send({ type: "request_state" });
  });
}

function render() {
  if (!state.match) {
    return;
  }
  els.matchLabel.textContent = `Match ${state.match.id}`;
  renderTrack();
  renderTeams();
  renderWorkspace();
  renderActions();
  renderEvents();
}

function renderTrack() {
  els.track.innerHTML = "";
  Object.values(state.match.teams).forEach((team) => {
    const row = document.createElement("div");
    row.className = "track-row";
    const pct = Math.min(100, ((team.level - 1) / state.match.max_level) * 100);
    row.innerHTML = `
      <strong>${team.name}</strong>
      <div class="track-bar"><div class="track-fill" style="width:${team.finished ? 100 : pct}%"></div></div>
      <span>L${team.level}/${state.match.max_level}</span>
    `;
    els.track.appendChild(row);
  });
}

function renderTeams() {
  els.teamStatus.innerHTML = "";
  Object.values(state.match.teams).forEach((team) => {
    const block = document.createElement("div");
    block.className = "team-block";
    const players = team.players.length
      ? team.players.map((player) => `
          <div class="player-row status-${player.status}">
            <span class="status-dot"></span>
            <span>
              <span class="player-name">${escapeHtml(player.name)}</span>
              <span class="player-role">${player.role} · ${player.status}</span>
            </span>
          </div>
        `).join("")
      : `<div class="event">Waiting for players</div>`;
    block.innerHTML = `
      <div class="team-heading"><span>${team.name}</span><span>L${team.level}</span></div>
      ${players}
    `;
    els.teamStatus.appendChild(block);
  });
}

function renderWorkspace() {
  const me = state.match.me;
  if (!me) {
    return;
  }
  const team = state.match.teams[me.team_id];
  els.roleName.textContent = `${me.name} · ${me.role}`;
  els.playerStatus.textContent = me.status;
  els.difficultyBadge.textContent = `x${team.difficulty_multiplier.toFixed(2)}`;
  const puzzle = me.status === "backlog" ? me.backlog_puzzle : me.status === "grinding" ? me.current_grind : me.current_puzzle;
  if (!puzzle) {
    els.puzzleCard.innerHTML = `
      <div class="puzzle-kind">Standby</div>
      <p class="puzzle-prompt">Waiting for the next relay signal.</p>
    `;
    els.answerInput.disabled = true;
    return;
  }
  els.answerInput.disabled = false;
  els.puzzleCard.innerHTML = `
    <div class="puzzle-kind">${puzzle.kind} · Level ${puzzle.level || "Grind"}</div>
    <p class="puzzle-prompt">${escapeHtml(puzzle.prompt)}</p>
    <div class="puzzle-hint">${escapeHtml(puzzle.payload.hint || puzzle.payload.format || puzzle.payload.focus || "Submit the exact answer.")}</div>
  `;
}

function renderActions() {
  if (!state.match || !state.match.me) {
    return;
  }
  const myTeam = state.match.teams[state.match.me.team_id];
  const targetTeam = Object.values(state.match.teams).find((team) => team.id !== myTeam.id);
  els.pointBank.textContent = myTeam.points;
  els.powerups.innerHTML = "";
  Object.entries(state.config.powerups).forEach(([name, meta]) => {
    const owned = myTeam.inventory[name] || 0;
    const row = document.createElement("div");
    row.className = "powerup-row";
    const deployButton = name === "shield"
      ? `<button data-action="shield">Arm</button>`
      : `<button data-action="deploy" data-powerup="${name}">Fire</button>`;
    row.innerHTML = `
      <span>${name} · ${meta.cost} pts · owned ${owned}</span>
      <button data-action="buy" data-powerup="${name}">Buy</button>
      ${deployButton}
    `;
    row.querySelector('[data-action="buy"]').addEventListener("click", () => send({ type: "buy_powerup", powerup: name }));
    const actionButton = row.querySelector('[data-action="deploy"], [data-action="shield"]');
    actionButton.addEventListener("click", () => {
      if (name === "shield") {
        send({ type: "activate_shield" });
      } else if (targetTeam) {
        send({ type: "deploy_powerup", powerup: name, target_team_id: targetTeam.id });
      }
    });
    els.powerups.appendChild(row);
  });
}

function renderEvents() {
  els.eventLog.innerHTML = "";
  [...state.match.events].reverse().forEach((event) => {
    const item = document.createElement("div");
    item.className = `event ${event.kind}`;
    item.textContent = event.message;
    els.eventLog.appendChild(item);
  });
}

function submitAnswer(event) {
  event.preventDefault();
  const me = state.match?.me;
  if (!me) {
    return;
  }
  const puzzle = me.status === "backlog" ? me.backlog_puzzle : me.status === "grinding" ? me.current_grind : me.current_puzzle;
  if (!puzzle) {
    return;
  }
  const type = me.status === "grinding" ? "submit_grind" : "submit_puzzle";
  send({ type, puzzle_id: puzzle.id, answer: els.answerInput.value });
  els.answerInput.value = "";
}

function send(payload) {
  if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
    setFeedback("Socket is not connected yet.");
    return;
  }
  state.socket.send(JSON.stringify(payload));
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Request failed.");
  }
  return data;
}

function applySabotage(effect, duration) {
  const className = `effect-${effect}`;
  document.body.classList.add(className);
  window.setTimeout(() => document.body.classList.remove(className), duration || 3500);
}

function setFeedback(message) {
  els.feedback.textContent = message;
  window.setTimeout(() => {
    if (els.feedback.textContent === message) {
      els.feedback.textContent = "";
    }
  }, 3600);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

boot().catch((error) => setFeedback(error.message));
