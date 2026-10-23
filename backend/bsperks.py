"""BULLSHIT QUIZ perks: the catalogue, the offers, and what they do to scores.

Pure functions over plain values, like `backend/bsquiz.py`, whose formulas
these sit on top of. Who may play what and when is the room's business
(`backend/bsroom.py`); this module knows what each perk is and, once a round
is locked, how the perks played in it change everybody's points. The design,
with every rule and the reason for it, is `docs/BULLSHIT_QUIZ_PERKS.md`.

Each player gets exactly one perk for the whole game, and no two players
the same one: they choose in a draft before the quiz (`backend/bsdraft.py`).
So nobody ever stacks two of their own; the only time two perks meet on one
player is when somebody else's lands on them (a Blood Pact on a player who
also doubled, say), or a Counterfeit copies one.

Card text is what players read, so it follows the house rule for copy: no
em dashes (tests/test_bsperks.py checks).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace

from backend import bsquiz, config
from backend.bsquiz import GUESSER, TRUTHFUL

# Who may play a perk.
WHO_GUESSER = "guesser"
WHO_SPEAKER = "speaker"           # truthful or bullshitting
WHO_WAGER = "wager"               # anyone holding a wager: guesser or bullshitter
WHO_ANYONE = "anyone"

# What playing a perk asks for, so a screen knows what to put in front of
# the player. The room sends the eligible choices; this only names the kind.
NEEDS_NOTHING = ""
NEEDS_GUESSER = "guesser"
NEEDS_SPEAKER = "speaker"
NEEDS_PLAYER = "player"
NEEDS_OPTION = "option"
NEEDS_ANSWER = "answer"           # an option and a wager: Blind Faith
NEEDS_WORDS = "words"             # a speaker and three words
NEEDS_PLAY = "play"               # a perk somebody else played
NEEDS_COPY = "copy"               # a perk to copy


@dataclass(frozen=True)
class Perk:
    id: str
    name: str
    text: str             # the card
    family: str           # wager, side, info, speaker, floor, power, counter
    who: str
    needs: str
    public: bool          # announced the moment it is played
    min_players: int = config.BS_MIN_PLAYERS
    min_options: int = 2


PERKS: tuple[Perk, ...] = (
    # --- wager perks ---
    Perk("insurance_fraud", "Insurance Fraud",
         "Lose your wager this round and only half of it comes off, rounded up. "
         "Win it and you get the full amount.",
         "wager", WHO_WAGER, NEEDS_NOTHING, public=False),
    Perk("double_or_nothing", "Double or Nothing",
         "Your wager counts twice this round, win or lose. Bet 8 and it is +16 or −16.",
         "wager", WHO_WAGER, NEEDS_NOTHING, public=False),
    Perk("backup_plan", "Backup Plan",
         "Pick a backup answer as well as your main one. If your main answer is "
         "wrong and your backup is right, you lose nothing.",
         "wager", WHO_GUESSER, NEEDS_OPTION, public=False, min_players=4, min_options=3),
    Perk("blind_faith", "Blind Faith",
         "Lock your answer and wager before anyone has said a word. Right pays "
         "three times your wager. Wrong costs only the wager.",
         "wager", WHO_GUESSER, NEEDS_ANSWER, public=False, min_players=4, min_options=3),
    Perk("damage_control", "Damage Control",
         "After the reveal, win back up to 5 points of a wager you just lost.",
         "wager", WHO_WAGER, NEEDS_NOTHING, public=True),
    # --- side bets ---
    Perk("ride_or_die", "Ride or Die",
         "Secretly back another guesser. If they get it right, you get 5. If they "
         "get it wrong, you lose 5.",
         "side", WHO_GUESSER, NEEDS_GUESSER, public=False, min_players=4),
    Perk("terrible_judgement", "I Know Your Judgement Is Terrible",
         "Secretly bet against another guesser. If they get it wrong, you get 5. "
         "If they get it right, you lose 5.",
         "side", WHO_GUESSER, NEEDS_GUESSER, public=False, min_players=4),
    Perk("lone_wolf", "Lone Wolf",
         "If you are the only guesser who picks the right answer, take 10 extra points.",
         "side", WHO_GUESSER, NEEDS_NOTHING, public=False, min_players=5),
    Perk("call_bullshit", "I Call Bullshit",
         "Secretly accuse one speaker. If they are bluffing, you get 5. If they are "
         "telling the truth, you lose 5.",
         "side", WHO_GUESSER, NEEDS_SPEAKER, public=False),
    # --- information ---
    Perk("read_the_room", "Read the Room",
         "Vote last. Once everybody else has locked in, you see how many picked "
         "each answer, then choose yours.",
         "info", WHO_GUESSER, NEEDS_NOTHING, public=False, min_players=4),
    Perk("inside_information", "Inside Information",
         "Peek at one guesser's locked answer before you lock yours. They will not know.",
         "info", WHO_GUESSER, NEEDS_GUESSER, public=False, min_players=4),
    Perk("the_audit", "The Audit",
         "Put one guesser's locked answer and wager on every screen while voting "
         "is still open.",
         "info", WHO_ANYONE, NEEDS_GUESSER, public=True),
    # --- speaker perks ---
    Perk("silver_tongue", "Silver Tongue",
         "Earn 1 extra point for every person who picks your answer. Each still "
         "counts as one vote.",
         "speaker", WHO_SPEAKER, NEEDS_NOTHING, public=False),
    Perk("victory_lap", "Victory Lap",
         "If your answer draws the biggest crowd outright, take 10 extra points.",
         "speaker", WHO_SPEAKER, NEEDS_NOTHING, public=False),
    Perk("target_audience", "Target Audience",
         "Secretly choose one guesser. If they pick your answer, you get 5 extra points.",
         "speaker", WHO_SPEAKER, NEEDS_GUESSER, public=False),
    Perk("honourable_mention", "Honourable Mention",
         "If your answer finishes outright second, take 5 extra points.",
         "speaker", WHO_SPEAKER, NEEDS_NOTHING, public=False, min_players=4),
    Perk("false_witness", "False Witness",
         "Secretly recruit a guesser to say a few words for your answer. They keep "
         "it quiet until the reveal and still vote however they like.",
         "speaker", WHO_SPEAKER, NEEDS_GUESSER, public=False),
    # --- floor perks: run on the call ---
    Perk("hot_seat", "The Hot Seat",
         "Put one speaker on the spot for 20 seconds. You ask, only they answer, "
         "and nobody can jump in.",
         "floor", WHO_ANYONE, NEEDS_SPEAKER, public=True),
    Perk("mute_button", "The Mute Button",
         "Silence one speaker for 20 seconds, once every speaker has made their "
         "opening case.",
         "floor", WHO_ANYONE, NEEDS_SPEAKER, public=True),
    Perk("last_word", "The Last Word",
         "Take 20 seconds, uninterrupted, right before voting opens.",
         "floor", WHO_ANYONE, NEEDS_NOTHING, public=True),
    Perk("forbidden_words", "Forbidden Words",
         "Ban three words for one speaker. Every time they say one, they lose 2 points.",
         "floor", WHO_ANYONE, NEEDS_WORDS, public=True),
    # --- power plays ---
    Perk("blackmail", "Blackmail",
         "Force one guesser to bet their highest number left. They still choose "
         "their own answer.",
         "power", WHO_ANYONE, NEEDS_GUESSER, public=True),
    Perk("blood_pact", "Blood Pact",
         "Bind yourself to another guesser in front of everyone. Both right: you "
         "each win double your own wager. Either wrong: you both lose yours.",
         "power", WHO_GUESSER, NEEDS_GUESSER, public=True, min_players=4),
    Perk("pickpocket", "Pickpocket",
         "After the reveal, take 3 points from a guesser who got it wrong, in "
         "front of everyone.",
         "power", WHO_ANYONE, NEEDS_GUESSER, public=True),
    Perk("kingmaker", "Kingmaker",
         "When the biggest crowd is a tie, you pick which speaker wins it. The "
         "truth stays hidden until the reveal.",
         "power", WHO_ANYONE, NEEDS_OPTION, public=True, min_players=4),
    Perk("doppelganger", "Doppelgänger",
         "Secretly pick another player. Their score this round, before any perks, "
         "becomes yours, even if it is negative.",
         "power", WHO_GUESSER, NEEDS_PLAYER, public=False),
    Perk("hostile_takeover", "Hostile Takeover",
         "Secretly pick another player. If your answer is right, you swap total "
         "scores with them after this round.",
         "power", WHO_GUESSER, NEEDS_PLAYER, public=False),
    # --- counters ---
    Perk("objection", "Objection!",
         "Cancel a speaking perk someone just announced, before it starts.",
         "counter", WHO_ANYONE, NEEDS_PLAY, public=True),
    Perk("not_my_problem", "Not My Problem",
         "When a perk lands on you, send it to someone else it could have hit, "
         "even the player who sent it.",
         "counter", WHO_ANYONE, NEEDS_PLAY, public=True),
    Perk("counterfeit", "Counterfeit",
         "Copy any perk another player has already used and revealed this game, "
         "and play it as your own.",
         "counter", WHO_ANYONE, NEEDS_COPY, public=False),
)

PERK = {p.id: p for p in PERKS}

# Floor perks are public and run on the call; Objection! cancels these and
# only these. The first three run one at a time for BS_FLOOR_SECONDS.
FLOOR = frozenset({"hot_seat", "mute_button", "last_word", "forbidden_words"})
TIMED_FLOOR = frozenset({"hot_seat", "mute_button", "last_word"})

# The perks a target is told about, so Not My Problem can redirect them.
# Each gets a BS_REACTION_SECONDS window before it lands.
REDIRECTABLE = frozenset({"hot_seat", "mute_button", "forbidden_words", "blackmail",
                          "blood_pact", "the_audit", "pickpocket", "false_witness",
                          "hostile_takeover"})

BLIND_FAITH_MULTIPLIER = 3
DOUBLE_MULTIPLIER = 2
FORBIDDEN_WORD_COUNT = 3
FORBIDDEN_WORD_MAX = 20
_WORD = re.compile(r"^[^\W\d_]+(?:['’-][^\W\d_]+)*$")


# --- the draft pool -------------------------------------------------------

def available(n: int, most_options: int) -> list[Perk]:
    """Every perk that could ever be played in a game of `n` players whose
    biggest round shows `most_options` options. A perk nobody could use is
    left out of the draft (docs/BULLSHIT_QUIZ_PERKS.md §6: small games)."""
    return [p for p in PERKS if n >= p.min_players and most_options >= p.min_options]


def draft_pool(n: int, most_options: int) -> list[str]:
    """The perks to draft from: the playable ones, or all of them if that
    would leave somebody without one. Every player needs a different perk."""
    playable = [p.id for p in available(n, most_options)]
    return playable if len(playable) >= n else [p.id for p in PERKS]


# --- small rules ----------------------------------------------------------

def half_rounded_up(x: int) -> int:
    return math.ceil(x / 2)


def words_problem(words: object, option_text: str) -> str | None:
    """Why these cannot be Forbidden Words for a speaker defending
    `option_text`, or None. Three different single words, none taken from
    their own answer: they have to be able to say it."""
    if not isinstance(words, (list, tuple)) or len(words) != FORBIDDEN_WORD_COUNT:
        return f"Ban exactly {FORBIDDEN_WORD_COUNT} words."
    clean = [w.strip() if isinstance(w, str) else "" for w in words]
    if any(not w for w in clean):
        return "Fill in all three words."
    if any(len(w) > FORBIDDEN_WORD_MAX or not _WORD.match(w) for w in clean):
        return "Single words only: letters, no spaces."
    lowered = [w.lower() for w in clean]
    if len(set(lowered)) != len(lowered):
        return "Ban three different words."
    theirs = set(re.findall(r"[^\W_]+(?:['’-][^\W_]+)*", option_text.lower()))
    if theirs & set(lowered):
        return "You can't ban a word from their own answer."
    return None


def clean_words(words: list[str]) -> tuple[str, ...]:
    return tuple(w.strip().lower() for w in words)


def damage_control_points(stake: int) -> int:
    """What Damage Control wins back from a round's wager part."""
    return min(config.BS_PERK_POINTS["damage_control"], -stake) if stake < 0 else 0


# --- scoring --------------------------------------------------------------

@dataclass(frozen=True)
class Use:
    """One perk that took effect in a round, as scoring needs it. A
    Counterfeit arrives as the perk it copied."""
    perk: str
    by: str
    target: str | None = None
    option: str | None = None
    slips: int = 0


def score(n: int, panel: bsquiz.Panel,
          guesses: dict[str, tuple[str, int]],
          bluffs: dict[str, int],
          uses: list[Use],
          kingmaker: str | None = None) -> bsquiz.RoundResult:
    """Score a locked round with its perks, in the order the design gives
    (docs/BULLSHIT_QUIZ_PERKS.md §5):

      1-3. votes, the crowd winner (Kingmaker can settle a tie), plain scores
      4.   wager perks: Blood Pact, then a multiplier, Insurance Fraud,
           Backup Plan
      5.   Doppelgänger replaces all of that with the target's plain score
      6.   side points on top

    Reveal-time perks (Damage Control, Pickpocket, Hostile Takeover) happen
    after this, as they are played; the room applies those.
    """
    base = bsquiz.score_round(n, panel, guesses, bluffs, kingmaker)
    counts = base.counts
    correct = panel.correct_key
    points = config.BS_PERK_POINTS
    own = {u.by: u for u in uses}
    right = {p: line.won for p, line in base.lines.items() if line.role == GUESSER}
    pacts: dict[str, str] = {}
    for u in uses:
        if u.perk == "blood_pact" and u.target:
            pacts[u.by], pacts[u.target] = u.target, u.by

    parts: dict[str, list[tuple[str, int]]] = {}
    stakes: dict[str, int] = {}

    def add(player: str, perk: str, value: int) -> None:
        parts.setdefault(player, []).append((PERK[perk].name, value))

    # 4. the wager part
    for player, line in base.lines.items():
        if line.role == TRUTHFUL or line.wager is None:
            continue
        w, stake = line.wager, line.stake
        partner = pacts.get(player) if line.role == GUESSER else None
        if partner in right:            # a partner who cast no vote voids the pact
            if line.won and right[partner]:
                add(player, "blood_pact", w)
                stake = 2 * w
            elif line.won:
                add(player, "blood_pact", -2 * w)
                stake = -w
            else:
                add(player, "blood_pact", 0)
        use = own.get(player)
        perk = use.perk if use else None
        if perk == "double_or_nothing":
            add(player, perk, stake)
            stake *= DOUBLE_MULTIPLIER
        elif perk == "blind_faith":
            extra = stake * (BLIND_FAITH_MULTIPLIER - 1) if stake > 0 else 0
            add(player, perk, extra)
            stake += extra
        elif perk == "insurance_fraud":
            back = -stake - half_rounded_up(-stake) if stake < 0 else 0
            add(player, perk, back)
            stake += back
        elif perk == "backup_plan":
            rescued = stake < 0 and line.option != correct and use.option == correct
            add(player, perk, -stake if rescued else 0)
            stake = 0 if rescued else stake
        stakes[player] = stake

    # 5. Doppelgänger
    for use in uses:
        if use.perk != "doppelganger":
            continue
        mine = base.lines[use.by].delta if use.by in base.lines else 0
        theirs = base.lines[use.target].delta if use.target in base.lines else 0
        parts[use.by] = []
        add(use.by, "doppelganger", theirs - mine)

    # 6. side points
    for use in uses:
        p, perk, target = use.by, use.perk, use.target
        mine = panel.option_of(p)
        if perk == "ride_or_die":
            add(p, perk, 0 if target not in right else
                points[perk] if right[target] else -points[perk])
        elif perk == "terrible_judgement":
            add(p, perk, 0 if target not in right else
                -points[perk] if right[target] else points[perk])
        elif perk == "lone_wolf":
            alone = right.get(p, False) and counts.get(correct, 0) == 1
            add(p, perk, points[perk] if alone else 0)
        elif perk == "call_bullshit":
            add(p, perk, points[perk] if target in panel.bullshitters else -points[perk])
        elif perk == "silver_tongue" and mine:
            add(p, perk, counts.get(mine.key, 0))
        elif perk == "victory_lap" and mine:
            outright = bsquiz.crowd_winner(counts) == mine.key
            add(p, perk, points[perk] if outright else 0)
        elif perk == "target_audience" and mine:
            pick = guesses.get(target)
            add(p, perk, points[perk] if pick and pick[0] == mine.key else 0)
        elif perk == "honourable_mention" and mine:
            second = bsquiz.outright_second(counts) == mine.key
            add(p, perk, points[perk] if second else 0)
        elif perk == "forbidden_words" and target:
            slips = min(use.slips, config.BS_FORBIDDEN_MAX_SLIPS)
            add(target, perk, -points[perk] * slips)

    lines: dict[str, bsquiz.Line] = {}
    for player in [*base.lines, *(p for p in parts if p not in base.lines)]:
        line = base.lines.get(player) or bsquiz.Line(
            player=player, role=GUESSER, option=None, wager=None)
        extra = parts.get(player, [])
        lines[player] = replace(
            line, delta=line.delta + sum(v for _, v in extra),
            stake=stakes.get(player, line.stake), parts=tuple(extra))
    return bsquiz.RoundResult(counts=counts, winner=base.winner, lines=lines)
