"""THREADLINE (Game 10, route construction): draw one cable from the start
socket to the end socket, through the numbered anchors in order, without
crossing itself and without spending more bends than the board allows.

Per game/RELAY_EXPANSION_GAMES_README.md §14. The board is a grid of cells; the
cable is a list of cells, each one orthogonally adjacent to the last. Version 1
takes the spec's strict readings: anchors must be reached in their declared
order (stepping onto a later one early is a failure, not a no-op), and *no*
nonterminal cell may be reused — which is a single rule that also rules out
self-crossing, edge reuse, and the 180-degree reversal the spec wants rejected.

Sides are named for the edge of a cell the cable crosses: stepping "n" leaves
through the north side of the cell it starts in and enters through the "s" side
of the cell it lands on. An anchor's `entry`/`exit` port, when present, names a
side of the anchor cell in exactly that sense, so a port is read the same way
whichever direction the player is travelling.

Generation is constructive: a self-avoiding reference route is drawn first,
anchors are taken from cells along it, and obstacles are then placed off it —
so every board ships with a route that satisfies every constraint, and no
board can be unsolvable. The reference route never reaches the client, and
`check` never compares against it: the server re-walks whatever the player
submits and judges it on the rules alone, so any legal route wins.

Two knobs make a board hard: `bend_cap` (how many direction changes the player
may spend) and how much of that cap is actually forced. `_min_bends` measures
the second one — a 0-1 BFS over `(cell, next anchor, heading)` that ignores
self-avoidance, so its answer is a true lower bound on what any legal route
must spend. Generation requires the cap to sit close to that bound (little
freedom) and requires the obstacles to *raise* it, which is what rejects the
spec's "obstacles do not influence routing" boards.
"""

from __future__ import annotations

import json
import random
from collections import deque

from backend.games.base import PuzzleInstance

RULES_VERSION = 1

# The four sides of a cell and the step that crosses each one.
STEPS: dict[str, tuple[int, int]] = {
    "n": (-1, 0), "s": (1, 0), "e": (0, 1), "w": (0, -1),
}
SIDE_OF_STEP = {step: side for side, step in STEPS.items()}
OPPOSITE = {"n": "s", "s": "n", "e": "w", "w": "e"}

# --- Level-1 board (the V5 baseline: generate_main(seed) == level 1) ---
MAIN_ROWS = 8                # the spec's 8x8 main board, at every level
MAIN_COLS = 8
MAIN_ANCHORS = 3
MAIN_BLOCKED = 5
MAIN_MIN_EDGES = 12
MAIN_MAX_EDGES = 18

HOLD_ROWS = 5
HOLD_COLS = 5
HOLD_ANCHORS = 1
HOLD_BLOCKED = 1

# The spec's bend-cap window for a main board; a generated cap outside it is
# rejected rather than clamped, because clamping down could put the cap under
# the reference route's own bend count and strand the board.
BEND_CAP_MIN = 6
BEND_CAP_MAX = 10

MIN_SOCKET_SPAN = 7          # manhattan start->end on a main board
HOLD_SOCKET_SPAN = 4

HUG_FRACTION = 0.6           # obstacles pressed against the reference corridor
GEN_ATTEMPTS = 400
ROUTE_BUDGET = 6000          # DFS steps per route attempt
SOLVE_BUDGET = 200_000       # DFS steps for the independent solver (tests)
MAX_ANSWER_CHARS = 1200      # ~30 cells of "[r, c], " and change

# Main difficulty curve (docs/TASK_LIST.md V5): one row per level 1..13, level 1
# == the board above. The route gets longer and turns more, obstacles and
# anchors multiply, ports start appearing at level 5 — and the two knobs that
# *tighten* are `bend_slack` (spare bends over the reference route, 3 -> 0) and
# `bend_freedom` (how far the cap may sit above the forced minimum, 4 -> 1).
# Levels 11..13 are BONUS-ONLY tiers, never served as a main board: `bend_slack`
# is already 0 by level 9, so they lean on route length, anchors and ports.
MAIN_LEVEL_PARAMS: tuple[dict, ...] = (
    {"anchors": 3, "blocked": 5, "edges": (12, 18), "min_bends": 4, "bend_slack": 3,
     "bend_freedom": 4, "edge_slack": 6, "ports": 0, "difficulty": 2, "time_hint": 30},  # 1
    {"anchors": 3, "blocked": 5, "edges": (12, 18), "min_bends": 4, "bend_slack": 3,
     "bend_freedom": 4, "edge_slack": 6, "ports": 0, "difficulty": 2, "time_hint": 30},  # 2
    {"anchors": 3, "blocked": 6, "edges": (13, 19), "min_bends": 5, "bend_slack": 2,
     "bend_freedom": 4, "edge_slack": 5, "ports": 0, "difficulty": 2, "time_hint": 33},  # 3
    {"anchors": 4, "blocked": 6, "edges": (13, 20), "min_bends": 5, "bend_slack": 2,
     "bend_freedom": 3, "edge_slack": 5, "ports": 0, "difficulty": 3, "time_hint": 36},  # 4
    {"anchors": 4, "blocked": 7, "edges": (14, 20), "min_bends": 5, "bend_slack": 2,
     "bend_freedom": 3, "edge_slack": 4, "ports": 1, "difficulty": 3, "time_hint": 40},  # 5
    {"anchors": 4, "blocked": 7, "edges": (14, 21), "min_bends": 6, "bend_slack": 1,
     "bend_freedom": 3, "edge_slack": 4, "ports": 1, "difficulty": 3, "time_hint": 43},  # 6
    {"anchors": 4, "blocked": 8, "edges": (15, 22), "min_bends": 6, "bend_slack": 1,
     "bend_freedom": 2, "edge_slack": 3, "ports": 1, "difficulty": 4, "time_hint": 46},  # 7
    {"anchors": 5, "blocked": 8, "edges": (15, 22), "min_bends": 6, "bend_slack": 1,
     "bend_freedom": 2, "edge_slack": 3, "ports": 2, "difficulty": 4, "time_hint": 50},  # 8
    {"anchors": 5, "blocked": 9, "edges": (16, 23), "min_bends": 7, "bend_slack": 0,
     "bend_freedom": 2, "edge_slack": 2, "ports": 2, "difficulty": 5, "time_hint": 53},  # 9
    {"anchors": 5, "blocked": 10, "edges": (16, 24), "min_bends": 7, "bend_slack": 0,
     "bend_freedom": 2, "edge_slack": 2, "ports": 2, "difficulty": 5, "time_hint": 56},  # 10
    {"anchors": 5, "blocked": 11, "edges": (17, 25), "min_bends": 7, "bend_slack": 0,
     "bend_freedom": 2, "edge_slack": 2, "ports": 3, "difficulty": 5, "time_hint": 60},  # 11 bonus
    {"anchors": 6, "blocked": 11, "edges": (17, 26), "min_bends": 8, "bend_slack": 0,
     "bend_freedom": 1, "edge_slack": 1, "ports": 3, "difficulty": 5, "time_hint": 64},  # 12 bonus
    {"anchors": 6, "blocked": 12, "edges": (18, 26), "min_bends": 8, "bend_slack": 0,
     "bend_freedom": 1, "edge_slack": 1, "ports": 3, "difficulty": 5, "time_hint": 68},  # 13 bonus
)

# Holding is practice-mode only: a short route, one obstacle, a roomy cap, and
# no forced-bend gate to satisfy.
HOLDING_PARAMS = {
    "anchors": HOLD_ANCHORS, "blocked": HOLD_BLOCKED, "edges": (5, 8), "min_bends": 2,
    "bend_slack": 3, "bend_freedom": 99, "edge_slack": 4, "ports": 0,
    "difficulty": 1, "time_hint": 10,
}


def _params_for_level(level: int) -> dict:
    """Main-board knobs for `level`, clamped to the 1..10 table."""
    return MAIN_LEVEL_PARAMS[min(max(level, 1), len(MAIN_LEVEL_PARAMS)) - 1]


Cell = tuple[int, int]


# --- Validation (shared by check(), the solver, and the JS parity fixture) ---


def _as_cells(path: object) -> list[Cell] | None:
    """`path` as (row, col) tuples, or None if it isn't a list of int pairs.

    `bool` is an `int` subclass, so `[True, False]` would otherwise sail
    through as the cell (1, 0).
    """
    if not isinstance(path, list) or not path:
        return None
    cells: list[Cell] = []
    for item in path:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        row, col = item
        if isinstance(row, bool) or isinstance(col, bool):
            return None
        if not isinstance(row, int) or not isinstance(col, int):
            return None
        cells.append((row, col))
    return cells


def validate(payload: dict, path: object, partial: bool = False) -> dict:
    """Walk `path` over `payload`'s board and report what it did.

    `partial` drops the two end-of-route rules (finish on the end socket, visit
    every anchor) so the browser can ask the same question of a half-drawn
    cable — "is what I have so far still legal?" — through this exact code.

    Returns `{ok, reason, edges, bends, anchors_visited}`; `reason` is "" when
    `ok`, and otherwise one of the stable strings below.
    """
    cells = _as_cells(path)
    if cells is None:
        return {"ok": False, "reason": "bad_shape", "edges": 0, "bends": 0,
                "anchors_visited": 0}

    rows, cols = payload["rows"], payload["cols"]
    start = tuple(payload["start"])
    end = tuple(payload["end"])
    blocked = {tuple(cell) for cell in payload["blocked_cells"]}
    ordered = sorted(payload["anchors"], key=lambda anchor: anchor["order"])
    anchor_at = {tuple(anchor["cell"]): anchor for anchor in ordered}
    edges = len(cells) - 1

    def report(ok: bool, reason: str, bends: int, visited: int) -> dict:
        return {"ok": ok, "reason": reason, "edges": edges, "bends": bends,
                "anchors_visited": visited}

    if edges > payload["edge_cap"]:
        return report(False, "too_long", 0, 0)
    if cells[0] != start:
        return report(False, "bad_start", 0, 0)

    seen: set[Cell] = set()
    bends = 0
    visited = 0
    heading: str | None = None    # side crossed by the previous step
    pending_exit: str | None = None   # port the current anchor must be left by

    for index, cell in enumerate(cells):
        row, col = cell
        if not (0 <= row < rows and 0 <= col < cols):
            return report(False, "out_of_bounds", bends, visited)
        if cell in blocked:
            return report(False, "blocked", bends, visited)
        if cell in seen:
            # One rule for three failures: self-crossing, edge reuse, and the
            # 180-degree reversal are all a cell visited twice.
            return report(False, "revisit", bends, visited)
        seen.add(cell)

        entry: str | None = None
        if index:
            previous = cells[index - 1]
            side = SIDE_OF_STEP.get((row - previous[0], col - previous[1]))
            if side is None:
                return report(False, "not_adjacent", bends, visited)
            if pending_exit is not None and side != pending_exit:
                return report(False, "anchor_port", bends, visited)
            if heading is not None and side != heading:
                bends += 1
                if bends > payload["bend_cap"]:
                    return report(False, "too_many_bends", bends, visited)
            pending_exit = None
            heading = side
            entry = OPPOSITE[side]

        anchor = anchor_at.get(cell)
        if anchor is not None:
            if anchor["order"] != visited:
                return report(False, "anchor_out_of_order", bends, visited)
            if anchor["entry"] is not None and anchor["entry"] != entry:
                return report(False, "anchor_port", bends, visited)
            visited += 1
            pending_exit = anchor["exit"]

    if partial:
        return report(True, "", bends, visited)
    if pending_exit is not None:
        return report(False, "anchor_port", bends, visited)
    if cells[-1] != end:
        return report(False, "not_at_end", bends, visited)
    if visited != len(ordered):
        return report(False, "missing_anchor", bends, visited)
    return report(True, "", bends, visited)


# --- Search (generation gates and the test-side solver) ------------------


def _anchor_orders(payload: dict) -> dict[Cell, int]:
    return {
        tuple(anchor["cell"]): anchor["order"] for anchor in payload["anchors"]
    }


def _min_bends(payload: dict, blocked: set[Cell] | None = None) -> int | None:
    """Fewest direction changes any route could possibly spend, or None if the
    board is hopeless even under the relaxation.

    The relaxation is self-avoidance: this walk may reuse cells (though never a
    consumed anchor, which no legal route can revisit either), so every legal
    route maps onto a walk here with the same bend count and the answer is a
    true lower bound. Ports are ignored for the same reason — dropping a
    constraint can only lower the bound. 0-1 BFS over
    `(cell, next anchor, heading)`, so it is linear in a few thousand states.
    """
    rows, cols = payload["rows"], payload["cols"]
    walls = {tuple(cell) for cell in payload["blocked_cells"]} if blocked is None else blocked
    orders = _anchor_orders(payload)
    total = len(payload["anchors"])
    start, end = tuple(payload["start"]), tuple(payload["end"])
    cap = payload["bend_cap"]

    def take(cell: Cell, index: int) -> int | None:
        """The next-anchor counter after entering `cell`, or None if entering
        it would break the anchor order."""
        order = orders.get(cell)
        if order is None:
            return index
        return index + 1 if order == index else None

    begin = take(start, 0)
    if begin is None or start in walls or end in walls:
        return None
    first = (start, begin, None)
    best: dict[tuple, int] = {first: 0}
    queue: deque[tuple] = deque([first])
    while queue:
        state = queue.popleft()
        cell, index, heading = state
        cost = best[state]
        if cell == end and index == total:
            return cost
        for side, (delta_row, delta_col) in STEPS.items():
            nxt = (cell[0] + delta_row, cell[1] + delta_col)
            if not (0 <= nxt[0] < rows and 0 <= nxt[1] < cols) or nxt in walls:
                continue
            after = take(nxt, index)
            if after is None:
                continue
            spent = cost + (0 if heading is None or heading == side else 1)
            if spent > cap:
                continue
            ahead = (nxt, after, side)
            if spent < best.get(ahead, cap + 1):
                best[ahead] = spent
                if spent == cost:
                    queue.appendleft(ahead)
                else:
                    queue.append(ahead)
    return None


def _manhattan(one: Cell, other: Cell) -> int:
    return abs(one[0] - other[0]) + abs(one[1] - other[1])


def _sides_towards(cell: Cell, target: Cell, heading: str | None) -> list[str]:
    """The four sides, worst first — the solver pops from the end, so this puts
    "closes on the target, and does it without turning" at the top of the pile.
    """
    return sorted(
        STEPS,
        key=lambda side: (
            _manhattan((cell[0] + STEPS[side][0], cell[1] + STEPS[side][1]), target),
            0 if side == heading else 1,
        ),
        reverse=True,
    )


def solve(payload: dict, budget: int = SOLVE_BUDGET) -> list[list[int]] | None:
    """A legal route found from the payload alone, or None inside `budget`.

    Nothing in the game needs this — generation already ships a constructive
    route — but the tests use it to confirm the *payload* admits a solution
    without consulting the reference, which is the spec's "a solver or
    constructive reference" gate.

    Depth-first over the same rules `validate` enforces, guided towards the
    next waypoint and pruned by an admissible bound: the obstacle-free
    manhattan walk through the remaining anchors is the least the route can
    still cost, so anything longer than `edge_cap` is already dead.
    """
    rows, cols = payload["rows"], payload["cols"]
    walls = {tuple(cell) for cell in payload["blocked_cells"]}
    orders = _anchor_orders(payload)
    ports = {tuple(a["cell"]): (a["entry"], a["exit"]) for a in payload["anchors"]}
    ordered = sorted(payload["anchors"], key=lambda anchor: anchor["order"])
    start, end = tuple(payload["start"]), tuple(payload["end"])
    bend_cap, edge_cap = payload["bend_cap"], payload["edge_cap"]

    waypoints = [tuple(anchor["cell"]) for anchor in ordered] + [end]
    # tail[i] = manhattan cost of the waypoints still to come after the i-th.
    tail = [0] * (len(waypoints) + 1)
    for at in range(len(waypoints) - 2, -1, -1):
        tail[at] = tail[at + 1] + _manhattan(waypoints[at], waypoints[at + 1])

    def to_go(cell: Cell, index: int) -> int:
        return _manhattan(cell, waypoints[index]) + tail[index]

    if start in walls or orders.get(start) not in (None, 0):
        return None
    route = [start]
    seen = {start}
    # Frame: heading, bends, next anchor, the exit port owed, untried sides.
    index = 1 if orders.get(start) == 0 else 0
    stack = [[None, 0, index, ports.get(start, (None, None))[1],
              _sides_towards(start, waypoints[index], None)]]
    while stack:
        budget -= 1
        if budget < 0:
            return None
        heading, bends, index, owed, options = stack[-1]
        if not options:
            stack.pop()
            seen.discard(route.pop())
            continue
        side = options.pop()
        if owed is not None and side != owed:
            continue
        delta_row, delta_col = STEPS[side]
        nxt = (route[-1][0] + delta_row, route[-1][1] + delta_col)
        if not (0 <= nxt[0] < rows and 0 <= nxt[1] < cols):
            continue
        if nxt in walls or nxt in seen:
            continue
        spent = bends + (0 if heading is None or heading == side else 1)
        if spent > bend_cap or len(route) > edge_cap:
            continue
        order = orders.get(nxt)
        after = index
        if order is not None:
            if order != index:
                continue
            entry, _ = ports[nxt]
            if entry is not None and entry != OPPOSITE[side]:
                continue
            after = index + 1
        if nxt == end:
            if after == len(ordered) and ports.get(nxt, (None, None))[1] is None:
                return [list(cell) for cell in route + [nxt]]
            continue
        if len(route) + to_go(nxt, after) > edge_cap:
            continue
        route.append(nxt)
        seen.add(nxt)
        stack.append([side, spent, after, ports.get(nxt, (None, None))[1],
                      _sides_towards(nxt, waypoints[after], side)])
    return None


# --- Generation ---------------------------------------------------------


def _sockets(rng: random.Random, rows: int, cols: int, span: int) -> tuple[Cell, Cell]:
    """Two border cells at least `span` apart — sockets read as sockets when
    they sit on the rim, and the span keeps the trivial L-route far away."""
    border = [
        (row, col)
        for row in range(rows)
        for col in range(cols)
        if row in (0, rows - 1) or col in (0, cols - 1)
    ]
    while True:
        start, end = rng.sample(border, 2)
        if abs(start[0] - end[0]) + abs(start[1] - end[1]) >= span:
            return start, end


def _route(
    rng: random.Random, rows: int, cols: int, start: Cell, end: Cell,
    min_edges: int, max_edges: int, max_bends: int,
) -> list[Cell] | None:
    """A self-avoiding reference route inside every length and bend bound.

    Randomised depth-first walk with three prunes: a route that has already
    turned too often, one that cannot reach the end inside `max_edges` (the
    remaining manhattan distance is a lower bound on what is left), and one
    that would step onto a cell it has already used.
    """
    budget = ROUTE_BUDGET
    sides = list(STEPS)
    rng.shuffle(sides)
    stack: list[list] = [[start, None, 0, sides]]
    seen = {start}
    while stack:
        budget -= 1
        if budget < 0:
            return None
        cell, heading, bends, options = stack[-1]
        if not options:
            stack.pop()
            seen.discard(cell)
            continue
        side = options.pop()
        delta_row, delta_col = STEPS[side]
        nxt = (cell[0] + delta_row, cell[1] + delta_col)
        if not (0 <= nxt[0] < rows and 0 <= nxt[1] < cols) or nxt in seen:
            continue
        spent = bends + (0 if heading is None or heading == side else 1)
        if spent > max_bends:
            continue
        edges = len(stack)
        remaining = abs(nxt[0] - end[0]) + abs(nxt[1] - end[1])
        if edges + remaining > max_edges:
            continue
        if nxt == end:
            if edges >= min_edges:
                return [frame[0] for frame in stack] + [nxt]
            continue          # too short to be worth drawing; keep looking
        seen.add(nxt)
        fresh = list(STEPS)
        rng.shuffle(fresh)
        stack.append([nxt, side, spent, fresh])
    return None


def _bends_of(route: list[Cell]) -> int:
    sides = [
        SIDE_OF_STEP[(b[0] - a[0], b[1] - a[1])] for a, b in zip(route, route[1:])
    ]
    return sum(1 for a, b in zip(sides, sides[1:]) if a != b)


def _anchor_positions(
    rng: random.Random, length: int, count: int, gap: int = 2,
) -> list[int] | None:
    """`count` route indices, spread out and clear of both sockets."""
    inner = range(2, length - 2)
    if len(inner) < count:
        return None
    for _ in range(60):
        picks = sorted(rng.sample(list(inner), count))
        if all(b - a >= gap for a, b in zip(picks, picks[1:])):
            return picks
    return None


def _obstacles(
    rng: random.Random, rows: int, cols: int, route: list[Cell], count: int,
) -> list[Cell]:
    """`count` cells off the route, most of them pressed against it.

    Obstacles that hug the corridor are the ones that close shortcuts; a board
    of obstacles scattered in open space would decorate rather than route.
    Whether they really bite is then measured, not assumed — see `_build`.
    """
    on_route = set(route)
    hugging, distant = [], []
    for row in range(rows):
        for col in range(cols):
            cell = (row, col)
            if cell in on_route:
                continue
            touching = any(
                (row + dr, col + dc) in on_route for dr, dc in STEPS.values()
            )
            (hugging if touching else distant).append(cell)
    rng.shuffle(hugging)
    rng.shuffle(distant)
    want_close = min(len(hugging), int(count * HUG_FRACTION + 0.5))
    chosen = hugging[:want_close]
    chosen += distant[:count - len(chosen)]
    if len(chosen) < count:                     # small board: take what is left
        chosen += hugging[want_close:want_close + count - len(chosen)]
    return sorted(chosen)


def _ports(
    rng: random.Random, route: list[Cell], positions: list[int], count: int,
) -> list[tuple[str | None, str | None]]:
    """Per-anchor `(entry, exit)` ports, read off the reference route.

    A port is only ever set to the side the reference route already uses, so
    adding one can prune alternative routes but never the constructive one.
    """
    ports: list[tuple[str | None, str | None]] = [(None, None)] * len(positions)
    for at in rng.sample(range(len(positions)), min(count, len(positions))):
        index = positions[at]
        into = SIDE_OF_STEP[(
            route[index][0] - route[index - 1][0],
            route[index][1] - route[index - 1][1],
        )]
        out = SIDE_OF_STEP[(
            route[index + 1][0] - route[index][0],
            route[index + 1][1] - route[index][1],
        )]
        ports[at] = (OPPOSITE[into], None) if rng.random() < 0.5 else (None, out)
    return ports


class ThreadlineGame:
    """Draw one cable through the anchors in order, inside the bend budget."""

    id = "threadline"
    name = "Threadline"

    def generate_main(self, seed: int, level: int = 1) -> PuzzleInstance:
        return self._generate(seed, kind="main", level=level)

    def generate_holding(self, seed: int) -> PuzzleInstance:
        return self._generate(seed, kind="holding")

    def _build(self, seed: int, kind: str, level: int = 1) -> tuple[dict, str]:
        """Payload + the reference route (server-only, used by tests)."""
        rng = random.Random(seed)
        main = kind == "main"
        params = _params_for_level(level) if main else HOLDING_PARAMS
        rows = MAIN_ROWS if main else HOLD_ROWS
        cols = MAIN_COLS if main else HOLD_COLS
        span = MIN_SOCKET_SPAN if main else HOLD_SOCKET_SPAN
        min_edges, max_edges = params["edges"]

        for _ in range(GEN_ATTEMPTS):
            start, end = _sockets(rng, rows, cols, span)
            # The cap is the route's own bends plus the level's slack, so the
            # route must stay under the window's ceiling to leave room for it.
            route = _route(
                rng, rows, cols, start, end, min_edges, max_edges,
                BEND_CAP_MAX - params["bend_slack"],
            )
            if route is None:
                continue
            bends = _bends_of(route)
            bend_cap = bends + params["bend_slack"]
            if bends < params["min_bends"] or not main and bend_cap < 2:
                continue
            if main and not BEND_CAP_MIN <= bend_cap <= BEND_CAP_MAX:
                continue

            positions = _anchor_positions(rng, len(route), params["anchors"])
            if positions is None:
                continue
            ports = _ports(rng, route, positions, params["ports"])
            blocked = _obstacles(rng, rows, cols, route, params["blocked"])
            payload = {
                "variant": kind,
                "difficulty": params["difficulty"],
                "time_hint_seconds": params["time_hint"],
                "rules_version": RULES_VERSION,
                "rows": rows,
                "cols": cols,
                "start": list(start),
                "end": list(end),
                "anchors": [
                    {
                        "id": f"a{order}",
                        "cell": list(route[index]),
                        "order": order,
                        "entry": ports[order][0],
                        "exit": ports[order][1],
                    }
                    for order, index in enumerate(positions)
                ],
                "blocked_cells": [list(cell) for cell in blocked],
                "bend_cap": bend_cap,
                "edge_cap": len(route) - 1 + params["edge_slack"],
            }

            forced = _min_bends(payload)
            if forced is None or bend_cap - forced > params["bend_freedom"]:
                continue      # the cap leaves the player too much room
            if main:
                open_board = _min_bends(payload, blocked=set())
                if open_board is None or open_board >= forced:
                    continue  # obstacles that do not influence routing
            answer = json.dumps({
                "v": RULES_VERSION, "path": [list(cell) for cell in route],
            })
            return payload, answer
        raise RuntimeError(f"threadline generation failed for seed {seed}")

    def _generate(self, seed: int, kind: str, level: int = 1) -> PuzzleInstance:
        payload, answer = self._build(seed, kind, level)
        anchors = len(payload["anchors"])
        return PuzzleInstance(
            game_id=self.id,
            kind=kind,
            prompt=(
                f"Draw the cable from START to END through all {anchors} anchors "
                f"in order, in at most {payload['bend_cap']} bends."
            ),
            answer=answer,  # server-only reference; check() re-walks instead
            payload=payload,
        )

    def check(self, puzzle: PuzzleInstance, answer: str) -> bool:
        try:
            raw = str(answer)
            if len(raw) > MAX_ANSWER_CHARS:
                return False  # cap the raw submission before any parsing
            text = raw.strip()
            if not text:
                return False
            data = json.loads(text)
            if not isinstance(data, dict) or data.get("v") != RULES_VERSION:
                return False
            return validate(puzzle.payload, data.get("path"))["ok"]
        except Exception:
            return False  # malformed input is just wrong, never a crash

    def reset(self) -> None:
        return None  # stateless
