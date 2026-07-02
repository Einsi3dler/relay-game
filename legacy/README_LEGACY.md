# ⚠️ Legacy Prototype — Reference Only, Do Not Extend

This folder is the **original Relay prototype**. It has been **archived** and is
frozen as a reference. The team is rebuilding the game from scratch against the
new specs under [`../docs/`](../docs/).

## Why it was archived

The prototype grew a lot of scope that is **out of scope for the MVP**:

- Power-ups (`blur`, `shake`, `scramble`, `dim`, `shield`)
- Cross-team sabotage
- "Grind mode" points economy
- Backlog-sync reconnect puzzles
- Dormant-node difficulty multipliers
- 10 roles, 10 players/team, 10 levels

The MVP keeps only the **relay core**: two teams of four, four games, and the
"everyone must be green to advance, first team to finish wins" loop.

## Rules for this folder

- **Do not import from `legacy/` in new code.** Treat it as read-only history.
- **Do not fix bugs here.** If behaviour is worth keeping, port the *idea* into
  the new codebase following the specs.
- Useful things to mine: the seeded puzzle generators in
  [`backend/puzzles.py`](backend/puzzles.py), the WebSocket connection manager in
  [`backend/main.py`](backend/main.py), and the vanilla-JS render loop in
  [`frontend/app.js`](frontend/app.js).

Start here instead: [`../docs/TASK_LIST.md`](../docs/TASK_LIST.md).
