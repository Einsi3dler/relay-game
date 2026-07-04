"""T4.1 — REWIRE: module-spec §8 suite + game-specific validation."""

from __future__ import annotations

import json
import random

from backend.games.game1_rewire import (
    MAIN_COLS, MAIN_ROWS, RewireGame, _build_board, open_edges,
)

game = RewireGame()


def reference_solution(seed: int) -> str:
    """Rebuild the board the way generate_main(seed) does to get the solution."""
    board = _build_board(random.Random(seed), MAIN_ROWS, MAIN_COLS, 2)
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
