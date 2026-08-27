"""T4.4 — ECHO: module-spec §8 suite + game-specific validation."""

from __future__ import annotations

from backend import config
from backend.games.game4_echo import (
    MAIN_LENGTH, MAIN_LEVEL_PARAMS, MAIN_PADS, EchoGame, _params_for_level,
)

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


def test_level_determinism():
    # V5 contract: same (seed, level) always yields the same puzzle.
    for level in (1, 5, 10, 13):
        a = game.generate_main(42, level=level)
        b = game.generate_main(42, level=level)
        assert a.payload == b.payload and a.answer == b.answer


def test_level_one_matches_original_sequence():
    assert _params_for_level(1) == {
        "length": MAIN_LENGTH, "flash_ms": 450, "gap_ms": 250,
        "difficulty": 2, "time_hint": 20,
    }
    assert game.generate_main(42, level=1).payload == game.generate_main(42).payload


def test_level_params_monotonic():
    for easier, harder in zip(MAIN_LEVEL_PARAMS, MAIN_LEVEL_PARAMS[1:]):
        assert easier["length"] <= harder["length"]
        assert easier["flash_ms"] >= harder["flash_ms"]  # faster = harder
        assert easier["gap_ms"] >= harder["gap_ms"]
        assert easier["difficulty"] <= harder["difficulty"]
        assert easier["time_hint"] <= harder["time_hint"]


def test_every_level_generates_scaled_sequences():
    for level in range(1, 14):
        params = _params_for_level(level)
        for seed in (3, 44, 90):
            puzzle = game.generate_main(seed, level=level)
            payload = puzzle.payload
            assert payload["pads"] == MAIN_PADS  # renderer grid stays 3x3
            assert len(payload["sequence"]) == params["length"]
            assert payload["flash_ms"] == params["flash_ms"]
            assert game.check(puzzle, puzzle.answer) is True


def test_level_ten_visibly_harder():
    top = game.generate_main(42, level=10).payload
    base = game.generate_main(42, level=1).payload
    assert len(top["sequence"]) == 9 and len(base["sequence"]) == 5
    assert top["flash_ms"] < base["flash_ms"]


def test_bonus_tiers_climb_past_level_ten():
    """Levels 11..13 are bonus-only: a team on the last level must still be
    offered something harder than the board they just cleared."""
    assert len(MAIN_LEVEL_PARAMS) == config.LEVEL_COUNT + config.BONUS_LEVEL_OFFSET
    top, tier = _params_for_level(10), _params_for_level(13)
    assert tier["length"] > top["length"]
    assert tier["flash_ms"] < top["flash_ms"]
