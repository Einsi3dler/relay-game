# The Relay — The Four Games (detailed spec)

The concrete design for the four MVP games. Each game owner ([G1]–[G4]) builds one
of these against the [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md) contract. This
document is the **gameplay + validation + anti-cheat** truth for each game; the
module spec is the **code interface**.

> All four are **action games**: the player produces the answer by *doing*
> something (rotating, flagging, pouring, tapping), not by typing a fact. That is
> deliberate — see §0.

---

## 0. Anti-cheat design principles (read first)

The brief: players must not be able to **ask an LLM or Google their way out**. No
game is cryptographically cheat-proof, but every game here is built so that using a
tool is **slower than just playing**, which is all we need given the relay's tight
timers and the fact that every player has a *different* puzzle.

Every game obeys these rules:

1. **Per-player, per-attempt randomization.** The board/state is generated from a
   seed unique to `(player, stage, attempt)`. There is no shared, static, or
   Google-able answer. A teammate's answer is useless to you.
2. **The answer is an *interaction*, not a *fact*.** You submit a set of rotations,
   flagged cells, pour moves, or taps — the result of manipulating state — not a
   word or number that an LLM "knows."
3. **State is visual/spatial, not textual.** To hand a board to an LLM you must
   transcribe a grid/tube layout by hand, wait for a reply, and translate it back
   into clicks — against a ~15–40s expected solve time, for a state nobody else
   shares. That round trip is slower than solving it.
4. **Time-boxed where it counts.** Holding questions are hard-timed
   (`HOLDING_SECONDS`). The main puzzle has **no hard limit in the MVP**
   (`MAIN_PUZZLE_SECONDS = 0` — see [GAME_DESIGN.md](GAME_DESIGN.md) §5): the only
   main-puzzle pressure is the race itself, which is *soft* — a player already
   behind loses little by taking minutes with a solver. Accept this for the MVP;
   the stretch main-puzzle limit is the hardening, and it matters most for
   search-friendly games (esp. DECANT).
5. **Server-authoritative validation.** The server never trusts a "yes I solved it"
   flag; it **replays/recomputes** correctness from the submitted interaction (§ per
   game). The client cannot fake a win.
6. **Submission rate limit.** Some answer spaces are tiny (ECHO holding: 64
   candidates) and a wrong main answer costs nothing beyond a
   fresh board, so the server enforces a minimum interval between submissions per
   player (`SUBMIT_MIN_INTERVAL_MS`, default 300, in `backend/config.py`). A
   too-fast submission gets an `error` and is ignored. This closes scripted
   brute-force without touching honest play.

**Threat model & honesty:** these defend against *casual tool-assist* (paste into
ChatGPT, search the answer). They do **not** defend against a determined player
inspecting their own WebSocket traffic — that is out of scope for the MVP and noted
per-game where relevant (esp. ECHO). Hardening (server-streamed state, obfuscated
payloads) is a stretch goal.

## 0.1 The interactive contract (shared by all four)

Because these are action games, each one is two pieces:

- **A backend module** implementing [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md): it
  generates the puzzle *state* into `PuzzleInstance.payload`, and its `check()`
  **validates a submitted interaction string** (it may recompute correctness from
  the payload rather than string-matching a stored answer).
- **A frontend renderer** (a small JS module the game owner also writes) that draws
  the state from `payload`, handles the clicks/drags/taps, and produces the
  **answer encoding** (a string) that gets sent via `submit_answer` /
  `submit_holding`. See [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md) §"Interactive
  games" for the renderer interface.

**Answer encoding** is always a compact string so it fits the existing
`check(puzzle, answer: str) -> bool` contract. Each game defines its own encoding
below. `check()` must treat any malformed/illegal encoding as **wrong**, never crash.

Common `payload` fields every game includes:

```jsonc
{
  "variant": "main" | "holding",   // convenience mirror of PuzzleInstance.kind
  "difficulty": 1,                  // per-game MODULE CONSTANT in the MVP (echoed
                                    //   for display/telemetry). Never derive it
                                    //   from `seed` — that would randomise
                                    //   fairness. Difficulty scaling is a stretch.
  "time_hint_seconds": 30           // suggested solve budget (display only; the
                                    //   authoritative timer is the engine's)
  // ...plus game-specific state (below)
}
```

---

# Game 1 — REWIRE  ·  Puzzle  ·  owner [G1]

### One-liner
Rotate the tiles of a scrambled circuit so power flows from the **source** to every
**sink**.

### Skills
Spatial reasoning, pattern completion, quick planning.

### What the player sees & does
A small grid (e.g. **4×4**) of pipe/wire tiles. Each tile is a fixed *shape*
(straight, elbow, T-junction, or endpoint) at some rotation. One tile is the
**SOURCE** (glowing), one or more are **SINKS**. **Clicking a tile rotates it 90°
clockwise.** Connected tiles light up live as power flows. Solve = every sink is lit
and no connection "leaks" into a wall (all open ends of powered tiles connect to
another open end).

### Rules
- Tiles never move, only rotate. 4 possible orientations (0,1,2,3 = 0°,90°,180°,270°).
- A connection exists between two adjacent tiles only if **both** have an open edge
  facing each other.
- The board is generated so **at least one** solution exists (see generation).
- Win condition: starting a flood-fill from the SOURCE reaches **all** SINKs, and
  every powered tile's open edges connect to a neighbouring open edge (no dangling
  live wire pointing off-grid or into a closed tile).

### Procedural generation (seeded, guaranteed solvable)
1. From `seed`, lay out a connected tree/path of pipes from source to sink(s) on the
   grid (a random spanning path). Record each tile's *correct* orientation.
2. Fill remaining cells with decoy tiles wired into the network or as dead stubs.
3. **Scramble**: set every tile to a random orientation. If the scramble happens to
   already be solved, re-roll one tile.
4. **Self-check before serving**: run the module's own `check()` against the
   recorded reference orientations. The decoy fill (step 2) can violate the
   no-dangling-wire rule, so a failing self-check means the board is unwinnable —
   re-roll and regenerate. Every served board must pass its own validator.
5. (Stretch) difficulty scaling: grid size (3×3 → 5×5), number of sinks (1 → 2),
   T-junction count. For the MVP, use the fixed sizes below.

### Main vs Holding
- **Main:** 4×4, 1–2 sinks.
- **Holding:** 2×2 or a single elbow that needs 1–2 rotations to connect A→B.

### Answer encoding
Row-major list of final orientations: `"o(0,0),o(0,1),...,o(R-1,C-1)"`, e.g.
`"1,0,3,2,0,1,2,3,..."`. (Tile types are fixed and known from the payload; only
orientations are the player's contribution.)

### Server validation (`check`)
1. Parse the orientation list; reject if length ≠ R×C or any value ∉ {0,1,2,3}.
2. Rebuild each tile's open-edge set from its (payload) shape + submitted orientation.
3. Flood-fill connectivity from SOURCE.
4. Return `True` iff all SINKs are reached **and** no powered tile has a live edge
   with no matching neighbour edge.
   > `answer` field in `PuzzleInstance` is unused (empty) — correctness is
   > **recomputed**, so multiple valid rotations all pass. This is intended.

### payload schema
```jsonc
{
  "variant": "main", "difficulty": 2, "time_hint_seconds": 35,
  "rows": 4, "cols": 4,
  "tiles": [ { "shape": "straight|elbow|tee|end", "orient": 2 }, ... ], // row-major, SCRAMBLED orient
  "source": [0, 0],
  "sinks": [ [3, 3] ]
}
```
No solution data is sent — only the scrambled board.

### Anti-cheat notes
Solution is a rotation vector over a random board; to offload it you'd transcribe 16
tile-shapes+orientations, a source and sink, get rotations back, and click each — far
slower than eyeballing the path. Per-player boards make copying pointless.

### Edge cases
Multiple valid solutions (fine — validated structurally). Isolated decoy tiles left
unpowered are allowed. Reject submissions that don't cover every cell.

---

# Game 2 — MIRROR RUN  ·  Divided attention  ·  owner [G2]

> Replaced **SWEEP** (minesweeper) in July 2026. The full prescriptive spec for
> MIRROR RUN lives in [`game/RELAY_EXPANSION_GAMES_README.md`](../game/RELAY_EXPANSION_GAMES_README.md) §1
> — that document is the source of truth; this section is the summary.

### One-liner
Steer **two runners through two mazes at once** with one set of controls —
Runner B interprets every command through a visible transformation
(mirror, rotate, or invert).

### Skills
Divided attention, spatial transformation, planning.

### Rules (summary)
- One command (U/R/D/L) moves **both** runners in the same turn; B applies the
  puzzle's fixed mapping first (`mirror_x`, `mirror_y`, `rotate_cw`,
  `rotate_ccw`, `invert`).
- A runner whose move would hit a wall or leave the board **stays still** —
  legal, and the key trick for de-synchronising the runners.
- Solved only when **both** runners occupy their own exits after the same turn.

### Main vs Holding
- **Main:** two 6×6 mazes, shortest solution 10–18 moves, move cap 30.
- **Holding:** two 4×4 mazes, 3–6 moves, simple mapping only, move cap 10.

### Generation & validation
Product-state `(posA, posB)` BFS proves solvability and shortest-path depth;
boards are re-rolled until the depth band is met and **both** boards matter
(each runner moves on ≥40% of a shortest path). `check` parses
`{"v":1,"moves":"URDL..."}`, replays every command server-side, and accepts
only if both final positions equal the exits. A reference path is server-only.

### Anti-cheat notes
Per-player mazes; the answer is a move sequence over a spatial state that is
tedious to transcribe. Normal play is faster than tool-assisted transcription.

---
# Game 3 — DECANT  ·  Sorting  ·  owner [G3]

### One-liner
The water/ball-sort classic: **pour** colours between tubes until each tube holds a
single colour.

### Skills
Planning, look-ahead, sequencing — a genuine "sorting" task with a large state space.

### What the player sees & does
A row of **tubes** (e.g. **5 tubes, capacity 4**, ~3 colours + 1–2 empty tubes),
each partly filled with stacked coloured segments. The player **clicks a source tube
then a destination tube** to pour. Solve = every tube is either empty or completely
filled with one colour.

### Rules (pour legality — free-stacking, single-block variant)
- A pour moves **exactly one block** — the source tube's top block.
- Legal into **any tube with room** — the destination's top colour does **not**
  need to match.
- The only illegal pours: source empty, destination full, or source == destination.
  Illegal pours are rejected (client should also prevent them; server enforces).
- Because any placement is legal, the board can never deadlock — the challenge
  is planning an efficient pour sequence under the race clock, and the
  generation difficulty floor (below) guarantees boards are never trivial.

### Procedural generation (seeded, guaranteed solvable)
1. Start from the **solved** state (each colour tube full, plus the empty tubes).
2. Apply `N` random **legal pours** (a reverse-scramble) driven by `seed`. Because
   every scramble step is a legal move, the reverse is always solvable.
3. **Difficulty gate (main only):** a reverse-scramble can collapse into a
   near-solved board, so re-roll until the colour-run lower bound
   (`total contiguous runs - colours`, which no pour can reduce by more than 1)
   guarantees at least `MAIN_MIN_POURS` pours (cap the attempts; fall back to
   the deepest board seen). Every served main board has a provable minimum
   solve depth, at zero search cost.
4. (Stretch) difficulty scaling: colours (4 → 5), tubes, and scramble depth `N`.
   For the MVP, use the fixed sizes below.

### Main vs Holding
- **Main:** 6 tubes / 4 colours / capacity 4 / min solve depth ≥ 7 pours.
- **Holding:** 3 tubes / 2 colours / solvable in ~2 pours.

### Answer encoding
Ordered move list `"src>dst;src>dst;..."` (tube indices), e.g. `"0>3;4>0;2>4"`.

### Server validation (`check`)
1. Clone the initial tubes from payload.
2. Replay each move in order; **reject** (return `False`) on any illegal pour or bad
   index.
3. After the last move, return `True` iff every tube is empty or single-colour-full.
> The move sequence is the proof of *action*; you cannot submit a static "answer."

### payload schema
```jsonc
{
  "variant": "main", "difficulty": 3, "time_hint_seconds": 40,
  "capacity": 4,
  "tubes": [ [1,2,1,3], [3,2,3,1], [2,1,2,3], [], [] ]  // bottom→top colour ids; [] = empty
}
```

### Anti-cheat notes
Ball-sort is solved by search; feeding a unique tube layout to a solver/LLM and
transcribing a move list back beats the timer only rarely, and never for all four
teammates at once. Layout is per-player.

### Edge cases
Player submits a legal-but-incomplete sequence → `False` (not sorted yet). Undo is a
client-side convenience; only the final submitted sequence is validated. Cap the
move list length (e.g. ≤ 60) to bound validation.

---

# Game 4 — ECHO  ·  Reflex / Memory  ·  owner [G4]

### One-liner
Simon-style memory: watch a **flashing sequence**, then reproduce it by tapping the
pads in the same order.

### Skills
Perception, short-term memory, timing — the hardest category to automate.

### What the player sees & does
A set of **pads** (e.g. a **2×2 or 3×3** grid of coloured pads). On start, the pads
**flash one at a time** in a generated order. When the flashing ends, the player
**taps the pads in that same order**. Solve = the tapped order matches the flashed
order exactly.

### Rules
- The sequence plays once (MVP) or on a "replay" button with a small penalty (stretch).
- One wrong tap = attempt fails immediately (→ normal re-solve path).
- Sequence length scales with difficulty.

### Procedural generation (seeded)
1. From `seed`, generate a sequence of pad indices of length `L`
   (main `L≈4–6`, holding `L=3`). (Stretch: scale `L` and pad count with
   difficulty; MVP uses the fixed sizes below.)
2. Include flash/gap timing so the client animates consistently.

### Main vs Holding
- **Main:** 3×3 pads, `L=5`.
- **Holding:** 2×2 pads, `L=3`.

### Answer encoding
Tapped pad order `"p,p,p,..."`, e.g. `"4,0,8,3,1"` (pad indices, row-major).

### Server validation (`check`)
Return `True` iff the submitted index list **equals** the generated sequence
(`PuzzleInstance.answer` = the sequence, server-side). Length and order must match.

### payload schema
```jsonc
{
  "variant": "main", "difficulty": 2, "time_hint_seconds": 20,
  "pads": 9,                 // grid of 9 pads (3x3)
  "sequence": [4,0,8,3,1],   // the order to flash — SEE anti-cheat caveat
  "flash_ms": 450, "gap_ms": 250
}
```

### Anti-cheat notes (important caveat)
ECHO is the **most LLM/Google-proof** game: the challenge is a *transient visual
animation* — there is no text to paste and nothing to search, and an LLM cannot watch
your screen in real time.

**Caveat:** because the client must animate the sequence, the sequence is present in
the payload — a player inspecting their own WebSocket traffic could read it. That is
**out of scope for the MVP** (it defeats no LLM/Google user, only a dev sniffing
their own client). *Stretch hardening:* have the server **stream** each flash as a
timed message instead of sending the whole sequence at once, and/or validate tap
**timing**. Do not block the MVP on this.

### Edge cases
A wrong tap mid-sequence → client submits the (wrong) partial/complete list → `check`
returns `False` → the normal wrong-answer path serves a **fresh sequence**. This is
load-bearing for ECHO: retrying a sequence you've already watched is no longer a
memory test. (Same reason a `solving` player gets a fresh instance on reconnect —
see [GAME_DESIGN.md](GAME_DESIGN.md) §9; a page refresh must not replay the same
flashes.) Empty submission → `False`. Cap sequence length (≤ 12).

---

## Summary table

| Game | Category | Interaction | Answer encoding | Validation |
| --- | --- | --- | --- | --- |
| **REWIRE** | Puzzle | Click-rotate tiles | orientations `"1,0,3,..."` | recompute source→sink connectivity |
| **MIRROR RUN** | Divided attention | One D-pad, two runners | `{"v":1,"moves":"URDL..."}` | replay both runners → both exits |
| **DECANT** | Sorting | Click source→dest pours | moves `"0>3;4>0"` | replay pours → all tubes uniform |
| **ECHO** | Reflex/Memory | Tap pads in order | taps `"4,0,8,3,1"` | taps == flashed sequence |

## Per-game deliverables (each game owner)

For your game, ship all of:

1. Backend module (`backend/games/gameN_<name>.py`) implementing the contract.
2. Frontend renderer (`frontend/games/<id>.js`) implementing the renderer interface
   in [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md) §"Interactive games".
3. Tests (`tests/games/test_gameN_<name>.py`) — the module-spec §8 suite **plus**:
   a solvable generated board is actually solvable (feed a known-good interaction →
   `check` True), an illegal/short interaction → `check` False, no solution data
   leaks in `public()` (documented exception: ECHO's `sequence`).
4. A playtest note with rough solve times (main & holding) for timer tuning.

Related: [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md) · [GAME_DESIGN.md](GAME_DESIGN.md) · [WEBSOCKET_PROTOCOL.md](WEBSOCKET_PROTOCOL.md) · [TASK_LIST.md](TASK_LIST.md)
