"""All gameplay tunables live here (timers, team size, levels, currency, perks).

Single source of truth per docs/ARCHITECTURE.md §2 — nothing else in the
codebase may hard-code these values.
"""

from __future__ import annotations

# --- Teams ---
PLAYERS_PER_TEAM = 4         # playing members; each team also has +1 leader
MIN_PLAYERS_PER_TEAM = 4     # both teams need this many players to start
TEAM_IDS = ("alpha", "bravo")

# --- Levels, timers & currency (docs/REDESIGN_PLAN.md) ---
# The timer/currency/perk values below are PROVISIONAL pending the V6 playtest
# (docs/TASK_LIST.md V7, docs/PLAYTEST_GUIDE.md). They are reasoned starting
# points, not yet tuned from real play data — adjust them from the playtest.
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

# Role catalogue (docs/TASK_LIST.md V8): the Grandmaster (team leader) assigns
# each player a role in the lobby; the game picker then only offers that
# role's games.
#   games=None  -> any registered game (Generalist).
#   games=[]    -> reserved, not assignable (no matching game shipped yet).
ROLES: dict[str, dict] = {
    "logician":         {"name": "Logician",         "games": ["sweep"]},
    "technocrat":       {"name": "Technocrat",       "games": ["rewire"]},
    "spatial_reasoner": {"name": "Spatial Reasoner", "games": ["mirror_run"]},
    "puzzle_master":    {"name": "Puzzle Master",    "games": ["decant"]},
    "spymaster":        {"name": "Spymaster",        "games": ["echo", "overprint"]},
    "generalist":       {"name": "Generalist",       "games": None},
    "lexicon":          {"name": "Lexicon",          "games": []},  # reserved: no word game yet
}


def role_allows(role_id: str, game_id: str) -> bool:
    """True if the role may be assigned `game_id` (Generalist allows all)."""
    games = ROLES[role_id]["games"]
    return games is None or game_id in games


def role_assignable(role_id: str) -> bool:
    """True if the role can be given to a player at all (reserved roles can't)."""
    games = ROLES[role_id]["games"]
    return games is None or bool(games)

# --- Server behaviour ---
SUBMIT_MIN_INTERVAL_MS = 300     # reject submissions arriving faster than this
MATCH_TTL_SECONDS = 1800         # evict finished/idle matches after this long
