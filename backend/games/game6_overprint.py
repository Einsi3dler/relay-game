"""OVERPRINT (Stage 6, layered composition): transform transparent layers so
their combined print exactly matches the target pattern.

Per game/RELAY_EXPANSION_GAMES_README.md §4. Each layer is a small cell
pattern that the player may translate, quarter-rotate, and (where flagged)
flip. The workspace composite is the Boolean OR of every placed layer and
must equal the target exactly — extra cells fail just like missing ones.

Transform order is fixed and mirrored by the frontend: flip_x (mirror
columns), flip_y (mirror rows), then `rot` clockwise quarter-turns, then
normalise the pattern so its bounding box starts at (0, 0), then translate
by (r, c). Generation composes the target from a chosen solution, verifies
the bounded transform space is not excessively ambiguous, and scrambles the
initial placements into a not-nearly-solved start. `check` recomputes the
composite from the submitted placements; the reference solution is kept
server-only in `answer` for tests and is never compared against, because
symmetric layers admit equivalent transform encodings.
"""

from __future__ import annotations

import json
import random

from backend.games.base import PuzzleInstance

RULES_VERSION = 1

MAIN_SIZE = 6
MAIN_LAYERS = 3
MAIN_CELLS = (2, 5)            # marked cells per layer
MAIN_MAX_OVERLAP = 2           # solution layers may share at most this many cells
MAIN_MIN_MISPLACED = 2         # layers that must start off their solution cells

HOLD_SIZE = 4
HOLD_LAYERS = 2
HOLD_CELLS = (2, 3)
HOLD_MAX_OVERLAP = 1
HOLD_MIN_MISPLACED = 1

GEN_ATTEMPTS = 400
SCRAMBLE_ATTEMPTS = 60         # per generated target
MAX_SOLUTIONS = 24             # reject boards more ambiguous than this
MAX_ANSWER_CHARS = 600

# Main-board difficulty curve (docs/TASK_LIST.md V5): one row per level 1..13,
# level 1 == the original board. Layers cap at 4 (the renderer has 4 layer
# styles); min_misplaced always stays <= layers. With both at their ceiling by
# level 10, the BONUS-ONLY tiers 11..13 climb on stamp size and board size
# instead — they are never served as a main board.
MAIN_LEVEL_PARAMS: tuple[dict, ...] = (
    {"size": 6, "layers": 3, "cells": (2, 5), "max_overlap": 2, "min_misplaced": 2, "difficulty": 2, "time_hint": 30},  # 1
    {"size": 6, "layers": 3, "cells": (2, 5), "max_overlap": 2, "min_misplaced": 2, "difficulty": 2, "time_hint": 30},  # 2
    {"size": 6, "layers": 3, "cells": (2, 5), "max_overlap": 2, "min_misplaced": 2, "difficulty": 2, "time_hint": 30},  # 3
    {"size": 6, "layers": 3, "cells": (3, 5), "max_overlap": 2, "min_misplaced": 3, "difficulty": 3, "time_hint": 38},  # 4
    {"size": 6, "layers": 3, "cells": (3, 5), "max_overlap": 2, "min_misplaced": 3, "difficulty": 3, "time_hint": 38},  # 5
    {"size": 6, "layers": 3, "cells": (3, 5), "max_overlap": 2, "min_misplaced": 3, "difficulty": 3, "time_hint": 38},  # 6
    {"size": 7, "layers": 4, "cells": (3, 5), "max_overlap": 3, "min_misplaced": 3, "difficulty": 4, "time_hint": 48},  # 7
    {"size": 7, "layers": 4, "cells": (3, 5), "max_overlap": 3, "min_misplaced": 3, "difficulty": 4, "time_hint": 48},  # 8
    {"size": 7, "layers": 4, "cells": (3, 5), "max_overlap": 3, "min_misplaced": 3, "difficulty": 4, "time_hint": 48},  # 9
    {"size": 7, "layers": 4, "cells": (3, 6), "max_overlap": 3, "min_misplaced": 4, "difficulty": 4, "time_hint": 55},  # 10
    {"size": 7, "layers": 4, "cells": (4, 6), "max_overlap": 3, "min_misplaced": 4, "difficulty": 5, "time_hint": 62},  # 11 bonus
    {"size": 7, "layers": 4, "cells": (4, 7), "max_overlap": 3, "min_misplaced": 4, "difficulty": 5, "time_hint": 68},  # 12 bonus
    {"size": 8, "layers": 4, "cells": (4, 7), "max_overlap": 3, "min_misplaced": 4, "difficulty": 5, "time_hint": 75},  # 13 bonus
)


def _params_for_level(level: int) -> dict:
    """Main-board knobs for `level`, clamped to the 1..10 table."""
    return MAIN_LEVEL_PARAMS[min(max(level, 1), len(MAIN_LEVEL_PARAMS)) - 1]

STEPS = ((1, 0), (-1, 0), (0, 1), (0, -1))

Cell = tuple[int, int]


def _normalize(cells: set[Cell]) -> frozenset[Cell]:
    """Shift a cell set so its bounding box starts at (0, 0)."""
    min_r = min(r for r, _ in cells)
    min_c = min(c for _, c in cells)
    return frozenset((r - min_r, c - min_c) for r, c in cells)


def _transform(pattern: list[list[int]], rot: int, fx: bool, fy: bool) -> frozenset[Cell]:
    """Apply flips then rotation to a local pattern; returns a normalised shape."""
    cells: set[Cell] = {(r, c) for r, c in pattern}
    if fx:
        cells = {(r, -c) for r, c in cells}
    if fy:
        cells = {(-r, c) for r, c in cells}
    for _ in range(rot % 4):
        cells = {(c, -r) for r, c in cells}  # one clockwise quarter-turn
    return _normalize(cells)


def _place(
    pattern: list[list[int]], rot: int, fx: bool, fy: bool, r: int, c: int
) -> frozenset[Cell]:
    """Workspace cells of a layer: transform, then translate by (r, c)."""
    return frozenset((pr + r, pc + c) for pr, pc in _transform(pattern, rot, fx, fy))


def _dims(shape: frozenset[Cell]) -> tuple[int, int]:
    return max(r for r, _ in shape) + 1, max(c for _, c in shape) + 1


def _layer_options(layer: dict) -> tuple[tuple, tuple, tuple]:
    rots = (0, 1, 2, 3) if layer["allow_rot"] else (0,)
    fxs = (False, True) if layer["allow_flip_x"] else (False,)
    fys = (False, True) if layer["allow_flip_y"] else (False,)
    return rots, fxs, fys


def _random_pattern(rng: random.Random, cell_count: int) -> list[list[int]]:
    """Connected random polyomino grown cell by cell, normalised and sorted."""
    cells: set[Cell] = {(0, 0)}
    while len(cells) < cell_count:
        r, c = rng.choice(sorted(cells))
        dr, dc = rng.choice(STEPS)
        cells.add((r + dr, c + dc))
    return [list(cell) for cell in sorted(_normalize(cells))]


def _random_placement(rng: random.Random, layer: dict, size: int) -> dict:
    rots, fxs, fys = _layer_options(layer)
    rot, fx, fy = rng.choice(rots), rng.choice(fxs), rng.choice(fys)
    h, w = _dims(_transform(layer["pattern"], rot, fx, fy))
    return {
        "id": layer["id"],
        "r": rng.randrange(size - h + 1),
        "c": rng.randrange(size - w + 1),
        "rot": rot,
        "fx": fx,
        "fy": fy,
    }


def _placed_cells(layer: dict, placement: dict) -> frozenset[Cell]:
    return _place(
        layer["pattern"],
        placement["rot"],
        placement["fx"],
        placement["fy"],
        placement["r"],
        placement["c"],
    )


def _count_solutions(layers: list[dict], target: frozenset[Cell], size: int) -> int:
    """Distinct cell-set placements composing exactly `target`, capped just
    past MAX_SOLUTIONS. OR-blend means every layer must sit inside the target,
    which prunes the bounded transform space hard."""
    candidate_sets: list[list[frozenset[Cell]]] = []
    for layer in layers:
        rots, fxs, fys = _layer_options(layer)
        seen: set[frozenset[Cell]] = set()
        options: list[frozenset[Cell]] = []
        for rot in rots:
            for fx in fxs:
                for fy in fys:
                    shape = _transform(layer["pattern"], rot, fx, fy)
                    h, w = _dims(shape)
                    for r in range(size - h + 1):
                        for c in range(size - w + 1):
                            placed = frozenset((pr + r, pc + c) for pr, pc in shape)
                            if placed <= target and placed not in seen:
                                seen.add(placed)
                                options.append(placed)
        if not options:
            return 0
        candidate_sets.append(options)

    count = 0

    def walk(index: int, covered: frozenset[Cell]) -> None:
        nonlocal count
        if count > MAX_SOLUTIONS:
            return
        if index == len(candidate_sets):
            count += covered == target
            return
        for placed in candidate_sets[index]:
            walk(index + 1, covered | placed)

    walk(0, frozenset())
    return count


class OverprintGame:
    """Layered stamps, one workspace: OR-compose the layers onto the target."""

    id = "overprint"
    name = "Overprint"

    def generate_main(self, seed: int, level: int = 1) -> PuzzleInstance:
        return self._generate(seed, kind="main", level=level)

    def generate_holding(self, seed: int) -> PuzzleInstance:
        return self._generate(seed, kind="holding")

    def _build(self, seed: int, kind: str, level: int = 1) -> tuple[dict, str]:
        """Payload + a reference solution (server-only, used by tests)."""
        rng = random.Random(seed)
        if kind == "main":
            params = _params_for_level(level)
            size, layer_count, cell_range = params["size"], params["layers"], params["cells"]
            max_overlap, min_misplaced = params["max_overlap"], params["min_misplaced"]
            difficulty, time_hint = params["difficulty"], params["time_hint"]
        else:
            size, layer_count, cell_range = HOLD_SIZE, HOLD_LAYERS, HOLD_CELLS
            max_overlap, min_misplaced = HOLD_MAX_OVERLAP, HOLD_MIN_MISPLACED
            difficulty, time_hint = 1, 8
        for _ in range(GEN_ATTEMPTS):
            # Main: every layer rotates, one may flip. Holding: translation
            # only, plus at most one rotatable layer (spec §4).
            special = rng.randrange(layer_count)
            layers = []
            for index in range(layer_count):
                layers.append(
                    {
                        "id": f"l{index}",
                        "pattern": _random_pattern(rng, rng.randint(*cell_range)),
                        "allow_rot": kind == "main" or index == special,
                        "allow_flip_x": kind == "main" and index == special,
                        "allow_flip_y": False,
                    }
                )
            if len({tuple(map(tuple, layer["pattern"])) for layer in layers}) < layer_count:
                continue  # visually identical layers read badly and add ambiguity

            solution = [_random_placement(rng, layer, size) for layer in layers]
            solution_cells = [
                _placed_cells(layer, placement)
                for layer, placement in zip(layers, solution)
            ]
            target = frozenset().union(*solution_cells)
            if sum(map(len, solution_cells)) - len(target) > max_overlap:
                continue  # heavily overlapped targets are unreadable
            if not 1 <= _count_solutions(layers, target, size) <= MAX_SOLUTIONS:
                continue

            for _ in range(SCRAMBLE_ATTEMPTS):
                initial = [_random_placement(rng, layer, size) for layer in layers]
                initial_cells = [
                    _placed_cells(layer, placement)
                    for layer, placement in zip(layers, initial)
                ]
                misplaced = sum(
                    before != after
                    for before, after in zip(initial_cells, solution_cells)
                )
                if frozenset().union(*initial_cells) == target:
                    continue  # served boards must start unsolved
                if misplaced < min_misplaced:
                    continue  # not a trivial one-nudge start
                break
            else:
                continue

            payload = {
                "variant": kind,
                "difficulty": difficulty,
                "time_hint_seconds": time_hint,
                "rules_version": RULES_VERSION,
                "rows": size,
                "cols": size,
                "blend": "or",
                "target": [
                    "".join("1" if (r, c) in target else "0" for c in range(size))
                    for r in range(size)
                ],
                "layers": layers,
                "initial": initial,
            }
            answer = json.dumps({"v": RULES_VERSION, "layers": solution})
            return payload, answer
        raise RuntimeError(f"overprint generation failed for seed {seed}")

    def _generate(self, seed: int, kind: str, level: int = 1) -> PuzzleInstance:
        payload, answer = self._build(seed, kind, level)
        return PuzzleInstance(
            game_id=self.id,
            kind=kind,
            prompt="Move, turn, and flip the layers until their overprint matches the target exactly.",
            answer=answer,  # server-only reference; check() recomputes instead
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
            placements = data.get("layers")
            payload = puzzle.payload
            layers = {layer["id"]: layer for layer in payload["layers"]}
            if not isinstance(placements, list) or len(placements) != len(layers):
                return False
            rows, cols = payload["rows"], payload["cols"]
            seen_ids: set[str] = set()
            composite: set[Cell] = set()
            for placement in placements:
                if not isinstance(placement, dict):
                    return False
                layer = layers.get(placement.get("id"))
                if layer is None or placement["id"] in seen_ids:
                    return False
                seen_ids.add(placement["id"])
                r, c = placement.get("r"), placement.get("c")
                rot = placement.get("rot", 0)
                fx = placement.get("fx", False)
                fy = placement.get("fy", False)
                for value in (r, c, rot):
                    if not isinstance(value, int) or isinstance(value, bool):
                        return False
                if not (isinstance(fx, bool) and isinstance(fy, bool)):
                    return False
                if not 0 <= rot <= 3:
                    return False
                if rot and not layer["allow_rot"]:
                    return False
                if fx and not layer["allow_flip_x"]:
                    return False
                if fy and not layer["allow_flip_y"]:
                    return False
                placed = _place(layer["pattern"], rot, fx, fy, r, c)
                if any(not (0 <= pr < rows and 0 <= pc < cols) for pr, pc in placed):
                    return False  # clipped rendering is still an illegal placement
                composite |= placed
            target = {
                (r, c)
                for r, row in enumerate(payload["target"])
                for c, mark in enumerate(row)
                if mark == "1"
            }
            return composite == target
        except Exception:
            return False  # malformed input is just wrong, never a crash

    def reset(self) -> None:
        return None  # stateless
