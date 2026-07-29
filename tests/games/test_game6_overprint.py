"""T4.x — OVERPRINT: module-spec §8 suite + the expansion spec's minimum
acceptance tests (game/RELAY_EXPANSION_GAMES_README.md §4)."""

from __future__ import annotations

import json

from backend.games.game6_overprint import (
    MAX_SOLUTIONS,
    OverprintGame,
    _count_solutions,
    _placed_cells,
    _transform,
)

game = OverprintGame()


def target_cells(payload: dict) -> frozenset:
    return frozenset(
        (r, c)
        for r, row in enumerate(payload["target"])
        for c, mark in enumerate(row)
        if mark == "1"
    )


def composite(payload: dict, placements: list[dict]) -> frozenset:
    layers = {layer["id"]: layer for layer in payload["layers"]}
    cells: frozenset = frozenset()
    for placement in placements:
        cells |= _placed_cells(layers[placement["id"]], placement)
    return cells


def test_determinism():
    a, b = game.generate_main(42), game.generate_main(42)
    assert a.payload == b.payload
    assert a.answer == b.answer


def test_different_seeds_differ():
    targets = {str(game.generate_main(seed).payload["target"]) for seed in range(15)}
    assert len(targets) > 1


def test_reference_solution_passes():
    for seed in range(10):
        puzzle = game.generate_main(seed)
        assert game.check(puzzle, puzzle.answer) is True


def test_holding_reference_solution_passes():
    for seed in range(10):
        puzzle = game.generate_holding(seed)
        assert puzzle.kind == "holding"
        assert game.check(puzzle, puzzle.answer) is True


def test_generated_boards_start_unsolved():
    for seed in range(10):
        puzzle = game.generate_main(seed)
        payload = puzzle.payload
        assert composite(payload, payload["initial"]) != target_cells(payload)


def test_symmetric_equivalent_encoding_passes():
    # The checker validates the composite, not one canonical transform vector:
    # any re-encoding of the reference placements that lands the same cells
    # must pass. A 180° rotation of a 2-cell bar is such an equivalent.
    for seed in range(40):
        puzzle = game.generate_main(seed)
        payload = puzzle.payload
        solution = json.loads(puzzle.answer)["layers"]
        layers = {layer["id"]: layer for layer in payload["layers"]}
        for placement in solution:
            layer = layers[placement["id"]]
            if not layer["allow_rot"]:
                continue
            alt = dict(placement, rot=(placement["rot"] + 2) % 4)
            if _placed_cells(layer, alt) != _placed_cells(layer, placement):
                continue
            reencoded = [alt if p is placement else p for p in solution]
            assert game.check(
                puzzle, json.dumps({"v": 1, "layers": reencoded})
            ) is True
            return
    raise AssertionError("no symmetric layer found on any sampled seed")


def test_nudged_layer_fails():
    # Shifting one layer off its solution cells changes the composite, which
    # must fail (extra cells fail just like missing ones).
    for seed in range(10):
        puzzle = game.generate_main(seed)
        payload = puzzle.payload
        solution = json.loads(puzzle.answer)["layers"]
        layers = {layer["id"]: layer for layer in payload["layers"]}
        for index, placement in enumerate(solution):
            for dr, dc in ((0, 1), (1, 0), (0, -1), (-1, 0)):
                nudged = dict(placement, r=placement["r"] + dr, c=placement["c"] + dc)
                cells = _placed_cells(layers[placement["id"]], nudged)
                if any(
                    not (0 <= r < payload["rows"] and 0 <= c < payload["cols"])
                    for r, c in cells
                ):
                    continue
                candidate = list(solution)
                candidate[index] = nudged
                if composite(payload, candidate) == target_cells(payload):
                    continue  # a harmless equivalent — not the case under test
                answer = json.dumps({"v": 1, "layers": candidate})
                assert game.check(puzzle, answer) is False
                return
    raise AssertionError("no composite-changing nudge found on any sampled seed")


def test_out_of_bounds_placement_fails():
    puzzle = game.generate_main(2)
    solution = json.loads(puzzle.answer)["layers"]
    escaped = [dict(p) for p in solution]
    escaped[0]["r"] = puzzle.payload["rows"]  # off the board even if clipped
    assert game.check(puzzle, json.dumps({"v": 1, "layers": escaped})) is False


def test_duplicate_and_missing_layers_fail():
    puzzle = game.generate_main(3)
    solution = json.loads(puzzle.answer)["layers"]
    doubled = [solution[0]] + solution[1:]
    doubled[1] = dict(solution[0])  # same id twice
    assert game.check(puzzle, json.dumps({"v": 1, "layers": doubled})) is False
    assert game.check(puzzle, json.dumps({"v": 1, "layers": solution[:-1]})) is False
    assert game.check(puzzle, json.dumps({"v": 1, "layers": []})) is False


def test_disallowed_transforms_fail():
    # Holding boards have at least one translation-only layer; rotating or
    # flipping it is illegal even when the resulting composite would match.
    puzzle = game.generate_holding(1)
    payload = puzzle.payload
    solution = json.loads(puzzle.answer)["layers"]
    fixed = next(
        placement
        for placement in solution
        for layer in payload["layers"]
        if layer["id"] == placement["id"] and not layer["allow_rot"]
    )
    for illegal in (dict(fixed, rot=1), dict(fixed, fx=True), dict(fixed, fy=True)):
        candidate = [illegal if p is fixed else p for p in solution]
        assert game.check(puzzle, json.dumps({"v": 1, "layers": candidate})) is False


def test_malformed_answers_fail_safely():
    puzzle = game.generate_main(1)
    solution = json.loads(puzzle.answer)["layers"]
    for bad in (
        "",
        "not json",
        '{"v":1,"layers":',  # broken JSON
        json.dumps({"v": 2, "layers": solution}),  # wrong version
        json.dumps({"v": 1}),  # missing layers
        json.dumps({"v": 1, "layers": "l0"}),  # wrong type
        json.dumps({"v": 1, "layers": [1, 2, 3]}),  # not dicts
        json.dumps({"v": 1, "layers": [dict(p, r="0") for p in solution]}),
        json.dumps({"v": 1, "layers": [dict(p, r=True) for p in solution]}),
        json.dumps({"v": 1, "layers": [dict(p, rot=7) for p in solution]}),
        json.dumps({"v": 1, "layers": [dict(p, id="ghost") for p in solution]}),
        json.dumps({"v": 1, "layers": solution}) + " " * 1000,  # oversized
        json.dumps({"v": 1, "solved": True}),  # client claims don't count
    ):
        assert game.check(puzzle, bad) is False, bad


def test_no_solution_in_public_payload():
    for seed in range(5):
        puzzle = game.generate_main(seed)
        public = puzzle.public()
        assert "answer" not in public
        assert set(public["payload"]) == {
            "variant", "difficulty", "time_hint_seconds", "rules_version",
            "rows", "cols", "blend", "target", "layers", "initial",
        }
        # The target bitmap is public by design (§4: visible-target variant);
        # the solution transform vector must not be recoverable verbatim.
        assert puzzle.answer not in json.dumps(public)
        for layer in public["payload"]["layers"]:
            assert set(layer) == {
                "id", "pattern", "allow_rot", "allow_flip_x", "allow_flip_y",
            }


def test_ambiguity_is_bounded():
    for seed in range(5):
        payload = game.generate_main(seed).payload
        count = _count_solutions(
            payload["layers"], target_cells(payload), payload["rows"]
        )
        assert 1 <= count <= MAX_SOLUTIONS


def test_holding_materially_smaller():
    main, hold = game.generate_main(3), game.generate_holding(3)
    assert hold.payload["rows"] < main.payload["rows"]
    assert len(hold.payload["layers"]) < len(main.payload["layers"])
    assert all(not layer["allow_flip_x"] for layer in hold.payload["layers"])
    assert sum(layer["allow_rot"] for layer in hold.payload["layers"]) <= 1


def test_transform_order_is_flip_then_rotate():
    # An L-tromino: flip_x then one CW turn differs from rotating first.
    pattern = [[0, 0], [1, 0], [1, 1]]
    flipped_then_rotated = _transform(pattern, rot=1, fx=True, fy=False)
    rotated_only = _transform(pattern, rot=1, fx=False, fy=False)
    assert flipped_then_rotated != rotated_only
    assert _transform(pattern, rot=0, fx=False, fy=False) == frozenset(
        {(0, 0), (1, 0), (1, 1)}
    )


def test_reset_safe_and_deterministic_after():
    before = game.generate_main(5).payload
    game.reset()
    assert game.generate_main(5).payload == before


def test_generate_main_accepts_level():
    # v2 contract (docs/REDESIGN_PLAN.md): level is accepted; scaling is follow-up.
    puzzle = game.generate_main(42, level=5)
    assert puzzle.kind == "main" and puzzle.game_id == game.id
