"""T1.4 — GameRegistry stage resolution and reset_all."""

from __future__ import annotations

import pytest

from backend import config
from backend.games.base import PuzzleInstance
from backend.registry import GameRegistry


class FakeGame:
    def __init__(self, game_id: str) -> None:
        self.id = game_id
        self.name = game_id.title()
        self.reset_calls = 0

    def generate_main(self, seed: int) -> PuzzleInstance:
        return PuzzleInstance(game_id=self.id, kind="main", prompt="?", answer="a")

    def generate_holding(self, seed: int) -> PuzzleInstance:
        return PuzzleInstance(game_id=self.id, kind="holding", prompt="?", answer="a")

    def check(self, puzzle: PuzzleInstance, answer: str) -> bool:
        return True

    def reset(self) -> None:
        self.reset_calls += 1


ORDER = ["rewire", "mirror_run", "decant", "echo"]


def make_registry() -> tuple[GameRegistry, list[FakeGame]]:
    games = [FakeGame(game_id) for game_id in ORDER]
    return GameRegistry(modules=games, game_order=ORDER), games


def test_for_stage_returns_right_module_per_stage():
    registry, _ = make_registry()
    for stage, expected_id in enumerate(ORDER, start=1):
        assert registry.for_stage(stage).id == expected_id


def test_for_stage_out_of_range_raises():
    registry, _ = make_registry()
    with pytest.raises(ValueError):
        registry.for_stage(5)
    with pytest.raises(ValueError):
        registry.for_stage(0)


def test_unregistered_game_id_raises_keyerror():
    registry = GameRegistry(modules=[], game_order=["ghost_game"])
    with pytest.raises(KeyError):
        registry.for_stage(1)


def test_reset_all_resets_every_module():
    registry, games = make_registry()
    registry.reset_all()
    assert [game.reset_calls for game in games] == [1, 1, 1, 1]


def test_defaults_come_from_config():
    registry = GameRegistry()  # real games registered (T4.x.3); order from config
    assert registry._order == config.GAME_ORDER
    for stage, game_id in enumerate(config.GAME_ORDER, start=1):
        assert registry.for_stage(stage).id == game_id
