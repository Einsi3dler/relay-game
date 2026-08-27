# Bomb Defuse Online — Implementation Specification

## 1. Purpose

Implement a lightweight online two-player bomb-defusal game inspired specifically by the supplied **Bomb Defuse Online** reference screenshots and observed game behaviour.

The implementation must reproduce the core visual structure, asymmetric two-player interaction, countdown pressure, sudden-death failure system, puzzle-module architecture, audio feedback, and bomb/manual separation.

This specification is intended to be handed directly to Codex or another implementation agent.

---

# 2. Source of Truth

Use the following priority order whenever implementation details conflict:

1. Behaviour explicitly confirmed in this specification.
2. Supplied screenshots of the original game.
3. Behaviour subsequently confirmed by the project owner.
4. Reasonable implementation decisions documented as assumptions.

Do **not** import rules or systems from *Keep Talking and Nobody Explodes* simply because Bomb Defuse Online was inspired by it.

The deployed CrazyGames build identifies the game as:

* HTML5
* Scratch-tagged
* 2-player
* point-and-click
* co-op
* 2D
* logic
* mouse-controlled

The original loaded package is small and lightweight, so this implementation should also remain lightweight.

---

# 3. Critical Non-Goals

Do not implement:

* 3D bomb geometry
* Three.js
* WebGL
* orbit cameras
* raycasting
* physics
* batteries
* serial numbers
* edgework indicators
* strikes
* KTANE-specific rules
* KTANE 6×6 mazes
* KTANE Simon vowel logic
* password modules
* hardware/Arduino support
* unnecessary backend complexity

Unless a mechanic is specifically defined in this document, do not infer it from another bomb-defusal game.

---

# 4. Game Concept

The game is played by exactly two participants.

## Player 1 — Defuser

The Defuser sees the physical bomb interface.

The Defuser:

* sees the countdown timer
* sees active puzzle modules
* interacts with puzzle controls
* can press the final OK button
* can Give Up
* does **not** see the manual

## Player 2 — Expert

The Expert sees the bomb-defusal manual.

The Expert:

* sees puzzle instructions
* navigates between manual pages
* cannot interact with the bomb
* cannot see the Defuser's bomb screen

Players must communicate verbally or through the surrounding application's communication system.

The bomb module itself must preserve this information asymmetry.

---

# 5. Host Application Integration

This game is expected to be embedded inside a larger online game system.

Do not implement a new matchmaking platform inside this module.

The surrounding application should provide or resolve:

```ts
type BombGameContext = {
  sessionId: string;
  playerId: string;
  role: "defuser" | "expert";
  difficulty: "easy" | "medium" | "hard";
};
```

The bomb game should expose callbacks/events such as:

```ts
onRoundStarted()
onPuzzleSolved(puzzleId)
onBombDefused()
onMissionFailed(reason)
onGiveUp()
onRoundRestarted()
onExit()
```

The host application may synchronize these events between the two players.

---

# 6. Round Duration

Every round begins with:

```text
180 seconds
```

Difficulty does **not** change the timer.

All difficulties use the same 180-second countdown.

The display therefore behaves approximately as:

```text
180
179
178
177
...
3
2
1
0
```

Use whole seconds.

No decimal display is required.

---

# 7. Timer Appearance

The timer is one of the most visually important parts of the bomb.

It must appear:

* in the upper-middle bomb panel
* large
* bold
* red
* highly visible
* centered within its panel

Do not make the timer small.

Do not replace it with a subtle modern digital-clock component.

Approximate visual treatment:

```css
font-family: Arial, Helvetica, sans-serif;
font-size: 64px;
font-weight: 700;
color: #ff0000;
text-align: center;
```

Exact size should be tuned against the reference screenshot.

---

# 8. Timer Implementation

Do not decrement the timer using render frequency.

Use an absolute deadline.

Example:

```ts
const endTime = performance.now() + 180_000;

const remainingSeconds = Math.max(
  0,
  Math.ceil((endTime - performance.now()) / 1000)
);
```

This prevents frame-rate differences from affecting gameplay.

---

# 9. Countdown Sound

The bomb produces one audible countdown tick every second.

Required behaviour:

```text
180 → tick
179 → tick
178 → tick
...
```

The exact order within a few milliseconds is not important.

The perceived behaviour should be:

> visible countdown + regular ticking clock pressure.

The ticking should remain consistent throughout the round.

Do not progressively accelerate the ticking unless added later.

---

# 10. Difficulty

Difficulty determines how many puzzle modules are active simultaneously.

```ts
const ACTIVE_PUZZLES = {
  easy: 1,
  medium: 2,
  hard: 3,
};
```

Therefore:

```text
Easy   = 1 active puzzle
Medium = 2 active puzzles
Hard   = 3 active puzzles
```

The timer remains 180 seconds for all three.

---

# 11. Puzzle Selection

At the beginning of each bomb:

1. Determine the number of active puzzles from difficulty.
2. Randomly select that number of puzzle types from the current playable puzzle pool.
3. Do not deliberately guarantee any particular combination.
4. Different rounds may contain different puzzle combinations.

Example Hard round:

```text
Maze
Simon Says
Mini Button
```

Another Hard round:

```text
According to Number
Maze
Mini Button
```

Random selection is a core part of the game.

---

# 12. Current V1 Playable Puzzle Pool

The initial implementation should contain:

```text
Maze
According to Number
Simon Says
Mini Button
```

Only these four modules have enough information to create a coherent first version without importing unrelated mechanics.

Additional original-game modules can be added later.

---

# 13. Duplicate Modules

For V1, select different puzzle types within one bomb.

Example:

Valid:

```text
Maze
Simon Says
Mini Button
```

Avoid:

```text
Maze
Maze
Simon Says
```

Implementation:

```ts
sampleWithoutReplacement(puzzlePool, puzzleCount);
```

---

# 14. Bomb Success Condition

Solving an individual puzzle does **not** immediately defuse the bomb.

When a puzzle is solved:

1. Disable its controls.
2. Close the orange shutter over its module bay.
3. Mark the module as solved.

Example:

```ts
module.status = "solved";
```

The bomb remains active until all selected modules are solved.

---

# 15. Final OK Button

The large green circular OK button is the final bomb confirmation control.

It is positioned in the centre of the bomb.

Before all puzzles have been solved, pressing the OK button is an error.

### Premature OK press

If the player presses OK before every active puzzle is solved:

```text
EXPLOSION
MISSION FAILED
```

There is no harmless press.

There is no warning.

There are no strikes.

---

# 16. OK Ready State

Once every active puzzle has been solved:

```ts
allSolved === true
```

the OK button begins flashing/pulsing.

Example:

```text
normal green
bright green
normal green
bright green
...
```

Keep the animation obvious but simple.

Recommended cycle:

```text
500–700 ms
```

Do not create an elaborate effect.

---

# 17. Bomb Defusal

When all modules are solved and the Defuser presses OK:

1. Freeze the countdown immediately.
2. Stop countdown ticking.
3. Disable all bomb interaction.
4. Play a success sound.
5. Transition to the defused state.
6. Display:

```text
BOMB DEFUSED
```

This is a successful round.

Suggested visual:

```text
large
bold
centred
green
```

The surrounding game system may decide what happens after victory.

---

# 18. Sudden-Death Rule

There are no strikes.

There are no recoverable mistakes.

Every incorrect gameplay action destroys the bomb immediately.

The core rule is:

```ts
wrongAction => missionFailed()
```

Examples include:

* invalid Maze movement
* incorrect Simon input
* incorrect According-to-Number answer
* Mini Button failure
* pressing OK too early
* timer reaching zero
* Give Up

There is no strike counter.

---

# 19. Explosion Behaviour

Whenever the round fails:

1. Immediately stop puzzle interaction.
2. Immediately stop the countdown.
3. Play a loud explosion / heavy bang sound.
4. Replace the bomb interface with a black screen.
5. Display:

```text
MISSION FAILED
```

in very large red lettering.

Recommended visual:

```css
background: #000;
color: #ff0000;
font-weight: 700;
font-size: clamp(48px, 10vw, 100px);
text-align: center;
```

The explosion sound should feel substantially louder and more dramatic than normal puzzle sounds.

---

# 20. Mission Failed Duration

Keep the Mission Failed screen visible for approximately:

```text
5 seconds
```

During these five seconds:

* there is no countdown
* puzzle interaction is disabled
* there is no recovery
* the failed bomb is dead

After approximately five seconds:

1. Destroy the old bomb state.
2. Generate a completely new random bomb.
3. Keep the same difficulty.
4. Reset the timer to 180 seconds.
5. Restart ticking.
6. Begin the new round.

Do not fade the failed bomb back into view.

The old bomb does not return.

---

# 21. Give Up

The Defuser screen contains a blue:

```text
Give up
```

button near the bottom-right edge.

Pressing Give Up immediately triggers the same failure flow as an incorrect puzzle action.

```text
Give Up
↓
Explosion sound
↓
Black screen
↓
MISSION FAILED
↓
5 seconds
↓
New bomb
```

No confirmation dialog is required.

---

# 22. Global Game State

Recommended state model:

```ts
type BombStatus =
  | "starting"
  | "active"
  | "failed"
  | "defused";

type BombState = {
  status: BombStatus;

  difficulty:
    | "easy"
    | "medium"
    | "hard";

  durationSeconds: 180;

  remainingSeconds: number;

  activePuzzleIds: string[];

  solvedPuzzleIds: string[];

  okReady: boolean;
};
```

No strike state is needed.

---

# 23. Logical Screen Size

Build the reference UI against an internal logical viewport close to:

```text
590 × 440
```

The supplied screenshots are approximately this size.

The entire game should scale proportionally when placed inside larger containers.

Recommended structure:

```text
Game viewport
    ↓
fixed logical size
    ↓
uniform CSS transform/scale
    ↓
responsive container
```

Do not independently rearrange the bomb at different desktop resolutions.

---

# 24. Responsive Scaling

Calculate:

```ts
scale = Math.min(
  availableWidth / 590,
  availableHeight / 440
);
```

Then scale the entire game surface uniformly.

Do not stretch horizontally.

Do not stretch vertically.

Maintain the reference aspect ratio.

---

# 25. General Visual Style

The original interface is intentionally simple and crude.

Preserve that.

Use:

* hard rectangles
* thick black outlines
* plain colours
* Arial/Helvetica-like text
* minimal animation
* simple shapes
* 2D presentation

Do not modernize it into:

* glassmorphism
* gradients everywhere
* rounded SaaS cards
* neon UI
* 3D effects
* fancy game-engine rendering

The simple visual appearance is intentional.

---

# 26. Shared Approximate Colour Palette

Use CSS variables so values can be tuned.

```css
:root {
  --manual-bg: #fff5bc;

  --black: #000000;
  --white: #ffffff;

  --panel-grey: #cbcbcb;
  --bomb-grey-dark: #373737;
  --bomb-grey: #666666;
  --bomb-grey-light: #aeaeae;

  --manual-exit-blue: #0e00a9;
  --bomb-background-blue: #001777;

  --shutter-orange: #ff6600;
  --shutter-orange-dark: #c95000;

  --success-green: #00ff02;
  --display-cyan: #00d4ff;

  --danger-red: #ff0000;
}
```

Values should be tuned after screenshot comparison.

---

# 27. Expert Manual Screen

The Expert view uses a pale-yellow background.

Approximate:

```css
background: #fff5bc;
color: #000;
font-family: Arial, Helvetica, sans-serif;
```

There should be no modern content card.

Content appears directly on the pale-yellow background.

---

# 28. Manual Exit Button

Each manual page has a blue Exit button in the upper-right corner.

Approximate:

```text
width: 53px
height: 34px
```

Style:

```css
background: #0e00a9;
color: white;
border: 0;
border-radius: 0;
```

Text:

```text
Exit
```

The host application should handle what Exit means.

---

# 29. Manual Main Page

The supplied reference shows:

```text
The Bomb:
```

in large text near the upper-left corner.

Below it is a large grey selector bordered heavily in black.

The reference selector shows module names such as:

```text
Wires
Timer
Memory
Keypads
Button
Maze
Read and Press
Simon Says
According to number
Mini Button
```

However, V1 should only enable manuals for implemented modules.

Do not implement missing module logic simply to make these buttons functional.

Unimplemented modules may either:

* be hidden in production V1, or
* remain visible but disabled during visual-development mode.

---

# 30. V1 Manual Entries

The working Expert manual must contain:

```text
Maze
According to number
Simon Says
Mini Button
```

These must be fully functional.

---

# 31. Defuser Bomb Layout

The bomb screen has a dark-blue external background.

A large grey/black bomb housing occupies most of the viewport.

The bomb contains a fixed module grid.

The reference visual approximately resembles:

```text
┌───────────┬───────────┬───────────┐
│ Puzzle    │   TIMER   │ Puzzle    │
├───────────┼───────────┼───────────┤
│ Puzzle    │    OK     │ Puzzle    │
├───────────┼───────────┼───────────┤
│ Puzzle    │ Puzzle    │ Puzzle    │
└───────────┴───────────┴───────────┘
```

The timer occupies the top-middle position.

The OK button occupies the centre position.

This leaves approximately seven visually available cells, although the original screenshot uses six shutter-style puzzle bays around the fixed controls.

Implement a fixed set of puzzle bays.

Difficulty controls how many of them are active.

---

# 32. Closed Puzzle Shutters

Inactive or solved puzzle bays use orange shutters.

Reference appearance:

* bright orange rectangle
* heavy black border
* three darker-orange vertical interior strips

Example component:

```tsx
<ClosedModulePanel />
```

Approximate structure:

```text
██████████████
█  ▌  ▌  ▌  █
█  ▌  ▌  ▌  █
█  ▌  ▌  ▌  █
██████████████
```

Do not require image assets.

CSS rectangles are sufficient.

---

# 33. Solved Module Animation

When a module is solved:

1. Disable the puzzle immediately.
2. Animate the orange shutter over it.

Suggested duration:

```text
250–400 ms
```

Keep it fast.

The player should clearly understand:

> this module is finished.

---

# 34. Puzzle Placement

V1 may assign selected puzzles to available puzzle bays randomly.

Example:

```ts
const bays = shuffle(availablePuzzleBays);

selectedPuzzles.forEach((puzzle, index) => {
  puzzle.bay = bays[index];
});
```

The timer and central OK positions never move.

If later reference material shows fixed puzzle-specific locations, change this without altering puzzle logic.

---

# 35. Common Puzzle Interface

Every puzzle module should implement a shared lifecycle.

```ts
interface PuzzleModule {
  id: string;

  type:
    | "maze"
    | "simon"
    | "according-to-number"
    | "mini-button";

  status:
    | "active"
    | "solved";

  reset(): void;
}
```

Puzzle modules should communicate upward.

Examples:

```ts
onSolved(puzzleId);
onFatalError(puzzleId, reason);
```

A puzzle must never directly control the global round screen.

The Bomb controller handles failure/success.

---

# 36. Maze — Core Mechanic

Maze is a navigation puzzle.

The Expert sees reference mazes.

The Defuser sees one generated maze instance.

The puzzle uses a:

```text
4 × 4
```

cell structure based on the supplied manual screenshot.

Do not implement a KTANE 6×6 maze.

---

# 37. Maze Markers

The Maze uses:

```text
Blue  = player position
Red   = destination
Green = identifying tip
```

The green marker helps the Expert identify which maze layout is currently active.

The Defuser must not touch or navigate into the green tip as a goal.

---

# 38. Maze Manual

The supplied manual contains eight reference mazes.

Display:

```text
4 mazes on the first row
4 mazes on the second row
```

Each maze should visually match the screenshot.

Approximate dimensions:

```text
70 × 70px
```

Walls:

```text
red
```

Tip:

```text
green
```

---

# 39. Maze Data Model

Do not draw independent manual and gameplay versions.

Both must use the same maze data.

Example:

```ts
type MazeDefinition = {
  id: number;

  tip: {
    col: number;
    row: number;
  };

  horizontalWalls: boolean[][];
  verticalWalls: boolean[][];
};
```

This guarantees that the Expert's manual matches the actual bomb.

---

# 40. Maze Selection

At bomb generation:

1. Randomly select one of the eight maze definitions.
2. Generate a player start cell.
3. Generate a different destination cell.
4. Display the identifying green marker belonging to that maze.

Start and destination must be reachable.

---

# 41. Maze Controls

The Defuser should have clickable directional controls:

```text
↑
← ↓ →
```

Keyboard support may also be added, but mouse/pointer controls are mandatory.

---

# 42. Maze Fatal Error

Before movement:

```ts
if (wallBlocks(direction)) {
  missionFailed("maze-wall");
}
```

Attempting to move through a wall immediately causes:

```text
EXPLOSION
MISSION FAILED
```

There is no warning or strike.

---

# 43. Maze Completion

When:

```ts
playerCell === destinationCell
```

the module is solved.

Trigger:

```ts
onSolved("maze");
```

Then close its shutter.

---

# 44. Simon Says — Visual Design

Simon contains four coloured controls:

```text
Red
Blue
Green
Yellow
```

The game presents a flashing sequence.

The Defuser does not repeat the literal colour.

The Expert translates it using the manual.

---

# 45. Simon Says Mapping

Because this implementation has no strike system, only the original manual's:

```text
No Strikes
```

mapping is relevant.

Use:

```ts
const SIMON_MAP = {
  red: "blue",
  blue: "red",
  green: "yellow",
  yellow: "green",
};
```

Therefore:

```text
Red flash    → press Blue
Blue flash   → press Red
Green flash  → press Yellow
Yellow flash → press Green
```

Do not implement vowel logic.

Do not implement strike-dependent mappings.

---

# 46. Simon Sequence

Use four stages for V1.

Stage 1:

```text
1 flash
```

Stage 2:

```text
2 flashes
```

Stage 3:

```text
3 flashes
```

Stage 4:

```text
4 flashes
```

Each successful stage appends one random colour to the existing sequence.

Example:

```text
Stage 1:
Red

Stage 2:
Red Yellow

Stage 3:
Red Yellow Blue

Stage 4:
Red Yellow Blue Green
```

---

# 47. Simon Timing

Recommended starting values:

```ts
const SIMON_FLASH_MS = 450;
const SIMON_GAP_MS = 250;
const SIMON_INPUT_DELAY_MS = 300;
```

These values are tunable.

Do not scatter them as magic numbers across components.

---

# 48. Simon Input

The player enters the translated sequence.

Example:

Flash:

```text
Red
Yellow
```

Correct input:

```text
Blue
Green
```

---

# 49. Simon Fatal Error

Any incorrect button in the sequence immediately triggers:

```ts
missionFailed("simon-wrong-input");
```

Do not reset Simon.

Do not replay the stage.

The entire bomb fails.

---

# 50. Simon Completion

Successfully completing stage 4 solves the module.

Close its shutter.

---

# 51. Mini Button — Purpose

Mini Button is a reaction-time puzzle.

The manual informs the Expert that:

* the button is intentionally tiny
* the player must wait
* the button eventually becomes red
* the player must react quickly and hold it

---

# 52. Mini Button States

Use:

```ts
type MiniButtonState =
  | "waiting"
  | "red"
  | "holding"
  | "ready-to-release"
  | "solved";
```

---

# 53. Mini Button Start

When the puzzle becomes active:

1. Show a very small neutral button.
2. Wait for a random period.
3. Turn the button red.

Recommended configurable delay:

```ts
minDelayMs: 2000
maxDelayMs: 6000
```

Use randomized timing per round.

---

# 54. Mini Button Reaction Window

Once the button becomes red, the Defuser must begin holding it quickly.

Initial V1 configuration:

```ts
reactionWindowMs: 700
```

If the player does not begin holding before this expires:

```text
EXPLOSION
MISSION FAILED
```

Keep this value configurable.

---

# 55. Mini Button Hold

Once held successfully:

```ts
requiredHoldMs: 750
```

After the required hold duration:

* change the button to green
* indicate that it can be safely released

The player then releases to solve the puzzle.

---

# 56. Mini Button Fatal Conditions

Immediately fail the bomb if:

```text
player presses before red
player waits too long after red
player releases too early
```

Do not create strikes.

---

# 57. Mini Button Completion

Correct sequence:

```text
wait
↓
red appears
↓
press quickly
↓
hold
↓
button becomes green
↓
release
↓
SOLVED
```

Close its shutter.

---

# 58. According to Number — Manual Structure

The manual contains eight:

```text
3 × 3
```

number grids.

Every grid contains:

```text
1 through 9
```

once each.

Each grid contains a green-highlighted:

```text
1
```

in a different position.

This green `1` acts as the identifying tip for the pattern.

---

# 59. According to Number — Pattern Data

Store the eight layouts as static data.

Example:

```ts
type NumberPattern = {
  id: number;
  grid: number[][];
  tip: {
    row: number;
    col: number;
  };
};
```

Do not construct the Expert manual independently.

The manual and playable module must use the same data source.

---

# 60. According to Number — Observed Patterns

Pattern 1:

```text
1 6 3
8 2 4
5 9 7
```

Pattern 2:

```text
5 7 9
2 4 3
6 8 1
```

Pattern 3:

```text
2 7 5
4 3 6
8 1 9
```

Pattern 4:

```text
5 3 9
1 7 2
8 6 4
```

Pattern 5:

```text
6 3 2
8 5 4
1 7 9
```

Pattern 6:

```text
8 2 4
3 1 7
6 9 5
```

Pattern 7:

```text
3 7 6
4 8 1
2 5 9
```

Pattern 8:

```text
6 1 4
2 9 7
3 8 5
```

Highlight the cell containing `1` in green.

---

# 61. According to Number — Defuser Interface

The reference bomb module contains:

* a cyan numeric display
* three buttons labelled `1`, `2`, `3`
* a vertical group of four progress boxes

Approximate structure:

```text
┌──────────────┐
│      3       │
├───┬───┬───┐ │
│ 1 │ 2 │ 3 │ □
└───┴───┴───┘ □
              □
              □
```

Use the supplied screenshot as the visual reference.

---

# 62. According to Number — V1 Rule

The exact original row-vs-column interpretation has not been directly confirmed.

For V1, use the following default:

> buttons `1`, `2`, and `3` correspond to the **column** containing the displayed number in the identified 3×3 grid.

Keep this rule configurable.

Example:

```ts
const ACCORDING_TO_NUMBER_AXIS:
  "column" | "row" = "column";
```

If later verification shows that the original uses rows, changing this constant should be sufficient.

---

# 63. According to Number — Example

Selected manual pattern:

```text
1 6 3
8 2 4
5 9 7
```

Bomb displays:

```text
3
```

The number `3` is located in:

```text
column 3
```

Correct Defuser input:

```text
3
```

If the bomb later displays:

```text
2
```

`2` is located in:

```text
column 2
```

Correct input:

```text
2
```

---

# 64. According to Number — Progress

Use four stages.

The right-side boxes display progress.

Initial:

```text
□
□
□
□
```

After one success:

```text
■
□
□
□
```

After two:

```text
■
■
□
□
```

After four:

```text
■
■
■
■
```

Then solve the module.

---

# 65. According to Number — Stage Generation

For each stage:

1. Display a random number from 1–9.
2. Determine its position in the selected pattern.
3. Calculate the correct response from the configured axis.
4. Wait for Defuser input.

Avoid immediately repeating the same displayed number when practical.

---

# 66. According to Number — Fatal Error

Wrong button:

```text
EXPLOSION
MISSION FAILED
```

No second attempt.

---

# 67. According to Number — Completion

Four correct answers solve the module.

Close its shutter.

---

# 68. Expert Maze Manual Page

Heading:

```text
Maze
```

Instruction concept:

```text
Blue is the player's position.
Guide the player to the red destination.
Green is the identifying tip.
```

Show all eight maze references at once if screen space permits.

---

# 69. Expert Simon Manual Page

Heading:

```text
Simon Says
```

Display the four-colour mapping clearly.

Since the game has no strikes, V1 should not display unused strike rows.

Show:

```text
Red    → Blue
Blue   → Red
Green  → Yellow
Yellow → Green
```

The player should be able to understand the page rapidly under time pressure.

---

# 70. Expert Mini Button Manual Page

Heading:

```text
The mini button
```

Instruction:

```text
Wait for the tiny button to turn red.
When it turns red, tell the Defuser to press and hold it immediately.
Release only after the button turns green.
```

Do not expose exact milliseconds to the Expert.

---

# 71. Expert According-to-Number Page

Heading:

```text
According to number
```

Show all eight 3×3 patterns.

The `1` cell in each pattern must be highlighted green.

Instruction for V1:

```text
The green 1 identifies the correct number grid.

When the Defuser tells you the displayed number,
find that number in the matching grid.

Tell the Defuser which column it is in:

left column   = 1
middle column = 2
right column  = 3
```

If the axis is later changed to rows, update these instructions from the same configuration.

---

# 72. Pointer Input

Use pointer events.

Prefer:

```text
pointerdown
pointerup
pointercancel
```

instead of implementing mouse and touch independently.

This supports:

* desktop mouse
* mobile touch
* hold behaviour for Mini Button

---

# 73. Pointer Capture

For held controls:

```ts
element.setPointerCapture(event.pointerId);
```

Use pointer capture so small cursor movement does not accidentally cancel the hold.

---

# 74. Audio System

Minimum required sounds:

```text
countdown tick
explosion / heavy bang
success / bomb defused
button click
Simon flash tone
```

Optional:

```text
shutter close
Mini Button ready cue
```

Do not reuse copyrighted original-game sound assets unless the project has permission.

Use newly created or properly licensed equivalents.

---

# 75. Audio Priorities

Explosion must overpower normal sounds.

When failure happens:

```ts
stopTick();
stopPuzzleAudio();
playExplosion();
```

When defused:

```ts
stopTick();
stopPuzzleAudio();
playSuccess();
```

Do not allow ticking to continue underneath Mission Failed or Bomb Defused.

---

# 76. Bomb Failure Function

Centralize failure logic.

Example:

```ts
function missionFailed(reason: FailureReason) {
  if (bomb.status !== "active") return;

  bomb.status = "failed";

  stopTimer();
  stopAllPuzzleInput();
  stopNormalAudio();

  playExplosion();

  showMissionFailedScreen();

  emit("mission-failed", {
    reason
  });

  setTimeout(() => {
    startFreshBomb();
  }, 5000);
}
```

Every fatal event must call this same function.

---

# 77. Defusal Function

Centralize success logic.

```ts
function defuseBomb() {
  if (!allPuzzlesSolved()) {
    missionFailed("premature-ok");
    return;
  }

  bomb.status = "defused";

  stopTimer();
  stopAllPuzzleInput();
  stopNormalAudio();

  playSuccess();

  showBombDefused();

  emit("bomb-defused");
}
```

---

# 78. Round Restart

After failure:

```ts
function startFreshBomb() {
  clearOldPuzzleState();

  const puzzles = selectRandomPuzzles(
    ACTIVE_PUZZLES[difficulty]
  );

  generatePuzzleInstances(puzzles);

  timer.reset(180);

  bomb.status = "active";

  timer.start();

  emit("round-restarted");
}
```

Never restore a failed bomb.

---

# 79. Random Generation

Use a simple random-generation abstraction.

Example:

```ts
interface RandomSource {
  int(min: number, max: number): number;
  pick<T>(items: T[]): T;
  shuffle<T>(items: T[]): T[];
}
```

A seedable development implementation is recommended for reproducible tests.

Do not add a complicated PRNG dependency solely for production gameplay.

---

# 80. Development Debug Mode

Create a debug-only panel.

It must allow the developer to:

```text
choose difficulty
select exact puzzle combination
force Maze layout
force Maze start/destination
force Simon sequence
force According-to-Number pattern
force According-to-Number display
force Mini Button red state
solve selected module
trigger failure
trigger timer zero
make OK ready
trigger bomb defused
```

This must not appear in production.

---

# 81. Separation of Game Logic and Rendering

Puzzle correctness must not depend on CSS or DOM coordinates.

Example:

Bad:

```ts
if (button.offsetLeft === 123) ...
```

Good:

```ts
if (selectedColumn === correctColumn) ...
```

Rendering reads state.

Game logic owns state.

---

# 82. Suggested Project Structure

```text
bomb-defuse/
│
├── BombGame.tsx
├── BombController.ts
├── bomb.types.ts
│
├── components/
│   ├── BombFrame.tsx
│   ├── BombTimer.tsx
│   ├── FinalOkButton.tsx
│   ├── ClosedModulePanel.tsx
│   ├── PuzzleBay.tsx
│   ├── GiveUpButton.tsx
│   ├── MissionFailedScreen.tsx
│   └── BombDefusedScreen.tsx
│
├── manual/
│   ├── ManualHome.tsx
│   ├── ManualLayout.tsx
│   ├── ManualExitButton.tsx
│   ├── MazeManual.tsx
│   ├── SimonManual.tsx
│   ├── AccordingToNumberManual.tsx
│   └── MiniButtonManual.tsx
│
├── puzzles/
│   ├── maze/
│   │   ├── MazePuzzle.tsx
│   │   ├── maze.logic.ts
│   │   └── maze.data.ts
│   │
│   ├── simon/
│   │   ├── SimonPuzzle.tsx
│   │   └── simon.logic.ts
│   │
│   ├── according-to-number/
│   │   ├── AccordingToNumber.tsx
│   │   ├── according.logic.ts
│   │   └── numberPatterns.ts
│   │
│   └── mini-button/
│       ├── MiniButton.tsx
│       └── miniButton.logic.ts
│
├── audio/
│   ├── useBombAudio.ts
│   └── audio.config.ts
│
├── config/
│   └── bomb.config.ts
│
└── styles/
    ├── bomb.css
    ├── manual.css
    └── variables.css
```

Adapt naming to the existing repository rather than forcing this exact structure.

---

# 83. Central Configuration

Keep tuning values in one place.

Example:

```ts
export const BOMB_CONFIG = {
  durationSeconds: 180,

  missionFailedDurationMs: 5000,

  difficulty: {
    easy: 1,
    medium: 2,
    hard: 3,
  },

  simon: {
    stages: 4,
    flashMs: 450,
    gapMs: 250,
    inputDelayMs: 300,
  },

  miniButton: {
    minDelayMs: 2000,
    maxDelayMs: 6000,
    reactionWindowMs: 700,
    requiredHoldMs: 750,
  },

  accordingToNumber: {
    stages: 4,
    axis: "column" as "column" | "row",
  },
};
```

Do not spread timing constants throughout the codebase.

---

# 84. Unimplemented Original Modules

The screenshots contain names for additional modules:

```text
Wires
Timer
Memory
Keypads
Button
Read and Press
```

Do not implement their mechanics in V1.

Do not substitute KTANE rules.

Mark them internally as:

```text
DEFERRED — ORIGINAL BEHAVIOUR NOT VERIFIED
```

They may be added later as independent puzzle modules.

---

# 85. No Fake Completeness

Codex must not create guessed puzzle mechanics merely because a UI button exists.

If functionality has not been specified:

```ts
throw new Error(
  "Puzzle behaviour not implemented"
);
```

during development rather than silently inventing behaviour.

---

# 86. Visual Regression Targets

Create screenshot tests at approximately:

```text
590 × 440
```

for:

```text
Defuser — initial bomb
Defuser — three active modules
Defuser — Mission Failed
Defuser — Bomb Defused
Expert — manual home
Expert — Maze
Expert — Simon Says
Expert — According to Number
Expert — Mini Button
```

---

# 87. Visual Acceptance Criteria

The implementation should visibly reproduce:

* pale-yellow Expert manual
* large headings
* blue top-right Exit
* thick black manual panel borders
* simple grey module boxes
* dark-blue bomb background
* large grey/black bomb casing
* bright orange shutters
* bold red top-middle timer
* cyan number display
* three number buttons
* central green OK button
* blue Give Up button
* hard square-edged visual style

Do not prioritize pixel-perfect text antialiasing.

Prioritize geometry, spacing, scale, colour, and hierarchy.

---

# 88. Gameplay Acceptance Test — Easy

Given:

```text
difficulty = easy
```

When the bomb starts:

```text
exactly one random puzzle is active
timer = 180
```

When that puzzle is solved:

```text
its shutter closes
OK begins flashing
```

When OK is pressed:

```text
timer freezes
success sound plays
BOMB DEFUSED appears
```

---

# 89. Gameplay Acceptance Test — Medium

Given:

```text
difficulty = medium
```

Then:

```text
exactly two different random puzzles are active
```

Solving only one must **not** activate OK.

After both are solved:

```text
OK flashes
```

---

# 90. Gameplay Acceptance Test — Hard

Given:

```text
difficulty = hard
```

Then:

```text
exactly three different random puzzles are active
```

All three may be interacted with in any order.

Only after all three are solved may OK be safely pressed.

---

# 91. Failure Acceptance Test

Given an active bomb:

When any puzzle produces an incorrect response:

```text
timer stops
controls immediately disable
explosion sound plays
screen becomes black
MISSION FAILED appears in red
```

After approximately five seconds:

```text
a fresh random bomb starts
same difficulty
timer returns to 180
```

---

# 92. Premature OK Acceptance Test

Given:

```text
one or more puzzles remain unsolved
```

When the Defuser presses OK:

```text
MISSION FAILED
```

No confirmation.

No warning.

---

# 93. Timer Failure Acceptance Test

When:

```text
remainingSeconds <= 0
```

trigger:

```ts
missionFailed("timer-expired");
```

The bomb must not remain visible at zero.

---

# 94. Give-Up Acceptance Test

When the Defuser clicks:

```text
Give up
```

trigger immediate Mission Failed.

Do not require confirmation.

---

# 95. Maze Acceptance Test

Given a Maze layout:

Correct movement:

```text
player marker moves
```

Movement through a wall:

```text
Mission Failed
```

Arrival at the red target:

```text
Maze solved
shutter closes
```

---

# 96. Simon Acceptance Test

Given flash:

```text
Red
```

correct response:

```text
Blue
```

Incorrect response:

```text
Mission Failed
```

Completing all four stages:

```text
Simon solved
shutter closes
```

---

# 97. Mini Button Acceptance Test

Pressing before red:

```text
Mission Failed
```

Ignoring red too long:

```text
Mission Failed
```

Releasing early:

```text
Mission Failed
```

Correct wait → press → hold → green → release:

```text
Mini Button solved
```

---

# 98. According-to-Number Acceptance Test

Given pattern:

```text
1 6 3
8 2 4
5 9 7
```

and display:

```text
3
```

with:

```ts
axis = "column"
```

correct response:

```text
3
```

Incorrect response:

```text
Mission Failed
```

Four correct stages:

```text
module solved
```

---

# 99. Performance Requirements

This is intentionally a lightweight game.

Avoid:

* large animation libraries
* heavy game engines
* 3D dependencies
* unnecessary network requests
* large textures

Normal DOM/CSS/SVG is sufficient.

Target smooth interaction on desktop and mobile browsers.

---

# 100. Implementation Order

Implement in this order.

## Phase 1 — Global Bomb

Build:

```text
bomb frame
timer
tick sound
orange shutters
OK button
Give Up
Mission Failed
Bomb Defused
difficulty handling
random puzzle selection
```

No detailed puzzle logic yet.

## Phase 2 — Expert Manual

Build:

```text
manual shell
navigation
Maze page
Simon page
According to Number page
Mini Button page
```

## Phase 3 — Maze

Build:

```text
maze data
manual renderer
Defuser renderer
movement
fatal wall collision
completion
```

## Phase 4 — Simon

Build:

```text
sequence generation
flash animation
manual mapping
input
fatal error
four-stage completion
```

## Phase 5 — According to Number

Build:

```text
eight static patterns
pattern identification
number display
1/2/3 buttons
progress boxes
four stages
fatal error
```

## Phase 6 — Mini Button

Build:

```text
random wait
red activation
reaction timer
hold detection
green ready state
release
fatal conditions
```

## Phase 7 — Multiplayer Integration

Connect:

```text
Defuser role
Expert role
round start
round result
restart
session lifecycle
```

to the existing application's multiplayer infrastructure.

## Phase 8 — Visual Pass

Compare against supplied screenshots.

Tune:

```text
sizes
spacing
border widths
font sizes
colours
bomb proportions
manual proportions
```

---

# 101. Final Rule for Codex

Do not expand the design beyond this specification merely to make the implementation appear more complete.

The core design principle is:

> **Simple controls, difficult communication, fatal mistakes, constant time pressure.**

If an original Bomb Defuse Online behaviour is later confirmed to differ from a V1 assumption, isolate the difference behind configuration or module logic rather than rewriting the entire game architecture.

The most important behaviours to preserve are:

```text
2-player asymmetric roles

180-second ticking timer

1 / 2 / 3 puzzles by difficulty

random puzzle selection

all active puzzles may be solved in any order

one wrong move = immediate explosion

black MISSION FAILED screen

automatic fresh bomb after ~5 seconds

solved puzzles close behind orange shutters

all puzzles solved → OK flashes

press OK → BOMB DEFUSED
```

These behaviours define the game.
