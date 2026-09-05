"""The shared duel round loop: commit, score, reveal, next round.

`backend/duelloop.py` was lifted out of the engine so the link duels in
`backend/duelroom.py` could score rounds the same way a match does, rather than
carrying a second implementation that would drift. The thing it would drift on
is the contract three of the four duel modules depend on: they score themselves
and return None from `resolve_round` to mean "replay", which is how a game with
sudden death and refreshing hands rides a loop that only counts round wins
(docs/DUEL_MODULE_SPEC.md §4.2).

So these tests are mostly about the tie path and the exact refusal wording. The
wording matters because it reaches players through the socket, and because
tests/test_duels.py asserts on it from the other side of the engine.
"""

from __future__ import annotations

import pytest

from backend import duelloop
from backend.games.duel1_rps import RockPaperScissorsDuel
from backend.models import DuelSession


def a_duel(wins_needed: int = 2) -> DuelSession:
    """One RPS duel between two bare seat ids. No match, no teams."""
    module = RockPaperScissorsDuel()
    module.wins_needed = wins_needed
    return DuelSession(
        id="d1",
        module=module,
        state=module.new_duel(seed=7),
        sides={"a": "s_alice", "b": "s_bob"},
    )


# --- committing a move ----------------------------------------------------

def test_a_move_is_recorded_and_the_round_stays_open():
    duel = a_duel()
    both, error = duelloop.apply_choice(duel, "s_alice", "d1", 1, "rock")
    assert error is None
    assert both is False  # one seat is not a round
    assert duel.state.choices == {"a": "rock"}


def test_the_second_move_closes_the_round():
    duel = a_duel()
    duelloop.apply_choice(duel, "s_alice", "d1", 1, "rock")
    both, error = duelloop.apply_choice(duel, "s_bob", "d1", 1, "paper")
    assert error is None and both is True


@pytest.mark.parametrize("seat, duel_id, rnd, choice, expected", [
    ("s_alice", "wrong", 1, "rock", "no duel to answer"),
    ("s_alice", "d1", 99, "rock", "that round is over"),
    ("s_nobody", "d1", 1, "rock", "you aren't in this duel"),
    ("s_alice", "d1", 1, "trebuchet", "not a legal move"),
])
def test_every_refusal_says_exactly_what_it_always_said(
    seat, duel_id, rnd, choice, expected
):
    """These strings reach a player through the socket, and the engine's own
    suite asserts on them from the other side. Rewording one is a protocol
    change, not a tidy-up."""
    duel = a_duel()
    both, error = duelloop.apply_choice(duel, seat, duel_id, rnd, choice)
    assert error == expected and both is False


def test_a_seat_cannot_choose_twice_in_one_round():
    duel = a_duel()
    duelloop.apply_choice(duel, "s_alice", "d1", 1, "rock")
    both, error = duelloop.apply_choice(duel, "s_alice", "d1", 1, "paper")
    assert error == "you already chose this round"
    assert duel.state.choices["a"] == "rock"  # the first answer stands


def test_a_closed_round_takes_no_more_moves():
    duel = a_duel()
    duel.phase = "reveal"
    both, error = duelloop.apply_choice(duel, "s_alice", "d1", 1, "rock")
    assert error == "the round is closed"


# --- scoring --------------------------------------------------------------

def test_a_won_round_is_stamped_and_counted():
    duel = a_duel()
    duel.state.choices = {"a": "rock", "b": "scissors"}
    decided = duelloop.score_round(duel)
    assert decided is False  # one win, and RPS wants two
    assert duel.state.wins == {"a": 1, "b": 0}
    assert duel.last_round == {"round": 1, "a": "rock", "b": "scissors", "winner": "a"}
    assert duel.state.history == [duel.last_round]
    assert duel.phase == "reveal"
    assert duel.winner_side is None


def test_the_duel_is_decided_only_at_wins_needed():
    duel = a_duel(wins_needed=2)
    duel.state.choices = {"a": "rock", "b": "scissors"}
    assert duelloop.score_round(duel) is False
    duelloop.open_next_round(duel)
    duel.state.choices = {"a": "paper", "b": "rock"}
    assert duelloop.score_round(duel) is True
    assert duel.winner_side == "a"
    # A decided duel is left for the caller to finish: paying it out is a
    # match's business, and a link duel has nothing to pay.
    assert duel.phase != "done"


def test_a_tie_scores_nothing_and_replays_the_round():
    """The path Crown Duel, Number Clash and Bid War all ride: they score
    themselves and return None to carry their own game forward."""
    duel = a_duel()
    duel.state.choices = {"a": "rock", "b": "rock"}
    decided = duelloop.score_round(duel)
    assert decided is False
    assert duel.state.wins == {"a": 0, "b": 0}
    assert duel.last_round["winner"] is None
    assert duel.phase == "reveal"


def test_the_reveal_keeps_the_choices_and_the_next_round_clears_them():
    duel = a_duel()
    duel.state.choices = {"a": "rock", "b": "scissors"}
    duelloop.score_round(duel)
    assert duel.state.choices == {"a": "rock", "b": "scissors"}  # the reveal beat

    duelloop.open_next_round(duel)
    assert duel.state.choices == {}
    assert duel.state.round_index == 2
    assert duel.last_round is None
    assert duel.phase == "choosing"
    assert duel.state.history  # but the record of it survives


# --- what this module is not ---------------------------------------------

def test_the_loop_knows_nothing_about_matches():
    """The whole point of the extraction. If this module ever reaches for a
    Match or the engine, a link duel has quietly become a match again.

    Read the imports rather than the text: the docstring has to be free to
    explain what the module deliberately does not do.
    """
    import ast

    tree = ast.parse(open("backend/duelloop.py").read())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)

    assert "backend.engine" not in imported
    assert "backend.config" not in imported
    assert "backend.models.Match" not in imported
    # DuelSession is the one model it needs, and it is the one the room shares.
    assert imported <= {
        "__future__", "__future__.annotations", "typing", "typing.Any",
        "backend.models", "backend.models.DuelSession",
    }, imported
