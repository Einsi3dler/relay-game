"""T4.x — STACKDROP: module-spec §8 suite + the expansion spec's minimum
acceptance tests (game/RELAY_EXPANSION_GAMES_README.md §3)."""

from __future__ import annotations

import json

from backend.games.base import PuzzleInstance
from backend.games.game7_stackdrop import (
    HAZARD,
    HOLD,
    MAIN_BALLS,
    MAIN_COLS,
    MAIN_DEPTH,
    MAIN_HAZARDS,
    MAIN_LEVEL_PARAMS,
    MAIN_PINS,
    MAIN_ROWS,
    RAMP_LEFT,
    TILT_LEFT,
    TILT_RIGHT,
    WALL,
    StackdropGame,
    _initial_state,
    _order_sensitive,
    _params_for_level,
    _play,
    _resolve,
    chamber_from_payload,
)

game = StackdropGame()


def board(rows: int, cols: int, static, pins, balls, containers) -> PuzzleInstance:
    """A hand-built chamber, used to pin down one simulation rule at a time."""
    return PuzzleInstance(
        game_id="stackdrop",
        kind="main",
        prompt="",
        answer="",
        payload={
            "variant": "main",
            "difficulty": 1,
            "time_hint_seconds": 10,
            "rules_version": 1,
            "rows": rows,
            "cols": cols,
            "static_cells": [{"r": r, "c": c, "type": t} for r, c, t in static],
            "pins": [
                {"id": pin_id, "kind": kind, "cells": [list(cell) for cell in cells]}
                for pin_id, kind, cells in pins
            ],
            "balls": [
                {"id": ball_id, "kind": kind, "start": list(start)}
                for ball_id, kind, start in balls
            ],
            "containers": [
                {"id": container_id, "kind": kind, "cells": [list(cell)]}
                for container_id, kind, cell in containers
            ],
            "removal_cap": len(pins),
        },
    )


def final_cells(puzzle: PuzzleInstance, removals: list[str]) -> list[tuple]:
    state, alive = _play(chamber_from_payload(puzzle.payload), removals)
    return [(ball[0], ball[1], ball[2], alive) for ball in state]


def answer(*removals: str) -> str:
    return json.dumps({"v": 1, "remove": list(removals)})


# --- Generation ---------------------------------------------------------


def test_determinism():
    a, b = game.generate_main(42), game.generate_main(42)
    assert a.payload == b.payload
    assert a.answer == b.answer


def test_different_seeds_differ():
    boards = {json.dumps(game.generate_main(seed).payload) for seed in range(15)}
    assert len(boards) > 1


def test_reference_solution_passes():
    for seed in range(20):
        puzzle = game.generate_main(seed)
        assert game.check(puzzle, puzzle.answer) is True


def test_holding_reference_solution_passes():
    for seed in range(20):
        puzzle = game.generate_holding(seed)
        assert puzzle.kind == "holding"
        assert game.check(puzzle, puzzle.answer) is True


def test_generated_boards_start_still_and_unsolved():
    # The chamber is static until a pin is pulled: gravity at t=0 must leave
    # every ball exactly where the payload says it is, and none contained.
    for seed in range(20):
        chamber = chamber_from_payload(game.generate_main(seed).payload)
        state = _initial_state(chamber)
        assert _resolve(chamber, set(), state) is True
        assert state == _initial_state(chamber)
        assert not any(ball[2] for ball in state)


def test_solution_depth_inside_the_level_band():
    for seed in range(20):
        removals = json.loads(game.generate_main(seed).answer)["remove"]
        assert MAIN_DEPTH[0] <= len(removals) <= MAIN_DEPTH[1]


def test_every_main_board_is_order_sensitive():
    # The spec's headline acceptance test: at least one order-sensitive pair.
    for seed in range(20):
        puzzle = game.generate_main(seed)
        chamber = chamber_from_payload(puzzle.payload)
        assert _order_sensitive(chamber, json.loads(puzzle.answer)["remove"])


def test_pins_never_overlap_walls_ramps_balls_or_each_other():
    for seed in range(20):
        payload = game.generate_main(seed).payload
        static = {(cell["r"], cell["c"]) for cell in payload["static_cells"]}
        containers = {
            (r, c) for container in payload["containers"] for r, c in container["cells"]
        }
        starts = {tuple(ball["start"]) for ball in payload["balls"]}
        seen: set[tuple] = set()
        for pin in payload["pins"]:
            for r, c in pin["cells"]:
                cell = (r, c)
                assert cell not in static and cell not in containers
                assert cell not in starts and cell not in seen
                seen.add(cell)


def test_hold_pins_support_every_ball():
    # A ball may only rest on something flat, so its support is a hold pin.
    for seed in range(20):
        payload = game.generate_main(seed).payload
        supports = {
            (r, c)
            for pin in payload["pins"]
            if pin["kind"] == HOLD
            for r, c in pin["cells"]
        }
        for ball in payload["balls"]:
            r, c = ball["start"]
            assert (r + 1, c) in supports


def test_holding_materially_smaller():
    main, hold = game.generate_main(3), game.generate_holding(3)
    assert hold.payload["rows"] < main.payload["rows"]
    assert hold.payload["cols"] < main.payload["cols"]
    assert len(hold.payload["balls"]) < len(main.payload["balls"])
    assert len(hold.payload["pins"]) < len(main.payload["pins"])
    assert len(json.loads(hold.answer)["remove"]) <= 2


# --- Simulation rules ---------------------------------------------------


def test_ball_falls_straight_into_its_container():
    puzzle = board(
        4, 3,
        [(3, 0, WALL), (3, 2, HAZARD)],
        [("p0", HOLD, [(1, 1)])],
        [("b0", "circle", (0, 1))],
        [("c0", "circle", (3, 1))],
    )
    assert final_cells(puzzle, []) == [(0, 1, False, True)]      # static until pulled
    assert final_cells(puzzle, ["p0"]) == [(3, 1, True, True)]
    assert game.check(puzzle, answer("p0")) is True


def test_slanted_pin_steers_while_it_is_there_and_not_once_it_is_gone():
    # p1 is the only thing routing the ball off the wall at (3,1): pull p0 and
    # it rolls into the container, pull p1 first and it lands on the wall.
    puzzle = board(
        4, 3,
        [(3, 0, HAZARD), (3, 1, WALL)],
        [("p0", HOLD, [(1, 1)]), ("p1", TILT_RIGHT, [(2, 1)])],
        [("b0", "circle", (0, 1))],
        [("c0", "circle", (3, 2))],
    )
    assert final_cells(puzzle, ["p0"]) == [(3, 2, True, True)]
    assert final_cells(puzzle, ["p1", "p0"]) == [(2, 1, False, True)]
    assert game.check(puzzle, answer("p0")) is True
    assert game.check(puzzle, answer("p1", "p0")) is False


def test_fixed_ramp_steers_the_same_way_as_a_slanted_pin():
    puzzle = board(
        4, 3,
        [(2, 1, RAMP_LEFT), (3, 1, WALL), (3, 2, HAZARD)],
        [("p0", HOLD, [(1, 1)])],
        [("b0", "square", (0, 1))],
        [("c0", "square", (3, 0))],
    )
    assert final_cells(puzzle, ["p0"]) == [(3, 0, True, True)]


def test_hazard_fails_the_attempt():
    hazard = board(
        4, 3,
        [(3, 0, WALL), (3, 1, HAZARD)],
        [("p0", HOLD, [(1, 1)])],
        [("b0", "circle", (0, 1))],
        [("c0", "circle", (3, 2))],
    )
    assert final_cells(hazard, ["p0"])[0][3] is False       # the attempt is dead
    assert game.check(hazard, answer("p0")) is False


def test_wrong_container_fails_the_attempt():
    wrong = board(
        4, 3,
        [(3, 0, WALL), (3, 2, WALL)],
        [("p0", HOLD, [(1, 1)])],
        [("b0", "triangle", (0, 1))],
        [("c0", "circle", (3, 1))],
    )
    assert final_cells(wrong, ["p0"])[0][3] is False
    assert game.check(wrong, answer("p0")) is False


def test_balls_stack_and_resolve_bottom_first():
    puzzle = board(
        6, 3,
        [(5, 0, WALL)],
        [("p0", HOLD, [(1, 2)]), ("p1", HOLD, [(3, 2)])],
        [("b0", "circle", (0, 2)), ("b1", "triangle", (2, 2))],
        [("c0", "circle", (5, 1)), ("c1", "triangle", (5, 2))],
    )
    # Pull the top pin only: b0 comes to rest on b1 instead of through it.
    assert final_cells(puzzle, ["p0"]) == [(1, 2, False, True), (2, 2, False, True)]
    # Both pins gone: the lower ball leads and the upper one settles on top of
    # it — no tunnelling either way round, and b0 never reaches its container.
    settled = [(4, 2, False, True), (5, 2, True, True)]
    assert final_cells(puzzle, ["p0", "p1"]) == settled
    assert final_cells(puzzle, ["p1", "p0"]) == settled
    assert game.check(puzzle, answer("p0", "p1")) is False
    assert game.check(puzzle, answer("p1", "p0")) is False


def test_a_ball_cannot_roll_out_through_the_chamber_wall():
    puzzle = board(
        4, 3,
        [(3, 0, WALL), (3, 1, WALL), (3, 2, WALL)],
        [("p0", HOLD, [(1, 0)]), ("p1", TILT_LEFT, [(2, 0)])],
        [("b0", "circle", (0, 0))],
        [("c0", "circle", (3, 2))],
    )
    assert final_cells(puzzle, ["p0"]) == [(1, 0, False, True)]
    assert game.check(puzzle, answer("p0", "p1")) is False


# --- Checker contract ---------------------------------------------------


def test_incomplete_removal_sequence_fails():
    for seed in range(10):
        puzzle = game.generate_main(seed)
        removals = json.loads(puzzle.answer)["remove"]
        assert game.check(puzzle, answer(*removals[:-1])) is False


def test_repeated_and_unknown_pin_ids_fail():
    puzzle = game.generate_main(4)
    removals = json.loads(puzzle.answer)["remove"]
    assert game.check(puzzle, answer(*removals, removals[0])) is False
    assert game.check(puzzle, answer(*removals, "ghost")) is False
    assert game.check(puzzle, answer("ghost")) is False


def test_more_removals_than_the_cap_fail():
    puzzle = game.generate_main(5)
    every_pin = [pin["id"] for pin in puzzle.payload["pins"]]
    assert game.check(puzzle, answer(*every_pin, "p99")) is False


def test_client_cannot_claim_a_solved_board():
    puzzle = game.generate_main(6)
    for bluff in (
        json.dumps({"v": 1, "remove": [], "solved": True}),
        json.dumps({"v": 1, "solved": True}),
        json.dumps({"v": 1, "remove": [], "state": "all balls contained"}),
    ):
        assert game.check(puzzle, bluff) is False


def test_malformed_answers_fail_safely():
    puzzle = game.generate_main(1)
    removals = json.loads(puzzle.answer)["remove"]
    for bad in (
        "",
        "   ",
        "not json",
        '{"v":1,"remove":',                                  # broken JSON
        json.dumps({"v": 2, "remove": removals}),            # wrong version
        json.dumps({"v": 1}),                                # missing removals
        json.dumps({"v": 1, "remove": "p0"}),                # wrong type
        json.dumps({"v": 1, "remove": [None]}),
        json.dumps({"v": 1, "remove": [["p0"]]}),
        json.dumps({"v": 1, "remove": [{"id": "p0"}]}),
        json.dumps({"v": 1, "remove": [0, 1]}),              # indices, not ids
        json.dumps([1, 2, 3]),                               # not an object
        json.dumps({"v": 1, "remove": removals}) + " " * 1000,   # oversized
    ):
        assert game.check(puzzle, bad) is False, bad


def test_no_solution_in_public_payload():
    for seed in range(5):
        puzzle = game.generate_main(seed)
        public = puzzle.public()
        assert "answer" not in public
        assert set(public["payload"]) == {
            "variant", "difficulty", "time_hint_seconds", "rules_version",
            "rows", "cols", "static_cells", "pins", "balls", "containers",
            "removal_cap",
        }
        # The chamber is public by design — the pull order is not.
        assert puzzle.answer not in json.dumps(public)
        for pin in public["payload"]["pins"]:
            assert set(pin) == {"id", "kind", "cells"}


def test_reset_safe_and_deterministic_after():
    before = game.generate_main(5).payload
    game.reset()
    assert game.generate_main(5).payload == before


# --- Level curve (V5) ---------------------------------------------------


def test_level_determinism():
    for level in (1, 5, 10):
        a = game.generate_main(42, level=level)
        b = game.generate_main(42, level=level)
        assert a.payload == b.payload and a.answer == b.answer


def test_level_one_matches_original_board():
    assert _params_for_level(1) == {
        "rows": MAIN_ROWS, "cols": MAIN_COLS, "balls": MAIN_BALLS,
        "pins": MAIN_PINS, "depth": MAIN_DEPTH, "hazards": MAIN_HAZARDS,
        "difficulty": 2, "time_hint": 30,
    }
    assert game.generate_main(42, level=1).payload == game.generate_main(42).payload


def test_level_params_monotonic():
    for easier, harder in zip(MAIN_LEVEL_PARAMS, MAIN_LEVEL_PARAMS[1:]):
        for knob in ("rows", "cols", "balls", "pins", "hazards", "difficulty",
                     "time_hint"):
            assert easier[knob] <= harder[knob]
        assert easier["depth"][0] <= harder["depth"][0]
        assert easier["depth"][1] <= harder["depth"][1]
        assert harder["balls"] <= 4        # one per shape glyph
        assert harder["pins"] <= 7         # the spec's main-board ceiling
        assert harder["balls"] < harder["pins"]   # room for steering pins


def test_levels_out_of_range_are_clamped():
    assert _params_for_level(0) == _params_for_level(1)
    assert _params_for_level(99) == _params_for_level(10)


def test_every_level_generates_solvable_scaled_boards():
    for level in range(1, 11):
        params = _params_for_level(level)
        for seed in (3, 44, 90):
            puzzle = game.generate_main(seed, level=level)
            payload = puzzle.payload
            assert payload["rows"] == params["rows"]
            assert payload["cols"] == params["cols"]
            assert len(payload["balls"]) == params["balls"]
            assert len(payload["pins"]) == params["pins"]
            assert len(payload["containers"]) == params["balls"]
            assert game.check(puzzle, puzzle.answer) is True


def test_level_ten_visibly_harder():
    top = _params_for_level(10)
    assert top["rows"] > MAIN_ROWS and top["cols"] > MAIN_COLS
    assert top["balls"] > MAIN_BALLS and top["pins"] > MAIN_PINS
    assert top["depth"][0] > MAIN_DEPTH[0]
