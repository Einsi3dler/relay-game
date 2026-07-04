"""SWEEP (Stage 2, logical): flag every mine using the number clues.

Per docs/GAMES_SPEC.md Game 2. Boards are generated no-guess: a solver using
satisfied-number/exhausted-number rules plus subset elimination must fully
deduce the board from the opening reveal, else the layout is re-rolled.
`answer` holds the sorted mine coordinates (server-only). The payload carries
the clue number for every safe cell (documented exception — mines are its
complement; see the spec's threat model).
"""

from __future__ import annotations

import random

from backend.games.base import PuzzleInstance

MAIN_ROWS, MAIN_COLS, MAIN_MINES = 6, 6, 6
HOLD_ROWS, HOLD_COLS, HOLD_MINES = 3, 3, 1
MAX_ROLLS = 50

Cell = tuple[int, int]


def _neighbours(rows: int, cols: int, cell: Cell) -> list[Cell]:
    r, c = cell
    return [
        (r + dr, c + dc)
        for dr in (-1, 0, 1)
        for dc in (-1, 0, 1)
        if (dr, dc) != (0, 0) and 0 <= r + dr < rows and 0 <= c + dc < cols
    ]


def _counts(rows: int, cols: int, mines: set[Cell]) -> dict[Cell, int]:
    return {
        (r, c): sum(1 for n in _neighbours(rows, cols, (r, c)) if n in mines)
        for r in range(rows)
        for c in range(cols)
        if (r, c) not in mines
    }


def _zero_flood(rows: int, cols: int, counts: dict[Cell, int], start: Cell) -> set[Cell]:
    """The opening reveal: the connected zero-region plus its numbered border."""
    revealed = {start}
    frontier = [start]
    while frontier:
        cell = frontier.pop()
        if counts[cell] != 0:
            continue
        for neighbour in _neighbours(rows, cols, cell):
            if neighbour in counts and neighbour not in revealed:
                revealed.add(neighbour)
                frontier.append(neighbour)
    return revealed


def _deducible(
    rows: int, cols: int, mines: set[Cell], counts: dict[Cell, int], opening: set[Cell]
) -> bool:
    """True if basic rules + subset elimination solve the board with no guess."""
    revealed = set(opening)
    flagged: set[Cell] = set()
    while True:
        progressed = False
        constraints: list[tuple[frozenset[Cell], int]] = []
        for cell in list(revealed):
            unknown = frozenset(
                n for n in _neighbours(rows, cols, cell)
                if n not in revealed and n not in flagged
            )
            need = counts[cell] - sum(
                1 for n in _neighbours(rows, cols, cell) if n in flagged
            )
            if not unknown:
                continue
            if need == len(unknown):  # every unknown neighbour is a mine
                flagged |= unknown
                progressed = True
            elif need == 0:  # every unknown neighbour is safe
                revealed |= unknown
                progressed = True
            else:
                constraints.append((unknown, need))
        if not progressed:
            for cells_a, need_a in constraints:
                for cells_b, need_b in constraints:
                    if cells_a < cells_b:
                        rest = cells_b - cells_a
                        if need_b - need_a == len(rest):
                            flagged |= rest
                            progressed = True
                        elif need_b == need_a:
                            revealed |= rest
                            progressed = True
        if flagged == mines and len(revealed) == rows * cols - len(mines):
            return True
        if not progressed:
            return False


def _roll_board(
    rng: random.Random, rows: int, cols: int, mine_count: int
) -> tuple[set[Cell], dict[Cell, int], set[Cell]] | None:
    cells = [(r, c) for r in range(rows) for c in range(cols)]
    mines = set(rng.sample(cells, mine_count))
    counts = _counts(rows, cols, mines)
    zero_cells = [cell for cell, n in counts.items() if n == 0]
    if not zero_cells:
        return None
    opening = _zero_flood(rows, cols, counts, rng.choice(zero_cells))
    if _deducible(rows, cols, mines, counts, opening):
        return mines, counts, opening
    return None


class SweepGame:
    """Flag every mine; boards are always solvable by pure deduction."""

    id = "sweep"
    name = "Sweep"

    def generate_main(self, seed: int) -> PuzzleInstance:
        return self._generate(seed, kind="main")

    def generate_holding(self, seed: int) -> PuzzleInstance:
        return self._generate(seed, kind="holding")

    def _generate(self, seed: int, kind: str) -> PuzzleInstance:
        rng = random.Random(seed)
        if kind == "main":
            rows, cols, mine_count = MAIN_ROWS, MAIN_COLS, MAIN_MINES
            difficulty, time_hint = 2, 40
        else:
            rows, cols, mine_count = HOLD_ROWS, HOLD_COLS, HOLD_MINES
            difficulty, time_hint = 1, 8
        board = None
        mines_now = mine_count
        for _ in range(MAX_ROLLS):
            board = _roll_board(rng, rows, cols, mines_now)
            if board is not None:
                break
        else:
            # Spec fallback: ease the board rather than serve a guessing game.
            while board is None and mines_now > 1:
                mines_now -= 1
                for _ in range(MAX_ROLLS):
                    board = _roll_board(rng, rows, cols, mines_now)
                    if board is not None:
                        break
        assert board is not None, "sweep generation failed"
        mines, counts, opening = board
        answer = ";".join(f"{r},{c}" for r, c in sorted(mines))
        return PuzzleInstance(
            game_id=self.id,
            kind=kind,
            prompt="Flag every mine. The numbers count mines in adjacent cells.",
            answer=answer,
            payload={
                "variant": kind,
                "difficulty": difficulty,
                "time_hint_seconds": time_hint,
                "rows": rows,
                "cols": cols,
                "mine_count": len(mines),
                "revealed": [
                    {"r": r, "c": c, "n": counts[(r, c)]} for r, c in sorted(opening)
                ],
                "clues": [
                    {"r": r, "c": c, "n": n} for (r, c), n in sorted(counts.items())
                ],
            },
        )

    def check(self, puzzle: PuzzleInstance, answer: str) -> bool:
        try:
            rows, cols = puzzle.payload["rows"], puzzle.payload["cols"]
            flagged: set[Cell] = set()
            for part in str(answer).strip().split(";"):
                r_text, c_text = part.split(",")
                r, c = int(r_text), int(c_text)
                if not (0 <= r < rows and 0 <= c < cols):
                    return False
                flagged.add((r, c))
            mines = {
                (int(r), int(c))
                for r, c in (pair.split(",") for pair in puzzle.answer.split(";"))
            }
            return flagged == mines
        except Exception:
            return False  # includes the "BOOM" sentinel and malformed input

    def reset(self) -> None:
        return None  # stateless
