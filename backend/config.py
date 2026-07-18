"""All gameplay tunables live here (timers, team size, stage count, game order).

Single source of truth per docs/ARCHITECTURE.md §2 — nothing else in the
codebase may hard-code these values.
"""

from __future__ import annotations

# --- Timers (seconds; 0 disables) ---
REST_SECONDS = 15            # rest after a correct main answer, before holding kicks in
HOLDING_SECONDS = 20         # time allowed on a holding question before losing green
MAIN_PUZZLE_SECONDS = 0      # main-puzzle time limit (0 = none in the MVP)

# --- Teams & stages ---
PLAYERS_PER_TEAM = 4
MIN_PLAYERS_PER_TEAM = 4     # both teams need this many players to start
STAGE_COUNT = 5
TEAM_IDS = ("alpha", "bravo")

# --- Server behaviour ---
SUBMIT_MIN_INTERVAL_MS = 300     # reject submissions arriving faster than this
MATCH_TTL_SECONDS = 1800         # evict finished/idle matches after this long

# Game module id per stage (index 0 = Stage 1). Registered per T4.x.3.
GAME_ORDER: list[str] = [
    "rewire",
    "sweep",
    "mirror_run",
    "decant",
    "echo",
]
