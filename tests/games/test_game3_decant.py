"""T4.3 — DECANT: module-spec §8 suite + game-specific validation."""

from __future__ import annotations

from backend.games.game3_decant import (
    CAPACITY,
    MAIN_MIN_POURS,
    DecantGame,
    _min_pours,
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
    # The generation gate must reject boards solvable in < MAIN_MIN_POURS pours.
    for seed in range(15):
        tubes = [list(t) for t in game.generate_main(seed).payload["tubes"]]
        assert _min_pours(tubes, CAPACITY, MAIN_MIN_POURS - 1) is None


def test_min_pours_solver():
    assert _min_pours([[1, 1, 1, 1], []], 4, 3) == 0  # already solved
    assert _min_pours([[1, 1, 1], [1]], 4, 3) == 1
    assert _min_pours([[1, 1, 2, 2], [2, 2], [1, 1]], 4, 3) == 2
    assert _min_pours([[1, 2], [2, 1], []], 2, 2) is None  # needs 3 > cap


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
    # pour onto a mismatched colour: find one and try it
    tubes = [list(t) for t in puzzle.payload["tubes"]]
    for src in range(len(tubes)):
        for dst in range(len(tubes)):
            if src != dst and tubes[src] and tubes[dst]:
                if tubes[dst][-1] != tubes[src][-1] and len(tubes[dst]) < CAPACITY:
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
    assert _pour(tubes, 0, 1, 4) is False  # 2 onto 1 mismatch
    assert _pour(tubes, 0, 2, 4) is True  # run of two 2s to empty
    assert tubes[0] == [1, 1] and tubes[2] == [2, 2]
    assert _pour(tubes, 0, 1, 4) is True  # 1s onto 1
    assert tubes[1] == [1, 1, 1] and tubes[0] == []


def test_reset_safe_and_deterministic_after():
    before = game.generate_main(5).payload
    game.reset()
    assert game.generate_main(5).payload == before
