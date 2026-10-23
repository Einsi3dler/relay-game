"""BULLSHIT QUIZ rules (backend/bsquiz.py), checked against the handoff.

The three worked examples from docs/BULLSHIT_QUIZ.md §10 are reproduced
number for number. If one of these fails, the formula is wrong, not the test.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from backend import bsquiz
from backend.bsquiz import (BULLSHITTER, GUESSER, TRUTHFUL, Option, Panel,
                            Question)


def panel(author: str, truth_key: str, liars: dict[str, str]) -> Panel:
    """A hand-built panel: `liars` maps option key to its bullshitter."""
    options = [Option(key=truth_key, text="real", speaker=author, correct=True)]
    options += [Option(key=k, text=f"fake {k}", speaker=who, correct=False)
                for k, who in liars.items()]
    return Panel(author=author, options=tuple(sorted(options, key=lambda o: o.key)))


def guesses_for(counts: dict[str, int], wager: int = 9) -> dict[str, tuple[str, int]]:
    out: dict[str, tuple[str, int]] = {}
    for key, how_many in counts.items():
        for i in range(how_many):
            out[f"g_{key}_{i}"] = (key, wager)
    return out


# --- §10, the worked examples ---------------------------------------------

def test_example_a_a_bullshitter_wins_the_crowd():
    n = 20
    p = panel("truth", "T", {"A": "liar_a", "B": "liar_b", "C": "liar_c"})
    guesses = guesses_for({"T": 6, "A": 7, "B": 2, "C": 1})
    assert len(guesses) == 16 == n - 1 - 3

    result = bsquiz.score_round(n, p, guesses,
                                {"liar_a": 8, "liar_b": 4, "liar_c": 1})

    assert result.winner == "A"
    assert result.lines["truth"].delta == 10
    assert result.lines["liar_a"].delta == 15
    assert result.lines["liar_b"].delta == -2
    assert result.lines["liar_c"].delta == 0
    assert result.lines["g_T_0"].delta == 9
    assert result.lines["g_A_0"].delta == -9
    assert result.lines["g_C_0"].delta == -9


def test_example_b_the_truth_wins_without_a_majority():
    n = 15
    p = panel("truth", "T", {"A": "liar_a", "B": "liar_b"})
    guesses = guesses_for({"T": 5, "A": 4, "B": 3})
    assert len(guesses) == 12

    result = bsquiz.score_round(n, p, guesses, {"liar_a": 6, "liar_b": 2})

    assert result.winner == "T"
    assert result.lines["truth"].delta == 15
    assert result.lines["liar_a"].delta == -2
    assert result.lines["liar_b"].delta == 1


@pytest.mark.parametrize("n, win, lose, cap", [
    (21, 21, 10, 20),
    (20, 20, 10, 19),
    (15, 15, 7, 14),
])
def test_example_c_odd_counts_round_down(n, win, lose, cap):
    assert bsquiz.truthful_reward(n, True) == win
    assert bsquiz.truthful_reward(n, False) == lose
    assert max(bsquiz.wager_values(n)) == cap


# --- crowd victory --------------------------------------------------------

def test_crowd_is_headcount_not_wager_size():
    p = panel("truth", "T", {"A": "liar"})
    guesses = {"g1": ("T", 1), "g2": ("T", 1), "g3": ("A", 19)}
    result = bsquiz.score_round(20, p, guesses, {"liar": 5})
    assert result.counts == {"A": 1, "T": 2}
    assert result.winner == "T"


def test_a_tie_at_the_top_means_nobody_wins_the_crowd():
    n = 10
    p = panel("truth", "T", {"A": "liar_a", "B": "liar_b"})
    guesses = {"g1": ("T", 3), "g2": ("T", 3), "g3": ("A", 4),
               "g4": ("A", 4), "g5": ("B", 2)}
    result = bsquiz.score_round(n, p, guesses, {"liar_a": 6, "liar_b": 1})

    assert result.winner is None
    assert result.lines["truth"].delta == n // 2
    # Tied for the lead still loses the wager, but keeps the fooled points.
    assert result.lines["liar_a"].delta == 2 - 6
    assert result.lines["liar_b"].delta == 1 - 1
    # Guessers are scored on the correct answer, tie or not.
    assert result.lines["g1"].delta == 3
    assert result.lines["g3"].delta == -4


def test_nobody_voting_is_a_tie_too():
    assert bsquiz.crowd_winner({"A": 0, "B": 0}) is None


def test_crowd_winner_does_not_need_a_majority():
    assert bsquiz.crowd_winner({"A": 3, "B": 2, "C": 2, "D": 2}) == "A"


def test_a_bullshitter_gets_no_truthful_bonus_for_winning():
    p = panel("truth", "T", {"A": "liar"})
    result = bsquiz.score_round(20, p, {"g1": ("A", 1)}, {"liar": 3})
    assert result.lines["liar"].delta == 1 + 3
    assert result.lines["liar"].fooled == 1


def test_the_truthful_player_gets_no_per_voter_points():
    p = panel("truth", "T", {"A": "liar"})
    many = guesses_for({"T": 8, "A": 1})
    result = bsquiz.score_round(12, p, many, {"liar": 2})
    assert result.lines["truth"].delta == 12
    assert result.lines["truth"].wager is None


def test_lines_carry_roles():
    p = panel("truth", "T", {"A": "liar"})
    result = bsquiz.score_round(5, p, {"g1": ("T", 2)}, {"liar": 2})
    assert result.lines["truth"].role == TRUTHFUL
    assert result.lines["liar"].role == BULLSHITTER
    assert result.lines["g1"].role == GUESSER


# --- wagers ---------------------------------------------------------------

def test_wager_range_uses_total_players():
    assert list(bsquiz.wager_values(5)) == [1, 2, 3, 4]


FULL = list(range(1, 15))


@pytest.mark.parametrize("wager, left, ok", [
    (1, FULL, True),
    (14, FULL, True),
    (0, FULL, False),
    (15, FULL, False),
    (7, [6, 8], False),
    (7, [6, 7, 8], True),
    (19, [19, 19], True),           # two 19s after a draft trade
    (3.0, FULL, False),
    (True, FULL, False),
    ("3", FULL, False),
])
def test_wager_problem(wager, left, ok):
    assert (bsquiz.wager_problem(wager, left) is None) is ok


def test_single_use_wagers_last_exactly_the_game():
    """N-1 wagering turns and N-1 cards: everybody spends each once."""
    n = 6
    left = list(bsquiz.wager_values(n))
    for w in bsquiz.wager_values(n):
        assert bsquiz.wager_problem(w, left) is None
        left.remove(w)
    assert left == []
    assert all(bsquiz.wager_problem(w, left) for w in range(1, n))


# --- sizes and dealing ----------------------------------------------------

@pytest.mark.parametrize("n, expected", [(2, 0), (3, 1), (4, 2), (5, 3), (30, 3)])
def test_max_bullshitters_leaves_a_guesser(n, expected):
    assert bsquiz.max_bullshitters(n) == expected


def test_option_count_stays_in_range():
    rng = random.Random(1)
    assert {bsquiz.option_count_for(3, rng) for _ in range(50)} == {2}
    assert {bsquiz.option_count_for(20, rng) for _ in range(200)} == {2, 3, 4}
    with pytest.raises(ValueError):
        bsquiz.option_count_for(2, rng)


def test_schedule_uses_every_author_once():
    authors = [f"p{i}" for i in range(12)]
    order = bsquiz.schedule(authors, random.Random(7))
    assert sorted(order) == sorted(authors)


def question(author="p0", decoys=("d1", "d2", "d3")) -> Question:
    return Question(author=author, prompt="?", answer="real", decoys=decoys)


def deal(q, players, rng):
    """Deal the way a fully online room would."""
    b = bsquiz.panel_size(len(q.decoys), len(players) - 1, len(players) - 1)
    return bsquiz.deal_panel(q, players, b, rng)


def test_deal_panel_shape():
    players = [f"p{i}" for i in range(8)]
    p = deal(question(), players, random.Random(3))

    assert len(p.options) == 4
    assert [o.key for o in p.options] == ["A", "B", "C", "D"]
    assert sum(o.correct for o in p.options) == 1
    assert p.option_of("p0").correct
    assert "p0" not in p.bullshitters
    assert len(set(p.speakers)) == 4
    assert len({o.text for o in p.options}) == 4
    assert p.role_of("p0") == TRUTHFUL
    guessers = [x for x in players if p.role_of(x) == GUESSER]
    assert len(guessers) == len(players) - 1 - 3


def test_deal_panel_caps_bullshitters_to_the_room():
    """Four players leave room for two bullshitters, even with three decoys."""
    p = deal(question(), ["p0", "p1", "p2", "p3"], random.Random(1))
    assert len(p.bullshitters) == 2


def test_deal_panel_moves_the_truth_around():
    players = [f"p{i}" for i in range(10)]
    rng = random.Random(11)
    keys = Counter(deal(question(), players, rng).correct_key
                   for _ in range(400))
    assert set(keys) == {"A", "B", "C", "D"}


def test_bluffing_can_repeat_and_is_not_equalised():
    """Over a game some players bluff twice and some never (handoff §3)."""
    players = [f"p{i}" for i in range(6)]
    rng = random.Random(5)
    repeated = never = False
    for _ in range(50):
        appearances = Counter()
        for author in players:
            for liar in deal(question(author, ("x",)), players, rng).bullshitters:
                appearances[liar] += 1
        repeated |= max(appearances.values()) > 1
        never |= len(appearances) < len(players)
    assert repeated and never


def test_winners_are_joint_on_a_tie():
    assert bsquiz.winners({"a": 5, "b": 9, "c": 9}) == ["b", "c"]
    assert bsquiz.winners({}) == []


@pytest.mark.parametrize("decoys, candidates, online_others, expected", [
    (3, 10, 10, 3),
    (3, 1, 10, 1),      # only one player may bluff
    (3, 10, 2, 1),      # one online guesser must remain
    (1, 10, 10, 1),
    (3, 0, 10, 0),
    (3, 10, 1, 0),      # nobody left to guess
])
def test_panel_size(decoys, candidates, online_others, expected):
    assert bsquiz.panel_size(decoys, candidates, online_others) == expected


def test_deal_panel_only_picks_candidates():
    q = question("p0")
    rng = random.Random(8)
    for _ in range(50):
        p = bsquiz.deal_panel(q, ["p0", "p2", "p5"], 2, rng)
        assert set(p.bullshitters) == {"p2", "p5"}
    with pytest.raises(ValueError):
        bsquiz.deal_panel(q, ["p0", "p2"], 2, rng)
