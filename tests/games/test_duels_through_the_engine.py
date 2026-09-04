"""Every registered duel, played to a finish through the real engine.

The per-module suites pin the rules; this one pins the thing no single module
can check — that a duel game actually *works* inside the loop the engine runs
around it: rounds open and resolve, the window can lapse, the duel ends, the
winner goes green and their team is paid.

Two properties make this worth its runtime:

  * **A client can play from its own view alone.** The bot below picks its
    moves out of the payload the server sent *that seat*, never from engine
    state. A duel whose payload doesn't carry enough to choose a legal move
    would deadlock here, which is exactly how it would fail in a browser.
  * **Nothing leaks, at any point in a real match.** Every open round is
    checked from both seats and from a Grandmaster, over hundreds of rounds of
    real play rather than a hand-built state.
"""

from __future__ import annotations

import random

import pytest

from backend import config
from backend.engine import DUEL_SCOPE, RelayEngine
from backend.games.duel1_rps import RockPaperScissorsDuel
from backend.games.duel2_crown import CrownDuel
from backend.games.duel3_number_clash import NumberClash
from backend.games.duel4_bid_war import BidWar
from backend.games.duel_base import SIDES, other_side
from backend.models import green
from backend.registry import REGISTERED_DUELS, GameRegistry
from tests.test_engine import GAMES, LEVELS, NOW, FakeGame
from tests.test_duels import duel_match, start

# A duel that needs more rounds than this is not converging, and the engine
# would keep it open for as long as both teams kept answering.
MAX_ROUNDS = 60

DUELS = [RockPaperScissorsDuel, CrownDuel, NumberClash, BidWar]


def test_every_registered_duel_is_covered_here():
    """A new duel game joins this suite by being registered, not by hand."""
    assert {type(duel) for duel in REGISTERED_DUELS} == set(DUELS)


@pytest.fixture
def engine_for(monkeypatch):
    """An engine whose duel catalogue holds exactly the module under test."""
    monkeypatch.setattr(config, "LEVEL_COUNT", LEVELS)

    def build(module):
        registry = GameRegistry(
            modules=[FakeGame(game_id) for game_id in GAMES], duels=[module]
        )
        return RelayEngine(registry)
    return build


def legal_moves(view: dict) -> list[str]:
    """Every move this seat could make, read only from the view it was sent.

    This is deliberately the browser's information and no more: if a payload
    stopped carrying enough to choose with, the bot would run out of moves.
    """
    payload = view["payload"]
    kind = payload.get("kind")
    if kind is None:                                   # RPS: the move set
        return list(payload["moves"])
    if kind == "crown_duel":
        if payload["phase"] == "strategy":
            moves = ["normal"]
            if payload["can_sacrifice"]:
                held = [
                    card for card in payload["hand"]
                    if card["status"] == "available"
                ]
                burn, target = held[:2], held[2]
                new_type = next(
                    kind for kind in payload["transform_types"]
                    if kind != target["type"]
                )
                moves.append(
                    f"sacrifice:{burn[0]['id']}+{burn[1]['id']}"
                    f">{target['id']}={new_type}"
                )
            return moves
        return [
            card["type"] for card in payload["hand"]
            if card["status"] == "available"
        ]
    if kind == "number_clash":
        return [str(number) for number in payload["available"]]
    if kind == "bid_war":
        return [str(bid) for bid in range(payload["max_bid"] + 1)]
    raise AssertionError(f"no bot knows how to play {kind!r}")


def assert_no_leak(match, duellists, leader):
    """While the round is open, nobody may hold anyone else's move."""
    duel = match.duel
    for side, player in duellists.items():
        view = duel.public(player, match.players)
        own = duel.state.choices.get(side)
        assert view["choices"] == ({side: own} if own is not None else {})
        assert set(view["locked"]) == set(SIDES)
        assert other_side(side) not in view["choices"]
    # A Grandmaster sees neither, so there is nobody to relay a move to.
    assert duel.public(leader, match.players)["choices"] == {}


def play_out(engine, match, leader, seed: int) -> int:
    """Play the open duel to its finish. Returns the rounds it took."""
    rng = random.Random(seed)
    duellists = {side: match.players[pid] for side, pid in match.duel.sides.items()}
    for round_count in range(1, MAX_ROUNDS + 1):
        duel = match.duel
        assert duel.phase == "choosing", duel.phase
        round_index = duel.state.round_index
        assert_no_leak(match, duellists, leader)

        for side, player in duellists.items():
            view = duel.public(player, match.players)
            moves = legal_moves(view)
            assert moves, f"{side} had no legal move in round {round_index}"
            result = engine.duel_choice(
                match, player.id, duel.id, round_index,
                rng.choice(moves), now=NOW,
            )
            assert result.ok, result.error
            if duel.phase == "choosing":
                assert_no_leak(match, duellists, leader)
            else:
                # The second commit resolved the round: now both are public.
                for seat in duellists.values():
                    view = duel.public(seat, match.players)
                    assert set(view["choices"]) == set(SIDES)

        if duel.phase == "done":
            return round_count
        engine.on_duel_timer(match, DUEL_SCOPE, "duel_reveal", now=NOW)
    raise AssertionError(f"the duel did not finish inside {MAX_ROUNDS} rounds")


@pytest.mark.parametrize("module", DUELS, ids=lambda m: m.id)
@pytest.mark.parametrize("seed", range(6))
def test_a_duel_plays_to_a_finish_and_pays_the_winner(engine_for, module, seed):
    engine = engine_for(module())
    match, members, leaders = duel_match(engine)
    start(engine, match)
    duel = match.duel
    assert duel.module.id == module.id

    rounds = play_out(engine, match, leaders["alpha"], seed)
    assert 1 <= rounds <= MAX_ROUNDS

    winner_side = duel.winner_side
    assert winner_side in SIDES
    winner = match.players[duel.sides[winner_side]]
    loser = match.players[duel.sides[other_side(winner_side)]]
    assert green(winner)
    # Losing holds your team back only while another duel is still coming. A
    # staked duel is fought once a level, so its loser stands down green with
    # the winner and carries the once-per-level advance lock instead.
    series_over = match.duels_played >= match.config_snapshot["duels_per_level"]
    assert green(loser) is series_over
    assert series_over == bool(getattr(module, "staked", False))
    assert match.teams[duel.team_of[winner_side]].currency >= config.DUEL_WIN_CURRENCY
    assert winner.coins_earned >= config.DUEL_WIN_CURRENCY
    # Both moves are public now, and the duel says who took it.
    revealed = duel.public(loser, match.players)
    assert set(revealed["choices"]) == set(SIDES)


@pytest.mark.parametrize("module", DUELS, ids=lambda m: m.id)
def test_a_duel_where_nobody_answers_still_resolves(engine_for, module):
    """Both windows lapse, round after round. A double no-show ties, so the
    duel keeps opening rounds rather than hanging — and never crashes."""
    engine = engine_for(module())
    match, _, _ = duel_match(engine)
    start(engine, match)
    duel = match.duel
    for _ in range(5):
        engine.on_duel_timer(match, DUEL_SCOPE, "duel_round", now=NOW)
        if duel.phase == "done":
            break
        assert duel.phase == "reveal"
        engine.on_duel_timer(match, DUEL_SCOPE, "duel_reveal", now=NOW)
        assert duel.phase == "choosing"


@pytest.mark.parametrize("module", DUELS, ids=lambda m: m.id)
def test_one_side_answering_alone_takes_the_duel(engine_for, module):
    """Stalling is never better than guessing: a Duelist who plays every round
    against a Duelist who plays none wins, whatever the game is."""
    engine = engine_for(module())
    match, _, leaders = duel_match(engine)
    start(engine, match)
    duel = match.duel
    active = match.players[duel.sides["a"]]

    for _ in range(MAX_ROUNDS):
        view = duel.public(active, match.players)
        engine.duel_choice(
            match, active.id, duel.id, duel.state.round_index,
            legal_moves(view)[-1], now=NOW,
        )
        engine.on_duel_timer(match, DUEL_SCOPE, "duel_round", now=NOW)
        if duel.phase == "done":
            break
        engine.on_duel_timer(match, DUEL_SCOPE, "duel_reveal", now=NOW)
    assert duel.winner_side == "a"
