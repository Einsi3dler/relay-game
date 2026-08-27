# The Relay — The Game Library (detailed spec)

The concrete design for the library games: REWIRE, SWEEP, MIRROR RUN, DECANT,
ECHO (this doc), plus OVERPRINT, STACKDROP, LANE SHIFT, SHADOW CAST, THREADLINE,
BOMB DEFUSE and the expansion candidates in
[`game/RELAY_EXPANSION_GAMES_README.md`](../game/RELAY_EXPANSION_GAMES_README.md).
In v2 there is no stage order — the **team leader assigns one game per player**
(see [GAME_DESIGN.md](GAME_DESIGN.md) §2). Each game owner builds against the
[GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md) contract. This document is the
**gameplay + validation + anti-cheat** truth for each game; the module spec is
the **code interface**.

> All of them are **action games**: the player produces the answer by *doing*
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
   seed unique to `(player, level, attempt)`. There is no shared, static, or
   Google-able answer. A teammate's answer is useless to you.
2. **The answer is an *interaction*, not a *fact*.** You submit a set of rotations,
   flagged cells, pour moves, or taps — the result of manipulating state — not a
   word or number that an LLM "knows."
3. **State is visual/spatial, not textual.** To hand a board to an LLM you must
   transcribe a grid/tube layout by hand, wait for a reply, and translate it back
   into clicks — against a ~15–40s expected solve time, for a state nobody else
   shares. That round trip is slower than solving it.
4. **Time-boxed where it counts.** The bonus board runs against the remaining
   wait deadline (`WAIT_SECONDS` — see [GAME_DESIGN.md](GAME_DESIGN.md) §5). The
   level puzzle has **no hard limit**: the only pressure is the race itself,
   which is *soft* — a player already behind loses little by taking minutes
   with a solver. Accept this for now; a hard per-puzzle limit is the stretch
   hardening, and it matters most for search-friendly games (esp. DECANT).
5. **Server-authoritative validation.** The server never trusts a "yes I solved it"
   flag; it **replays/recomputes** correctness from the submitted interaction (§ per
   game). The client cannot fake a win.
6. **Submission rate limit.** Some answer spaces are tiny (SWEEP holding: 9
   candidates; ECHO holding: 64) and a wrong main answer costs nothing beyond a
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

# Game 2 — SWEEP  ·  Logical  ·  owner [G2]

### One-liner
Classic minesweeper deduction: from the revealed number clues, **flag every mine**
without detonating one.

### Skills
Logical deduction, constraint reasoning, careful reading of state.

### What the player sees & does
A small grid (e.g. **6×6 with 6 mines**) with an **opening safe region already
revealed** (numbers showing adjacent-mine counts). The player **left-clicks to
reveal** a cell they've deduced is safe and **right-clicks / long-press to flag** a
cell they've deduced is a mine. Solve = all mines flagged (equivalently, all safe
cells revealed).

### Rules
- A revealed number = count of mines in the 8 neighbours.
- Revealing a mine = **instant fail of this attempt** — the client submits the
  `"BOOM"` sentinel, `check` returns `False`, and the normal wrong-answer rule
  ([GAME_DESIGN.md](GAME_DESIGN.md) §4) serves a **fresh board**.
- The board is generated to be **logically solvable with no guessing** from the
  opening.

### Procedural generation (seeded, no-guess)
1. Place `mines` from `seed`. Pick an opening cell with a 0-count and flood its
   zero-region as the initial reveal.
2. Run a **deducibility check**: a simple solver applying (a) "a satisfied number
   flags its remaining neighbours" and (b) subset elimination between adjacent
   number constraints. If the board is *not* fully solvable without guessing, **re-roll
   the seed** and retry (cap ~50 tries; log and fall back to an easier board).
3. (Stretch) difficulty scaling: grid size (5×5 → 7×7) and mine density. For the
   MVP, use the fixed sizes below.

> If no-guess generation proves too costly, an acceptable MVP fallback is **Lights
> Out** (toggle cells to turn all lights off) with identical contract shape — but
> ship SWEEP if you can; it is the stronger "logical" game. Coordinate with Core
> before switching.

### Main vs Holding
- **Main:** 6×6, 6 mines.
- **Holding:** 3×3, 1 mine, opening reveal makes the mine trivially deducible.

### Answer encoding
Semicolon-separated flagged coordinates: `"r,c;r,c;..."`, e.g. `"0,4;2,1;5,5"`.
Order-independent.

### Server validation (`check`)
1. Parse coordinate set; reject out-of-range/malformed.
2. Return `True` iff the flagged set **exactly equals** the true mine set
   (`PuzzleInstance.answer` holds the mine coordinates, server-only).
   - No missing mines, no over-flagging safe cells.
> Reveal actions during play are handled **client-side** for UX; only the final flag
> set is validated. To make that possible the payload carries the clue number for
> **every safe cell** (the client can't compute reveal numbers without the mine
> layout, and reveals can't round-trip to the server in the one-shot `check`
> contract). See the anti-cheat caveat below. (Optionally the client refuses to
> submit until #flags == #mines.)

### payload schema
```jsonc
{
  "variant": "main", "difficulty": 2, "time_hint_seconds": 40,
  "rows": 6, "cols": 6, "mine_count": 6,
  "revealed": [ { "r": 0, "c": 0, "n": 0 }, { "r": 0, "c": 1, "n": 1 }, ... ],  // opening reveal
  "clues":    [ { "r": 0, "c": 2, "n": 2 }, ... ]  // number for EVERY safe cell;
                                                   //   client reveals from this locally
}
```
Mine coordinates are never listed in the payload — only in `answer` (stripped from
`public()`). **Caveat:** they are *derivable* from it (mines = the cells missing
from `clues`), the same exception class as ECHO's sequence.

### Anti-cheat notes (caveat)
A multimodal LLM *could* solve a screenshot, but the per-player board, the ~40s
target, and the screenshot→upload→read-back→click loop (×4 teammates) make it slower
than deducing. No static board exists to search.

**Caveat:** because reveals are client-side, the full clue grid is in the payload —
a player inspecting their own WebSocket traffic can read the mine set as the
complement of `clues`. Like ECHO's sequence, that defeats no LLM/Google user, only a
dev sniffing their own client, and is **out of scope for the MVP** (§0 threat
model). *Stretch hardening:* server round-trip per reveal.

### Edge cases
Player reveals a mine → the client submits the `"BOOM"` sentinel → `check` returns
`False` → normal wrong-answer path (fresh board). Never let a fake "solved" pass;
only an exact flag-set match wins.

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

# Game 5 — MIRROR RUN  ·  Divided attention  ·  owner [G2]

> Added July 2026 from the expansion library (briefly replaced SWEEP before the
> roster grew to five). The full prescriptive spec lives in
> [`game/RELAY_EXPANSION_GAMES_README.md`](../game/RELAY_EXPANSION_GAMES_README.md) §1
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
## Summary table

| Game | Category | Interaction | Answer encoding | Validation |
| --- | --- | --- | --- | --- |
| **REWIRE** | Puzzle | Click-rotate tiles | orientations `"1,0,3,..."` | recompute source→sink connectivity |
| **SWEEP** | Logical | Reveal / flag cells | flagged coords `"r,c;r,c"` | flag set == mine set |
| **MIRROR RUN** | Divided attention | One D-pad, two runners | `{"v":1,"moves":"URDL..."}` | replay both runners → both exits |
| **DECANT** | Sorting | Click source→dest pours | moves `"0>3;4>0"` | replay pours → all tubes uniform |
| **ECHO** | Reflex/Memory | Tap pads in order | taps `"4,0,8,3,1"` | taps == flashed sequence |
| **STACKDROP** | Causal prediction | Tap a pin to arm, again to pull | `{"v":1,"remove":["p1","p0"]}` | replay pulls through the cell simulation → every ball in its container |
| **LANE SHIFT** | Scheduling | One action per turn, then the belt moves | `{"v":1,"actions":[["toggle","s0"],["pass"]]}` | replay turns → every packet in its matching exit |
| **SHADOW CAST** | Spatial | Six quarter-turn buttons (X/Y/Z, each way) | `{"v":1,"turns":["x+","y-"]}` | replay turns → both projections match their targets |
| **THREADLINE** | Routing | Tap/drag/arrow-key a cable cell by cell | `{"v":1,"path":[[7,0],[6,0]]}` | walk the route → anchors in order, no reuse, inside both caps |
| **BOMB DEFUSE** | Manual lookup | Open a bay, work it, press OK | `{"v":2,"moves":[{"m":"m0","a":"n"},{"m":"ok"}]}` | replay every bay's actions → each bank shut in turn, the last OK defuses |

**STACKDROP rules note.** The module ships two pin kinds — flat pins *hold* a
ball, slanted pins *roll* it one cell down-slope — which extends
[`game/RELAY_EXPANSION_GAMES_README.md`](../game/RELAY_EXPANSION_GAMES_README.md)
§3 (its payload lists only `id` + `cells`). The extension is what makes the game
a puzzle: with hold-only pins a ball's route through the chamber is invariant —
a pull can delay a ball but never divert it — so no board could ever satisfy
that section's order-sensitivity requirement. Python and JavaScript run the same
simulation, locked together by `tests/games/fixtures/stackdrop_cases.json`.

**LANE SHIFT rules note.** The board is a lane grid rather than the explicit
node/edge list in
[`game/RELAY_EXPANSION_GAMES_README.md`](../game/RELAY_EXPANSION_GAMES_README.md)
§2 — the same directed graph written compactly, where a cell's outgoing edge is
`(row + delta, col + 1)` and `delta` comes from the junction standing on that
cell. Two explicit rulings the spec leaves open: packets that swap *rows*
diagonally in one tick do **not** collide (they share no cell — only two
packets landing on the same cell do), and a packet that cannot spawn because
its cell is still occupied fails the attempt. Python and JavaScript run the
same simulation, locked together by
`tests/games/fixtures/lane_shift_cases.json`.

**SHADOW CAST rules note.** The payload adds an explicit `bound` to the shape
described in
[`game/RELAY_EXPANSION_GAMES_README.md`](../game/RELAY_EXPANSION_GAMES_README.md)
§7 — that section requires the projection grids be padded to fixed dimensions
but never names the field. Axis conventions are pinned in the module docstring
and tested: `x` right, `y` away, `z` up, with `front[bound - 1 - z][x]` and
`top[bound - 1 - y][x]`. `initial_orientation` indexes an ordered table of the
24 proper cube orientations, enumerated breadth-first from the identity over
the six quarter turns; Python and JavaScript build that table independently and
`tests/games/fixtures/shadow_cast_cases.json` locks both the table and every
pose along a run.

One correction to that section's numbers: its "2–5 quarter turns from a valid
orientation" is a **scramble count, not a distance**. Under the six quarter
turns the cube's rotation group has diameter 3 — 1 orientation at distance 0, 6
at 1, 11 at 2, 6 at 3 — so no board can ever need a fourth turn. The level
curve therefore climbs through shape size (6→9 cubes) and shadow ambiguity
(4→2 orientations allowed to share the target pair), with distance pinned at 3
from level 6 on.

**SHADOW CAST anti-cheat caveat (accepted).** The targets and the shape are
both in the payload — they have to be, the player is looking at them — and
there are only 24 orientations, so a scripted client could search all of them
instantly. This is the same accepted weakness class as SWEEP's board and ECHO's
flash sequence (§0): the payload is inspectable, the *checker* is not
bypassable. The server replays the submitted turns through its own projection
code and ignores any claimed orientation, bitmap or success flag, so the only
forgeable thing is the player's effort, not the result. Normal play is faster
than writing the script.

**THREADLINE rules note.** Version 1 takes the strict reading of every ruling
[`game/RELAY_EXPANSION_GAMES_README.md`](../game/RELAY_EXPANSION_GAMES_README.md)
§14 leaves open. Passing through a later anchor early **fails** rather than being
ignored, and **no cell is used twice** — one rule that covers self-crossing, edge
reuse, and the 180-degree reversal the section wants rejected. Anchor order is
read off the walk, never from anything the client says. A port names a **side of
the anchor cell**: `entry: "n"` means the cable crosses that cell's north side on
its way in, and `exit: "s"` means it leaves through the south side, so a port
reads the same whichever way the player is travelling. Ports appear from level 5,
one side per ported anchor.

The board is generated constructively — a self-avoiding reference route first,
anchors taken from cells along it, obstacles placed off it — so a legal route
exists by construction and the checker never compares against it: **any** route
satisfying the rules wins. Two extra generation gates enforce the section's
quality bar. `_min_bends` runs a 0-1 BFS over `(cell, next anchor, heading)` that
ignores self-avoidance, so its answer is a true *lower bound* on the bends any
legal route must spend; a board is rejected unless `bend_cap` sits within the
level's `bend_freedom` of that bound (the cap is real, not decorative), and — the
section's "obstacles do not influence routing" rejection — unless removing the
obstacles *lowers* it. The level curve then climbs through anchors (3→5),
obstacles (5→10), route length (12→24 edges) and ports (0→2) while
`bend_freedom` falls 4→2. Python and JavaScript run the same route walk, locked
together by `tests/games/fixtures/threadline_cases.json`; the renderer uses it in
`partial` mode to refuse an illegal step where the player makes it.

**THREADLINE anti-cheat caveat (accepted).** The board is in the payload — it has
to be, the player is looking at it — and a scripted client could search it. Same
accepted weakness class as SWEEP's board and ECHO's flash sequence (§0): the
payload is inspectable, the *checker* is not bypassable. The reference route is
never sent, no claimed verdict is read, and the server re-walks whatever arrives,
so the only forgeable thing is the player's effort, not the result.

**BOMB DEFUSE rules note.** The source design in [`bomb.md`](../bomb.md) is a
two-player co-op: a Defuser who sees the bomb but not the manual, and an Expert
who sees the manual but not the bomb. The Relay seats both.

- **The Defuser** is a playing member, and the role is **required** — every team
  fields exactly one, so this is the game no team opts out of
  ([GAME_DESIGN.md](GAME_DESIGN.md) §2c). The role names the game; the
  Grandmaster picks who holds it, never what they play.
- **The Grandmaster is the Expert.** The manual lives on their dashboard as the
  **bomb console**. It is the manual and *nothing else* — no board, no fuse, no
  bay progress reaches them — which is both the faithful reading of §4 and the
  reason the console needs no synchronisation at all: a static page has nothing
  to keep in step. The Defuser describes the bay; the Grandmaster reads back the
  rule. **Silence blanks it**: the perk that takes a Grandmaster's roster takes
  their manual with it, and the card sits marked 🔇 until it lapses.
- **The Defuser keeps their own copy — up to a point.** Flipping to it hides
  the bomb while the fuse burns, so asking is faster than looking. Below
  `WITHHOLD_FROM_LEVEL` (8) that is the whole of it: a Grandmaster busy with
  four other players, silenced, or disconnected only *slows* their Defuser
  down. From level 8 the board also names one `withheld_pages` entry, and that
  page is missing from the Defuser's copy and present only on the console — so
  on a deep board an absent Grandmaster can strand their Defuser on one bay.
  The entry is drawn per `(seed, level)` from a stream of its own, always names
  a bay that is on the board, and is never more than one, so the rest of the
  bomb stays workable while the Defuser asks. Practice boards and the authored
  missions withhold nothing.

**Banks** (rules version 2). A board is a list of *banks*, each with its own
bays and its own fuse. Shut every bay in the armed bank and press OK and the
next bank arms behind it on a fresh countdown; the last one defuses the bomb.
Levels 1–10 are a single bank — an ordinary bomb — and the bonus-only tiers
11–13 are the ones that come in two, which is what makes them a different board
rather than just a wider one. Three rules follow from it, all enforced in the
replay: a bay of a bank that is not armed is refused (`wrong_bank`), whether it
has not come up yet or has already shut behind you; OK with a bay of the armed
bank still open is still the explosion; and only the final OK ends the bomb, so
a transcript that stops one OK short is `missing_ok`. §13's
no-two-of-a-kind rule is **per bank** — a later bank may reuse a type, since the
first instance is shuttered by then and the player is reading a fresh board.

Bays work the same way as the manual: the face is a dashboard, and working a bay
means opening it over the face. Looking at one thing at a time is the whole
adaptation; every other rule in §12–§67 survives intact.

One file backs both screens — `frontend/games/bomb_manual.js` holds the eight
mazes, the eight number grids and the colour mapping, and the browser's rules
mirror is built on those same tables. A drift between the seats is therefore
impossible by construction, and `tests/games/fixtures/bomb_defuse_cases.json`
locks the tables to Python.

**Sudden death** (§18) drives the loop. There are no strikes: a step into a maze
wall, a Simon colour echoed instead of translated, a wrong According-to-Number
button, a mini button touched early or released early, OK pressed with a bay
still open, Give Up, or the fuse reaching zero all detonate. The renderer plays
the explosion, holds MISSION FAILED for five seconds, then submits
`{"v":1,"failed":reason}` — which `check` rejects, so the engine serves a fresh
board at the same level. That *is* §20's "generate a completely new random
bomb", and it is why a failed board is never restartable in place: nobody gets
to learn a maze by walking into its walls.

Two rulings resolve gaps the source material left open. §61 gives the
According-to-Number bay a display, three buttons and progress boxes but no way
to tell *which* of the eight grids is live, while §58 and §71 both say the green
`1` identifies it — so the bay renders that cell as a 3×3 of dots with one lit,
which the player matches to the manual. And the maze's green tip is a **label,
not a hazard**: generation keeps the start and the goal off it, but stepping
through it is legal (§37 only rules it out as a destination).

The level curve runs bays 1 → 3 by level 6 and all four on a bonus board (there
are four module types, which is what caps it), with Simon 4 → 6 stages,
According to Number 4 → 6 stages, maze runs of 4 → 12 steps, and the mini
button's reaction window tightening 700 → 600 ms as its required hold grows
750 → 1000 ms. The `fuse_seconds` column *rises* wherever a bay is added and
tightens level by level after that, because a fuse is only difficulty relative
to the work it covers — three bays inside the two-bay fuse is not hard, it is
impossible. Python and JavaScript run the same replay and the same manual data,
locked together by `tests/games/fixtures/bomb_defuse_cases.json`; the renderer
uses it in `partial` mode so a wrong action detonates where the player makes it.

**Practice missions (set pieces).** The module also ships a ladder of *authored*
bombs — fixed bays, fixed fuse, the same board every time — served through
practice mode as `kind=<mission id>` and listed at
`GET /api/practice/{game_id}/missions`. Four drills teach one bay each, then
three missions build up to a two-bank gauntlet. They are **practice-only, by
rule**: a bomb you can memorise is exactly the "shared, static, Google-able
answer" §0 rules out, so `generate_main` never serves one and a test asserts no
generated board ever matches an authored one. They matter because the bomb is
now the game no team opts out of — every Defuser has to meet these four bays
somewhere, and every Grandmaster has to find their way around the console before
it counts. The hook is duck-typed (`missions()` / `generate_mission()`), so no
other game grows a method it has no use for.

**BOMB DEFUSE anti-cheat caveat (accepted, and larger than most).** This is a
lookup game, so its manual is public by definition — the eight mazes, the eight
number grids and the colour mapping ship in the renderer, and the payload names
which of them is live. A scripted client could therefore compute the whole
defusal. That is inherent: the tested skill is *reading the manual fast under a
fuse*, not knowing a secret. The checker is still not bypassable — the server
replays every action against the board and reads no claimed verdict — so what a
script forges is the player's effort, not the result, the same accepted class as
SWEEP's board and ECHO's flash sequence (§0).

Two things here are **unenforceable by construction** and are documented rather
than dressed up. The **fuse** is a clock the browser keeps, and the repo never
trusts client-reported elapsed time, so it is pressure on an honest player and
nothing more — the same standing as §0.4's "the level puzzle has no hard limit".
The **mini button** is a reaction test, and no client-side reaction test can be
proven server-side. Its hold code narrows the gap without closing it: reaching
the green state is what reveals the two-digit code the transcript must carry, so
a forged transcript has to model the module's state machine rather than assert
"solved" — but the code is in the payload, because the renderer has to display
it, and a script can read it there. Treated exactly like the cosmetic
screen-effect perks in `config.PERKS`: real for the people playing, not claimed
to be more.

## Per-game deliverables (each game owner)

For your game, ship all of:

1. Backend module (`backend/games/gameN_<name>.py`) implementing the contract.
2. Frontend renderer (`frontend/games/<id>.js`) implementing the renderer interface
   in [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md) §"Interactive games".
3. Tests (`tests/games/test_gameN_<name>.py`) — the module-spec §8 suite **plus**:
   a solvable generated board is actually solvable (feed a known-good interaction →
   `check` True), an illegal/short interaction → `check` False, no solution data
   leaks in `public()` (documented exceptions: ECHO's `sequence`, SWEEP's `clues` grid).
4. A playtest note with rough solve times (main & holding) for timer tuning.

Related: [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md) · [GAME_DESIGN.md](GAME_DESIGN.md) · [WEBSOCKET_PROTOCOL.md](WEBSOCKET_PROTOCOL.md) · [TASK_LIST.md](TASK_LIST.md)
