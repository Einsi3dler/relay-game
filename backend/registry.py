"""GameRegistry: the id-indexed game library.

The engine resolves games by id (each player has their own assigned game per
docs/REDESIGN_PLAN.md); the lobby assignment UI reads `library()`. The engine
only ever asks the registry, never a concrete game (docs/ARCHITECTURE.md §2).
"""

from __future__ import annotations

from typing import Iterable

from backend import config
from backend.games.base import GameModule
from backend.games.duel1_rps import RockPaperScissorsDuel
from backend.games.duel2_crown import CrownDuel
from backend.games.duel3_number_clash import NumberClash
from backend.games.duel4_bid_war import BidWar
from backend.games.duel_base import DuelModule
from backend.games.game1_rewire import RewireGame
from backend.games.game2_sweep import SweepGame
from backend.games.game3_decant import DecantGame
from backend.games.game4_echo import EchoGame
from backend.games.game5_mirror_run import MirrorRunGame
from backend.games.game6_overprint import OverprintGame
from backend.games.game7_stackdrop import StackdropGame
from backend.games.game8_lane_shift import LaneShiftGame
from backend.games.game9_shadow_cast import ShadowCastGame
from backend.games.game10_threadline import ThreadlineGame
from backend.games.game11_bomb_defuse import BombDefuseGame

# Game owners register their module instance here (the sanctioned one-line
# cross-slice edit, alongside a role entry in config.ROLES).
REGISTERED_MODULES: list[GameModule] = [
    RewireGame(),
    SweepGame(),
    MirrorRunGame(),
    DecantGame(),
    EchoGame(),
    OverprintGame(),
    StackdropGame(),
    LaneShiftGame(),
    ShadowCastGame(),
    ThreadlineGame(),
    BombDefuseGame(),
]

# Duel games live in their own list: they implement a different interface
# (DuelModule, not GameModule) and the Grandmaster never picks one — the
# server does. Keeping them out of REGISTERED_MODULES is what keeps them out
# of `library()`, and so out of the lobby game picker.
REGISTERED_DUELS: list[DuelModule] = [
    RockPaperScissorsDuel(),
    CrownDuel(),
    NumberClash(),
    BidWar(),
]


class GameRegistry:
    """Resolves game ids to modules; `modules` defaults to the module-level
    registrations (overridable in tests)."""

    def __init__(
        self,
        modules: Iterable[GameModule] | None = None,
        duels: Iterable[DuelModule] | None = None,
    ) -> None:
        self._by_id = {
            module.id: module
            for module in (REGISTERED_MODULES if modules is None else modules)
        }
        self._duels_by_id = {
            duel.id: duel
            for duel in (REGISTERED_DUELS if duels is None else duels)
        }

    def by_id(self, game_id: str) -> GameModule:
        """Return the module registered under `game_id` (KeyError if unknown)."""
        module = self._by_id.get(game_id)
        if module is None:
            raise KeyError(f"no module registered for game id {game_id!r}")
        return module

    def has(self, game_id: str) -> bool:
        return game_id in self._by_id

    def game_count(self) -> int:
        """How many game modules are registered.

        This is what caps a team: no two teammates may play the same game
        (RelayEngine.assign_game), so the library is the seat count.
        """
        return len(self._by_id)

    def library(self) -> list[dict[str, str | None]]:
        """All registered games as `{id, name, role}` for the assignment UI.

        `role` is the game's specialist role id — catch-all roles (games=None)
        and reserved roles (games=[]) never claim a game.
        """
        role_of = {
            game_id: role_id
            for role_id, role in config.ROLES.items()
            if role["games"]
            for game_id in role["games"]
        }
        return [
            {"id": module.id, "name": module.name, "role": role_of.get(module.id)}
            for module in self._by_id.values()
        ]

    # --- duels ---

    def duel_by_id(self, duel_game_id: str) -> DuelModule:
        """Return the duel module registered under `duel_game_id`."""
        module = self._duels_by_id.get(duel_game_id)
        if module is None:
            raise KeyError(f"no duel module registered for id {duel_game_id!r}")
        return module

    def has_duel(self, duel_game_id: str) -> bool:
        return duel_game_id in self._duels_by_id

    def pick_duel(self, seed: int) -> DuelModule:
        """The server's choice of duel game — Duelists don't pick their own.

        Deterministic in `seed` so a match's duel game is reproducible from
        the seed the engine drew.
        """
        duels = list(self._duels_by_id.values())
        if not duels:
            raise KeyError("no duel modules registered")
        return duels[seed % len(duels)]

    def duel_library(self) -> list[dict[str, str]]:
        """All registered duels as `{id, name}` — for the explainer pages, not
        for the assignment picker."""
        return [
            {"id": duel.id, "name": duel.name}
            for duel in self._duels_by_id.values()
        ]

    def reset_all(self) -> None:
        for module in self._by_id.values():
            module.reset()
        for duel in self._duels_by_id.values():
            duel.reset()
