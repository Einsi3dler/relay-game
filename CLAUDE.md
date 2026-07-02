# CLAUDE.md — rules for AI agents (and humans) working in this repo

This file is loaded automatically by Claude Code and other agents. Read it before
doing anything. It encodes the non-obvious rules of this project. The full human
guide is [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).

---

## 🔴 RULE 0 — PULL BEFORE YOU WORK, PUSH OFTEN

**Multiple people (and their AI tools) are committing to this repo in parallel.**
Stale local state is the #1 way to create conflicts and clobber someone's work.

At the **start of every working session**, before editing anything:

```bash
git fetch origin
git pull --rebase origin main        # or: git pull --rebase origin <your-branch>
git status                           # confirm a clean, up-to-date tree
```

While working:

- Commit in **small, focused** chunks with clear messages.
- **Push at least once per working session**, and again before you stop.
- If a `git pull --rebase` reports conflicts, **stop and resolve them** (or ask the
  human) — never `--force` push to `main` or a shared branch.
- Before opening a PR, rebase onto the latest `main` again.

If you (the agent) are ever unsure whether the tree is current, run the pull block
above again. It is cheap; a merge disaster is not.

---

## What this project is

The Relay: a two-team (4 players each) synchronous relay puzzle race. Read
[docs/GAME_DESIGN.md](docs/GAME_DESIGN.md) for the rules and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the shape of the system. We are
**rebuilding from scratch**; the old prototype is frozen in
[`legacy/`](legacy/README_LEGACY.md).

## Ownership — do not edit code you don't own

Contributors each own a slice (see [docs/TASK_LIST.md](docs/TASK_LIST.md)):

- **Core/engine** — match state, relay gating, timers, WebSocket protocol.
- **Game 1 / Game 2 / Game 3 / Game 4** — one owner per game module.
- **Frontend** — the browser client.

A person (or agent) can hold **more than one** slice — lanes exist to stop two
people editing the same file at once, not to cap how much you take on. Drive each
lane on its own branch/worktree.

Rules:

- Build the tasks for the slice(s) you're **currently driving**, one active editor
  per file. If you need a change in a slice someone else is driving (e.g. a new
  protocol field), **write it up in your PR description** and flag it — don't
  silently edit their active files.
- Game modules must talk to the engine **only through the interface** in
  [docs/GAME_MODULE_SPEC.md](docs/GAME_MODULE_SPEC.md). Never reach into engine
  internals from a game, or into another game from a game.
- **Never import from `legacy/`.** It is reference-only.

## Coding conventions

- Python 3.11+, FastAPI, WebSockets, in-memory state (no DB in the MVP).
- Frontend is vanilla HTML/CSS/JS — **no build step, no framework** for the MVP.
- Match the style already in the file you're editing (naming, comment density,
  type hints). Keep functions small and pure where you can.
- **All timers, team size, and stage config live in one config module**
  (`backend/config.py`) — no magic numbers scattered in the engine. Defaults:
  `REST_SECONDS = 15`, `PLAYERS_PER_TEAM = 4`, `STAGE_COUNT = 4`.
- Every new engine rule and every game module ships with **pytest tests**. A PR
  that changes gameplay without a test will be sent back.

## Before you say you're done

1. `python3 -m pytest` passes.
2. You did a manual smoke check if you touched runtime behaviour (see
   [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md)).
3. You rebased on the latest `main` and pushed.
4. You did **not** invent scope that isn't in [docs/TASK_LIST.md](docs/TASK_LIST.md)
   (no power-ups, economy, sabotage, extra roles — those were cut on purpose).

## Where to start reading

`docs/GAME_DESIGN.md` → `docs/ARCHITECTURE.md` → the spec for your slice
(`docs/GAME_MODULE_SPEC.md` and/or `docs/WEBSOCKET_PROTOCOL.md`) → your tasks in
`docs/TASK_LIST.md`.
