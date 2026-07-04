"""Copy this module to build a new game (docs/GAME_MODULE_SPEC.md §7).

Save as `backend/games/gameN_<name>.py`, rename the class, fill in the logic.
"""

from __future__ import annotations

import random

from backend.games.base import GameModule, PuzzleInstance, normalize_answer


class TemplateGame:
    """One-line description of the puzzle idea and what a correct answer looks like."""

    id = "template_game"      # unique snake_case; also goes in config.GAME_ORDER
    name = "Template Game"    # display name

    def generate_main(self, seed: int) -> PuzzleInstance:
        rng = random.Random(seed)          # seed everything from `seed` — no bare random
        a, b = rng.randint(2, 9), rng.randint(2, 9)
        return PuzzleInstance(
            game_id=self.id,
            kind="main",
            prompt=f"What is {a} × {b}?",
            answer=str(a * b),
            payload={"hint": "Just the number."},
        )

    def generate_holding(self, seed: int) -> PuzzleInstance:
        rng = random.Random(seed)
        n = rng.randint(10, 40)
        return PuzzleInstance(
            game_id=self.id,
            kind="holding",
            prompt=f"Quick check: is {n} even?",
            answer="yes" if n % 2 == 0 else "no",
            payload={"options": ["yes", "no"]},
        )

    def check(self, puzzle: PuzzleInstance, answer: str) -> bool:
        return normalize_answer(answer) == normalize_answer(puzzle.answer)

    def reset(self) -> None:
        # Stateless module → nothing to reset.
        return None
