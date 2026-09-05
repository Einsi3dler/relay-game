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
# Rounds each team must clear to win. This is the *default*; the host picks the
# length of their own match (MIN_LEVEL_COUNT..max_level_count()).
LEVEL_COUNT = 10
MIN_LEVEL_COUNT = 3          # shorter than this is not a race

# How deep every game's difficulty table is (docs/GAME_MODULE_SPEC.md: each
# table has LEVEL_COUNT + BONUS_LEVEL_OFFSET rows, tiers 11..13 being bonus-only
# headroom). This is a fixed contract with the game modules and does NOT move
# when a host shortens their match — a short match plays *fewer* rungs of the
# same ladder, not a shorter ladder.
DIFFICULTY_TIERS = 13
WAIT_SECONDS = 180           # cleared-status hold before it lapses back to solving
CURRENCY_PER_CLEAR = 1       # paid once per player per level, on the first clear
CURRENCY_BONUS_FIRST = 3     # first successful bonus of a level
CURRENCY_BONUS_REPEAT = 1    # each later bonus that level (diminishing returns)
BONUS_LEVEL_OFFSET = 3       # bonus puzzle = own game at level + this offset


def max_level_count() -> int:
    """The longest match the game tables can serve.

    The finale still owes a bonus board harder than itself, so the top
    BONUS_LEVEL_OFFSET tiers are reserved and the last *main* rung is what is
    left. Deepen every game's table and this rises with it.
    """
    return DIFFICULTY_TIERS - BONUS_LEVEL_OFFSET


def difficulty_tier(round_number: int, rounds: int) -> int:
    """Which rung of the 13-row table round `round_number` of `rounds` plays.

    A shorter match is a quicker race, not an easier one: the rungs are spread
    so every match length starts at tier 1 and finishes at the hardest main
    tier, with the bonus reaching the top of the table either way. At the
    default length this is the identity — round 7 of 10 is tier 7.

        3 rounds ->  1  5 10
        4 rounds ->  1  4  7 10
       10 rounds ->  1  2  3  4  5  6  7  8  9 10
    """
    top = max_level_count()
    if rounds <= 1:
        return top
    clamped = min(max(round_number, 1), rounds)
    return 1 + round((clamped - 1) * (top - 1) / (rounds - 1))

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
# --- Staked duels (BID WAR) ---
# A staked duel is bought, not given: the Duelist asks their Grandmaster for
# coins out of the team purse, and bids with exactly what they are handed. The
# grant is spent whether they win or lose, so funding a champion is a real
# trade against the perk shop.
# Below this in EITHER purse the server deals a free duel instead: teams open
# a match on nothing, and a staked duel fought with two empty hands is decided
# by a tiebreak neither side paid for.
DUEL_STAKE_MIN_PURSE = 8
DUEL_STAKE_REQUEST_SECONDS = 25  # the window a Grandmaster has to answer
DUEL_STAKE_DEFAULT = 10          # auto-granted when they never do (capped by
                                 # what the purse actually holds, including 0)
# The sale is funded by the two stakes and paid out at a multiple of them, so
# money does enter the game but only in proportion to what was actually risked:
#
#     pool = 2 * min(stake_a, stake_b) * DUEL_STAKE_POOL_MULTIPLIER
#
# `min`, not the sum, and that is the load-bearing part. Sizing the pool off the
# combined stake means out-staking your opponent inflates the prize you are
# bidding for, and staking everything becomes the only correct move — the exact
# degenerate shape the first version of this had. Off the smaller stake, out-
# staking only buys bidding power: it cannot grow the pot, so there is a best
# stake to find and going past it burns coins. Matching your opponent is what
# makes the sale worth having, which is a decision two Grandmasters make about
# each other without ever speaking.
DUEL_STAKE_POOL_MULTIPLIER = 4   # 3-5 is the intended band; higher = more money
DUEL_STAKE_POOL_FLOOR = 20       # a backstop so two poor teams still have a sale
DUEL_STAKE_LOTS = 5              # lots the pool is cut into

# The per-round choice window. Each duel module declares its own natural cost
# (`choice_seconds`: 5s to throw a hand, 10s to read a hand of cards), and the
# host may override it for the whole match so every duel runs at the pace their
# group wants. `None` on the match means each duel keeps its own.
DUEL_ROUND_SECONDS_MIN = 3   # below this nobody reads the board, they guess
DUEL_ROUND_SECONDS_MAX = 30
# What the host's picker offers, inside those bounds.
DUEL_ROUND_SECONDS_CHOICES = (3, 5, 8, 10, 12, 15, 20, 30)

# --- Link duels (/explore rooms; backend/duelroom.py) --------------------
# Two people, one link, one duel, outside any match. A room has no teams, no
# purses and no levels, so the two numbers a duel would normally read off a
# match have to come from somewhere: here.
#
# BID WAR is the only staked duel, and a room's grant is EQUAL to both sides —
# unlike a match's, which is unequal by design because a Grandmaster chooses
# how much to back their champion and that choice is the game. Nobody makes
# that choice in a room, so an unequal grant would not be a decision, only an
# unfair sale. Through `pool_for`, 20 a side buys a 160-coin sale in
# DUEL_STAKE_LOTS pieces, which is roughly the shape a well-funded match duel
# has. Deliberately not derived from DUEL_STAKE_DEFAULT: the auto-grant a
# silent Grandmaster gets and the fixed grant a room hands out are answers to
# different questions, and tying them together would hide that.
DUEL_ROOM_STAKE = 20
DUEL_ROOM_REVEAL_SECONDS = 3     # the beat before the next round, as in a match
# A room with nobody in it aged out. Its link stops resolving at that point, so
# this is really "how long a link is good for once everyone has stopped
# looking at it" — a tab left open keeps touching the room and it lives on.
DUEL_ROOM_TTL_SECONDS = 1800

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
# Costs run 1..8 rather than the old 1..4. The spread is the point: with
# everything inside a coin or two of everything else, a Grandmaster could hold
# the whole shop and never trade one purchase against another. Now the cheap
# plays stay impulse buys and the two that decide fights — Reflect, which
# bounces an attack back at its buyer, and Silence, which blinds a Grandmaster
# outright — cost enough to be worth saving for. PROVISIONAL: see
# docs/PLAYTEST_GUIDE.md.
PERKS: dict[str, dict] = {
    # --- attacks: enforced ---
    "freeze":      {"name": "Freeze",      "kind": "attack",  "cost": 4, "seconds": 10,
                    "desc": "A random opponent can't submit for 10s."},
    "scramble":    {"name": "Scramble",    "kind": "attack",  "cost": 3,
                    "desc": "A random solving opponent gets a fresh board."},
    "clock_burn":  {"name": "Clock Burn",  "kind": "attack",  "cost": 4, "seconds": 30,
                    "desc": "Burn 30s off a random cleared opponent's wait."},
    # Skim costs more than it takes on purpose: it is attrition that hurts you
    # too (net -1 each), a way to deny a purchase, never a way to farm.
    "skim":        {"name": "Skim",        "kind": "attack",  "cost": 2, "amount": 1,
                    "desc": "Steal 1 from the opponent's pool."},
    "silence":     {"name": "Silence",     "kind": "attack",  "cost": 7, "seconds": 30,
                    "desc": "Blind the enemy Grandmaster for 30s: roster, "
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
    "shield":      {"name": "Shield",      "kind": "defense", "cost": 3,
                    "desc": "Blocks the next incoming attack."},
    "reflect":     {"name": "Reflect",     "kind": "defense", "cost": 8,
                    "desc": "Bounces the next attack back at its buyer."},
    "insurance":   {"name": "Insurance",   "kind": "defense", "cost": 3,
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
    "duelist":          {"name": "Duelist",
                         "games": ["rps_duel", "crown_duel", "number_clash",
                                   "bid_war"],
                         "duel": True},
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

# --- Rejoin codes ---
# A player who loses their browser recovers their seat by typing this code, so
# it has to survive being read aloud across a noisy room: no 0/O, 1/I/L, or the
# lowercase half of the alphabet. Six characters over 31 symbols is ~9x10^8
# combinations, far past guessing a live match dry before it ends.
REJOIN_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
REJOIN_CODE_LENGTH = 6
