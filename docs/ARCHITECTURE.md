# The Relay — Architecture (MVP)

How the system is put together, and the seams contributors build against. Pair
this with [GAME_DESIGN.md](GAME_DESIGN.md) (the rules) and
[GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md) (the game contract).

---

## 1. High-level shape

```
 Browser (vanilla JS)                     FastAPI server (single process)
 ┌───────────────────────┐   HTTP/REST    ┌──────────────────────────────┐
 │ index.html / app.js    │ ─────────────▶ │ main.py  (routes + WS + fanout)
 │  - join / lobby        │   WebSocket    │   ├─ ConnectionManager        │
 │  - play view           │ ◀────────────▶ │   ├─ RelayEngine  (rules)     │
 │  - countdowns          │                │   ├─ StateStore   (in-memory) │
 │  - result screen       │                │   ├─ TimerService (deadlines) │
 └───────────────────────┘                │   └─ GameRegistry (games 1-4) │
                                           └──────────────────────────────┘
```

- **One Python process, in-memory state.** No database, no external services.
  A match lives in a dict keyed by match id and disappears when the process stops.
  That is fine for the MVP. To keep the dict from growing forever, matches that
  are `finished` or idle (no messages) for `MATCH_TTL_SECONDS` are **evicted**
  (see [TASK_LIST.md](TASK_LIST.md) T3.6).
- **Server-authoritative.** All correctness checks, status transitions, and timer
  expiries happen on the server. The client renders state and submits intents.
- **The engine is pure and synchronous.** It takes a `Match` + an action and
  mutates state, returning a result. It does no I/O and no `await`. This makes it
  trivially testable. All networking and timing lives *around* it.

## 2. Backend modules (target layout)

```
backend/
  __init__.py
  config.py        # ALL tunables (timers, team size, stage count, game order)
  models.py        # dataclasses: Match, Team, Player, PuzzleInstance, Event
  state.py         # InMemoryStateStore
  registry.py      # GameRegistry: maps stage index -> GameModule
  engine.py        # RelayEngine: the pure rules (relay loop, advance check, win)
  timers.py        # TimerService: schedules deadline callbacks into the engine
  protocol.py      # message (de)serialisation helpers + type constants
  main.py          # FastAPI app: REST routes, WebSocket endpoint, ConnectionManager
  games/
    __init__.py
    base.py        # GameModule Protocol/ABC + PuzzleInstance helpers (from spec)
    template.py    # copy this to build a new game
    game1_*.py     # owned by Game 1 dev
    game2_*.py     # owned by Game 2 dev
    game3_*.py     # owned by Game 3 dev
    game4_*.py     # owned by Game 4 dev
```

> This is the intended layout for the rebuild. The exact filenames for
> `game1_*` etc. are chosen by their owners; register them in `registry.py`.

### Responsibilities

- **`config.py`** — single home for `REST_SECONDS=15`, `HOLDING_SECONDS=20`,
  `MAIN_PUZZLE_SECONDS=0`, `PLAYERS_PER_TEAM=4`, `MIN_PLAYERS_PER_TEAM=4`,
  `STAGE_COUNT=4`, `SUBMIT_MIN_INTERVAL_MS=300`, `MATCH_TTL_SECONDS=1800`, and the
  ordered list of game module ids. Nothing else in the codebase should contain
  these literals.
- **`models.py`** — plain dataclasses with `.public()` methods that return the
  JSON-safe dict the client sees. **`.public()` must never include puzzle
  answers.** See [WEBSOCKET_PROTOCOL.md](WEBSOCKET_PROTOCOL.md) for exact shapes.
- **`state.py`** — create/get/require/list matches. Async signatures so the store
  could later be swapped for a real backing store without touching callers.
- **`registry.py`** — `GameRegistry.for_stage(n)` returns the `GameModule` for
  stage `n` (1-based). Built from the ordered id list in config. Games register
  themselves here; the engine only ever asks the registry, never a concrete game.
- **`engine.py`** — the `RelayEngine`. Pure functions over a `Match`:
  `start_match`, `submit_main`, `submit_holding`, `on_rest_expired`,
  `on_holding_expired`, plus the private `advance_check`. Returns an
  `EngineResult` describing what changed (events, timers to (re)schedule, whether
  the match ended). **This is where the GAME_DESIGN §4 loop is implemented.**
- **`timers.py`** — see §4 below.
- **`main.py`** — FastAPI wiring: REST for match create/join/config, one WebSocket
  per player, a `ConnectionManager` for fanout, and the glue that (a) calls the
  engine on incoming messages, (b) hands timer scheduling to `TimerService`, and
  (c) broadcasts fresh `state_snapshot`s after every change.

## 3. Data model (essentials)

```
Match
  id: str
  status: "lobby" | "active" | "finished"
  teams: { "alpha": Team, "bravo": Team }
  winner_team_id: str | None
  events: [Event]           # last ~30, for the log
  config_snapshot: {...}    # timers/sizes frozen at match start

Team
  id, name
  stage: int                # 1..STAGE_COUNT, per-team (independent)
  roster_size: int          # frozen at match start (usually 4)
  player_ids: [str]
  finished: bool

Player
  id, name, team_id                        # id is long + random — it is the WS credential
  status: "lobby"|"solving"|"resting"|"holding"|"finished"
  connected: bool
  attempt: int                             # counts main-puzzle instances this stage;
                                           #   feeds seed derivation (see "Seeds")
  current_main: PuzzleInstance | None      # server-only answer stripped in public()
  current_holding: PuzzleInstance | None
  timer_deadline: str | None               # UTC ISO; drives client countdown
  timer_kind: "rest"|"holding"|None

PuzzleInstance   (produced by a GameModule; see GAME_MODULE_SPEC)
  id, game_id, kind ("main"|"holding")
  prompt, payload            # what the client renders
  answer                     # SERVER ONLY — stripped from .public()
```

`green(player)` is a derived helper: `player.status in {"resting","holding"}`.

### Seeds

Game modules are deterministic in their `seed` (see
[GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md)), so a **predictable seed would let a
player precompute their board**. Rules:

- Seeds are generated **server-side** and are **never sent to the client** (not in
  payloads, snapshots, or logs the client can see).
- Derive them unguessably, e.g.
  `seed = int.from_bytes(hmac_sha256(SERVER_SEED_SECRET, f"{match_id}:{player_id}:{stage}:{attempt}")[:8])`
  where `SERVER_SEED_SECRET` is a per-process random value created at startup —
  or simply draw each seed from `secrets` and store it on the `PuzzleInstance`.
  Never use sequential counters or timestamps alone.
- `Player.attempt` increments every time a fresh main instance is served (stage
  start, wrong answer, lost green, reconnect-while-solving), which is what makes
  every attempt a genuinely new puzzle.

## 4. Timers (the tricky part)

Timers must fire even if the relevant client is closed, so they cannot live in the
browser. Approach:

- **`TimerService`** holds, per `(match_id, player_id)`, at most one pending
  deadline and an `asyncio` task (or a single global tick loop that scans
  deadlines every ~500ms — either is acceptable; a per-timer `asyncio.create_task`
  with `asyncio.sleep` is simplest).
- When the engine returns "start a REST timer for player X (deadline T)", `main.py`
  asks `TimerService` to schedule a callback at `T`.
- When the deadline fires, the callback calls back **into the engine**
  (`on_rest_expired` / `on_holding_expired`), which applies the GAME_DESIGN rule,
  then `main.py` broadcasts the new state and reschedules any follow-up timer.
- Starting a new timer for a player **cancels** their previous one (a player has at
  most one active timer). Advancing a stage or winning **cancels all** of a team's
  timers.
- The engine stays pure: it never sleeps. It only says *"schedule/cancel this
  deadline."* `TimerService` is the only place that touches the clock and the loop.

> Keep the clock in one place. If timer logic starts leaking into games or the
> connection manager, stop and refactor.

## 5. Frontend

- Single page: `frontend/index.html`, `frontend/app.js`, `frontend/styles.css`.
  No framework, no bundler.
- Flow: fetch `/api/config` → join via REST → open WebSocket → render every
  `state_snapshot`. The client is a **pure function of the latest snapshot** plus
  local countdown animation derived from `timer_deadline`.
- Views: **lobby** (waiting for players), **play** (current puzzle — main or
  holding — with countdown and a team readiness strip showing who's green), and
  **result** (win/lose).
- The client **never** decides correctness or advancement. It submits
  `submit_answer` / `submit_holding` and reacts to the snapshot.
- **Shell + per-game renderers.** The four MVP games are *action* games (rotate,
  flag, pour, tap), so rendering is split:
  - The **shell** (Frontend owner) owns the app frame: join/lobby, the play view
    container, the countdown from `timer_deadline`, the team-readiness strip, error
    toasts, and the result screen. It is generic and game-agnostic.
  - Each game ships its own **renderer** at `frontend/games/<id>.js` that registers
    into `window.RelayGames[game_id]` and implements `mount/unmount` (see
    [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md) §10). The shell looks up the renderer
    by `me.current_puzzle.game_id` and mounts it into the container.
  - A built-in **fallback renderer** handles plain text / multiple-choice puzzles
    (`payload.options`) so simple games need no JS. See
    [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md) §6.

```
frontend/
  index.html
  app.js            # shell: state, WS, mount/unmount active renderer, countdowns
  styles.css
  games/
    registry.js     # optional helper; renderers self-register on window.RelayGames
    rewire.js       # owned by Game 1 dev
    sweep.js        # owned by Game 2 dev
    decant.js       # owned by Game 3 dev
    echo.js         # owned by Game 4 dev
    fallback.js     # text / multiple-choice (shell/Frontend owner)
```

## 6. Concurrency model

- One match is mutated by one coroutine at a time. Process WebSocket messages and
  timer callbacks for a given match **serially** (e.g. an `asyncio.Lock` per match,
  or a single-consumer queue). This removes all races from the engine and makes the
  "who won first" question deterministic.
- Never mutate `Match` from two coroutines concurrently.

## 7. Testing seams

- The pure engine is unit-tested with no server or sockets — construct a `Match`,
  call `submit_main` / `on_rest_expired`, assert statuses and stage. See
  [TASK_LIST.md](TASK_LIST.md) for required cases.
- Game modules are tested in isolation against the `GameModule` contract
  (generate → check(correct)==True, check(wrong)==False, determinism by seed).
- WebSocket flow is tested with FastAPI's `TestClient` websocket support.

Related: [GAME_DESIGN.md](GAME_DESIGN.md) · [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md) · [WEBSOCKET_PROTOCOL.md](WEBSOCKET_PROTOCOL.md)
