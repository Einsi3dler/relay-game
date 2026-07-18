"""MIRROR RUN (Stage 2, divided attention): steer two runners at once.

Per game/RELAY_EXPANSION_GAMES_README.md §1. One command stream drives both
runners: Runner A takes each U/R/D/L directly, Runner B first transforms it
through the puzzle's fixed mapping (mirror/rotate/invert). A runner whose move
would hit a wall or leave the board stays still — that is legal and often
necessary. The puzzle is solved only when both runners sit on their own exits
after the same completed turn.

Generation searches the product state (posA, posB) with BFS (≤ rows²·cols²
states, tiny) and re-rolls until the shortest solution falls in the target
depth band and both boards matter (each runner moves on a healthy fraction of
a shortest path). `check` replays the submitted move string; a reference
solution is kept server-only in `answer` for tests but is never required by
the checker.
"""

from __future__ import annotations

import json
import random
from collections import deque

from backend.games.base import PuzzleInstance

RULES_VERSION = 1

MAIN_SIZE = 6
MAIN_DEPTH = (10, 18)          # accepted shortest-solution band
MAIN_RELAXED_DEPTH = (8, 22)   # fallback band if generation runs long
MAIN_WALL_P = 0.18
MAIN_MOVE_CAP = 30
MAIN_MAPPINGS = ("mirror_x", "mirror_y", "rotate_cw", "rotate_ccw", "invert")

HOLD_SIZE = 4
HOLD_DEPTH = (3, 6)
HOLD_RELAXED_DEPTH = (2, 8)
HOLD_WALL_P = 0.15
HOLD_MOVE_CAP = 10
HOLD_MAPPINGS = ("mirror_x", "mirror_y", "invert")

GEN_ATTEMPTS = 300             # per band; both bands are tried in order
MIN_MOVE_FRACTION = 0.4        # each runner must move in >= this share of steps
MAX_ANSWER_CHARS = 400

DELTAS = {"U": (-1, 0), "R": (0, 1), "D": (1, 0), "L": (0, -1)}

MAPPINGS: dict[str, dict[str, str]] = {
    "mirror_x": {"U": "U", "D": "D", "L": "R", "R": "L"},
    "mirror_y": {"U": "D", "D": "U", "L": "L", "R": "R"},
    "invert": {"U": "D", "D": "U", "L": "R", "R": "L"},
    "rotate_cw": {"U": "R", "R": "D", "D": "L", "L": "U"},
    "rotate_ccw": {"U": "L", "L": "D", "D": "R", "R": "U"},
}

Cell = tuple[int, int]


def _step(pos: Cell, command: str, walls: frozenset[Cell], size: int) -> Cell:
    """One runner's move: blocked by walls or the edge means staying still."""
    dr, dc = DELTAS[command]
    target = (pos[0] + dr, pos[1] + dc)
    if not (0 <= target[0] < size and 0 <= target[1] < size):
        return pos
    if target in walls:
        return pos
    return target


def _solve(
    boards: list[dict], mapping: str, size: int, cap: int
) -> str | None:
    """Shortest shared-command solution via BFS on (posA, posB), or None."""
    walls_a = frozenset(map(tuple, boards[0]["walls"]))
    walls_b = frozenset(map(tuple, boards[1]["walls"]))
    start = (tuple(boards[0]["start"]), tuple(boards[1]["start"]))
    goal = (tuple(boards[0]["exit"]), tuple(boards[1]["exit"]))
    if start == goal:
        return ""  # served-solved boards are rejected by the depth band
    transform = MAPPINGS[mapping]
    seen = {start}
    queue: deque[tuple[tuple[Cell, Cell], str]] = deque([(start, "")])
    while queue:
        (pos_a, pos_b), path = queue.popleft()
        if len(path) >= cap:
            continue
        for command in "URDL":
            nxt = (
                _step(pos_a, command, walls_a, size),
                _step(pos_b, transform[command], walls_b, size),
            )
            if nxt in seen:
                continue
            if nxt == goal:
                return path + command
            seen.add(nxt)
            queue.append((nxt, path + command))
    return None


def _both_boards_matter(
    boards: list[dict], mapping: str, size: int, solution: str
) -> bool:
    """Each runner must actually move on >= MIN_MOVE_FRACTION of the steps."""
    walls_a = frozenset(map(tuple, boards[0]["walls"]))
    walls_b = frozenset(map(tuple, boards[1]["walls"]))
    pos_a: Cell = tuple(boards[0]["start"])
    pos_b: Cell = tuple(boards[1]["start"])
    transform = MAPPINGS[mapping]
    moved_a = moved_b = 0
    for command in solution:
        next_a = _step(pos_a, command, walls_a, size)
        next_b = _step(pos_b, transform[command], walls_b, size)
        moved_a += next_a != pos_a
        moved_b += next_b != pos_b
        pos_a, pos_b = next_a, next_b
    floor = MIN_MOVE_FRACTION * len(solution)
    return moved_a >= floor and moved_b >= floor


def _random_board(rng: random.Random, size: int, wall_p: float) -> dict:
    cells = [(r, c) for r in range(size) for c in range(size)]
    start, exit_ = rng.sample(cells, 2)
    walls = [
        [r, c]
        for r, c in cells
        if (r, c) not in (start, exit_) and rng.random() < wall_p
    ]
    return {"walls": walls, "start": list(start), "exit": list(exit_)}


class MirrorRunGame:
    """Two mazes, one control stream; Runner B obeys a transformed command."""

    id = "mirror_run"
    name = "Mirror Run"

    def generate_main(self, seed: int) -> PuzzleInstance:
        return self._generate(seed, kind="main")

    def generate_holding(self, seed: int) -> PuzzleInstance:
        return self._generate(seed, kind="holding")

    def _build(self, seed: int, kind: str) -> tuple[dict, str]:
        """Payload + a reference solution (server-only, used by tests)."""
        rng = random.Random(seed)
        if kind == "main":
            size, wall_p, cap = MAIN_SIZE, MAIN_WALL_P, MAIN_MOVE_CAP
            mappings, bands = MAIN_MAPPINGS, (MAIN_DEPTH, MAIN_RELAXED_DEPTH)
        else:
            size, wall_p, cap = HOLD_SIZE, HOLD_WALL_P, HOLD_MOVE_CAP
            mappings, bands = HOLD_MAPPINGS, (HOLD_DEPTH, HOLD_RELAXED_DEPTH)
        for lo, hi in bands:
            for _ in range(GEN_ATTEMPTS):
                mapping = rng.choice(mappings)
                boards = [
                    _random_board(rng, size, wall_p),
                    _random_board(rng, size, wall_p),
                ]
                solution = _solve(boards, mapping, size, cap)
                if solution is None or not lo <= len(solution) <= hi:
                    continue
                if not _both_boards_matter(boards, mapping, size, solution):
                    continue
                payload = {
                    "variant": kind,
                    "difficulty": 2 if kind == "main" else 1,
                    "time_hint_seconds": 30 if kind == "main" else 8,
                    "rules_version": RULES_VERSION,
                    "rows": size,
                    "cols": size,
                    "boards": boards,
                    "mapping_b": mapping,
                    "move_cap": cap,
                }
                return payload, solution
        raise RuntimeError(f"mirror_run generation failed for seed {seed}")

    def _generate(self, seed: int, kind: str) -> PuzzleInstance:
        payload, solution = self._build(seed, kind)
        return PuzzleInstance(
            game_id=self.id,
            kind=kind,
            prompt="Guide BOTH runners onto their exits — B follows the twisted controls.",
            answer=solution,  # server-only reference; check() replays instead
            payload=payload,
        )

    def check(self, puzzle: PuzzleInstance, answer: str) -> bool:
        try:
            text = str(answer).strip()
            if not text or len(text) > MAX_ANSWER_CHARS:
                return False
            data = json.loads(text)
            if not isinstance(data, dict) or data.get("v") != RULES_VERSION:
                return False
            moves = data.get("moves")
            if not isinstance(moves, str) or not moves:
                return False
            payload = puzzle.payload
            if len(moves) > payload["move_cap"]:
                return False
            mapping = MAPPINGS.get(payload["mapping_b"])
            if mapping is None:
                return False
            size = payload["rows"]
            boards = payload["boards"]
            walls_a = frozenset(map(tuple, boards[0]["walls"]))
            walls_b = frozenset(map(tuple, boards[1]["walls"]))
            pos_a: Cell = tuple(boards[0]["start"])
            pos_b: Cell = tuple(boards[1]["start"])
            for command in moves:
                if command not in DELTAS:
                    return False
                pos_a = _step(pos_a, command, walls_a, size)
                pos_b = _step(pos_b, mapping[command], walls_b, size)
            return pos_a == tuple(boards[0]["exit"]) and pos_b == tuple(
                boards[1]["exit"]
            )
        except Exception:
            return False  # malformed input is just wrong, never a crash

    def reset(self) -> None:
        return None  # stateless
