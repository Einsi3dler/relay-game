"""T4.x — LANE SHIFT: module-spec §8 suite + the expansion spec's minimum
acceptance tests (game/RELAY_EXPANSION_GAMES_README.md §2)."""

from __future__ import annotations

import json

from backend.games.base import PuzzleInstance
from backend.games.game8_lane_shift import (
    DOWN,
    HOLD_TURN_CAP,
    MAIN_BLOCKERS,
    MAIN_COLUMNS,
    MAIN_HOLDS,
    MAIN_LANES,
    MAIN_LEVEL_PARAMS,
    MAIN_MIN_ACTIONS,
    MAIN_PACKETS,
    MAIN_SWITCHES,
    MAIN_TURNS,
    MAIN_TURN_CAP,
    PASS,
    STRAIGHT,
    UP,
    LaneShiftGame,
    _params_for_level,
    _replay,
    _solve,
    board_from_payload,
)

game = LaneShiftGame()


def board(
    lanes: int, columns: int, switches, holds, blockers, packets, exits,
    turn_cap: int = 20,
) -> PuzzleInstance:
    """A hand-built conveyor, used to pin down one rule at a time."""
    return PuzzleInstance(
        game_id="lane_shift",
        kind="main",
        prompt="",
        answer="",
        payload={
            "variant": "main",
            "difficulty": 1,
            "time_hint_seconds": 10,
            "rules_version": 1,
            "lanes": lanes,
            "columns": columns,
            "switches": [
                {"id": sid, "cell": list(cell), "states": list(states), "initial": initial}
                for sid, cell, states, initial in switches
            ],
            "holds": [
                {"id": hid, "cell": list(cell), "charges": charges}
                for hid, cell, charges in holds
            ],
            "blockers": [list(cell) for cell in blockers],
            "packets": [
                {"id": pid, "kind": kind, "start": list(start), "spawn_tick": spawn}
                for pid, kind, start, spawn in packets
            ],
            "exits": [{"lane": lane, "kind": kind} for lane, kind in enumerate(exits)],
            "turn_cap": turn_cap,
        },
    )


def answer(*actions) -> str:
    return json.dumps({"v": 1, "actions": [list(action) for action in actions]})


PASS_ACTION = ("pass",)


# --- Generation ---------------------------------------------------------


def test_determinism():
    a, b = game.generate_main(42), game.generate_main(42)
    assert a.payload == b.payload
    assert a.answer == b.answer


def test_different_seeds_differ():
    boards = {json.dumps(game.generate_main(seed).payload) for seed in range(15)}
    assert len(boards) > 1


def test_reference_schedule_passes():
    for seed in range(20):
        puzzle = game.generate_main(seed)
        assert game.check(puzzle, puzzle.answer) is True


def test_holding_reference_schedule_passes():
    for seed in range(20):
        puzzle = game.generate_holding(seed)
        assert puzzle.kind == "holding"
        assert game.check(puzzle, puzzle.answer) is True


def test_passing_forever_never_solves_a_board():
    # If the belt sorted itself there would be no puzzle: every generated board
    # must need real junction work.
    for seed in range(20):
        puzzle = game.generate_main(seed)
        cap = puzzle.payload["turn_cap"]
        assert game.check(puzzle, answer(*([PASS_ACTION] * cap))) is False


def test_solution_needs_the_level_minimum_of_real_actions():
    for seed in range(12):
        puzzle = game.generate_main(seed)
        solved = _solve(board_from_payload(puzzle.payload))
        assert solved is not None
        assert solved[1] >= MAIN_MIN_ACTIONS


def test_solution_length_inside_the_level_band():
    for seed in range(20):
        actions = json.loads(game.generate_main(seed).answer)["actions"]
        assert MAIN_TURNS[0] <= len(actions) <= MAIN_TURNS[1]
        assert len(actions) <= MAIN_TURN_CAP


def test_packets_are_sorted_across_at_least_two_exits():
    for seed in range(20):
        payload = game.generate_main(seed).payload
        assert len({packet["kind"] for packet in payload["packets"]}) >= 2


def test_junction_options_always_stay_on_the_belt():
    # Every switch option must be survivable — a decoy that always loses is
    # noise, not a decision (spec §7).
    for seed in range(20):
        payload = game.generate_main(seed).payload
        for switch in payload["switches"]:
            row = switch["cell"][0]
            assert UP not in switch["states"] or row > 0
            assert DOWN not in switch["states"] or row < payload["lanes"] - 1
            assert switch["initial"] in switch["states"]


def test_packets_never_share_a_spawn_slot():
    for seed in range(20):
        payload = game.generate_main(seed).payload
        slots = [
            (packet["start"][0], packet["spawn_tick"]) for packet in payload["packets"]
        ]
        assert len(set(slots)) == len(slots)


def test_holding_materially_smaller():
    main, hold = game.generate_main(3), game.generate_holding(3)
    assert hold.payload["lanes"] < main.payload["lanes"]
    assert hold.payload["columns"] < main.payload["columns"]
    assert len(hold.payload["packets"]) < len(main.payload["packets"])
    assert len(hold.payload["switches"]) < len(main.payload["switches"])
    assert hold.payload["turn_cap"] == HOLD_TURN_CAP
    assert len(json.loads(hold.answer)["actions"]) <= 4


# --- Simulation rules ---------------------------------------------------


def test_a_packet_rides_its_lane_into_the_matching_exit():
    puzzle = board(
        2, 2, [], [], [], [("p0", "circle", (0, 0), 0)], ["circle", "triangle"],
    )
    assert game.check(puzzle, answer(PASS_ACTION, PASS_ACTION)) is True
    assert game.check(puzzle, answer(PASS_ACTION)) is False   # still on the belt


def test_toggling_a_junction_changes_the_exit():
    puzzle = board(
        2, 2,
        [("s0", (0, 0), [STRAIGHT, DOWN], STRAIGHT)],
        [], [], [("p0", "triangle", (0, 0), 0)], ["circle", "triangle"],
    )
    assert game.check(puzzle, answer(("toggle", "s0"), PASS_ACTION)) is True
    assert game.check(puzzle, answer(PASS_ACTION, PASS_ACTION)) is False  # wrong exit


def test_two_packets_landing_on_one_cell_collide():
    puzzle = board(
        2, 3,
        [("s0", (0, 0), [STRAIGHT, DOWN], DOWN)],
        [], [],
        [("p0", "triangle", (0, 0), 0), ("p1", "triangle", (1, 0), 0)],
        ["circle", "triangle"],
    )
    # Left as-is both packets target (1,1).
    assert game.check(puzzle, answer(PASS_ACTION, PASS_ACTION, PASS_ACTION)) is False
    # Straightening the junction first keeps them apart, but then p0 reaches the
    # circle exit while it is a triangle — the collision is what is under test.
    assert game.check(puzzle, answer(("toggle", "s0"), PASS_ACTION, PASS_ACTION)) is False


def test_packets_may_cross_diagonally_without_colliding():
    # Documented ruling: a simultaneous swap of *rows* shares no cell, so it is
    # legal; only two packets landing on the same cell collide.
    puzzle = board(
        2, 2,
        [("s0", (0, 0), [STRAIGHT, DOWN], DOWN), ("s1", (1, 0), [STRAIGHT, UP], UP)],
        [], [],
        [("p0", "triangle", (0, 0), 0), ("p1", "circle", (1, 0), 0)],
        ["circle", "triangle"],
    )
    assert game.check(puzzle, answer(PASS_ACTION, PASS_ACTION)) is True


def test_running_off_the_belt_fails():
    puzzle = board(
        2, 2,
        [("s0", (0, 0), [STRAIGHT, UP], UP)],
        [], [], [("p0", "circle", (0, 0), 0)], ["circle", "triangle"],
    )
    assert game.check(puzzle, answer(PASS_ACTION, PASS_ACTION)) is False
    assert game.check(puzzle, answer(("toggle", "s0"), PASS_ACTION)) is True


def test_a_blocker_stops_the_attempt():
    puzzle = board(
        2, 3, [], [], [(0, 1)],
        [("p0", "circle", (0, 0), 0)], ["circle", "triangle"],
    )
    assert game.check(puzzle, answer(PASS_ACTION, PASS_ACTION, PASS_ACTION)) is False


def test_hold_delays_a_packet_and_spends_a_charge():
    puzzle = board(
        2, 2, [], [("h0", (0, 0), 1)], [],
        [("p0", "circle", (0, 0), 0)], ["circle", "triangle"],
    )
    assert game.check(puzzle, answer(("hold", "h0"), PASS_ACTION, PASS_ACTION)) is True
    # The pad only has one charge.
    assert game.check(
        puzzle, answer(("hold", "h0"), ("hold", "h0"), PASS_ACTION, PASS_ACTION)
    ) is False


def test_holding_an_empty_pad_is_illegal():
    puzzle = board(
        2, 2, [], [("h0", (1, 0), 1)], [],
        [("p0", "circle", (0, 0), 0)], ["circle", "triangle"],
    )
    assert game.check(puzzle, answer(("hold", "h0"), PASS_ACTION)) is False


def test_a_packet_cannot_spawn_onto_an_occupied_cell():
    puzzle = board(
        2, 3, [], [("h0", (0, 0), 1)], [],
        [("p0", "circle", (0, 0), 0), ("p1", "circle", (0, 0), 1)],
        ["circle", "triangle"],
    )
    # Holding p0 on the spawn cell means p1 has nowhere to arrive.
    assert game.check(
        puzzle, answer(("hold", "h0"), PASS_ACTION, PASS_ACTION, PASS_ACTION)
    ) is False
    # Letting p0 move on first is fine.
    assert game.check(
        puzzle, answer(PASS_ACTION, PASS_ACTION, PASS_ACTION, PASS_ACTION)
    ) is True


# --- Checker contract ---------------------------------------------------


def test_unknown_ids_and_bad_action_names_fail():
    puzzle = game.generate_main(4)
    assert game.check(puzzle, answer(("toggle", "s99"))) is False
    assert game.check(puzzle, answer(("hold", "h99"))) is False
    assert game.check(puzzle, answer(("teleport", "s0"))) is False
    assert game.check(puzzle, answer(("pass", "s0"))) is False


def test_more_actions_than_the_turn_cap_fail():
    puzzle = game.generate_main(5)
    cap = puzzle.payload["turn_cap"]
    assert game.check(puzzle, answer(*([PASS_ACTION] * (cap + 1)))) is False


def test_incomplete_schedule_fails():
    for seed in range(10):
        puzzle = game.generate_main(seed)
        actions = [tuple(a) for a in json.loads(puzzle.answer)["actions"]]
        assert game.check(puzzle, answer(*actions[:-1])) is False


def test_client_cannot_claim_a_solved_board():
    puzzle = game.generate_main(6)
    for bluff in (
        json.dumps({"v": 1, "actions": [], "solved": True}),
        json.dumps({"v": 1, "solved": True}),
        json.dumps({"v": 1, "actions": [["pass"]], "delivered": 99}),
    ):
        assert game.check(puzzle, bluff) is False


def test_malformed_answers_fail_safely():
    puzzle = game.generate_main(1)
    actions = json.loads(puzzle.answer)["actions"]
    for bad in (
        "",
        "   ",
        "not json",
        '{"v":1,"actions":',                                   # broken JSON
        json.dumps({"v": 2, "actions": actions}),              # wrong version
        json.dumps({"v": 1}),                                  # missing actions
        json.dumps({"v": 1, "actions": "pass"}),               # wrong type
        json.dumps({"v": 1, "actions": ["pass"]}),             # not a list of lists
        json.dumps({"v": 1, "actions": [[]]}),                 # empty action
        json.dumps({"v": 1, "actions": [["toggle", "s0", "x"]]}),
        json.dumps({"v": 1, "actions": [[1, 2]]}),             # non-string parts
        json.dumps({"v": 1, "actions": [None]}),
        json.dumps([1, 2, 3]),                                 # not an object
        json.dumps({"v": 1, "actions": actions}) + " " * 1200,  # oversized
    ):
        assert game.check(puzzle, bad) is False, bad


def test_no_solution_in_public_payload():
    for seed in range(5):
        puzzle = game.generate_main(seed)
        public = puzzle.public()
        assert "answer" not in public
        assert set(public["payload"]) == {
            "variant", "difficulty", "time_hint_seconds", "rules_version",
            "lanes", "columns", "switches", "holds", "blockers", "packets",
            "exits", "turn_cap",
        }
        # The belt is public by design — the schedule is not.
        assert puzzle.answer not in json.dumps(public)
        for switch in public["payload"]["switches"]:
            assert set(switch) == {"id", "cell", "states", "initial"}


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
    params = _params_for_level(1)
    assert (params["lanes"], params["columns"]) == (MAIN_LANES, MAIN_COLUMNS)
    assert params["packets"] == MAIN_PACKETS
    assert params["switches"] == MAIN_SWITCHES
    assert params["holds"] == MAIN_HOLDS
    assert params["blockers"] == MAIN_BLOCKERS
    assert params["turns"] == MAIN_TURNS
    assert params["min_actions"] == MAIN_MIN_ACTIONS
    assert game.generate_main(42, level=1).payload == game.generate_main(42).payload


def test_level_params_monotonic():
    for easier, harder in zip(MAIN_LEVEL_PARAMS, MAIN_LEVEL_PARAMS[1:]):
        for knob in ("lanes", "columns", "packets", "switches", "holds",
                     "blockers", "min_actions", "difficulty", "time_hint"):
            assert easier[knob] <= harder[knob]
        assert easier["turns"][1] <= harder["turns"][1]
        assert harder["lanes"] <= 4          # one exit per shape glyph
        assert harder["switches"] <= 5       # the spec's junction ceiling
        assert harder["packets"] <= 6        # the spec's packet ceiling


def test_levels_out_of_range_are_clamped():
    assert _params_for_level(0) == _params_for_level(1)
    assert _params_for_level(99) == _params_for_level(10)


def test_every_level_generates_solvable_scaled_boards():
    for level in range(1, 11):
        params = _params_for_level(level)
        for seed in (3, 44):
            puzzle = game.generate_main(seed, level=level)
            payload = puzzle.payload
            assert payload["lanes"] == params["lanes"]
            assert payload["columns"] == params["columns"]
            assert len(payload["packets"]) == params["packets"]
            assert len(payload["switches"]) == params["switches"]
            assert len(payload["holds"]) == params["holds"]
            assert game.check(puzzle, puzzle.answer) is True
            assert _replay(
                board_from_payload(payload), [PASS] * payload["turn_cap"]
            ) is False


def test_level_ten_visibly_harder():
    top = _params_for_level(10)
    assert top["lanes"] > MAIN_LANES and top["columns"] > MAIN_COLUMNS
    assert top["packets"] > MAIN_PACKETS and top["switches"] > MAIN_SWITCHES
    assert top["min_actions"] > MAIN_MIN_ACTIONS
    assert top["holds"] > MAIN_HOLDS
