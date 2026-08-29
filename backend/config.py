"""All gameplay tunables live here (timers, team size, levels, currency, perks).

Single source of truth per docs/ARCHITECTURE.md §2 — nothing else in the
codebase may hard-code these values.
"""

from __future__ import annotations

# --- Teams ---
# A team never fields two players on the same game (RelayEngine.assign_game),
# so the registry is what caps a team: one seat per registered game, plus the
# single Duelist seat whose game the server picks from the duel catalogue.
# Registering a game raises the ceiling on its own — there is no hand-kept
# number here to fall out of step with the games that actually exist.
DUEL_SEATS_PER_TEAM = 1      # a duel has two sides, so one champion per team
MIN_PLAYERS_PER_TEAM = 4     # both teams need this many players to start
TEAM_IDS = ("alpha", "bravo")
TEAM_NAME_MAX = 20           # longest host-set team name


def max_players_per_team(game_count: int) -> int:
    """The most PLAYING members one team can hold (the leader is extra).

    `game_count` is the number of registered game modules. Every playing member
    but the Duelist needs a game of their own, and the Duelist's comes from the
    duel catalogue instead, so the ceiling is one seat per game plus the duel
    seat. Below this a match is merely small; above it `start_blocker` could
    never be satisfied, because somebody would have no game left to be given.
    """
    return game_count + DUEL_SEATS_PER_TEAM

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

# A game may cap its own board with `payload["time_limit_seconds"]`
# (docs/GAME_MODULE_SPEC.md). The engine publishes that deadline to the player
# and kills the board this many seconds later — slack the player never sees,
# covering an answer already in flight when the deadline passes. Games without
# the key are unlimited, which is still the default.
PUZZLE_GRACE_SECONDS = 5

# --- Duels (the Duelist role; docs/DUEL_MODULE_SPEC.md) ---
# A Duelist earns green for their team by beating the opposing Duelist. These
# are the engine-side costs; the move set and the per-round choice window come
# from the duel module itself, so different duel games cost different time.
DUELS_PER_LEVEL = 2          # duels a Duelist plays per level: the main, then
                             # one bonus. After the last the series is over and
                             # both champions go green until the level advances.
DUEL_INTERVAL_SECONDS = 30   # gap from one duel resolving to the next starting
DUEL_REVEAL_SECONDS = 3      # reveal beat between rounds of the same duel
DUEL_PENALTY_SECONDS = 60    # advance lock on the losing team, once per level
DUEL_WIN_CURRENCY = 2        # paid to the winning team, doubling per...
DUEL_CURRENCY_CAP = 8        # ...consecutive win, capped here

# Perk catalogue: leader-only purchases from the team currency pool.
#   "seconds" — the effect duration/extension where the perk has one.
#   "amount"  — currency moved, for the perks that move currency.
#   "effect"  — marks a SCREEN-EFFECT perk: the server stamps a deadline on the
#               victim and the client renders it. These are cosmetic and
#               therefore *unenforceable* — a determined player can disable one
#               in devtools — so they are priced as annoyances, not counters.
#               Every other attack is enforced server-side.
# An attack that would land on nobody is rejected, not wasted (see
# RelayEngine._apply_attack), so a perk never costs currency for no effect.
PERKS: dict[str, dict] = {
    # --- attacks: enforced ---
    "freeze":      {"name": "Freeze",      "kind": "attack",  "cost": 3, "seconds": 10,
                    "desc": "A random opponent can't submit for 10s."},
    "scramble":    {"name": "Scramble",    "kind": "attack",  "cost": 2,
                    "desc": "A random solving opponent gets a fresh board."},
    "clock_burn":  {"name": "Clock Burn",  "kind": "attack",  "cost": 3, "seconds": 30,
                    "desc": "Burn 30s off a random cleared opponent's wait."},
    # Skim costs more than it takes on purpose: it is attrition that hurts you
    # too (net -1 each), a way to deny a purchase, never a way to farm.
    "skim":        {"name": "Skim",        "kind": "attack",  "cost": 2, "amount": 1,
                    "desc": "Steal 1 from the opponent's pool."},
    "silence":     {"name": "Silence",     "kind": "attack",  "cost": 3, "seconds": 30,
                    "desc": "Blind the enemy Grandmaster for 30s — roster, "
                            "feed and bomb manual."},
    # --- attacks: screen effects (cosmetic) ---
    "wobble":      {"name": "Wobble",      "kind": "attack",  "cost": 2, "seconds": 12,
                    "effect": "wobble",
                    "desc": "A random opponent's board wobbles for 12s."},
    "static":      {"name": "Static",      "kind": "attack",  "cost": 2, "seconds": 10,
                    "effect": "static",
                    "desc": "Screen noise over a random opponent's board."},
    "mirror":      {"name": "Mirror",      "kind": "attack",  "cost": 3, "seconds": 10,
                    "effect": "mirror",
                    "desc": "Flip a random opponent's board for 10s."},
    "blackout":    {"name": "Blackout",    "kind": "attack",  "cost": 3, "seconds": 4,
                    "effect": "blackout",
                    "desc": "Black out a random opponent's board for 4s."},
    # --- defense ---
    "shield":      {"name": "Shield",      "kind": "defense", "cost": 2,
                    "desc": "Blocks the next incoming attack."},
    "reflect":     {"name": "Reflect",     "kind": "defense", "cost": 4,
                    "desc": "Bounces the next attack back at its buyer."},
    "insurance":   {"name": "Insurance",   "kind": "defense", "cost": 2,
                    "desc": "A failed bonus keeps its earnings this level."},
    "extend_wait": {"name": "Extend Wait", "kind": "defense", "cost": 1, "seconds": 60,
                    "desc": "+60s on a chosen cleared teammate's wait."},
}

# Screen-effect ids the client knows how to render. Kept here so the engine can
# reject a mistyped `effect` in the catalogue rather than stamping a deadline
# no renderer will ever pick up.
SCREEN_EFFECTS = ("wobble", "static", "mirror", "blackout")

# Role catalogue (docs/TASK_LIST.md V8): the Grandmaster (team leader) assigns
# each player a role in the lobby; the game picker then only offers that
# role's games.
#   games=None    -> any registered game (Generalist).
#   games=[]      -> reserved, not assignable (no matching game shipped yet).
#   duel=True     -> the Duelist: the *server* picks the game, the role is
#                    mirrored (both teams field one or neither does), and the
#                    player duels the opposing Duelist instead of solving.
#   fixed=True    -> the role carries exactly one game and assigns it itself;
#                    the Grandmaster picks *who* holds the role, never what
#                    they play. `assign_game` is refused for these.
#   required=True -> every team must field exactly one, or the match can't
#                    start. Only the Defuser: the bomb is the game no team
#                    opts out of.
ROLES: dict[str, dict] = {
    "logician":         {"name": "Logician",         "games": ["sweep", "threadline"]},
    "technocrat":       {"name": "Technocrat",       "games": ["rewire", "lane_shift"]},
    "spatial_reasoner": {"name": "Spatial Reasoner", "games": ["mirror_run", "shadow_cast"]},
    "puzzle_master":    {"name": "Puzzle Master",    "games": ["decant", "stackdrop"]},
    "spymaster":        {"name": "Spymaster",        "games": ["echo", "overprint"]},
    "generalist":       {"name": "Generalist",       "games": None},
    "defuser":          {"name": "Defuser",          "games": ["bomb_defuse"],
                         "fixed": True, "required": True},
    "duelist":          {"name": "Duelist",          "games": ["rps_duel"], "duel": True},
}


def role_allows(role_id: str, game_id: str) -> bool:
    """True if the role may be assigned `game_id` (Generalist allows all)."""
    games = ROLES[role_id]["games"]
    return games is None or game_id in games


def role_assignable(role_id: str) -> bool:
    """True if the role can be given to a player at all (reserved roles can't)."""
    games = ROLES[role_id]["games"]
    return games is None or bool(games)


def role_is_duel(role_id: str | None) -> bool:
    """True for a role whose player duels instead of solving puzzles."""
    return bool(role_id) and bool(ROLES.get(role_id, {}).get("duel"))


def role_fixed_game(role_id: str | None) -> str | None:
    """The one game a `fixed` role assigns itself, or None.

    The Duelist is deliberately *not* one of these: the server picks its game
    from the duel catalogue at random, so there is no single id to return.
    """
    if not role_id:
        return None
    role = ROLES.get(role_id, {})
    if not role.get("fixed"):
        return None
    return role["games"][0]


def required_roles() -> list[str]:
    """Roles every team must field exactly one of."""
    return [role_id for role_id, role in ROLES.items() if role.get("required")]

# --- Server behaviour ---
SUBMIT_MIN_INTERVAL_MS = 300     # reject submissions arriving faster than this
MATCH_TTL_SECONDS = 1800         # evict finished/idle matches after this long
