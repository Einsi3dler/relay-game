"""DECANT (Stage 3, sorting): pour colours between tubes until each is uniform.

Per docs/GAMES_SPEC.md Game 3, free-stacking single-block variant: a pour
moves exactly ONE block (the source's top) and may target ANY tube with room —
the destination's top colour does not need to match — so the puzzle can never
deadlock and the challenge is planning efficiency under the race clock.
Boards start from the solved state and are scrambled by reverse-pours whose
undo is itself a legal pour; the reversed scramble is a valid solution, making
every board provably solvable. A reverse-scramble can still collapse into a
near-solved board, so main generation re-rolls until the colour-run lower
bound guarantees at least MAIN_MIN_POURS pours. `check` replays the submitted
move list under the pour rules; `answer` is unused.
"""

from __future__ import annotations

import random

from backend.games.base import PuzzleInstance

MAIN_COLOURS, MAIN_TUBES, MAIN_SCRAMBLE = 4, 6, 20
MAIN_MIN_POURS = 7        # provable floor (run-count bound) on pours to solve
MAIN_GEN_ATTEMPTS = 30    # scrambles tried before accepting the deepest seen
HOLD_COLOURS, HOLD_TUBES, HOLD_SCRAMBLE = 2, 3, 2
CAPACITY = 4
MAX_MOVES = 60

Tubes = list[list[int]]


def _pour(tubes: Tubes, src: int, dst: int, capacity: int) -> bool:
    """Apply one legal pour in place; False if the pour is illegal.

    Free-stacking, single-block variant: a pour moves exactly ONE block (the
    source's top) onto ANY tube with room — the destination's top colour does
    not need to match. Illegal only if the source is empty, the destination is
    full, or src == dst.
    """
    if src == dst or not (0 <= src < len(tubes) and 0 <= dst < len(tubes)):
        return False
    source, dest = tubes[src], tubes[dst]
    if not source or len(dest) >= capacity:
        return False
    dest.append(source.pop())
    return True


def _solved(tubes: Tubes, capacity: int) -> bool:
    return all(
        not tube or (len(tube) == capacity and len(set(tube)) == 1) for tube in tubes
    )


def _colour_runs(tubes: Tubes) -> int:
    """Total contiguous same-colour runs across all tubes.

    A pour reduces the run count by at most 1, and a solved board has exactly
    one run per colour — so `_colour_runs(t) - colours` is a hard lower bound
    on the number of pours needed to solve `t`. Used as the generation gate
    (search-based depth checks are intractable under free-stacking rules).
    """
    return sum(
        1 + sum(1 for i in range(1, len(tube)) if tube[i] != tube[i - 1])
        for tube in tubes
        if tube
    )


def _scramble(
    rng: random.Random, colours: int, tube_count: int, moves: int
) -> tuple[Tubes, list[tuple[int, int]]]:
    """Reverse-pour scramble from solved. Returns (tubes, solution pours).

    Each scramble step moves one block from a random tube X onto a random
    tube Y with room. Because a pour moves exactly one block, the undo (pour
    Y→X) restores exactly that block, so the reversed move list is always a
    valid solution — every board is provably solvable.
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
        targets = [
            y for y in range(tube_count) if y != x and len(tubes[y]) < CAPACITY
        ]
        if not targets:
            continue
        y = rng.choice(targets)
        tubes[y].append(tubes[x].pop())
        solution.append((y, x))  # the undo: pour Y's top block back onto X
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
            # Difficulty gate: a reverse-scramble is always solvable but can
            # land near-solved, so re-roll until the run-count lower bound
            # guarantees at least MAIN_MIN_POURS pours. If every attempt is
            # shallow (rare), serve the deepest board seen — still solvable,
            # just easier.
            deepest: tuple[int, Tubes, list[tuple[int, int]]] | None = None
            for _ in range(MAIN_GEN_ATTEMPTS):
                tubes, solution = _scramble(
                    rng, MAIN_COLOURS, MAIN_TUBES, MAIN_SCRAMBLE
                )
                runs = _colour_runs(tubes)
                if runs - MAIN_COLOURS >= MAIN_MIN_POURS:
                    break
                if deepest is None or runs > deepest[0]:
                    deepest = (runs, tubes, solution)
            else:
                assert deepest is not None
                _, tubes, solution = deepest
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
