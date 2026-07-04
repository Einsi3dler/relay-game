"""RelayEngine: the pure relay rules (loop, advance check, win).

Populated in Phase 2. Pure/synchronous over a Match; returns an EngineResult
describing what changed and which timers to (re)schedule — never sleeps or
does I/O. Implements the loop in docs/GAME_DESIGN.md §4.
"""

from __future__ import annotations
