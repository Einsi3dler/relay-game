# The Relay — Build Task List (MVP rebuild)

The full plan for rebuilding The Relay from the archived prototype into the MVP. Work
top-to-bottom by phase; within a phase, tasks tagged to different owners run in
parallel. Each task has an **owner slice**, **dependencies**, **files**, and
**acceptance criteria (AC)** you can check yourself.

**Before starting any task:** read [GAME_DESIGN.md](GAME_DESIGN.md) and
[ARCHITECTURE.md](ARCHITECTURE.md), and `git pull --rebase` (see
[CONTRIBUTING.md](CONTRIBUTING.md) §1).

Legend: **[C]** Core · **[G1..G4]** Game owners · **[F]** Frontend · **[ALL]** everyone.
Status boxes are for you to tick in PRs.

---

## Phase 0 — Project setup (blocks everything)  ·  owner: [C]

- [x] **T0.1 Scaffold the package** — create the `backend/` layout from
  [ARCHITECTURE.md](ARCHITECTURE.md) §2 with empty/stub modules and `__init__.py`s.
  Restore a `pyproject.toml` at repo root (mirror `legacy/pyproject.toml`: FastAPI,
  uvicorn, pytest, httpx; package name `relay-mvp`; `pythonpath=["."]`).
  **AC:** `pip install -e ".[test]"` succeeds; `python -c "import backend"` works;
  `pytest` runs (0 tests OK).
- [x] **T0.2 `backend/config.py`** — single source of tunables:
  `REST_SECONDS=15`, `HOLDING_SECONDS=20`, `MAIN_PUZZLE_SECONDS=0`,
  `PLAYERS_PER_TEAM=4`, `MIN_PLAYERS_PER_TEAM=4`, `STAGE_COUNT=4`,
  `SUBMIT_MIN_INTERVAL_MS=300`, `MATCH_TTL_SECONDS=1800`,
  `TEAM_IDS=("alpha","bravo")`, and `GAME_ORDER: list[str]` (game ids per stage,
  initially placeholders). **AC:** imported by other modules; no gameplay literal
  exists anywhere else (grep for `15`, `20`, `4` in engine returns nothing
  meaningful).
- [x] **T0.3 CI (optional but recommended)** — GitHub Actions running `pytest` on PRs.
  **AC:** red/green check appears on PRs.

## Phase 1 — Data model & state  ·  owner: [C]  ·  depends: T0.1–T0.2

- [x] **T1.1 `models.py`** — dataclasses `Match`, `Team`, `Player`, `Event` per
  [ARCHITECTURE.md](ARCHITECTURE.md) §3, each with `.public()` returning exactly the
  shapes in [WEBSOCKET_PROTOCOL.md](WEBSOCKET_PROTOCOL.md) §3. Include the derived
  `green(player)` helper. **AC:** `.public()` output validates against the protocol
  shapes; **no `answer` field is ever present** in any `.public()` output (unit test).
- [x] **T1.2 `state.py`** — `InMemoryStateStore` with async `add/get/require/all`
  (port from legacy, it's fine as-is). **AC:** create → get returns same match;
  `require` on missing id raises.
- [x] **T1.3 `games/base.py`** — the `GameModule` Protocol, `PuzzleInstance`
  dataclass, and `normalize_answer` **exactly** as in
  [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md) §2 & §5, plus the spec §7 template
  saved as `backend/games/template.py`. This unblocks all game owners.
  **AC:** `games/template.py` imports and type-checks against the Protocol.
- [x] **T1.4 `registry.py`** — `GameRegistry` built from `config.GAME_ORDER`;
  `for_stage(n)` (1-based) returns the module for that stage; `reset_all()` calls
  `reset()` on every module. **AC:** with placeholder/fake games registered,
  `for_stage(1..4)` returns the right ids; `for_stage(5)` raises.

## Phase 2 — The relay engine  ·  owner: [C]  ·  depends: Phase 1

> This is the heart. Implement the loop in [GAME_DESIGN.md](GAME_DESIGN.md) §4 **exactly**.
> The engine is pure/synchronous and returns an `EngineResult` describing what
> changed and which timers to (re)schedule/cancel — it never sleeps or does I/O.

- [x] **T2.1 Join & lobby** — `create_match`, `join_match(name, team_id)` with
  auto-balance, team-full/started rejection, and `start_match` when both teams hit
  `MIN_PLAYERS_PER_TEAM`. On start, freeze `roster_size` per team and config
  snapshot, set everyone to `solving` with a Stage-1 main puzzle.
  **AC:** joining past `PLAYERS_PER_TEAM` raises; match flips `lobby→active` at start;
  each player gets a distinct seeded main puzzle.
- [x] **T2.2 `submit_main`** — validate puzzle id & status; on correct answer:
  `solving→resting`, start a `rest` timer (deadline = now + `REST_SECONDS`), then run
  the **advance check**. On wrong answer: stay `solving` but serve a **fresh main
  puzzle** (new seed, `attempt` incremented — see
  [GAME_DESIGN.md](GAME_DESIGN.md) §4); no other penalty.
  **AC:** correct → `resting` + deadline set; wrong → still `solving` with a *new*
  puzzle id; stale/foreign `puzzle_id` → rejected result.
- [x] **T2.3 Advance check + win** — when all of a team's `roster_size` players are
  green: if stage `== STAGE_COUNT` → team wins (`finished`, match `finished`,
  `winner_team_id` set, cancel team timers); else advance the team's `stage`, reset
  every team member to `solving` with a fresh next-stage main puzzle, cancel their
  timers. **Runs on every green transition, not just timer fires.**
  **AC:** the §7 worked example reproduces step-by-step in a unit test; win fires
  only on Stage 4; teams advance independently.
- [x] **T2.4 `on_rest_expired`** — when a `resting` player's timer fires: if team all
  green, no-op; else `resting→holding`, assign a holding puzzle, start `holding`
  timer. **AC:** rest expiry with team not-ready → `holding` + holding puzzle +
  deadline; with team ready → no change.
- [x] **T2.5 `submit_holding`** — correct: `holding→resting`, new `rest` timer, run
  advance check. Wrong: **lose green** → `holding→solving`, fresh main puzzle, cancel
  timer. **AC:** correct holding keeps green and can trigger advance; wrong holding
  returns to `solving` with a *new* main puzzle id.
- [x] **T2.6 `on_holding_expired`** — same consequence as a wrong holding answer
  (lose green → `solving`). **AC:** holding timer expiry → `solving` + new main puzzle.
- [x] **T2.7 Reconnect/disconnect (minimal)** — mark `connected` false/true; **do not**
  change status or timers on disconnect (green persists; server timers keep running).
  On reconnect: `resting`/`holding` resume the current state and timer; a `solving`
  player is served a **fresh** main puzzle (prevents replay-to-rewatch, esp. ECHO).
  **AC:** disconnect while `resting` keeps player green and the team can still
  advance; reconnect while `holding` resumes the same holding puzzle; reconnect
  while `solving` yields a new puzzle id. (Follow [GAME_DESIGN.md](GAME_DESIGN.md) §9.)
- [x] **T2.8 Engine unit tests** — cover T2.1–T2.7 including: advance blocked until
  all green; advance on 4th green mid-rest; lose-green-then-cannot-advance; win on
  Stage 4 only; independent team stages. **AC:** all pass; the design §7 example is
  a named test.

## Phase 3 — Timers & server wiring  ·  owner: [C]  ·  depends: Phase 2

- [x] **T3.1 `timers.py` `TimerService`** — schedule/cancel a single pending deadline
  per `(match_id, player_id)`; on fire, call the engine hook and hand the result back
  to the broadcast layer. Per [ARCHITECTURE.md](ARCHITECTURE.md) §4. **AC:** a
  scheduled `rest` timer fires `on_rest_expired` at the deadline; scheduling a new
  timer cancels the old; advancing cancels team timers (no ghost holding questions
  after advance).
- [x] **T3.2 Per-match serialization** — an `asyncio.Lock`/queue per match so messages
  and timer callbacks mutate a match one at a time. **AC:** concurrent submits don't
  interleave; "who won first" is deterministic in a test.
- [x] **T3.3 `main.py` REST routes** — `/`, `/api/config`, `POST /api/matches`,
  `POST /api/matches/{id}/join`, `GET /api/matches/{id}` per
  [WEBSOCKET_PROTOCOL.md](WEBSOCKET_PROTOCOL.md) §1. **AC:** join returns player+match;
  full/started join → 400 with `detail`.
- [x] **T3.4 WebSocket endpoint + `ConnectionManager`** — accept, register, snapshot
  on connect, broadcast on change; a second socket for the same `player_id`
  supersedes the first (close code `4001`); dispatch `submit_answer`/`submit_holding`/
  `request_state`/`heartbeat` into the engine; send `error` on invalid input and on
  submissions faster than `SUBMIT_MIN_INTERVAL_MS`; emit `state_snapshot` after
  every change and the nudge messages (`event`, `stage_advanced`, `match_won`).
  Message (de)serialisation helpers live in `protocol.py`. **AC:** protocol §2
  behaviours hold; closing a socket doesn't crash the match; a duplicate connect
  closes the old socket; snapshots never contain answers.
- [x] **T3.5 WebSocket integration tests** — with FastAPI `TestClient`: two full teams
  play to a win over the socket. **AC:** a scripted match reaches `match_won`.
- [x] **T3.6 Match eviction** — evict matches that are `finished` or idle (no
  messages, no timer activity) for `MATCH_TTL_SECONDS` so the in-memory store
  doesn't grow forever; cancel their timers on eviction. **AC:** an evicted match
  id returns 404 on lookup; an active match is untouched; no timer fires for an
  evicted match.

## Phase 4 — The four games (parallel)  ·  owners: [G1][G2][G3][G4]  ·  depends: T1.3 (`games/base.py`)

> The four MVP games are fully specified in [GAMES_SPEC.md](GAMES_SPEC.md):
> **G1 = REWIRE** (puzzle), **G2 = MIRROR RUN** (divided attention; replaced SWEEP), **G3 = DECANT** (sorting),
> **G4 = ECHO** (reflex/memory). They are **action** games, so each owner delivers
> **both** a backend module and a frontend renderer. Games are independent of each
> other and of the engine — build and test the module with **no server running**.
> You need `games/base.py` (T1.3), [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md), and
> your game's section of [GAMES_SPEC.md](GAMES_SPEC.md).

For **each** of Game 1–4 (`[G1]`…`[G4]`):

- [x] **T4.x.1 Implement the module** — `backend/games/gameN_<name>.py`: `id`,
  `name`, `generate_main`, `generate_holding`, `check`, `reset`. Deterministic by
  `seed`, stateless, guaranteed-solvable generation, **no solution in `payload`**
  (recompute in `check` where many solutions are valid — see your game's validation
  in [GAMES_SPEC.md](GAMES_SPEC.md)). **AC:** matches your game's spec; a generated
  board is provably solvable; illegal/short interactions → `check` False.
- [x] **T4.x.2 Frontend renderer** — `frontend/games/<id>.js` implementing the
  `mount/unmount` interface in [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md) §10:
  draw the state from `payload`, handle the clicks/drags/taps, build the answer
  encoding, call `api.submit(...)`. Vanilla JS, self-registers on
  `window.RelayGames["<id>"]`. **AC:** mounts into the shell by `game_id`, submits a
  valid encoding, `unmount()` fully cleans up before the next puzzle.
- [x] **T4.x.3 Register it** — add your `id` at your stage index in
  `config.GAME_ORDER` and your class in `registry.py` (the sanctioned one-line
  cross-slice edits; call them out in your PR). **AC:** `registry.for_stage(x)`
  returns your module; a full match reaches your stage and serves your puzzle.
- [x] **T4.x.4 Tests** — the 7-point suite in
  [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md) §8 **plus** the game-specific cases in
  [GAMES_SPEC.md](GAMES_SPEC.md) "Per-game deliverables", in
  `tests/games/test_gameN_<name>.py`. **AC:** all pass, including no-solution-leak
  (documented exception: ECHO's `sequence`) and
  solvable-board.
- [ ] **T4.x.5 Playtest note** — record rough solve times for main & holding in your
  PR so Core can tune `REST_SECONDS`/`HOLDING_SECONDS`. **AC:** main ≈ 15–40s,
  holding ≈ a few seconds; times noted.

## Phase 5 — Frontend  ·  owner: [F]  ·  depends: T3.3–T3.4 (can stub against protocol earlier)

- [x] **T5.1 Join / lobby view** — fetch `/api/config`, create/join a match, pick
  team/name, show a lobby that lists players and waits for match start. **AC:**
  two browsers can join opposing teams and see each other in the lobby.
- [x] **T5.2 Play view shell + renderer registry** — mount the correct game
  renderer by `me.current_puzzle.game_id` from `window.RelayGames` into the play
  container, `unmount()` the previous one on change, and provide `api.submit()`
  wiring (picks `submit_answer`/`submit_holding` from `current_puzzle.kind`). Ship
  the **fallback renderer** (`frontend/games/fallback.js`) for text /
  `payload.options` puzzles. **AC:** an action game (or the fallback) mounts and
  submits; switching puzzles cleanly unmounts the old renderer; wrong submits show
  the `error` toast. (Game renderers themselves are T4.x.2, owned by game devs.)
- [x] **T5.3 Readiness + countdown** — a team strip showing each player's status
  (green when `resting`/`holding`) and `green_count/roster_size`; a countdown driven
  by `timer_deadline` for rest and holding. **AC:** countdown matches server within
  ~1s; going green flips the indicator; holding question appears when the server
  sends it.
- [x] **T5.4 Stage transition + result** — animate `stage_advanced`; show a win/loss
  screen on `match_won`. **AC:** the winning team sees "You won", the other "You lost";
  no further input accepted.
- [x] **T5.5 Reconnect** — on socket drop, reconnect and re-sync purely from
  `state_snapshot`. **AC:** refreshing the page mid-match restores the correct view.

## Phase 6 — Integration, tuning, polish  ·  owner: [ALL]  ·  depends: Phases 2–5

- [ ] **T6.1 Full 8-player playtest** — two teams of four, real browsers, play to a
  win. File bugs. **AC:** a match completes end-to-end with no server errors.
- [ ] **T6.2 Timer tuning** — set `REST_SECONDS`/`HOLDING_SECONDS` from playtest data.
  **AC:** finishing early feels like a meaningful rest but idling still costs
  attention (holding questions actually occur in normal play).
- [ ] **T6.3 Docs sync** — update any doc whose rule/shape changed during the build.
  **AC:** docs match code; `README` links resolve.

## Phase 7 — Stretch (only after MVP is solid)  ·  owner: [ALL]

- [ ] Attempt cap / lockout on main puzzles.
- [ ] `MAIN_PUZZLE_SECONDS > 0` time limit on main puzzles.
- [ ] Spectator/dashboard view.
- [ ] Randomised game order or a 5th game.
- [ ] Rejoin-by-code UX niceties.

---

## Can one person work on multiple things at once? Yes.

Lanes exist to stop **two people editing the same file at the same time** (merge
hell), **not** to cap how much one person takes on. What actually limits parallel
work is only two things:

1. **Dependencies** — you can't build a task before the thing it needs exists (see
   the critical path below).
2. **One active editor per file at a time** — a file/slice should have a single
   person driving it *right now*, so changes don't collide.

Within those limits, go as wide as you want:

- **One person can hold several lanes.** A single dev (or their AI agent) can own
  Core *and* Game 2, or build two games — as long as nobody else is editing those
  files concurrently.
- **Run tasks in parallel on separate branches** (or `git worktree`s / separate
  agent sessions), one branch per lane you're driving. Keep each branch small and
  merge often so others get your changes. This is the clean way to have one person
  push several things at once without tangling them.
- **Lanes are handoffs, not property.** Finished your game early? Grab an
  unclaimed lane, help on the frontend, or take a Phase 6/7 task. Announce it (so
  no one double-drives a file) and update the table below.
- **Independent tasks inside a lane parallelize too.** The four games (T4.1–T4.4)
  are fully independent of each other; the frontend views (T5.1–T5.5) can be split;
  engine tasks T2.2–T2.6 can be drafted in parallel once the model (Phase 1) exists.

The only things that are genuinely serial: Phase 0 → Phase 1 must land before the
engine and games; the two shared registration files (`config.py`, `registry.py`)
should be edited one PR at a time (see [CONTRIBUTING.md](CONTRIBUTING.md) §2).

## Suggested starting split (scale to however many people you have)

This assumes ~6 people for maximum parallelism. With fewer, **combine lanes onto
one person** (e.g. Core lead also builds Games 1–2); with more, split the frontend
or pair on Core. It's a starting point, not a fence.

| Lane | First tasks | Combine-if-short hint |
| --- | --- | --- |
| **[C]** Core | Phase 0 → 1 → 2 → 3 (unblocks everyone) | If solo-heavy, Core can also take a game or two after Phase 3. |
| **[G1]** REWIRE (puzzle) | Read specs, then T4.1.* once `games/base.py` lands (T1.3) | Any game dev can hold 2 games. |
| **[G2]** MIRROR RUN (divided attention) | same, T4.2.* | " |
| **[G3]** DECANT (sorting) | same, T4.3.* | " |
| **[G4]** ECHO (reflex/memory) | same, T4.4.* | " |
| **[F]** Frontend | Stub against [WEBSOCKET_PROTOCOL.md](WEBSOCKET_PROTOCOL.md), then T5.* | Split T5.1–T5.5 across two people, or fold into Core if needed. |

**Critical path:** T1.3 (`games/base.py`) unblocks all four game lanes — Core should
land it early. The Frontend can start against the protocol doc before the backend is
finished. Games need nothing from the engine to be built and tested. Everything else
can run concurrently within the two limits above.

Related: [GAME_DESIGN.md](GAME_DESIGN.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md) · [WEBSOCKET_PROTOCOL.md](WEBSOCKET_PROTOCOL.md) · [CONTRIBUTING.md](CONTRIBUTING.md)
