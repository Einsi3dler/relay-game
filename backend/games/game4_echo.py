"""ECHO (Stage 4, reflex/memory): watch the flash sequence, tap it back.

Per docs/GAMES_SPEC.md Game 4. The sequence must be in the payload so the
client can animate it — the documented exception to the no-solution rule
(defeats only a player sniffing their own traffic; see the spec's threat
model). `check` demands an exact order match.
"""

from __future__ import annotations

import random

from backend.games.base import PuzzleInstance, normalize_answer

MAIN_PADS, MAIN_LENGTH = 9, 5
HOLD_PADS, HOLD_LENGTH = 4, 3
FLASH_MS, GAP_MS = 450, 250


class EchoGame:
    """Repeat the flashed pad sequence by tapping in the same order."""

    id = "echo"
    name = "Echo"

    def generate_main(self, seed: int) -> PuzzleInstance:
        return self._generate(seed, kind="main")

    def generate_holding(self, seed: int) -> PuzzleInstance:
        return self._generate(seed, kind="holding")

    def _generate(self, seed: int, kind: str) -> PuzzleInstance:
        rng = random.Random(seed)
        if kind == "main":
            pads, length, difficulty, time_hint = MAIN_PADS, MAIN_LENGTH, 2, 20
        else:
            pads, length, difficulty, time_hint = HOLD_PADS, HOLD_LENGTH, 1, 8
        sequence = [rng.randrange(pads) for _ in range(length)]
        return PuzzleInstance(
            game_id=self.id,
            kind=kind,
            prompt="Watch the flashes, then tap the pads in the same order.",
            answer=",".join(str(pad) for pad in sequence),
            payload={
                "variant": kind,
                "difficulty": difficulty,
                "time_hint_seconds": time_hint,
                "pads": pads,
                "sequence": sequence,  # documented exception — must be animated
                "flash_ms": FLASH_MS,
                "gap_ms": GAP_MS,
            },
        )

    def check(self, puzzle: PuzzleInstance, answer: str) -> bool:
        try:
            taps = normalize_answer(answer).replace(" ", "")
            return bool(taps) and taps == puzzle.answer
        except Exception:
            return False

    def reset(self) -> None:
        return None  # stateless
