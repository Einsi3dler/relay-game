"""T4.x — MIRROR RUN: module-spec §8 suite + the expansion spec's minimum
acceptance tests (game/RELAY_EXPANSION_GAMES_README.md §1)."""

from __future__ import annotations

import json
from collections import deque

from backend.games.game5_mirror_run import (
    HOLD_SIZE,
    MAIN_DEPTH,
    MAIN_MOVE_CAP,
    MAIN_SIZE,
    MAIN_WALL_P,
    MAPPINGS,
    MirrorRunGame,
    _params_for_level,
    _solve,
    _step,
)

game = MirrorRunGame()


def good_answer(seed: int, kind: str = "main", level: int = 1) -> str:
    _, solution = game._build(seed, kind, level)
    return json.dumps({"v": 1, "moves": solution})


def test_determinism():
    a, b = game.generate_main(42), game.generate_main(42)
    assert a.payload == b.payload
    assert a.answer == b.answer


def test_different_seeds_differ():
    boards = {str(game.generate_main(seed).payload["boards"]) for seed in range(15)}
    assert len(boards) > 1


def test_generated_boards_are_solvable_and_unsolved():
    for seed in range(10):
        puzzle = game.generate_main(seed)
        boards = puzzle.payload["boards"]
        assert boards[0]["start"] != boards[0]["exit"] or boards[1]["start"] != boards[1]["exit"]
        assert game.check(puzzle, good_answer(seed)) is True


def test_main_depth_in_band_and_under_cap():
    # The primary band can spill into the relaxed band by design, so the
    # relaxed band is the hard bound at every level.
    for level in (1, 5, 10):
        params = _params_for_level(level)
        lo, hi = params["relaxed_depth"]
        for seed in range(10):
            puzzle = game.generate_main(seed, level=level)
            assert lo <= len(puzzle.answer) <= hi
            assert len(puzzle.answer) <= puzzle.payload["move_cap"]
            assert puzzle.payload["move_cap"] == params["move_cap"]


def test_holding_materially_smaller():
    main, hold = game.generate_main(3), game.generate_holding(3)
    assert hold.payload["rows"] < main.payload["rows"]
    assert len(hold.answer) < len(main.answer)
    assert hold.payload["move_cap"] < main.payload["move_cap"]


def test_incomplete_solution_fails():
    seed = 4
    puzzle = game.generate_main(seed)
    partial = puzzle.answer[:-1]
    assert game.check(puzzle, json.dumps({"v": 1, "moves": partial})) is False


def test_solving_only_one_board_fails():
    # A path that parks Runner A on its exit while ignoring B must not pass.
    for seed in range(8):
        puzzle = game.generate_main(seed)
        p = puzzle.payload
        board_a = p["boards"][0]
        walls = frozenset(map(tuple, board_a["walls"]))
        start, goal = tuple(board_a["start"]), tuple(board_a["exit"])
        # BFS on board A alone (ignores B entirely).
        seen, queue = {start}, deque([(start, "")])
        path = None
        while queue:
            pos, moves = queue.popleft()
            if pos == goal:
                path = moves
                break
            for command in "URDL":
                nxt = _step(pos, command, walls, p["rows"])
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append((nxt, moves + command))
        assert path is not None
        if game.check(puzzle, json.dumps({"v": 1, "moves": path})) is False:
            return  # found a seed where the A-only path leaves B stranded
    raise AssertionError("A-only paths passed on every sampled seed")


def test_blocked_moves_are_legal():
    # A command that moves neither runner must not invalidate the sequence.
    seed = 6
    puzzle = game.generate_main(seed)
    p = puzzle.payload
    # Find a command that moves neither runner from the start (may not exist
    # on every board — walk seeds until one is found).
    for probe_seed in range(6, 30):
        puzzle = game.generate_main(probe_seed)
        p = puzzle.payload
        walls_a = frozenset(map(tuple, p["boards"][0]["walls"]))
        walls_b = frozenset(map(tuple, p["boards"][1]["walls"]))
        start_a, start_b = tuple(p["boards"][0]["start"]), tuple(p["boards"][1]["start"])
        mapping = MAPPINGS[p["mapping_b"]]
        for command in "URDL":
            if (
                _step(start_a, command, walls_a, p["rows"]) == start_a
                and _step(start_b, mapping[command], walls_b, p["rows"]) == start_b
            ):
                # no-op prefix + real solution must still pass
                answer = json.dumps({"v": 1, "moves": command + puzzle.answer})
                if len(command + puzzle.answer) <= p["move_cap"]:
                    assert game.check(puzzle, answer) is True
                return
    raise AssertionError("no fully-blocked command found on any sampled seed")


def test_malformed_answers_fail_safely():
    puzzle = game.generate_main(1)
    for bad in (
        "",
        "URDL",  # not JSON
        json.dumps({"v": 2, "moves": puzzle.answer}),  # wrong version
        json.dumps({"v": 1}),  # missing moves
        json.dumps({"v": 1, "moves": 123}),  # wrong type
        json.dumps({"v": 1, "moves": "URDX"}),  # unknown command
        json.dumps({"v": 1, "moves": "U" * 999}),  # over the cap
        json.dumps({"v": 1, "moves": ""}),  # empty
        '{"v":1,"moves":',  # broken JSON
    ):
        assert game.check(puzzle, bad) is False, bad


def test_final_coordinates_cannot_be_submitted():
    # The checker only accepts a move string; a coordinate claim is malformed.
    puzzle = game.generate_main(2)
    exits = [puzzle.payload["boards"][i]["exit"] for i in (0, 1)]
    assert game.check(puzzle, json.dumps({"v": 1, "final": exits})) is False


def test_no_solution_in_public_payload():
    puzzle = game.generate_main(7)
    public = puzzle.public()
    assert "answer" not in public
    assert set(public["payload"]) == {
        "variant", "difficulty", "time_hint_seconds", "rules_version",
        "rows", "cols", "boards", "mapping_b", "move_cap",
    }
    for board in public["payload"]["boards"]:
        assert set(board) == {"walls", "start", "exit"}


def test_both_boards_matter_on_shortest_path():
    # Spec: >= 70% of sampled boards need meaningful movement on both sides.
    # Our generation gate enforces it on 100% of served boards.
    for seed in range(10):
        puzzle = game.generate_main(seed)
        p = puzzle.payload
        walls_a = frozenset(map(tuple, p["boards"][0]["walls"]))
        walls_b = frozenset(map(tuple, p["boards"][1]["walls"]))
        pos_a, pos_b = tuple(p["boards"][0]["start"]), tuple(p["boards"][1]["start"])
        mapping = MAPPINGS[p["mapping_b"]]
        moved_a = moved_b = 0
        for command in puzzle.answer:
            nxt_a = _step(pos_a, command, walls_a, p["rows"])
            nxt_b = _step(pos_b, mapping[command], walls_b, p["rows"])
            moved_a += nxt_a != pos_a
            moved_b += nxt_b != pos_b
            pos_a, pos_b = nxt_a, nxt_b
        assert moved_a >= 0.4 * len(puzzle.answer)
        assert moved_b >= 0.4 * len(puzzle.answer)


def test_reset_safe_and_deterministic_after():
    before = game.generate_main(5).payload
    game.reset()
    assert game.generate_main(5).payload == before


def test_level_determinism():
    # V5 contract: same (seed, level) always yields the same puzzle.
    for level in (1, 5, 10):
        a = game.generate_main(42, level=level)
        b = game.generate_main(42, level=level)
        assert a.payload == b.payload and a.answer == b.answer


def test_level_one_matches_original_board():
    params = _params_for_level(1)
    assert params["size"] == MAIN_SIZE
    assert params["depth"] == MAIN_DEPTH
    assert params["wall_p"] == MAIN_WALL_P
    assert params["move_cap"] == MAIN_MOVE_CAP
    assert game.generate_main(42, level=1).payload == game.generate_main(42).payload


def test_level_params_monotonic():
    for level in range(1, 10):
        easier, harder = _params_for_level(level), _params_for_level(level + 1)
        assert easier["size"] <= harder["size"]
        assert easier["depth"][0] < harder["depth"][0]
        assert easier["wall_p"] <= harder["wall_p"]
        assert easier["move_cap"] <= harder["move_cap"]
        assert easier["difficulty"] <= harder["difficulty"]
        assert easier["time_hint"] <= harder["time_hint"]


def test_every_level_generates_solvable_scaled_boards():
    for level in range(1, 11):
        params = _params_for_level(level)
        for seed in (3, 44, 90):
            puzzle = game.generate_main(seed, level=level)
            assert puzzle.payload["rows"] == params["size"]
            assert game.check(puzzle, good_answer(seed, "main", level)) is True


def test_level_ten_visibly_harder():
    top = _params_for_level(10)
    assert top["size"] > MAIN_SIZE
    assert top["depth"][0] > MAIN_DEPTH[0]
