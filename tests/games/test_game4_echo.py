"""T4.4 — ECHO: module-spec §8 suite + game-specific validation."""

from __future__ import annotations

from backend.games.game4_echo import EchoGame

game = EchoGame()


def test_determinism():
    a, b = game.generate_main(42), game.generate_main(42)
    assert a.payload == b.payload and a.answer == b.answer


def test_different_seeds_differ():
    sequences = {game.generate_main(seed).answer for seed in range(20)}
    assert len(sequences) > 1


def test_correct_taps_pass_with_spacing():
    puzzle = game.generate_main(3)
    assert game.check(puzzle, puzzle.answer) is True
    spaced = " , ".join(puzzle.answer.split(","))
    assert game.check(puzzle, f"  {spaced} ") is True


def test_wrong_partial_and_empty_taps_fail():
    puzzle = game.generate_main(1)
    sequence = puzzle.answer.split(",")
    assert game.check(puzzle, "definitely-wrong") is False
    assert game.check(puzzle, ",".join(sequence[:-1])) is False  # too short
    assert game.check(puzzle, ",".join(sequence + ["0"])) is False  # too long
    wrong = list(sequence)
    wrong[0] = str((int(wrong[0]) + 1) % puzzle.payload["pads"])
    assert game.check(puzzle, ",".join(wrong)) is False
    assert game.check(puzzle, "") is False


def test_shapes_main_and_holding():
    main = game.generate_main(5)
    assert main.payload["pads"] == 9 and len(main.payload["sequence"]) == 5
    holding = game.generate_holding(5)
    assert holding.kind == "holding"
    assert holding.payload["pads"] == 4 and len(holding.payload["sequence"]) == 3
    assert all(0 <= pad < 4 for pad in holding.payload["sequence"])
    assert {"flash_ms", "gap_ms"} <= set(main.payload)


def test_sequence_is_the_documented_exception():
    # The payload sequence IS the answer (must be animated); assert the
    # documented shape instead of no-leak.
    puzzle = game.generate_main(7)
    payload = puzzle.public()["payload"]
    assert ",".join(str(p) for p in payload["sequence"]) == puzzle.answer


def test_reset_safe_and_deterministic_after():
    before = game.generate_main(9).answer
    game.reset()
    assert game.generate_main(9).answer == before
