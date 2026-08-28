# BOMB DEFUSE — finishing the two-seat design

> **✅ Delivered.** Items 0–4 are all built and on `main`; this document is kept
> as the record of *why*, not as a queue. Two decisions moved during the build
> and the reasons are in the commits: **item 2** withholds a page from level 8
> (main-ladder tiers, not bonus-only), and **item 4** blacks out from level 11
> rather than 10, so that blackout lines up exactly with banks and no
> multi-bank board is left without a readable fuse. The only thing still open
> from this work is the `_apply_attack` timed-victim policy noted in
> [TASK_LIST.md](TASK_LIST.md). "Still dropped" below is still dropped.
>
> **Original handoff note.** This was a work order for a fresh session.
> Everything it describes was unbuilt; everything under "Already shipped" was on
> the branch and needed no attention. Read [CLAUDE.md](../CLAUDE.md) first — it
> is binding, and RULE 0 (pull before you work) applies before touching
> anything.
>
> - **This document:** `docs/BOMB_TWO_SEAT_PLAN.md`
> - **Repo:** `/home/einsiedler/Relay`
> - **Branch:** `games/bomb-grandmaster` (pushed; branched off
>   `games/bomb-defuse`, which is itself not yet merged to `main`)
> - **Baseline:** `python3 -m pytest` → **582 passing**. Use `.venv/bin/python`.
> - **Orientation:** [GAME_DESIGN.md](GAME_DESIGN.md) §2c (the Defuser, and the
>   Grandmaster as Expert) → the BOMB DEFUSE rules note in
>   [GAMES_SPEC.md](GAMES_SPEC.md) → the module docstring in
>   `backend/games/game11_bomb_defuse.py`.

## Already shipped (do not redo)

Four commits on the branch, in order:

1. **`bomb.md` → the game.** `backend/games/game11_bomb_defuse.py` +
   `frontend/games/bomb_defuse.js`: four bay types (maze, Simon, according-to-
   number, mini button), sudden death, the fuse, the manual, synthesised audio.
2. **The Defuser role.** `bomb_defuse` left the Technocrat for a new **required**
   role — every team fields exactly one or the lobby refuses to start. Config
   flags `fixed` (the role names its own game) and `required`, mirrored
   client-side in `startBlocker`. The gate only bites when `bomb_defuse` is
   actually registered, which is why the fake-registry suites still pass.
3. **The console.** The manual moved to `frontend/games/bomb_manual.js`, drawn
   by both the Defuser's own view and a card on the leader dashboard. No backend
   at all — a static manual has nothing to sync.
4. **Banks + missions.** Rules version 2: a board is a list of banks, each with
   its own fuse; OK shuts one and arms the next. Levels 1–10 are single-bank,
   bonus tiers 11–13 escalate. Plus seven authored practice missions, served via
   `kind=<mission id>` and listed at `GET /api/practice/{game_id}/missions`.

## Context

The five items below finish the design: they make the second seat *necessary*
rather than merely convenient, close the larger of the two honesty gaps
documented in [GAMES_SPEC.md](GAMES_SPEC.md), and put the app shell under test
for the first time.

Two facts from the code shape the work:

- **`_start_scope_timer` already exists** (`backend/engine.py:1093`): it
  schedules a deadline on an arbitrary scope *without* touching
  `player.timer_*`, which is exactly what a per-puzzle fuse needs. The engine
  work is plumbing, not new machinery.
- **`frontend/app.js` is never executed by a test** — only regex-scanned by
  `tests/test_perk_frontend_parity.py`. The console wiring already shipped is
  behaviourally untested, and so is everything below that touches the shell.

Build in the order given: the harness first, because three later steps land
tested only if it exists.

---

## 0. An execution harness for `app.js` (~150 lines)

New `tests/test_app_shell.py`, following the pattern the game renderers already
use (`tests/games/test_game11_bomb_defuse_renderer.py`): the real file in node,
a fake DOM, a virtual clock.

The shell reaches for more of the DOM than a renderer does — `getElementById`
over a fixed id set, `querySelectorAll`, `classList`, `hidden` — so the fake
needs a small element registry seeded from the ids in `frontend/index.html`
rather than a bare `createElement`. Parse the ids out of index.html so a renamed
element fails the test instead of silently creating a stub.

Cover what exists today: `renderBombConsole` mounting and tearing down,
`startBlocker`'s mirrored lobby rules (including the Defuser rules added in this
branch), and `startCountdown`.

## 1. Silence blanks the console (~30 lines)

A silenced Grandmaster already loses the roster and the who-cleared feed; the
manual goes with them.

- Gate the card in `renderBombConsole` on `team.silenced_until`, showing the
  same "🔇" treatment `statusPill` uses for hidden players.
- `watchSilence` (app.js:951) already re-requests state at the deadline, so the
  console returns on its own — no new timer.
- Docs: [GAME_DESIGN.md](GAME_DESIGN.md) §2c, and the `silence` entry in
  `config.PERKS`.

## 2. The Defuser's manual thins out (~300 lines)

Makes the Grandmaster **necessary** on deep boards rather than merely faster.

- `backend/games/game11_bomb_defuse.py`: a per-level knob drawn per
  `(seed, level)`, emitting `payload["withheld_pages"]`. It must only ever name
  a page for a bay actually on the board — withholding a page for a bay that is
  not there reads as a bug, not as difficulty.
- `frontend/games/bomb_manual.js`: `render` takes a `withheld` option; those
  selector entries grey out and say the Grandmaster holds that page. The console
  never passes the option, so its copy stays whole.
- Tests: withheld pages always match a live bay, deterministic per seed, absent
  below the threshold level; the manual greys them and still navigates the rest.

**Decision made, and it reverses a shipped rule.** Until now: "a Grandmaster who
is busy, silenced or disconnected only ever slows their Defuser down — they
never strand them." On deep boards they now do. [GAME_DESIGN.md](GAME_DESIGN.md)
§2c and the rules note in [GAMES_SPEC.md](GAMES_SPEC.md) both state that rule and
both need rewriting, not just amending.

Note this compounds with item 1: a silenced Grandmaster on a board with withheld
pages leaves their Defuser genuinely stuck for the duration of the perk. That is
intended, but it is worth a sentence in the docs so it does not read as a bug.

## 3. Server-authoritative fuse (~650 lines)

Implements the "hard per-puzzle limit" that [GAMES_SPEC.md](GAMES_SPEC.md) §0.4
already names as the stretch hardening for the whole library.

**Banks force its shape.** A bank arming is a client-side event, so a *per-bank*
server deadline would need the client to report it — client-claimed time, the
exact thing we refuse to trust. What the server can own with no new channel is a
**total budget** for the board: the sum of the bank fuses. The client keeps the
per-bank countdown for drama; the server holds one backstop an honest player
never reaches. Say this plainly in the docs — it is a narrower fix than "the
fuse is now enforced."

- `models.Player` gains `puzzle_deadline`, surfaced in `private()`.
- `backend/engine.py`: schedule in `_serve_main` via `_start_scope_timer` on a
  `fuse:<player_id>` scope (player ids are uuid hex, so the prefix cannot
  collide with a bare player scope); cancel on clear, on submit, and on
  re-serve; a new `on_puzzle_expired` that serves a fresh board exactly as a
  wrong answer does.
- `backend/main.py`: `_timer_fired` routes the new kind — it currently sends
  everything non-`duel_` to `on_wait_expired`.
- `frontend/app.js`: draw it on the **existing** timer bar. A solving player
  holds no wait timer, so the bar is free.
- The bomb renderer takes the deadline from the server instead of computing
  `Date.now() + fuse * 1000`.
- Keep it generic: the engine reads an optional payload key, so any game can opt
  in later. Document it in [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md).

**Where the risk is:** not the timer, but its interaction surface — freeze,
scramble, the bonus deadline, reconnect (which already re-serves a board), and
level advance. That is where the tests go. This touches the **Core/engine
slice**, so it needs flagging in the PR per [CLAUDE.md](../CLAUDE.md).

## 4. Blackout (~120 lines)

Only the Grandmaster sees the fuse. Item 3 is what makes this honest: once the
deadline is real server state, the snapshot routes it to one seat and withholds
it from the other — a visibility rule, not a sync channel.

- Server-side: the Defuser's `private()` omits `puzzle_deadline` on a blackout
  board; the leader's console view carries it instead.
- The console grows its first live element — a countdown — which is a departure
  from "the manual and nothing else" and should be written up as such.
- Gate it behind a level threshold or a scenario flag on the payload, so
  ordinary boards are unchanged.

## Still dropped

**Crossed Wires** (the seats swap mid-round) needs live board state on both
screens — a `PairSession`-shaped change that contradicts the manual-only design.
Nothing in items 0–4 brings it closer.

---

## Verification

- `python3 -m pytest` — 582 green now; item 3 is the only one that should move
  the count much.
- **Parity:** `test_game11_bomb_defuse_parity.py` must keep passing throughout.
  Item 2 touches the manual's render signature, not its tables, so
  `tests/games/fixtures/bomb_defuse_cases.json` should not move at all.
- **Hit test:** every new console and manual state goes through the reachability
  checks in `test_game11_bomb_defuse_renderer.py` and `test_bomb_manual.py` — a
  greyed selector entry is still a control a click has to reach, or deliberately
  not reach.
- **Engine (item 3):** a lapsed fuse serves a fresh board and disturbs neither a
  wait nor a bonus deadline; freeze, scramble, reconnect and level advance all
  leave it consistent.
- **Manual smoke:** `./run.sh`, two browsers — Grandmaster and Defuser — through
  a level-11+ board, the only tier that fields two banks. Confirm the console
  blanks under Silence, that a withheld page sends the Defuser to ask, and that
  the server fuse expires a board the client has been told to ignore.

## Traps worth knowing before you start

Each of these cost real time on the branch already; none are obvious from the
code.

- **Full-surface overlays eat clicks.** The bomb face was once completely dead
  because an empty `position:absolute` panel layer spanned the whole surface
  above it. Renderer tests that fire handlers on a node found by label cannot
  see this. `test_game11_bomb_defuse_renderer.py` now carries a hit test that
  walks real geometry and paint order — put any new surface through it, and
  check a new hit test *fails* when you break it deliberately, because the first
  version of that check silently passed (its CSS parser required a `px` unit, so
  `left:0` read as "no box").
- **The renderer harness owns `Date.now`.** Advancing the virtual clock by
  `undefined` sets it to `NaN`, and every later scenario then silently does
  nothing while appearing to pass. If scenarios start failing in a block, check
  what the *earlier* ones advanced by.
- **`fill_match` in `tests/test_server.py`** blocks forever on a refused start
  unless the error branch is kept — a lobby rule change is the usual cause. This
  hung the whole suite once.
- **Python and JavaScript must agree exactly.** `validate` is mirrored in
  `frontend/games/bomb_defuse.js`; `tests/games/fixtures/bomb_defuse_cases.json`
  locks them together. Regenerate it only if the encoding genuinely changes, and
  never edit it by hand.
- **Level-curve invariants are load-bearing.** The fuse test compares levels of
  the same bank shape and requires a bigger bomb to buy time back; it has
  already caught one accidental difficulty step in the table.
- **`frontend/games/bomb_manual.js` loads before `bomb_defuse.js`.** The bomb
  binds to the manual's tables at module scope, so the script order in
  `index.html` / `explore.html` and in both node harnesses matters.
