"""BULLSHIT QUIZ perks (backend/bsperks.py): the catalogue, the offers and
what each perk does to a locked round's scores.

Who may play what, and when, is the room's job and is tested in
tests/test_bsroom_perks.py.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from backend import bsperks, bsquiz, config
from backend.bsperks import Use

N = 12          # wagers 1..11, truthful reward 12 or 6


def panel3() -> bsquiz.Panel:
    """The design doc's worked example: Tara truthful on A, Ben bluffing on
    B, Cleo bluffing on C."""
    return bsquiz.panel_from("tara", [("real", "tara", True), ("fake one", "ben", False),
                                      ("fake two", "cleo", False)])


# --- the catalogue --------------------------------------------------------

def test_thirty_perks_with_unique_ids():
    assert len(bsperks.PERKS) == 30
    assert len(bsperks.PERK) == 30


def test_card_text_has_no_em_dashes():
    for perk in bsperks.PERKS:
        assert "—" not in perk.text and "—" not in perk.name, perk.id


def test_floor_and_redirectable_perks_exist():
    assert bsperks.FLOOR <= set(bsperks.PERK)
    assert bsperks.REDIRECTABLE <= set(bsperks.PERK)


def test_wager_perks_a_bluffer_could_play_are_secret():
    # The truthful player has no wager, so announcing one would out a liar.
    for pid in ("insurance_fraud", "double_or_nothing"):
        assert bsperks.PERK[pid].public is False


# --- the offers -----------------------------------------------------------

def test_small_games_are_never_offered_perks_they_cannot_play():
    three = {p.id for p in bsperks.available(3, 2)}
    assert "lone_wolf" not in three and "backup_plan" not in three
    assert "ride_or_die" not in three and "kingmaker" not in three
    assert "insurance_fraud" in three
    assert "blind_faith" not in {p.id for p in bsperks.available(8, 2)}
    assert len(bsperks.available(20, 4)) == 30


def test_offers_are_distinct_and_spread_evenly():
    pool = [p.id for p in bsperks.available(20, 4)]
    players = [f"p{i}" for i in range(20)]
    offers = bsperks.deal_offers(players, pool, random.Random(3))
    assert all(len(set(hand)) == config.BS_PERK_OFFERS for hand in offers.values())
    seen = Counter(card for hand in offers.values() for card in hand)
    assert max(seen.values()) - min(seen.values()) <= 2


def test_offers_from_a_tiny_pool():
    offers = bsperks.deal_offers(["a", "b"], ["x", "y"], random.Random(1))
    assert all(sorted(hand) == ["x", "y"] for hand in offers.values())


# --- forbidden words ------------------------------------------------------

@pytest.mark.parametrize("words, ok", [
    (["I", "remember", "because"], True),
    (["it's", "well", "basically"], True),
    (["one", "two"], False),
    (["one", "two", "two"], False),
    (["two words", "a", "b"], False),
    (["", "a", "b"], False),
    (["banana", "a", "b"], False),         # from their own answer
])
def test_forbidden_words(words, ok):
    assert (bsperks.words_problem(words, "A banana split") is None) is ok


# --- the worked example (docs/BULLSHIT_QUIZ_PERKS.md §5) -------------------

def test_the_worked_example():
    guesses = {
        "gus": ("A", 3), "hana": ("B", 6), "ivo": ("A", 9), "jas": ("C", 4),
        "kim": ("B", 2), "lou": ("A", 8), "max": ("B", 5), "ned": ("A", 10),
        "pia": ("B", 1),
    }
    bluffs = {"ben": 7, "cleo": 5}
    uses = [
        Use("silver_tongue", "tara"),
        Use("victory_lap", "ben"),
        Use("insurance_fraud", "cleo"),
        Use("double_or_nothing", "hana"),
        Use("blood_pact", "ivo", target="jas"),
        Use("backup_plan", "kim", option="A"),
        Use("doppelganger", "lou", target="ben"),
        Use("ride_or_die", "ned", target="ivo"),
    ]
    result = bsperks.score(N, panel3(), guesses, bluffs, uses, kingmaker="B")
    assert result.counts == {"A": 4, "B": 4, "C": 1}
    assert result.winner == "B"
    got = {p: line.delta for p, line in result.lines.items()}
    assert got == {"tara": 10, "ben": 11, "cleo": -2, "gus": 3, "hana": -12,
                   "ivo": -9, "jas": -4, "kim": 0, "lou": 11, "max": -5,
                   "ned": 15, "pia": -1}
    # Every perk shows as its own part, even one that paid nothing.
    assert ("Victory Lap", 0) in result.lines["ben"].parts
    assert ("Silver Tongue", 4) in result.lines["tara"].parts
    assert result.lines["cleo"].stake == -3


def test_without_a_kingmaker_the_tie_wins_nobody():
    guesses = {"g1": ("A", 1), "g2": ("B", 1)}
    plain = bsperks.score(N, panel3(), guesses, {"ben": 4, "cleo": 2}, [])
    assert plain.winner is None
    assert plain.lines["tara"].delta == N // 2


# --- one perk at a time ---------------------------------------------------

def two_option_round(guess: str = "A", wager: int = 8):
    panel = bsquiz.panel_from("t", [("real", "t", True), ("fake", "b", False)])
    return panel, {"g": (guess, wager), "h": ("B", 2), "i": ("A", 1)}, {"b": 5}


@pytest.mark.parametrize("guess, expected", [("A", 16), ("B", -16)])
def test_double_or_nothing(guess, expected):
    panel, guesses, bluffs = two_option_round(guess)
    result = bsperks.score(N, panel, guesses, bluffs, [Use("double_or_nothing", "g")])
    assert result.lines["g"].delta == expected


def test_double_or_nothing_leaves_people_fooled_alone():
    panel, guesses, bluffs = two_option_round()
    result = bsperks.score(N, panel, guesses, bluffs, [Use("double_or_nothing", "b")])
    # Ben fooled 1 and lost 5: 1 - 10.
    assert result.lines["b"].delta == 1 - 10


@pytest.mark.parametrize("wager, cost", [(7, 4), (8, 4), (1, 1)])
def test_insurance_fraud_halves_a_loss_rounded_up(wager, cost):
    panel, guesses, bluffs = two_option_round("B", wager)
    result = bsperks.score(N, panel, guesses, bluffs, [Use("insurance_fraud", "g")])
    assert result.lines["g"].delta == -cost


def test_insurance_fraud_leaves_a_win_alone():
    panel, guesses, bluffs = two_option_round("A", 7)
    result = bsperks.score(N, panel, guesses, bluffs, [Use("insurance_fraud", "g")])
    assert result.lines["g"].delta == 7


def test_blind_faith_pays_three_times_and_costs_once():
    panel, guesses, bluffs = two_option_round("A", 4)
    assert bsperks.score(N, panel, guesses, bluffs,
                         [Use("blind_faith", "g")]).lines["g"].delta == 12
    panel, guesses, bluffs = two_option_round("B", 4)
    assert bsperks.score(N, panel, guesses, bluffs,
                         [Use("blind_faith", "g")]).lines["g"].delta == -4


def test_backup_plan_only_rescues_a_wrong_main_with_a_right_backup():
    guesses = {"g": ("B", 6), "h": ("A", 1)}
    rescued = bsperks.score(N, panel3(), guesses, {"ben": 1, "cleo": 1},
                            [Use("backup_plan", "g", option="A")])
    assert rescued.lines["g"].delta == 0
    missed = bsperks.score(N, panel3(), guesses, {"ben": 1, "cleo": 1},
                           [Use("backup_plan", "g", option="C")])
    assert missed.lines["g"].delta == -6


def test_blood_pact_both_right_doubles_both():
    panel, guesses, bluffs = two_option_round("A", 8)
    result = bsperks.score(N, panel, guesses, bluffs, [Use("blood_pact", "g", target="i")])
    assert result.lines["g"].delta == 16 and result.lines["i"].delta == 2


def test_blood_pact_drags_the_right_one_down():
    panel, guesses, bluffs = two_option_round("A", 8)
    result = bsperks.score(N, panel, guesses, bluffs, [Use("blood_pact", "g", target="h")])
    assert result.lines["g"].delta == -8 and result.lines["h"].delta == -2


def test_blood_pact_with_somebody_who_never_voted_is_void():
    panel, guesses, bluffs = two_option_round("A", 8)
    result = bsperks.score(N, panel, guesses, bluffs, [Use("blood_pact", "g", target="z")])
    assert result.lines["g"].delta == 8


def test_a_pact_target_who_doubled_gets_both():
    panel, guesses, bluffs = two_option_round("A", 8)
    result = bsperks.score(N, panel, guesses, bluffs,
                           [Use("blood_pact", "i", target="g"), Use("double_or_nothing", "g")])
    assert result.lines["g"].delta == 32


@pytest.mark.parametrize("perk, target, expected", [
    ("ride_or_die", "i", 5), ("ride_or_die", "h", -5), ("ride_or_die", "nobody", 0),
    ("terrible_judgement", "h", 5), ("terrible_judgement", "i", -5),
    ("call_bullshit", "b", 5), ("call_bullshit", "t", -5),
])
def test_side_bets(perk, target, expected):
    panel, guesses, bluffs = two_option_round("A", 8)
    result = bsperks.score(N, panel, guesses, bluffs, [Use(perk, "g", target=target)])
    assert result.lines["g"].delta == 8 + expected


def test_lone_wolf_needs_to_be_the_only_one_right():
    guesses = {"g": ("A", 3), "h": ("B", 1), "i": ("C", 1)}
    alone = bsperks.score(N, panel3(), guesses, {"ben": 1, "cleo": 1}, [Use("lone_wolf", "g")])
    assert alone.lines["g"].delta == 13
    guesses["h"] = ("A", 1)
    company = bsperks.score(N, panel3(), guesses, {"ben": 1, "cleo": 1}, [Use("lone_wolf", "g")])
    assert company.lines["g"].delta == 3


def test_silver_tongue_pays_the_truthful_player_per_vote():
    panel, guesses, bluffs = two_option_round("A", 8)
    result = bsperks.score(N, panel, guesses, bluffs, [Use("silver_tongue", "t")])
    assert result.lines["t"].delta == N + 2


def test_victory_lap_and_honourable_mention():
    guesses = {"a": ("B", 1), "b": ("B", 1), "c": ("A", 1)}
    result = bsperks.score(N, panel3(), guesses, {"ben": 2, "cleo": 2},
                           [Use("victory_lap", "ben"), Use("honourable_mention", "tara")])
    assert ("Victory Lap", 10) in result.lines["ben"].parts
    assert ("Honourable Mention", 5) in result.lines["tara"].parts


def test_a_kingmaker_win_is_not_an_outright_first():
    guesses = {"a": ("B", 1), "b": ("A", 1)}
    result = bsperks.score(N, panel3(), guesses, {"ben": 2, "cleo": 2},
                           [Use("victory_lap", "ben")], kingmaker="B")
    assert result.winner == "B"
    assert ("Victory Lap", 0) in result.lines["ben"].parts
    assert result.lines["ben"].delta == 1 + 2


def test_target_audience():
    panel, guesses, bluffs = two_option_round("B", 8)
    result = bsperks.score(N, panel, guesses, bluffs, [Use("target_audience", "b", target="g")])
    assert ("Target Audience", 5) in result.lines["b"].parts


def test_doppelganger_takes_the_plain_score_and_ignores_their_perks():
    panel, guesses, bluffs = two_option_round("A", 8)
    result = bsperks.score(N, panel, guesses, bluffs,
                           [Use("doppelganger", "i", target="t"), Use("silver_tongue", "t")])
    assert result.lines["i"].delta == N           # Tara's plain score, no Silver Tongue
    assert result.lines["t"].delta == N + 2


def test_forbidden_words_fine_the_target_and_stop_at_the_cap():
    panel, guesses, bluffs = two_option_round("A", 8)
    result = bsperks.score(N, panel, guesses, bluffs,
                           [Use("forbidden_words", "g", target="b", slips=9)])
    cap = config.BS_FORBIDDEN_MAX_SLIPS * config.BS_PERK_POINTS["forbidden_words"]
    assert ("Forbidden Words", -cap) in result.lines["b"].parts


def test_a_side_bet_from_somebody_who_never_voted_still_settles():
    panel, guesses, bluffs = two_option_round("A", 8)
    result = bsperks.score(N, panel, guesses, bluffs, [Use("ride_or_die", "z", target="g")])
    assert result.lines["z"].delta == 5 and result.lines["z"].option is None


def test_damage_control_points():
    assert bsperks.damage_control_points(-9) == 5
    assert bsperks.damage_control_points(-3) == 3
    assert bsperks.damage_control_points(4) == 0
