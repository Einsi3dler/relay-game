"""T4.3 — DECANT: module-spec §8 suite + game-specific validation."""

from __future__ import annotations

from backend.games.game3_decant import (
    CAPACITY,
    MAIN_COLOURS,
    MAIN_MIN_POURS,
    DecantGame,
    _colour_runs,
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
    assert _pour(tubes, 0, 1, 4) is True  # free-stacking: 2s onto 1 is legal
    assert tubes[0] == [1, 1] and tubes[1] == [1, 2, 2]
    assert _pour(tubes, 1, 2, 4) is True  # run of two 2s to empty
    assert tubes[1] == [1] and tubes[2] == [2, 2]
    assert _pour(tubes, 1, 0, 4) is True  # 1 onto 1s merges
    assert tubes[0] == [1, 1, 1] and tubes[1] == []
    assert _pour(tubes, 2, 2, 4) is False  # src == dst
    full = [[1, 1, 2, 2], [3, 3, 3, 3], []]
    assert _pour(full, 0, 1, 4) is False  # destination full
    assert _pour(full, 2, 0, 4) is False  # source empty
    # a pour bigger than the room pours only what fits
    tight = [[1, 2, 2, 2], [3, 3, 3], []]
    assert _pour(tight, 0, 1, 4) is True
    assert tight[0] == [1, 2, 2] and tight[1] == [3, 3, 3, 2]


def test_reset_safe_and_deterministic_after():
    before = game.generate_main(5).payload
    game.reset()
    assert game.generate_main(5).payload == before
