"""DECANT (Stage 3, sorting): pour colours between tubes until each is uniform.

Per docs/GAMES_SPEC.md Game 3. Boards start from the solved state and are
scrambled by reverse-pours (the inverse of a legal pour), each constrained so
its undo is itself a legal pour — the reversed scramble is therefore a valid
solution, making every board provably solvable. `check` replays the submitted
move list under the pour rules; `answer` is unused.
"""

from __future__ import annotations

import random

from backend.games.base import PuzzleInstance

MAIN_COLOURS, MAIN_TUBES, MAIN_SCRAMBLE = 3, 5, 14
HOLD_COLOURS, HOLD_TUBES, HOLD_SCRAMBLE = 2, 3, 2
CAPACITY = 4
MAX_MOVES = 60

Tubes = list[list[int]]


def _top_run(tube: list[int]) -> int:
    """Length of the contiguous top-colour run."""
    if not tube:
        return 0
    run = 1
    while run < len(tube) and tube[-run - 1] == tube[-1]:
        run += 1
    return run


def _pour(tubes: Tubes, src: int, dst: int, capacity: int) -> bool:
    """Apply one legal pour in place; False if the pour is illegal."""
    if src == dst or not (0 <= src < len(tubes) and 0 <= dst < len(tubes)):
        return False
    source, dest = tubes[src], tubes[dst]
    if not source or len(dest) >= capacity:
        return False
    colour = source[-1]
    if dest and dest[-1] != colour:
        return False
    amount = min(_top_run(source), capacity - len(dest))
    del source[len(source) - amount:]
    dest.extend([colour] * amount)
    return True


def _solved(tubes: Tubes, capacity: int) -> bool:
    return all(
        not tube or (len(tube) == capacity and len(set(tube)) == 1) for tube in tubes
    )


def _scramble(
    rng: random.Random, colours: int, tube_count: int, moves: int
) -> tuple[Tubes, list[tuple[int, int]]]:
    """Reverse-pour scramble from solved. Returns (tubes, solution pours).

    A reverse-pour takes j segments of the top colour from tube X onto any
    tube Y with room whose top differs (or is empty). Constraining j to leave
    the same colour on X's top — or empty X entirely — makes the undo (pour
    Y→X) legal, so the reversed move list is a valid solution.
    """
    tubes: Tubes = [[colour + 1] * CAPACITY for colour in range(colours)]
    tubes += [[] for _ in range(tube_count - colours)]
    solution: list[tuple[int, int]] = []
    tries = 0
    while (len(solution) < moves or _solved(tubes, CAPACITY)) and tries < moves * 30:
        tries += 1
        x = rng.randrange(tube_count)
        if not tubes[x]:
            continue
        colour = tubes[x][-1]
        run = _top_run(tubes[x])
        # j < run keeps colour on X's top; j == len(tube) empties X.
        max_j = run if run == len(tubes[x]) else run - 1
        if max_j < 1:
            continue
        targets = [
            y for y in range(tube_count)
            if y != x and len(tubes[y]) < CAPACITY
            and (not tubes[y] or tubes[y][-1] != colour)
        ]
        if not targets:
            continue
        y = rng.choice(targets)
        j = rng.randint(1, min(max_j, CAPACITY - len(tubes[y])))
        del tubes[x][len(tubes[x]) - j:]
        tubes[y].extend([colour] * j)
        solution.append((y, x))  # the undo: pour Y back onto X
    solution.reverse()
    return tubes, solution


class DecantGame:
    """Pour between tubes until every tube is empty or one full colour."""

    id = "decant"
    name = "Decant"

    def generate_main(self, seed: int) -> PuzzleInstance:
        return self._generate(seed, kind="main")

    def generate_holding(self, seed: int) -> PuzzleInstance:
        return self._generate(seed, kind="holding")

    def _build(self, seed: int, kind: str) -> tuple[Tubes, list[tuple[int, int]]]:
        """Board + a known-good solution (used by generation self-check and tests)."""
        rng = random.Random(seed)
        if kind == "main":
            tubes, solution = _scramble(rng, MAIN_COLOURS, MAIN_TUBES, MAIN_SCRAMBLE)
        else:
            tubes, solution = _scramble(rng, HOLD_COLOURS, HOLD_TUBES, HOLD_SCRAMBLE)
        # Self-check: replaying the recorded solution must solve the board.
        replay = [list(tube) for tube in tubes]
        assert all(_pour(replay, src, dst, CAPACITY) for src, dst in solution)
        assert _solved(replay, CAPACITY)
        return tubes, solution

    def _generate(self, seed: int, kind: str) -> PuzzleInstance:
        tubes, _ = self._build(seed, kind)
        difficulty, time_hint = (3, 40) if kind == "main" else (1, 8)
        return PuzzleInstance(
            game_id=self.id,
            kind=kind,
            prompt="Pour between tubes until every tube holds a single colour.",
            answer="",  # unused — correctness is recomputed by replaying moves
            payload={
                "variant": kind,
                "difficulty": difficulty,
                "time_hint_seconds": time_hint,
                "capacity": CAPACITY,
                "tubes": [list(tube) for tube in tubes],
            },
        )

    def check(self, puzzle: PuzzleInstance, answer: str) -> bool:
        try:
            capacity = puzzle.payload["capacity"]
            tubes = [list(tube) for tube in puzzle.payload["tubes"]]
            text = str(answer).strip()
            if not text:
                return False
            moves = text.split(";")
            if len(moves) > MAX_MOVES:
                return False
            for move in moves:
                src_text, dst_text = move.split(">")
                if not _pour(tubes, int(src_text), int(dst_text), capacity):
                    return False  # illegal pour or bad index
            return _solved(tubes, capacity)
        except Exception:
            return False  # malformed input is just wrong, never a crash

    def reset(self) -> None:
        return None  # stateless
