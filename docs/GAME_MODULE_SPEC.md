# Game Module Spec — build a Relay game

**Read this before writing a game.** Every game in the library is a
self-contained module that implements one interface. If your module honours this
contract, it plugs into the engine with zero engine changes and other people's
games are none of your business. This is the seam that lets many people build
games in parallel.

Pair with [GAME_DESIGN.md](GAME_DESIGN.md) (rules),
[ARCHITECTURE.md](ARCHITECTURE.md) (system), and — for the concrete games —
[GAMES_SPEC.md](GAMES_SPEC.md).

> **Relay games are *action* games** (rotate, flag, pour, tap), not
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

Your game is **assigned to one player per team** by their leader
(v2 — see [GAME_DESIGN.md](GAME_DESIGN.md) §2) and played for the whole match,
level by level:

- **Main puzzle** — the real challenge, generated per `(seed, level)`. Solving
  it clears the player for the current level. The same generator also serves
  the harder **bonus board** (the engine just passes a higher `level`).
- **Holding puzzle** — a shorter quick-fire variant. In v2 it appears **only in
  practice mode** (`/explore`); the match loop no longer uses it.

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

    def generate_main(self, seed: int, level: int = 1) -> PuzzleInstance: ...
    def generate_holding(self, seed: int) -> PuzzleInstance: ...   # practice mode only
    def check(self, puzzle: PuzzleInstance, answer: str) -> bool: ...
    def reset(self) -> None: ...
```

### Method rules

- **`generate_main(seed)`** — return a `PuzzleInstance` with `kind="main"`. Must be
  **deterministic in `seed`**: the same seed always yields the same puzzle
  (prompt + answer). The engine passes a per-player, per-attempt seed so every
  player gets a different-but-reproducible puzzle. (Seeds are server-generated and
  unguessable — see [ARCHITECTURE.md](ARCHITECTURE.md) §"Seeds"; your module just
  consumes them.) Set `game_id` to `self.id`. **Never derive board size/difficulty
  from `seed`** (that would randomise fairness between players). `level` (1-based,
  1..`DIFFICULTY_TIERS`) is the sanctioned difficulty knob and
  every shipped game **scales with it**: level 1 == the game's original board,
  difficulty rising to level 10, deterministic per `(seed, level)`. Each game
  reads a per-level `MAIN_LEVEL_PARAMS` table (or `_params_for_level`), which
  must have **`DIFFICULTY_TIERS` rows (13)**: levels 11..13 are
  **bonus-only tiers**, never served as a main board, and they exist so a team
  on the last level still gets a bonus board harder than the one they just
  cleared. Clamp anything outside the table. Same seed + same level must always
  yield the same puzzle.
- **`generate_holding(seed)`** — same, with `kind="holding"`; a quick few-second
  variant. **v2 uses it only for practice mode** (`/api/practice`) — it no longer
  appears in the match loop.
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
   the pour sequence). There are exactly **two sanctioned exceptions**, each documented in
   [GAMES_SPEC.md](GAMES_SPEC.md) with its threat model: ECHO's flash `sequence`
   (the content *is* the solution and must be sent to be animated) and SWEEP's
   full `clues` grid (needed for client-side reveals; mines are derivable as its
   complement). If in doubt, keep the solution server-side and **recompute**
   in `check`.
4. **No engine/other-game imports.** Import only from `backend.games.base` (and the
   stdlib). You do not know or care about teams, timers, or statuses.
5. **Self-contained answers:** a puzzle must be checkable purely from
   `(puzzle, answer)`. Don't rely on external lookups.

## 3. How it gets wired in (you do the tiny registration; Core owns the engine)

1. Put your class in `backend/games/gameN_<yourname>.py`.
2. Add your class to `REGISTERED_MODULES` in `backend/registry.py`, and add your
   `id` to a role's `games` list in `config.ROLES` (or leave it for the
   Generalist) so the Grandmaster's assignment picker can offer it.
   (Coordinate the one-line edits to shared files via your PR — see
   [CLAUDE.md](CLAUDE.md) ownership rules.)
3. That's it. The engine calls `generate_main(seed, level)` for whichever player
   was assigned your game, shows `.public()`, and later calls `check`. You never
   touch the loop.

## 4. Reset semantics — three scopes (read carefully)

"How do I reset a game?" has three different answers depending on scope. Getting
this right is what keeps replays and re-qualification clean.

| Scope | Who triggers it | What must happen | Your responsibility |
| --- | --- | --- | --- |
| **Per-puzzle (re-clear)** | Engine, when a player loses cleared status, fails a bonus, is scrambled, or starts a level | The player gets a **brand-new** `PuzzleInstance` from `generate_main(new_seed, level)`. | Just return a fresh instance for the new seed. Because you're deterministic and stateless, there is nothing to clean up. |
| **Module reset** | Engine/host, e.g. between matches or in tests | `GameModule.reset()` returns the module to its initial state as if freshly constructed. | If your module holds **any** cross-call state (a cache, a counter), clear it here. If it's fully stateless, `reset()` is `pass`. |
| **Match reset** | Core engine | The whole match is torn down / a new match is created. | Nothing game-specific — a new match uses fresh seeds and calls `reset()` on modules. |

**Design goal: make your module stateless so per-puzzle reset is automatic and
`reset()` is a no-op.** Determinism-by-seed gives you that for free. Only introduce
`reset()` logic if you have a concrete reason to cache.

> Why `reset()` exists at all: game modules are **long-lived singletons** (one
> instance per game id, reused across every player and every match in the process).
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
| `time_limit_seconds: int` | **A hard, server-enforced deadline for this board.** See below. |
| `hidden_deadline: true` | That deadline is withheld from the player and sent to their Grandmaster instead. See below. |

Keep everything JSON-serialisable (str/int/float/bool/list/dict). **Never** put the
answer in the payload. If your game needs custom rendering, coordinate with the
Frontend owner — but a text or multiple-choice puzzle needs **zero** frontend work.

### `time_limit_seconds` — a board the server will take away

By default a main board has no hard limit: the only pressure is the race
([GAMES_SPEC.md](GAMES_SPEC.md) §0.4). Emit `payload["time_limit_seconds"]` and
the engine gives that board a deadline of its own:

- It is armed wherever a main board is served — a wrong answer, a Scramble, a
  reconnect, a level advance, a Grandmaster handoff — and cancelled the moment
  the player stops solving it.
- It rides its own timer scope (`fuse:<player_id>`), so it runs *alongside* a
  wait timer rather than displacing one.
- When it passes, the engine serves a **fresh board at the same level**, exactly
  as a wrong answer does. Losing the board is the whole penalty: no currency
  moves, no cleared status is touched, the level does not change.
- The deadline reaches the player twice from one source: `me.puzzle_deadline`
  for the shell, which draws it on the timer bar a solving player leaves free,
  and `me.current_puzzle.deadline` for a renderer that draws a clock of its own
  and takes no other argument.

Three things to know before you opt in:

- **The published deadline is not the one that fires.** The engine kills the
  board `config.PUZZLE_GRACE_SECONDS` after the deadline it told the player
  about, so an answer already in flight when the clock runs out still counts.
  Draw the published one; the grace is not the player's time.
- **The attack perks know about you.** The enforced attacks were written when
  no game had a clock, and two of them read wrong on a timed board, so both are
  special-cased:
  - **Freeze** pushes your deadline out by exactly as long as it locks the
    player out. The frozen overlay covers the whole screen, so a freeze that
    let the clock run would cost the board rather than the ten seconds it is
    priced at. **Your renderer must follow the deadline when it moves** — see
    `update` in §10.
  - **Scramble** hands over a fresh board on the *old* board's deadline. A
    fresh clock would make the attack a gift: a victim eighty seconds into a
    ninety-second board would be rescued by it.
- **A board deadline is not a fail state the engine understands.** When it
  lapses the player simply gets another board. If your game wants a losing
  screen first, draw it and submit something `check` rejects, as BOMB DEFUSE
  does — the engine answers a wrong board and a lapsed one identically.

#### `hidden_deadline` — the clock goes to the other seat

Set `payload["hidden_deadline"] = True` alongside `time_limit_seconds` and the
engine routes that deadline to the team's **Grandmaster** instead of to the
player working the board:

- The player's `me.puzzle_deadline` is `null` and their puzzle carries no
  `deadline`. Their client has nothing to count.
- The Grandmaster's roster entry for that player carries `board_deadline`
  instead, and the leader dashboard draws it.
- **Exactly one seat is ever sent it.** Same instant, one copy — which is what
  makes this a visibility rule rather than a synchronisation problem, and the
  reason a leader dashboard can hold a live clock without becoming a second
  copy of the board.
- Silence takes it too. A silenced Grandmaster loses the roster, the feed and
  the clock, so for those seconds the deadline is in nobody's hands. Design for
  that rather than around it.

Your renderer must then keep **no clock of its own** on such a board — not even
a hidden one. A clock running where nobody can see it is still the client
deciding when the board ends, and here that is the server's call. Blank the
readout rather than removing it, since a missing element reads as broken, and
check that nothing else on screen leaks the number by the back door: banners,
end screens, anything that used to print seconds.

It needs `time_limit_seconds` to mean anything. Without one there is no
deadline to hide, and the board simply has no limit.

BOMB DEFUSE calls this its **dark fuse** and turns it on for the bonus-only
tiers, where the bomb's timer cell reads `--` and the Grandmaster's console is
the only countdown in the match. (Not to be confused with the **Blackout perk**
in `config.PERKS`, which is an unrelated four-second screen effect.)

**Bonus boards get no deadline of their own** — they already run against the
remaining wait deadline, and two clocks on one bar is one too many.

BOMB DEFUSE is the only game that opts in today, and its entry is honest about
what it buys: a bank arming is a client-side event, so the server cannot own a
*per-bank* deadline without the client reporting one, which is the
client-claimed time this repo refuses to trust. What it owns is the **whole
board's budget**, the sum of the bank fuses. Levels 1–10 are single-bank, so
there the budget *is* the fuse, exactly; on the two-bank bonus tiers it is the
sum, and a player could spend one bank's slack on the next. The per-bank
countdown on the bomb's face is still the client's.

## 7. Copy-paste template

Save as `backend/games/gameN_<name>.py`, rename the class, fill in the logic.

```python
from __future__ import annotations
import random
from backend.games.base import GameModule, PuzzleInstance, normalize_answer


class TemplateGame:
    """One-line description of the puzzle idea and what a correct answer looks like."""

    id = "template_game"      # unique snake_case; also add to a config.ROLES games list
    name = "Template Game"    # display name

    def generate_main(self, seed: int, level: int = 1) -> PuzzleInstance:
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
   same determinism/correctness checks (practice mode uses it).
7. **`reset()` is safe:** calling it doesn't raise and doesn't change future
   deterministic output.
8. **Level scaling:** `generate_main(seed, level)` scales difficulty
   deterministically with `level` — level 1 == your original board, harder by
   level 10, harder again through the bonus-only tiers 11..13,
   guaranteed-solvable at every level, same `(seed, level)` → same puzzle. Ship
   tests per level band (determinism, monotonic knobs, level-1-equals-original,
   solvable at **all 13** levels, bonus tiers measurably past level 10).
9. **Generation stays fast:** a bonus board is generated synchronously when a
   player opts in, so a deep tier that takes ~a second blocks the match. If a
   tier's difficulty gate makes generation expensive, **back the knobs off** —
   never raise the generator's attempt/node budget to compensate.

## 9. The game library

The concrete games are fully specified in [GAMES_SPEC.md](GAMES_SPEC.md) —
that document is the gameplay / validation / anti-cheat truth for each. Keep them
**short** (main ≈ 15–40s each level; holding ≈ a few seconds).

| Game | Category | Owner |
| --- | --- | --- |
| **REWIRE** — rotate tiles to route power from source to sinks | Puzzle | [G1] |
| **SWEEP** — flag every mine from the number clues | Logical | [G2] |
| **MIRROR RUN** — steer two runners, one with twisted controls, onto both exits | Divided attention | [G5] |
| **DECANT** — pour colours between tubes until each is uniform | Sorting | [G3] |
| **ECHO** — watch the flash sequence, repeat it by tapping | Reflex/Memory | [G4] |
| **OVERPRINT** — recreate the layered stamp composition | Spatial | [G6] |
| **STACKDROP** — pull the pins so each ball drops into its own container | Causal prediction | [G7] |
| **LANE SHIFT** — one action per turn while the conveyor keeps moving | Scheduling | [G8] |
| **SHADOW CAST** — turn the block until both of its shadows match | Spatial | [G9] |
| **THREADLINE** — draw one cable through the anchors in order, inside the bend budget | Routing | [G10] |
| **BOMB DEFUSE** — solve every live bay from the manual, then press OK, before the fuse runs out | Manual lookup | [G11] |

The legacy `puzzles.py` generators are inspiration only — reimplement against
**this** contract (do not import from `legacy/`).

## 10. Interactive (action) games — the frontend half

The library games ([GAMES_SPEC.md](GAMES_SPEC.md)) are action games. That changes
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

`mount(host, puzzle, api)` and `unmount()` are required. `update(puzzle)` is
**optional** and is called when the *same* puzzle id arrives again with
something changed — today that is only a board deadline the server has moved
(see `time_limit_seconds` in §6). A renderer that draws no clock of its own can
leave it out. Snapshots arrive constantly and mostly carry no change at all, so
`update` must be idempotent: shift by how much a value *grew*, never re-apply a
fixed amount.

The shell also **holds a submitted answer back while the player is frozen**
rather than letting the server refuse it, and sends it when the freeze lifts.
That matters for a game that submits exactly once at the end: without it a
completed board is thrown away and the renderer, which has already drawn its
win screen, never finds out.


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
- `api.submit(answerString)` sends `submit_answer` (the same message covers
  level boards and bonus boards). Never talk to the WebSocket directly.
- The shell (Frontend owner) provides: the container, the countdown from
  `timer_deadline`, the wait/bonus choice, and error toasts. Your renderer only
  owns the puzzle area.
- Keep renderers dependency-free vanilla JS (no framework/build), matching the rest
  of `frontend/`.

### Reset for interactive games

Same three scopes as §4. The renderer's `unmount()` is the **frontend** analogue of
per-puzzle reset: it must fully clear state/listeners so mounting the next instance
(a re-clear board, a bonus board, or the next level) starts clean. The backend
module stays stateless-by-seed; `reset()` is still a no-op unless you cache.

Related: [GAMES_SPEC.md](GAMES_SPEC.md) · [GAME_DESIGN.md](GAME_DESIGN.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [WEBSOCKET_PROTOCOL.md](WEBSOCKET_PROTOCOL.md) · [TASK_LIST.md](TASK_LIST.md)
