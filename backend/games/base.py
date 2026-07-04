"""GameModule Protocol, PuzzleInstance dataclass, and answer normalisation.

Verbatim from docs/GAME_MODULE_SPEC.md §2 & §5. Provided by Core; do not edit
to suit one game.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4


@dataclass
class PuzzleInstance:
    """One puzzle handed to one player. Created by a GameModule."""

    game_id: str                       # e.g. "rewire"
    kind: str                          # "main" | "holding"
    prompt: str                        # human-readable question the client shows
    answer: str                        # SERVER ONLY — never sent to the client
    payload: dict[str, Any] = field(default_factory=dict)  # render hints (see §6)
    id: str = field(default_factory=lambda: uuid4().hex[:12])

    def public(self) -> dict[str, Any]:
        """JSON the client is allowed to see. MUST NOT include `answer`."""
        return {
            "id": self.id,
            "game_id": self.game_id,
            "kind": self.kind,
            "prompt": self.prompt,
            "payload": self.payload,
        }


class GameModule(Protocol):
    """Every game implements this. The engine only ever talks to this interface."""

    id: str            # unique, stable, snake_case. e.g. "rewire"
    name: str          # display name. e.g. "Rewire"

    def generate_main(self, seed: int) -> PuzzleInstance: ...
    def generate_holding(self, seed: int) -> PuzzleInstance: ...
    def check(self, puzzle: PuzzleInstance, answer: str) -> bool: ...
    def reset(self) -> None: ...


def normalize_answer(value: object) -> str:
    return " ".join(str(value).strip().lower().replace("/", " ").split())
