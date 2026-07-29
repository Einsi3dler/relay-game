# The Relay v2 — Leaders, Levels, Currency & Perks (redesign plan)

Status: **approved, in implementation**. This document is the source of truth for
the v2 redesign that **replaces** the MVP relay mode. When it conflicts with an
older doc, this one wins until the docs-sync phase lands (see Phases below).

## Context

The six-stage MVP relay is complete (158 tests green). v2 replaces that mode with
the full game design: each team gets a non-playing **leader** who assigns a
distinct game to each teammate, the team climbs **10 levels** together
(all-clear gate per level), fast solvers choose **wait or bonus**, clears earn
**team currency**, and the leader spends it on **attack/defense perks** from an
observer dashboard. Games are grouped under placeholder **roles**. The old
rest/holding-question mechanic is removed.

The frontend and WS protocol are already per-player-shaped (the client mounts
whatever `me.current_puzzle.game_id` says; snapshots are personalised per
socket), so the redesign concentrates in `backend/engine.py`,
`backend/models.py`, config/registry, protocol shapes, and the frontend views.

## Locked design decisions

1. v2 **replaces** the current mode entirely.
2. Team = configurable `PLAYERS_PER_TEAM` playing members **+ 1 leader**
   (leader claimed in lobby; required per team to start).
3. Leader assigns one game per teammate in the lobby; **no duplicate games
   within a team**.
4. `LEVEL_COUNT = 10`; the whole team must clear the level to advance; first
   team to clear level 10 wins.
5. Cleared players hold a **3-minute wait timer** (`WAIT_SECONDS = 180`);
   expiry ⇒ lose cleared status, re-clear on a fresh puzzle.
6. **Bonus** = harder instance of the player's own game. Taking it un-clears
   them (the team can't advance past them).
7. **Economy** (anti-farming by design):
   - Base clear of a level pays `CURRENCY_PER_CLEAR` **once per player per
     level** (re-clears after losing status pay nothing, but previously earned
     currency is kept).
   - Bonus success ⇒ back to **cleared with a fresh wait timer and a new
     wait-or-bonus choice** (bonuses chain). First bonus of a level pays
     `CURRENCY_BONUS_FIRST`; each subsequent bonus that level pays
     `CURRENCY_BONUS_REPEAT` (much smaller — diminishing returns).
   - Bonus **failure or timer expiry while in bonus** ⇒ back to solving AND
     **forfeit all bonus currency earned this level** (base clear pay stays;
     team balance clamped at 0).
8. **Perks**: small real effects, leader-only purchase, team-pooled currency.
   Placeholder catalogue: **freeze** (attack, random opponent input locked
   ~10s), **scramble** (attack, random solving opponent gets a forced reroll),
   **shield** (defense, blocks the next incoming attack), **extend_wait**
   (defense, +60s on a cleared teammate's wait timer). Attack targets are
   **server-picked at random** among valid opponents.
9. **Visibility**: progress information is **leader-exclusive**. Players see
   only their own game/state (teams play on a voice call; they ask their
   leader). Leaders see full own-team detail (incl. who hasn't cleared) plus
   the opponent's **current level and cleared-count only**.
10. **Roles**: placeholder config mapping grouping the existing game ids; real
    role design is future work.
11. **Leader handoff is a real in-game feature**: the leader can give the
    position to a teammate at any time. In the lobby it simply moves the flag
    (the new leader's game assignment is cleared; the old leader becomes
    assignable). Mid-match it is a **full swap**: the recipient stops playing
    and becomes leader; the old leader takes over the recipient's assigned game
    at the team's current level with a **fresh puzzle, un-cleared** (a cleared
    recipient's clear is lost — handoff has a real cost). Limited to **once per
    team per level**. The old leader inherits the recipient's economy counters
    (`earned_level`, `bonus_streak`, `bonus_earned`) so the same level can't
    pay base currency twice.

## New player state machine

`lobby | solving | cleared | bonus | leading | finished`
(replaces `solving/resting/holding`)

```
start:            players → solving (their assigned game, level 1); leader → leading
solving  --correct-->  cleared   [choice_pending=True; wait timer WAIT_SECONDS;
                                  +CURRENCY_PER_CLEAR if first clear of this level]
solving  --wrong---->  solving   [fresh instance, attempt+1]
cleared  --choose_wait-->    cleared  [choice_pending=False; timer keeps running]
cleared  --choose_bonus-->   bonus    [keeps the SAME running wait deadline as bonus deadline;
                                       puzzle = own game at level+BONUS_LEVEL_OFFSET]
cleared  --wait expiry-->    solving  [lose cleared; fresh puzzle; keep earned currency]
bonus    --correct-->  cleared  [+bonus pay (first/repeat); fresh wait timer; choice again]
bonus    --wrong or timer expiry--> solving  [forfeit this level's bonus earnings]
team all cleared --> level+1 (everyone fresh puzzle, own game) or win at LEVEL_COUNT
```

- `green(player)` ⇒ `status == "cleared"`. A bonus player is not green, which
  enforces "bonus must finish before the team advances" through the existing
  all-clear gate for free.
- The wait timer starts **at the moment of clearing** (not at choice time) so
  stalling the choice can't hold green forever. `choice_pending` is engine
  state so the choice overlay is derivable purely from the snapshot.
- **Freeze is not a status** — `Player.frozen_until` is a deadline checked
  lazily on submit; it composes with any state and needs no timer task.
- Leaders are excluded from the advance gate, serving loops, `roster_size`,
  and green counts.

## Implementation phases (one PR each)

### Phase 0 — This document

Commit the plan as `docs/REDESIGN_PLAN.md`; link it from `docs/TASK_LIST.md`
and the README doc map.

### Phase 1 — Non-breaking prep (all existing tests stay green)

- `backend/config.py` — add `LEVEL_COUNT=10`, `WAIT_SECONDS=180`,
  `CURRENCY_PER_CLEAR=1`, `CURRENCY_BONUS_FIRST=3`, `CURRENCY_BONUS_REPEAT=1`,
  `BONUS_LEVEL_OFFSET=3`, `PERKS` (freeze/scramble/shield/extend_wait with
  kind, cost, seconds), `ROLES` placeholder dict grouping the six game ids.
- `backend/games/base.py` — `generate_main(self, seed: int, level: int = 1)`.
  All six game modules + `template.py` accept-and-ignore `level` for now
  (per-game difficulty curves are follow-up tasks). `generate_holding` stays
  (practice mode uses it) but is marked practice-only.
- `backend/registry.py` — add `by_id(game_id)`, `has(game_id)`, `library()`
  → `[{id, name, role}]` (role from `config.ROLES`). `for_stage` survives
  until Phase 2.
- Tests: extend `tests/test_registry.py`; one `level=5` assert per game suite.
  Update `docs/GAME_MODULE_SPEC.md` §2.

### Phase 2 — Core loop replacement (engine/models/protocol/server + rewritten backend tests)

`backend/models.py`
- `Player`: new status enum, `is_leader`, `assigned_game`, `choice_pending`,
  `current_bonus` (replaces `current_holding`), `timer_kind: "wait" | None`,
  `frozen_until`, `earned_level=0`, `bonus_streak=0`, `bonus_earned=0` (this
  level's forfeitable bonus pay). `current_puzzle()`: solving→main,
  bonus→bonus. `green()` = cleared.
- `Team`: `stage` → `level`, plus `currency`, `shield_active`, `leader_id`,
  `handoff_used_level=0`; `roster_size` counts playing members only.
- Visibility in `Match.public(viewer)`: **leader viewer** → own team full
  (players, currency, shield) + opponent limited
  `{id, name, level, roster_size, green_count, finished}`; **player viewer** →
  own team `{id, name, level, roster_size, finished}` only + `me`; lobby
  status → full views for everyone (assignment UI needs rosters).
  Clear/lost-cleared events route to leaders only; advance/win/perk events to
  all.

`backend/engine.py`
- Lobby: `join_match` capacity = `PLAYERS_PER_TEAM + 1` per team;
  `claim_leader` (one per team; clears their `assigned_game`; team-switch/kick
  clears leadership); `assign_game(leader, target, game_id)` with
  `registry.has` + within-team uniqueness; `start_blocker` requires per team:
  a leader, playing count within `min_players..PLAYERS_PER_TEAM`, every player
  assigned.
- `start_match`: leaders → `leading`; players → `_serve_main` from
  `registry.by_id(player.assigned_game)` at `level=team.level`. All four
  `registry.for_stage` call sites die.
- `submit_answer` — single path replacing `submit_main`/`submit_holding`,
  guarded by status ∈ {solving, bonus} and the lazy `frozen_until` check;
  branches per the state machine, including the economy rules (first-clear pay
  via `earned_level`; bonus pay via `bonus_streak`; forfeit via `bonus_earned`
  with a `max(0, ...)` clamp on team currency).
- `choose_wait` / `choose_bonus`; `on_wait_expired` (replaces both old expiry
  hooks — status `cleared` → lose cleared; status `bonus` → bonus failure with
  forfeit; stale-timer no-op guard as today).
- `_advance_check` over `_playing_members(team)`; win at
  `level >= level_count` (everyone incl. leader → finished); advance resets
  `choice_pending`/`bonus_streak`/`bonus_earned` and serves fresh puzzles.
- `buy_perk(match, leader_id, perk_id, target_id=None, now)`: validate → apply
  → charge (no charge on reject); shield consumption blocks attacks; freeze
  sets `frozen_until` (no timer task); scramble = forced `_serve_main` on a
  random solving opponent; extend_wait reschedules the target's wait timer.
- `give_leader(match, leader_id, target_id)`: caller must be the team's
  leader; target a teammate. Lobby: move `is_leader`/`team.leader_id`, clear
  the new leader's `assigned_game`, leave the old leader unassigned. Active
  match: enforce `team.handoff_used_level != team.level`; full swap —
  recipient → `leading` (puzzles/timers cleared, wait timer cancelled), old
  leader → `solving` on the recipient's `assigned_game` with a fresh instance
  at `team.level`, inheriting the recipient's economy counters; run
  `_advance_check` afterwards.
- `on_reconnect`: fresh instance when `solving` **or `bonus`** (anti-replay).
- Config snapshot: `{wait_seconds, level_count, players_per_team, currency_*,
  bonus_level_offset, perks}`.
- `backend/timers.py` unchanged — the only scheduled deadline left is the
  wait/bonus deadline (one per player, existing invariant holds); freeze is
  lazy. Any future second concurrent deadline must be lazy too, or the timer
  key must grow.

`backend/protocol.py` + `backend/main.py`
- Client→server: `submit_answer` (delete `submit_holding`), `choose_wait`,
  `choose_bonus`, `buy_perk {perk_id, target_id?}`, `give_leader {target_id}`
  (valid in lobby and active match), `lobby_action` += `claim_leader`,
  `assign_game {target_id, game_id}`.
- Server→client: `level_advanced {team_id, level}` (replaces
  `stage_advanced`), `perk_used {perk_id, by_team_id}`; snapshot/error/event/
  match_won unchanged in envelope.
- `main.py`: timer hook map → `{"wait": on_wait_expired}`; dispatch new
  messages under the match lock (submits keep the `_too_fast` throttle);
  `/api/config` gains `level_count`, `wait_seconds`, `perks`, `roles`,
  `library: registry.library()`.
- Delete from `config.py`: `REST_SECONDS`, `HOLDING_SECONDS`,
  `MAIN_PUZZLE_SECONDS`, `STAGE_COUNT`, `GAME_ORDER`; delete
  `GameRegistry.for_stage`.

Tests (rewritten in place, same patterns — FakeGame, registry override,
explicit `now=NOW`): leader claim/uniqueness/switch; assignment rules; start
gating; clear→wait-timer deadline; wait expiry; choose_wait/choose_bonus;
bonus chain economy (first vs repeat pay, forfeit on fail and on expiry, clamp
at 0, keep-base-pay after wait expiry); advance excludes leader & blocks on
bonus player; win at LEVEL_COUNT; every perk incl. shield consume, freeze lazy
rejection around the deadline, no-valid-target no-charge; leader handoff
(lobby move + assignment reset; mid-match full swap incl. lost clear, fresh
puzzle at current level, economy counters transferred, once-per-level limit,
advance check re-runs after swap, non-leader caller rejected). Plus
`test_models.py` (new statuses, per-viewer snapshot shapes, no-answer-leak
incl. `current_bonus`), `test_timers.py` (kinds → `wait`), `test_server.py`
(new dispatch + `/api/config` shape), `test_registry.py` (drop `for_stage`).

### Phase 3 — Frontend (land immediately after Phase 2; play is broken in between)

- `frontend/index.html`: lobby claim-leader button + leader badge +
  leader-only assignment panel (select per teammate from
  `/api/config.library`, grouped by role, taken options disabled); play view
  drops `#rest-card`, gains cleared-card + wait countdown, wait/bonus choice
  overlay (`me.status=="cleared" && me.choice_pending`), bonus badge, frozen
  overlay; new `#view-leader`: own-team progress list (name, game, status,
  cleared dot), team level + currency, opponent level/cleared chip, perk shop
  grid (disabled when unaffordable; extend_wait opens a teammate picker), a
  "give leadership" control (teammate picker + confirm; disabled once used
  this level), event feed. The lobby leader panel also gets a give-leadership
  option.
- `frontend/app.js`: route active leaders to `view-leader`; players' play view
  shows only their own game + level number (no team strips, per visibility
  ruling); `mountPuzzle` always sends `submit_answer` (renderer registry
  untouched — it already mounts by `game_id`); countdown kind `wait`; handle
  `level_advanced` overlay + `perk_used` toast; buy_perk wiring.
- `frontend/style.css`: choice/frozen overlays, perk cards, leader dashboard.
- Copy sweep: stale "five games"/stage copy in index.html, app.js, landing/
  games pages.

### Phase 4 — Docs sync

- `docs/GAME_DESIGN.md` full rewrite of §2–§9 (leaders, levels, wait/bonus
  economy, perks, leader handoff rules, visibility, disconnect rulings: leader
  offline ⇒ no purchases or handoff until reconnect; bonus reconnect ⇒ fresh
  instance).
- `CLAUDE.md`: update the "no power-ups, economy, sabotage, extra roles" scope
  line and config defaults.
- `docs/WEBSOCKET_PROTOCOL.md` (messages + shapes + visibility rules),
  `docs/ARCHITECTURE.md` (§3 model, §4 timer invariant),
  `docs/GAME_MODULE_SPEC.md` (level param), `docs/TASK_LIST.md` (new phase
  list incl. per-game level-difficulty tasks and future real-roles design),
  `README.md`.

## Verification

1. `python3 -m pytest` green at every phase merge; CI runs it on PRs.
2. Manual smoke via `./run.sh`, two browsers / 4+ tabs, `min_players` lowered:
   claim leaders, verify start blockers (no leader / unassigned game), assign
   distinct games, start; clear → choice overlay; wait-expiry (puzzle returns,
   currency kept); bonus success chain (diminishing pay, new choice); bonus
   failure (forfeit visible on the leader dashboard); team blocked while
   someone is in bonus; buy each perk (freeze rejection on submit, scramble
   reroll, shield block, extend_wait countdown moves); mid-match leader
   handoff (roles swap, old leader gets a fresh puzzle, second handoff same
   level rejected); `LEVEL_COUNT=2` locally to smoke the win screen; refresh
   mid-match in each state (fresh puzzle when solving/bonus; preserved wait
   timer when cleared).

## Follow-ups (explicitly out of this build)

- Per-game difficulty curves for `level` (six one-per-owner tasks; until then
  the bonus is mechanically real but not actually harder).
- Real role definitions (the placeholder `ROLES` mapping ships now).
- More games for the library; perk balance and economy tuning from playtests.
- Role-based assignment constraints (e.g. one game per role per team) once
  real roles are defined.
