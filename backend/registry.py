"""GameRegistry: the id-indexed game library.

The engine resolves games by id (each player has their own assigned game per
docs/REDESIGN_PLAN.md); the lobby assignment UI reads `library()`. The engine
only ever asks the registry, never a concrete game (docs/ARCHITECTURE.md §2).
"""

from __future__ import annotations

from typing import Iterable

from backend import config
from backend.games.base import GameModule
from backend.games.game1_rewire import RewireGame
from backend.games.game2_sweep import SweepGame
from backend.games.game3_decant import DecantGame
from backend.games.game4_echo import EchoGame
from backend.games.game5_mirror_run import MirrorRunGame
from backend.games.game6_overprint import OverprintGame

# Game owners register their module instance here (the sanctioned one-line
# cross-slice edit, alongside a role entry in config.ROLES).
REGISTERED_MODULES: list[GameModule] = [
    RewireGame(),
    SweepGame(),
    MirrorRunGame(),
    DecantGame(),
    EchoGame(),
    OverprintGame(),
]


class GameRegistry:
    """Resolves game ids to modules; `modules` defaults to the module-level
    registrations (overridable in tests)."""

    def __init__(self, modules: Iterable[GameModule] | None = None) -> None:
        self._by_id = {
            module.id: module
            for module in (REGISTERED_MODULES if modules is None else modules)
        }

    def by_id(self, game_id: str) -> GameModule:
        """Return the module registered under `game_id` (KeyError if unknown)."""
        module = self._by_id.get(game_id)
        if module is None:
            raise KeyError(f"no module registered for game id {game_id!r}")
        return module

    def has(self, game_id: str) -> bool:
        return game_id in self._by_id

    def library(self) -> list[dict[str, str | None]]:
        """All registered games as `{id, name, role}` for the assignment UI."""
        role_of = {
            game_id: role
            for role, game_ids in config.ROLES.items()
            for game_id in game_ids
        }
        return [
            {"id": module.id, "name": module.name, "role": role_of.get(module.id)}
            for module in self._by_id.values()
        ]

    def reset_all(self) -> None:
        for module in self._by_id.values():
            module.reset()
