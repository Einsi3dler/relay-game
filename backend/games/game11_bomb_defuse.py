"""BOMB DEFUSE (Game 11, manual lookup under a fuse): a bomb of shuttered
puzzle bays, a red countdown, and one green OK button that only ever gets
pressed once.

Per `bomb.md`. The source game is a two-player co-op — a Defuser who can see
the bomb but not the manual, and an Expert who can see the manual but not the
bomb. The Relay seats both: the **Defuser** is a required role every team must
field, and their **Grandmaster** holds the manual on the leader dashboard
(docs/GAME_DESIGN.md §2c). The Defuser keeps a copy too, but flipping to it
hides the bomb while the fuse burns — so asking is faster than looking, and for
the first seven levels a Grandmaster who is busy elsewhere only ever slows
their Defuser down. From `WITHHOLD_FROM_LEVEL` the board also withholds one
page from the Defuser's copy, and the console becomes the only copy of it in
the match: on a deep board an absent Grandmaster strands their Defuser on one
bay, which is what makes the second seat the game rather than a convenience.

Four module types (`bomb.md` §12): MAZE, SIMON SAYS, ACCORDING TO NUMBER, and
the MINI BUTTON. Which ones are live is drawn per board; how many is the level's
job, 1 at level 1 up to 4 in the bonus-only tiers. Solving a module closes an
orange shutter over its bay; only when every live bay is shut may OK be pressed
— and a board comes in **banks**, so on the deepest tiers that OK arms the next
bank rather than ending the bomb.

**Sudden death** (§18) is the spine of the design: there are no strikes, and any
wrong action detonates the bomb. That maps cleanly onto the Relay loop — the
renderer plays the explosion, holds MISSION FAILED for five seconds, and submits
a `failed` transcript, which `check` rejects and the engine answers with a
brand-new board at the same level (§20's "generate a completely new random
bomb"). A failed board is never restartable in place, so no one can learn a maze
by walking into its walls.

What arrives at `check` is the whole defusal as an ordered move list, and the
server replays it: every maze step is re-walked against the layout, every Simon
press re-derived through the manual's mapping, every According-to-Number answer
re-read off the pattern, and the OK press is only accepted as the final move of a
board where everything else is already shut. No claimed verdict is read.

**Two honest limits, both client-side by nature.** The fuse and the mini
button's reaction window are *clocks*, and a clock a browser reports is a clock
a browser can lie about — the repo's rule is to never trust client-claimed
elapsed time, so neither is enforced server-side. They are real pressure on an
honest player and documented as unenforceable, in the same spirit as the
cosmetic screen-effect perks in `config.PERKS`. The mini button's hold code
narrows the gap a little: reaching the green state is what reveals the code the
transcript has to carry, so a forged transcript has to at least model the
module's state machine rather than assert "solved". It is not proof of timing,
and this module does not pretend otherwise.
"""

from __future__ import annotations

import json
import random
from collections import deque

from backend.games.base import PuzzleInstance

# 2: the board comes in *banks* (bomb.md §20's escalation, applied inside one
# board). `payload["modules"]` became `payload["banks"][i]["modules"]`, each
# bank carries its own fuse, and OK shuts a bank rather than always ending the
# bomb. Version 1 transcripts no longer parse, which is the point of the bump.
RULES_VERSION = 2

# --- the bomb frame (bomb.md §31) ---------------------------------------
# A 3x3 face: the timer holds the top-middle cell, the OK button the centre,
# Give Up the bottom-right. That leaves six shuttered bays around them.
BAY_COUNT = 6

MODULE_TYPES = ("maze", "simon", "according_to_number", "mini_button")

# --- MAZE (§36-§43) -----------------------------------------------------
MAZE_SIZE = 4                # the manual's 4x4 grid, never KTANE's 6x6

# The four sides of a cell and the step that crosses each one.
STEPS: dict[str, tuple[int, int]] = {
    "n": (-1, 0), "s": (1, 0), "e": (0, 1), "w": (0, -1),
}

# The eight reference mazes, drawn identically in the manual and walked by the
# server (§39: one data source, so the Expert page cannot drift from the bomb).
# Each is a spanning tree — every cell reachable, exactly one route between any
# two — so no start/goal pair can ever be unsolvable, and `tip` is the green
# marker that tells the player *which* of the eight they are looking at. The
# eight tips are distinct, which is what makes the lookup unambiguous.
#   h[r][c] — wall between (r, c) and (r + 1, c)   (3 rows x 4 cols)
#   v[r][c] — wall between (r, c) and (r, c + 1)   (4 rows x 3 cols)
MAZE_LAYOUTS: tuple[dict, ...] = (
    {"tip": (0, 1), "h": ((0, 1, 0, 0), (0, 0, 1, 0), (1, 1, 0, 0)),
     "v": ((1, 0, 0), (1, 0, 1), (0, 1, 1), (0, 0, 0))},
    {"tip": (0, 3), "h": ((1, 0, 1, 0), (0, 1, 1, 0), (0, 1, 0, 1)),
     "v": ((0, 1, 0), (1, 0, 0), (0, 1, 0), (0, 0, 0))},
    {"tip": (1, 0), "h": ((0, 1, 0, 0), (1, 1, 1, 0), (0, 0, 1, 0)),
     "v": ((1, 0, 0), (0, 0, 1), (0, 0, 1), (1, 0, 0))},
    {"tip": (1, 2), "h": ((0, 0, 0, 1), (0, 1, 1, 0), (0, 0, 1, 0)),
     "v": ((1, 0, 0), (1, 1, 0), (1, 0, 0), (0, 1, 0))},
    {"tip": (2, 1), "h": ((1, 1, 0, 0), (0, 0, 1, 0), (0, 1, 0, 0)),
     "v": ((0, 0, 1), (1, 0, 1), (0, 1, 0), (0, 0, 1))},
    {"tip": (2, 3), "h": ((1, 0, 0, 1), (0, 0, 1, 0), (0, 1, 0, 0)),
     "v": ((0, 1, 0), (1, 1, 0), (1, 0, 1), (0, 0, 0))},
    {"tip": (3, 0), "h": ((1, 1, 0, 0), (0, 1, 1, 0), (0, 0, 0, 0)),
     "v": ((0, 0, 1), (0, 1, 0), (1, 0, 1), (0, 1, 0))},
    {"tip": (3, 2), "h": ((1, 0, 1, 0), (0, 1, 1, 0), (0, 0, 0, 1)),
     "v": ((0, 1, 0), (1, 0, 0), (0, 1, 0), (1, 0, 0))},
)

# --- SIMON SAYS (§44-§50) -----------------------------------------------
SIMON_COLOURS = ("red", "blue", "green", "yellow")

# The manual's whole rule. There are no strikes in this game, so the source
# manual's strike-dependent rows do not exist and neither does KTANE's vowel
# logic (§45): one mapping, always.
SIMON_MAP: dict[str, str] = {
    "red": "blue", "blue": "red", "green": "yellow", "yellow": "green",
}

SIMON_FLASH_MS = 450         # §47 — tunable, and never duplicated in the client
SIMON_GAP_MS = 250
SIMON_INPUT_DELAY_MS = 300

# --- ACCORDING TO NUMBER (§58-§67) --------------------------------------
# The manual's eight 3x3 patterns, exactly as observed (§60). Every grid holds
# 1..9 once, and each one's `1` sits in a different cell — that cell is the
# green tip the bomb shows, and it is how the player picks the right grid.
NUMBER_PATTERNS: tuple[tuple[tuple[int, ...], ...], ...] = (
    ((1, 6, 3), (8, 2, 4), (5, 9, 7)),
    ((5, 7, 9), (2, 4, 3), (6, 8, 1)),
    ((2, 7, 5), (4, 3, 6), (8, 1, 9)),
    ((5, 3, 9), (1, 7, 2), (8, 6, 4)),
    ((6, 3, 2), (8, 5, 4), (1, 7, 9)),
    ((8, 2, 4), (3, 1, 7), (6, 9, 5)),
    ((3, 7, 6), (4, 8, 1), (2, 5, 9)),
    ((6, 1, 4), (2, 9, 7), (3, 8, 5)),
)

# §62: which axis the 1/2/3 buttons name is the one rule the source material
# never confirmed. "column" is the documented V1 reading; the manual page is
# written from this constant, so flipping it flips both halves at once.
ACCORDING_TO_NUMBER_AXIS = "column"      # "column" | "row"

# --- MINI BUTTON (§51-§57) ----------------------------------------------
MINI_MIN_DELAY_MS = 2000     # §53 — how long the tiny button stays neutral
MINI_MAX_DELAY_MS = 6000
MINI_CODE_MIN = 10           # the two-digit code the green state reveals
MINI_CODE_MAX = 99

# --- the withheld page (§2c: the Grandmaster becomes necessary) ---------
# From this tier up, the Defuser's own copy of the manual is missing one page
# and their Grandmaster's console is the only copy of it in the match. Below it
# the second seat is a speed advantage and nothing more.
#
# Never more than one page: a board with two dead pages is not a harder lookup,
# it is a board you cannot start. A level-8 board fields three bays of distinct
# types, so one withheld page always leaves two the Defuser can still read
# alone — the board slows down, it does not stop.
WITHHOLD_FROM_LEVEL = 8
WITHHELD_PAGES = 1

# --- dark fuse (§7: the timer is on the bomb, and the bomb is not yours) --
# The deepest tier and the bonus-only boards behind it hand the *clock* to the
# Grandmaster as well as the manual: the Defuser's face shows no number, runs
# no fuse of its own, and the only countdown in the match is the one on the
# console. That is only honest because the board's deadline is real server
# state (docs/GAME_MODULE_SPEC.md §6) — without it a blacked-out board would
# simply have no limit at all.
#
# The bonus-only tiers, and only those. Two reasons, both load-bearing:
#
#   - A bonus board is *chosen*. The hardest compound state this game can
#     reach — a page withheld, the clock gone, and two banks — is then always
#     something a player opted into, never something the ladder imposed on
#     them. Level 10 stays hard (§2c's withheld page) without becoming a
#     different game.
#   - It lines up exactly with banks, which are bonus-only too. So a blacked-out
#     board is the same board that comes in banks: one coherent step up rather
#     than two unrelated ones landing on different levels.
# 11 is the first bonus-only tier. The level table is defined below this
# point, so the tie is asserted in the tests rather than computed here.
DARK_FUSE_FROM_LEVEL = 11

# --- answer limits (expansion spec §4: cap before parsing) --------------
MAX_ANSWER_CHARS = 8000
MAX_MOVES = 200              # comfortably over a level-13 board's ~50

# --- the level curve ----------------------------------------------------
# One row per level 1..13; level 1 is the source game's "easy" bomb — a single
# module, four Simon stages, four According-to-Number stages, the spec's 700ms
# reaction window (§54) and 750ms hold (§55).
#
# `banks` is the shape of the bomb: one `(modules, fuse_seconds)` per bank of
# bays. Shut every bay in a bank and press OK and the *next* bank arms behind
# it on its own fresh fuse; the last one defuses. Most levels are a single
# bank, which is an ordinary bomb.
#
# A fuse *rises* wherever a module is added, because a fuse is only difficulty
# relative to the work it has to cover: three modules in the two-module fuse is
# not hard, it is impossible. Within a band it then tightens level by level,
# which is where the pressure actually comes from. A second bank always gets
# less time than the first — it is smaller, and by then the player knows the
# board. `time_hint_seconds` is the expected solve across every bank, not the
# cap — the Relay convention for that field.
#
# Levels 11..13 are BONUS-ONLY tiers, never served as a main board. They are
# the only ones that come in two banks, which is what makes them a different
# board rather than just a wider one.
MAIN_LEVEL_PARAMS: tuple[dict, ...] = (
    {"banks": [(1, 90)], "simon_stages": 4, "atn_stages": 4,
     "maze_moves": (4, 6), "react_ms": 700, "hold_ms": 750,
     "difficulty": 2, "time_hint": 28},                                   # 1
    {"banks": [(1, 82)], "simon_stages": 4, "atn_stages": 4,
     "maze_moves": (5, 7), "react_ms": 700, "hold_ms": 750,
     "difficulty": 2, "time_hint": 30},                                   # 2
    {"banks": [(2, 105)], "simon_stages": 4, "atn_stages": 4,
     "maze_moves": (5, 7), "react_ms": 700, "hold_ms": 780,
     "difficulty": 3, "time_hint": 45},                                   # 3
    {"banks": [(2, 98)], "simon_stages": 4, "atn_stages": 4,
     "maze_moves": (5, 8), "react_ms": 680, "hold_ms": 800,
     "difficulty": 3, "time_hint": 48},                                   # 4
    {"banks": [(2, 92)], "simon_stages": 5, "atn_stages": 5,
     "maze_moves": (6, 8), "react_ms": 680, "hold_ms": 820,
     "difficulty": 3, "time_hint": 52},                                   # 5
    {"banks": [(3, 120)], "simon_stages": 5, "atn_stages": 5,
     "maze_moves": (6, 9), "react_ms": 660, "hold_ms": 840,
     "difficulty": 4, "time_hint": 68},                                   # 6
    {"banks": [(3, 114)], "simon_stages": 5, "atn_stages": 5,
     "maze_moves": (6, 9), "react_ms": 660, "hold_ms": 860,
     "difficulty": 4, "time_hint": 70},                                   # 7
    {"banks": [(3, 108)], "simon_stages": 5, "atn_stages": 5,
     "maze_moves": (7, 10), "react_ms": 640, "hold_ms": 880,
     "difficulty": 4, "time_hint": 72},                                   # 8
    {"banks": [(3, 102)], "simon_stages": 5, "atn_stages": 6,
     "maze_moves": (7, 10), "react_ms": 640, "hold_ms": 900,
     "difficulty": 5, "time_hint": 75},                                   # 9
    {"banks": [(3, 96)], "simon_stages": 6, "atn_stages": 6,
     "maze_moves": (8, 11), "react_ms": 620, "hold_ms": 920,
     "difficulty": 5, "time_hint": 78},                                   # 10
    # The bonus-only tiers are where the bomb starts coming in banks: shut the
    # first one and a second arms behind it on a shorter fuse (§20's escalation
    # applied inside a single board rather than across boards).
    {"banks": [(3, 120), (2, 66)], "simon_stages": 6, "atn_stages": 6,
     "maze_moves": (8, 11), "react_ms": 620, "hold_ms": 950,
     "difficulty": 5, "time_hint": 120},                                  # 11 bonus
    {"banks": [(3, 114), (3, 78)], "simon_stages": 6, "atn_stages": 6,
     "maze_moves": (9, 12), "react_ms": 600, "hold_ms": 980,
     "difficulty": 5, "time_hint": 132},                                  # 12 bonus
    {"banks": [(4, 126), (3, 68)], "simon_stages": 6, "atn_stages": 6,
     "maze_moves": (9, 12), "react_ms": 600, "hold_ms": 1000,
     "difficulty": 5, "time_hint": 145},                                  # 13 bonus
)

# Holding is practice-mode only: one bank, one module, the shortest version of
# whichever type comes up, and a fuse that is still a fuse.
HOLDING_PARAMS = {
    "banks": [(1, 35)], "simon_stages": 2, "atn_stages": 2,
    "maze_moves": (2, 4), "react_ms": 800, "hold_ms": 600,
    "difficulty": 1, "time_hint": 10,
}


def _clamp_level(level: int) -> int:
    """`level` as the table reads it. Everything level-dependent goes through
    this, or a level past the last row would draw a *different* board from the
    row it clamps to."""
    return min(max(level, 1), len(MAIN_LEVEL_PARAMS))


def _params_for_level(level: int) -> dict:
    """Main-board knobs for `level`, clamped to the table."""
    return MAIN_LEVEL_PARAMS[_clamp_level(level) - 1]


def _withheld_pages(seed: int, level: int, banks: list[dict]) -> list[str]:
    """Which manual pages this board keeps from the Defuser (§2c).

    Only ever a page for a bay that is actually on the board: withholding the
    Simon page from a bomb with no Simon bay reads as a bug rather than as
    difficulty, and costs the Defuser nothing, which is worse.

    Drawn from a stream of its own rather than from the board's `rng`, so
    adding this changed no bomb anyone can generate — the same seed still
    builds the same bays it always did.
    """
    tier = _clamp_level(level)
    if tier < WITHHOLD_FROM_LEVEL:
        return []
    live = sorted({module["type"] for bank in banks for module in bank["modules"]})
    if not live:
        return []
    picker = random.Random(f"withheld:{seed}:{tier}")
    return sorted(picker.sample(live, min(WITHHELD_PAGES, len(live))))


def _dark_fuse(level: int) -> bool:
    """Whether this board's clock belongs to the Grandmaster rather than the
    Defuser. A tier property, not a draw: a bomb whose timer is sometimes there
    and sometimes not teaches nothing."""
    return _clamp_level(level) >= DARK_FUSE_FROM_LEVEL


Cell = tuple[int, int]


# --- maze geometry (shared by generation, validation and the solver) -----


def _wall_between(layout: dict, cell: Cell, side: str) -> bool:
    """True if the cable of a step from `cell` through `side` is blocked.

    The grid border counts as a wall, so this is the single question every maze
    step asks: the move is either through an open side or it is fatal.
    """
    row, col = cell
    delta_row, delta_col = STEPS[side]
    next_row, next_col = row + delta_row, col + delta_col
    if not (0 <= next_row < MAZE_SIZE and 0 <= next_col < MAZE_SIZE):
        return True
    if side == "n":
        return bool(layout["h"][next_row][col])
    if side == "s":
        return bool(layout["h"][row][col])
    if side == "e":
        return bool(layout["v"][row][col])
    return bool(layout["v"][row][next_col])


def _layout_for_tip(tip: Cell) -> dict | None:
    """The maze whose green tip sits on `tip` — the lookup the player makes."""
    for layout in MAZE_LAYOUTS:
        if layout["tip"] == tip:
            return layout
    return None


def _maze_route(layout: dict, start: Cell, goal: Cell) -> list[str] | None:
    """The sides to step through to walk `start` -> `goal`, or None.

    Breadth-first, so on a spanning tree it returns the one route that exists.
    Used to build the server-only reference transcript and, in the tests, to
    confirm every generated maze is walkable.
    """
    if start == goal:
        return []
    came: dict[Cell, tuple[Cell, str]] = {}
    seen = {start}
    queue: deque[Cell] = deque([start])
    while queue:
        cell = queue.popleft()
        for side in STEPS:
            if _wall_between(layout, cell, side):
                continue
            delta_row, delta_col = STEPS[side]
            nxt = (cell[0] + delta_row, cell[1] + delta_col)
            if nxt in seen:
                continue
            seen.add(nxt)
            came[nxt] = (cell, side)
            if nxt == goal:
                route: list[str] = []
                at = goal
                while at != start:
                    at, side_taken = came[at]
                    route.append(side_taken)
                return list(reversed(route))
            queue.append(nxt)
    return None


def _maze_distances(layout: dict, start: Cell) -> dict[Cell, int]:
    """Step count from `start` to every cell it can reach."""
    out = {start: 0}
    queue: deque[Cell] = deque([start])
    while queue:
        cell = queue.popleft()
        for side in STEPS:
            if _wall_between(layout, cell, side):
                continue
            delta_row, delta_col = STEPS[side]
            nxt = (cell[0] + delta_row, cell[1] + delta_col)
            if nxt not in out:
                out[nxt] = out[cell] + 1
                queue.append(nxt)
    return out


def _pattern_for_tip(tip: Cell) -> tuple[tuple[int, ...], ...] | None:
    """The According-to-Number grid whose green `1` sits on `tip`."""
    for pattern in NUMBER_PATTERNS:
        if pattern[tip[0]][tip[1]] == 1:
            return pattern
    return None


def _number_answer(
    pattern: tuple[tuple[int, ...], ...], shown: object, axis: str,
) -> int | None:
    """The button 1/2/3 that answers `shown` in `pattern` (§62-§63), or None if
    the number is not in the grid — which a generated board never is, but a
    hand-written payload can be, and neither side of the parity pair may throw.
    """
    for row in range(3):
        for col in range(3):
            if pattern[row][col] == shown:
                return (col if axis == "column" else row) + 1
    return None


# --- validation ---------------------------------------------------------
# One walk, mirrored move-for-move in frontend/games/bomb_defuse.js and locked
# to it by tests/games/fixtures/bomb_defuse_cases.json. The renderer runs it in
# `partial` mode after every action, which is how a wrong action detonates the
# bomb where the player makes it rather than at submit time.


def _is_number(value: object) -> bool:
    """True for a JSON number. `bool` is an `int` subclass, so `true` would
    otherwise sail through as the button 1 — and JavaScript, which has one
    number type, has to reach the same verdict for the parity fixture to mean
    anything, so a whole-valued float counts too.
    """
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _all_modules(payload: dict) -> list[dict]:
    """Every bay on the board, across every bank."""
    return [module for bank in payload["banks"] for module in bank["modules"]]


def _initial_state(payload: dict) -> dict[str, dict]:
    """Fresh per-module progress, before any move is replayed.

    Every bank's bays are here from the start, armed or not. Which ones may be
    touched is `bank`'s job, not this one's — keeping the map whole is what
    lets a stale move name a real bay and be refused for the right reason.
    """
    state: dict[str, dict] = {}
    for module in _all_modules(payload):
        if module["type"] == "maze":
            state[module["id"]] = {"solved": False, "cell": list(module["player"])}
        elif module["type"] == "simon":
            state[module["id"]] = {"solved": False, "stage": 0, "in_stage": 0}
        elif module["type"] == "according_to_number":
            state[module["id"]] = {"solved": False, "stage": 0}
        else:
            state[module["id"]] = {"solved": False}
    return state


def validate(payload: dict, moves: object, partial: bool = False) -> dict:
    """Replay `moves` over `payload`'s bomb and report what happened.

    `partial` drops the two end-of-board rules (the OK press must be there, and
    every bank must be shut by then) so the browser can ask the same question
    of a half-defused bomb — "is what I have done so far still survivable?" —
    through this exact code.

    The trust boundary runs between the two arguments. `moves` is whatever the
    client sent, so every shape of it is handled and none of it raises. The
    `payload` is the board this server generated, so it is taken as given — the
    same split the other action games use.

    Returns `{ok, reason, defused, bank, state}`. `bank` is the index of the
    bank currently armed, and equals `len(payload["banks"])` once the board is
    defused. `reason` is "" while nothing has gone wrong and otherwise one of
    the stable strings below.
    """
    state = _initial_state(payload)
    banks = payload["banks"]
    by_id = {module["id"]: module for module in _all_modules(payload)}
    bank_of = {
        module["id"]: index
        for index, entry in enumerate(banks)
        for module in entry["modules"]
    }
    bank = 0

    def report(ok: bool, reason: str, defused: bool = False) -> dict:
        return {"ok": ok, "reason": reason, "defused": defused,
                "bank": bank, "state": state}

    if not isinstance(moves, list):
        return report(False, "bad_shape")
    if len(moves) > MAX_MOVES:
        return report(False, "too_many_moves")

    defused = False
    for move in moves:
        if defused:
            # OK on the last bank ends the bomb. Anything after it is a
            # transcript that did not come from this game.
            return report(False, "after_ok")
        if not isinstance(move, dict):
            return report(False, "bad_shape")
        module_id = move.get("m")
        if not isinstance(module_id, str):
            return report(False, "bad_shape")

        if module_id == "ok":
            # §15: pressing OK with a bay of the armed bank still open is not a
            # warning, it is the explosion.
            armed = banks[bank]["modules"]
            if not all(state[module["id"]]["solved"] for module in armed):
                return report(False, "premature_ok")
            bank += 1
            if bank == len(banks):
                defused = True
            continue

        module = by_id.get(module_id)
        if module is None:
            return report(False, "unknown_module")
        if bank_of[module_id] != bank:
            # A bay of a bank that is not armed: either one already shut behind
            # you, or one that has not come up yet and cannot be pre-solved.
            return report(False, "wrong_bank")
        entry = state[module_id]
        if entry["solved"]:
            return report(False, "already_solved")   # a shut bay takes no input
        action = move.get("a")

        if module["type"] == "maze":
            if not isinstance(action, str) or action not in STEPS:
                return report(False, "bad_action")
            layout = _layout_for_tip(tuple(module["tip"]))
            if layout is None:
                return report(False, "bad_shape")    # payload names no maze
            cell = (entry["cell"][0], entry["cell"][1])
            if _wall_between(layout, cell, action):
                return report(False, "maze_wall")    # §42
            delta_row, delta_col = STEPS[action]
            entry["cell"] = [cell[0] + delta_row, cell[1] + delta_col]
            if entry["cell"] == list(module["goal"]):
                entry["solved"] = True               # §43

        elif module["type"] == "simon":
            if not isinstance(action, str):
                return report(False, "bad_action")
            if entry["in_stage"] >= len(module["sequence"]):
                return report(False, "bad_shape")    # payload shorter than it claims
            flashed = module["sequence"][entry["in_stage"]]
            if action != SIMON_MAP.get(flashed):
                return report(False, "simon_wrong")  # §49
            entry["in_stage"] += 1
            if entry["in_stage"] > entry["stage"]:
                # Stage k plays the first k+1 flashes; entering them all
                # appends the next colour and restarts the input (§46).
                entry["stage"] += 1
                entry["in_stage"] = 0
                if entry["stage"] == module["stages"]:
                    entry["solved"] = True           # §50

        elif module["type"] == "according_to_number":
            if not _is_number(action) or action not in (1, 2, 3):
                return report(False, "bad_action")
            pattern = _pattern_for_tip(tuple(module["tip"]))
            if pattern is None or entry["stage"] >= len(module["displays"]):
                return report(False, "bad_shape")    # payload names no pattern
            shown = module["displays"][entry["stage"]]
            if action != _number_answer(pattern, shown, module["axis"]):
                return report(False, "atn_wrong")    # §66
            entry["stage"] += 1
            if entry["stage"] == len(module["displays"]):
                entry["solved"] = True               # §67

        else:  # mini_button
            if not _is_number(action):
                return report(False, "bad_action")
            if action != module["code"]:
                return report(False, "mini_code")
            entry["solved"] = True                   # §57

    if partial:
        return report(True, "", defused)
    if not defused:
        return report(False, "missing_ok")
    return report(True, "", True)


# --- generation ---------------------------------------------------------


def _make_maze(rng: random.Random, module_id: str, bay: int, params: dict) -> dict:
    """A maze bay: one of the eight layouts, plus a start and a goal the level's
    number of steps apart. Neither marker ever lands on the green tip, which is
    an identifier and not a destination (§37)."""
    low, high = params["maze_moves"]
    for _ in range(200):
        layout = MAZE_LAYOUTS[rng.randrange(len(MAZE_LAYOUTS))]
        tip = layout["tip"]
        cells = [
            (row, col)
            for row in range(MAZE_SIZE)
            for col in range(MAZE_SIZE)
            if (row, col) != tip
        ]
        start = cells[rng.randrange(len(cells))]
        reachable = _maze_distances(layout, start)
        goals = sorted(
            cell for cell, steps in reachable.items()
            if low <= steps <= high and cell != tip
        )
        if not goals:
            continue
        goal = goals[rng.randrange(len(goals))]
        return {
            "id": module_id, "type": "maze", "bay": bay,
            "tip": list(tip), "player": list(start), "goal": list(goal),
        }
    raise RuntimeError("bomb_defuse: no maze fits the level's step range")


def _make_simon(rng: random.Random, module_id: str, bay: int, params: dict) -> dict:
    """A Simon bay: one colour per stage, never three of a kind in a row (a
    triple is hard to *count* under a flash, which is not the tested skill)."""
    sequence: list[str] = []
    while len(sequence) < params["simon_stages"]:
        colour = SIMON_COLOURS[rng.randrange(len(SIMON_COLOURS))]
        if len(sequence) >= 2 and sequence[-1] == sequence[-2] == colour:
            continue
        sequence.append(colour)
    return {
        "id": module_id, "type": "simon", "bay": bay,
        "stages": params["simon_stages"], "sequence": sequence,
        "flash_ms": SIMON_FLASH_MS, "gap_ms": SIMON_GAP_MS,
        "input_delay_ms": SIMON_INPUT_DELAY_MS,
    }


def _make_according_to_number(
    rng: random.Random, module_id: str, bay: int, params: dict,
) -> dict:
    """An According-to-Number bay: one of the eight patterns, named by the cell
    its green `1` sits in, and a number to look up per stage (§65: never the
    same number twice running)."""
    pattern = NUMBER_PATTERNS[rng.randrange(len(NUMBER_PATTERNS))]
    tip = next(
        (row, col)
        for row in range(3)
        for col in range(3)
        if pattern[row][col] == 1
    )
    displays: list[int] = []
    while len(displays) < params["atn_stages"]:
        shown = rng.randint(1, 9)
        if displays and displays[-1] == shown:
            continue
        displays.append(shown)
    return {
        "id": module_id, "type": "according_to_number", "bay": bay,
        "tip": list(tip), "axis": ACCORDING_TO_NUMBER_AXIS, "displays": displays,
    }


def _make_mini_button(
    rng: random.Random, module_id: str, bay: int, params: dict,
) -> dict:
    """A mini-button bay: how long the tiny button stays neutral, how fast the
    player has to catch it, how long to hold, and the code the green state
    shows (see the module docstring on what that code is and is not worth)."""
    return {
        "id": module_id, "type": "mini_button", "bay": bay,
        "delay_ms": rng.randint(MINI_MIN_DELAY_MS, MINI_MAX_DELAY_MS),
        "reaction_window_ms": params["react_ms"],
        "required_hold_ms": params["hold_ms"],
        "code": rng.randint(MINI_CODE_MIN, MINI_CODE_MAX),
    }


_BUILDERS = {
    "maze": _make_maze,
    "simon": _make_simon,
    "according_to_number": _make_according_to_number,
    "mini_button": _make_mini_button,
}


def _module_moves(module: dict) -> list[dict]:
    """The moves that shut one bay."""
    if module["type"] == "maze":
        layout = _layout_for_tip(tuple(module["tip"]))
        route = _maze_route(
            layout, tuple(module["player"]), tuple(module["goal"])
        ) if layout else None
        if route is None:
            raise RuntimeError("bomb_defuse: unwalkable maze")
        return [{"m": module["id"], "a": side} for side in route]
    if module["type"] == "simon":
        moves: list[dict] = []
        for stage in range(module["stages"]):
            moves += [
                {"m": module["id"], "a": SIMON_MAP[colour]}
                for colour in module["sequence"][: stage + 1]
            ]
        return moves
    if module["type"] == "according_to_number":
        pattern = _pattern_for_tip(tuple(module["tip"]))
        return [
            {"m": module["id"],
             "a": _number_answer(pattern, shown, module["axis"])}
            for shown in module["displays"]
        ]
    return [{"m": module["id"], "a": module["code"]}]


def _reference_moves(payload: dict) -> list[dict]:
    """A transcript that defuses this bomb: each bank's bays, then OK to arm
    the next, and a final OK that ends it.

    Generation runs it through `validate` before serving, which is the quality
    gate: a board that its own reference cannot defuse never reaches a player.
    It is server-only — `check` re-derives everything and accepts any correct
    defusal, not this one.
    """
    moves: list[dict] = []
    for bank in payload["banks"]:
        for module in bank["modules"]:
            moves += _module_moves(module)
        moves.append({"m": "ok"})
    return moves


# --- practice missions (set pieces) --------------------------------------
# Authored bombs: fixed bays, fixed fuse, the same board every time. They are
# **practice only** and deliberately never served as a match board — a bomb you
# can memorise is exactly the "shared, static, Google-able answer" the library's
# anti-cheat rules rule out (docs/GAMES_SPEC.md §0.1). What they are for is
# learning: the bomb is the one game no team opts out of, so every Defuser has
# to meet these four bays somewhere, and every Grandmaster has to find their way
# around the console before it matters.
#
# A spec names only what makes the mission the mission; `_mission_module` fills
# in the ids and the shared timings. Every mission is walked by its own
# reference transcript in the tests, so an unwalkable authored maze or a wrong
# According-to-Number display fails loudly rather than shipping.
MISSIONS: tuple[dict, ...] = (
    {
        "id": "maze_drill",
        "name": "Drill · the maze",
        "blurb": "One maze, one long fuse. Find the green tip in the manual, "
                 "then walk blue to red without touching a wall.",
        "banks": [{"fuse_seconds": 150, "modules": [
            {"type": "maze", "bay": 0, "tip": [0, 1], "player": [3, 3], "goal": [0, 0]},
        ]}],
    },
    {
        "id": "simon_drill",
        "name": "Drill · Simon Says",
        "blurb": "Three stages. The mapping never changes, so this is the one "
                 "page worth memorising.",
        "banks": [{"fuse_seconds": 150, "modules": [
            {"type": "simon", "bay": 1, "sequence": ["red", "green", "yellow"]},
        ]}],
    },
    {
        "id": "numbers_drill",
        "name": "Drill · according to number",
        "blurb": "Three lookups against one grid. Match the lit dot to the "
                 "green 1 and read off the column.",
        "banks": [{"fuse_seconds": 150, "modules": [
            {"type": "according_to_number", "bay": 2, "tip": [0, 0],
             "displays": [3, 8, 5]},
        ]}],
    },
    {
        "id": "button_drill",
        "name": "Drill · the mini button",
        "blurb": "A forgiving window and a short hold. Arming it commits you, "
                 "so read the page first.",
        "banks": [{"fuse_seconds": 120, "modules": [
            {"type": "mini_button", "bay": 3, "delay_ms": 2500, "code": 47,
             "reaction_window_ms": 1100, "required_hold_ms": 600},
        ]}],
    },
    {
        "id": "first_bomb",
        "name": "Mission · first bomb",
        "blurb": "Two bays and a real fuse. This is roughly a level-four board.",
        "banks": [{"fuse_seconds": 105, "modules": [
            {"type": "maze", "bay": 0, "tip": [2, 1], "player": [0, 0], "goal": [3, 3]},
            {"type": "according_to_number", "bay": 3, "tip": [1, 1],
             "displays": [4, 9, 2, 6]},
        ]}],
    },
    {
        "id": "second_bank",
        "name": "Mission · it comes in banks",
        "blurb": "Shut the first bank and a second arms behind it on a shorter "
                 "fuse. Do not relax when the shutters fall.",
        "banks": [
            {"fuse_seconds": 100, "modules": [
                {"type": "simon", "bay": 1,
                 "sequence": ["blue", "blue", "yellow", "red"]},
                {"type": "mini_button", "bay": 4, "delay_ms": 3200, "code": 61},
            ]},
            {"fuse_seconds": 70, "modules": [
                {"type": "maze", "bay": 2, "tip": [3, 0], "player": [0, 3], "goal": [3, 3]},
                {"type": "according_to_number", "bay": 5, "tip": [2, 2],
                 "displays": [7, 1, 9]},
            ]},
        ],
    },
    {
        "id": "the_gauntlet",
        "name": "Mission · the gauntlet",
        "blurb": "All four bays, then three more behind them. The hardest board "
                 "this bomb can be built, and then some.",
        "banks": [
            {"fuse_seconds": 135, "modules": [
                {"type": "maze", "bay": 0, "tip": [1, 2], "player": [3, 0], "goal": [0, 3]},
                {"type": "simon", "bay": 1,
                 "sequence": ["green", "red", "yellow", "blue", "green"]},
                {"type": "according_to_number", "bay": 2, "tip": [2, 0],
                 "displays": [8, 3, 6, 2, 5]},
                {"type": "mini_button", "bay": 3, "delay_ms": 4100, "code": 88,
                 "reaction_window_ms": 620, "required_hold_ms": 950},
            ]},
            {"fuse_seconds": 80, "modules": [
                {"type": "simon", "bay": 4,
                 "sequence": ["yellow", "blue", "blue", "red"]},
                {"type": "according_to_number", "bay": 5, "tip": [1, 0],
                 "displays": [9, 4, 7, 1]},
                {"type": "maze", "bay": 0, "tip": [3, 2], "player": [0, 0], "goal": [2, 3]},
            ]},
        ],
    },
)

MISSIONS_BY_ID = {mission["id"]: mission for mission in MISSIONS}


def _mission_module(spec: dict, module_id: str) -> dict:
    """One authored bay, with the boilerplate filled in.

    A spec carries only what makes the bay interesting; the shared timings and
    the axis come from the same constants a generated board uses, so a mission
    can never quietly drift from the real game.
    """
    module = {"id": module_id, "type": spec["type"], "bay": spec["bay"]}
    if spec["type"] == "maze":
        module.update(tip=list(spec["tip"]), player=list(spec["player"]),
                      goal=list(spec["goal"]))
    elif spec["type"] == "simon":
        module.update(stages=len(spec["sequence"]), sequence=list(spec["sequence"]),
                      flash_ms=SIMON_FLASH_MS, gap_ms=SIMON_GAP_MS,
                      input_delay_ms=SIMON_INPUT_DELAY_MS)
    elif spec["type"] == "according_to_number":
        module.update(tip=list(spec["tip"]), axis=ACCORDING_TO_NUMBER_AXIS,
                      displays=list(spec["displays"]))
    else:
        module.update(
            delay_ms=spec["delay_ms"],
            reaction_window_ms=spec.get("reaction_window_ms", 800),
            required_hold_ms=spec.get("required_hold_ms", 750),
            code=spec["code"],
        )
    return module


class BombDefuseGame:
    """Defuse the bomb: solve every live module from the manual, then press OK."""

    id = "bomb_defuse"
    name = "Bomb Defuse"

    def generate_main(self, seed: int, level: int = 1) -> PuzzleInstance:
        return self._generate(seed, kind="main", level=level)

    def generate_holding(self, seed: int) -> PuzzleInstance:
        return self._generate(seed, kind="holding")

    def _build(self, seed: int, kind: str, level: int = 1) -> tuple[dict, str]:
        """Payload + the reference transcript (server-only, used by tests)."""
        rng = random.Random(seed)
        params = _params_for_level(level) if kind == "main" else HOLDING_PARAMS

        banks = []
        made = 0
        for count, fuse in params["banks"]:
            # §13 applies *per bank*: a bank never asks the same question
            # twice, which is what caps one at four bays. A later bank may
            # reuse a type — by then the first instance is shut behind its
            # shutter and the player is reading a fresh board.
            types = rng.sample(MODULE_TYPES, count)
            bays = rng.sample(range(BAY_COUNT), count)
            banks.append({
                "fuse_seconds": fuse,
                "modules": [
                    _BUILDERS[module_type](rng, f"m{made + index}", bay, params)
                    for index, (module_type, bay) in enumerate(zip(types, bays))
                ],
            })
            made += count
        payload = {
            "variant": kind,
            "difficulty": params["difficulty"],
            "time_hint_seconds": params["time_hint"],
            "rules_version": RULES_VERSION,
            "bays": BAY_COUNT,
            "banks": banks,
            # Practice keeps the whole manual: a drill you cannot look up is
            # not a drill. Only a match board thins out.
            "withheld_pages": (
                _withheld_pages(seed, level, banks) if kind == "main" else []
            ),
            # The one honest thing the server can hold (docs/GAME_MODULE_SPEC.md).
            # A bank arming is a client-side event, so a per-bank deadline would
            # need the client to report it — client-claimed time, which this repo
            # refuses to trust. The sum of the fuses is the whole board's budget,
            # and no client that honours its own fuses can ever reach it. The
            # per-bank countdown on the face stays exactly as it was.
            "time_limit_seconds": sum(bank["fuse_seconds"] for bank in banks),
            # Practice has no Grandmaster to hand the clock to, so a blacked-out
            # drill would just be a drill with no clock.
            "hidden_deadline": kind == "main" and _dark_fuse(level),
        }

        moves = _reference_moves(payload)
        if not validate(payload, moves)["ok"]:
            raise RuntimeError(f"bomb_defuse generated an unsolvable bomb ({seed})")
        return payload, json.dumps({"v": RULES_VERSION, "moves": moves})

    def _generate(self, seed: int, kind: str, level: int = 1) -> PuzzleInstance:
        payload, answer = self._build(seed, kind, level)
        banks = payload["banks"]
        count = len(banks[0]["modules"])
        shape = (
            f"{count} live {'module' if count == 1 else 'modules'}, "
            f"{banks[0]['fuse_seconds']}s on the fuse"
        )
        behind = len(banks) - 1
        if behind:
            shape += (
                f", and {behind} more bank{'' if behind == 1 else 's'} "
                "armed behind it"
            )
        return PuzzleInstance(
            game_id=self.id,
            kind=kind,
            prompt=(
                f"Defuse the bomb: {shape}. Read the manual, solve every bay, "
                "then press OK. One wrong move and it goes off."
            ),
            answer=answer,   # server-only reference; check() replays instead
            payload=payload,
        )

    # --- practice missions ---
    # Not part of the GameModule contract: practice mode asks for these by
    # duck-typing, and a game without them simply offers main and holding.

    def missions(self) -> list[dict[str, str]]:
        """The authored practice boards, as `{id, name, blurb}`."""
        return [
            {"id": mission["id"], "name": mission["name"], "blurb": mission["blurb"]}
            for mission in MISSIONS
        ]

    def generate_mission(self, mission_id: str, seed: int = 0) -> PuzzleInstance:
        """One authored board. The same bomb every time — `seed` is ignored,
        which is the whole point of a set piece and also why these never reach
        a match."""
        mission = MISSIONS_BY_ID.get(mission_id)
        if mission is None:
            raise KeyError(f"no bomb mission {mission_id!r}")
        made = 0
        banks = []
        for bank in mission["banks"]:
            modules = []
            for spec in bank["modules"]:
                modules.append(_mission_module(spec, f"m{made}"))
                made += 1
            banks.append({"fuse_seconds": bank["fuse_seconds"], "modules": modules})
        payload = {
            "variant": "mission",
            "mission_id": mission["id"],
            "difficulty": min(5, 1 + made),
            "time_hint_seconds": sum(b["fuse_seconds"] for b in banks) // 2,
            "rules_version": RULES_VERSION,
            "bays": BAY_COUNT,
            "banks": banks,
            "withheld_pages": [],   # a set piece you cannot look up is not one
            "time_limit_seconds": sum(b["fuse_seconds"] for b in banks),
            "hidden_deadline": False,      # ...and neither does a set piece
        }
        moves = _reference_moves(payload)
        if not validate(payload, moves)["ok"]:
            raise RuntimeError(f"bomb mission {mission_id!r} is not defusable")
        return PuzzleInstance(
            game_id=self.id,
            kind="main",
            prompt=f"{mission['name']} — {mission['blurb']}",
            answer=json.dumps({"v": RULES_VERSION, "moves": moves}),
            payload=payload,
        )

    def check(self, puzzle: PuzzleInstance, answer: str) -> bool:
        try:
            raw = str(answer)
            if len(raw) > MAX_ANSWER_CHARS:
                return False   # cap the raw submission before any parsing
            text = raw.strip()
            if not text:
                return False
            data = json.loads(text)
            if not isinstance(data, dict) or data.get("v") != RULES_VERSION:
                return False
            if "failed" in data:
                # The renderer's own detonation report. It is wrong by
                # construction — the engine answers a wrong board with a fresh
                # one, which is exactly §20's new bomb.
                return False
            return validate(puzzle.payload, data.get("moves"))["ok"]
        except Exception:
            return False       # malformed input is just wrong, never a crash

    def reset(self) -> None:
        return None            # stateless
