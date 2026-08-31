"""The design gallery: every page and every screen state, on demand.

Not part of the game. Most of this product only exists inside a running match —
the play view, the wait-or-bonus choice, the Grandmaster's dashboard, the duel
card in each of its phases — which makes the screens you most want to redesign
the hardest ones to *look at*. This builds a throwaway match through the real
engine, drives it to the state you asked for, and hands the snapshot to the
ordinary client. What you see is the real components with real shapes, not a
mock that drifts away from the app a month later.

Two rules keep it honest:

  * **The engine makes the states, not this file.** A cleared player is put
    there by the same `_go_cleared` a correct answer calls; a duel in reveal
    got there by both seats committing a legal move. Only the finished-match
    scoreline is stamped by hand, because playing ten levels out to reach it
    would tell you nothing a flag doesn't.
  * **Nothing is stored.** Every request builds a fresh match with its own
    engine, and it never enters the match store, so no real game can collide
    with a preview and no preview outlives its request.

Hidden behind `?key=` (`PREVIEW_KEY`, override with `RELAY_PREVIEW_KEY`). A
wrong key is a 404 rather than a 403, so the path does not announce itself —
but the default key is in this file, and this file is on GitHub. Treat it as a
closed door, not a locked one, and set the environment variable if the server
is reachable by anyone else. The door is worth little anyway: what is behind it
is dummy players on a throwaway match, never anybody's real one.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from backend import config
from backend.engine import DUEL_SCOPE, EngineResult, RelayEngine
from backend.games.duel_base import DuelModule
from backend.models import Match
from backend.registry import REGISTERED_DUELS, REGISTERED_MODULES, GameRegistry

PREVIEW_KEY = os.environ.get("RELAY_PREVIEW_KEY", "dev")
PREVIEW_PATH = "/preview"

# The two seats that carry a game, and the games they get. Distinct per team,
# which the lobby requires.
SOLVER_GAMES = ("rewire", "sweep")

# Dummy squads. Names, not "Player 1", because a roster full of placeholders
# reads differently from a roster full of people.
SQUADS = {
    "alpha": ("Ada", "Bo", "Cass", "Dev", "Eze"),
    "bravo": ("Fern", "Gus", "Halle", "Ike", "Jo"),
}


def enabled(key: str | None) -> bool:
    return bool(key) and key == PREVIEW_KEY


def _now() -> datetime:
    # Real wall-clock, so every countdown in the gallery actually ticks.
    return datetime.now(timezone.utc)


def _engine(duel: DuelModule | None = None) -> RelayEngine:
    """A private engine. `duel` pins the duel catalogue so a preview of one
    duel game does not get whichever the server would have picked."""
    duels = [duel] if duel is not None else None
    return RelayEngine(GameRegistry(REGISTERED_MODULES, duels=duels))


def _seated_match(engine: RelayEngine) -> tuple[Match, dict, dict]:
    """Two full squads, roles assigned, one step short of kickoff."""
    now = _now()
    match = engine.create_match()
    match.min_players = 4
    seats: dict[str, list] = {}
    leaders: dict[str, Any] = {}
    for team_id, names in SQUADS.items():
        leader, _ = engine.join_match(match, names[0], team_id, now=now)
        engine.claim_leader(match, leader.id)
        leaders[team_id] = leader
        seats[team_id] = []
        for index, name in enumerate(names[1:]):
            player, _ = engine.join_match(match, name, team_id, now=now)
            seats[team_id].append(player)
            role = ("defuser", "duelist", "generalist", "generalist")[index]
            engine.assign_role(match, leader.id, player.id, role)
            if role == "generalist":
                engine.assign_game(
                    match, leader.id, player.id, SOLVER_GAMES[index - 2]
                )
    return match, seats, leaders


def _started(engine: RelayEngine) -> tuple[Match, dict, dict]:
    match, seats, leaders = _seated_match(engine)
    engine.start_match(match, now=_now())
    return match, seats, leaders


def _clear(engine: RelayEngine, match: Match, player) -> None:
    """Take a player to cleared through the engine's own transition.

    Not through `submit_answer`: an action game keeps no replayable answer on
    the instance (the module recomputes correctness from the interaction), so
    there is nothing a fixture could submit. `_go_cleared` is what a correct
    submission calls, and calling it directly is the difference between the
    engine producing this state and a preview inventing it.
    """
    engine._go_cleared(match, player, EngineResult(), _now())


def _duel_moves(module: DuelModule, state, side: str) -> list[str]:
    """Legal moves for a seat, read from the view that seat would be sent.

    The same shape as the bot in tests/games/test_duels_through_the_engine.py.
    It lives twice because production code does not import tests, and both
    copies are pinned by that suite: a duel whose payload stopped carrying
    enough to choose from would fail there first.
    """
    payload = module.public(state, side, False)["payload"]
    kind = payload.get("kind")
    if kind is None:
        return list(payload["moves"])
    if kind == "crown_duel":
        if payload["phase"] == "strategy":
            return ["normal"]
        return [
            card["type"] for card in payload["hand"]
            if card["status"] == "available"
        ]
    if kind == "number_clash":
        return [str(number) for number in payload["available"]]
    if kind == "bid_war":
        return [str(bid) for bid in range(payload["max_bid"] + 1)]
    return []


def _play_a_duel_round(engine: RelayEngine, match: Match) -> None:
    """Commit both seats, so the duel lands on its reveal beat.

    One plays high and one plays low: two identical picks would only ever draw,
    and a draw is the least interesting reveal to look at. Crown Duel spends a
    whole round on its secret strategy beat, so if that is what resolved, this
    steps past it to the round that puts cards on the table.
    """
    for _ in range(2):
        duel = match.duel
        for side, pick in (("a", -1), ("b", 0)):
            moves = _duel_moves(duel.module, duel.state, side)
            if not moves:
                continue
            engine.duel_choice(
                match, duel.sides[side], duel.id, duel.state.round_index,
                moves[pick], now=_now(),
            )
        last = duel.last_round or {}
        if last.get("a") not in ("normal", "sacrifice"):
            return
        engine.on_duel_timer(match, DUEL_SCOPE, "duel_reveal", now=_now())


def _duel_module(game_id: str | None) -> DuelModule:
    for duel in REGISTERED_DUELS:
        if duel.id == game_id:
            return duel
    return REGISTERED_DUELS[0]


# --- the scenarios --------------------------------------------------------

def snapshot(state: str, **params: str) -> dict[str, Any] | None:
    """The match snapshot for one gallery entry, as its viewer would receive
    it. None if the scenario name is unknown."""
    if state == "lobby":
        engine = _engine()
        match, _, leaders = _seated_match(engine)
        # Alpha's Grandmaster is the first joiner, so they are the host too:
        # one viewer, both the host panel and the assignment panel.
        return match.public(leaders["alpha"].id)

    if state == "leader":
        engine = _engine()
        match, seats, leaders = _started(engine)
        _clear(engine, match, seats["alpha"][2])   # a green row on the roster
        match.teams["alpha"].currency = 14         # enough to shop the perks
        return match.public(leaders["alpha"].id)

    if state in ("solving", "cleared", "bonus"):
        engine = _engine()
        match, seats, _ = _started(engine)
        solver = seats["alpha"][2]
        if state in ("cleared", "bonus"):
            _clear(engine, match, solver)
        if state == "bonus":
            engine.choose_bonus(match, solver.id, now=_now())
        effect = params.get("effect")
        if effect in config.SCREEN_EFFECTS:
            # Cosmetic sabotage the server stamps on one player; the client
            # draws it. Worth looking at without buying the perk to get there.
            solver.screen_effects[effect] = _effect_deadline(effect)
        return match.public(solver.id)

    if state == "duel":
        module = _duel_module(params.get("game"))
        engine = _engine(module)
        match, seats, _ = _started(engine)
        if params.get("phase") == "reveal":
            _play_a_duel_round(engine, match)
        return match.public(seats["alpha"][1].id)

    if state in ("won", "lost"):
        engine = _engine()
        match, seats, _ = _started(engine)
        # The only stamped state in here: playing ten levels out to reach a
        # scoreline would show you nothing the flag doesn't.
        match.status = "finished"
        match.winner_team_id = "alpha" if state == "won" else "bravo"
        return match.public(seats["alpha"][2].id)

    return None


def _effect_deadline(effect: str) -> str:
    """The same UTC ISO stamp `buy_perk` would leave, for the perk that lands
    this effect, so the client renders it on exactly the real terms."""
    seconds = next(
        (perk.get("seconds", 20) for perk in config.PERKS.values()
         if perk.get("effect") == effect),
        20,
    )
    return (_now() + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


# --- the gallery ----------------------------------------------------------

def _link(key: str, state: str, label: str, note: str, **params: str) -> str:
    query = "".join(f"&{name}={value}" for name, value in params.items())
    href = f"/play?preview={state}{query}&key={key}"
    return (
        f'<li><a href="{href}"><strong>{label}</strong>'
        f'<span>{note}</span></a></li>'
    )


def _plain(href: str, label: str, note: str) -> str:
    return f'<li><a href="{href}"><strong>{label}</strong><span>{note}</span></a></li>'


def gallery_html(key: str) -> str:
    """Every page and every screen state this product has, in one list.

    Built from the registries, so a newly registered game or duel appears here
    without anyone remembering to add it.
    """
    pages = "\n".join([
        _plain("/", "Landing", "the public front page"),
        _plain("/games", "Rules", "every game and duel, written out"),
        _plain("/explore", "Practice mode", "solo boards, no match"),
        _plain("/play", "Join", "the app shell with no session: the join view"),
    ])
    boards = "\n".join(
        _plain(f"/explore?game={module.id}", module.name, "practice board")
        for module in REGISTERED_MODULES
    )
    shell = "\n".join([
        _link(key, "lobby", "Lobby", "host controls and the assignment panel"),
        _link(key, "solving", "Playing", "a board mid-match, in the race frame"),
        _link(key, "cleared", "Cleared", "the wait-or-bonus choice, clock running"),
        _link(key, "bonus", "Bonus board", "the gamble, on a harder board"),
        _link(key, "leader", "Grandmaster", "the dashboard: roster, perks, shop"),
        _link(key, "won", "Win screen", "match finished, your team took it"),
        _link(key, "lost", "Loss screen", "match finished, the other team did"),
    ])
    effects = "\n".join(
        _link(key, "solving", effect.title(), "screen-effect perk, landed on you",
              effect=effect)
        for effect in config.SCREEN_EFFECTS
    )
    duels = "\n".join(
        _link(key, "duel", f"{duel.name} · {phase}",
              "choosing a move" if phase == "open" else "both moves on the table",
              game=duel.id, phase="choosing" if phase == "open" else "reveal")
        for duel in REGISTERED_DUELS
        for phase in ("open", "reveal")
    )
    return TEMPLATE.format(
        pages=pages, boards=boards, shell=shell, effects=effects, duels=duels,
        key=key,
    )


TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Relay design gallery</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 32px 20px 64px;
    font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    background: #16151d; color: #e9e7f2;
  }}
  main {{ max-width: 940px; margin: 0 auto; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 4px; }}
  p.lede {{ color: #a5a1bb; margin: 0 0 8px; max-width: 62ch; }}
  h2 {{
    font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.14em;
    color: #a5a1bb; margin: 34px 0 10px;
  }}
  ul {{
    list-style: none; margin: 0; padding: 0;
    display: grid; gap: 8px;
    grid-template-columns: repeat(auto-fill, minmax(232px, 1fr));
  }}
  a {{
    display: block; padding: 11px 14px; text-decoration: none;
    border: 1px solid #322f42; border-radius: 10px;
    background: #1e1d28; color: inherit;
  }}
  a:hover, a:focus-visible {{ border-color: #7d63ff; background: #232131; }}
  strong {{ display: block; font-weight: 650; }}
  span {{ color: #a5a1bb; font-size: 0.83rem; }}
  footer {{ margin-top: 40px; color: #6f6b86; font-size: 0.83rem; max-width: 62ch; }}
  code {{ background: #232131; padding: 1px 5px; border-radius: 4px; }}
</style>
</head>
<body>
<main>
  <h1>Design gallery</h1>
  <p class="lede">Every page and every screen state, none of it behind a real
    match. The shell entries below run the actual client against a throwaway
    match built by the engine, so what you see is the live components.</p>
  <p class="lede">These are read-only: buttons render but send nothing, because
    there is no socket behind them.</p>

  <h2>Pages</h2>
  <ul>{pages}</ul>

  <h2>Match states</h2>
  <ul>{shell}</ul>

  <h2>Duels</h2>
  <ul>{duels}</ul>

  <h2>Screen-effect perks</h2>
  <ul>{effects}</ul>

  <h2>Practice boards</h2>
  <ul>{boards}</ul>

  <footer>
    <p>Hidden behind <code>?key={key}</code>. The default key is in the source
    and the source is public, so this is a closed door rather than a locked
    one. Set <code>RELAY_PREVIEW_KEY</code> if the server is reachable by
    anyone but you. Nothing real is behind it either way: every entry builds a
    fresh match of dummy players that is never stored.</p>
  </footer>
</main>
</body>
</html>
"""
