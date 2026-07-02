# Game Module Spec — build a Relay game

**Read this before writing a game.** Every one of the four games is a self-contained
module that implements one interface. If your module honours this contract, it
plugs into the engine with zero engine changes and other people's games are none of
your business. This is the seam that lets four people build four games in parallel.

Pair with [GAME_DESIGN.md](GAME_DESIGN.md) (rules),
[ARCHITECTURE.md](ARCHITECTURE.md) (system), and — for the four concrete MVP games —
[GAMES_SPEC.md](GAMES_SPEC.md).

> **The four MVP games are *action* games** (rotate, flag, pour, tap), not
> type-a-word games. So each one is **two files**: a backend module (this contract)
> **and** a small frontend renderer. Read §10 "Interactive games" before you start —
> it changes how `payload`, `answer`, and `check` are used.

---

## 1. What a game *is* in The Relay

A game is a **puzzle generator + answer checker**, nothing more. The engine owns
the relay loop, statuses, timers, teams, and winning. Your module only answers two
questions:

1. *"Give me a fresh puzzle for a player."* → `generate_main()` / `generate_holding()`
2. *"Is this submitted answer correct for that puzzle?"* → `check()`

Your game is played on **one stage** (Game 1 = Stage 1, etc.). During that stage:

- **Main puzzle** — the real challenge. Solving it makes a player green.
- **Holding puzzle** — a shorter keep-alive question shown to a green player who is
  waiting for slower teammates (after their rest window). It should be **quick and
  easy** — its job is to make idling cost attention, not to be a second boss. A
  wrong/expired holding answer costs the player their green status.

Both come from **your** module so they share a theme.

## 2. The contract

Target file: `backend/games/base.py` (owned by Core). You implement a subclass in
your own file under `backend/games/`.

```python
# backend/games/base.py  — provided by Core; do not edit to suit one game.
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4


@dataclass
class PuzzleInstance:
    """One puzzle handed to one player. Created by a GameModule."""
    game_id: str                       # e.g. "rewire"
    kind: str                          # "main" | "holding"
    prompt: str                        # human-readable question the client shows
    answer: str                        # SERVER ONLY — never sent to the client
    payload: dict[str, Any] = field(default_factory=dict)  # render hints (see §6)
    id: str = field(default_factory=lambda: uuid4().hex[:12])

    def public(self) -> dict[str, Any]:
        """JSON the client is allowed to see. MUST NOT include `answer`."""
        return {
            "id": self.id,
            "game_id": self.game_id,
            "kind": self.kind,
            "prompt": self.prompt,
            "payload": self.payload,
        }


class GameModule(Protocol):
    """Every game implements this. The engine only ever talks to this interface."""

    id: str            # unique, stable, snake_case. e.g. "rewire"
    name: str          # display name. e.g. "Rewire"

    def generate_main(self, seed: int) -> PuzzleInstance: ...
    def generate_holding(self, seed: int) -> PuzzleInstance: ...
    def check(self, puzzle: PuzzleInstance, answer: str) -> bool: ...
    def reset(self) -> None: ...
```

### Method rules

- **`generate_main(seed)`** — return a `PuzzleInstance` with `kind="main"`. Must be
  **deterministic in `seed`**: the same seed always yields the same puzzle
  (prompt + answer). The engine passes a per-player, per-attempt seed so every
  player gets a different-but-reproducible puzzle. (Seeds are server-generated and
  unguessable — see [ARCHITECTURE.md](ARCHITECTURE.md) §"Seeds"; your module just
  consumes them.) Set `game_id` to `self.id`. **Difficulty is a module constant in
  the MVP** — never derive board size/difficulty from `seed` (that would randomise
  fairness between players); difficulty scaling is a stretch knob.
- **`generate_holding(seed)`** — same, with `kind="holding"`. Keep it solvable in a
  few seconds.
- **`check(puzzle, answer)`** — return `True`/`False`. Two valid styles:
  (a) **match** a stored `puzzle.answer` (normalise both sides, see §5) — for games
  with one canonical answer; or (b) **recompute** correctness from the submitted
  interaction and `puzzle.payload` — for action games where many interactions are
  valid (e.g. REWIRE re-runs connectivity; DECANT replays the pour sequence). Either
  way it must **never raise** on weird/malformed input (treat it as wrong) and must
  be **pure** (no state, no I/O).
- **`reset()`** — see §4. For a stateless module this is a no-op (`pass`).

### Hard rules (enforced in review)

1. **Deterministic:** `generate_*` output depends only on `seed` (and constants).
   No `random` without seeding from `seed`, no wall-clock, no globals.
2. **Stateless between calls** wherever possible. If you must cache, see §4.
3. **No answer leakage:** `public()` strips `PuzzleInstance.answer`. The `payload`
   may carry the puzzle **state** needed to render it (a board, tubes, clues) — that
   is not leakage — but it must **not** carry the *solution* (the correct rotations,
   the pour sequence). There are exactly **two sanctioned exceptions**, each
   documented in [GAMES_SPEC.md](GAMES_SPEC.md) with its threat model: ECHO's flash
   `sequence` (the content *is* the solution and must be sent to be animated) and
   SWEEP's full `clues` grid (needed for client-side reveals; mines are derivable
   as its complement). If in doubt, keep the solution server-side and **recompute**
   in `check`.
4. **No engine/other-game imports.** Import only from `backend.games.base` (and the
   stdlib). You do not know or care about teams, timers, or statuses.
5. **Self-contained answers:** a puzzle must be checkable purely from
   `(puzzle, answer)`. Don't rely on external lookups.

## 3. How it gets wired in (you do the tiny registration; Core owns the engine)

1. Put your class in `backend/games/gameN_<yourname>.py`.
2. Register your `id` in `backend/config.py`'s ordered `GAME_ORDER` list at the
   stage index you were assigned, and add your class to the registry map in
   `backend/registry.py`. (Coordinate the one-line edits to shared files via your
   PR — see [CLAUDE.md](CLAUDE.md) ownership rules.)
3. That's it. The engine calls `generate_main`, shows `.public()`, and later calls
   `check`. You never touch the loop.

## 4. Reset semantics — three scopes (read carefully)

"How do I reset a game?" has three different answers depending on scope. Getting
this right is what keeps replays and re-qualification clean.

| Scope | Who triggers it | What must happen | Your responsibility |
| --- | --- | --- | --- |
| **Per-puzzle (re-qualify)** | Engine, when a player loses green or starts a stage | The player gets a **brand-new** `PuzzleInstance` from `generate_main(new_seed)`. | Just return a fresh instance for the new seed. Because you're deterministic and stateless, there is nothing to clean up. |
| **Module reset** | Engine/host, e.g. between matches or in tests | `GameModule.reset()` returns the module to its initial state as if freshly constructed. | If your module holds **any** cross-call state (a cache, a counter), clear it here. If it's fully stateless, `reset()` is `pass`. |
| **Match reset** | Core engine | The whole match is torn down / a new match is created. | Nothing game-specific — a new match uses fresh seeds and calls `reset()` on modules. |

**Design goal: make your module stateless so per-puzzle reset is automatic and
`reset()` is a no-op.** Determinism-by-seed gives you that for free. Only introduce
`reset()` logic if you have a concrete reason to cache.

> Why `reset()` exists at all: game modules are **long-lived singletons** (one
> instance per stage, reused across every player and every match in the process).
> If you ever memoise expensive generation, `reset()` is the hook that guarantees a
> new match doesn't inherit stale data. A leaked global is the classic bug here.

## 5. Answer normalisation

Use one shared normaliser so "True", "true ", "TRUE" all match. Core provides:

```python
def normalize_answer(value: object) -> str:
    return " ".join(str(value).strip().lower().replace("/", " ").split())
```

- Call it on **both** the submitted answer and `puzzle.answer` inside `check()`.
- If your game needs stricter matching (case-sensitive, exact spacing), do the
  comparison yourself in `check()` and document why in a comment — but default to
  the shared normaliser so players aren't punished for capitalisation.

## 6. Payload conventions (so the generic frontend can render you)

The frontend renders any puzzle from `{prompt, payload, kind}` without knowing your
game. Use these optional `payload` keys and it "just works":

| `payload` key | Effect on the client |
| --- | --- |
| *(none)* | Renders `prompt` + a single free-text input. The default. |
| `options: [str, ...]` | Renders `prompt` + one button per option (multiple-choice). |
| `hint: str` | Renders a small hint line under the prompt. |
| `sequence: [...]` / `values: [...]` | Passed through for a game that adds richer rendering later. Safe to include. |

Keep everything JSON-serialisable (str/int/float/bool/list/dict). **Never** put the
answer in the payload. If your game needs custom rendering, coordinate with the
Frontend owner — but a text or multiple-choice puzzle needs **zero** frontend work.

## 7. Copy-paste template

Save as `backend/games/gameN_<name>.py`, rename the class, fill in the logic.

```python
from __future__ import annotations
import random
from backend.games.base import GameModule, PuzzleInstance, normalize_answer


class TemplateGame:
    """One-line description of the puzzle idea and what a correct answer looks like."""

    id = "template_game"      # unique snake_case; also goes in config.GAME_ORDER
    name = "Template Game"    # display name

    def generate_main(self, seed: int) -> PuzzleInstance:
        rng = random.Random(seed)          # seed everything from `seed` — no bare random
        a, b = rng.randint(2, 9), rng.randint(2, 9)
        return PuzzleInstance(
            game_id=self.id,
            kind="main",
            prompt=f"What is {a} × {b}?",
            answer=str(a * b),
            payload={"hint": "Just the number."},
        )

    def generate_holding(self, seed: int) -> PuzzleInstance:
        rng = random.Random(seed)
        n = rng.randint(10, 40)
        return PuzzleInstance(
            game_id=self.id,
            kind="holding",
            prompt=f"Quick check: is {n} even?",
            answer="yes" if n % 2 == 0 else "no",
            payload={"options": ["yes", "no"]},
        )

    def check(self, puzzle: PuzzleInstance, answer: str) -> bool:
        return normalize_answer(answer) == normalize_answer(puzzle.answer)

    def reset(self) -> None:
        # Stateless module → nothing to reset.
        return None
```

## 8. Tests your game must ship with

Put them in `tests/games/test_gameN_<name>.py`. Minimum bar:

1. **Determinism:** `generate_main(42)` twice → identical `prompt` and `answer`.
2. **Different seeds differ:** `generate_main(1)` and `generate_main(2)` differ
   (probabilistically; assert prompts aren't all identical across, say, 20 seeds).
3. **Correct answer passes:** `check(p, p.answer)` is `True` (and with odd
   casing/whitespace, e.g. `check(p, f"  {p.answer.upper()} ")` is `True`).
4. **Wrong answer fails:** `check(p, "definitely-wrong")` is `False`.
5. **No answer leakage:** `p.answer` (normalised) is **not** a substring of
   `p.public()` serialised to text. (Documented exceptions — ECHO's `sequence`,
   SWEEP's `clues` grid — assert their documented shape instead; see
   [GAMES_SPEC.md](GAMES_SPEC.md).)
6. **Holding is quick:** `generate_holding` returns `kind="holding"` and a puzzle;
   same determinism/correctness checks.
7. **`reset()` is safe:** calling it doesn't raise and doesn't change future
   deterministic output.

## 9. The four MVP games

The four concrete games are fully specified in [GAMES_SPEC.md](GAMES_SPEC.md) —
that document is the gameplay / validation / anti-cheat truth for each. Keep them
**short** (main ≈ 15–40s, holding ≈ a few seconds).

| Stage | Game | Category | Owner |
| --- | --- | --- | --- |
| 1 | **REWIRE** — rotate tiles to route power from source to sinks | Puzzle | [G1] |
| 2 | **SWEEP** — flag every mine from the number clues | Logical | [G2] |
| 3 | **DECANT** — pour colours between tubes until each is uniform | Sorting | [G3] |
| 4 | **ECHO** — watch the flash sequence, repeat it by tapping | Reflex/Memory | [G4] |

The legacy `puzzles.py` generators are inspiration only — reimplement against
**this** contract (do not import from `legacy/`).

## 10. Interactive (action) games — the frontend half

The four MVP games ([GAMES_SPEC.md](GAMES_SPEC.md)) are action games. That changes
three things versus a plain text puzzle:

1. **`payload` carries the game *state*** the renderer needs to draw (grid, tubes,
   pads, opening clues), plus the shared fields in
   [GAMES_SPEC.md](GAMES_SPEC.md) §0.1. Not the solution (§2 rule 3).
2. **The submitted `answer` is an *encoded interaction*** (a compact string the
   renderer builds from clicks/drags/taps), decoded and validated by `check`. Each
   game defines its own encoding in [GAMES_SPEC.md](GAMES_SPEC.md).
3. **You ship a frontend renderer** so the generic text/multiple-choice client
   (see §6) is only a fallback. Your renderer lives in `frontend/games/<id>.js` and
   registers itself so the play view can mount it by `game_id`.

### Renderer interface

```js
// frontend/games/<id>.js  — one per action game, written by the game owner.
window.RelayGames = window.RelayGames || {};
window.RelayGames["your_game_id"] = {
  // Draw the puzzle into `container` from puzzle.public() data.
  // Call api.submit(answerString) when the player commits their answer.
  // Optional api.setReady(bool) to enable/disable the shell's submit button.
  mount(container, puzzle, api) { /* build DOM, wire events */ },

  // Tear down listeners/timers before the next puzzle mounts. Must be idempotent.
  unmount() { /* cleanup */ },
};
```

- `puzzle` is exactly a `PuzzlePublic` (id, game_id, kind, prompt, payload — see
  [WEBSOCKET_PROTOCOL.md](WEBSOCKET_PROTOCOL.md) §3). **No answer is present.**
- `api.submit(answerString)` sends `submit_answer` or `submit_holding` (the shell
  picks the message type from `puzzle.kind`). Never talk to the WebSocket directly.
- The shell (Frontend owner) provides: the container, the countdown from
  `timer_deadline`, the team readiness strip, and error toasts. Your renderer only
  owns the puzzle area.
- Keep renderers dependency-free vanilla JS (no framework/build), matching the rest
  of `frontend/`.

### Reset for interactive games

Same three scopes as §4. The renderer's `unmount()` is the **frontend** analogue of
per-puzzle reset: it must fully clear state/listeners so mounting the next instance
(a re-qualify board, or the next stage) starts clean. The backend module stays
stateless-by-seed; `reset()` is still a no-op unless you cache.

Related: [GAMES_SPEC.md](GAMES_SPEC.md) · [GAME_DESIGN.md](GAME_DESIGN.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [WEBSOCKET_PROTOCOL.md](WEBSOCKET_PROTOCOL.md) · [TASK_LIST.md](TASK_LIST.md)
