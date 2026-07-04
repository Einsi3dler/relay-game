"""T1.3 — games/base.py contract: normalize_answer, PuzzleInstance, template conformance."""

from __future__ import annotations

from backend.games.base import GameModule, PuzzleInstance, normalize_answer
from backend.games.template import TemplateGame


# --- normalize_answer (spec §5) ---

def test_normalize_answer_case_and_whitespace():
    assert normalize_answer("  TRUE ") == "true"
    assert normalize_answer("True") == normalize_answer("true ")
    assert normalize_answer("a   b\tc") == "a b c"


def test_normalize_answer_slashes_and_non_strings():
    assert normalize_answer("yes/no") == "yes no"
    assert normalize_answer(42) == "42"
    assert normalize_answer(None) == "none"


# --- template type-checks and behaves against the Protocol (T1.3 AC) ---

def test_template_satisfies_protocol():
    module: GameModule = TemplateGame()  # statically checkable assignment
    assert isinstance(module.id, str) and module.id == "template_game"
    assert isinstance(module.name, str)
    for attr in ("generate_main", "generate_holding", "check", "reset"):
        assert callable(getattr(module, attr))


def test_template_generation_is_deterministic_in_seed():
    game = TemplateGame()
    a, b = game.generate_main(123), game.generate_main(123)
    assert (a.prompt, a.answer) == (b.prompt, b.answer)
    assert a.id != b.id  # instance ids are always fresh
    assert a.kind == "main" and a.game_id == "template_game"
    h = game.generate_holding(5)
    assert h.kind == "holding"


def test_template_check_accepts_normalised_answers():
    game = TemplateGame()
    puzzle = game.generate_main(7)
    assert game.check(puzzle, f"  {puzzle.answer} ") is True
    assert game.check(puzzle, "definitely wrong") is False


def test_puzzle_public_has_no_answer():
    puzzle = TemplateGame().generate_main(1)
    public = puzzle.public()
    assert "answer" not in public
    assert set(public) == {"id", "game_id", "kind", "prompt", "payload"}
