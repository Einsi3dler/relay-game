"""T4.1 — REWIRE: module-spec §8 suite + game-specific validation."""

from __future__ import annotations

import json
import random

from backend.games.game1_rewire import (
    MAIN_COLS, MAIN_LEVEL_PARAMS, MAIN_ROWS, RewireGame, _build_board,
    _params_for_level, open_edges,
)

game = RewireGame()


def reference_solution(seed: int, level: int = 1) -> str:
    """Rebuild the board the way generate_main(seed) does to get the solution."""
    params = _params_for_level(level)
    board = _build_board(
        random.Random(seed), params["rows"], params["cols"], params["sinks"]
    )
    return ",".join(str(orient) for orient in board.solution)


def test_determinism():
    a, b = game.generate_main(42), game.generate_main(42)
    assert a.prompt == b.prompt and a.payload == b.payload and a.answer == b.answer


def test_different_seeds_differ():
    payloads = {json.dumps(game.generate_main(seed).payload) for seed in range(20)}
    assert len(payloads) > 1


def test_generated_board_is_solvable():
    for seed in range(10):
        puzzle = game.generate_main(seed)
        assert game.check(puzzle, reference_solution(seed)) is True


def test_holding_board_is_solvable():
    for seed in range(5):
        puzzle = game.generate_holding(seed)
        board = _build_board(random.Random(seed), 2, 2, 1)
        solution = ",".join(str(orient) for orient in board.solution)
        assert puzzle.kind == "holding"
        assert game.check(puzzle, solution) is True


def test_wrong_and_malformed_answers_fail():
    puzzle = game.generate_main(1)
    tiles = puzzle.payload["rows"] * puzzle.payload["cols"]
    assert game.check(puzzle, "definitely-wrong") is False
    assert game.check(puzzle, ",".join("9" for _ in range(tiles))) is False
    assert game.check(puzzle, "0,1,2") is False  # wrong length
    assert game.check(puzzle, "") is False


def test_scrambled_board_not_served_solved():
    for seed in range(10):
        puzzle = game.generate_main(seed)
        scrambled = ",".join(str(t["orient"]) for t in puzzle.payload["tiles"])
        assert game.check(puzzle, scrambled) is False


def test_no_solution_in_payload():
    # answer is empty (recomputed) and the payload holds only scrambled orients.
    puzzle = game.generate_main(7)
    assert puzzle.answer == ""
    public = puzzle.public()
    assert set(public["payload"]) == {
        "variant", "difficulty", "time_hint_seconds", "rows", "cols",
        "tiles", "source", "sinks",
    }


def test_powered_edges_must_all_mate():
    # A lone live edge pointing off-grid or into a closed face must fail even
    # if the sinks happen to be reached — build a tiny known case by hand.
    puzzle = game.generate_holding(3)
    rows, cols = puzzle.payload["rows"], puzzle.payload["cols"]
    board = _build_board(random.Random(3), rows, cols, 1)
    solution = list(board.solution)
    # Rotate a tile that is NOT on the source→sink path if one exists; any
    # single rotation of a tree board breaks the all-edges-mate rule.
    for i in range(len(solution)):
        broken = list(solution)
        broken[i] = (broken[i] + 1) % 4
        assert game.check(puzzle, ",".join(map(str, broken))) is False


def test_reset_safe_and_deterministic_after():
    before = game.generate_main(5).payload
    game.reset()
    assert game.generate_main(5).payload == before


def test_shape_edge_rotation_math():
    assert open_edges("straight", 1) == {1, 3}
    assert open_edges("elbow", 2) == {2, 3}
    assert open_edges("tee", 3) == {3, 0, 1}
    assert open_edges("end", 2) == {2}


def test_level_determinism():
    # V5 contract: same (seed, level) always yields the same puzzle.
    for level in (1, 5, 10):
        a = game.generate_main(42, level=level)
        b = game.generate_main(42, level=level)
        assert a.payload == b.payload and a.answer == b.answer


def test_level_one_matches_original_board():
    assert _params_for_level(1) == {
        "rows": MAIN_ROWS, "cols": MAIN_COLS, "sinks": 2,
        "difficulty": 2, "time_hint": 35,
    }
    assert game.generate_main(42, level=1).payload == game.generate_main(42).payload


def test_level_params_monotonic():
    for easier, harder in zip(MAIN_LEVEL_PARAMS, MAIN_LEVEL_PARAMS[1:]):
        assert easier["rows"] * easier["cols"] <= harder["rows"] * harder["cols"]
        assert easier["sinks"] <= harder["sinks"]
        assert easier["difficulty"] <= harder["difficulty"]
        assert easier["time_hint"] <= harder["time_hint"]


def test_every_level_generates_solvable_scaled_boards():
    for level in range(1, 11):
        params = _params_for_level(level)
        for seed in (3, 44, 90):
            puzzle = game.generate_main(seed, level=level)
            payload = puzzle.payload
            assert payload["rows"] == params["rows"]
            assert payload["cols"] == params["cols"]
            assert 1 <= len(payload["sinks"]) <= params["sinks"]
            assert game.check(puzzle, reference_solution(seed, level)) is True


def test_level_ten_visibly_harder():
    top = game.generate_main(42, level=10).payload
    base = game.generate_main(42, level=1).payload
    assert top["rows"] * top["cols"] > base["rows"] * base["cols"]
    assert len(top["sinks"]) > len(base["sinks"])
