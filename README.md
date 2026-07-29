# The Relay

A synchronous, browser-playable multiplayer **relay puzzle race**. Two teams
climb ten levels in parallel, each player on their own game chosen by a
non-playing **team leader** who banks the squad's currency and spends it on
perks. Nobody advances until **every teammate is cleared** — and staying
cleared takes nerve: wait it out, or gamble it on a bonus round. First team
through level 10 wins.

> **🔴 Working here with other people? `git pull --rebase` before you start,
> and push small commits often.** Multiple contributors (and their AI tools)
> are building on this repo in parallel. See [CLAUDE.md](CLAUDE.md) and
> [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).

---

## Status: v2 — leaders, levels, currency & perks

The MVP relay (six shared stages, rest/holding questions) is complete and has
been replaced by the **v2 design** per
[docs/REDESIGN_PLAN.md](docs/REDESIGN_PLAN.md). The original prototype stays
archived in [`legacy/`](legacy/README_LEGACY.md) (read-only reference). **If you
are here to write code, start with [docs/TASK_LIST.md](docs/TASK_LIST.md).**

## The game in one paragraph

Two teams (**Alpha** and **Bravo**), each `PLAYERS_PER_TEAM` (default 4) playing
members **plus one leader**. In the lobby the leader assigns every teammate a
different **game module** from the library; the leader themself doesn't play —
they watch a dashboard, see what only leaders may see (who's cleared, the
opponent's level), and spend the team's **currency** on attack/defense
**perks** (freeze, scramble, shield, extend-wait). Clearing your board marks
you **cleared** and pays currency; clear early and you choose to **wait** (a
3-minute hold — lapse and you re-solve) or gamble on a **harder bonus board**
for more currency, forfeitable on failure. The team advances only when **all
playing members are cleared at the same instant**; first team to clear level
`LEVEL_COUNT` (default 10) wins.

## Documentation map

| Doc | What it covers |
| --- | --- |
| [docs/GAME_DESIGN.md](docs/GAME_DESIGN.md) | The rules: level loop, cleared status, wait/bonus, economy, perks, leader handoff. Read first. |
| [docs/REDESIGN_PLAN.md](docs/REDESIGN_PLAN.md) | The approved v2 redesign plan and its follow-up list. |
| [docs/GAMES_SPEC.md](docs/GAMES_SPEC.md) | **The game library** (REWIRE / SWEEP / MIRROR RUN / DECANT / ECHO / OVERPRINT): rules, generation, validation, anti-cheat. |
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

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -e ".[test]"
python3 -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000> in ten browser tabs (a leader + four players per
team) to play a full match, or fewer with the host lowering the minimum players
per team in the lobby — each team always needs a leader plus at least one
player.

```bash
python3 -m pytest
```
