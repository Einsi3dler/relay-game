"""SHADOW CAST (Game 9, 3D orientation): turn a block of cubes until the two
shadows it casts — FRONT and TOP — match the two target silhouettes.

Per game/RELAY_EXPANSION_GAMES_README.md §7. The object is a connected polycube
that stays rigid; the only move is a 90-degree turn about one principal axis, so
the whole puzzle lives in the 24 proper orientations of a cube. Several
orientations can cast the same pair of shadows, and all of them count as solved
— the player is matching projections, not guessing a canonical pose.

Axis conventions are pinned once here because the spec calls inconsistent axes
the largest implementation risk. `x` runs right, `y` runs away from the viewer,
`z` runs up. Both projections are read into the same fixed `bound`-square grid
with the same formula shape — `front[bound - 1 - z][x]` and
`top[bound - 1 - y][x]` — so the two grids share their column axis and line up
for the player, and neither can be quietly transposed without the other. The
grid is padded to a fixed size so a changing bounding box never shifts what a
target means.

The 24 orientations are enumerated once by breadth-first search from the
identity over the six quarter turns in the order they appear in `TURNS`; the
payload names a starting orientation by its index in that list, and the browser
rebuilds the same table. Every turn matrix has determinant +1, so normalising
into a nonnegative box can only translate the object, never mirror it.

Generation grows a random polycube, keeps only shapes whose 24 orientations are
all distinct — a symmetric shape makes the controls look broken — and picks a
target whose shadow pair few orientations share. The starting orientation is
then drawn from exactly `distance` quarter turns away in the 24-node rotation
graph, which makes every board solvable by construction and yields the reference
turn list for free. `check` replays the submitted turns through the same
projection code and never trusts a client's claim about an orientation, a
bitmap, or success.

One thing the spec's "2-5 quarter turns from a valid orientation" hides: that is
a scramble count, not a distance. Under the six quarter turns the cube's
rotation group has diameter 3 (1 orientation at distance 0, 6 at 1, 11 at 2, 6
at 3), so no board can ever need more than three turns. The level curve
therefore climbs through shape complexity and shadow ambiguity, with distance
pinned at its ceiling of 3 from level 6 on and a roomy action cap for
exploration.
"""

from __future__ import annotations

import json
import random
from collections import deque

from backend.games.base import PuzzleInstance

RULES_VERSION = 1

# The only six legal actions, in the fixed order that seeds the orientation
# table (changing this order renumbers `initial_orientation`).
TURNS = ("x+", "x-", "y+", "y-", "z+", "z-")

# Right-hand-rule quarter turns about each principal axis. All six have
# determinant +1: proper rotations only, so the object is never mirrored.
MATRICES: dict[str, tuple[tuple[int, ...], ...]] = {
    "x+": ((1, 0, 0), (0, 0, -1), (0, 1, 0)),
    "x-": ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
    "y+": ((0, 0, 1), (0, 1, 0), (-1, 0, 0)),
    "y-": ((0, 0, -1), (0, 1, 0), (1, 0, 0)),
    "z+": ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
    "z-": ((0, 1, 0), (-1, 0, 0), (0, 0, 1)),
}

IDENTITY = ((1, 0, 0), (0, 1, 0), (0, 0, 1))

# Face-adjacent growth directions for the polycube builder.
OFFSETS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))

# A silhouette that is nearly empty or nearly solid is unreadable (spec §7).
MIN_FILLED = 3
MIN_EMPTY = 2

# --- Level-1 board (the V5 baseline: generate_main(seed) == level 1) ---
MAIN_VOXELS = 6
MAIN_BOUND = 4               # projection grid is BOUND x BOUND
MAIN_EQUIVALENT = 4          # orientations allowed to share the target shadows
MAIN_DISTANCE = 2            # quarter turns from the nearest solved orientation
MAIN_ACTION_CAP = 12         # spec §7 recommended action cap

HOLD_VOXELS = 4
HOLD_BOUND = 3
HOLD_DISTANCE = 1
HOLD_ACTION_CAP = 6

GEN_ATTEMPTS = 300
MAX_ANSWER_CHARS = 400

# Main difficulty curve (docs/TASK_LIST.md V5): one row per level 1..10, level 1
# == the board above. `voxels` tops out under the spec's ceiling of 10 and
# `distance` at the rotation graph's diameter of 3; `max_equivalent` is the one
# knob that *falls* with difficulty — fewer orientations casting the target
# shadows means the pose has to be pinned down more exactly.
MAIN_LEVEL_PARAMS: tuple[dict, ...] = (
    {"voxels": 6, "max_equivalent": 4, "distance": 2, "difficulty": 2, "time_hint": 30},  # 1
    {"voxels": 6, "max_equivalent": 4, "distance": 2, "difficulty": 2, "time_hint": 30},  # 2
    {"voxels": 6, "max_equivalent": 4, "distance": 2, "difficulty": 2, "time_hint": 30},  # 3
    {"voxels": 7, "max_equivalent": 3, "distance": 2, "difficulty": 3, "time_hint": 36},  # 4
    {"voxels": 7, "max_equivalent": 3, "distance": 2, "difficulty": 3, "time_hint": 36},  # 5
    {"voxels": 7, "max_equivalent": 3, "distance": 3, "difficulty": 3, "time_hint": 36},  # 6
    {"voxels": 8, "max_equivalent": 3, "distance": 3, "difficulty": 4, "time_hint": 42},  # 7
    {"voxels": 8, "max_equivalent": 2, "distance": 3, "difficulty": 4, "time_hint": 42},  # 8
    {"voxels": 8, "max_equivalent": 2, "distance": 3, "difficulty": 4, "time_hint": 42},  # 9
    {"voxels": 9, "max_equivalent": 2, "distance": 3, "difficulty": 5, "time_hint": 48},  # 10
)

# A 3-or-4 cube shape cannot be fully chiral, so holding asks for half the
# orientations to be distinct rather than all 24.
HOLDING_PARAMS = {
    "voxels": HOLD_VOXELS, "max_equivalent": 6, "distance": HOLD_DISTANCE,
    "difficulty": 1, "time_hint": 10,
}

MAIN_DISTINCT = 24           # main shapes must have no rotational symmetry
HOLD_DISTINCT = 12


def _params_for_level(level: int) -> dict:
    """Main-board knobs for `level`, clamped to the 1..10 table."""
    return MAIN_LEVEL_PARAMS[min(max(level, 1), len(MAIN_LEVEL_PARAMS)) - 1]


Cell = tuple[int, int, int]
Matrix = tuple[tuple[int, ...], ...]
Shape = tuple[Cell, ...]


# --- The rotation group -------------------------------------------------


def _mul(left: Matrix, right: Matrix) -> Matrix:
    """`left` applied after `right` — a button press turns the object in the
    fixed screen frame, so the new matrix is the turn times the old one."""
    return tuple(
        tuple(sum(left[row][k] * right[k][col] for k in range(3)) for col in range(3))
        for row in range(3)
    )


def _enumerate_orientations() -> tuple[Matrix, ...]:
    """The 24 proper orientations, breadth-first from the identity over `TURNS`.

    Index 0 is always the identity; the rest are in discovery order, which is
    what `initial_orientation` refers to and what the browser must reproduce.
    """
    order = [IDENTITY]
    seen = {IDENTITY}
    queue: deque[Matrix] = deque([IDENTITY])
    while queue:
        current = queue.popleft()
        for token in TURNS:
            after = _mul(MATRICES[token], current)
            if after not in seen:
                seen.add(after)
                order.append(after)
                queue.append(after)
    return tuple(order)


ORIENTATIONS: tuple[Matrix, ...] = _enumerate_orientations()
_INDEX_OF = {matrix: index for index, matrix in enumerate(ORIENTATIONS)}

# Cayley graph of the rotation group: NEIGHBOURS[i][t] is the orientation you
# land on by applying TURNS[t] to orientation i.
NEIGHBOURS: tuple[tuple[int, ...], ...] = tuple(
    tuple(_INDEX_OF[_mul(MATRICES[token], matrix)] for token in TURNS)
    for matrix in ORIENTATIONS
)


# --- Shapes and shadows -------------------------------------------------


def _normalise(cells) -> Shape:
    """Slide into the nonnegative corner and sort — a pure translation, so two
    orientations compare equal exactly when they are the same pose."""
    lows = tuple(min(cell[axis] for cell in cells) for axis in range(3))
    return tuple(sorted(
        (cell[0] - lows[0], cell[1] - lows[1], cell[2] - lows[2]) for cell in cells
    ))


def _transform(shape: Shape, matrix: Matrix) -> Shape:
    return _normalise([
        tuple(sum(matrix[row][axis] * cell[axis] for axis in range(3)) for row in range(3))
        for cell in shape
    ])


def _extent(cells) -> int:
    """Longest side of the bounding box. Rotations only permute and negate the
    axes, so a shape that fits `bound` here fits it in all 24 orientations."""
    return max(
        max(cell[axis] for cell in cells) - min(cell[axis] for cell in cells) + 1
        for axis in range(3)
    )


def _project(shape: Shape, bound: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(front, top) silhouettes as row strings of "0"/"1".

    A cell is filled when at least one cube lies along that viewing ray. Both
    grids are `bound` square regardless of how big the shape actually is.
    """
    front = [["0"] * bound for _ in range(bound)]
    top = [["0"] * bound for _ in range(bound)]
    for x, y, z in shape:
        front[bound - 1 - z][x] = "1"
        top[bound - 1 - y][x] = "1"
    return (
        tuple("".join(row) for row in front),
        tuple("".join(row) for row in top),
    )


def _readable(pair: tuple[tuple[str, ...], tuple[str, ...]], bound: int) -> bool:
    """Both silhouettes have enough filled and enough empty cells to read."""
    cells = bound * bound
    for grid in pair:
        filled = sum(row.count("1") for row in grid)
        if filled < MIN_FILLED or filled > cells - MIN_EMPTY:
            return False
    return True


def _polycube(rng: random.Random, count: int, bound: int) -> Shape | None:
    """A connected shape grown one face-adjacent cube at a time, or None if it
    ran out of room inside `bound`."""
    cells = {(0, 0, 0)}
    while len(cells) < count:
        options = sorted({
            (cell[0] + dx, cell[1] + dy, cell[2] + dz)
            for cell in cells
            for dx, dy, dz in OFFSETS
        } - cells)
        rng.shuffle(options)
        for cell in options:
            if _extent(cells | {cell}) <= bound:
                cells.add(cell)
                break
        else:
            return None
    return _normalise(cells)


def _distances(accepting: set[int]) -> list[int]:
    """Quarter turns from every orientation to the nearest accepting one."""
    far = [-1] * len(ORIENTATIONS)
    queue: deque[int] = deque()
    for index in sorted(accepting):
        far[index] = 0
        queue.append(index)
    while queue:
        current = queue.popleft()
        for after in NEIGHBOURS[current]:
            if far[after] == -1:
                far[after] = far[current] + 1
                queue.append(after)
    return far


def _walk_down(rng: random.Random, start: int, far: list[int]) -> list[str]:
    """A shortest turn list from `start` to an accepting orientation."""
    turns: list[str] = []
    current = start
    while far[current] > 0:
        downhill = [
            token
            for at, token in enumerate(TURNS)
            if far[NEIGHBOURS[current][at]] == far[current] - 1
        ]
        token = rng.choice(downhill)
        turns.append(token)
        current = NEIGHBOURS[current][TURNS.index(token)]
    return turns


# --- Replay (shared by check() and the Python/JS parity fixture) ---------


def _matches(front: tuple[str, ...], top: tuple[str, ...], payload: dict) -> bool:
    targets = payload["targets"]
    return list(front) == list(targets["front"]) and list(top) == list(targets["top"])


def replay(payload: dict, turns) -> dict:
    """Walk `turns` from the payload's starting orientation.

    Returns the state after every turn plus the verdict, which is the shape the
    fixture pins so the browser can be locked to the same simulation.
    """
    shape = tuple(tuple(cell) for cell in payload["voxels"])
    matrix = ORIENTATIONS[payload["initial_orientation"]]
    bound = payload["bound"]
    steps: list[dict] = []
    legal = True
    for token in turns:
        if token not in MATRICES:
            legal = False
            break
        matrix = _mul(MATRICES[token], matrix)
        oriented = _transform(shape, matrix)
        front, top = _project(oriented, bound)
        steps.append({
            "voxels": [list(cell) for cell in oriented],
            "front": list(front),
            "top": list(top),
            "matched": _matches(front, top, payload),
        })
    return {
        "steps": steps,
        "legal": legal,
        "matched": bool(legal and steps and steps[-1]["matched"]),
    }


def _payload(shape: Shape, start: int, target_pair, params: dict, bound: int, kind: str) -> dict:
    return {
        "variant": kind,
        "difficulty": params["difficulty"],
        "time_hint_seconds": params["time_hint"],
        "rules_version": RULES_VERSION,
        "voxels": [list(cell) for cell in shape],
        "initial_orientation": start,
        "bound": bound,
        "targets": {"front": list(target_pair[0]), "top": list(target_pair[1])},
        "action_cap": MAIN_ACTION_CAP if kind == "main" else HOLD_ACTION_CAP,
    }


class ShadowCastGame:
    """Turn the block until both of its shadows land on their targets."""

    id = "shadow_cast"
    name = "Shadow Cast"

    def generate_main(self, seed: int, level: int = 1) -> PuzzleInstance:
        return self._generate(seed, kind="main", level=level)

    def generate_holding(self, seed: int) -> PuzzleInstance:
        return self._generate(seed, kind="holding")

    def _build(self, seed: int, kind: str, level: int = 1) -> tuple[dict, str]:
        """Payload + a reference turn list (server-only, used by tests)."""
        rng = random.Random(seed)
        main = kind == "main"
        params = _params_for_level(level) if main else HOLDING_PARAMS
        bound = MAIN_BOUND if main else HOLD_BOUND
        distinct = MAIN_DISTINCT if main else HOLD_DISTINCT

        for _ in range(GEN_ATTEMPTS):
            shape = _polycube(rng, params["voxels"], bound)
            if shape is None:
                continue
            poses = tuple(_transform(shape, matrix) for matrix in ORIENTATIONS)
            if len(set(poses)) < distinct:
                continue           # too symmetric: the controls would look dead
            pairs = tuple(_project(pose, bound) for pose in poses)

            candidates = list(range(len(ORIENTATIONS)))
            rng.shuffle(candidates)
            for target in candidates:
                if not _readable(pairs[target], bound):
                    continue
                accepting = {i for i, pair in enumerate(pairs) if pair == pairs[target]}
                if len(accepting) > params["max_equivalent"]:
                    continue       # the target shadows pin down too little
                far = _distances(accepting)
                starts = [i for i, steps in enumerate(far) if steps == params["distance"]]
                if not starts:
                    continue       # nothing sits exactly that far out
                start = rng.choice(starts)
                turns = _walk_down(rng, start, far)
                payload = _payload(shape, start, pairs[target], params, bound, kind)
                answer = json.dumps({"v": RULES_VERSION, "turns": turns})
                return payload, answer
        raise RuntimeError(f"shadow_cast generation failed for seed {seed}")

    def _generate(self, seed: int, kind: str, level: int = 1) -> PuzzleInstance:
        payload, answer = self._build(seed, kind, level)
        return PuzzleInstance(
            game_id=self.id,
            kind=kind,
            prompt="Turn the block until its FRONT and TOP shadows match both targets.",
            answer=answer,  # server-only reference; check() replays instead
            payload=payload,
        )

    def check(self, puzzle: PuzzleInstance, answer: str) -> bool:
        try:
            raw = str(answer)
            if len(raw) > MAX_ANSWER_CHARS:
                return False  # cap the raw submission before any parsing
            text = raw.strip()
            if not text:
                return False
            data = json.loads(text)
            if not isinstance(data, dict) or data.get("v") != RULES_VERSION:
                return False
            submitted = data.get("turns")
            payload = puzzle.payload
            if not isinstance(submitted, list) or not submitted:
                return False
            if len(submitted) > payload["action_cap"]:
                return False
            if any(turn not in MATRICES for turn in submitted):
                return False   # only the six documented quarter turns are legal
            return replay(payload, submitted)["matched"]
        except Exception:
            return False  # malformed input is just wrong, never a crash

    def reset(self) -> None:
        return None  # stateless
