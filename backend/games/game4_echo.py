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

# Main-sequence difficulty curve (docs/TASK_LIST.md V5): one row per level
# 1..13, level 1 == the original puzzle. Length carries the difficulty; the
# flash speed-up is modest to keep the calm feel. Pads stay 9 — the renderer
# lays out a sqrt(pads) grid with a 9-colour palette. Levels 11..13 are
# BONUS-ONLY tiers, never served as a main board.
MAIN_LEVEL_PARAMS: tuple[dict, ...] = (
    {"length": 5, "flash_ms": 450, "gap_ms": 250, "difficulty": 2, "time_hint": 20},  # 1
    {"length": 5, "flash_ms": 450, "gap_ms": 250, "difficulty": 2, "time_hint": 20},  # 2
    {"length": 6, "flash_ms": 450, "gap_ms": 250, "difficulty": 2, "time_hint": 22},  # 3
    {"length": 6, "flash_ms": 450, "gap_ms": 250, "difficulty": 2, "time_hint": 22},  # 4
    {"length": 7, "flash_ms": 420, "gap_ms": 230, "difficulty": 3, "time_hint": 25},  # 5
    {"length": 7, "flash_ms": 420, "gap_ms": 230, "difficulty": 3, "time_hint": 25},  # 6
    {"length": 8, "flash_ms": 390, "gap_ms": 210, "difficulty": 3, "time_hint": 28},  # 7
    {"length": 8, "flash_ms": 390, "gap_ms": 210, "difficulty": 3, "time_hint": 28},  # 8
    {"length": 9, "flash_ms": 360, "gap_ms": 200, "difficulty": 4, "time_hint": 30},  # 9
    {"length": 9, "flash_ms": 360, "gap_ms": 200, "difficulty": 4, "time_hint": 30},  # 10
    {"length": 10, "flash_ms": 340, "gap_ms": 190, "difficulty": 4, "time_hint": 33},  # 11 bonus
    {"length": 11, "flash_ms": 320, "gap_ms": 180, "difficulty": 5, "time_hint": 36},  # 12 bonus
    {"length": 12, "flash_ms": 300, "gap_ms": 170, "difficulty": 5, "time_hint": 39},  # 13 bonus
)


def _params_for_level(level: int) -> dict:
    """Main-sequence knobs for `level`, clamped to the 1..10 table."""
    return MAIN_LEVEL_PARAMS[min(max(level, 1), len(MAIN_LEVEL_PARAMS)) - 1]


class EchoGame:
    """Repeat the flashed pad sequence by tapping in the same order."""

    id = "echo"
    name = "Echo"

    def generate_main(self, seed: int, level: int = 1) -> PuzzleInstance:
        return self._generate(seed, kind="main", level=level)

    def generate_holding(self, seed: int) -> PuzzleInstance:
        return self._generate(seed, kind="holding")

    def _generate(self, seed: int, kind: str, level: int = 1) -> PuzzleInstance:
        rng = random.Random(seed)
        if kind == "main":
            params = _params_for_level(level)
            pads, length = MAIN_PADS, params["length"]
            difficulty, time_hint = params["difficulty"], params["time_hint"]
            flash_ms, gap_ms = params["flash_ms"], params["gap_ms"]
        else:
            pads, length, difficulty, time_hint = HOLD_PADS, HOLD_LENGTH, 1, 8
            flash_ms, gap_ms = FLASH_MS, GAP_MS
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
                "flash_ms": flash_ms,
                "gap_ms": gap_ms,
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
