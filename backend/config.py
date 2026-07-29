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
STAGE_COUNT = 6
TEAM_IDS = ("alpha", "bravo")

# --- v2 (leaders/levels/currency — docs/REDESIGN_PLAN.md) ---
LEVEL_COUNT = 10             # levels each team must clear to win
WAIT_SECONDS = 180           # cleared-status hold before it lapses back to solving
CURRENCY_PER_CLEAR = 1       # paid once per player per level, on the first clear
CURRENCY_BONUS_FIRST = 3     # first successful bonus of a level
CURRENCY_BONUS_REPEAT = 1    # each later bonus that level (diminishing returns)
BONUS_LEVEL_OFFSET = 3       # bonus puzzle = own game at level + this offset

# Perk catalogue: leader-only purchases from the team currency pool.
# "seconds" is the effect duration/extension where the perk has one.
PERKS: dict[str, dict] = {
    "freeze":      {"name": "Freeze",      "kind": "attack",  "cost": 3, "seconds": 10},
    "scramble":    {"name": "Scramble",    "kind": "attack",  "cost": 2},
    "shield":      {"name": "Shield",      "kind": "defense", "cost": 2},
    "extend_wait": {"name": "Extend Wait", "kind": "defense", "cost": 1, "seconds": 60},
}

# Placeholder role grouping for the game library (real roles are future work).
ROLES: dict[str, list[str]] = {
    "builder": ["rewire", "decant"],
    "scout":   ["sweep", "mirror_run"],
    "cipher":  ["echo", "overprint"],
}

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
    "overprint",
]
