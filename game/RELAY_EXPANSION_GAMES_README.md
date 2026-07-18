# The Relay — Expansion Game Library

This document is an implementation specification for the proposed games that may be added to **The Relay** after the initial REWIRE, SWEEP, DECANT, and ECHO set. It is deliberately more prescriptive than a brainstorm. Each section defines the player-facing rules, expected interaction, procedural-generation constraints, answer format, server validation, holding-mode adaptation, accessibility requirements, failure cases, and minimum tests.

These are **candidate modules**, not a commitment to ship all of them. A game should only enter the active stage rotation after it has passed deterministic-generation tests, automated validator tests, mobile playtesting, and human solve-time testing.

## 1. Relay integration requirements

Every game in this document must fit the existing Relay lifecycle unless its section is explicitly marked **REQUIRES ENGINE EXTENSION**.

A standard game consists of:

- A Python backend module under `backend/games/` implementing the existing `GameModule` contract.
- A dependency-free JavaScript renderer under `frontend/games/<game_id>.js` registered through `window.RelayGames`.
- A deterministic main-puzzle generator.
- A deterministic holding-puzzle generator.
- A pure server-side checker that accepts `(puzzle, answer)` and never trusts a client-provided solved flag.
- Automated tests covering generation, serialization, malformed submissions, known-good solutions, known-bad solutions, and public-payload leakage.

The game module owns only puzzle generation and correctness. The Relay engine continues to own team progress, green status, rest periods, holding timers, requalification, match completion, and WebSocket transport.

## 2. Shared module contract

Every standard module must expose a stable snake-case `id`, a display `name`, `generate_main(seed)`, `generate_holding(seed)`, `check(puzzle, answer)`, and `reset()`.

Generation must be deterministic. The same seed and the same module constants must generate the same public puzzle state and the same accepted solution set. Do not use wall-clock time, process-global random state, browser randomness, network data, or nondeterministic collection ordering.

A generated `PuzzleInstance` must contain these common public payload fields:

```jsonc
{
  "variant": "main",
  "difficulty": 2,
  "time_hint_seconds": 30,
  "rules_version": 1
}
```

`difficulty` is a configured mode, not something randomly derived from the seed. Players in the same stage must face comparable complexity even though their exact states differ.

## 3. Shared renderer contract

Each renderer must implement:

```js
window.RelayGames["game_id"] = {
  mount(container, puzzle, api) {
    // Render puzzle.payload and wire interactions.
    // Call api.submit(answerString) only when the player commits.
  },
  unmount() {
    // Remove listeners, animation frames, intervals, and transient state.
  }
};
```

The renderer must not open its own WebSocket, inspect team state to alter difficulty, or decide whether an answer is correct. It may provide local visual feedback, but the server remains authoritative.

`unmount()` must be idempotent. It must cancel every `requestAnimationFrame`, timeout, interval, pointer listener, key listener, and resize observer created by the renderer. A fresh puzzle must never inherit move history or animation state from the previous attempt.

## 4. Standard interaction and answer rules

A submitted answer should be a compact, versioned interaction string or JSON string. The server must enforce maximum answer length and maximum move count before parsing. Recommended format:

```json
{"v":1,"moves":[...]}
```

Compact delimiter strings are still acceptable when they are easier to validate, but JSON is preferred for games with multiple action types.

Every checker must:

- Catch malformed input and return `False` rather than raising.
- Reject unknown action names, invalid indices, impossible coordinates, illegal transitions, duplicate one-use actions, and excessive move counts.
- Rebuild the initial state from the stored puzzle payload or server-only puzzle data.
- Replay each submitted action in order.
- Decide success from the reconstructed final state.
- Ignore all client claims about score, elapsed time, collisions, matched objects, or completion.
- Remain pure: no database access, network access, wall clock, mutation of the puzzle instance, or hidden per-player state.

## 5. Main and holding design targets

A main puzzle should normally take a first-time player **15–40 seconds** once the rules are understood. It should contain enough state that another player cannot simply shout the answer across the room.

A holding puzzle should normally take **3–8 seconds**. It must use the same central mechanic but reduce board size, action count, object count, or number of interacting rules. Holding mode must not introduce a different control scheme.

Holding puzzles should avoid long unskippable animations. Where transient presentation is essential, the complete presentation plus response should remain comfortably inside the Relay holding deadline.

## 6. Mobile, accessibility, and visual standards

All games must work at approximately 320 CSS pixels wide and with touch input. Interactive targets should be at least 44×44 CSS pixels wherever layout permits. Drag actions must have tap-based alternatives unless dragging is the entire tested skill and has been explicitly approved.

Colour cannot be the only carrier of meaning. Pair colours with symbols, textures, labels, or shapes. Every animated game must respect reduced-motion preferences by simplifying nonessential movement while preserving the tested information. Essential transient information may still animate, but it must avoid rapid flashes and unsafe flicker rates.

Keyboard support is required for desktop when practical. Focus must remain visible. Canvas games must expose a concise text instruction and should maintain a DOM representation of selectable controls even when the board itself is drawn on canvas or SVG.

## 7. Generation quality gates

A generator must not merely produce a technically solvable state. It must produce a state suitable for a race.

Before a puzzle is served, generation should verify:

- The puzzle is legal and solvable.
- It is not already solved.
- It does not exceed configured solution-depth bounds.
- It does not collapse into a trivial one-action main puzzle.
- It does not have an unreadable or ambiguous visual arrangement.
- Its required action count falls inside the target range.
- Any decoys or hazards are relevant rather than decorative noise.

For search-based generators, generate from a solved state by applying legal transformations, then use a bounded solver to estimate shortest-path depth. Reject instances outside the accepted range. Do not assume that the reverse of the scramble is the only solution.

## 8. Anti-assistance expectations

Per-player randomization prevents copying but does not by itself prevent solver assistance. The best Relay games make external assistance awkward because the state is visual, changing, transient, or cumbersome to transcribe.

For search-friendly deterministic puzzles, consider enabling a future main-puzzle time limit. Never describe a game as LLM-proof. A determined player can inspect client code or automate a solver. The practical standard is that normal play should be faster and easier than transcription, tool use, and re-entry.

## 9. Shared testing requirements

Each game must ship with tests for:

1. Identical seed produces identical puzzle public data.
2. A representative set of seeds generates valid, unsolved, solvable puzzles.
3. A known-good interaction passes.
4. A legal but incomplete interaction fails.
5. Every documented illegal move fails safely.
6. Empty, oversized, malformed, and wrong-version answers fail safely.
7. `PuzzleInstance.public()` does not expose server-only reference solutions.
8. Holding puzzles are materially smaller than main puzzles.
9. The frontend renderer can mount, unmount, and remount without duplicate listeners.
10. A playtest sample records median and 90th-percentile completion times.

The acceptance target is not merely “tests pass.” Main and holding solve times must fit the Relay loop, and players must understand the objective after one short instruction screen.

---
# 1. MIRROR RUN

**Module ID:** `mirror_run`  
**Integration:** CURRENT ENGINE  
**Estimated implementation complexity:** Medium  
**Primary mechanic:** Asymmetric simultaneous movement and divided attention.

## Player objective

Move two runners through two different boards and place both on their own exits at the same time. Every direction command affects both runners, but the second runner interprets the command through a visible transformation rule.

## What appears on screen

Two maze panels are shown side by side. Each contains one runner, one exit, walls, and optionally a small number of floor switches or one-way gates. A persistent rule badge between the boards states how Runner B interprets input, such as MIRROR LEFT/RIGHT, ROTATE CLOCKWISE, ROTATE COUNTER-CLOCKWISE, or INVERT.

## Rules

- A move command is one of U, R, D, or L and is applied to both runners during the same turn.
- Runner A uses the submitted direction directly. Runner B transforms it using the puzzle's fixed mapping.
- If a transformed move would enter a wall or leave the board, that runner stays still while the other runner may still move.
- A runner on its exit is not locked unless the payload explicitly enables exit locking; by default, later commands can move it away again.
- The puzzle is solved only when both runners occupy their own exits after the same completed turn.
- The player may undo locally, but the committed answer must be the complete final move sequence from the original state.

## Main-puzzle specification

Two 6×6 boards, 10–18 moves in an estimated shortest solution, one fixed direction mapping, and at most one simple board modifier such as a one-way tile. Recommended move cap: 30.

## Holding-puzzle specification

Two 3×3 or 4×4 boards with a 3–6 move solution, no switches, and a simple mirror or inversion mapping. Recommended move cap: 10.

## Seeded procedural generation

- Choose the direction mapping from an allowed set with equal control complexity.
- Generate a valid paired state by performing a reverse walk from both exits under shared commands, or generate random paired mazes and solve the product state `(posA, posB)` using breadth-first search.
- Reject boards with no solution, an already solved start, a shortest path outside the configured range, or long stretches in which one runner never moves.
- Prefer instances where both boards influence the solution; reject a board if one side can be ignored for most of the path.
- Store only the public mazes, positions, exits, mapping, and optional tile rules. A reference path may remain server-only for tests but must not be required by `check()`.

## Suggested public payload

```jsonc
{
  "variant": "main",
  "difficulty": 2,
  "time_hint_seconds": 30,
  "rules_version": 1,
  "rows": 6,
  "cols": 6,
  "boards": [
    {
      "walls": [
        [
          0,
          1
        ],
        [
          1,
          1
        ]
      ],
      "start": [
        5,
        0
      ],
      "exit": [
        0,
        5
      ]
    },
    {
      "walls": [
        [
          2,
          3
        ],
        [
          3,
          3
        ]
      ],
      "start": [
        5,
        5
      ],
      "exit": [
        0,
        0
      ]
    }
  ],
  "mapping_b": "mirror_x",
  "move_cap": 30
}
```

## Answer or action encoding

`{"v":1,"moves":"URRDLU..."}`. Only U/R/D/L are valid. The sequence length must not exceed `move_cap`.

## Server-authoritative validation

- Parse the version and move string.
- Initialize both positions from the payload.
- For each command, transform the command for Runner B, then apply collision rules independently to both boards.
- Apply any tile effects in a fixed documented order after movement.
- Accept only if both final positions equal their exits. Do not accept a client-provided final coordinate.

## Frontend and interaction requirements

- Provide four large directional buttons and keyboard arrows/WASD.
- Animate both runners together in under 150 ms per move; queued input must be bounded so players cannot accidentally submit dozens of moves.
- Show the transformed B direction briefly on each move for learnability, but never reveal future moves.
- Provide Restart and Undo. Undo changes only local renderer state; submission still contains the final sequence from the beginning.

## Edge cases and explicit rulings

- Both runners may occupy visually equivalent coordinates because boards are separate.
- A blocked move is legal and may be necessary, so the validator must not reject it merely because one runner stays still.
- Reject mappings unknown to the current `rules_version`.
- If switches are later added, switch state becomes part of the solver state and must be replayed server-side.

## Minimum acceptance tests

- Across a seed sample, every main board has a verified solution within the move cap.
- At least 70% of shortest solutions require meaningful movement on both boards.
- A path that solves only one board fails.
- Submitting final coordinates without a move history is impossible through the checker contract.

---
# 2. LANE SHIFT

**Module ID:** `lane_shift`  
**Integration:** CURRENT ENGINE  
**Estimated implementation complexity:** High  
**Primary mechanic:** Turn-based management of a changing conveyor system.

## Player objective

Route coloured packets to matching exits while the conveyor advances after every player action. The player changes junctions, pauses packets, or shifts track sections before the next movement tick.

## What appears on screen

A horizontal or top-down conveyor board shows lanes, packet tokens with colour-and-symbol identities, matching exits, junction switches, blockers, and a visible next-tick preview. Controls are attached directly to junctions and permitted hold cells.

## Rules

- The game advances in discrete turns; it is not based on browser frame timing.
- On each turn the player selects exactly one permitted control action or PASS.
- After the action, every non-held packet advances one conveyor edge according to the current switch configuration.
- Movement resolution is simultaneous. Two packets targeting the same cell collide and fail unless the cell is explicitly a merge-safe buffer.
- A packet entering the wrong exit, leaving the board, or colliding causes immediate attempt failure in the renderer and a failing submission.
- The puzzle succeeds when every packet has entered its matching exit and no packet remains on the board.

## Main-puzzle specification

Three or four lanes, 4–6 packets, 3–5 controllable junctions, and a 7–14 turn solution. Recommended action cap: 20.

## Holding-puzzle specification

Two lanes, 2 packets, one junction, and a 2–4 turn solution. Recommended action cap: 6.

## Seeded procedural generation

- Represent the board as a directed graph with cells as nodes and switch-dependent outgoing edges.
- Generate a solved schedule first: choose packet spawn positions, exits, and a sequence of control states that routes all packets safely.
- Simulate the schedule, then add decoy switch options only when they remain visually understandable.
- Run a bounded state-space solver over packet positions, delivered packets, switch states, hold charges, and turn number. Reject unsolved, already solved, or out-of-range instances.
- Ensure packet identities are distinguishable without colour by pairing each colour with a symbol.

## Suggested public payload

```jsonc
{
  "variant": "main",
  "difficulty": 3,
  "time_hint_seconds": 35,
  "rules_version": 1,
  "graph": {
    "nodes": [
      "0,0",
      "0,1",
      "1,1"
    ],
    "edges": [
      [
        "0,0",
        "0,1"
      ],
      [
        "0,1",
        "1,1"
      ]
    ]
  },
  "packets": [
    {
      "id": "p0",
      "kind": "circle",
      "start": "0,0",
      "exit": "exit_circle",
      "spawn_tick": 0
    }
  ],
  "switches": [
    {
      "id": "s0",
      "states": [
        "straight",
        "down"
      ],
      "initial": "straight"
    }
  ],
  "holds": [
    {
      "id": "h0",
      "charges": 1
    }
  ],
  "turn_cap": 20
}
```

## Answer or action encoding

`{"v":1,"actions":[["toggle","s0"],["pass"],["hold","h0"]]}`. Action names and target IDs must come from the payload.

## Server-authoritative validation

- Initialize the conveyor graph, packet states, switches, holds, and tick counter.
- For each submitted action, verify that it is legal in the current state, apply it, then resolve one simultaneous conveyor tick.
- Detect same-target collisions, swaps through a single-edge collision if disallowed, wrong exits, illegal holds, and packets moving beyond defined edges.
- Accept only if all packets are correctly delivered within `turn_cap` and no failure occurred.

## Frontend and interaction requirements

- Show a clear phase rhythm: player action, then a short automatic movement animation.
- Disable controls during the movement animation to prevent accidental double turns.
- Provide a predicted arrow for each packet's next edge, but do not preview beyond one tick.
- Restart is required. Undo may be omitted because replaying a changing multi-packet state can confuse players; if included, it must restore the entire prior simulation snapshot.

## Edge cases and explicit rulings

- Define whether packets may swap adjacent cells during one simultaneous tick; the recommended default is that a head-on swap counts as a collision.
- Packets scheduled to spawn later must be included in server simulation and cannot spawn into an occupied cell.
- If all remaining packets are irrecoverably lost, the renderer may submit a failure sentinel, but the server still determines failure by replay.
- Do not use real-time conveyor speed as part of correctness under the current engine.

## Minimum acceptance tests

- Reference schedules pass, while changing any critical switch action fails or produces a different valid solution only when verified by the solver.
- The server and browser simulation produce identical states for a shared fixture set.
- Simultaneous collision resolution is covered by explicit tests.
- Holding mode completes without more than one automatic animation cycle per player decision.

---
# 3. STACKDROP

**Module ID:** `stackdrop`  
**Integration:** CURRENT ENGINE  
**Estimated implementation complexity:** Medium  
**Primary mechanic:** Causal prediction through discrete gravity and removable supports.

## Player objective

Remove pins in the correct order so every marked ball falls through the structure and lands in its matching container without becoming trapped or entering a hazard.

## What appears on screen

A vertical chamber contains grid-aligned walls, horizontal support pins, ramps, balls marked by colour and shape, matching containers, and hazards. Pins are visibly numbered or individually selectable. The chamber is static until a pin is removed, after which gravity resolves.

## Rules

- A pin may be removed at most once and cannot be reinserted.
- After each removal, gravity resolves fully before the next action.
- Balls fall one cell at a time and may roll along explicitly defined 45-degree ramps. This is a deterministic grid simulation, not free-body physics.
- If two balls attempt to occupy the same cell, apply the documented priority or collision rule; the recommended default is that they stack if the cell supports stacking, otherwise the attempt fails.
- A ball entering a wrong container or hazard fails the attempt.
- The puzzle succeeds when all required balls are inside their matching containers.

## Main-puzzle specification

A chamber approximately 7×9 cells, 3–5 balls, 4–7 removable pins, 2–4 containers, and a 3–6 removal solution.

## Holding-puzzle specification

A 4×5 chamber, 1–2 balls, 2–3 pins, and a 1–2 removal solution.

## Seeded procedural generation

- Use a discrete cell model with exact fall and ramp-transition rules shared between generator, client, and checker.
- Construct from a successful terminal arrangement by adding supports and reverse-placing balls, or sample chambers and use a bounded solver over remaining pins and ball positions.
- Reject any board with nondeterministic simultaneous outcomes, visually overlapping controls, an already solved start, or a solution outside the removal-depth range.
- Prefer puzzles in which order matters; reject boards where every permutation of useful pins succeeds.
- Keep a server-only reference solution for generator verification, but recompute success from submitted removals.

## Suggested public payload

```jsonc
{
  "variant": "main",
  "difficulty": 2,
  "time_hint_seconds": 30,
  "rules_version": 1,
  "rows": 9,
  "cols": 7,
  "static_cells": [
    {
      "r": 8,
      "c": 0,
      "type": "wall"
    },
    {
      "r": 4,
      "c": 3,
      "type": "ramp_right"
    }
  ],
  "pins": [
    {
      "id": "p0",
      "cells": [
        [
          3,
          1
        ],
        [
          3,
          2
        ]
      ]
    },
    {
      "id": "p1",
      "cells": [
        [
          6,
          4
        ],
        [
          6,
          5
        ]
      ]
    }
  ],
  "balls": [
    {
      "id": "b0",
      "kind": "triangle",
      "start": [
        1,
        1
      ]
    }
  ],
  "containers": [
    {
      "id": "c0",
      "kind": "triangle",
      "cells": [
        [
          8,
          5
        ]
      ]
    }
  ],
  "removal_cap": 7
}
```

## Answer or action encoding

`{"v":1,"remove":["p1","p0","p3"]}`. Pin IDs must be unique and listed no more than once.

## Server-authoritative validation

- Build the initial chamber and verify all submitted pin IDs.
- For each removal, delete the pin and run gravity until no ball can move.
- Resolve balls in a deterministic order while using simultaneous intent checks where collisions matter.
- Reject immediately on hazards, wrong containers, invalid overlaps, repeated pins, or excessive removals.
- Accept only if all balls are correctly contained after the submitted sequence.

## Frontend and interaction requirements

- Clicking a pin should highlight it before removal; a second click or explicit Pull control commits, reducing accidental taps.
- Gravity animation should interpolate the deterministic cell path rather than run an independent physics engine.
- Provide Restart. Do not provide Undo after a pin is pulled unless the entire simulation can be rewound exactly.
- Use shape markings on balls and containers, not colour alone.

## Edge cases and explicit rulings

- Define whether containers can hold multiple balls and whether order inside a container matters; version 1 should use one required ball per container unless stacking is central to a generated puzzle.
- A ball supported by another ball must be handled consistently during gravity resolution.
- Do not allow a ball to tunnel through another moving ball due to animation frame differences.
- The client animation path is cosmetic; server cell simulation is authoritative.

## Minimum acceptance tests

- Every generated main puzzle contains at least one order-sensitive pair of pins.
- Browser and server fixtures agree on final ball cells after each removal.
- Repeated or unknown pin IDs fail without exceptions.
- A visually successful client state cannot pass unless the removal sequence reproduces it server-side.

---
# 4. OVERPRINT

**Module ID:** `overprint`  
**Integration:** CURRENT ENGINE  
**Estimated implementation complexity:** Medium  
**Primary mechanic:** Layered visual construction with rotation, reflection, and translation.

## Player objective

Transform and position several transparent pattern layers so their combined visible result exactly matches a target pattern.

## What appears on screen

The interface shows a target grid and a workspace grid. Below or beside it are two to four transparent layers, each containing marked cells or simple line segments. The selected layer can be rotated, flipped, and moved with buttons or direct dragging.

## Rules

- Each layer has a fixed local pattern and may support a configured subset of transforms: quarter-turn rotation, horizontal flip, vertical flip, and bounded translation.
- The composite is calculated cell by cell using the puzzle's declared blend rule. Version 1 should use Boolean OR or exact coloured occupancy rather than visual alpha arithmetic.
- No marked cell may extend outside the workspace.
- The final composite must exactly equal the target; extra cells fail just as missing cells do.
- Equivalent transform representations are acceptable if they produce the same composite.
- For transient variants, the target may disappear after a preview period, but the target data used for validation remains server-side.

## Main-puzzle specification

A 6×6 workspace, 3 layers, 2–5 marked cells per layer, rotations and translations enabled, and optionally one flip-capable layer. The target may remain visible for the first implementation; a 3-second preview is a later hardening option.

## Holding-puzzle specification

A 4×4 workspace, 2 layers, translation plus at most one rotation, and no transient target unless playtests show enough response time.

## Seeded procedural generation

- Generate random layer-local patterns that are visually distinct and connected or nearly connected.
- Choose a valid transform for each layer and compose the target from those transforms.
- Scramble the initial layer transforms and reject if already solved.
- Enumerate or search the bounded transform space to estimate solution count. Reject excessive ambiguity when it makes the puzzle feel arbitrary, while still accepting harmless symmetric equivalents.
- Do not include the chosen target transforms in the public payload. The target bitmap is public only when the UI is supposed to show it.

## Suggested public payload

```jsonc
{
  "variant": "main",
  "difficulty": 2,
  "time_hint_seconds": 30,
  "rules_version": 1,
  "rows": 6,
  "cols": 6,
  "blend": "or",
  "target": [
    "001100",
    "011010",
    "000010",
    "000000",
    "000000",
    "000000"
  ],
  "layers": [
    {
      "id": "l0",
      "pattern": [
        [
          0,
          0
        ],
        [
          0,
          1
        ]
      ],
      "allow_flip_x": false,
      "allow_flip_y": false
    },
    {
      "id": "l1",
      "pattern": [
        [
          0,
          0
        ],
        [
          1,
          0
        ],
        [
          1,
          1
        ]
      ],
      "allow_flip_x": true,
      "allow_flip_y": false
    }
  ],
  "initial": [
    {
      "id": "l0",
      "r": 4,
      "c": 2,
      "rot": 1,
      "flip_x": false
    },
    {
      "id": "l1",
      "r": 0,
      "c": 0,
      "rot": 3,
      "flip_x": false
    }
  ]
}
```

## Answer or action encoding

`{"v":1,"layers":[{"id":"l0","r":1,"c":2,"rot":0,"fx":false,"fy":false},...]}`. Layer IDs must appear exactly once.

## Server-authoritative validation

- Validate that each declared transform is allowed for that layer and remains in bounds.
- Apply flip, then rotation, then translation using one fixed transform order.
- Compose the workspace using the declared blend rule.
- Accept only when the final bitmap exactly equals the server target.
- Do not compare against one canonical transform vector because symmetrical alternatives may be valid.

## Frontend and interaction requirements

- Selecting a layer must clearly outline it and raise it visually without changing blend semantics.
- Provide rotate and flip buttons as alternatives to gestures. Dragging should snap to integer cells.
- Show the live composite and optionally highlight missing versus extra cells only after the player presses Check; continuous error highlighting would trivialize some puzzles.
- Use patterned fills or glyphs to distinguish layers for colour-blind players.

## Edge cases and explicit rulings

- Symmetric layers may have multiple equivalent transform encodings; composite validation handles this.
- A transform that sends any marked cell out of bounds is invalid even if clipped rendering looks correct.
- Define whether overlapping identical coloured marks remain one mark; version 1 Boolean OR should do so.
- If the target is transient, the payload must not leave it accessible after preview without an accepted threat-model decision.

## Minimum acceptance tests

- Reference composites pass and transformations producing one extra cell fail.
- Every generated puzzle starts unsolved and has at least one validated solution.
- Out-of-bounds and duplicate-layer submissions fail.
- Holding mode is comfortably manipulable on a phone without precise pixel dragging.

---
# 5. GRAVITY SHIFT

**Module ID:** `gravity_shift`  
**Integration:** CURRENT ENGINE  
**Estimated implementation complexity:** Low to Medium  
**Primary mechanic:** Whole-board rotation and deterministic sliding.

## Player objective

Rotate the chamber so all marbles slide under the new gravity direction and eventually occupy their matching docks.

## What appears on screen

A square grid chamber contains walls, marbles identified by colour and symbol, matching docks, optional stop blocks, and four large rotate controls. The board visibly rotates or the gravity arrow changes while objects slide.

## Rules

- An action rotates gravity 90 degrees clockwise or counter-clockwise. A direct set-gravity action may be used only if configured; relative rotation is preferred.
- After gravity changes, every marble slides until blocked by a wall, another settled marble, or the chamber boundary.
- Movement is resolved deterministically. The recommended version processes simultaneous movement in repeated one-cell steps until stable.
- A marble may pass over its dock unless docks are configured as catch cells; version 1 should make matching docks catch and lock their marble.
- Entering a mismatched dock is either a hard failure or a blocking cell; version 1 should use hard failure for clarity.
- Success requires every marble to be locked in its own dock.

## Main-puzzle specification

A 6×6 chamber, 3 marbles, 3 docks, 5–9 walls, and a 4–8 rotation solution. Recommended action cap: 14.

## Holding-puzzle specification

A 4×4 chamber, 1–2 marbles, and a 1–3 rotation solution. Recommended action cap: 5.

## Seeded procedural generation

- Sample walls, docks, and marble starts, then solve using breadth-first search over gravity orientation, marble positions, and locked state.
- Alternatively start with marbles in docks and reverse-generate only if the reverse operation is proven equivalent; simple random reverse sliding is not always valid.
- Reject already solved, unsolved, overly ambiguous, or shortest-path-out-of-range boards.
- Prefer boards where marbles interact or use shared obstacles, while avoiding visually congested layouts.
- Record shortest depth for telemetry but recompute final success from actions.

## Suggested public payload

```jsonc
{
  "variant": "main",
  "difficulty": 2,
  "time_hint_seconds": 25,
  "rules_version": 1,
  "rows": 6,
  "cols": 6,
  "initial_gravity": "down",
  "walls": [
    [
      0,
      0
    ],
    [
      1,
      0
    ]
  ],
  "marbles": [
    {
      "id": "m0",
      "kind": "star",
      "start": [
        1,
        2
      ]
    }
  ],
  "docks": [
    {
      "id": "d0",
      "kind": "star",
      "cell": [
        5,
        4
      ],
      "catch": true
    }
  ],
  "action_cap": 14
}
```

## Answer or action encoding

`{"v":1,"turns":"RLLR..."}` where `R` and `L` are relative quarter-turns.

## Server-authoritative validation

- Parse the turn sequence and enforce `action_cap`.
- For each turn, update gravity and repeatedly compute each unlocked marble's next-cell intent until no movement remains.
- Resolve conflicts by a documented deterministic rule and detect mismatched docks.
- Lock marbles entering matching catch docks.
- Accept only if all marbles are correctly locked.

## Frontend and interaction requirements

- Use large left-rotate and right-rotate controls plus Q/E keyboard bindings.
- Prevent input while slide animation is resolving, or queue at most one next command.
- Show a persistent gravity arrow and a move counter.
- Restart and one-step Undo are recommended because the state is fully deterministic and cheap to snapshot.

## Edge cases and explicit rulings

- Specify whether two marbles moving toward the same cell stop, collide, or use reading-order priority; simultaneous stop is recommended.
- Locked marbles should become obstacles unless the puzzle version states otherwise.
- Animation duration must not affect cell outcome.
- Because this puzzle is highly solver-friendly, main-puzzle timing or deeper boards may be needed after playtesting.

## Minimum acceptance tests

- Server and frontend agree on multi-marble conflict fixtures.
- A valid alternate turn sequence passes even if it differs from the generator's reference solution.
- Unknown characters and over-cap sequences fail safely.
- No generated main puzzle is solvable in fewer than the configured minimum turns.

---
# 6. PRESSURE VALVES

**Module ID:** `pressure_valves`  
**Integration:** CURRENT ENGINE  
**Estimated implementation complexity:** Low to Medium  
**Primary mechanic:** Discrete multivariable system control and short-horizon forecasting.

## Player objective

Operate valves so every gauge enters its own safe target band at the same time, accounting for cross-effects and automatic drift after every turn.

## What appears on screen

The screen shows three or four large gauges with labelled safe bands and current values. Beneath them are valve controls. Each valve displays a concise effect legend such as `A +2, B -1`, while the drift rule is shown separately.

## Rules

- Each turn the player activates one valve, optionally with one of a small number of fixed settings.
- Valve effects apply first, then automatic drift applies to all gauges.
- Gauge values are integers or fixed-point tenths; floating-point accumulation is not permitted.
- Values are clamped only if the payload explicitly defines hard physical bounds. Crossing a red critical bound fails the attempt.
- The puzzle succeeds after a turn when every gauge lies inside its inclusive safe interval.
- The player may not submit an empty sequence unless the start state is valid, which generation must forbid.

## Main-puzzle specification

Four gauges, 4–6 valves, fixed integer effects, mild per-turn drift, a 4–9 action solution, and optional one-use valves. Recommended action cap: 14.

## Holding-puzzle specification

Two or three gauges, 2–3 valves, no one-use valve, and a 1–3 action solution. Recommended action cap: 5.

## Seeded procedural generation

- Choose target bands and a successful valve sequence first, then reverse-calculate a legal starting vector when the transformation is invertible, or sample starts and solve with breadth-first search over bounded gauge vectors and valve charges.
- Reject states that start inside all bands, have unavoidable critical failure, permit trivial repeated use of one valve, or require precision too fine for quick mental reasoning.
- Keep all numbers small enough to inspect visually; suggested magnitude range is -9 to 20.
- Ensure at least two valves have cross-gauge effects in main mode.
- Use the same integer arithmetic in generator, browser preview, and checker.

## Suggested public payload

```jsonc
{
  "variant": "main",
  "difficulty": 2,
  "time_hint_seconds": 25,
  "rules_version": 1,
  "gauges": [
    {
      "id": "a",
      "start": 2,
      "safe": [
        7,
        9
      ],
      "critical": [
        -3,
        15
      ]
    },
    {
      "id": "b",
      "start": 11,
      "safe": [
        4,
        6
      ],
      "critical": [
        0,
        16
      ]
    }
  ],
  "valves": [
    {
      "id": "v0",
      "effects": {
        "a": 2,
        "b": -1
      },
      "charges": null
    },
    {
      "id": "v1",
      "effects": {
        "a": -1,
        "b": -2
      },
      "charges": 2
    }
  ],
  "drift": {
    "a": 0,
    "b": 1
  },
  "action_cap": 14
}
```

## Answer or action encoding

`{"v":1,"actions":["v0","v1","v0"]}`. If valves later have settings, encode each as `[valve_id, setting_id]`.

## Server-authoritative validation

- Initialize integer gauge values and valve charges.
- For each action, verify the valve exists and has charge, apply effects, decrement charge, then apply drift.
- Reject immediately if a critical bound is crossed after either the valve phase or drift phase, according to the documented rule.
- After each complete turn, check whether all values lie inside their safe bands.
- Accept only if the submitted sequence ends in a successful state; extra actions after first success should be rejected to keep encoding canonical and avoid ambiguous post-win failures.

## Frontend and interaction requirements

- Gauges must show exact numeric values as well as needles or bars.
- Valve effect labels must remain visible; the game tests planning, not memorizing hidden arithmetic.
- Animate value changes quickly and in two phases so players can see valve effect followed by drift.
- Provide Restart and Undo.

## Edge cases and explicit rulings

- Safe and critical bounds are inclusive unless explicitly versioned otherwise.
- A value that reaches success after valve effects but leaves the safe band after drift is not successful.
- Never use binary floating-point comparisons for correctness.
- Reject action sequences that continue after the first success state.

## Minimum acceptance tests

- Integer simulation is identical in Python and JavaScript fixtures.
- Every main puzzle needs at least two distinct valve types in a shortest solution.
- Critical-bound timing is tested before and after drift.
- The holding version can be understood without reading more than three short effect labels.

---
# 7. SHADOW CAST

**Module ID:** `shadow_cast`  
**Integration:** CURRENT ENGINE  
**Estimated implementation complexity:** Medium  
**Primary mechanic:** Three-dimensional spatial orientation using voxel projections.

## Player objective

Rotate a connected block structure until its front and top silhouettes match the two target silhouettes.

## What appears on screen

The central view is an isometric voxel object made from a small number of cubes. Two target projection grids labelled FRONT and TOP appear beside it. Six rotation buttons rotate the object around the X, Y, or Z axes in quarter turns.

## Rules

- The object consists of occupied integer voxel coordinates and remains rigid.
- An action is a 90-degree positive or negative rotation around one principal axis.
- After rotation, coordinates are normalized into a nonnegative bounding box for display and projection.
- A projection cell is filled if at least one voxel lies along that viewing ray.
- The puzzle succeeds when both required projections exactly match their targets. A later difficulty may add a side projection, but version 1 should use two.
- Multiple physical orientations that yield identical projections are all valid.

## Main-puzzle specification

A connected shape of 6–10 voxels inside a 4×4×4 bound, two 4×4 target silhouettes, and a scrambled orientation 2–5 quarter turns from a valid orientation.

## Holding-puzzle specification

A 3–5 voxel shape inside 3×3×3, one or two target projections, and a 1–2 turn scramble.

## Seeded procedural generation

- Generate a connected polycube by adding face-adjacent voxels.
- Enumerate the shape's 24 proper cube orientations and compute front/top projections for each.
- Choose a target orientation whose projection pair is not shared by too many orientations, then choose a different initial orientation at a desired rotation distance.
- Reject rotationally symmetric shapes that make controls appear ineffective, target pairs that are identical to the start, or silhouettes that are nearly empty/full and hard to read.
- The server need not store one canonical solution; it can compare submitted final projections.

## Suggested public payload

```jsonc
{
  "variant": "main",
  "difficulty": 2,
  "time_hint_seconds": 30,
  "rules_version": 1,
  "voxels": [
    [
      0,
      0,
      0
    ],
    [
      1,
      0,
      0
    ],
    [
      1,
      1,
      0
    ],
    [
      1,
      1,
      1
    ]
  ],
  "initial_orientation": 7,
  "targets": {
    "front": [
      "0110",
      "0100",
      "0000",
      "0000"
    ],
    "top": [
      "0110",
      "0010",
      "0000",
      "0000"
    ]
  },
  "action_cap": 12
}
```

## Answer or action encoding

`{"v":1,"turns":["x+","y-","z+"]}`. Only the six documented quarter-turn tokens are valid.

## Server-authoritative validation

- Initialize voxels in the declared initial orientation.
- Apply each integer rotation matrix and normalize coordinates.
- Compute front and top projection bitmaps using the versioned viewing axes.
- Accept only if both match exactly and the action cap is respected.
- Do not trust a submitted orientation index or bitmap.

## Frontend and interaction requirements

- Use SVG, CSS 3D, or canvas for the object, but derive it from the same voxel state rather than storing handcrafted images.
- Rotation controls need axis labels and curved-arrow icons; keyboard bindings may use X/Y/Z with Shift for inverse.
- Animate rotations briefly, then snap to the exact discrete orientation.
- Provide a no-motion mode that crossfades or instantly redraws while preserving the same interactions.

## Edge cases and explicit rulings

- Define front/top axes once and test them; inconsistent axis conventions are the largest implementation risk.
- Normalization must not accidentally mirror the object.
- Projection grids should be padded to fixed dimensions so bounding-box changes do not shift the target interpretation.
- Equivalent orientations must pass.

## Minimum acceptance tests

- All 24 orientation fixtures are consistent between Python and JavaScript.
- A known projection-equivalent alternate sequence passes.
- A mirrored but non-rotationally-equivalent shape does not pass.
- Generated main shapes respond visibly to each allowed axis rotation.

---
# 8. FOLDLINE

**Module ID:** `foldline`  
**Integration:** CURRENT ENGINE  
**Estimated implementation complexity:** High  
**Primary mechanic:** Coordinate transformation and overlap planning through paper folds.

## Player objective

Fold a marked sheet along permitted grid lines so required marks coincide while forbidden mark combinations remain separate.

## What appears on screen

A rectangular translucent grid contains marked dots, holes, or symbols. Candidate fold lines are shown between rows or columns. A target panel states required final groups, for example all triangle marks must overlap, while hazard pairs must not overlap.

## Rules

- A fold action specifies an axis, a grid line, and a direction indicating which side moves over the other.
- All points on the moving side are reflected across the fold line. Points on the stationary side do not move.
- The folded sheet's active footprint shrinks; later folds use coordinates in the current folded coordinate system.
- Marks may stack. Their stack retains every mark identity for validation.
- The puzzle succeeds when all required groups share coordinates and all forbidden pairs occupy different coordinates after the submitted fold sequence.
- Version 1 uses only horizontal and vertical folds on grid boundaries; diagonal folds are out of scope.

## Main-puzzle specification

A 6×6 or 6×8 sheet, 5–8 marks, 4–7 permitted fold lines, and a 2–3 fold solution. Recommended fold cap: 4.

## Holding-puzzle specification

A 4×4 sheet, 2–4 marks, and exactly one required fold.

## Seeded procedural generation

- Choose a legal fold sequence first and place mark groups so they overlap after that sequence.
- Add forbidden pairs and decoys that remain valid under the reference sequence.
- Simulate every permitted fold sequence up to the cap to determine accepted solutions and reject puzzles with no solution, already satisfied starts, excessive solution count, or indistinguishable folds.
- Ensure every reference fold changes the sheet and moves at least one relevant mark.
- Store mark identities and constraints publicly; do not store the reference fold sequence in the payload.

## Suggested public payload

```jsonc
{
  "variant": "main",
  "difficulty": 3,
  "time_hint_seconds": 35,
  "rules_version": 1,
  "rows": 6,
  "cols": 6,
  "marks": [
    {
      "id": "a",
      "kind": "triangle",
      "cell": [
        1,
        1
      ]
    },
    {
      "id": "b",
      "kind": "triangle",
      "cell": [
        1,
        4
      ]
    },
    {
      "id": "x",
      "kind": "cross",
      "cell": [
        5,
        0
      ]
    }
  ],
  "required_groups": [
    [
      "a",
      "b"
    ]
  ],
  "forbidden_pairs": [
    [
      "a",
      "x"
    ]
  ],
  "allowed_folds": [
    {
      "axis": "v",
      "line": 3,
      "direction": "right_to_left"
    },
    {
      "axis": "h",
      "line": 4,
      "direction": "bottom_to_top"
    }
  ],
  "fold_cap": 4
}
```

## Answer or action encoding

`{"v":1,"folds":[{"axis":"v","line":3,"dir":"rtl"},...]}`. Each fold must be valid for the current footprint, not merely the original sheet.

## Server-authoritative validation

- Initialize each mark as a coordinate plus identity and the current sheet footprint.
- For each fold, confirm it is permitted and valid in the current coordinate system, reflect moving-side coordinates, merge stacks, and update the footprint.
- After the final fold, verify every required group shares one coordinate and no forbidden pair shares a coordinate.
- Reject no-op folds, over-cap sequences, and folds that would create an invalid half-cell geometry under version 1.

## Frontend and interaction requirements

- Players should tap a fold line, see the selected moving side highlighted, then confirm the fold.
- Animate the fold schematically; exact 3D paper animation is unnecessary and may obscure state.
- After folding, stacked marks should fan or display a count so identities remain inspectable.
- Provide Restart; Undo is strongly recommended because only a few discrete transformations are involved.

## Edge cases and explicit rulings

- Odd sheet dimensions and off-centre folds can produce unequal sides; version 1 should permit them only when the moving side fits entirely over the stationary side.
- Marks on the fold boundary remain stationary.
- A required group of more than two marks must all occupy one shared coordinate, not merely pairwise overlap at different steps.
- The checker must use current-sheet coordinates consistently after each fold.

## Minimum acceptance tests

- All permitted fold sequences up to the cap can be exhaustively checked for generated boards.
- No-op and geometrically impossible folds fail.
- Reference constraints pass even with stacked decoys.
- Holding puzzles require one visually obvious but not pre-satisfied fold.

---
# 9. SIGNAL BUFFER

**Module ID:** `signal_buffer`  
**Integration:** CURRENT ENGINE  
**Estimated implementation complexity:** Medium  
**Primary mechanic:** Sequence transformation using limited temporary storage.

## Player objective

Process an incoming symbol stream through a tiny buffer so the output matches a required target order without overflowing or emitting the wrong symbol.

## What appears on screen

The interface shows an incoming queue, two or three buffer slots, an output track, and the target order. Buttons allow the next input to go directly to output, enter a selected buffer slot, release a buffered symbol, or swap buffer slots when that action is enabled.

## Rules

- Only the first symbol in the input queue can be consumed.
- A symbol may be sent directly to output or placed into an empty buffer slot.
- A buffered symbol may be released to output only from an occupied slot.
- If swap is enabled, it swaps two occupied or occupied/empty slots as explicitly defined; version 1 should allow swapping any two slots and count it as one action.
- The next emitted symbol must equal the next unmet target symbol. Emitting a different symbol fails immediately.
- Success occurs when the input queue and buffer are empty and the full target output has been produced.

## Main-puzzle specification

An input sequence of 7–10 symbols, 2–3 buffer slots, repeated symbol types, and a 10–18 action solution. Swap should be disabled initially unless needed for variety.

## Holding-puzzle specification

An input of 3–5 symbols, one buffer slot, and a 3–7 action solution.

## Seeded procedural generation

- Generate an input sequence with identifiable tokens and derive a reachable target by simulating random legal buffer actions.
- Use a solver over `(input_index, buffer_contents, output_index)` to verify reachability and estimate shortest action count.
- Reject targets identical to the input for main mode, states where the buffer is never needed, excessive repeated symbols that make token identity ambiguous, or puzzles outside the action-depth range.
- For repeated kinds, tokens may be considered interchangeable only if the rules state so. Version 1 should validate kinds, not unique serial numbers.
- Keep the full input and target public; the challenge is planning the manipulation.

## Suggested public payload

```jsonc
{
  "variant": "main",
  "difficulty": 2,
  "time_hint_seconds": 30,
  "rules_version": 1,
  "input": [
    "circle",
    "star",
    "square",
    "circle",
    "triangle"
  ],
  "target": [
    "star",
    "circle",
    "circle",
    "square",
    "triangle"
  ],
  "buffer_slots": 2,
  "allow_swap": false,
  "action_cap": 18
}
```

## Answer or action encoding

`{"v":1,"actions":[["store",0],["direct"],["release",0]]}`. Slot indices are zero-based.

## Server-authoritative validation

- Initialize input index, empty buffer, and output index.
- Replay each action, rejecting consumption from an empty input, storing into an occupied slot, releasing an empty slot, illegal swaps, and excessive actions.
- Whenever a symbol is emitted, compare it immediately with the next target symbol; reject on mismatch.
- Accept only when all input is consumed, all slots are empty, and the target is fully matched.

## Frontend and interaction requirements

- Tokens should move visibly between queue, buffer, and output after each action.
- Use tap controls on tokens and slots rather than drag-only interactions.
- Show buffer capacity and next required output prominently.
- Provide Undo and Restart because mistaken storage choices are common and the state is cheap to snapshot.

## Edge cases and explicit rulings

- A direct action is invalid when the input queue is empty.
- If repeated kinds are interchangeable, the UI must not imply that individual serial identity matters.
- Do not allow a target shorter or longer than the input.
- Reject extra actions after success.

## Minimum acceptance tests

- Every generated main puzzle requires at least one store and one release.
- A wrong emitted token fails at the exact action that emits it.
- Buffer overflow and empty release fail safely.
- The shortest solution depth is measured and remains within configured bounds.

---
# 10. TETHER

**Module ID:** `tether`  
**Integration:** CURRENT ENGINE  
**Estimated implementation complexity:** High  
**Primary mechanic:** Coupled path planning with a constrained connecting segment.

## Player objective

Move two endpoints through an obstacle field so each reaches its target while the tether between them never exceeds its allowed length or intersects forbidden barriers.

## What appears on screen

A grid or geometric board contains Endpoint A, Endpoint B, their matching targets, solid obstacles, tether-blocking barriers, and a visible line connecting the endpoints. The active endpoint is highlighted, with movement controls beside the board.

## Rules

- Each action selects one endpoint and moves it one orthogonal grid cell. Diagonal movement is disabled in version 1.
- The destination cell must be inside the board and not a solid obstacle.
- After the move, the straight tether segment between endpoint centres must be no longer than `max_length` and must not cross a tether-blocking barrier.
- Endpoints may not occupy the same cell unless a later rule explicitly permits it.
- The tether may touch an obstacle corner only according to a fixed geometric tolerance; version 1 should treat corner contact with a blocking barrier as collision.
- The puzzle succeeds when A is on Target A and B is on Target B simultaneously.

## Main-puzzle specification

An 8×8 grid, 6–12 obstacles/barriers, maximum tether length of 3–5 cells, and a 10–18 move solution. Recommended move cap: 28.

## Holding-puzzle specification

A 4×4 or 5×5 grid, one barrier, and a 3–6 move solution. Recommended move cap: 10.

## Seeded procedural generation

- Represent endpoint positions as a product state and precompute which position pairs satisfy length and line-of-sight constraints.
- Sample obstacles, starts, and targets, then run breadth-first search over `(posA, posB)` with alternating or freely selected endpoint moves.
- Reject boards with no solution, direct trivial paths, paths that move only one endpoint, excessive geometric ambiguity, or solutions outside the target depth.
- Prefer boards where the tether must be deliberately repositioned around a barrier rather than merely kept short.
- Use exact rational or integer segment-intersection logic on the server; do not rely on rendered pixel positions.

## Suggested public payload

```jsonc
{
  "variant": "main",
  "difficulty": 3,
  "time_hint_seconds": 35,
  "rules_version": 1,
  "rows": 8,
  "cols": 8,
  "a": {
    "start": [
      7,
      0
    ],
    "target": [
      0,
      2
    ]
  },
  "b": {
    "start": [
      7,
      3
    ],
    "target": [
      0,
      7
    ]
  },
  "solid_cells": [
    [
      4,
      3
    ],
    [
      4,
      4
    ]
  ],
  "barriers": [
    [
      [
        2,
        2
      ],
      [
        2,
        5
      ]
    ]
  ],
  "max_length_squared": 16,
  "move_cap": 28
}
```

## Answer or action encoding

`{"v":1,"moves":[["a","U"],["b","R"],...]}`.

## Server-authoritative validation

- Initialize endpoint cells and parse each endpoint/direction pair.
- Apply the proposed endpoint move, then check board bounds, solid-cell collision, endpoint overlap, squared tether length, and segment intersection against every barrier.
- Reject the sequence on the first illegal state.
- Accept only if both endpoints finish on their matching targets within the move cap.

## Frontend and interaction requirements

- Provide explicit A/B selector buttons and directional controls; clicking an endpoint may also select it.
- Render the tether continuously and show it tightening as length approaches the limit.
- When a move is locally illegal, shake or highlight the reason but do not mutate state.
- Provide Undo and Restart.

## Edge cases and explicit rulings

- Length should use squared Euclidean distance between cell centres to avoid floating-point square roots.
- Define barrier geometry on grid lines, not arbitrary screen pixels.
- A tether crossing a solid cell that is not declared a barrier should be either permitted or forbidden consistently; version 1 should make all solid obstacles tether-blocking to reduce rule burden.
- Moving onto the correct target does not lock an endpoint by default.

## Minimum acceptance tests

- Segment-intersection fixtures cover crossing, touching endpoints, touching corners, and parallel overlap.
- Every main solution moves both endpoints.
- Client visuals derive from the same grid geometry as validation.
- A final-coordinate-only claim cannot pass without a legal move sequence.

---
# 11. ORBIT SYNC

**Module ID:** `orbit_sync`  
**Integration:** CURRENT ENGINE  
**Estimated implementation complexity:** Medium  
**Primary mechanic:** Discrete rotating systems and timing-like planning without trusted real-time input.

## Player objective

Control several rotating symbol rings so the required symbols align along the highlighted radial line on the same turn.

## What appears on screen

Two to four concentric rings contain symbols in evenly spaced slots. A fixed alignment ray crosses the rings. Controls can reverse a ring, pause it for one tick, or advance a selected ring depending on the puzzle's action set. A tick button or action automatically advances time.

## Rules

- The system advances in discrete ticks. Each player action consumes one tick unless the payload defines a setup action that does not.
- Every unpaused ring advances by its current direction and speed after the control action.
- Reverse changes a ring's direction before movement. Pause prevents movement for that tick and consumes a limited pause charge if configured.
- The puzzle succeeds after a tick when the symbol at the alignment index on every ring matches that ring's required target symbol.
- Speeds are integer slot increments and wrap modulo ring length.
- No browser timestamp is part of correctness.

## Main-puzzle specification

Three rings of 6–10 slots, speeds of one or two slots per tick, 2–4 control types, and a 4–9 action solution. Recommended action cap: 14.

## Holding-puzzle specification

Two rings of 4–6 slots, one pause or reverse control, and a 1–3 action solution.

## Seeded procedural generation

- Choose ring symbol layouts, target symbols, initial offsets, directions, speeds, and limited control charges.
- Solve the finite state graph over offsets, directions, charges, and tick number.
- Reject starts already aligned, targets that align automatically without control in main mode, instances outside depth bounds, or layouts with indistinguishable repeated symbols at the alignment ray.
- Ensure controls are actually relevant; a shortest solution should use at least two rings or two control types in main mode.
- Use symbols plus colour so ring contents remain distinguishable.

## Suggested public payload

```jsonc
{
  "variant": "main",
  "difficulty": 2,
  "time_hint_seconds": 28,
  "rules_version": 1,
  "rings": [
    {
      "id": "r0",
      "symbols": [
        "star",
        "dot",
        "moon",
        "x",
        "dot",
        "square"
      ],
      "offset": 2,
      "direction": 1,
      "speed": 1,
      "target": "moon"
    }
  ],
  "controls": {
    "reverse": true,
    "pause_charges": {
      "r0": 1
    }
  },
  "alignment_index": 0,
  "action_cap": 14
}
```

## Answer or action encoding

`{"v":1,"actions":[["reverse","r0"],["pass"],["pause","r1"]]}`.

## Server-authoritative validation

- Initialize ring offsets, directions, and charges.
- For each action, validate and apply the control, then advance all rings according to current state.
- Reduce offsets modulo each ring length and inspect the alignment slot.
- Accept at the first post-tick state in which all target symbols align, and reject extra actions after success.
- Never accept elapsed-time or angle values from the client.

## Frontend and interaction requirements

- Animate one discrete rotation per tick and lock controls during the short transition.
- Display direction arrows, speed, and remaining pause charges beside each ring.
- Keep the alignment ray and target symbols persistent.
- Provide Undo and Restart.

## Edge cases and explicit rulings

- Repeated target symbols may create multiple valid alignments; this is acceptable when depth bounds remain suitable.
- Pause and reverse order must be fixed: recommended order is apply control, then move.
- A speed larger than ring length is normalized modulo length.
- Unknown ring IDs or exhausted charges fail.

## Minimum acceptance tests

- Python and JavaScript agree on offset modular arithmetic.
- Automatic no-control wins are rejected for main generation.
- Alternate valid control sequences pass.
- No real-time measurement appears in the answer schema.

---
# 12. ODD MOTION

**Module ID:** `odd_motion`  
**Integration:** CURRENT ENGINE WITH DISCLOSED PAYLOAD CAVEAT  
**Estimated implementation complexity:** Medium  
**Primary mechanic:** Visual anomaly detection across short generated animations.

## Player objective

Identify the one object whose motion rule differs from the group across a series of brief rounds.

## What appears on screen

Each round displays four to eight simple objects moving inside identical lanes or cells. Most follow one common temporal pattern; one differs in a controlled way such as an extra pause, reversed turn, shorter bounce, opposite rotation, or delayed start. After playback, selectable labels remain.

## Rules

- A main puzzle consists of several independent rounds. Each round has exactly one odd object.
- The animation plays once by default. A replay may be allowed only if it carries a race cost or is disabled for holding mode; version 1 should allow one replay in main and none in holding.
- The player selects one object per round, then submits all selections together.
- Objects must differ only in motion rule, not in a static giveaway such as initial colour, size, or position.
- A round answer is correct only if the selected object ID equals the generated odd ID.
- Animation timing is presentation data, not measured player reaction time.

## Main-puzzle specification

Four rounds, 6 objects per round, 1.5–2.5 second playback each, and anomaly magnitude tuned for approximately 4–8 seconds of observation per round including selection.

## Holding-puzzle specification

One round, 4 objects, approximately 1.2 second playback, and a clear single anomaly.

## Seeded procedural generation

- Choose a base motion program from a small versioned grammar: positions or rotations at fixed ticks.
- Clone it across objects, then modify exactly one parameter for one chosen object.
- Render all tracks with different spatial lanes but equivalent visibility.
- Reject anomalies that create static differences at tick zero, leave the viewport, overlap another object, or are too subtle/obvious under playtest thresholds.
- The payload necessarily contains enough keyframes or parameters for the browser to animate; a player inspecting traffic may derive the odd object. This is accepted only under the current casual-assistance threat model.

## Suggested public payload

```jsonc
{
  "variant": "main",
  "difficulty": 2,
  "time_hint_seconds": 32,
  "rules_version": 1,
  "rounds": [
    {
      "id": "q0",
      "duration_ms": 1800,
      "tick_ms": 150,
      "objects": [
        {
          "id": "o0",
          "program": {
            "kind": "bounce",
            "turn_tick": 5,
            "pause_ticks": 0
          }
        },
        {
          "id": "o1",
          "program": {
            "kind": "bounce",
            "turn_tick": 5,
            "pause_ticks": 1
          }
        }
      ]
    }
  ],
  "replays_allowed": 1
}
```

## Answer or action encoding

`{"v":1,"choices":[["q0","o1"],["q1","o4"]]}`. Every round ID must appear exactly once.

## Server-authoritative validation

- Validate round IDs and object IDs against the puzzle.
- Compare each selected object with the server-only odd ID for that round.
- Accept only if all rounds are correct and there are no duplicate or missing rounds.
- Do not validate reported replay count or viewing time from the client; these cannot be trusted under the current contract.

## Frontend and interaction requirements

- Use fixed-timestep keyframe interpolation so all devices show the same logical pattern despite frame-rate differences.
- Provide reduced-motion handling by reducing travel distance while retaining temporal pauses or direction changes; do not replace animation with static arrows because that changes the tested skill.
- After playback, freeze every object in a neutral final pose that does not reveal the anomaly.
- Labels must be large and selectable by touch.

## Edge cases and explicit rulings

- Background-tab throttling can distort playback; restart the round if the document becomes hidden during animation rather than pretending the observation was fair.
- Frame drops must not skip logical key events; derive state from elapsed animation time.
- Do not use dangerous flash frequencies.
- Traffic inspection remains a known caveat because motion programs are public.

## Minimum acceptance tests

- Every round has exactly one changed semantic parameter.
- The anomaly is not visible in the initial or frozen final frame.
- Missing, duplicate, or foreign round selections fail.
- Playtesting confirms anomaly detection is based on perception rather than tiny device-dependent timing differences.

---
# 13. VANISH TRACE

**Module ID:** `vanish_trace`  
**Integration:** CURRENT ENGINE WITH DISCLOSED PAYLOAD CAVEAT  
**Estimated implementation complexity:** Low to Medium  
**Primary mechanic:** Transient spatial-path memory and ordered route reproduction.

## Player objective

Watch a marker travel through a grid while its trail fades, then reproduce the complete ordered route by tracing the same cells.

## What appears on screen

A grid appears with a start marker. During presentation, a token moves cell by cell and the recent trail fades behind it. After the route disappears, the player traces through cells using pointer drag or sequential taps. The selected route remains visible during response.

## Rules

- The presented route is an ordered sequence of grid cells beginning at a marked start.
- Version 1 allows orthogonal moves only. A later difficulty may allow diagonals with explicit visual distinction.
- The response must visit the exact cells in the exact order. Repeated cells are allowed only when the generated route contains them.
- The player may backtrack locally before submitting, but the final submitted route must match from the start.
- Main mode may provide one replay only if testing shows the no-replay version is too punishing; holding mode should not provide replay.
- The game does not measure drawing speed, only route correctness.

## Main-puzzle specification

A 6×6 grid with an 8–12 step route, 180–260 ms per step, limited crossings, and at most two repeated cells.

## Holding-puzzle specification

A 4×4 grid with a 4–6 step route and no crossing.

## Seeded procedural generation

- Generate a bounded random walk from a non-edge-biased start while preventing long stationary-looking oscillations.
- Control self-crossings and repeated cells according to difficulty.
- Reject routes with ambiguous diagonal-looking transitions, excessive back-and-forth pairs, or a final route outside length bounds.
- The sequence must be sent to the renderer to animate, so it is visible in network traffic. This is the same accepted threat class as ECHO.
- Use seeded timing parameters only for presentation variation; logical route correctness depends solely on cells.

## Suggested public payload

```jsonc
{
  "variant": "main",
  "difficulty": 2,
  "time_hint_seconds": 25,
  "rules_version": 1,
  "rows": 6,
  "cols": 6,
  "route": [
    [
      4,
      1
    ],
    [
      3,
      1
    ],
    [
      3,
      2
    ],
    [
      2,
      2
    ],
    [
      2,
      1
    ]
  ],
  "step_ms": 220,
  "trail_cells": 2,
  "replays_allowed": 1
}
```

## Answer or action encoding

`{"v":1,"route":[[4,1],[3,1],[3,2],[2,2],[2,1]]}`.

## Server-authoritative validation

- Parse a coordinate list and enforce exact expected length.
- Reject out-of-range cells and transitions that violate the movement rules.
- Compare the submitted ordered route exactly with the server sequence.
- Do not accept a set of cells, compressed direction claim, or client success flag unless the encoding version explicitly supports and validates it.

## Frontend and interaction requirements

- Support both drag-across-cells and tap-each-cell entry. Interpolate drag positions carefully so fast movement does not skip crossed cells.
- Disable response input until presentation ends.
- Show a clear transition from Watch to Repeat.
- If the tab becomes hidden during presentation, restart the presentation rather than consuming the player's attempt.

## Edge cases and explicit rulings

- A route revisiting a cell must append it again; deduplicating selected cells is incorrect.
- Pointer movement crossing a cell corner should not add diagonal neighbours accidentally.
- The route payload leaks the sequence to traffic inspection by design; document this caveat.
- Reduced motion may shorten travel interpolation but cannot remove the ordered presentation.

## Minimum acceptance tests

- Exact route passes; same cells in a different order fail.
- Repeated-cell routes are preserved correctly.
- Touch tracing does not skip cells under a representative set of pointer paths.
- Renderer cleanup cancels active presentation animations on unmount.

---
# 14. THREADLINE

**Module ID:** `threadline`  
**Integration:** CURRENT ENGINE  
**Estimated implementation complexity:** Medium  
**Primary mechanic:** Continuous-looking route construction with geometric constraints.

## Player objective

Draw one cable through required anchors in order while avoiding hazards, self-intersection, and an excessive number of bends.

## What appears on screen

A grid or bounded plane contains a start socket, an end socket, numbered anchors, blocked cells or hazard regions, and optional directional ports on anchors. The current cable is drawn as the player selects adjacent vertices or drags through the grid.

## Rules

- The path begins at the start socket and ends at the end socket.
- The path travels along orthogonal grid edges in version 1.
- Anchors must be visited in their declared order. Passing through a later anchor early is either forbidden or ignored; version 1 should forbid it for clarity.
- The path cannot enter blocked cells, cross itself, reuse an edge, or exceed the configured bend count.
- Directional anchors require entry or exit through a specified side when present.
- The puzzle succeeds when the path reaches the end after visiting every anchor in order and all constraints hold.

## Main-puzzle specification

An 8×8 grid, 3–5 ordered anchors, 5–10 blocked cells, a bend limit of 6–10, and a route of 12–24 edges.

## Holding-puzzle specification

A 4×4 or 5×5 grid, 1–2 anchors, one obstacle, and a short route with a generous bend limit.

## Seeded procedural generation

- Generate a valid non-self-intersecting path first, select ordered vertices along it as anchors, then add obstacles outside the reference path.
- Optionally add decoy corridors while preserving at least one route.
- Use a bounded path solver with state `(position, next_anchor, used_edges_or_cells, bends, previous_direction)` for verification on small boards.
- Reject starts already adjacent to a trivial final path, boards with unreadably narrow touch targets, and instances where obstacles do not influence routing.
- Do not include the reference path in the public payload.

## Suggested public payload

```jsonc
{
  "variant": "main",
  "difficulty": 2,
  "time_hint_seconds": 30,
  "rules_version": 1,
  "rows": 8,
  "cols": 8,
  "start": [
    7,
    0
  ],
  "end": [
    0,
    7
  ],
  "anchors": [
    {
      "id": "a0",
      "cell": [
        6,
        3
      ],
      "order": 0,
      "entry": null,
      "exit": null
    }
  ],
  "blocked_cells": [
    [
      5,
      2
    ],
    [
      5,
      3
    ]
  ],
  "bend_cap": 8,
  "edge_cap": 30
}
```

## Answer or action encoding

`{"v":1,"path":[[7,0],[6,0],[6,1],...]}`. The path includes start and end coordinates.

## Server-authoritative validation

- Validate path length and coordinate bounds.
- For each consecutive pair, require one orthogonal grid step and reject blocked cells, repeated edges, forbidden repeated vertices, and self-intersection.
- Track direction changes to count bends.
- Track anchors in order and enforce directional-port constraints.
- Accept only if the final coordinate is the end, every anchor was visited in order, and all caps are respected.

## Frontend and interaction requirements

- Support drag tracing and tap-to-extend. The tap alternative is important on smaller devices.
- Snap to grid vertices or cells and show the next required anchor prominently.
- Allow local backtracking by removing the last segment before submission.
- Show bend count and maximum without showing a solver hint.

## Edge cases and explicit rulings

- Define whether touching an earlier cable vertex without crossing counts as self-intersection; version 1 should forbid repeated nonterminal vertices.
- A 180-degree immediate reversal should simply remove the last step in the renderer, but a submitted path containing it should fail as an edge reuse.
- Grid-edge and grid-cell path models must not be mixed between client and server.
- Anchor order must be checked from path traversal, not from client metadata.

## Minimum acceptance tests

- Reference paths pass while a geometrically similar path visiting anchors out of order fails.
- Self-crossing, edge reuse, blocked entry, and bend overflow have dedicated tests.
- No solution path is leaked publicly.
- The tap-based input can complete every holding puzzle without drag precision.

---
# Engine-extension candidates

The following concepts are retained because they offer strong live-play mechanics, but they cannot be implemented securely under the current final-answer-only contract.

---

# 15. PHASE LOCK

**Module ID:** `phase_lock`  
**Integration:** REQUIRES ENGINE EXTENSION  
**Estimated implementation complexity:** High  
**Primary mechanic:** Server-timed synchronization of continuously moving rings.

## Player objective

Freeze multiple continuously rotating rings when their openings overlap to create one complete passage.

## What appears on screen

Several rings rotate continuously at different angular speeds. Each has one or more gaps. The player has individual lock buttons or a single capture button depending on mode.

## Rules

- The authoritative server defines a start epoch, ring angular velocities, initial phases, lock state, and tolerance window.
- The client renders predicted positions from server time but cannot decide success.
- A lock action is sent immediately with a sequence number. The server stamps receipt time and computes the authoritative phase at that time, optionally compensating only with a bounded latency policy.
- Main mode may require locking rings one at a time; locked rings stop at the server-computed phase.
- Success occurs when all required gaps overlap within the configured angular tolerance.
- A miss may unlock the ring, consume an attempt, or fail the puzzle; version 1 should allow a small number of locks and fail after the action cap.

## Main-puzzle specification

Three rings, different speeds and directions, 2–4 lock actions, and a 6–12 second expected interaction window.

## Holding-puzzle specification

Two rings and one capture action with a generous tolerance.

## Seeded procedural generation

- Generate phases and velocities so a valid overlap window occurs within a bounded future horizon after puzzle activation.
- Use modular interval calculations to verify at least one window of sufficient duration.
- Reject windows occurring too early for network delivery, too late for holding mode, or with sub-frame tolerance.
- Seeded generation remains deterministic, but activation time is assigned by the server when the puzzle is mounted.

## Suggested public payload

```jsonc
{
  "variant": "main",
  "difficulty": 3,
  "rules_version": 1,
  "rings": [
    {
      "id": "r0",
      "initial_phase_mdeg": 0,
      "velocity_mdeg_per_ms": 45,
      "gaps": [
        [
          0,
          18000
        ]
      ]
    }
  ],
  "server_start_time": "authoritative timestamp supplied at activation",
  "tolerance_mdeg": 6000,
  "action_cap": 4
}
```

## Answer or action encoding

This cannot use only the existing final answer string. It needs live action messages such as `{type:"game_action", puzzle_id, seq, action:["lock","r0"]}` that the server timestamps and applies immediately.

## Server-authoritative validation

- Maintain authoritative live puzzle state in the match engine or a game-session object.
- On each action, verify sequence order and current puzzle identity, compute phases from server monotonic time, and update lock state.
- Declare success server-side and emit a state transition; do not wait for a final client summary.
- Define a tested latency policy. Do not accept arbitrary client event timestamps.

## Frontend and interaction requirements

- Synchronize rendering with periodic server time-offset estimates.
- Display connection degradation if timing uncertainty exceeds the accepted tolerance.
- Use reduced visual effects but preserve continuous movement.
- Disable the game entirely on connections whose latency policy cannot provide fair play, or use a much wider holding tolerance.

## Edge cases and explicit rulings

- Browser background throttling makes play unfair; pause or regenerate if the page becomes hidden.
- Reconnect cannot resume an old phase blindly; issue a fresh puzzle or a new activation epoch.
- Duplicate action messages must be idempotent by sequence number.
- This game should not ship until live game actions and server monotonic timing exist.

## Minimum acceptance tests

- A forged client timestamp cannot improve a result.
- Duplicate and reordered actions do not alter state twice.
- Server-side phase fixtures cover wrap-around at 0/360 degrees.
- Latency and visibility-change policies are tested end to end.

---
# 16. RHYTHM LOCK

**Module ID:** `rhythm_lock`  
**Integration:** REQUIRES ENGINE EXTENSION  
**Estimated implementation complexity:** High  
**Primary mechanic:** Server-observed temporal reproduction of a rhythm pattern.

## Player objective

Listen to or watch a short beat pattern and reproduce its timing within a configured tolerance.

## What appears on screen

A beat lane or set of pads presents a short rhythm using visual pulses, optional audio, and a metronome. The player then taps one large response pad or multiple pads depending on the variant.

## Rules

- The server defines the pattern as beat offsets from an activation epoch.
- After presentation, the response window opens at a server-defined time.
- Each tap is sent as a live action and timestamped on receipt by the server. Client timestamps may be retained for diagnostics but never trusted for correctness.
- Scoring aligns received taps with expected beats and checks absolute or relative interval errors using a versioned algorithm.
- Too many, too few, or grossly early taps fail.
- Version 1 should test interval reproduction rather than audio latency-sensitive absolute synchronization.

## Main-puzzle specification

A 5–8 beat pattern over approximately 2–4 seconds, using two interval lengths and one rest, followed by one response attempt.

## Holding-puzzle specification

A 3–4 beat pattern with wide interval tolerance and no syncopation.

## Seeded procedural generation

- Generate beat intervals from a small rhythm grammar and reject patterns with ambiguous near-duplicate intervals.
- Keep total duration inside the Relay timer budget.
- The pattern is deterministic by seed, while activation and response epochs are live server state.
- Offer a visual-only mode with equivalent timing information for users who cannot or do not use audio.

## Suggested public payload

```jsonc
{
  "variant": "main",
  "difficulty": 3,
  "rules_version": 1,
  "beat_offsets_ms": [
    0,
    400,
    800,
    1400,
    1800
  ],
  "presentation_mode": "visual_audio",
  "response_start": "server activation field",
  "interval_tolerance_ms": 140
}
```

## Answer or action encoding

Requires live `tap` action messages with monotonically increasing sequence numbers. A final list of client timestamps is not acceptable as authoritative evidence.

## Server-authoritative validation

- Collect authoritative server receipt times for the expected number of taps.
- Normalize the first tap to time zero and compare inter-tap intervals with expected intervals, optionally using bounded global tempo scaling if explicitly designed.
- Reject duplicate sequence numbers, taps outside the response window, excessive tap count, and hidden-page sessions.
- The server triggers success or failure after enough taps or window expiry.

## Frontend and interaction requirements

- Provide visual pulses even when audio is enabled.
- Use one large accessible tap target and Space/Enter keyboard support.
- Do not produce rapid unsafe flashes.
- Calibrate only display/audio presentation offset; never ask users to perform a long device calibration during a Relay match.

## Edge cases and explicit rulings

- Network jitter can distort server receipt intervals. This concept requires an explicit fairness threshold and may not be suitable for geographically distant players.
- Audio output latency differs by device; interval-based scoring reduces but does not eliminate this issue.
- Background tabs, lost focus, and reconnects should invalidate and regenerate the attempt.
- The game must have a non-audio equivalent.

## Minimum acceptance tests

- Fabricated client timestamps are ignored.
- The interval matcher is deterministic and covered around tolerance boundaries.
- The game detects excess and missing taps server-side.
- Playtests across device classes show an acceptable false-failure rate before launch.

---
# 17. BALANCE HOLD

**Module ID:** `balance_hold`  
**Integration:** REQUIRES ENGINE EXTENSION  
**Estimated implementation complexity:** High  
**Primary mechanic:** Continuous server-simulated control under drift and disturbances.

## Player objective

Continuously adjust one or more controls to keep a moving indicator inside a safe zone for a required duration.

## What appears on screen

A balance beam, moving dot, or pair of linked gauges drifts under simulated forces. The player holds left/right buttons, uses keyboard keys, or manipulates a slider while a server-authoritative survival meter fills.

## Rules

- The server owns the simulation state, fixed timestep, disturbance sequence, safe bounds, and accumulated safe duration.
- The client sends control-state changes such as left pressed, left released, right pressed, and right released.
- Between actions, the server advances the simulation according to monotonic time and the last known control state.
- Leaving the safe zone for longer than a small grace period fails; briefly touching the boundary follows the versioned inclusive/exclusive rule.
- Success occurs when the server's accumulated valid hold time reaches the target duration.
- A final client claim that it survived is never accepted.

## Main-puzzle specification

A 6–10 second hold with one controlled axis, bounded random disturbances, and slowly increasing drift. Two-axis control should be deferred until after playtesting.

## Holding-puzzle specification

A 3–4 second hold, broad safe zone, and mild deterministic drift.

## Seeded procedural generation

- Generate a deterministic force/disturbance sequence from the seed and verify with an automated controller that survival is possible within input-rate limits.
- Reject sequences requiring superhuman reaction speed or oscillation faster than network action delivery.
- Activation creates live server state with initial position, velocity, last-update time, and current control.
- Difficulty should alter zone width, disturbance amplitude, and target duration through configured presets, not random unfairness.

## Suggested public payload

```jsonc
{
  "variant": "main",
  "difficulty": 3,
  "rules_version": 1,
  "initial": {
    "position_milli": 0,
    "velocity_milli_per_s": 0
  },
  "safe_range_milli": [
    -300,
    300
  ],
  "target_hold_ms": 8000,
  "control_accel_milli_per_s2": 450,
  "disturbance_seed": "server-associated"
}
```

## Answer or action encoding

Requires live control-transition messages with sequence numbers. The engine must also process server timer ticks while no messages arrive. A final action log submitted after play is insufficient.

## Server-authoritative validation

- Advance the simulation from the last monotonic timestamp to each action receipt using a fixed or analytically integrated timestep.
- Apply the previous control during that interval, then update the current control state.
- Track time inside/outside the safe zone and trigger failure or success server-side.
- Ignore client position, velocity, survival time, and action timestamps.

## Frontend and interaction requirements

- Use press-and-hold controls with pointer-cancel and keyup cleanup so controls cannot stick.
- Interpolate between server snapshots for smooth visuals without making the visual state authoritative.
- Show network instability when prediction diverges significantly from corrections.
- On unmount or disconnect, send or infer neutral control immediately.

## Edge cases and explicit rulings

- Lost release messages can create stuck input; sequence numbers, heartbeat state, and neutral-on-timeout are required.
- Reconnect should start a fresh attempt rather than resume an unseen live simulation.
- Simulation must use server monotonic time, not wall-clock timestamps vulnerable to clock changes.
- This game is unsuitable until the engine supports active per-puzzle sessions and periodic simulation updates.

## Minimum acceptance tests

- No-message intervals still advance and can fail the simulation.
- Duplicate/reordered control transitions are handled safely.
- A client cannot pass by submitting a fabricated action log after the target duration.
- Automated feasibility and human playtests establish fair disturbance limits.

---
# 18. Recommended implementation order

Do not build all modules simultaneously. The highest-value first wave is:

1. **MIRROR RUN**, because it adds a new cognitive mechanic while fitting the current replay-based checker cleanly.
2. **OVERPRINT**, because it adds direct visual manipulation and has a bounded transform space that is practical to test.
3. **STACKDROP**, because it introduces causal simulation without requiring real-time server messages.
4. **LANE SHIFT**, after the shared deterministic simulation conventions have been proven by STACKDROP.
5. **SHADOW CAST**, because it broadens the library into 3D reasoning while retaining a small finite orientation space.
6. **THREADLINE**, because it provides a touch-first motor-spatial game with straightforward server geometry.

GRAVITY SHIFT, PRESSURE VALVES, SIGNAL BUFFER, TETHER, FOLDLINE, and ORBIT SYNC should follow after the first wave establishes conventions for replayable action JSON, deterministic simulation fixtures, and renderer cleanup.

ODD MOTION and VANISH TRACE can fit the current engine, but their complete presentation programs must be sent to the browser. They should ship only with the same explicit payload-inspection caveat already accepted for ECHO.

PHASE LOCK, RHYTHM LOCK, and BALANCE HOLD must remain disabled until the engine supports live, sequence-numbered game actions and authoritative active puzzle sessions. Implementing them as final client-submitted timestamps or action logs would create an easily forged correctness path.

# 19. Suggested repository layout

Each accepted module should follow this shape:

```text
backend/
  games/
    base.py
    mirror_run.py
    lane_shift.py
    ...
frontend/
  games/
    mirror_run.js
    lane_shift.js
    ...
tests/
  games/
    test_mirror_run.py
    test_lane_shift.py
    fixtures/
      mirror_run_cases.json
      lane_shift_cases.json
docs/
  CANDIDATE_GAMES_SPEC.md
```

Games with a shared deterministic simulator should keep the simulation rules in a small pure backend helper colocated with the module. The browser must independently implement the same rules for responsiveness, and shared JSON fixtures must test that both implementations produce identical state transitions.

# 20. Definition of done for a game entering rotation

A game is ready for the Relay stage rotation only when all of the following are true:

- Its backend generator and checker satisfy the existing module contract.
- Its renderer mounts and unmounts cleanly with no leaked input handlers or timers.
- At least 1,000 deterministic seeds generate without an invalid or unsolved instance under automated testing.
- A solver or constructive reference confirms every generated puzzle is solvable.
- Malformed and adversarial submissions cannot crash the checker or bypass legal transitions.
- Main and holding modes have distinct, tested complexity.
- Median human solve time falls in the intended range, and the 90th percentile is not so high that one unfamiliar player routinely stalls an entire match.
- The game is usable at phone width and has non-colour identification.
- The rules fit on a short pre-game instruction overlay and are understood in a first-play observation test.
- Any payload-inspection weakness is explicitly documented and accepted.
- Games marked **REQUIRES ENGINE EXTENSION** have real server-observed action support rather than a client-attested substitute.

The final selection should be based on the diversity of the full stage pack, not only on which modules are individually easiest to implement. A strong four-stage Relay should avoid placing two memory games, two sorting games, or two slow search puzzles next to each other. It should vary observation, manipulation, planning, and pressure across the match.
