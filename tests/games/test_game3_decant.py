"""T4.3 — DECANT: module-spec §8 suite + game-specific validation."""

from __future__ import annotations

from backend import config
from backend.games.game3_decant import (
    CAPACITY,
    MAIN_COLOURS,
    MAIN_LEVEL_PARAMS,
    MAIN_MIN_POURS,
    DecantGame,
    _colour_runs,
    _params_for_level,
    _pour,
    _solved,
)

game = DecantGame()


def solution_for(seed: int, kind: str = "main") -> str:
    _, solution = game._build(seed, kind)
    return ";".join(f"{src}>{dst}" for src, dst in solution)


def test_determinism():
    a, b = game.generate_main(42), game.generate_main(42)
    assert a.payload == b.payload


def test_different_seeds_differ():
    boards = {str(game.generate_main(seed).payload["tubes"]) for seed in range(20)}
    assert len(boards) > 1


def test_generated_boards_are_solvable():
    for seed in range(10):
        puzzle = game.generate_main(seed)
        assert game.check(puzzle, solution_for(seed)) is True


def test_holding_solvable_in_couple_of_pours():
    for seed in range(5):
        puzzle = game.generate_holding(seed)
        solution = solution_for(seed, "holding")
        assert puzzle.kind == "holding"
        assert len(solution.split(";")) <= 4
        assert game.check(puzzle, solution) is True


def test_main_boards_meet_difficulty_floor():
    # runs - colours is a hard lower bound on pours to solve; the generation
    # gate must guarantee it for every served main board.
    for seed in range(25):
        tubes = game.generate_main(seed).payload["tubes"]
        assert _colour_runs(tubes) - MAIN_COLOURS >= MAIN_MIN_POURS


def test_colour_runs():
    assert _colour_runs([[1, 1, 1, 1], []]) == 1
    assert _colour_runs([[1, 2, 1], [2, 2]]) == 4
    assert _colour_runs([[], []]) == 0


def test_boards_are_not_served_solved():
    for seed in range(10):
        tubes = game.generate_main(seed).payload["tubes"]
        assert not _solved([list(t) for t in tubes], CAPACITY)


def test_illegal_and_malformed_moves_fail():
    puzzle = game.generate_main(1)
    assert game.check(puzzle, "definitely-wrong") is False
    assert game.check(puzzle, "") is False
    assert game.check(puzzle, "0>0") is False  # src == dst
    assert game.check(puzzle, "0>99") is False  # bad index
    assert game.check(puzzle, "9>1") is False  # bad index
    # pour into a full tube is illegal even under free-stacking rules
    tubes = [list(t) for t in puzzle.payload["tubes"]]
    for src in range(len(tubes)):
        for dst in range(len(tubes)):
            if src != dst and tubes[src] and len(tubes[dst]) == CAPACITY:
                assert game.check(puzzle, f"{src}>{dst}") is False
                return


def test_mismatched_pour_is_legal_but_non_solving_sequence_fails():
    # Free-stacking: pouring onto a different colour is allowed, but a single
    # pour never solves a gated main board, so check still returns False.
    puzzle = game.generate_main(1)
    tubes = [list(t) for t in puzzle.payload["tubes"]]
    for src in range(len(tubes)):
        for dst in range(len(tubes)):
            if src != dst and tubes[src] and tubes[dst]:
                if tubes[dst][-1] != tubes[src][-1] and len(tubes[dst]) < CAPACITY:
                    clone = [list(t) for t in tubes]
                    assert _pour(clone, src, dst, CAPACITY) is True
                    assert game.check(puzzle, f"{src}>{dst}") is False
                    return


def test_incomplete_sequence_fails():
    seed = 2
    puzzle = game.generate_main(seed)
    moves = solution_for(seed).split(";")
    assert game.check(puzzle, ";".join(moves[:-1])) is False  # not sorted yet


def test_move_cap_enforced():
    puzzle = game.generate_main(3)
    # 61 legal-looking moves get rejected before replay
    assert game.check(puzzle, ";".join(["0>4"] * 61)) is False


def test_no_solution_in_payload():
    puzzle = game.generate_main(7)
    assert puzzle.answer == ""
    assert set(puzzle.public()["payload"]) == {
        "variant", "difficulty", "time_hint_seconds", "capacity", "tubes",
    }


def test_pour_rules():
    tubes = [[1, 1, 2, 2], [1], [], []]
    assert _pour(tubes, 0, 1, 4) is True  # one block, mismatched top is legal
    assert tubes[0] == [1, 1, 2] and tubes[1] == [1, 2]
    assert _pour(tubes, 0, 2, 4) is True  # one block to an empty tube
    assert tubes[0] == [1, 1] and tubes[2] == [2]
    assert _pour(tubes, 0, 1, 4) is True  # exactly one block moves, never a run
    assert tubes[0] == [1] and tubes[1] == [1, 2, 1]
    assert _pour(tubes, 2, 2, 4) is False  # src == dst
    full = [[1, 1, 2, 2], [3, 3, 3, 3], []]
    assert _pour(full, 0, 1, 4) is False  # destination full
    assert _pour(full, 2, 0, 4) is False  # source empty


def test_reset_safe_and_deterministic_after():
    before = game.generate_main(5).payload
    game.reset()
    assert game.generate_main(5).payload == before


def test_level_determinism():
    # V5 contract: same (seed, level) always yields the same puzzle.
    for level in (1, 5, 10, 13):
        a = game.generate_main(42, level=level)
        b = game.generate_main(42, level=level)
        assert a.payload == b.payload and a.answer == b.answer


def test_level_one_matches_original_board():
    assert _params_for_level(1) == {
        "colours": MAIN_COLOURS, "tubes": 6, "scramble": 20,
        "min_pours": MAIN_MIN_POURS, "difficulty": 3, "time_hint": 40,
    }
    assert game.generate_main(42, level=1).payload == game.generate_main(42).payload


def test_level_params_monotonic():
    for easier, harder in zip(MAIN_LEVEL_PARAMS, MAIN_LEVEL_PARAMS[1:]):
        for knob in ("colours", "tubes", "scramble", "min_pours",
                     "difficulty", "time_hint"):
            assert easier[knob] <= harder[knob]


def test_every_level_generates_solvable_scaled_boards():
    for level in range(1, 14):
        params = _params_for_level(level)
        for seed in (3, 44, 90):
            tubes, solution = game._build(seed, "main", level)
            assert len(tubes) == params["tubes"]
            colours = {block for tube in tubes for block in tube}
            assert len(colours) == params["colours"]
            puzzle = game.generate_main(seed, level=level)
            answer = ";".join(f"{src}>{dst}" for src, dst in solution)
            assert game.check(puzzle, answer) is True


def test_level_ten_visibly_harder():
    tubes, _ = game._build(42, "main", 10)
    params = _params_for_level(10)
    # The run-count lower bound must clear the level-10 floor (no fallback).
    assert _colour_runs(tubes) - params["colours"] >= params["min_pours"]
    assert params["min_pours"] > MAIN_MIN_POURS


def test_bonus_tiers_climb_past_level_ten():
    """Levels 11..13 are bonus-only: a team on the last level must still be
    offered something harder than the board they just cleared."""
    assert len(MAIN_LEVEL_PARAMS) == config.LEVEL_COUNT + config.BONUS_LEVEL_OFFSET
    top, tier = _params_for_level(10), _params_for_level(13)
    assert tier["scramble"] > top["scramble"]
    assert tier["min_pours"] > top["min_pours"]
    # More tubes would mean more free space and an EASIER board, so the tiers
    # must never buy difficulty that way.
    assert tier["tubes"] == top["tubes"]
