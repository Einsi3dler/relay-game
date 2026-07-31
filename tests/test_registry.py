"""GameRegistry — id-indexed library resolution and reset_all (v2)."""

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

    def generate_main(self, seed: int, level: int = 1) -> PuzzleInstance:
        return PuzzleInstance(game_id=self.id, kind="main", prompt="?", answer="a")

    def generate_holding(self, seed: int) -> PuzzleInstance:
        return PuzzleInstance(game_id=self.id, kind="holding", prompt="?", answer="a")

    def check(self, puzzle: PuzzleInstance, answer: str) -> bool:
        return True

    def reset(self) -> None:
        self.reset_calls += 1


IDS = ["rewire", "sweep", "mirror_run", "decant", "echo"]


def make_registry() -> tuple[GameRegistry, list[FakeGame]]:
    games = [FakeGame(game_id) for game_id in IDS]
    return GameRegistry(modules=games), games


def test_by_id_returns_module_and_raises_on_unknown():
    registry, _ = make_registry()
    for game_id in IDS:
        assert registry.by_id(game_id).id == game_id
    with pytest.raises(KeyError):
        registry.by_id("ghost_game")


def test_has():
    registry, _ = make_registry()
    assert registry.has("rewire")
    assert not registry.has("ghost_game")


def test_library_lists_all_games_with_specialist_roles():
    registry = GameRegistry()  # real games + config.ROLES
    library = registry.library()
    specialist_ids = {
        game_id
        for role in config.ROLES.values()
        if role["games"]
        for game_id in role["games"]
    }
    # Every registered game belongs to exactly one specialist role; catch-all
    # (games=None) and reserved (games=[]) roles never claim a game.
    assert {entry["id"] for entry in library} == specialist_ids
    roles = {entry["id"]: entry["role"] for entry in library}
    for role_id, role in config.ROLES.items():
        for game_id in role["games"] or []:
            assert roles[game_id] == role_id
    assert "generalist" not in roles.values()
    assert "lexicon" not in roles.values()
    for entry in library:
        assert entry["name"]


def test_reset_all_resets_every_module():
    registry, games = make_registry()
    registry.reset_all()
    assert [game.reset_calls for game in games] == [1] * len(IDS)


def test_defaults_are_the_real_modules():
    registry = GameRegistry()
    for game_id in ("rewire", "sweep", "mirror_run", "decant", "echo", "overprint",
                    "stackdrop"):
        assert registry.by_id(game_id).id == game_id
