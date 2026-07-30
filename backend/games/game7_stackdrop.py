"""STACKDROP (Game 7, causal prediction): pull the pins in the right order so
every ball drops into its matching container.

Per game/RELAY_EXPANSION_GAMES_README.md §3. The chamber is a discrete grid of
walls, fixed 45° ramps, removable pins, balls, containers and hazards. Nothing
moves until a pin is pulled; then gravity resolves to a fixed point before the
next pull. One rule covers every landing: a ball that lands on something
**slanted** (a ramp or a slanted pin) rolls one cell down-slope, a ball that
lands on something **flat** (wall, flat pin, another ball) stops. A ball
entering a hazard or a container of the wrong kind fails the attempt at once;
the attempt succeeds when every ball sits in its matching container.

Two pin kinds is a deliberate extension of the spec's payload (§3 lists only
`id` + `cells`), and it is what makes the game a *puzzle* rather than a
sequence of independent drops: with hold-only pins a ball's route through the
chamber is invariant — pulling a pin can delay a ball but never divert it, so
no board could ever be order-sensitive, which §3 requires. A slanted pin steers
a ball while it is still in place and lets it fall straight through once it is
gone, so *when* you pull it decides where a ball ends up.

Generation carves each ball's fall path backwards from its container (so every
board is built from a working terminal arrangement), hangs the balls off hold
pins, then gates the result with a bounded breadth-first solver: a shortest
solution inside the level's removal-depth band, not already solved, and
**order-sensitive** — at least one permutation of that solution must fail.
`check` never trusts the client: it rebuilds the chamber from the public
payload and replays the submitted removals. The reference solution stored in
`answer` is server-only and used by tests; the checker never compares against
it, because a board can have several winning orders.

Level 1 sits under the spec's suggested main ranges (2 balls, 4 pins) — that is
the gentle end of the V5 level curve; level 10 lands inside them (4 balls, 7
pins, 9x7).
"""

from __future__ import annotations

import json
import random
from itertools import permutations

from backend.games.base import PuzzleInstance

RULES_VERSION = 1

# Static cell features. Walls and ramps are solid; a hazard is open space that
# destroys any ball entering it.
WALL = "wall"
RAMP_LEFT = "ramp_left"
RAMP_RIGHT = "ramp_right"
HAZARD = "hazard"

# Pin kinds: a flat pin holds a ball, a slanted pin rolls it down-slope.
HOLD = "hold"
TILT_LEFT = "tilt_left"
TILT_RIGHT = "tilt_right"

# Every slanted surface, static or removable, and the column it rolls a ball to.
SLOPES = {RAMP_LEFT: -1, RAMP_RIGHT: 1, TILT_LEFT: -1, TILT_RIGHT: 1}
RAMPS = (RAMP_LEFT, RAMP_RIGHT)
TILTS = (TILT_LEFT, TILT_RIGHT)

FREE = "free"                # generator-only: a cell reserved as fall path

# Ball/container kinds are shapes, never colour alone (spec §6).
KINDS = ("circle", "triangle", "square", "diamond")

# --- Level-1 board (the V5 baseline: generate_main(seed) == level 1) ---
MAIN_ROWS = 8
MAIN_COLS = 6
MAIN_BALLS = 2
MAIN_PINS = 4
MAIN_DEPTH = (2, 4)          # shortest-solution removal band
MAIN_HAZARDS = 1

HOLD_ROWS = 5
HOLD_COLS = 4
HOLD_BALLS = 1
HOLD_PINS = 2
HOLD_DEPTH = (1, 2)
HOLD_HAZARDS = 0

GEN_ATTEMPTS = 900
TILT_BIAS = 3                # slanted deflectors weighted this much over ramps
WALL_FILL = 0.14             # share of leftover cells that become wall structure
MAX_ANSWER_CHARS = 400
MAX_PERMUTATION_CHECK = 720  # 6! — work cap on the order-sensitivity gate

# Main-board difficulty curve (docs/TASK_LIST.md V5): one row per level 1..10,
# level 1 == the board above. Balls cap at 4 (one per KINDS glyph) and pins at
# 7 (the spec's main ceiling).
MAIN_LEVEL_PARAMS: tuple[dict, ...] = (
    {"rows": 8, "cols": 6, "balls": 2, "pins": 4, "depth": (2, 4), "hazards": 1, "difficulty": 2, "time_hint": 30},  # 1
    {"rows": 8, "cols": 6, "balls": 2, "pins": 4, "depth": (2, 4), "hazards": 1, "difficulty": 2, "time_hint": 30},  # 2
    {"rows": 8, "cols": 6, "balls": 2, "pins": 4, "depth": (2, 4), "hazards": 1, "difficulty": 2, "time_hint": 30},  # 3
    {"rows": 8, "cols": 6, "balls": 3, "pins": 5, "depth": (3, 5), "hazards": 1, "difficulty": 3, "time_hint": 38},  # 4
    {"rows": 8, "cols": 6, "balls": 3, "pins": 5, "depth": (3, 5), "hazards": 1, "difficulty": 3, "time_hint": 38},  # 5
    {"rows": 8, "cols": 6, "balls": 3, "pins": 5, "depth": (3, 5), "hazards": 1, "difficulty": 3, "time_hint": 38},  # 6
    {"rows": 9, "cols": 7, "balls": 3, "pins": 6, "depth": (3, 6), "hazards": 2, "difficulty": 4, "time_hint": 46},  # 7
    {"rows": 9, "cols": 7, "balls": 3, "pins": 6, "depth": (3, 6), "hazards": 2, "difficulty": 4, "time_hint": 46},  # 8
    {"rows": 9, "cols": 7, "balls": 3, "pins": 6, "depth": (3, 6), "hazards": 2, "difficulty": 4, "time_hint": 46},  # 9
    {"rows": 9, "cols": 7, "balls": 4, "pins": 7, "depth": (3, 6), "hazards": 2, "difficulty": 4, "time_hint": 55},  # 10
)

HOLDING_PARAMS = {
    "rows": HOLD_ROWS, "cols": HOLD_COLS, "balls": HOLD_BALLS, "pins": HOLD_PINS,
    "depth": HOLD_DEPTH, "hazards": HOLD_HAZARDS, "difficulty": 1, "time_hint": 8,
}


def _params_for_level(level: int) -> dict:
    """Main-board knobs for `level`, clamped to the 1..10 table."""
    return MAIN_LEVEL_PARAMS[min(max(level, 1), len(MAIN_LEVEL_PARAMS)) - 1]


Cell = tuple[int, int]

# Outcomes of one attempted ball step.
FAIL, MOVE, CONTAIN, BLOCKED = "fail", "move", "contain", "blocked"


class _Chamber:
    """The simulated board. Built from the public payload in `check`, so the
    server never needs anything the client cannot see."""

    def __init__(
        self,
        rows: int,
        cols: int,
        static: dict[Cell, str],
        containers: dict[Cell, str],
        pins: dict[str, tuple[str, tuple[Cell, ...]]],
        balls: tuple[tuple[str, str, Cell], ...],
    ) -> None:
        self.rows = rows
        self.cols = cols
        self.static = static            # cell -> WALL | RAMP_* | HAZARD
        self.containers = containers    # cell -> the kind it accepts
        self.pins = pins                # pin id -> (kind, cells)
        self.balls = balls              # (id, kind, start cell), payload order
        self.solid = {
            cell for cell, feature in static.items() if feature != HAZARD
        }                               # walls + ramps; hazards are open space


def _pin_map(chamber: _Chamber, removed: set[str]) -> dict[Cell, str]:
    """Cells still occupied by a pin, mapped to that pin's kind."""
    return {
        cell: kind
        for pin_id, (kind, cells) in chamber.pins.items()
        if pin_id not in removed
        for cell in cells
    }


def _enter(
    chamber: _Chamber, occupied: set[Cell], pinned: dict[Cell, str], cell: Cell, kind: str
) -> str:
    """What happens when a ball of `kind` tries to move into `cell`."""
    r, c = cell
    if r >= chamber.rows:
        return FAIL                     # dropped out through the chamber floor
    if not 0 <= c < chamber.cols:
        return BLOCKED                  # the chamber's side walls
    if cell in occupied:
        return BLOCKED                  # balls rest on each other
    if cell in chamber.containers:
        return CONTAIN if chamber.containers[cell] == kind else FAIL
    if chamber.static.get(cell) == HAZARD:
        return FAIL
    if cell in chamber.solid or cell in pinned:
        return BLOCKED
    return MOVE


def _slope(chamber: _Chamber, pinned: dict[Cell, str], cell: Cell) -> int | None:
    """Column delta a ball rolls when it lands on `cell`, None if it is flat."""
    if cell in chamber.static:
        return SLOPES.get(chamber.static[cell])
    return SLOPES.get(pinned.get(cell, ""))


def _resolve(chamber: _Chamber, removed: set[str], state: list[list]) -> bool:
    """Run gravity on `state` (mutated in place) until nothing can move.

    `state` holds one `[row, col, contained]` per ball in payload order.
    Returns False the moment a ball enters a hazard or the wrong container.
    Every move strictly increases a ball's row, so the fixed point always
    exists.
    """
    pinned = _pin_map(chamber, removed)
    moved = True
    while moved:
        moved = False
        # Bottom-most ball first (then leftmost) so a ball can never tunnel
        # through one that is about to move out of its way.
        for index in sorted(
            range(len(state)), key=lambda i: (-state[i][0], state[i][1])
        ):
            ball = state[index]
            if ball[2]:
                continue                # settled in its container
            occupied = {
                (other[0], other[1]) for i, other in enumerate(state) if i != index
            }
            kind = chamber.balls[index][1]
            target = (ball[0] + 1, ball[1])
            outcome = _enter(chamber, occupied, pinned, target, kind)
            if outcome == BLOCKED:
                slope = _slope(chamber, pinned, target)
                if slope is None:
                    continue            # resting on a wall, flat pin or ball
                target = (ball[0] + 1, ball[1] + slope)
                outcome = _enter(chamber, occupied, pinned, target, kind)
                if outcome == BLOCKED:
                    continue            # the roll-off cell is taken as well
            if outcome == FAIL:
                return False
            ball[0], ball[1] = target
            ball[2] = outcome == CONTAIN
            moved = True
    return True


def _initial_state(chamber: _Chamber) -> list[list]:
    return [[r, c, False] for _, _, (r, c) in chamber.balls]


def _play(chamber: _Chamber, removals: list[str]) -> tuple[list[list], bool]:
    """Replay a removal sequence from the start. Returns (state, alive)."""
    state = _initial_state(chamber)
    removed: set[str] = set()
    if not _resolve(chamber, removed, state):
        return state, False
    for pin_id in removals:
        removed.add(pin_id)
        if not _resolve(chamber, removed, state):
            return state, False
    return state, True


def _solved(state: list[list]) -> bool:
    return all(ball[2] for ball in state)


def _succeeds(chamber: _Chamber, removals: list[str]) -> bool:
    state, alive = _play(chamber, removals)
    return alive and _solved(state)


def _shortest_solution(chamber: _Chamber, max_depth: int) -> list[str] | None:
    """Breadth-first search over removal sequences. Order matters, so a search
    node is keyed by the removed set *and* the ball state it produced."""
    state = _initial_state(chamber)
    if not _resolve(chamber, set(), state):
        return None
    if _solved(state):
        return []
    frontier = [(frozenset(), state, [])]
    seen = {(frozenset(), tuple(map(tuple, state)))}
    for _ in range(max_depth):
        nxt: list[tuple[frozenset, list[list], list[str]]] = []
        for removed, base, sequence in frontier:
            for pin_id in sorted(chamber.pins):
                if pin_id in removed:
                    continue
                after = [list(ball) for ball in base]
                if not _resolve(chamber, set(removed) | {pin_id}, after):
                    continue            # this pull kills a ball — dead branch
                if _solved(after):
                    return sequence + [pin_id]
                key = (removed | {pin_id}, tuple(map(tuple, after)))
                if key in seen:
                    continue
                seen.add(key)
                nxt.append((removed | {pin_id}, after, sequence + [pin_id]))
        frontier = nxt
    return None


def _order_sensitive(chamber: _Chamber, solution: list[str]) -> bool:
    """True if some permutation of a winning sequence does not win — the
    spec's 'at least one order-sensitive pair of pins' gate (§3)."""
    if len(solution) < 2:
        return False
    for index, order in enumerate(permutations(solution)):
        if index >= MAX_PERMUTATION_CHECK:
            break
        if not _succeeds(chamber, list(order)):
            return True
    return False


def _claim(plan: dict[Cell, str], cell: Cell, feature: str) -> bool:
    """Reserve `cell` for `feature`; False if it is already something else.

    Fall paths may share cells (balls pass at different times), and a path may
    cross a slanted pin: that path simply needs the pin pulled before its ball
    arrives, while the path that placed the pin needs it still there. That
    tension is where a board's pull order comes from.
    """
    current = plan.get(cell)
    if current is None or current == feature:
        plan[cell] = feature
        return True
    if feature == FREE and current in TILTS:
        return True                     # this ball passes once the pin is gone
    if feature in TILTS and current == FREE:
        plan[cell] = feature            # the other path must clear it first
        return True
    return False


def _carve(
    rng: random.Random, rows: int, cols: int, plan: dict[Cell, str],
    column: int, steps: int, tilt_budget: int
) -> tuple[list[Cell], dict[Cell, str], int] | None:
    """Trace one ball's fall path backwards from above its container.

    Returns the path (container end first, ball start last), the updated plan
    and the number of slanted pins the path spent, or None if it cannot be
    carved. The last backward step is always a straight fall so the ball's hold
    pin has a cell to sit in.
    """
    plan = dict(plan)
    cell = (rows - 2, column)
    if not _claim(plan, cell, FREE):
        return None
    path = [cell]
    spent = 0
    for step in range(steps):
        r, c = path[-1]
        # A deflection step means the ball arrived diagonally: it sat at
        # (r-1, c-delta), landed on the slanted cell there and rolled into
        # (r, c). Slanted *pins* are weighted over fixed ramps because they
        # are what makes pull order matter.
        moves: list[str | None] = [None]
        if step < steps - 1:
            moves += list(RAMPS)
            if spent < tilt_budget:
                moves += list(TILTS) * TILT_BIAS
        rng.shuffle(moves)
        for move in moves:
            delta = 0 if move is None else SLOPES[move]
            previous = (r - 1, c - delta)
            if previous[0] < 0 or not 0 <= previous[1] < cols:
                continue
            trial = dict(plan)
            if move is not None and not _claim(trial, (r, c - delta), move):
                continue
            if not _claim(trial, previous, FREE):
                continue
            plan = trial
            spent += move in TILTS
            path.append(previous)
            break
        else:
            return None
    return path, plan, spent


def _hold_pin(
    rng: random.Random, plan: dict[Cell, str], cols: int, used: set[Cell],
    starts: set[Cell], anchor: Cell, width: int
) -> tuple[Cell, ...] | None:
    """A 1–2 cell flat pin anchored at `anchor`, or None if it will not fit.

    Hold pins may sit on reserved path cells — that is what makes them matter —
    but never on walls, ramps, slanted pins, containers, hazards or balls.
    """
    if anchor in used or anchor in starts or plan.get(anchor, FREE) != FREE:
        return None
    r, c = anchor
    if width == 2:
        for step in rng.sample([-1, 1], 2):
            neighbour = (r, c + step)
            if not 0 <= neighbour[1] < cols:
                continue
            if neighbour in used or neighbour in starts:
                continue
            if plan.get(neighbour, FREE) != FREE:
                continue
            return tuple(sorted((anchor, neighbour)))
    return (anchor,)


def _build_chamber(rng: random.Random, params: dict) -> _Chamber | None:
    """One candidate chamber, or None if this attempt does not fit together."""
    rows, cols = params["rows"], params["cols"]
    ball_count, pin_count = params["balls"], params["pins"]

    columns = rng.sample(range(cols), ball_count + params["hazards"])
    container_columns = sorted(columns[:ball_count])
    kinds = list(KINDS[:ball_count])
    rng.shuffle(kinds)

    # The floor: containers, hazards, wall everywhere else.
    plan: dict[Cell, str] = {}
    static: dict[Cell, str] = {}
    containers: dict[Cell, str] = {}
    for column, kind in zip(container_columns, kinds):
        plan[(rows - 1, column)] = "container"
        containers[(rows - 1, column)] = kind
    for column in columns[ball_count:]:
        plan[(rows - 1, column)] = HAZARD
        static[(rows - 1, column)] = HAZARD
    for column in range(cols):
        if (rows - 1, column) not in plan:
            plan[(rows - 1, column)] = WALL
            static[(rows - 1, column)] = WALL

    # Carve each ball's path backwards from its container, in shuffled order so
    # no container systematically gets the roomiest route. Every slanted pin a
    # path spends comes out of the shared pin budget.
    tilt_budget = pin_count - ball_count
    paths: list[tuple[list[Cell], str]] = []
    for column in rng.sample(container_columns, len(container_columns)):
        carved = _carve(
            rng, rows, cols, plan, column, rng.randint(2, rows - 2), tilt_budget
        )
        if carved is None:
            return None
        path, plan, spent = carved
        tilt_budget -= spent
        paths.append((path, containers[(rows - 1, column)]))
    for cell, feature in plan.items():
        if feature in RAMPS:
            static[cell] = feature      # the fixed deflectors the paths rely on

    starts = {path[-1] for path, _ in paths}
    if len(starts) < len(paths):
        return None                     # two balls cannot share a start cell
    supports = [path[-2] for path, _ in paths]
    if any(support in starts for support in supports):
        return None                     # a hold pin would sit inside a ball

    # Pins: one hold pin under each ball, the slanted pins the paths asked for,
    # then extra hold pins on the fall paths so every decoy is a real obstacle
    # rather than decoration.
    used: set[Cell] = set()
    pin_list: list[tuple[str, tuple[Cell, ...]]] = []
    for support in supports:
        cells = _hold_pin(
            rng, plan, cols, used, starts, support, rng.choice([1, 2, 2])
        )
        if cells is None:
            return None
        pin_list.append((HOLD, cells))
        used.update(cells)
    for cell, feature in sorted(plan.items()):
        if feature in TILTS:
            pin_list.append((feature, (cell,)))
            used.add(cell)
    if len(pin_list) > pin_count:
        return None
    candidates = [
        cell
        for path, _ in paths
        for cell in path[1:-1]
        if cell not in used and cell not in starts
    ]
    rng.shuffle(candidates)
    for anchor in candidates:
        if len(pin_list) >= pin_count:
            break
        cells = _hold_pin(rng, plan, cols, used, starts, anchor, rng.choice([1, 2]))
        if cells is None:
            continue
        pin_list.append((HOLD, cells))
        used.update(cells)
    if len(pin_list) != pin_count:
        return None

    # Leftover cells become wall structure; reserved paths stay clear.
    for r in range(rows - 1):
        for c in range(cols):
            cell = (r, c)
            if cell in plan or cell in used or cell in starts:
                continue
            if rng.random() < WALL_FILL:
                plan[cell] = WALL
                static[cell] = WALL

    # Numbering runs top-to-bottom, left-to-right so the labels read naturally.
    pin_list.sort(key=lambda pin: pin[1])
    pins = {f"p{index}": pin for index, pin in enumerate(pin_list)}
    ball_kind = {path[-1]: path_kind for path, path_kind in paths}
    balls = tuple(
        (f"b{index}", ball_kind[start], start)
        for index, start in enumerate(sorted(starts))
    )
    return _Chamber(rows, cols, static, containers, pins, balls)


def _payload(chamber: _Chamber, params: dict, kind: str) -> dict:
    return {
        "variant": kind,
        "difficulty": params["difficulty"],
        "time_hint_seconds": params["time_hint"],
        "rules_version": RULES_VERSION,
        "rows": chamber.rows,
        "cols": chamber.cols,
        "static_cells": [
            {"r": r, "c": c, "type": feature}
            for (r, c), feature in sorted(chamber.static.items())
        ],
        "pins": [
            {"id": pin_id, "kind": pin_kind, "cells": [list(cell) for cell in cells]}
            for pin_id, (pin_kind, cells) in sorted(chamber.pins.items())
        ],
        "balls": [
            {"id": ball_id, "kind": ball_kind, "start": list(start)}
            for ball_id, ball_kind, start in chamber.balls
        ],
        "containers": [
            {"id": f"c{index}", "kind": container_kind, "cells": [list(cell)]}
            for index, (cell, container_kind) in enumerate(
                sorted(chamber.containers.items())
            )
        ],
        "removal_cap": len(chamber.pins),
    }


def chamber_from_payload(payload: dict) -> _Chamber:
    """Rebuild the simulated chamber from the public payload (checker path)."""
    static = {
        (cell["r"], cell["c"]): cell["type"] for cell in payload["static_cells"]
    }
    containers = {
        (r, c): container["kind"]
        for container in payload["containers"]
        for r, c in container["cells"]
    }
    pins = {
        pin["id"]: (pin["kind"], tuple((r, c) for r, c in pin["cells"]))
        for pin in payload["pins"]
    }
    balls = tuple(
        (ball["id"], ball["kind"], (ball["start"][0], ball["start"][1]))
        for ball in payload["balls"]
    )
    return _Chamber(payload["rows"], payload["cols"], static, containers, pins, balls)


class StackdropGame:
    """Pull pins, let gravity do the rest — every ball into its own container."""

    id = "stackdrop"
    name = "Stackdrop"

    def generate_main(self, seed: int, level: int = 1) -> PuzzleInstance:
        return self._generate(seed, kind="main", level=level)

    def generate_holding(self, seed: int) -> PuzzleInstance:
        return self._generate(seed, kind="holding")

    def _build(self, seed: int, kind: str, level: int = 1) -> tuple[dict, str]:
        """Payload + a reference solution (server-only, used by tests)."""
        rng = random.Random(seed)
        params = _params_for_level(level) if kind == "main" else HOLDING_PARAMS
        min_depth, max_depth = params["depth"]
        for _ in range(GEN_ATTEMPTS):
            chamber = _build_chamber(rng, params)
            if chamber is None:
                continue
            state = _initial_state(chamber)
            if not _resolve(chamber, set(), state) or state != _initial_state(chamber):
                continue                # served boards must start still and alive
            solution = _shortest_solution(chamber, max_depth)
            if solution is None or not min_depth <= len(solution) <= max_depth:
                continue
            # Holding boards are one or two pulls — too short to demand an
            # order trap; main boards must punish the wrong order.
            if kind == "main" and not _order_sensitive(chamber, solution):
                continue
            payload = _payload(chamber, params, kind)
            answer = json.dumps({"v": RULES_VERSION, "remove": solution})
            return payload, answer
        raise RuntimeError(f"stackdrop generation failed for seed {seed}")

    def _generate(self, seed: int, kind: str, level: int = 1) -> PuzzleInstance:
        payload, answer = self._build(seed, kind, level)
        return PuzzleInstance(
            game_id=self.id,
            kind=kind,
            prompt="Pull the pins in the right order — every ball into its matching container.",
            answer=answer,  # server-only reference; check() replays instead
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
            removals = data.get("remove")
            payload = puzzle.payload
            if not isinstance(removals, list) or not removals:
                return False
            if len(removals) > payload["removal_cap"]:
                return False
            if len(set(map(str, removals))) != len(removals):
                return False  # a pin cannot be pulled twice
            known = {pin["id"] for pin in payload["pins"]}
            if any(pin_id not in known for pin_id in removals):
                return False
            return _succeeds(chamber_from_payload(payload), removals)
        except Exception:
            return False  # malformed input is just wrong, never a crash

    def reset(self) -> None:
        return None  # stateless
