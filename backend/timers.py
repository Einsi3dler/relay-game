"""TimerService: schedules deadline callbacks into the engine.

Populated in T3.1. One pending deadline per (match_id, player_id).
See docs/ARCHITECTURE.md §4.
"""

from __future__ import annotations
