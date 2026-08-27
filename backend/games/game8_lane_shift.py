"""LANE SHIFT (Game 8, conveyor scheduling): one action per turn, then every
packet advances — sort them all into the exit matching their symbol.

Per game/RELAY_EXPANSION_GAMES_README.md §2. The conveyor is a grid of lanes:
packets advance one column per turn, and a junction switch under a packet
decides whether it goes straight, down or up as it leaves. A turn is spawn →
one player action (toggle a switch, spend a hold, or pass) → one simultaneous
advance. Two packets landing on the same cell collide, a packet entering the
wrong exit or a blocker or running off the top/bottom fails, and the attempt
succeeds once every packet has reached its matching exit.

The lane grid *is* the spec's "directed graph with switch-dependent outgoing
edges" (§2), written compactly: a cell's outgoing edge is `(row + delta,
col + 1)` where delta comes from the switch standing on that cell. Expressing
it as lanes rather than an explicit node/edge list keeps the payload small and
the board drawable; nothing about the rules changes.

Generation runs the spec's "solved schedule first" strategy with one twist that
makes it cheap: play a random legal schedule on an *unlabelled* board, see
which exit each packet reaches, and only then label the exits and the packets
from that outcome. Every generated board is therefore solvable by construction,
and a 0-1 breadth-first solver (passes are free, real actions cost one) then
gates it: a solution must exist inside the turn cap, use at least the level's
minimum number of real actions — which also rules out boards that solve
themselves if you only ever press PASS — and deliver across at least two exits.
`check` replays the submitted actions through the same simulation and never
trusts a client's claim of success.

Level 1 sits under the spec's suggested main ranges (3 lanes, 3 packets, 2
junctions) — the gentle end of the V5 curve; level 10 lands inside them (4
lanes, 6 packets, 5 junctions).
"""

from __future__ import annotations

import json
import random
from collections import deque

from backend.games.base import PuzzleInstance

RULES_VERSION = 1

# Junction states and the row delta each applies as a packet leaves the cell.
STRAIGHT, DOWN, UP = "straight", "down", "up"
DELTAS = {STRAIGHT: 0, DOWN: 1, UP: -1}

# Packet/exit identities are shapes, never colour alone (spec §6).
KINDS = ("circle", "triangle", "square", "diamond")

# Packet position tags: waiting to spawn, on the board, delivered to an exit.
WAIT = ("wait",)

PASS = ("pass",)

# --- Level-1 board (the V5 baseline: generate_main(seed) == level 1) ---
MAIN_LANES = 3
MAIN_COLUMNS = 6
MAIN_PACKETS = 3
MAIN_SWITCHES = 2
MAIN_HOLDS = 0
MAIN_BLOCKERS = 0
MAIN_TURNS = (5, 12)         # solution length band
MAIN_MIN_ACTIONS = 2         # real (non-pass) actions the solution must need
MAIN_TURN_CAP = 20           # spec §2 recommended action cap

HOLD_LANES = 2
HOLD_COLUMNS = 3
HOLD_PACKETS = 2
HOLD_SWITCHES = 1
HOLD_TURNS = (2, 4)
HOLD_MIN_ACTIONS = 1
HOLD_TURN_CAP = 6

GEN_ATTEMPTS = 400
SCHEDULE_ATTEMPTS = 2        # random schedules tried per generated layout
SCHEDULE_NODES = 2000        # search budget for one schedule hunt
SOLVER_NODES = 15000         # search budget; boards that overrun are rejected
MAX_ANSWER_CHARS = 900

# Main-board difficulty curve (docs/TASK_LIST.md V5): one row per level 1..13,
# level 1 == the board above. Lanes cap at 4 (one exit per shape glyph), so the
# BONUS-ONLY tiers 11..13 climb on columns, packets, switches and blockers —
# they are never served as a main board. Deeper tiers cost the solver more
# nodes; anything that overruns SOLVER_NODES is rejected at generation.
#
# Generation cost is why the bonus tiers look the way they do. `min_actions` is
# a *gate* — it throws boards away — and raising it to 5 pushed generation past
# a second per board, so it stays at 4. `holds` and `blockers` are expensive for
# the same reason and climb only one step each. Wider boards with more packets
# are the CHEAP way to add difficulty here: they give the solver more room, so
# they generate faster than level 10 does while giving a player more to track.
MAIN_LEVEL_PARAMS: tuple[dict, ...] = (
    {"lanes": 3, "columns": 6, "packets": 3, "switches": 2, "holds": 0, "blockers": 0, "turns": (5, 12), "min_actions": 2, "difficulty": 3, "time_hint": 35},  # 1
    {"lanes": 3, "columns": 6, "packets": 3, "switches": 2, "holds": 0, "blockers": 0, "turns": (5, 12), "min_actions": 2, "difficulty": 3, "time_hint": 35},  # 2
    {"lanes": 3, "columns": 6, "packets": 3, "switches": 2, "holds": 0, "blockers": 0, "turns": (5, 12), "min_actions": 2, "difficulty": 3, "time_hint": 35},  # 3
    {"lanes": 3, "columns": 7, "packets": 4, "switches": 3, "holds": 1, "blockers": 1, "turns": (6, 14), "min_actions": 3, "difficulty": 4, "time_hint": 42},  # 4
    {"lanes": 3, "columns": 7, "packets": 4, "switches": 3, "holds": 1, "blockers": 1, "turns": (6, 14), "min_actions": 3, "difficulty": 4, "time_hint": 42},  # 5
    {"lanes": 3, "columns": 7, "packets": 4, "switches": 3, "holds": 1, "blockers": 1, "turns": (6, 14), "min_actions": 3, "difficulty": 4, "time_hint": 42},  # 6
    {"lanes": 4, "columns": 7, "packets": 4, "switches": 4, "holds": 1, "blockers": 1, "turns": (6, 15), "min_actions": 3, "difficulty": 4, "time_hint": 48},  # 7
    {"lanes": 4, "columns": 7, "packets": 4, "switches": 4, "holds": 1, "blockers": 1, "turns": (6, 15), "min_actions": 3, "difficulty": 4, "time_hint": 48},  # 8
    {"lanes": 4, "columns": 7, "packets": 4, "switches": 4, "holds": 1, "blockers": 1, "turns": (6, 15), "min_actions": 3, "difficulty": 4, "time_hint": 48},  # 9
    {"lanes": 4, "columns": 8, "packets": 5, "switches": 4, "holds": 1, "blockers": 2, "turns": (7, 16), "min_actions": 4, "difficulty": 5, "time_hint": 55},  # 10
    {"lanes": 4, "columns": 9, "packets": 6, "switches": 5, "holds": 1, "blockers": 2, "turns": (8, 18), "min_actions": 4, "difficulty": 5, "time_hint": 62},  # 11 bonus
    {"lanes": 4, "columns": 9, "packets": 6, "switches": 5, "holds": 2, "blockers": 2, "turns": (8, 18), "min_actions": 4, "difficulty": 5, "time_hint": 68},  # 12 bonus
    {"lanes": 4, "columns": 9, "packets": 6, "switches": 5, "holds": 2, "blockers": 3, "turns": (8, 19), "min_actions": 4, "difficulty": 5, "time_hint": 75},  # 13 bonus
)

HOLDING_PARAMS = {
    "lanes": HOLD_LANES, "columns": HOLD_COLUMNS, "packets": HOLD_PACKETS,
    "switches": HOLD_SWITCHES, "holds": 0, "blockers": 0, "turns": HOLD_TURNS,
    "min_actions": HOLD_MIN_ACTIONS, "difficulty": 1, "time_hint": 8,
}


def _params_for_level(level: int) -> dict:
    """Main-board knobs for `level`, clamped to the 1..10 table."""
    return MAIN_LEVEL_PARAMS[min(max(level, 1), len(MAIN_LEVEL_PARAMS)) - 1]


Cell = tuple[int, int]


class _Board:
    """The conveyor. Built from the public payload in `check`, so the server
    never needs anything the client cannot see."""

    def __init__(
        self,
        lanes: int,
        columns: int,
        switches: tuple[tuple[str, Cell, tuple[str, str], int], ...],
        holds: tuple[tuple[str, Cell, int], ...],
        blockers: frozenset[Cell],
        packets: tuple[tuple[str, str | None, Cell, int], ...],
        exits: tuple[str | None, ...],
        turn_cap: int,
    ) -> None:
        self.lanes = lanes
        self.columns = columns
        self.switches = switches      # (id, cell, (state a, state b), initial index)
        self.holds = holds            # (id, cell, charges)
        self.blockers = blockers
        self.packets = packets        # (id, kind, start cell, spawn tick)
        self.exits = exits            # kind accepted per lane; None = any (generation)
        self.turn_cap = turn_cap
        self.switch_at = {switch[1]: index for index, switch in enumerate(switches)}
        self.switch_by_id = {switch[0]: index for index, switch in enumerate(switches)}
        self.hold_by_id = {hold[0]: index for index, hold in enumerate(holds)}


def _initial_state(board: _Board) -> tuple:
    """(tick, packet positions, switch indices, hold charges) — all hashable."""
    return (
        0,
        tuple(WAIT for _ in board.packets),
        tuple(switch[3] for switch in board.switches),
        tuple(hold[2] for hold in board.holds),
    )


def _step(board: _Board, state: tuple, action: tuple) -> tuple | None:
    """One whole turn: spawn, apply `action`, advance everything. None = the
    attempt failed (illegal action, collision, wrong exit, or a packet lost)."""
    tick, positions, switch_index, charges = state
    positions = list(positions)

    # 1. Spawn — a packet may not arrive on top of one still sitting there.
    occupied = {position[1:] for position in positions if position[0] == "on"}
    for index, packet in enumerate(board.packets):
        if positions[index] == WAIT and packet[3] == tick:
            if packet[2] in occupied:
                return None
            positions[index] = ("on",) + packet[2]
            occupied.add(packet[2])

    # 2. The player's single action for this turn.
    held = None
    if action == PASS:
        pass
    elif len(action) == 2 and action[0] == "toggle":
        at = board.switch_by_id.get(action[1])
        if at is None:
            return None
        switch_index = tuple(
            (value + 1) % len(board.switches[i][2]) if i == at else value
            for i, value in enumerate(switch_index)
        )
    elif len(action) == 2 and action[0] == "hold":
        at = board.hold_by_id.get(action[1])
        if at is None or charges[at] <= 0:
            return None
        cell = board.holds[at][1]
        held = next(
            (
                index
                for index, position in enumerate(positions)
                if position[0] == "on" and position[1:] == cell
            ),
            None,
        )
        if held is None:
            return None                # nothing on the pad: an illegal hold
        charges = tuple(
            value - 1 if i == at else value for i, value in enumerate(charges)
        )
    else:
        return None

    # 3. Everything advances at once.
    moved = list(positions)
    for index, position in enumerate(positions):
        if position[0] != "on" or index == held:
            continue
        row, column = position[1], position[2]
        switch = board.switch_at.get((row, column))
        delta = 0 if switch is None else DELTAS[
            board.switches[switch][2][switch_index[switch]]
        ]
        row, column = row + delta, column + 1
        if not 0 <= row < board.lanes:
            return None                # ran off the top or bottom of the belt
        if column == board.columns:
            accepted = board.exits[row]
            if accepted is not None and accepted != board.packets[index][1]:
                return None            # wrong exit
            moved[index] = ("done", row)
            continue
        if (row, column) in board.blockers:
            return None
        moved[index] = ("on", row, column)

    # 4. Simultaneous movement: two packets may not land on the same cell.
    landed = [position[1:] for position in moved if position[0] == "on"]
    if len(set(landed)) != len(landed):
        return None
    return (tick + 1, tuple(moved), switch_index, charges)


def _solved(state: tuple) -> bool:
    return all(position[0] == "done" for position in state[1])


def _legal_actions(board: _Board, state: tuple) -> list[tuple[tuple, int]]:
    """Actions worth trying, with their cost: PASS is free, the rest cost one.

    Holds are offered only where they are legal, which keeps the search narrow;
    `check` still validates every submitted action itself.
    """
    options: list[tuple[tuple, int]] = [(PASS, 0)]
    options += [(("toggle", switch[0]), 1) for switch in board.switches]
    on_board = {position[1:] for position in state[1] if position[0] == "on"}
    options += [
        (("hold", hold[0]), 1)
        for index, hold in enumerate(board.holds)
        if state[3][index] > 0 and hold[1] in on_board
    ]
    return options


def _solve(board: _Board, budget: int = SOLVER_NODES) -> tuple[list[tuple], int] | None:
    """Cheapest schedule by real-action count (0-1 BFS), or None if the board
    cannot be solved inside the turn cap and the node budget."""
    start = _initial_state(board)
    queue: deque = deque([(start, [], 0)])
    best = {start: 0}
    nodes = 0
    while queue and nodes < budget:
        state, actions, cost = queue.popleft()
        if cost > best.get(state, cost):
            continue                   # a cheaper route to this state won
        if _solved(state):
            return actions, cost
        if state[0] >= board.turn_cap:
            continue
        nodes += 1
        for action, action_cost in _legal_actions(board, state):
            after = _step(board, state, action)
            if after is None:
                continue
            spent = cost + action_cost
            if spent >= best.get(after, spent + 1):
                continue
            best[after] = spent
            item = (after, actions + [action], spent)
            if action_cost:
                queue.append(item)
            else:
                queue.appendleft(item)
    return None


def _replay(board: _Board, actions: list[tuple]) -> bool:
    """True if `actions` delivers every packet without a failure."""
    state = _initial_state(board)
    if _solved(state):
        return False                   # a board with nothing to do never counts
    for action in actions:
        state = _step(board, state, action)
        if state is None:
            return False
        if _solved(state):
            return True
    return False


def _layout(rng: random.Random, params: dict) -> _Board:
    """A random unlabelled conveyor: switches, holds, blockers and packets, but
    exits that accept anything — the schedule decides what belongs where."""
    lanes, columns = params["lanes"], params["columns"]
    cells = [(row, column) for row in range(lanes) for column in range(columns)]

    taken: set[Cell] = set()
    switches = []
    for index in range(params["switches"]):
        # A junction only ever offers moves that stay on the belt, so every
        # option is a real choice rather than an instant loss.
        options = [cell for cell in cells if cell not in taken and cell[1] < columns]
        rng.shuffle(options)
        for cell in options:
            row = cell[0]
            pairs = [(STRAIGHT, DOWN)] if row == 0 else (
                [(STRAIGHT, UP)] if row == lanes - 1 else
                [(STRAIGHT, DOWN), (STRAIGHT, UP), (UP, DOWN)]
            )
            states = rng.choice(pairs)
            switches.append((f"s{index}", cell, states, rng.randrange(len(states))))
            taken.add(cell)
            break

    holds = []
    for index in range(params["holds"]):
        options = [cell for cell in cells if cell not in taken and 0 < cell[1]]
        if not options:
            break
        cell = rng.choice(options)
        holds.append((f"h{index}", cell, 1))
        taken.add(cell)

    blockers: set[Cell] = set()
    for _ in range(params["blockers"]):
        options = [cell for cell in cells if cell not in taken and cell[1] > 1]
        if not options:
            break
        cell = rng.choice(options)
        blockers.add(cell)
        taken.add(cell)

    # Distinct (lane, tick) pairs: two packets spawning into one cell on the
    # same turn would lose the board before the player touched anything.
    slots = [
        (row, tick)
        for row in range(lanes)
        for tick in range(max(2, params["packets"]))
    ]
    chosen = sorted(rng.sample(slots, params["packets"]), key=lambda slot: (slot[1], slot[0]))
    # Slide the whole schedule so the first packet arrives on turn 0: a board
    # that opens with nothing on the belt just spends its first turns passing.
    lead = chosen[0][1]
    packets = tuple(
        (f"p{index}", None, (row, 0), tick - lead)
        for index, (row, tick) in enumerate(chosen)
    )

    return _Board(
        lanes, columns, tuple(switches), tuple(holds), frozenset(blockers),
        packets, tuple(None for _ in range(lanes)), params["turn_cap"],
    )


def _random_schedule(
    rng: random.Random, board: _Board, budget: int = SCHEDULE_NODES
) -> tuple[list[tuple], tuple[int, ...]] | None:
    """Find some schedule that gets every packet off the belt, exploring in a
    random order so different seeds produce different sorting patterns.

    Depth-first with backtracking: a greedy walk dead-ends constantly once
    there are several packets in flight, because the losing move is usually
    made several turns before the crash. Returns the schedule and the exit lane
    each packet reached.
    """
    start = _initial_state(board)
    stack = [(start, [])]
    seen = {start}
    nodes = 0
    while stack and nodes < budget:
        state, actions = stack.pop()
        if _solved(state):
            return actions, tuple(position[1] for position in state[1])
        if state[0] >= board.turn_cap:
            continue
        nodes += 1
        # Explore real actions before PASS (the stack pops what was pushed
        # last): a schedule that works the junctions hard tends to label a
        # board that then *demands* those junctions, which is the whole game.
        options = [action for action, _ in _legal_actions(board, state)]
        rng.shuffle(options)
        options.sort(key=lambda action: action != PASS)
        for action in options:
            after = _step(board, state, action)
            if after is None or after in seen:
                continue
            seen.add(after)
            stack.append((after, actions + [action]))
    return None


def _label(board: _Board, exit_lanes: tuple[int, ...]) -> _Board:
    """Give each lane's exit a shape and each packet the shape of the exit its
    schedule delivered it to — so the board is solvable by construction."""
    exits = tuple(KINDS[lane] for lane in range(board.lanes))
    packets = tuple(
        (packet[0], exits[exit_lanes[index]], packet[2], packet[3])
        for index, packet in enumerate(board.packets)
    )
    return _Board(
        board.lanes, board.columns, board.switches, board.holds, board.blockers,
        packets, exits, board.turn_cap,
    )


def _payload(board: _Board, params: dict, kind: str) -> dict:
    return {
        "variant": kind,
        "difficulty": params["difficulty"],
        "time_hint_seconds": params["time_hint"],
        "rules_version": RULES_VERSION,
        "lanes": board.lanes,
        "columns": board.columns,
        "switches": [
            {
                "id": switch[0],
                "cell": list(switch[1]),
                "states": list(switch[2]),
                "initial": switch[2][switch[3]],
            }
            for switch in board.switches
        ],
        "holds": [
            {"id": hold[0], "cell": list(hold[1]), "charges": hold[2]}
            for hold in board.holds
        ],
        "blockers": [list(cell) for cell in sorted(board.blockers)],
        "packets": [
            {
                "id": packet[0],
                "kind": packet[1],
                "start": list(packet[2]),
                "spawn_tick": packet[3],
            }
            for packet in board.packets
        ],
        "exits": [
            {"lane": lane, "kind": kind_name}
            for lane, kind_name in enumerate(board.exits)
        ],
        "turn_cap": board.turn_cap,
    }


def board_from_payload(payload: dict) -> _Board:
    """Rebuild the conveyor from the public payload (checker path)."""
    switches = tuple(
        (
            switch["id"],
            (switch["cell"][0], switch["cell"][1]),
            tuple(switch["states"]),
            list(switch["states"]).index(switch["initial"]),
        )
        for switch in payload["switches"]
    )
    holds = tuple(
        (hold["id"], (hold["cell"][0], hold["cell"][1]), hold["charges"])
        for hold in payload["holds"]
    )
    packets = tuple(
        (
            packet["id"],
            packet["kind"],
            (packet["start"][0], packet["start"][1]),
            packet["spawn_tick"],
        )
        for packet in payload["packets"]
    )
    exits = [None] * payload["lanes"]
    for exit_spec in payload["exits"]:
        exits[exit_spec["lane"]] = exit_spec["kind"]
    return _Board(
        payload["lanes"],
        payload["columns"],
        switches,
        holds,
        frozenset((cell[0], cell[1]) for cell in payload["blockers"]),
        packets,
        tuple(exits),
        payload["turn_cap"],
    )


class LaneShiftGame:
    """A conveyor that moves every turn — one action at a time, sort it out."""

    id = "lane_shift"
    name = "Lane Shift"

    def generate_main(self, seed: int, level: int = 1) -> PuzzleInstance:
        return self._generate(seed, kind="main", level=level)

    def generate_holding(self, seed: int) -> PuzzleInstance:
        return self._generate(seed, kind="holding")

    def _build(self, seed: int, kind: str, level: int = 1) -> tuple[dict, str]:
        """Payload + a reference schedule (server-only, used by tests)."""
        rng = random.Random(seed)
        params = dict(_params_for_level(level) if kind == "main" else HOLDING_PARAMS)
        params["turn_cap"] = MAIN_TURN_CAP if kind == "main" else HOLD_TURN_CAP
        min_turns, max_turns = params["turns"]
        for _ in range(GEN_ATTEMPTS):
            draft = _layout(rng, params)
            for _ in range(SCHEDULE_ATTEMPTS):
                played = _random_schedule(rng, draft)
                if played is None:
                    continue
                board = _label(draft, played[1])
                if len(set(played[1])) < 2:
                    continue            # every packet to one exit reads as sorted
                solved = _solve(board)
                if solved is None:
                    continue            # unsolvable or over the search budget
                actions, cost = solved
                # `cost` is the fewest real actions any schedule needs, so this
                # also rejects boards that solve themselves on PASS alone.
                if cost < params["min_actions"]:
                    continue
                if not min_turns <= len(actions) <= max_turns:
                    continue
                payload = _payload(board, params, kind)
                answer = json.dumps(
                    {"v": RULES_VERSION, "actions": [list(action) for action in actions]}
                )
                return payload, answer
        raise RuntimeError(f"lane_shift generation failed for seed {seed}")

    def _generate(self, seed: int, kind: str, level: int = 1) -> PuzzleInstance:
        payload, answer = self._build(seed, kind, level)
        return PuzzleInstance(
            game_id=self.id,
            kind=kind,
            prompt="Take one action per turn — every packet into the exit with its own symbol.",
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
            submitted = data.get("actions")
            payload = puzzle.payload
            if not isinstance(submitted, list) or not submitted:
                return False
            if len(submitted) > payload["turn_cap"]:
                return False
            actions = []
            for action in submitted:
                if not isinstance(action, list) or not 1 <= len(action) <= 2:
                    return False
                if any(not isinstance(part, str) for part in action):
                    return False
                actions.append(tuple(action))
            return _replay(board_from_payload(payload), actions)
        except Exception:
            return False  # malformed input is just wrong, never a crash

    def reset(self) -> None:
        return None  # stateless
