"""T4.2 — SWEEP: module-spec §8 suite + game-specific validation."""

from __future__ import annotations

import json

from backend.games.game2_sweep import SweepGame

game = SweepGame()


def mines_of(puzzle) -> set[tuple[int, int]]:
    return {
        (int(r), int(c))
        for r, c in (pair.split(",") for pair in puzzle.answer.split(";"))
    }


def test_determinism():
    a, b = game.generate_main(42), game.generate_main(42)
    assert a.payload == b.payload and a.answer == b.answer


def test_different_seeds_differ():
    answers = {game.generate_main(seed).answer for seed in range(20)}
    assert len(answers) > 1


def test_correct_flag_set_passes_any_order_and_spacing():
    puzzle = game.generate_main(3)
    mines = sorted(mines_of(puzzle), reverse=True)  # different order than answer
    encoded = " ; ".join(f"{r} , {c}" for r, c in mines)
    assert game.check(puzzle, encoded) is True


def test_wrong_answers_fail():
    puzzle = game.generate_main(1)
    mines = sorted(mines_of(puzzle))
    assert game.check(puzzle, "definitely-wrong") is False
    assert game.check(puzzle, "BOOM") is False  # revealed a mine
    assert game.check(puzzle, "") is False
    # missing one mine
    partial = ";".join(f"{r},{c}" for r, c in mines[:-1])
    assert game.check(puzzle, partial) is False
    # over-flagging a safe cell on top of all mines
    safe = next(
        (r, c) for r in range(6) for c in range(6) if (r, c) not in set(mines)
    )
    over = ";".join(f"{r},{c}" for r, c in mines + [safe])
    assert game.check(puzzle, over) is False
    # out-of-range coordinate
    assert game.check(puzzle, "99,99") is False


def test_boards_are_deducible_without_guessing():
    from backend.games.game2_sweep import _counts, _deducible

    for seed in range(8):
        puzzle = game.generate_main(seed)
        rows, cols = puzzle.payload["rows"], puzzle.payload["cols"]
        mines = mines_of(puzzle)
        counts = _counts(rows, cols, mines)
        opening = {(cell["r"], cell["c"]) for cell in puzzle.payload["revealed"]}
        assert _deducible(rows, cols, mines, counts, opening) is True


def test_payload_shape_and_documented_clues_exception():
    puzzle = game.generate_main(5)
    payload = puzzle.public()["payload"]
    mines = mines_of(puzzle)
    clue_cells = {(cell["r"], cell["c"]) for cell in payload["clues"]}
    # Documented exception: clues cover exactly the safe cells (mines are the
    # complement) — but the mine list itself is never in the payload.
    assert clue_cells == {
        (r, c) for r in range(payload["rows"]) for c in range(payload["cols"])
    } - mines
    assert puzzle.answer not in json.dumps(payload)
    revealed = {(cell["r"], cell["c"]) for cell in payload["revealed"]}
    assert revealed <= clue_cells  # opening reveal is safe cells only
    assert payload["mine_count"] == len(mines)


def test_holding_is_small_and_solvable():
    for seed in range(5):
        puzzle = game.generate_holding(seed)
        assert puzzle.kind == "holding"
        assert puzzle.payload["rows"] == 3 and puzzle.payload["cols"] == 3
        assert game.check(puzzle, puzzle.answer) is True


def test_reset_safe_and_deterministic_after():
    before = game.generate_main(9).answer
    game.reset()
    assert game.generate_main(9).answer == before
