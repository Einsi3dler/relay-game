"""BULLSHIT QUIZ: the rules, and nothing else.

Pure functions over plain values. No room, no sockets, no storage: those live
in `backend/bsroom.py` and `backend/bsroster.py`, and they call in here for
every decision that is a rule rather than plumbing. The design is
`docs/BULLSHIT_QUIZ.md`; the section numbers below refer to it.

Policies the handoff left open, as settled by the game's owner:

  * **Wager numbers are single-use.** Each player starts with 1..N-1 and spends
    one on every wagering turn, win or lose. With exactly N-1 wagering turns
    each, everybody spends every number once, so *when* to bet big is the game.
  * **A tie at the top means nobody wins the crowd.** The truthful player gets
    N // 2 and every bullshitter loses their wager, keeping their points per
    person fooled. Guessers are scored on the correct answer regardless.

`crowd_winner` is the only place the tie policy lives. The score formulas
take its result as an input and never re-derive it, so changing the policy is
a one-function change (handoff §11).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from backend import config

TRUTHFUL = "truthful"
BULLSHITTER = "bullshitter"
GUESSER = "guesser"

OPTION_KEYS = ("A", "B", "C", "D")


# --- sizes that follow from N ---------------------------------------------
# N is every registered player in the game, the truthful one and the
# bullshitters included. It is fixed when the game starts and these numbers
# never change after that, whoever disconnects (handoff §3, §11).

def max_bullshitters(n: int) -> int:
    """At most three, and always at least one guesser left over: N-1-B >= 1."""
    return max(0, min(config.BS_MAX_BULLSHITTERS, n - 2))


def option_count_for(n: int, rng: random.Random) -> int:
    """How many options an author is asked to write: the real answer plus one
    decoy per bullshitter. Uniform over what the room size allows; the
    handoff sets no probabilities, so none are invented here."""
    top = max_bullshitters(n)
    if top < 1:
        raise ValueError(f"need at least {config.BS_MIN_PLAYERS} players")
    return 1 + rng.randint(1, top)


def wager_values(n: int) -> range:
    """Every legal wager: whole numbers 1..N-1 (handoff §6)."""
    return range(1, n)


def truthful_reward(n: int, won_crowd: bool) -> int:
    return n if won_crowd else n // 2


def wager_problem(n: int, wager: object, used: set[int]) -> str | None:
    """Why this wager is not allowed, or None if it is."""
    if isinstance(wager, bool) or not isinstance(wager, int):
        return "A wager is a whole number."
    if wager not in wager_values(n):
        return f"Wager between 1 and {n - 1}."
    if wager in used:
        return f"You have already used {wager}."
    return None


# --- the schedule ---------------------------------------------------------

def schedule(authors: list[str], rng: random.Random) -> list[str]:
    """The order questions are played in: every author exactly once."""
    order = list(authors)
    rng.shuffle(order)
    return order


# --- one round ------------------------------------------------------------

@dataclass(frozen=True)
class Question:
    """What an author submitted. `decoys` are the wrong answers they wrote."""
    author: str
    prompt: str
    answer: str
    decoys: tuple[str, ...]
    explanation: str = ""


@dataclass(frozen=True)
class Option:
    key: str          # "A".."D", the order it is shown in
    text: str
    speaker: str      # the player defending it
    correct: bool


@dataclass(frozen=True)
class Panel:
    """Who defends what this round. `options` is in display order, shuffled,
    so the truth is not always A."""
    author: str
    options: tuple[Option, ...]

    @property
    def bullshitters(self) -> tuple[str, ...]:
        return tuple(o.speaker for o in self.options if not o.correct)

    @property
    def speakers(self) -> tuple[str, ...]:
        return tuple(o.speaker for o in self.options)

    @property
    def correct_key(self) -> str:
        return next(o.key for o in self.options if o.correct)

    def option(self, key: str) -> Option | None:
        return next((o for o in self.options if o.key == key), None)

    def option_of(self, speaker: str) -> Option | None:
        return next((o for o in self.options if o.speaker == speaker), None)

    def role_of(self, player: str) -> str:
        if player == self.author:
            return TRUTHFUL
        if player in self.bullshitters:
            return BULLSHITTER
        return GUESSER


def deal_panel(question: Question, players: list[str],
               rng: random.Random) -> Panel:
    """Pick the bullshitters and hand each a different decoy (handoff §5).

    B is the number of decoys the author wrote, capped by what the room
    allows. Anyone but the author may be picked, including somebody who
    bluffed last round: repeat appearances are part of the game (§3).
    """
    others = [p for p in players if p != question.author]
    b = min(len(question.decoys), max_bullshitters(len(players)))
    if b < 1:
        raise ValueError("not enough decoys or players for a round")

    liars = rng.sample(others, b)
    decoys = rng.sample(list(question.decoys), b)
    entries = [(question.answer, question.author, True)]
    entries += [(text, liar, False) for text, liar in zip(decoys, liars)]
    rng.shuffle(entries)

    return Panel(
        author=question.author,
        options=tuple(Option(key=OPTION_KEYS[i], text=text, speaker=who,
                             correct=correct)
                      for i, (text, who, correct) in enumerate(entries)),
    )


# --- scoring --------------------------------------------------------------

def vote_counts(panel: Panel, guesses: dict[str, tuple[str, int]]) -> dict[str, int]:
    """Headcount per option. People, never wager sizes (handoff §7)."""
    counts = {o.key: 0 for o in panel.options}
    for key, _wager in guesses.values():
        if key in counts:
            counts[key] += 1
    return counts


def crowd_winner(counts: dict[str, int]) -> str | None:
    """The option with more votes than every other, or None.

    Policy: an outright lead is required, so a tie at the top (including
    nobody voting at all) means no speaker wins the crowd.
    """
    if not counts:
        return None
    top = max(counts.values())
    leaders = [key for key, votes in counts.items() if votes == top]
    if top <= 0 or len(leaders) != 1:
        return None
    return leaders[0]


@dataclass(frozen=True)
class Line:
    """One player's result for one round, kept as parts so the reveal can say
    "fooled 3, lost an 8 wager, -5" rather than just "-5" (handoff §12)."""
    player: str
    role: str
    option: str | None      # what they defended or picked
    wager: int | None       # None for the truthful player
    fooled: int = 0         # bullshitters only: guessers who picked their option
    won: bool = False       # guessers: picked the truth. speakers: won the crowd
    delta: int = 0


@dataclass(frozen=True)
class RoundResult:
    counts: dict[str, int]
    winner: str | None
    lines: dict[str, Line] = field(default_factory=dict)


def score_round(n: int, panel: Panel,
                guesses: dict[str, tuple[str, int]],
                bluffs: dict[str, int]) -> RoundResult:
    """Score one locked round.

    `guesses` maps each guesser to (option key, wager); `bluffs` maps each
    bullshitter to their wager. Both are assumed already validated by the
    room: this function applies the formulas, it does not police moves.
    """
    counts = vote_counts(panel, guesses)
    winner = crowd_winner(counts)
    correct = panel.correct_key
    lines: dict[str, Line] = {}

    truth_won = winner == correct
    lines[panel.author] = Line(
        player=panel.author, role=TRUTHFUL, option=correct, wager=None,
        won=truth_won, delta=truthful_reward(n, truth_won),
    )

    for option in panel.options:
        if option.correct:
            continue
        wager = bluffs[option.speaker]
        fooled = counts[option.key]
        won = winner == option.key
        lines[option.speaker] = Line(
            player=option.speaker, role=BULLSHITTER, option=option.key,
            wager=wager, fooled=fooled, won=won,
            delta=fooled + (wager if won else -wager),
        )

    for player, (key, wager) in guesses.items():
        right = key == correct
        lines[player] = Line(
            player=player, role=GUESSER, option=key, wager=wager,
            won=right, delta=wager if right else -wager,
        )

    return RoundResult(counts=counts, winner=winner, lines=lines)


def winners(totals: dict[str, int]) -> list[str]:
    """Everybody on the top score. A tie is joint winners: an extra round
    would break the fixed N-round structure (handoff §11)."""
    if not totals:
        return []
    top = max(totals.values())
    return sorted(p for p, score in totals.items() if score == top)
