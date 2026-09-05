# The Relay — Build Task List

**Before starting any task:** read [GAME_DESIGN.md](GAME_DESIGN.md),
[REDESIGN_PLAN.md](REDESIGN_PLAN.md), and [ARCHITECTURE.md](ARCHITECTURE.md),
and `git pull --rebase` (see [CONTRIBUTING.md](CONTRIBUTING.md) §1).

Legend: **[C]** Core · **[G1..G6]** Game owners · **[F]** Frontend · **[ALL]** everyone.
Status boxes are for you to tick in PRs.

---

## Part 1 — v2: leaders, levels, currency & perks (current)

Built per [REDESIGN_PLAN.md](REDESIGN_PLAN.md); it holds the full task detail.

- [x] **V0 Plan doc** — `docs/REDESIGN_PLAN.md` committed and linked. · [C]
- [x] **V1 Non-breaking prep** — `generate_main(seed, level=1)` contract,
  registry `by_id`/`has`/`library()`, v2 config tunables. · [C]
- [x] **V2 Core loop** — leaders + assignment lobby, wait/bonus state machine,
  chained bonus economy with forfeit, perks, leader handoff, win at
  `LEVEL_COUNT`; backend test suites rewritten. · [C]
- [x] **V3 Frontend** — lobby leader/assignment UI, play view (choice overlay,
  frozen overlay, wait countdown), leader dashboard with perk shop and
  handoff. · [F]
- [x] **V4 Docs sync** — GAME_DESIGN / ARCHITECTURE / WEBSOCKET_PROTOCOL /
  GAME_MODULE_SPEC / CLAUDE.md / README rewritten for v2. · [C]
- [x] **V5 Level difficulty curves** — each game scales `generate_main` with
  `level` (1..`LEVEL_COUNT` + `BONUS_LEVEL_OFFSET`), deterministic per
  `(seed, level)`, still guaranteed-solvable; tests per level band shipped.
  Each game reads a per-level `MAIN_LEVEL_PARAMS` table (or `_params_for_level`);
  the curve is moderate — level 1 == the original board, level 10 clearly
  harder but still calm. · [G1..G6]
  **AC:** level 1 ≈ today's difficulty; level 10 clearly harder; the bonus
  board is genuinely harder than the current level.
  **Bonus tiers (follow-up, shipped):** the tables originally stopped at 10 rows
  and `choose_bonus` clamped to `LEVEL_COUNT`, so a team on level 10 was handed a
  bonus board of *identical* difficulty — the last part of the AC was unmet for
  levels 8–10. All ten tables now run to 13 rows (11..13 are bonus-only tiers)
  and the engine clamps at `LEVEL_COUNT + BONUS_LEVEL_OFFSET` via one shared
  `RelayEngine._bonus_level`. Several games were at renderer/spec ceilings by
  level 10 and climb on whatever knob was left; LANE SHIFT's tiers are shaped by
  generation cost (its `min_actions` gate pushed board generation past a second,
  so it stays at 4). Perceived difficulty of the new tiers is for V6/V7.
- [ ] **V6 Full playtest** — two full teams + Grandmasters in real browsers,
  play to a win; file bugs. Run it with [PLAYTEST_GUIDE.md](PLAYTEST_GUIDE.md).
  **AC:** a match completes with no server errors. · [ALL]
- [x] **V7 Economy & perk tuning** — `WAIT_SECONDS`, currency amounts, and perk
  costs are set as **provisional** values (reasoned, marked in
  `backend/config.py`); [PLAYTEST_GUIDE.md](PLAYTEST_GUIDE.md) captures what to
  measure. Re-tune from V6 data. **AC:** bonuses feel worth the risk; perks
  get bought but don't dominate. · [ALL]
- [x] **V7b Perk catalogue** — grown from 4 to 13, to give the Grandmaster a
  real decision and to stop Freeze being the obvious buy every time. Adds
  enforced attacks (Clock Burn, Skim, Silence), four **screen-effect** attacks
  (Wobble, Static, Mirror, Blackout — cosmetic, client-rendered, therefore
  unenforceable and priced as annoyances), and two defenses (Reflect,
  Insurance). `_apply_attack` is now validate-then-mutate so a rejected buy
  consumes nothing, and a bounced attack ignores the buyer's own defenses so
  two Reflects can't ping-pong. All costs/durations remain **provisional**.
  · [C + Frontend]
  **Note for a timed game (e.g. bomb defusal):** every shipped game has no
  internal clock and no fail state, which the enforced attacks quietly assume.
  On a timed game Scramble *helps* the victim (fresh instance = fresh clock) and
  Freeze becomes lethal rather than annoying. Screen effects are safe by
  construction — they never touch a clock. Before such a game lands, give the
  module contract a capability flag (e.g. `timed = True` / `perk_policy`) that
  `_apply_attack` honours when picking a victim, exactly as it already skips
  Duelists. Adding it with today's behaviour as the default costs nothing now;
  retrofitting it later means editing every module.
- [x] **V8 Real roles** — the placeholder `config.ROLES` grouping is replaced by
  the designed catalogue (Logician, Technocrat, Spatial Reasoner, Puzzle
  Master, Spymaster, Generalist, and the Duelist); the team leader is
  themed as the **Grandmaster**, who assigns each player a role that gates
  which games they may be given (Generalist = any). Duplicate roles per team
  are allowed. · [ALL]

- [x] **V9 The Duelist** — a team may field one **Duelist**, a champion who
  never solves a puzzle and instead fights the other team's Duelist. The role is
  mirrored (both teams field one or neither), the **server** picks the duel
  game, a win is how the Duelist goes green, and a loss stamps a once-per-level
  advance lock on their team while paying the winner 2/4/8. First duel game is
  **RPS DUEL**. Needed a real engine extension: timers are now scope-keyed so a
  duel clock can run concurrently with a wait timer, and `duel_choice` is the
  first live in-game client action. See
  [DUEL_MODULE_SPEC.md](DUEL_MODULE_SPEC.md), GAME_DESIGN §2b.
  **AC:** the match refuses to start with a lone Duelist; neither Duelist ever
  receives the other's move before the round resolves; a losing team is held
  even when otherwise fully green. · [ALL]

- [x] **V10 Rejoin by code** — a seat survives the browser that claimed it. Every
  player is given a short readable `rejoin_code` at join;
  `POST /api/matches/{id}/rejoin` trades it for the original `player_id` at any
  point in a match, and the client session moved from `sessionStorage` to
  `localStorage` so closing a tab no longer loses it. The engine already held a
  disconnected seat indefinitely, so this is identity recovery only:
  `rejoin` mutates nothing and `on_reconnect` stays the one path that touches
  match state. Rebinding to the **existing** `Player` is what keeps the frozen
  `roster_size` correct and leaves a live duel's id-keyed sides intact.
  **AC:** a lost seat no longer blocks its team from advancing for the rest of
  the match; a rejoin code reaches only its own player and their own
  Grandmaster; a deliberate exit (kicked, cancelled) still clears the session.
  · [C][F]

- [x] **V11 Staked duels (BID WAR)** — the Duelist's coins now come out of the
  team purse instead of appearing from nowhere. Each Duelist asks their
  Grandmaster for a stake (`request_stake`) and the Grandmaster answers with any
  amount they choose (`answer_stake`); the two purses are **deliberately
  unequal**, the grant is spent win or lose, and only winnings return. Lots are
  worth coins and are rolled against what both seats still hold, so there is no
  ladder to count and no lookahead to publish. A staked duel is fought **once**
  per level. Needed a `DuelModule` extension (`staked`, `new_duel(seed, stakes)`,
  `settlement`) and a new pre-duel phase (`PendingStake`, the `duel_stake`
  timer). See [DUEL_MODULE_SPEC.md](DUEL_MODULE_SPEC.md) §Staked duels,
  GAME_DESIGN §2b.
  **AC:** an absent Grandmaster cannot stall both teams (the window lapses into
  a default); a staked duel is never dealt to teams that cannot fund one; the
  opposing stake never reaches the other side, through the snapshot **or** the
  event feed.
  **Provisional:** `DUEL_STAKE_POOL_MULTIPLIER` and the widened perk costs —
  see [PLAYTEST_GUIDE.md](PLAYTEST_GUIDE.md). · [ALL]

- [x] **V12 God mode (dev only)** — a seat that runs a match without playing in
  it. Every viewer used to be a `Player`, and a player who never plays blocks
  the start outright, so the person running a session had nowhere to sit. An
  `Observer` lives in `match.observers`, outside `match.players` and
  `team.player_ids` — which is where every rule that counts, seats, gates and
  advances people looks, and is what keeps the change small. It holds the host's
  controls without holding the host *seat* (`claim_host` decides whether that is
  free by looking its holder up in `match.players`, so an observer there would
  let any player seize it), can name either team's Grandmaster over a seated
  one, and reads both teams whole in every status — through Silence, with the
  leader-only events, and both sides of a stake. Watching a Grandmaster's board
  is `renderLeader` handed a projection of the God's own snapshot, never a
  borrowed credential. Password: `RELAY_GOD_KEY`, separate from the gallery's.
  See [GOD_MODE.md](GOD_MODE.md).
  **AC:** an observer costs no seat and blocks no start; nothing in any player's
  snapshot or event feed reveals one exists; the God holds host controls without
  holding the host seat; watching a Grandmaster sends nothing and never touches
  the WebSocket credential. · [C][F]

- [x] **V13 Link duels (/explore rooms)** — the four duels were the only games
  nobody could try: half of a duel is not knowing what the other person just
  did, so it can never be a solo board, and the only way to play one was to
  assemble two full teams and start a match. Now `/explore` hands you a room and
  a link. A `DuelRoom` is deliberately not a `Match` (the God-mode lesson:
  `match.status` is read in 34 places, and a room faking one would give every
  guard a second meaning), and it reuses `DuelSession` verbatim so all four
  renderers work unchanged. The scoring moved to `backend/duelloop.py` so a duel
  behaves identically in a room and in a race. BID WAR is fought on an equal
  grant from config and pays no settlement. See
  [DUEL_ROOMS.md](DUEL_ROOMS.md).
  **AC:** a room carries no team, currency, level or perk in any state; a
  shared link never contains the sharer's seat id; a disconnect forfeits the
  round rather than pausing it; the two keyspaces cannot evict each other; and
  the match duel path is untouched — its whole suite passes unchanged.
  **Provisional:** `DUEL_ROOM_STAKE` — see
  [PLAYTEST_GUIDE.md](PLAYTEST_GUIDE.md). · [ALL]

### v2 stretch (only after V5–V7)

- [ ] More games for the library (see `game/RELAY_EXPANSION_GAMES_README.md`) —
  every new game widens the library the Grandmaster picks from.
  **THREADLINE** (game 10, the Logician's second game) shipped: backend module,
  renderer, Python/JS parity fixture, docs. That completes the expansion spec's
  **Done (V10).** `payload["time_limit_seconds"]` turned out to be exactly the
  capability flag this note asked for, and `_apply_attack` now reads it. Rather
  than skipping a timed victim — which would have made the Defuser, a *required*
  role, permanently immune to two of the five attacks — both perks were made to
  land properly: **Freeze** pushes the board deadline out by however long it
  locks the player out (the overlay covers the whole screen, so it was costing
  the board rather than the ten seconds it is priced at), and **Scramble**
  serves its fresh board on the *old* board's deadline (a fresh clock rescued a
  victim who was eighty seconds into a ninety-second board — the attack was
  measurably helping). Screen effects were and remain safe by construction.
  The shell also holds a submitted answer back while its player is frozen
  instead of letting the server discard it, which a game that submits once at
  the end — the bomb — otherwise loses outright. · [C]

  §18 first wave (MIRROR RUN, OVERPRINT, STACKDROP, LANE SHIFT, SHADOW CAST,
  THREADLINE); GRAVITY SHIFT, PRESSURE VALVES, SIGNAL BUFFER, TETHER, FOLDLINE
  and ORBIT SYNC are the next batch.
  **BOMB DEFUSE** (game 11, the Technocrat's third game) shipped from
  [`bomb.md`](../bomb.md): backend module, renderer with the bomb face, four
  puzzle bays and the full Expert manual, Python/JS parity fixture, docs. It is
  the first game to carry a fuse of its own and the first adapted from a
  two-player design — see the rules note in [GAMES_SPEC.md](GAMES_SPEC.md) for
  the single-seat adaptation and the two unenforceable clocks it accepts.
  **The second seat landed next**: `bomb_defuse` moved to a new **required**
  Defuser role (every team fields one — the bomb is the game no team opts out
  of), and the manual moved onto the Grandmaster's dashboard as the bomb
  console. Then **banks** (rules version 2 — a board escalates into a second
  bank of bays on a fresh fuse, on the bonus tiers) and a **practice mission
  ladder** (authored boards, practice-only by rule).
  **The second seat is now load-bearing**: from level 8 the board withholds one
  manual page from the Defuser's own copy (the console holds the only copy in
  the match), Silence jams the console, and on the bonus tiers the *clock* moves
  to the console too — the Defuser's face reads `--` and keeps no fuse. That
  answers the open question and reverses the old "a Grandmaster never strands
  their Defuser" rule, which GAME_DESIGN.md §2c and GAMES_SPEC.md now state the
  other way round. The **dark fuse** rides on the engine's new opt-in board deadline
  (`payload["time_limit_seconds"]`, GAME_MODULE_SPEC.md §6), which is also
  §0.4's stretch hardening landing for the first time.
- [x] More **duel** games for the Duelist role (see
  [DUEL_MODULE_SPEC.md](DUEL_MODULE_SPEC.md)) — same rules, different time
  costs. The engine picks between whatever is registered. **CROWN DUEL**,
  **NUMBER CLASH** and **BID WAR** join RPS DUEL, from the duel-mode handoff
  spec. All three carry state between rounds, which the duel contract could not
  hold before: `DuelState.private` (server-only working state) and the seat
  passed to `normalize_choice` are the two additions, and they let a module
  score its own match instead of the engine counting round wins.
  **AC:** the opponent never learns a Crown Duel hand or what a Royal Sacrifice
  did, only that one happened; Bid War never publishes the prize order past the
  next lot; every duel still resolves on a lapsed window rather than stalling.
- [ ] PHASE LOCK / RHYTHM LOCK / BALANCE HOLD, the three expansion games marked
  REQUIRES ENGINE EXTENSION — V9 built the live-action seam they were waiting
  on (`duel_choice` + server-fired deadlines), so they are no longer blocked.
- [ ] Mid-match Grandmaster *claim* when the Grandmaster is long-disconnected.
- [ ] Spectator/dashboard view for non-players — a *public* one. V12's God board
  is the developer's version of this and is password-gated on purpose; a
  spectator seat anyone can take is a different question about what an audience
  should be allowed to see.

---

## Part 2 — MVP rebuild (complete; kept for history)

The original phased plan that produced the engine, the first six games, and the
frontend shell. All tasks below are done; task ids (T0.x–T6.x) are still
referenced by tests and old PRs. The v1 mechanics described here
(stages/rest/holding) were replaced by v2 — read them as history, not rules.

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
> **G1 = REWIRE** (puzzle), **G2 = SWEEP** (logical), **G3 = DECANT** (sorting),
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
  (documented exceptions: ECHO's `sequence`, SWEEP's `clues` grid) and
  solvable-board.
- [ ] ~~**T4.x.5 Playtest note**~~ — superseded by **V6/V7** (wait-timer and
  economy tuning replaced rest/holding tuning).

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

- [ ] ~~**T6.1 Full 8-player playtest**~~ — superseded by **V6**.
- [ ] ~~**T6.2 Timer tuning**~~ — superseded by **V7**.
- [x] **T6.3 Docs sync** — done as part of **V4**.

## Phase 7 — Stretch (superseded by the v2 plan above)  ·  owner: [ALL]

- [ ] Attempt cap / lockout on main puzzles.
- [x] ~~Randomised game order or a 5th game~~ — games 5–9 (MIRROR RUN, OVERPRINT,
  STACKDROP, LANE SHIFT, SHADOW CAST) shipped; per-player assignment replaced the
  fixed order.
- [x] ~~Rejoin-by-code UX niceties~~ — shipped as **V10** above, including the
  join-screen rejoin panel and the offer-to-rejoin path on a dropped socket.

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
| **[G2]** SWEEP (logical) | same, T4.2.* | " |
| **[G3]** DECANT (sorting) | same, T4.3.* | " |
| **[G4]** ECHO (reflex/memory) | same, T4.4.* | " |
| **[F]** Frontend | Stub against [WEBSOCKET_PROTOCOL.md](WEBSOCKET_PROTOCOL.md), then T5.* | Split T5.1–T5.5 across two people, or fold into Core if needed. |

**Critical path:** T1.3 (`games/base.py`) unblocks all four game lanes — Core should
land it early. The Frontend can start against the protocol doc before the backend is
finished. Games need nothing from the engine to be built and tested. Everything else
can run concurrently within the two limits above.

Related: [GAME_DESIGN.md](GAME_DESIGN.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md) · [WEBSOCKET_PROTOCOL.md](WEBSOCKET_PROTOCOL.md) · [CONTRIBUTING.md](CONTRIBUTING.md)
