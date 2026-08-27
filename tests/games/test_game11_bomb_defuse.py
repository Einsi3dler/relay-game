"""BOMB DEFUSE (game 11) — generation, the replay, and the level curve.

Per `bomb.md` and the module contract in docs/GAME_MODULE_SPEC.md §8. The
Python/JavaScript agreement on the replay itself lives in
`test_game11_bomb_defuse_parity.py`; the renderer lifecycle in
`test_game11_bomb_defuse_renderer.py`.
"""

from __future__ import annotations

import json
import time

import pytest

from backend.games.base import normalize_answer
from backend.games.game11_bomb_defuse import (
    BAY_COUNT, MAIN_LEVEL_PARAMS, MAX_ANSWER_CHARS, MAX_MOVES, MAZE_LAYOUTS,
    MAZE_SIZE, MODULE_TYPES, NUMBER_PATTERNS, RULES_VERSION, SIMON_COLOURS,
    SIMON_MAP, BombDefuseGame, _maze_distances, _maze_route, _number_answer,
    _pattern_for_tip, _reference_moves, _wall_between, validate,
)

LEVELS = range(1, len(MAIN_LEVEL_PARAMS) + 1)


@pytest.fixture()
def game() -> BombDefuseGame:
    return BombDefuseGame()


def moves_of(puzzle) -> list[dict]:
    return json.loads(puzzle.answer)["moves"]


def bays_of(payload: dict) -> list[dict]:
    """Every bay on the board, across every bank."""
    return [module for bank in payload["banks"] for module in bank["modules"]]


def banks_of(level: int) -> list[tuple[int, int]]:
    return MAIN_LEVEL_PARAMS[level - 1]["banks"]


# --- the static data the manual and the bomb share ----------------------


def test_every_number_pattern_holds_one_to_nine_once():
    for pattern in NUMBER_PATTERNS:
        assert sorted(value for row in pattern for value in row) == list(range(1, 10))


def test_each_number_pattern_is_named_by_a_different_green_one():
    tips = [
        (row, col)
        for pattern in NUMBER_PATTERNS
        for row in range(3)
        for col in range(3)
        if pattern[row][col] == 1
    ]
    assert len(set(tips)) == len(NUMBER_PATTERNS)
    # ...and that tip is what resolves a payload back to its grid.
    for pattern, tip in zip(NUMBER_PATTERNS, tips):
        assert _pattern_for_tip(tip) == pattern


def test_each_maze_is_named_by_a_different_green_tip():
    tips = [layout["tip"] for layout in MAZE_LAYOUTS]
    assert len(set(tips)) == len(MAZE_LAYOUTS)


def test_every_maze_cell_is_reachable_from_every_other():
    # Spanning trees, so no generated start/goal pair can ever be unsolvable.
    for layout in MAZE_LAYOUTS:
        for row in range(MAZE_SIZE):
            for col in range(MAZE_SIZE):
                assert len(_maze_distances(layout, (row, col))) == MAZE_SIZE ** 2


def test_simon_mapping_never_sends_a_colour_to_itself():
    assert set(SIMON_MAP) == set(SIMON_COLOURS)
    for flashed, pressed in SIMON_MAP.items():
        assert pressed != flashed
        assert SIMON_MAP[pressed] == flashed      # the mapping is its own inverse


def test_number_answer_reads_the_configured_axis():
    pattern = NUMBER_PATTERNS[0]                  # 1 6 3 / 8 2 4 / 5 9 7
    assert _number_answer(pattern, 3, "column") == 3      # bomb.md §63
    assert _number_answer(pattern, 2, "column") == 2
    assert _number_answer(pattern, 3, "row") == 1
    assert _number_answer(pattern, 99, "column") is None


# --- the module contract ------------------------------------------------


def test_generation_is_deterministic_in_seed_and_level(game):
    for level in LEVELS:
        one, two = game.generate_main(4242, level), game.generate_main(4242, level)
        assert one.payload == two.payload
        assert one.prompt == two.prompt and one.answer == two.answer
        assert one.id != two.id                   # instance ids are always fresh
        assert one.kind == "main" and one.game_id == "bomb_defuse"


def test_different_seeds_give_different_bombs(game):
    boards = {json.dumps(game.generate_main(seed, 8).payload) for seed in range(20)}
    assert len(boards) >= 18


def test_the_reference_defusal_is_accepted(game):
    for seed in range(40):
        for level in LEVELS:
            puzzle = game.generate_main(seed, level)
            assert game.check(puzzle, puzzle.answer) is True
            assert game.check(puzzle, f"  {puzzle.answer}  ") is True


def test_a_wrong_answer_is_rejected(game):
    puzzle = game.generate_main(5, 6)
    assert game.check(puzzle, "definitely-wrong") is False
    assert game.check(puzzle, json.dumps({"v": RULES_VERSION, "moves": []})) is False


def test_malformed_answers_are_wrong_and_never_raise(game):
    puzzle = game.generate_main(9, 4)
    for answer in [
        "", "   ", "{", "[]", "null", "42", '{"v":1}', '{"v":2,"moves":[]}',
        '{"moves":[{"m":"m0","a":"n"}]}', '{"v":1,"moves":"nope"}',
        '{"v":1,"moves":[[1,2]]}', '{"v":1,"moves":[{"m":null}]}',
        '{"v":1,"moves":[{"m":"ok"},{"m":"ok"}]}', "NaN",
        json.dumps({"v": 1, "moves": [{"m": "ok"}] * (MAX_MOVES + 1)}),
        "x" * (MAX_ANSWER_CHARS + 1),
    ]:
        assert game.check(puzzle, answer) is False


def test_a_detonation_report_is_rejected_so_the_engine_reissues(game):
    # The renderer's own "it went off" submission. It has to be wrong: a
    # rejected board is how bomb.md §20's fresh bomb gets asked for.
    puzzle = game.generate_main(3, 3)
    assert game.check(puzzle, json.dumps({"v": RULES_VERSION, "failed": "maze_wall"})) is False
    assert game.check(
        puzzle,
        json.dumps({"v": RULES_VERSION, "failed": "give-up", "moves": moves_of(puzzle)}),
    ) is False


def test_public_payload_carries_no_reference_transcript(game):
    # The board is a manual lookup, so its answer is derivable from the public
    # manual data (documented in docs/GAMES_SPEC.md) — but the transcript the
    # server generated is never shipped, and no bay reports itself solved.
    for seed in range(20):
        puzzle = game.generate_main(seed, 11)
        public = json.dumps(puzzle.public())
        assert "answer" not in puzzle.public()
        assert normalize_answer(puzzle.answer) not in normalize_answer(public)
        assert "moves" not in public and "solved" not in public


def test_holding_is_a_smaller_bomb(game):
    for seed in range(20):
        holding = game.generate_holding(seed)
        assert holding.kind == "holding"
        assert game.check(holding, holding.answer) is True
        assert len(holding.payload["banks"]) == 1        # never escalates
        assert len(bays_of(holding.payload)) == 1
        assert holding.payload["banks"][0]["fuse_seconds"] < \
            MAIN_LEVEL_PARAMS[0]["banks"][0][1]
        assert game.generate_holding(seed).payload == holding.payload
    # A mini-button bay is one move whatever size it is, so the comparison that
    # means anything is the work across a spread of seeds, not one board.
    def work(boards) -> int:
        return sum(len(moves_of(board)) for board in boards)

    assert work(game.generate_holding(seed) for seed in range(20)) < \
        work(game.generate_main(seed, 1) for seed in range(20))


def test_reset_is_a_no_op(game):
    before = game.generate_main(77, 5).payload
    game.reset()
    assert game.generate_main(77, 5).payload == before


# --- the board itself ---------------------------------------------------


def test_a_board_never_asks_the_same_question_twice(game):
    for seed in range(60):
        for level in LEVELS:
            payload = game.generate_main(seed, level).payload
            # §13 is a per-bank rule: a bank never asks the same question
            # twice, but a later bank may reuse a type behind a shut shutter.
            for bank in payload["banks"]:
                types = [module["type"] for module in bank["modules"]]
                bays = [module["bay"] for module in bank["modules"]]
                assert len(set(types)) == len(types)      # bomb.md §13
                assert len(set(bays)) == len(bays)
                assert all(0 <= bay < BAY_COUNT for bay in bays)
            # Ids are unique board-wide, which is what lets a stale move from a
            # shut bank still resolve to a real bay and be refused correctly.
            ids = [module["id"] for module in bays_of(payload)]
            assert ids == [f"m{index}" for index in range(len(ids))]


def test_every_module_type_turns_up(game):
    seen = set()
    for seed in range(40):
        seen.update(
            module["type"] for module in bays_of(game.generate_main(seed, 6).payload)
        )
    assert seen == set(MODULE_TYPES)


def test_maze_bays_are_walkable_and_never_sit_on_the_tip(game):
    checked = 0
    for seed in range(120):
        for level in LEVELS:
            payload = game.generate_main(seed, level).payload
            for module in bays_of(payload):
                if module["type"] != "maze":
                    continue
                checked += 1
                low, high = MAIN_LEVEL_PARAMS[level - 1]["maze_moves"]
                layout = next(
                    entry for entry in MAZE_LAYOUTS
                    if list(entry["tip"]) == module["tip"]
                )
                route = _maze_route(
                    layout, tuple(module["player"]), tuple(module["goal"])
                )
                assert route is not None
                assert low <= len(route) <= high     # the level's step range
                assert module["player"] != module["tip"]   # §37: a label, not a target
                assert module["goal"] != module["tip"]
                assert module["player"] != module["goal"]
    assert checked > 50


def test_simon_bays_flash_the_right_number_of_colours_and_no_triples(game):
    for seed in range(120):
        for level in LEVELS:
            for module in bays_of(game.generate_main(seed, level).payload):
                if module["type"] != "simon":
                    continue
                sequence = module["sequence"]
                assert module["stages"] == MAIN_LEVEL_PARAMS[level - 1]["simon_stages"]
                assert len(sequence) == module["stages"]
                assert all(colour in SIMON_COLOURS for colour in sequence)
                triples = zip(sequence, sequence[1:], sequence[2:])
                assert not any(a == b == c for a, b, c in triples)


def test_number_bays_never_repeat_a_display_back_to_back(game):
    for seed in range(120):
        for level in LEVELS:
            for module in bays_of(game.generate_main(seed, level).payload):
                if module["type"] != "according_to_number":
                    continue
                displays = module["displays"]
                assert len(displays) == MAIN_LEVEL_PARAMS[level - 1]["atn_stages"]
                assert all(1 <= shown <= 9 for shown in displays)
                assert all(a != b for a, b in zip(displays, displays[1:]))   # §65
                assert _pattern_for_tip(tuple(module["tip"])) is not None


def test_mini_button_bays_carry_the_level_s_timings(game):
    for seed in range(120):
        for level in LEVELS:
            for module in bays_of(game.generate_main(seed, level).payload):
                if module["type"] != "mini_button":
                    continue
                params = MAIN_LEVEL_PARAMS[level - 1]
                assert 2000 <= module["delay_ms"] <= 6000          # §53
                assert module["reaction_window_ms"] == params["react_ms"]
                assert module["required_hold_ms"] == params["hold_ms"]
                assert 10 <= module["code"] <= 99


# --- sudden death, straight through validate ----------------------------


def find_module(payload: dict, module_type: str) -> dict | None:
    return next(
        (module for module in bays_of(payload) if module["type"] == module_type), None
    )


def board_with(game: BombDefuseGame, module_type: str, level: int = 13):
    """The first generated board at `level` that fields `module_type`."""
    for seed in range(400):
        puzzle = game.generate_main(seed, level)
        module = find_module(puzzle.payload, module_type)
        if module is not None:
            return puzzle, module
    raise AssertionError(f"no {module_type} board found")


def test_walking_into_a_wall_ends_the_run(game):
    puzzle, module = board_with(game, "maze")
    layout = next(
        entry for entry in MAZE_LAYOUTS if list(entry["tip"]) == module["tip"]
    )
    start = (module["player"][0], module["player"][1])
    blocked = [side for side in "nsew" if _wall_between(layout, start, side)]
    assert blocked, "a 4x4 spanning tree always walls at least one side of a cell"
    for side in blocked:
        result = validate(puzzle.payload, [{"m": module["id"], "a": side}], True)
        assert result["ok"] is False and result["reason"] == "maze_wall"
    # ...and the sides that are open are simply steps.
    for side in set("nsew") - set(blocked):
        assert validate(puzzle.payload, [{"m": module["id"], "a": side}], True)["ok"]


def test_pressing_ok_early_ends_the_run(game):
    puzzle = game.generate_main(21, 10)
    assert validate(puzzle.payload, [{"m": "ok"}])["reason"] == "premature_ok"
    # ...and so does OK with only some of the bays shut.
    moves = moves_of(puzzle)
    partway = moves.index({"m": "ok"}) - 1
    assert validate(puzzle.payload, moves[:partway] + [{"m": "ok"}])["reason"] == "premature_ok"


def test_a_defusal_needs_the_ok_press(game):
    puzzle = game.generate_main(21, 10)
    moves = moves_of(puzzle)[:-1]
    assert validate(puzzle.payload, moves)["reason"] == "missing_ok"
    assert validate(puzzle.payload, moves, True)["ok"] is True   # ...but survivable
    assert validate(puzzle.payload, moves + [{"m": "ok"}])["ok"] is True


def test_bays_may_be_worked_in_any_order(game):
    puzzle, _ = board_with(game, "maze", level=13)
    moves = moves_of(puzzle)
    first_ok = moves.index({"m": "ok"})
    by_module: dict[str, list[dict]] = {}
    for move in moves[:first_ok]:
        by_module.setdefault(move["m"], []).append(move)
    # Interleave one move from each open bay at a time — a real player flipping
    # between bays, which must be worth exactly as much as doing them in turn.
    interleaved: list[dict] = []
    while any(by_module.values()):
        for queue in by_module.values():
            if queue:
                interleaved.append(queue.pop(0))
    rest = moves[first_ok:]
    assert validate(puzzle.payload, interleaved + rest)["ok"] is True


def test_banks_may_not_be(game):
    """Bays inside a bank are free; the banks themselves are strictly ordered."""
    puzzle, _ = board_with(game, "maze", level=13)
    payload = puzzle.payload
    assert len(payload["banks"]) > 1, "level 13 comes in banks"
    moves = moves_of(puzzle)
    first_ok = moves.index({"m": "ok"})

    # A second-bank bay cannot be pre-solved while the first is still armed...
    ahead = moves[first_ok + 1]
    assert validate(payload, [ahead], True)["reason"] == "wrong_bank"
    # ...and a first-bank bay is dead once its bank has shut behind it.
    stale = validate(payload, moves[: first_ok + 1] + [moves[0]], True)
    assert stale["reason"] == "wrong_bank"
    # The OK that shuts a bank arms the next one rather than ending the bomb.
    shut = validate(payload, moves[: first_ok + 1], True)
    assert shut["ok"] is True and shut["defused"] is False and shut["bank"] == 1
    assert validate(payload, moves[: first_ok + 1])["reason"] == "missing_ok"


def test_a_shut_bay_takes_no_further_input(game):
    puzzle, module = board_with(game, "mini_button")
    moves = [{"m": module["id"], "a": module["code"]}] * 2
    assert validate(puzzle.payload, moves, True)["reason"] == "already_solved"


def test_the_replay_reports_progress_per_bay(game):
    puzzle, module = board_with(game, "according_to_number")
    first = next(move for move in moves_of(puzzle) if move["m"] == module["id"])
    result = validate(puzzle.payload, [first], True)
    assert result["ok"] is True
    assert result["state"][module["id"]] == {"solved": False, "stage": 1}


# --- the level curve ----------------------------------------------------


def test_the_table_has_a_row_per_level_including_the_bonus_tiers():
    # LEVEL_COUNT + BONUS_LEVEL_OFFSET rows: 10 played, 11..13 bonus-only.
    from backend import config
    assert len(MAIN_LEVEL_PARAMS) == config.LEVEL_COUNT + config.BONUS_LEVEL_OFFSET


def test_level_one_is_the_source_game_s_easy_bomb(game):
    params = MAIN_LEVEL_PARAMS[0]
    assert params["banks"] == [(1, 90)]           # bomb.md §10, easy: one bay
    assert params["simon_stages"] == 4            # §46
    assert params["atn_stages"] == 4              # §64
    assert params["react_ms"] == 700              # §54
    assert params["hold_ms"] == 750               # §55
    for seed in range(30):
        payload = game.generate_main(seed, 1).payload
        assert len(payload["banks"]) == 1
        assert len(bays_of(payload)) == 1


def test_the_knobs_only_ever_tighten():
    rows = MAIN_LEVEL_PARAMS
    for earlier, later in zip(rows, rows[1:]):
        assert len(later["banks"]) >= len(earlier["banks"])
        assert sum(count for count, _ in later["banks"]) >= \
            sum(count for count, _ in earlier["banks"])
        assert later["simon_stages"] >= earlier["simon_stages"]
        assert later["atn_stages"] >= earlier["atn_stages"]
        assert later["maze_moves"] >= earlier["maze_moves"]
        assert later["react_ms"] <= earlier["react_ms"]     # less time to react
        assert later["hold_ms"] >= earlier["hold_ms"]       # longer to hold
        assert later["difficulty"] >= earlier["difficulty"]
        assert later["time_hint"] >= earlier["time_hint"]


def test_the_fuse_tightens_inside_each_band_and_never_covers_less_work():
    # A fuse is only difficulty relative to the work it has to cover, so the
    # opening bank's fuse rises where a bay is added and tightens level by
    # level after that.
    rows = MAIN_LEVEL_PARAMS

    def shape(row):
        return [count for count, _ in row["banks"]]

    for earlier, later in zip(rows, rows[1:]):
        if shape(later) == shape(earlier):
            # Same bomb, less time: this is where the pressure comes from.
            fuses = list(zip(earlier["banks"], later["banks"]))
            assert all(after <= before for (_, before), (_, after) in fuses)
            assert any(after < before for (_, before), (_, after) in fuses)
        else:
            # A wider or deeper bomb buys time back, or it is not hard, it is
            # impossible.
            assert sum(f for _, f in later["banks"]) > \
                sum(f for _, f in earlier["banks"])
    for row in rows:
        # A later bank is always smaller and faster than the one that armed it.
        for (before_count, before_fuse), (after_count, after_fuse) in zip(
            row["banks"], row["banks"][1:]
        ):
            assert after_count <= before_count
            assert after_fuse < before_fuse
        # And the whole board still leaves real room over the expected solve.
        assert sum(fuse for _, fuse in row["banks"]) > row["time_hint"]


def test_the_bonus_tiers_go_past_level_ten():
    played, bonus = MAIN_LEVEL_PARAMS[9], MAIN_LEVEL_PARAMS[10:]
    # Levels 1..10 are a single bank; the bonus-only tiers are where a second
    # arms behind the first, which is what makes them a different board rather
    # than just a wider one.
    assert all(len(row["banks"]) == 1 for row in MAIN_LEVEL_PARAMS[:10])
    assert all(len(row["banks"]) > 1 for row in bonus)
    def total(row):
        return sum(count for count, _ in row["banks"])

    assert all(total(row) > total(played) for row in bonus)
    # A bank still cannot ask more than the four questions that exist.
    assert all(
        count <= len(MODULE_TYPES) for row in MAIN_LEVEL_PARAMS
        for count, _ in row["banks"]
    )
    assert bonus[-1]["hold_ms"] > played["hold_ms"]
    assert bonus[-1]["react_ms"] < played["react_ms"]


def test_levels_outside_the_table_clamp(game):
    assert game.generate_main(5, 0).payload == game.generate_main(5, 1).payload
    assert game.generate_main(5, 99).payload == \
        game.generate_main(5, len(MAIN_LEVEL_PARAMS)).payload


def test_a_deep_board_is_measurably_bigger_than_a_level_one_board(game):
    def work(level: int) -> float:
        return sum(
            len(_reference_moves(game.generate_main(seed, level).payload))
            for seed in range(30)
        ) / 30

    assert work(13) > work(10) > work(5) > work(1)


def test_generation_stays_fast(game):
    # A bonus board is generated synchronously when a player opts in.
    started = time.perf_counter()
    for seed in range(60):
        game.generate_main(seed, 13)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"60 level-13 boards took {elapsed:.2f}s"


def test_a_defusal_transcript_fits_the_answer_caps(game):
    for seed in range(60):
        puzzle = game.generate_main(seed, 13)
        assert len(puzzle.answer) <= MAX_ANSWER_CHARS
        assert len(moves_of(puzzle)) <= MAX_MOVES
