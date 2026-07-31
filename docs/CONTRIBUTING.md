# Contributing to The Relay

Multiple people (often with AI assistants) build here at once. These rules keep us
from stepping on each other. The AI-agent version of the same rules is in
[../CLAUDE.md](../CLAUDE.md).

---

## 1. 🔴 Golden rule: pull first, push often

**Every session, before you touch anything:**

```bash
git fetch origin
git pull --rebase origin main       # get everyone else's work first
git status                          # clean, up to date
```

Then work on a branch, commit small, and **push before you stop for the day**.
Rebase on `main` again right before you open a PR. Never force-push a shared branch.

If a pull produces conflicts, resolve them locally (or ask). Do not paper over them
with a merge you don't understand.

## 2. Pick a lane (ownership)

To let people work simultaneously, the repo is split into slices. Ownership means
**"the single person driving these files right now"** — it prevents two people
editing the same file at once. It is **not** a limit on how much one person takes
on: one person can hold several lanes, and lanes hand off freely when someone frees
up. What limits parallel work is dependencies and "one active editor per file," not
headcount. **You edit your current lane; you don't edit someone else's active
files** without coordinating. To go wide as one person, run each lane on its own
branch/worktree. (See [TASK_LIST.md](TASK_LIST.md) "Can one person work on multiple
things at once?".)

| Slice | Owns | Files |
| --- | --- | --- |
| **Core / Engine** | Rules, timers, state, protocol | `backend/config.py`, `models.py`, `state.py`, `engine.py`, `timers.py`, `registry.py`, `protocol.py`, `main.py`, `games/base.py` |
| **Game 1 — REWIRE** | Game module + renderer | `backend/games/game1_*.py`, `frontend/games/rewire.js`, `tests/games/test_game1_*.py` |
| **Game 2 — SWEEP** | Game module + renderer | `backend/games/game2_*.py`, `frontend/games/sweep.js`, `tests/games/test_game2_*.py` |
| **Game 5 — MIRROR RUN** | Game module + renderer | `backend/games/game5_mirror_run.py`, `frontend/games/mirror_run.js`, `tests/games/test_game5_*.py` |
| **Game 3 — DECANT** | Game module + renderer | `backend/games/game3_*.py`, `frontend/games/decant.js`, `tests/games/test_game3_*.py` |
| **Game 4 — ECHO** | Game module + renderer | `backend/games/game4_*.py`, `frontend/games/echo.js`, `tests/games/test_game4_*.py` |
| **Game 6 — OVERPRINT** | Game module + renderer | `backend/games/game6_overprint.py`, `frontend/games/overprint.js`, `tests/games/test_game6_*.py` |
| **Game 7 — STACKDROP** | Game module + renderer | `backend/games/game7_stackdrop.py`, `frontend/games/stackdrop.js`, `tests/games/test_game7_*.py`, `tests/games/fixtures/stackdrop_cases.json` |
| **Game 8 — LANE SHIFT** | Game module + renderer | `backend/games/game8_lane_shift.py`, `frontend/games/lane_shift.js`, `tests/games/test_game8_*.py`, `tests/games/fixtures/lane_shift_cases.json` |
| **Game 9 — SHADOW CAST** | Game module + renderer | `backend/games/game9_shadow_cast.py`, `frontend/games/shadow_cast.js`, `tests/games/test_game9_*.py`, `tests/games/fixtures/shadow_cast_cases.json` |
| **Frontend** | App shell + leader dashboard + fallback | `frontend/index.html`, `frontend/app.js`, `frontend/style.css`, `frontend/games/fallback.js` |

- Game owners need the **contract** in [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md)
  and your game's section of [GAMES_SPEC.md](GAMES_SPEC.md). You can build and unit-
  test the backend module with no running server; the renderer mounts into the
  shell (or a tiny local HTML harness) via `window.RelayGames`.
- Two shared files must be touched to register a game — `backend/config.py`
  (add the id to a role's `games` list in `ROLES`) and `backend/registry.py`
  (`REGISTERED_MODULES`). Keep those
  edits to **one line each**, call them out in your PR, and expect the Core owner
  to review them. This is the only sanctioned cross-slice edit.
- Need something new from another slice (a protocol field, a config value)? **Ask
  in your PR / the channel** — don't reach into their files.

## 3. Branching & commits

- Branch off the latest `main`: `git switch -c <slice>/<short-desc>`, e.g.
  `game2/signal-sequence`, `core/relay-loop`, `frontend/countdown`.
- Small, focused commits with imperative messages: `Add holding-question timeout`.
- One PR per logical change. Keep PRs reviewable (roughly < 400 lines of diff).

## 4. Definition of done (PR checklist)

- [ ] Rebased on the latest `main`; no conflicts.
- [ ] `python3 -m pytest` passes locally.
- [ ] New/changed behaviour has tests (engine rule or game module — see specs).
- [ ] No new scope beyond [TASK_LIST.md](TASK_LIST.md) /
      [REDESIGN_PLAN.md](REDESIGN_PLAN.md) (v2 includes leaders, currency, and
      the placeholder perks; new perks/roles/curves need a design decision).
- [ ] No import from `legacy/`. No cross-slice edits except the two registration
      lines, which are called out in the PR description.
- [ ] Puzzle answers never appear in any `.public()` / client-visible payload.
- [ ] If you changed runtime behaviour, you did the smoke check (§6).
- [ ] Docs updated if you changed a rule, the protocol, or the module contract.

## 5. Running it

```bash
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -e ".[test]"
python3 -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
python3 -m pytest
```

## 6. Manual smoke check

To exercise the loop without ten people, have the host lower the minimum
players per team in the lobby (each team still needs a Grandmaster + that many
players), open that many tabs per team, and verify:

1. Each team claims a Grandmaster; the Grandmaster assigns each player a role,
   then a game from that role (distinct per team); start unlocks.
2. A player who solves goes **cleared**, sees the wait countdown, and gets the
   wait-or-bonus choice; the team's currency ticks up on the Grandmaster dashboard.
3. Taking the bonus serves a harder board; failing it returns the player to
   solving and claws back the level's bonus pay.
4. Letting the wait timer lapse drops the player back to a fresh board.
5. When all playing members are cleared, the team advances a level (and the
   boards get harder).
6. The Grandmaster can buy each perk (watch the freeze/scramble land on an
   opponent) and can hand the seat to a teammate (full swap) once per level.
7. The first team to clear the last level sees the win screen; the other the loss.

For a fuller playtest (V6/V7), follow [PLAYTEST_GUIDE.md](PLAYTEST_GUIDE.md).

For a shorter run, temporarily set `LEVEL_COUNT = 2` in `backend/config.py` —
revert the config change before committing.

## 7. Style

- Python 3.11+, type hints, small pure functions. Match the surrounding file.
- Frontend: vanilla JS, no framework/build step for the MVP.
- Keep all tunables in `backend/config.py`. No magic numbers in the engine or games.

Related: [../CLAUDE.md](../CLAUDE.md) · [TASK_LIST.md](TASK_LIST.md) · [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md)
