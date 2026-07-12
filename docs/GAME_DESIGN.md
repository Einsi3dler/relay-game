# The Relay — Game Design (MVP)

This is the source of truth for **what the game does**. If code and this document
disagree, this document wins until it is updated by agreement. Keep it current.

---

## 1. The pitch

Two teams of four race through four puzzle games. The twist is the **relay rule**:
a team only moves to the next game when **all four teammates are simultaneously
"green" (ready)** — and staying green while you wait for slower teammates takes
active effort. The **first team to finish all four games wins.**

It rewards a team that is *evenly* fast, not one carried by a single star player.

## 2. Match structure

| Concept | MVP value | Notes |
| --- | --- | --- |
| Teams per match | 2 (Alpha, Bravo) | Fixed for MVP. |
| Players per team | 4 | `PLAYERS_PER_TEAM`. |
| Stages per match | 4 | `STAGE_COUNT`. One game per stage. |
| Games | Game 1 → Game 2 → Game 3 → Game 4 | Fixed order for MVP. |
| Win condition | First team to complete Stage 4 | See §6. |

- Both teams play the **same four games in the same order**. Each player solves
  their **own instance** of the current stage's game (same game, independent
  puzzle so nobody can copy an answer).
- **No roles.** Every player on a team plays the same game each stage. (Roles were
  a legacy concept; cut for the MVP.)

### Lobby / start (host-controlled)

- The **first player to join a match is its host**. Players join **unassigned**
  (no team) and pick a team from inside the lobby — or the host assigns them.
- The **host controls the lobby**: move any player between teams, kick players
  (kicked sockets close with code `4403`; the kicked id is dead, but the person
  may rejoin as a new player), set the **minimum players per team**
  (1..`PLAYERS_PER_TEAM`, default `MIN_PLAYERS_PER_TEAM`), and **start** the
  match. There is no auto-start.
- Start is allowed only when **every player has a team** and **both teams have at
  least the minimum**. Host powers exist only while the match is in the lobby —
  the roster freezes at start.
- If the host disconnects, any player can **claim host** (only while the host is
  actually gone, so a host page-refresh keeps the seat).
- Sharing: the lobby exposes an invite URL (`/?match=<id>`) that routes a visitor
  straight to the join flow for that match.

## 3. Player status model

Each player has exactly one status at any time. This is the heart of the engine.

| Status | Meaning | Player sees |
| --- | --- | --- |
| `lobby` | Joined, match not started. | Waiting screen. |
| `solving` | Working on the current stage's **main puzzle**. | The game puzzle + answer box. |
| `resting` | Just went green; in the **rest window**. | "Ready ✅" + a rest countdown. No task. |
| `holding` | Rest window ended, team not all green yet; answering a **holding question**. | A quick holding puzzle + countdown. |
| `finished` | Team has cleared Stage 4 (match over for them). | Result screen. |

**"Green" = the player counts as ready for advancement.** A player is green while
in `resting` **or** `holding`. A player is *not* green while `solving`.

```
green(player) := player.status in {"resting", "holding"}
```

## 4. The relay loop (per stage)

This is the exact lifecycle. Implement it precisely; it is covered by tests.

```
STAGE N BEGINS
  └─ every player on both teams: status = solving,
     assigned a fresh main puzzle from Game N.

WHILE a team has not advanced:

  ── Player solves main puzzle correctly ──────────────────────────────
     status: solving → resting
     start REST timer (REST_SECONDS, default 15s)
     >>> run ADVANCE CHECK for that team   (a 4th green may advance now)

  ── Player answers main puzzle incorrectly ───────────────────────────
     stays solving, but is assigned a FRESH main puzzle (new seed).
     (MVP: unlimited attempts, no other penalty. A fresh instance per
     attempt keeps retry fair for state-revealing games — a failed SWEEP
     board or a watched ECHO sequence must not be retried. Optional
     attempt cap is a stretch goal, see §8.)

  ── REST timer expires for a resting player ──────────────────────────
     if team is ALL green  → (advance already happened / will happen; stay green)
     else:
        status: resting → holding
        assign a HOLDING puzzle from Game N
        start HOLDING timer (HOLDING_SECONDS, default 20s)

  ── Player answers HOLDING puzzle correctly ──────────────────────────
     status: holding → resting
     start a new REST timer (REST_SECONDS)
     >>> run ADVANCE CHECK

  ── Player answers HOLDING puzzle incorrectly, OR HOLDING timer expires ─
     >>> player LOSES GREEN
     status: holding → solving
     assign a fresh main puzzle from Game N   (must re-qualify)

ADVANCE CHECK (for a team):
  if all PLAYERS_PER_TEAM players on the team are green (resting or holding):
     if N == STAGE_COUNT (last stage):
        team wins → every player on team status = finished; MATCH ENDS.
     else:
        team advances → STAGE N+1 BEGINS for that team (see top).
```

### Key rules to get right

1. **Advancement is checked the instant a player becomes green** (solving→resting
   or holding→resting), not only when a timer fires. If the 4th teammate goes green
   while the other three are resting, the team advances immediately.
2. **The rest window is a grace period, not a requirement.** A team can advance
   during anyone's rest window. The window only matters if the team is *not* all
   green — then it decides *when* the waiting player gets a holding question.
3. **Losing green** sends you back to a **fresh main puzzle**, not the holding
   puzzle. Holding questions never advance the stage; they only keep/lose green.
4. Each team advances **independently** — Alpha can be on Stage 3 while Bravo is on
   Stage 1. There is no shared stage clock.
5. All state is **server-authoritative.** Timers, correctness, and status live on
   the server. The client only displays and submits.

## 5. Timers

| Timer | Config key | Default | Behaviour |
| --- | --- | --- | --- |
| Rest window | `REST_SECONDS` | 15 | After going green, before a holding question can appear. |
| Holding question | `HOLDING_SECONDS` | 20 | Time to answer a holding question; expiry = fail = lose green. |
| Main puzzle | `MAIN_PUZZLE_SECONDS` | `0` (off) | MVP: no time limit on the main puzzle. Non-zero enables a limit (stretch). |

- Timers are **server-authoritative**. The server stores an absolute
  **`deadline`** (UTC ISO timestamp) for each active timer and sends it in the
  state snapshot; the client renders a countdown from `deadline - now`.
- When a timer expires the **server** applies the consequence (holding starts, or
  green lost). Do **not** rely on the client to report expiry — a client may be
  closed. See [ARCHITECTURE.md](ARCHITECTURE.md) §"Timers" for the mechanism.
- All durations are **tunable** via config. Expect these numbers to change during
  playtesting; never hard-code them in the engine or a game module.

## 6. Winning and ending

- The **first team** to pass the Stage 4 advance check **wins immediately**. The
  match transitions to a terminal `finished` state.
- The losing team is shown the result; the match no longer accepts answers.
- MVP has no draw handling beyond "first check to fire wins" — the engine is
  single-threaded per match, so ties resolve to whichever team's green transition
  the engine processed first.

## 7. Worked example

Team Alpha on Stage 2. Players: A, B, C, D.

1. All four are `solving` Game 2.
2. A solves → `resting`, 15s rest starts. Advance check: B,C,D not green → no.
3. B solves → `resting`. Advance check: C,D not green → no.
4. A's 15s rest ends; team still not all green → A → `holding`, gets a holding
   question, 20s timer.
5. C solves → `resting`. Advance check: D not green → no.
6. A answers the holding question correctly → `resting`, new 15s rest. Advance
   check: D not green → no.
7. D solves → `resting`. Advance check: **A,B,C,D all green → advance to Stage 3.**
   Everyone resets to `solving` with Game 3.

Alternate branch at step 6: A's holding **timer expires** → A loses green,
back to `solving` Game 2. Now even if D solves, the team is not all green (A isn't),
so it does not advance until A re-solves.

## 8. Explicitly out of scope for the MVP

Cut on purpose — **do not add these** without a design decision:

- Power-ups, sabotage, or any cross-team interference.
- Points / economy / grind mode.
- Roles or per-player puzzle specialisation.
- More than 2 teams or team sizes other than 4.
- Persistence / database / accounts (matches are in-memory and ephemeral).
- Reconnect "backlog" puzzles (see §9 for the simple MVP reconnect rule).

### Stretch goals (only after the MVP loop is solid and tested)

- Attempt cap on main puzzles (e.g. 3 misses → short lockout).
- Main-puzzle time limit (`MAIN_PUZZLE_SECONDS > 0`).
- Spectator view / bigger dashboard.
- Randomised game order or a 5th game.

## 9. Edge cases and their MVP rulings

| Situation | MVP ruling |
| --- | --- |
| A player disconnects mid-stage | They keep their status server-side and timers keep running — **no special grace period, no auto-kick**. A green player's green decays via the normal cascade (rest expires → holding → holding expires → green lost), i.e. within `REST_SECONDS + HOLDING_SECONDS` of going absent. If they were `solving`, the team simply can't complete until they return or a host ends the match. On reconnect: `resting`/`holding` resume the current state and timer; a `solving` player is served a **fresh** main puzzle (prevents replay-to-rewatch, esp. ECHO — see [GAMES_SPEC.md](GAMES_SPEC.md)). |
| A player disconnects while `holding` and the timer expires | Server applies the normal rule: they lose green and go to `solving`. When they reconnect they see the main puzzle. |
| Team is all green but one player's socket is dead | Advancement still fires (server-authoritative). The dead client catches up via `state_snapshot` on reconnect. |
| Both teams could win on the same engine tick | Impossible — one match is processed one message at a time; first green-transition to complete a team wins. |
| Fewer than 4 players (local testing) | Allowed only when `MIN_PLAYERS_PER_TEAM` is lowered; advance check uses the **actual** connected roster size at match start (frozen at start), not a hard-coded 4. |

> Reconnect handling is intentionally minimal. The legacy "backlog sync" puzzle is
> **not** part of the MVP.

---

Related: [ARCHITECTURE.md](ARCHITECTURE.md) · [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md) · [WEBSOCKET_PROTOCOL.md](WEBSOCKET_PROTOCOL.md)
