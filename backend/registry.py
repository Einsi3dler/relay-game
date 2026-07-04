"""GameRegistry: maps a 1-based stage index to its GameModule.

Populated in T1.4; built from `config.GAME_ORDER`. The engine only ever asks
the registry, never a concrete game.
"""

from __future__ import annotations
