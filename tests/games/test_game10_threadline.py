"""T4.x — THREADLINE: module-spec §8 suite + the expansion spec's minimum
acceptance tests (game/RELAY_EXPANSION_GAMES_README.md §14).

Boards are hand-built where a single rule is under test, and generated where
the point is that real boards behave.
"""

from __future__ import annotations

import json

from backend.games.base import PuzzleInstance, normalize_answer
from backend.games.game10_threadline import (
    BEND_CAP_MAX,
    BEND_CAP_MIN,
    HOLD_ANCHORS,
    HOLD_BLOCKED,
    HOLD_COLS,
    HOLD_ROWS,
    MAIN_COLS,
    MAIN_LEVEL_PARAMS,
    MAIN_ROWS,
    ThreadlineGame,
    _min_bends,
    _params_for_level,
    solve,
    validate,
)

game = ThreadlineGame()


def board(
    anchors, blocked, bend_cap=6, edge_cap=12, rows=5, cols=5,
    start=(4, 0), end=(0, 4),
) -> PuzzleInstance:
    """A hand-built board, used to pin down one rule at a time.

    `anchors` is a list of `(cell, entry, exit)` in visiting order.
    """
    return PuzzleInstance(
        game_id="threadline",
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
            "start": list(start),
            "end": list(end),
            "anchors": [
                {"id": f"a{order}", "cell": list(cell), "order": order,
                 "entry": entry, "exit": leave}
                for order, (cell, entry, leave) in enumerate(anchors)
            ],
            "blocked_cells": [list(cell) for cell in blocked],
            "bend_cap": bend_cap,
            "edge_cap": edge_cap,
        },
    )


def answer(*cells) -> str:
    return json.dumps({"v": 1, "path": [list(cell) for cell in cells]})


# The reference board for the rule tests: two anchors, one obstacle at (3,1).
PLAIN = board(anchors=[((4, 2), None, None), ((2, 4), None, None)], blocked=[(3, 1)])

# Along the bottom, then up the right edge: one bend, both anchors in order.
STRAIGHTFORWARD = ((4, 0), (4, 1), (4, 2), (4, 3), (4, 4), (3, 4), (2, 4), (1, 4), (0, 4))


# --- Generation ---------------------------------------------------------


def test_generate_main_is_deterministic():
    first, second = game.generate_main(42), game.generate_main(42)
    assert first.prompt == second.prompt
    assert first.answer == second.answer
    assert first.payload == second.payload


def test_different_seeds_give_different_boards():
    boards = {json.dumps(game.generate_main(seed).payload) for seed in range(15)}
    assert len(boards) > 10


def test_main_board_sits_inside_the_spec_windows():
    for level in range(1, len(MAIN_LEVEL_PARAMS) + 1):
        params = _params_for_level(level)
        for seed in range(6):
            puzzle = game.generate_main(seed, level)
            payload = puzzle.payload
            assert (payload["rows"], payload["cols"]) == (MAIN_ROWS, MAIN_COLS)
            assert 3 <= len(payload["anchors"]) <= 5
            assert 5 <= len(payload["blocked_cells"]) <= 10
            assert BEND_CAP_MIN <= payload["bend_cap"] <= BEND_CAP_MAX
            assert payload["edge_cap"] <= 30
            edges = len(json.loads(puzzle.answer)["path"]) - 1
            assert params["edges"][0] <= edges <= params["edges"][1]
            assert 12 <= edges <= 24


def test_board_geometry_is_coherent():
    for seed in range(20):
        payload = game.generate_main(seed, 7).payload
        start, end = tuple(payload["start"]), tuple(payload["end"])
        blocked = {tuple(cell) for cell in payload["blocked_cells"]}
        anchors = [tuple(anchor["cell"]) for anchor in payload["anchors"]]
        assert start != end
        assert not blocked & ({start, end} | set(anchors))
        assert len(set(anchors)) == len(anchors)
        assert len(blocked) == len(payload["blocked_cells"])
        assert [anchor["order"] for anchor in payload["anchors"]] == list(range(len(anchors)))
        for cell in [start, end] + anchors + list(blocked):
            assert 0 <= cell[0] < payload["rows"] and 0 <= cell[1] < payload["cols"]


def test_ports_only_appear_from_the_level_they_are_scheduled():
    for level in range(1, len(MAIN_LEVEL_PARAMS) + 1):
        allowed = _params_for_level(level)["ports"]
        for seed in range(5):
            payload = game.generate_main(seed, level).payload
            ported = [
                anchor for anchor in payload["anchors"]
                if anchor["entry"] is not None or anchor["exit"] is not None
            ]
            assert len(ported) == allowed
            for anchor in ported:
                # Version 1 sets one side per ported anchor, never both.
                assert (anchor["entry"] is None) != (anchor["exit"] is None)
                assert (anchor["entry"] or anchor["exit"]) in ("n", "s", "e", "w")


def test_every_generated_board_is_solvable_from_the_payload_alone():
    """The spec's solvability gate — a solver that never sees the reference."""
    for level in (1, 5, 10):
        for seed in range(25):
            puzzle = game.generate_main(seed, level)
            route = solve(puzzle.payload)
            assert route is not None, (level, seed)
            assert validate(puzzle.payload, route)["ok"]


def test_many_seeds_generate_and_the_reference_always_wins():
    for seed in range(150):
        puzzle = game.generate_main(seed, 1 + seed % len(MAIN_LEVEL_PARAMS))
        assert game.check(puzzle, puzzle.answer)


def test_obstacles_influence_routing():
    """The spec rejects boards whose obstacles decorate rather than route: on a
    main board, taking them away must lower the bends any route is forced to
    spend."""
    for seed in range(15):
        payload = game.generate_main(seed, 4).payload
        assert _min_bends(payload, blocked=set()) < _min_bends(payload)


def test_the_bend_cap_stays_close_to_what_the_board_forces():
    for level in (1, 10):
        freedom = _params_for_level(level)["bend_freedom"]
        for seed in range(10):
            payload = game.generate_main(seed, level).payload
            assert payload["bend_cap"] - _min_bends(payload) <= freedom


def test_min_bends_is_a_lower_bound_on_the_reference():
    for seed in range(15):
        puzzle = game.generate_main(seed, 6)
        reference = json.loads(puzzle.answer)["path"]
        assert _min_bends(puzzle.payload) <= validate(puzzle.payload, reference)["bends"]


# --- Level scaling (V5) -------------------------------------------------


def test_level_one_is_the_default_board():
    for seed in range(8):
        assert game.generate_main(seed).payload == game.generate_main(seed, 1).payload


def test_levels_are_deterministic_and_distinct():
    for seed in (3, 11):
        for level in range(1, len(MAIN_LEVEL_PARAMS) + 1):
            assert game.generate_main(seed, level).payload == game.generate_main(seed, level).payload
        boards = {
            json.dumps(game.generate_main(seed, level).payload)
            for level in range(1, len(MAIN_LEVEL_PARAMS) + 1)
        }
        assert len(boards) > 5


def test_level_knobs_climb_and_freedom_shrinks():
    levels = [_params_for_level(level) for level in range(1, len(MAIN_LEVEL_PARAMS) + 1)]
    for earlier, later in zip(levels, levels[1:]):
        assert later["anchors"] >= earlier["anchors"]
        assert later["blocked"] >= earlier["blocked"]
        assert later["ports"] >= earlier["ports"]
        assert later["min_bends"] >= earlier["min_bends"]
        assert later["edges"] >= earlier["edges"]
        # The two knobs that tighten with difficulty.
        assert later["bend_slack"] <= earlier["bend_slack"]
        assert later["bend_freedom"] <= earlier["bend_freedom"]
        assert later["edge_slack"] <= earlier["edge_slack"]
    assert levels[-1]["anchors"] > levels[0]["anchors"]
    assert levels[-1]["bend_freedom"] < levels[0]["bend_freedom"]


def test_level_ten_boards_are_harder_than_level_one_boards():
    def measure(level: int) -> tuple[float, float]:
        anchors, freedom = [], []
        for seed in range(12):
            payload = game.generate_main(seed, level).payload
            anchors.append(len(payload["anchors"]))
            freedom.append(payload["bend_cap"] - _min_bends(payload))
        return sum(anchors) / len(anchors), sum(freedom) / len(freedom)

    easy_anchors, easy_freedom = measure(1)
    hard_anchors, hard_freedom = measure(10)
    assert hard_anchors > easy_anchors
    assert hard_freedom < easy_freedom


def test_levels_outside_the_table_are_clamped():
    assert game.generate_main(5, 0).payload == game.generate_main(5, 1).payload
    top = len(MAIN_LEVEL_PARAMS)
    assert game.generate_main(5, top + 7).payload == game.generate_main(5, top).payload


def test_bonus_levels_stay_solvable():
    """A bonus board is the player's game at level + BONUS_LEVEL_OFFSET, which
    can run off the end of the table."""
    for level in (11, 13, 20):
        puzzle = game.generate_main(4, level)
        assert game.check(puzzle, puzzle.answer)
        assert solve(puzzle.payload) is not None


# --- Holding ------------------------------------------------------------


def test_holding_is_small_and_quick():
    for seed in range(20):
        puzzle = game.generate_holding(seed)
        payload = puzzle.payload
        assert puzzle.kind == "holding"
        assert (payload["rows"], payload["cols"]) == (HOLD_ROWS, HOLD_COLS)
        assert len(payload["anchors"]) == HOLD_ANCHORS
        assert len(payload["blocked_cells"]) == HOLD_BLOCKED
        assert len(json.loads(puzzle.answer)["path"]) - 1 <= 8
        assert game.check(puzzle, puzzle.answer)


def test_holding_is_deterministic():
    assert game.generate_holding(9).payload == game.generate_holding(9).payload
    assert game.generate_holding(9).answer == game.generate_holding(9).answer


# --- Checking -----------------------------------------------------------


def test_reference_route_passes_with_surrounding_whitespace():
    puzzle = game.generate_main(12, 3)
    assert game.check(puzzle, puzzle.answer)
    assert game.check(puzzle, f"  {puzzle.answer}\n")
    # JSON keys are case-sensitive, so the spec's upper-casing tolerance does
    # not apply to an encoded-interaction answer (as for the other action games).


def test_any_legal_route_wins_not_just_the_reference():
    puzzle = game.generate_main(1, 5)
    found = solve(puzzle.payload)
    assert found is not None
    assert game.check(puzzle, json.dumps({"v": 1, "path": found}))


def test_wrong_and_malformed_answers_fail():
    puzzle = game.generate_main(7)
    for bad in (
        "definitely-wrong",
        "",
        "   ",
        "[]",
        "{}",
        "null",
        '{"v":1}',
        '{"v":2,"path":[[0,0]]}',
        '{"v":1,"path":"nope"}',
        '{"v":1,"path":[]}',
        '{"v":1,"path":[[0]]}',
        '{"v":1,"path":[["0","0"]]}',
        '{"v":1,"path":[[0.5,1]]}',
        '{"v":1,"path":[[true,false]]}',
        '{"v":1,"path":[[0,0],[0,1]]}',
        "{'v': 1}",
        "[" * 400,
        json.dumps({"v": 1, "path": [[0, 0]] * 500}),
    ):
        assert game.check(puzzle, bad) is False, bad


def test_a_claimed_success_flag_is_ignored():
    puzzle = game.generate_main(8)
    forged = json.dumps({"v": 1, "path": [list(puzzle.payload["start"])], "ok": True})
    assert game.check(puzzle, forged) is False


def test_reference_route_wins_but_the_same_route_out_of_order_fails():
    """The spec's headline case: a geometrically similar route that takes the
    anchors in the wrong order is not a solution."""
    two_anchors = board(
        anchors=[((4, 2), None, None), ((2, 0), None, None)],
        blocked=[],
        bend_cap=6,
        edge_cap=16,
    )
    in_order = answer(
        (4, 0), (4, 1), (4, 2), (3, 2), (3, 1), (3, 0), (2, 0),
        (1, 0), (1, 1), (1, 2), (1, 3), (1, 4), (0, 4),
    )
    reversed_order = answer(
        (4, 0), (3, 0), (2, 0), (2, 1), (2, 2), (3, 2), (4, 2),
        (4, 3), (4, 4), (3, 4), (2, 4), (1, 4), (0, 4),
    )
    assert game.check(two_anchors, in_order) is True
    assert game.check(two_anchors, reversed_order) is False


def test_self_crossing_and_edge_reuse_fail():
    assert game.check(PLAIN, answer(*STRAIGHTFORWARD)) is True
    # Crosses back over a cell it has already used.
    assert game.check(PLAIN, answer(
        (4, 0), (4, 1), (4, 2), (3, 2), (3, 3), (4, 3), (4, 2),
    )) is False
    # A 180-degree reversal is edge reuse, and fails for the same reason.
    assert game.check(PLAIN, answer((4, 0), (4, 1), (4, 0), (4, 1), (4, 2))) is False


def test_blocked_entry_and_stray_steps_fail():
    assert game.check(PLAIN, answer((4, 0), (3, 0), (3, 1))) is False   # blocked
    assert game.check(PLAIN, answer((4, 0), (4, 2))) is False           # not adjacent
    assert game.check(PLAIN, answer((4, 0), (5, 0))) is False           # off board
    assert game.check(PLAIN, answer((3, 0), (4, 0))) is False           # wrong start
    assert game.check(PLAIN, answer(*STRAIGHTFORWARD[:-1])) is False    # stops short
    # Reaches the end socket but never takes an anchor.
    assert game.check(PLAIN, answer(
        (4, 0), (3, 0), (2, 0), (1, 0), (0, 0), (0, 1), (0, 2), (0, 3), (0, 4),
    )) is False


def test_bend_and_length_caps_bite():
    tight = board(
        anchors=[((4, 2), None, None), ((2, 4), None, None)],
        blocked=[(3, 1)],
        bend_cap=1,
        edge_cap=12,
    )
    assert game.check(tight, answer(*STRAIGHTFORWARD)) is True          # exactly 1 bend
    assert game.check(tight, answer(
        (4, 0), (4, 1), (4, 2), (3, 2), (3, 3), (3, 4), (2, 4), (1, 4), (0, 4),
    )) is False                                                         # 3 bends
    short = board(
        anchors=[((4, 2), None, None), ((2, 4), None, None)],
        blocked=[(3, 1)],
        bend_cap=6,
        edge_cap=6,
    )
    assert game.check(short, answer(*STRAIGHTFORWARD)) is False         # 8 edges


def test_directional_ports_are_enforced_in_both_directions():
    ported = board(
        anchors=[((4, 2), None, "n"), ((2, 4), "s", None)],
        blocked=[(3, 1)],
        bend_cap=4,
        edge_cap=12,
    )
    # Leaves a0 northwards and enters a1 from the south: both ports honoured.
    assert game.check(ported, answer(
        (4, 0), (4, 1), (4, 2), (3, 2), (3, 3), (3, 4), (2, 4), (1, 4), (0, 4),
    )) is True
    # Same anchors in the same order, but a0 is left eastwards.
    assert game.check(ported, answer(*STRAIGHTFORWARD)) is False
    # a1 entered from its west side instead of its south side.
    assert game.check(ported, answer(
        (4, 0), (4, 1), (4, 2), (3, 2), (2, 2), (2, 3), (2, 4), (1, 4), (0, 4),
    )) is False
    # A route that stops on a ported anchor never pays the exit it owes.
    assert game.check(ported, answer((4, 0), (4, 1), (4, 2))) is False


def test_partial_walks_report_progress_without_demanding_an_ending():
    prefix = [list(cell) for cell in STRAIGHTFORWARD[:5]]
    walk = validate(PLAIN.payload, prefix, partial=True)
    assert walk == {"ok": True, "reason": "", "edges": 4, "bends": 0, "anchors_visited": 1}
    assert validate(PLAIN.payload, prefix)["reason"] == "not_at_end"
    # A partial walk still refuses an illegal step.
    assert validate(PLAIN.payload, [[4, 0], [3, 0], [3, 1]], partial=True)["reason"] == "blocked"


# --- Contract -----------------------------------------------------------


def test_public_payload_leaks_no_route():
    for seed in range(10):
        puzzle = game.generate_main(seed, 9)
        public = json.dumps(puzzle.public())
        assert normalize_answer(puzzle.answer) not in normalize_answer(public)
        assert "path" not in public
        assert "answer" not in public
        # The payload carries the board, and nothing about how to cross it.
        assert set(puzzle.payload) == {
            "variant", "difficulty", "time_hint_seconds", "rules_version", "rows",
            "cols", "start", "end", "anchors", "blocked_cells", "bend_cap", "edge_cap",
        }


def test_module_identity_and_reset():
    assert game.id == "threadline"
    assert game.name == "Threadline"
    before = game.generate_main(2, 4).payload
    game.reset()
    assert game.generate_main(2, 4).payload == before
    game.reset()


def test_prompt_names_the_board_it_belongs_to():
    puzzle = game.generate_main(6, 8)
    assert str(len(puzzle.payload["anchors"])) in puzzle.prompt
    assert str(puzzle.payload["bend_cap"]) in puzzle.prompt
