"""T4.x — SHADOW CAST: module-spec §8 suite + the expansion spec's minimum
acceptance tests (game/RELAY_EXPANSION_GAMES_README.md §7)."""

from __future__ import annotations

import json
from collections import Counter, deque

from backend.games.base import PuzzleInstance
from backend.games.game9_shadow_cast import (
    HOLD_ACTION_CAP,
    HOLD_BOUND,
    HOLD_DISTANCE,
    HOLD_VOXELS,
    IDENTITY,
    MAIN_ACTION_CAP,
    MAIN_BOUND,
    MAIN_DISTANCE,
    MAIN_EQUIVALENT,
    MAIN_LEVEL_PARAMS,
    MAIN_VOXELS,
    MATRICES,
    MIN_EMPTY,
    MIN_FILLED,
    NEIGHBOURS,
    ORIENTATIONS,
    TURNS,
    ShadowCastGame,
    _distances,
    _extent,
    _normalise,
    _params_for_level,
    _project,
    _transform,
    replay,
)

game = ShadowCastGame()


def puzzle(voxels, start, bound, front, top, action_cap=MAIN_ACTION_CAP) -> PuzzleInstance:
    """A hand-built board, used to pin down one rule at a time."""
    return PuzzleInstance(
        game_id="shadow_cast",
        kind="main",
        prompt="",
        answer="",
        payload={
            "variant": "main",
            "difficulty": 1,
            "time_hint_seconds": 10,
            "rules_version": 1,
            "voxels": [list(cell) for cell in voxels],
            "initial_orientation": start,
            "bound": bound,
            "targets": {"front": list(front), "top": list(top)},
            "action_cap": action_cap,
        },
    )


def answer(*turns) -> str:
    return json.dumps({"v": 1, "turns": list(turns)})


def determinant(matrix) -> int:
    return (
        matrix[0][0] * (matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1])
        - matrix[0][1] * (matrix[1][0] * matrix[2][2] - matrix[1][2] * matrix[2][0])
        + matrix[0][2] * (matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0])
    )


def mirrored(shape):
    """The shape reflected through x — a pose no rotation can reach when the
    shape is chiral."""
    return _normalise([(-x, y, z) for x, y, z in shape])


def poses_of(payload) -> list:
    shape = tuple(tuple(cell) for cell in payload["voxels"])
    return [_transform(shape, matrix) for matrix in ORIENTATIONS]


def pairs_of(payload) -> list:
    return [_project(pose, payload["bound"]) for pose in poses_of(payload)]


def target_pair(payload):
    return (tuple(payload["targets"]["front"]), tuple(payload["targets"]["top"]))


def accepting_of(payload) -> set[int]:
    """Every orientation whose shadows already match the targets."""
    wanted = target_pair(payload)
    return {index for index, pair in enumerate(pairs_of(payload)) if pair == wanted}


def routes_from(start: int) -> dict[int, list[str]]:
    """A shortest turn list from `start` to every reachable orientation."""
    found = {start: []}
    queue: deque[int] = deque([start])
    while queue:
        current = queue.popleft()
        for at, token in enumerate(TURNS):
            after = NEIGHBOURS[current][at]
            if after not in found:
                found[after] = found[current] + [token]
                queue.append(after)
    return found


# --- The rotation group -------------------------------------------------


def test_the_group_is_the_24_proper_orientations():
    assert len(ORIENTATIONS) == 24
    assert len(set(ORIENTATIONS)) == 24
    assert ORIENTATIONS[0] == IDENTITY          # index 0 anchors the payload
    # Proper rotations only: a determinant of -1 anywhere would mean the object
    # could be mirrored by a "rotation".
    assert all(determinant(matrix) == 1 for matrix in ORIENTATIONS)
    assert all(determinant(MATRICES[token]) == 1 for token in TURNS)


def test_quarter_turns_have_order_four_and_pair_up_as_inverses():
    for axis in "xyz":
        forward, back = MATRICES[axis + "+"], MATRICES[axis + "-"]
        current = IDENTITY
        for _ in range(4):
            current = tuple(
                tuple(sum(forward[r][k] * current[k][c] for k in range(3)) for c in range(3))
                for r in range(3)
            )
        assert current == IDENTITY               # four quarter turns is a no-op
        undone = tuple(
            tuple(sum(back[r][k] * forward[k][c] for k in range(3)) for c in range(3))
            for r in range(3)
        )
        assert undone == IDENTITY


def test_the_graph_diameter_is_three():
    # This is why the level curve tops out at distance 3: no board can ever
    # need a fourth quarter turn.
    shells = Counter(_distances({0}))
    assert dict(shells) == {0: 1, 1: 6, 2: 11, 3: 6}


# --- Axis conventions ---------------------------------------------------


def test_projection_axes_are_pinned():
    # Two cubes side by side on the floor plus one stacked on the left.
    shape = _normalise([(0, 0, 0), (1, 0, 0), (0, 0, 1)])
    front, top = _project(shape, 3)
    # FRONT: columns are x, rows run bottom-to-top in z.
    assert list(front) == ["000", "100", "110"]
    # TOP: columns are x, rows run bottom-to-top in y — one row deep here.
    assert list(top) == ["000", "000", "110"]


def test_tipping_the_object_moves_depth_into_the_top_view():
    shape = _normalise([(0, 0, 0), (1, 0, 0), (0, 0, 1)])
    tipped = _transform(shape, MATRICES["x+"])
    front, top = _project(tipped, 3)
    assert list(front) == ["000", "000", "110"]   # the stack has folded away
    assert list(top) == ["000", "110", "100"]     # and shows up as depth


def test_normalisation_only_translates():
    shape = _normalise([(0, 0, 0), (1, 0, 0), (1, 1, 0), (1, 1, 1)])
    for matrix in ORIENTATIONS:
        turned = _transform(shape, matrix)
        assert len(turned) == len(shape)          # a rotation loses no cubes
        for axis in range(3):
            assert min(cell[axis] for cell in turned) == 0
        # The bounding box is only ever permuted, never stretched or squashed.
        assert sorted(
            max(cell[axis] for cell in turned) for axis in range(3)
        ) == sorted(max(cell[axis] for cell in shape) for axis in range(3))


# --- Generation ---------------------------------------------------------


def test_determinism():
    a, b = game.generate_main(42), game.generate_main(42)
    assert a.payload == b.payload
    assert a.answer == b.answer


def test_different_seeds_differ():
    boards = {json.dumps(game.generate_main(seed).payload) for seed in range(15)}
    assert len(boards) > 1


def test_reference_turns_pass():
    for seed in range(20):
        board = game.generate_main(seed)
        assert game.check(board, board.answer) is True


def test_holding_reference_turns_pass():
    for seed in range(20):
        board = game.generate_holding(seed)
        assert board.kind == "holding"
        assert game.check(board, board.answer) is True


def test_a_thousand_seeds_generate_and_stay_solvable():
    # The expansion spec's definition of done: 1,000 deterministic seeds with no
    # invalid or unsolved instance.
    for seed in range(1000):
        board = game.generate_main(seed, level=1 + seed % 10)
        assert game.check(board, board.answer) is True


def test_shape_fits_the_bound_in_every_orientation():
    for seed in range(50):
        payload = game.generate_main(seed).payload
        assert _extent([tuple(cell) for cell in payload["voxels"]]) <= payload["bound"]
        for pose in poses_of(payload):
            assert _extent(pose) <= payload["bound"]


def test_generated_shapes_have_no_rotational_symmetry():
    # A symmetric shape would make some buttons look broken (spec §7).
    for seed in range(50):
        assert len(set(poses_of(game.generate_main(seed).payload))) == 24


def test_every_turn_visibly_moves_the_object():
    # The spec's acceptance test: generated main shapes respond to each allowed
    # axis rotation, from wherever the player has got to.
    for seed in range(20):
        poses = poses_of(game.generate_main(seed).payload)
        for index, pose in enumerate(poses):
            for at in range(len(TURNS)):
                assert poses[NEIGHBOURS[index][at]] != pose


def test_target_shadows_pin_down_at_most_the_level_allowance():
    for level in (1, 5, 10):
        allowed = _params_for_level(level)["max_equivalent"]
        for seed in range(20):
            payload = game.generate_main(seed, level=level).payload
            assert 1 <= len(accepting_of(payload)) <= allowed


def test_start_is_exactly_the_level_distance_from_solved():
    for level in (1, 5, 10):
        wanted = _params_for_level(level)["distance"]
        for seed in range(20):
            payload = game.generate_main(seed, level=level).payload
            far = _distances(accepting_of(payload))
            assert far[payload["initial_orientation"]] == wanted
            assert len(json.loads(
                game.generate_main(seed, level=level).answer
            )["turns"]) == wanted


def test_the_start_never_already_matches():
    for seed in range(50):
        payload = game.generate_main(seed).payload
        start = pairs_of(payload)[payload["initial_orientation"]]
        assert start != target_pair(payload)


def test_silhouettes_are_readable():
    for seed in range(50):
        payload = game.generate_main(seed).payload
        cells = payload["bound"] ** 2
        for grid in (payload["targets"]["front"], payload["targets"]["top"]):
            filled = sum(row.count("1") for row in grid)
            assert MIN_FILLED <= filled <= cells - MIN_EMPTY


def test_holding_materially_smaller():
    main, hold = game.generate_main(3), game.generate_holding(3)
    assert hold.payload["bound"] < main.payload["bound"]
    assert len(hold.payload["voxels"]) < len(main.payload["voxels"])
    assert hold.payload["action_cap"] == HOLD_ACTION_CAP
    assert len(json.loads(hold.answer)["turns"]) == HOLD_DISTANCE
    assert len(hold.payload["voxels"]) == HOLD_VOXELS
    assert hold.payload["bound"] == HOLD_BOUND


# --- Solving rules ------------------------------------------------------


def test_every_projection_equivalent_orientation_passes():
    # The spec's acceptance test: a known projection-equivalent alternate
    # sequence passes. Several orientations usually cast the same shadows.
    shared = 0
    for seed in range(30):
        board = game.generate_main(seed)
        routes = routes_from(board.payload["initial_orientation"])
        accepting = accepting_of(board.payload)
        if len(accepting) > 1:
            shared += 1
        for index in accepting:
            assert game.check(board, answer(*routes[index])) is True
    assert shared, "no seed produced a board with an equivalent solved pose"


def test_a_loop_back_to_the_same_orientation_still_passes():
    # Only the final shadows are judged, so four quarter turns about one axis
    # tacked on the end change nothing.
    for seed in range(10):
        board = game.generate_main(seed)
        turns = json.loads(board.answer)["turns"]
        assert game.check(board, answer(*(turns + ["x+"] * 4))) is True
        assert game.check(board, answer(*(["z-"] * 4 + turns))) is True


def test_a_wrong_orientation_fails():
    for seed in range(20):
        board = game.generate_main(seed)
        routes = routes_from(board.payload["initial_orientation"])
        accepting = accepting_of(board.payload)
        wrong = [index for index in routes if index not in accepting]
        assert wrong
        for index in wrong[:6]:
            assert game.check(board, answer(*routes[index])) is False


def test_a_mirrored_shape_cannot_cast_the_target_shadows():
    # The spec's acceptance test. The screw tetracube is chiral: its reflection
    # is not any of its 24 rotations, so a target taken from the reflection is
    # unreachable — which is exactly what would break if normalising ever
    # mirrored the object.
    screw = _normalise([(0, 0, 0), (1, 0, 0), (1, 1, 0), (1, 1, 1)])
    poses = {_transform(screw, matrix) for matrix in ORIENTATIONS}
    reflection = mirrored(screw)
    assert reflection not in poses

    ours = {_project(pose, HOLD_BOUND) for pose in poses}
    unreachable = [
        _project(_transform(reflection, matrix), HOLD_BOUND)
        for matrix in ORIENTATIONS
    ]
    unreachable = [pair for pair in unreachable if pair not in ours]
    assert unreachable, "the reflection casts only reachable shadows"

    front, top = unreachable[0]
    board = puzzle(screw, 0, HOLD_BOUND, front, top)
    routes = routes_from(0)
    for turns in routes.values():
        assert game.check(board, answer(*(turns or ["x+"]))) is False


# --- Checker contract ---------------------------------------------------


def test_more_turns_than_the_action_cap_fail():
    board = game.generate_main(5)
    cap = board.payload["action_cap"]
    assert game.check(board, answer(*(["x+"] * (cap + 1)))) is False


def test_unknown_turn_tokens_fail():
    board = game.generate_main(4)
    turns = json.loads(board.answer)["turns"]
    for bad in ("w+", "x", "X+", "x++", "", "x-1"):
        assert game.check(board, answer(*(turns + [bad]))) is False


def test_client_cannot_claim_a_solved_board():
    board = game.generate_main(6)
    targets = board.payload["targets"]
    for bluff in (
        json.dumps({"v": 1, "turns": [], "solved": True}),
        json.dumps({"v": 1, "solved": True}),
        json.dumps({"v": 1, "turns": ["x+"], "orientation": 0, "matched": True}),
        json.dumps({"v": 1, "turns": ["x+"], "front": targets["front"], "top": targets["top"]}),
    ):
        assert game.check(board, bluff) is False


def test_malformed_answers_fail_safely():
    board = game.generate_main(1)
    turns = json.loads(board.answer)["turns"]
    for bad in (
        "",
        "   ",
        "not json",
        '{"v":1,"turns":',                                      # broken JSON
        json.dumps({"v": 2, "turns": turns}),                   # wrong version
        json.dumps({"v": 1}),                                   # missing turns
        json.dumps({"v": 1, "turns": "x+"}),                    # wrong type
        json.dumps({"v": 1, "turns": []}),                      # nothing played
        json.dumps({"v": 1, "turns": [["x", "+"]]}),            # not strings
        json.dumps({"v": 1, "turns": [1]}),
        json.dumps({"v": 1, "turns": [None]}),
        json.dumps([1, 2, 3]),                                  # not an object
        json.dumps({"v": 1, "turns": turns}) + " " * 500,       # oversized
    ):
        assert game.check(board, bad) is False, bad


def test_replay_reports_each_turn():
    board = game.generate_main(2)
    turns = json.loads(board.answer)["turns"]
    walk = replay(board.payload, turns)
    assert walk["legal"] is True
    assert walk["matched"] is True
    assert len(walk["steps"]) == len(turns)
    # Only the last turn lands it; the rest are on the way.
    assert [step["matched"] for step in walk["steps"][:-1]] == [False] * (len(turns) - 1)
    assert walk["steps"][-1]["matched"] is True


def test_no_solution_in_public_payload():
    for seed in range(5):
        board = game.generate_main(seed)
        public = board.public()
        assert "answer" not in public
        assert set(public["payload"]) == {
            "variant", "difficulty", "time_hint_seconds", "rules_version",
            "voxels", "initial_orientation", "bound", "targets", "action_cap",
        }
        # The shape and the targets are public by design — the turn list is not.
        assert board.answer not in json.dumps(public)
        assert set(public["payload"]["targets"]) == {"front", "top"}


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
    assert params["voxels"] == MAIN_VOXELS
    assert params["max_equivalent"] == MAIN_EQUIVALENT
    assert params["distance"] == MAIN_DISTANCE
    assert game.generate_main(42, level=1).payload == game.generate_main(42).payload


def test_level_params_monotonic():
    for easier, harder in zip(MAIN_LEVEL_PARAMS, MAIN_LEVEL_PARAMS[1:]):
        for knob in ("voxels", "distance", "difficulty", "time_hint"):
            assert easier[knob] <= harder[knob]
        # Fewer equivalent poses is *harder*, so this knob falls.
        assert easier["max_equivalent"] >= harder["max_equivalent"]
        assert harder["voxels"] <= 10        # the spec's shape ceiling
        assert harder["distance"] <= 3       # the rotation graph's diameter
        assert harder["max_equivalent"] >= 1


def test_levels_out_of_range_are_clamped():
    assert _params_for_level(0) == _params_for_level(1)
    assert _params_for_level(99) == _params_for_level(10)


def test_every_level_generates_scaled_boards():
    for level in range(1, 11):
        params = _params_for_level(level)
        for seed in (3, 44):
            board = game.generate_main(seed, level=level)
            payload = board.payload
            assert len(payload["voxels"]) == params["voxels"]
            assert payload["bound"] == MAIN_BOUND
            assert payload["action_cap"] == MAIN_ACTION_CAP
            assert payload["difficulty"] == params["difficulty"]
            assert game.check(board, board.answer) is True


def test_level_ten_visibly_harder():
    top = _params_for_level(10)
    assert top["voxels"] > MAIN_VOXELS
    assert top["distance"] > MAIN_DISTANCE
    assert top["max_equivalent"] < MAIN_EQUIVALENT
    assert top["difficulty"] > _params_for_level(1)["difficulty"]
