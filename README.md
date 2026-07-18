# The Relay

A synchronous, browser-playable multiplayer **relay puzzle race**. Two teams of
four solve a series of games in parallel. Nobody advances until **all four
teammates are ready** — and staying ready takes effort. First team through all
four games wins.

> **🔴 Working here with other people? `git pull --rebase` before you start,
> and push small commits often.** Multiple contributors (and their AI tools)
> are building on this repo in parallel. See [CLAUDE.md](CLAUDE.md) and
> [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).

---

## Status: rebuilding from scratch

The original prototype has been archived to [`legacy/`](legacy/README_LEGACY.md)
(read-only reference). We are rebuilding a leaner MVP against the specs in
[`docs/`](docs/). **If you are here to write code, start with
[docs/TASK_LIST.md](docs/TASK_LIST.md).**

## MVP scope in one paragraph

Two teams (**Alpha** and **Bravo**), **exactly four players each**. A match is
**four stages**; each stage is a self-contained **game module** (Game 1–4). Every
player solves their own instance of the current stage's game. When you solve it
you go **green** and get a **15-second rest** (configurable). If your whole team
isn't green yet when the rest ends, you get a **holding question** to stay busy —
**fail it and you lose your green status** and must re-solve. A team advances to
the next stage only when **all four players are green at the same instant**. The
**first team to clear Stage 4 wins**. No power-ups, no economy, no sabotage — just
the relay.

## Documentation map

| Doc | What it covers |
| --- | --- |
| [docs/GAME_DESIGN.md](docs/GAME_DESIGN.md) | The rules: relay loop, green status, timers, win condition. Read first. |
| [docs/GAMES_SPEC.md](docs/GAMES_SPEC.md) | **The four games** (REWIRE / MIRROR RUN / DECANT / ECHO): rules, generation, validation, anti-cheat. |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the system is built: backend engine, state, timers, frontend. |
| [docs/GAME_MODULE_SPEC.md](docs/GAME_MODULE_SPEC.md) | **The contract every game must implement** (incl. action-game renderer interface). |
| [docs/WEBSOCKET_PROTOCOL.md](docs/WEBSOCKET_PROTOCOL.md) | Every client↔server message and the state snapshot schema. |
| [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) | Git workflow, branching, ownership, PR checklist. |
| [docs/TASK_LIST.md](docs/TASK_LIST.md) | The full, phased build plan with per-owner tasks and acceptance criteria. |
| [CLAUDE.md](CLAUDE.md) | Rules for AI coding agents working in this repo. |

## Tech stack (target)

- **Backend:** Python 3.11+, FastAPI, WebSockets, in-memory state (no DB for MVP).
- **Frontend:** Vanilla HTML/CSS/JS served by the backend (no build step for MVP).
- **Tests:** pytest.

## Quickstart (once the rebuild has a backend)

```bash
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -e ".[test]"
python3 -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000> in eight browser tabs (four per team) to play a full
match, or fewer to test with a reduced team size (see `MIN_PLAYERS_PER_TEAM` in
config).

```bash
python3 -m pytest
```
