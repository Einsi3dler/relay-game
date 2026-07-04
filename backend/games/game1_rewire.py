"""REWIRE (Stage 1, puzzle): rotate tiles so power reaches every sink.

Per docs/GAMES_SPEC.md Game 1. The board is a random spanning tree of the
grid (every cell wired, so a correctly oriented board has no dangling edges),
scrambled by rotation. `check` recomputes connectivity from the submitted
orientations — `answer` is unused; any valid rotation vector passes.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from backend.games.base import PuzzleInstance

# Edge directions: 0=N, 1=E, 2=S, 3=W. DELTAS[d] moves a cell that way.
DELTAS = {0: (-1, 0), 1: (0, 1), 2: (1, 0), 3: (0, -1)}

# Open edges of each shape at orientation 0; rotation r opens (edge + r) % 4.
SHAPE_EDGES = {
    "end": (0,),
    "straight": (0, 2),
    "elbow": (0, 1),
    "tee": (0, 1, 2),
}

MAIN_ROWS, MAIN_COLS = 4, 4
HOLDING_ROWS, HOLDING_COLS = 2, 2


def open_edges(shape: str, orient: int) -> set[int]:
    return {(edge + orient) % 4 for edge in SHAPE_EDGES[shape]}


def _shape_for(edges: set[int]) -> tuple[str, int]:
    """The (shape, orientation) whose open edges are exactly `edges`."""
    for shape, base in SHAPE_EDGES.items():
        if len(base) != len(edges):
            continue
        for orient in range(4):
            if {(e + orient) % 4 for e in base} == edges:
                return shape, orient
    raise ValueError(f"no shape for edges {edges}")  # degree 4 is prevented


@dataclass
class Board:
    rows: int
    cols: int
    shapes: list[str]  # row-major
    solution: list[int]  # reference orientations (server-side only)
    scrambled: list[int]
    source: tuple[int, int]
    sinks: list[tuple[int, int]]


def _build_board(rng: random.Random, rows: int, cols: int, sink_count: int) -> Board:
    """Random spanning tree over the full grid (max degree 3), then scramble.

    The degree cap can (very rarely) strand cells; retry until the tree spans —
    deterministic because the retries consume the same seeded rng.
    """
    while True:
        board = _try_build_board(rng, rows, cols, sink_count)
        if board is not None:
            return board


def _try_build_board(
    rng: random.Random, rows: int, cols: int, sink_count: int
) -> Board | None:
    cells = [(r, c) for r in range(rows) for c in range(cols)]
    source = rng.choice(cells)
    edges: dict[tuple[int, int], set[int]] = {cell: set() for cell in cells}
    visited = {source}
    depth = {source: 0}
    stack = [source]
    while stack:
        r, c = cell = stack[-1]
        candidates = [
            (d, (r + dr, c + dc))
            for d, (dr, dc) in DELTAS.items()
            if (r + dr, c + dc) in edges and (r + dr, c + dc) not in visited
        ]
        if not candidates or len(edges[cell]) >= 3:
            stack.pop()
            continue
        d, neighbour = rng.choice(candidates)
        edges[cell].add(d)
        edges[neighbour].add((d + 2) % 4)
        visited.add(neighbour)
        depth[neighbour] = depth[cell] + 1
        stack.append(neighbour)

    if len(visited) != len(cells):
        return None  # degree cap stranded a pocket — caller retries

    leaves = [cell for cell in cells if len(edges[cell]) == 1 and cell != source]
    leaves.sort(key=lambda cell: -depth[cell])
    sinks = leaves[: max(1, min(sink_count, len(leaves)))]

    shapes, solution, scrambled = [], [], []
    for cell in cells:
        shape, orient = _shape_for(edges[cell])
        shapes.append(shape)
        solution.append(orient)
        scrambled.append(rng.randrange(4))
    if scrambled == solution:  # don't serve an already-solved board
        i = rng.randrange(len(scrambled))
        scrambled[i] = (scrambled[i] + 1 + rng.randrange(3)) % 4
    return Board(rows, cols, shapes, solution, scrambled, source, sinks)


def _connected_ok(
    rows: int,
    cols: int,
    shapes: list[str],
    orients: list[int],
    source: tuple[int, int],
    sinks: list[tuple[int, int]],
) -> bool:
    """Flood-fill from source; True iff all sinks powered and no powered tile
    has an open edge that doesn't mate with a neighbouring open edge."""
    def edges_at(r: int, c: int) -> set[int]:
        return open_edges(shapes[r * cols + c], orients[r * cols + c])

    powered = {source}
    frontier = [source]
    while frontier:
        r, c = frontier.pop()
        for d in edges_at(r, c):
            dr, dc = DELTAS[d]
            nr, nc = r + dr, c + dc
            if not (0 <= nr < rows and 0 <= nc < cols):
                return False  # live edge points off-grid
            if (d + 2) % 4 not in edges_at(nr, nc):
                return False  # live edge into a closed face
            if (nr, nc) not in powered:
                powered.add((nr, nc))
                frontier.append((nr, nc))
    return all(tuple(sink) in powered for sink in sinks)


class RewireGame:
    """Rotate the scrambled circuit until the source powers every sink."""

    id = "rewire"
    name = "Rewire"

    def generate_main(self, seed: int) -> PuzzleInstance:
        return self._generate(seed, kind="main")

    def generate_holding(self, seed: int) -> PuzzleInstance:
        return self._generate(seed, kind="holding")

    def _generate(self, seed: int, kind: str) -> PuzzleInstance:
        rng = random.Random(seed)
        if kind == "main":
            rows, cols, sink_count = MAIN_ROWS, MAIN_COLS, 2
            difficulty, time_hint = 2, 35
        else:
            rows, cols, sink_count = HOLDING_ROWS, HOLDING_COLS, 1
            difficulty, time_hint = 1, 8
        board = _build_board(rng, rows, cols, sink_count)
        # Self-check (spec generation step 4): the reference orientation must
        # pass our own validator; the full-tree construction guarantees it.
        assert _connected_ok(
            rows, cols, board.shapes, board.solution, board.source, board.sinks
        )
        return PuzzleInstance(
            game_id=self.id,
            kind=kind,
            prompt="Rotate the tiles so power reaches every sink.",
            answer="",  # unused — correctness is recomputed in check()
            payload={
                "variant": kind,
                "difficulty": difficulty,
                "time_hint_seconds": time_hint,
                "rows": rows,
                "cols": cols,
                "tiles": [
                    {"shape": shape, "orient": orient}
                    for shape, orient in zip(board.shapes, board.scrambled)
                ],
                "source": list(board.source),
                "sinks": [list(sink) for sink in board.sinks],
            },
        )

    def check(self, puzzle: PuzzleInstance, answer: str) -> bool:
        try:
            payload = puzzle.payload
            rows, cols = payload["rows"], payload["cols"]
            orients = [int(part) for part in str(answer).strip().split(",")]
            if len(orients) != rows * cols:
                return False
            if any(orient not in (0, 1, 2, 3) for orient in orients):
                return False
            shapes = [tile["shape"] for tile in payload["tiles"]]
            return _connected_ok(
                rows, cols, shapes, orients,
                tuple(payload["source"]),
                [tuple(sink) for sink in payload["sinks"]],
            )
        except Exception:
            return False  # malformed input is just wrong, never a crash

    def reset(self) -> None:
        return None  # stateless
