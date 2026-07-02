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
| **Game 1 — REWIRE** | Stage-1 game (module + renderer) | `backend/games/game1_*.py`, `frontend/games/rewire.js`, `tests/games/test_game1_*.py` |
| **Game 2 — SWEEP** | Stage-2 game (module + renderer) | `backend/games/game2_*.py`, `frontend/games/sweep.js`, `tests/games/test_game2_*.py` |
| **Game 3 — DECANT** | Stage-3 game (module + renderer) | `backend/games/game3_*.py`, `frontend/games/decant.js`, `tests/games/test_game3_*.py` |
| **Game 4 — ECHO** | Stage-4 game (module + renderer) | `backend/games/game4_*.py`, `frontend/games/echo.js`, `tests/games/test_game4_*.py` |
| **Frontend** | App shell + renderer registry + fallback | `frontend/index.html`, `frontend/app.js`, `frontend/styles.css`, `frontend/games/registry.js`, `frontend/games/fallback.js` |

- Game owners need the **contract** in [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md)
  and your game's section of [GAMES_SPEC.md](GAMES_SPEC.md). You can build and unit-
  test the backend module with no running server; the renderer mounts into the
  shell (or a tiny local HTML harness) via `window.RelayGames`.
- Two shared files must be touched to register a game — `backend/config.py`
  (`GAME_ORDER`) and `backend/registry.py`. Keep those edits to **one line each**,
  call them out in your PR, and expect the Core owner to review them. This is the
  only sanctioned cross-slice edit.
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
- [ ] No new scope that isn't in [TASK_LIST.md](TASK_LIST.md) (no power-ups,
      economy, sabotage, roles — those were cut deliberately).
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

To exercise the relay loop without eight people, temporarily lower
`MIN_PLAYERS_PER_TEAM` (e.g. to 1 or 2) in `backend/config.py`, open that many tabs
per team, and verify:

1. A player who solves goes green and shows a rest countdown.
2. When the rest ends and the team isn't all green, a holding question appears.
3. Failing/ignoring the holding question drops the player back to a new main puzzle.
4. When all players on a team are green, the team advances to the next stage.
5. The first team to finish Stage 4 sees the win screen; the other sees the loss.

Revert the config change before committing.

## 7. Style

- Python 3.11+, type hints, small pure functions. Match the surrounding file.
- Frontend: vanilla JS, no framework/build step for the MVP.
- Keep all tunables in `backend/config.py`. No magic numbers in the engine or games.

Related: [../CLAUDE.md](../CLAUDE.md) · [TASK_LIST.md](TASK_LIST.md) · [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md)
