"""GameRegistry: maps a 1-based stage index to its GameModule.

Built from `config.GAME_ORDER`. The engine only ever asks the registry, never
a concrete game (docs/ARCHITECTURE.md §2).
"""

from __future__ import annotations

from typing import Iterable, Sequence

from backend import config
from backend.games.base import GameModule
from backend.games.game1_rewire import RewireGame
from backend.games.game2_sweep import SweepGame
from backend.games.game3_decant import DecantGame
from backend.games.game4_echo import EchoGame

# Game owners register their module instance here (task T4.x.3 — the
# sanctioned one-line edit, alongside their id in config.GAME_ORDER).
REGISTERED_MODULES: list[GameModule] = [
    RewireGame(),
    SweepGame(),
    DecantGame(),
    EchoGame(),
]


class GameRegistry:
    """Resolves stages to game modules; `modules`/`game_order` default to the
    module-level registrations and `config.GAME_ORDER` (overridable in tests)."""

    def __init__(
        self,
        modules: Iterable[GameModule] | None = None,
        game_order: Sequence[str] | None = None,
    ) -> None:
        self._order = list(config.GAME_ORDER if game_order is None else game_order)
        self._by_id = {
            module.id: module
            for module in (REGISTERED_MODULES if modules is None else modules)
        }

    def for_stage(self, stage: int) -> GameModule:
        """Return the module for a 1-based stage index."""
        if not 1 <= stage <= len(self._order):
            raise ValueError(f"stage {stage} out of range 1..{len(self._order)}")
        game_id = self._order[stage - 1]
        module = self._by_id.get(game_id)
        if module is None:
            raise KeyError(f"no module registered for game id {game_id!r} (stage {stage})")
        return module

    def reset_all(self) -> None:
        for module in self._by_id.values():
            module.reset()
