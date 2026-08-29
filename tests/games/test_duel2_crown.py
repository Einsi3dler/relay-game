"""CROWN DUEL module suite — docs/DUEL_MODULE_SPEC.md §"Tests your duel must
ship with", plus the rules the handoff spec calls out by name.

Two things here are worth more than the rest:

  * **the matchup table** — all 25 ordered pairings, because "only the same
    card draws" is the sentence the whole game rests on;
  * **the Royal Sacrifice secret** — the opponent must learn *that* a sacrifice
    happened and nothing else. Every leak assertion below is scoped to the
    served view, not to internal state, because the served view is the only
    thing an opponent can reach.
"""

from __future__ import annotations

import pytest

from backend.games.duel2_crown import (
    CARD_TYPES,
    CROWNS_NEEDED,
    DEFEATS,
    NORMAL_ROUNDS,
    TRANSFORM_TYPES,
    CrownDuel,
    resolve_crown_cards,
)
from backend.games.duel_base import (
    DUEL_RULES_VERSION,
    MAX_CHOICE_CHARS,
    SIDES,
    DuelState,
    other_side,
)


@pytest.fixture
def duel() -> CrownDuel:
    return CrownDuel()


def commit(duel: CrownDuel, state: DuelState, a: str | None, b: str | None):
    """Both seats submit and the round resolves — the engine's loop, inlined.

    `None` is a Duelist who let the window lapse. Returns the module's verdict:
    a side once it has taken the duel, None while the duel is still running.
    """
    for side, choice in (("a", a), ("b", b)):
        if choice is None:
            continue
        move = duel.normalize_choice(state, choice, side)
        assert move is not None, f"{side} could not play {choice!r}"
        state.choices[side] = move
    winner = duel.resolve_round(state)
    state.history.append({
        "round": state.round_index,
        "a": state.choices.get("a"),
        "b": state.choices.get("b"),
        "winner": winner,
    })
    if winner is not None:
        state.wins[winner] += 1
    else:
        state.choices.clear()          # the engine clears after the reveal beat
        state.round_index += 1
    return winner


def combat_state(duel: CrownDuel) -> DuelState:
    """A fresh duel with the opening strategy round played out normally, so the
    next commit is a card."""
    state = duel.new_duel(seed=1)
    commit(duel, state, "normal", "normal")
    assert state.private["phase"] == "combat"
    return state


def play(duel: CrownDuel, state: DuelState, a: str | None, b: str | None):
    """Play a *card* round, stepping over the strategy beat if one is open.

    A Crown Duel round is two engine rounds while either Duelist can still
    sacrifice, so a test that only cares about cards would otherwise have to
    spell out every "play normally" in between.
    """
    if state.private["phase"] == "strategy":
        commit(duel, state, "normal", "normal")
    return commit(duel, state, a, b)


def sacrifice(duel: CrownDuel, state: DuelState, side: str, burn, target, new_type):
    """Submit a Royal Sacrifice for `side` and lock it in, as the engine would."""
    move = duel.normalize_choice(
        state, f"sacrifice:{burn[0]}+{burn[1]}>{target}={new_type}", side
    )
    if move is not None:
        state.choices[side] = move
    return move


def to_sudden_death(duel: CrownDuel) -> DuelState:
    """Three drawn rounds: level at 0-0, so both hands are dealt fresh and the
    duel plays on a round at a time."""
    state = combat_state(duel)
    for card in ("king", "knight", "guard"):
        play(duel, state, card, card)
    assert state.private["sudden_death"] is True
    return state


def spent_sacrifice_to_sudden_death(duel: CrownDuel) -> DuelState:
    """a rewrites their hand in round one, then the match runs level through
    three rounds — so Sudden Death deals fresh cards to a Duelist whose one
    Royal Sacrifice is already gone."""
    state = duel.new_duel(2)
    sacrifice(duel, state, "a", ("c1", "c2"), "c3", "peasant")
    commit(duel, state, None, "normal")      # a is locked; b plays normally
    play(duel, state, "peasant", "king")     # Peasant takes the King: 1-0
    play(duel, state, "assassin", "knight")  # Knight takes the Assassin: 1-1
    play(duel, state, "peasant", "peasant")  # a draw leaves it level
    assert state.private["sudden_death"] is True
    return state


def hand_types(state: DuelState, side: str) -> list[str]:
    return [
        card["type"] for card in state.private["hands"][side]
        if card["status"] == "available"
    ]


# --- Generation ---

def test_determinism(duel):
    first, second = duel.new_duel(11), duel.new_duel(11)
    assert first.payload == second.payload
    assert first.private == second.private
    assert first.duel_game_id == second.duel_game_id == "crown_duel"


def test_both_hands_start_with_one_of_every_card(duel):
    state = duel.new_duel(3)
    for side in SIDES:
        assert hand_types(state, side) == list(CARD_TYPES)
        assert not state.private["sacrifice_used"][side]
    assert state.private["phase"] == "strategy"   # the round opens on strategy
    assert state.private["crowns"] == {"a": 0, "b": 0}
    assert state.round_index == 1 and state.choices == {}


def test_the_module_declares_its_own_time_cost(duel):
    assert duel.choice_seconds == 10
    # The module scores the match, so one returned winner *is* the duel.
    assert duel.wins_needed == 1
    assert duel._payload(duel.new_duel(1), "a")["choice_seconds"] == 10


# --- The matchup table (the sentence the game rests on) ---

@pytest.mark.parametrize("card", CARD_TYPES)
def test_a_card_against_itself_is_the_only_draw(duel, card):
    assert resolve_crown_cards(card, card) is None


def test_every_ordered_pairing_is_decided(duel):
    """All 25: same card draws, every other pair has exactly one winner."""
    for a in CARD_TYPES:
        for b in CARD_TYPES:
            winner = resolve_crown_cards(a, b)
            if a == b:
                assert winner is None
            else:
                assert winner in SIDES, f"{a} vs {b} was undecided"


@pytest.mark.parametrize("winner,loser", [
    ("king", "knight"), ("king", "guard"), ("king", "assassin"),
    ("peasant", "king"),
    ("knight", "assassin"), ("knight", "peasant"),
    ("guard", "knight"), ("guard", "peasant"),
    ("assassin", "guard"), ("assassin", "peasant"),
])
def test_the_rules_as_written(duel, winner, loser):
    """King beats all fighters, Peasant beats King, all fighters beat Peasant,
    Guard > Knight > Assassin > Guard."""
    assert resolve_crown_cards(winner, loser) == "a"
    assert resolve_crown_cards(loser, winner) == "b"


def test_the_table_is_symmetric(duel):
    """Swapping the seats swaps the winner — the module can't favour a side."""
    for a in CARD_TYPES:
        for b in CARD_TYPES:
            forward = resolve_crown_cards(a, b)
            mirrored = resolve_crown_cards(b, a)
            if forward is None:
                assert mirrored is None
            else:
                assert mirrored == other_side(forward)


def test_defeat_table_is_total_over_different_cards(duel):
    """No card is undefeated and none is unbeatable: each is named as a loser
    somewhere, and beats at least one other."""
    beaten = {card for beats in DEFEATS.values() for card in beats}
    assert beaten == set(CARD_TYPES)
    assert all(DEFEATS[card] for card in CARD_TYPES)


# --- Playing a round ---

def test_a_won_round_pays_one_crown_and_spends_both_cards(duel):
    state = combat_state(duel)
    assert commit(duel, state, "king", "knight") is None
    assert state.private["crowns"] == {"a": 1, "b": 0}
    assert "king" not in hand_types(state, "a")
    assert "knight" not in hand_types(state, "b")


def test_a_drawn_round_still_spends_both_cards(duel):
    state = combat_state(duel)
    commit(duel, state, "guard", "guard")
    assert state.private["crowns"] == {"a": 0, "b": 0}
    assert "guard" not in hand_types(state, "a")
    assert "guard" not in hand_types(state, "b")


def test_the_duel_ends_the_moment_a_second_crown_lands(duel):
    state = combat_state(duel)
    assert commit(duel, state, "king", "knight") is None
    assert state.private["crowns"]["a"] == 1
    assert play(duel, state, "guard", "peasant") == "a"
    assert state.private["crowns"]["a"] == CROWNS_NEEDED


@pytest.mark.parametrize("card", CARD_TYPES)
def test_letting_the_window_lapse_loses_the_round(duel, card):
    state = combat_state(duel)
    commit(duel, state, card, None)
    assert state.private["crowns"] == {"a": 1, "b": 0}
    state = combat_state(duel)
    commit(duel, state, None, card)
    assert state.private["crowns"] == {"a": 0, "b": 1}


def test_a_double_no_show_is_a_draw_that_spends_nothing(duel):
    state = combat_state(duel)
    assert commit(duel, state, None, None) is None
    assert state.private["crowns"] == {"a": 0, "b": 0}
    assert hand_types(state, "a") == list(CARD_TYPES)


def test_a_card_cannot_be_played_twice(duel):
    state = combat_state(duel)
    commit(duel, state, "king", "peasant")   # Peasant takes the King
    commit(duel, state, "normal", "normal")  # step over the strategy beat
    assert duel.normalize_choice(state, "king", "a") is None
    assert duel.normalize_choice(state, "peasant", "b") is None
    assert duel.normalize_choice(state, "knight", "a") == "knight"


# --- Royal Sacrifice ---

def test_a_sacrifice_burns_two_cards_and_rewrites_a_third(duel):
    state = duel.new_duel(2)
    # King and Knight burn; the Assassin comes back as a second Peasant.
    assert sacrifice(duel, state, "a", ("c1", "c2"), "c4", "peasant") == "sacrifice"
    commit(duel, state, None, "normal")   # a is already locked by the line above
    assert sorted(hand_types(state, "a")) == ["guard", "peasant", "peasant"]
    assert state.private["sacrifice_used"] == {"a": True, "b": False}
    assert hand_types(state, "b") == list(CARD_TYPES)   # b paid nothing


def test_duplicate_identities_are_legal_and_both_playable(duel):
    state = duel.new_duel(2)
    sacrifice(duel, state, "a", ("c1", "c2"), "c3", "peasant")  # King, Knight go
    commit(duel, state, None, "normal")
    assert hand_types(state, "a").count("peasant") == 2
    commit(duel, state, "peasant", "king")   # Peasant beats King
    assert hand_types(state, "a").count("peasant") == 1
    assert state.private["crowns"]["a"] == 1


def test_a_sacrifice_can_never_create_a_king(duel):
    state = duel.new_duel(2)
    assert sacrifice(duel, state, "a", ("c2", "c3"), "c5", "king") is None
    assert "king" not in TRANSFORM_TYPES
    assert state.private["pending"]["a"] is None


def test_a_sacrifice_needs_exactly_two_destroyed_cards(duel):
    state = duel.new_duel(2)
    assert duel.normalize_choice(state, "sacrifice:c1>c3=guard", "a") is None
    assert duel.normalize_choice(state, "sacrifice:c1+c2+c3>c4=guard", "a") is None
    assert duel.normalize_choice(state, "sacrifice:c1+c1>c3=guard", "a") is None


def test_the_target_cannot_be_one_of_the_destroyed_cards(duel):
    state = duel.new_duel(2)
    assert sacrifice(duel, state, "a", ("c1", "c2"), "c1", "guard") is None


def test_a_rewrite_that_changes_nothing_is_rejected(duel):
    state = duel.new_duel(2)
    # c2 is already the Knight.
    assert sacrifice(duel, state, "a", ("c1", "c3"), "c2", "knight") is None


def test_a_sacrifice_cannot_touch_a_spent_card(duel):
    state = combat_state(duel)
    commit(duel, state, "king", "knight")
    # a's King is played; naming it in a sacrifice is not a legal move.
    assert sacrifice(duel, state, "a", ("c1", "c2"), "c3", "guard") is None
    assert sacrifice(duel, state, "a", ("c2", "c3"), "c1", "guard") is None


def test_royal_sacrifice_is_once_per_duellist_per_duel(duel):
    state = spent_sacrifice_to_sudden_death(duel)
    # Both hands are five cards again, so a short hand cannot be the reason
    # this is refused — only that a has already had their one rewrite.
    assert len(hand_types(state, "a")) == 5
    assert sacrifice(duel, state, "a", ("c1", "c2"), "c3", "peasant") is None
    assert sacrifice(duel, state, "b", ("c1", "c2"), "c3", "peasant") == "sacrifice"


def test_a_hand_too_short_cannot_pay_for_a_sacrifice(duel):
    """Two cards destroyed and a third to play means a sacrifice needs three."""
    state = to_sudden_death(duel)            # fresh hands, b never sacrificed
    play(duel, state, "king", "king")
    play(duel, state, "knight", "knight")    # three cards left: still payable
    assert duel._can_sacrifice(state.private, "b") is True
    play(duel, state, "guard", "guard")
    assert len(hand_types(state, "b")) == 2
    assert state.private["sacrifice_used"]["b"] is False
    assert duel._can_sacrifice(state.private, "b") is False
    # Nobody can pay any more, so the strategy beat stops being offered at all.
    assert state.private["phase"] == "combat"


def test_the_strategy_round_is_skipped_once_nobody_can_sacrifice(duel):
    state = duel.new_duel(2)
    commit(duel, state, "normal", "normal")
    commit(duel, state, "king", "king")
    # Both still hold four cards and their sacrifice, so the beat comes round.
    assert state.private["phase"] == "strategy"
    sacrifice(duel, state, "a", ("c2", "c3"), "c4", "peasant")
    sacrifice(duel, state, "b", ("c2", "c3"), "c4", "peasant")
    commit(duel, state, None, None)          # both are locked already
    assert state.private["sacrifice_used"] == {"a": True, "b": True}
    commit(duel, state, "peasant", "peasant")
    assert state.private["phase"] == "combat"   # nothing left to decide


def test_a_strategy_round_never_pays_a_crown(duel):
    state = duel.new_duel(2)
    assert commit(duel, state, "normal", "normal") is None
    assert state.private["crowns"] == {"a": 0, "b": 0}


def test_silence_in_the_strategy_round_is_playing_normally(duel):
    """Nothing is at stake in a strategy round, so a lapsed window forfeits
    nothing — it just means no sacrifice."""
    state = duel.new_duel(2)
    assert commit(duel, state, None, None) is None
    assert state.private["sacrifice_used"] == {"a": False, "b": False}
    assert state.private["phase"] == "combat"


def test_a_card_move_is_illegal_during_the_strategy_round(duel):
    state = duel.new_duel(2)
    assert state.private["phase"] == "strategy"
    assert duel.normalize_choice(state, "king", "a") is None


def test_a_strategy_move_is_illegal_during_the_combat_round(duel):
    state = combat_state(duel)
    assert duel.normalize_choice(state, "normal", "a") is None
    assert duel.normalize_choice(state, "sacrifice:c1+c2>c3=guard", "a") is None


# --- Sudden Death ---

def test_a_tied_match_refreshes_both_hands_and_plays_on(duel):
    state = to_sudden_death(duel)
    private = state.private
    assert private["game_round"] == NORMAL_ROUNDS + 1
    assert private["crowns"] == {"a": 0, "b": 0}
    for side in SIDES:
        assert hand_types(state, side) == list(CARD_TYPES)


def test_the_first_sudden_death_round_that_is_not_a_draw_takes_the_duel(duel):
    state = to_sudden_death(duel)
    assert play(duel, state, "guard", "guard") is None       # still level
    assert play(duel, state, "peasant", "king") == "a"       # and now it isn't
    assert state.private["crowns"] == {"a": 1, "b": 0}       # one Crown is enough


def test_more_crowns_after_three_rounds_takes_the_duel(duel):
    """A 1-0 lead after the third round wins it — there is no fourth round to
    play for, and Sudden Death is only for a match that is level."""
    state = combat_state(duel)
    assert commit(duel, state, "king", "knight") is None     # a leads 1-0
    assert play(duel, state, "guard", "guard") is None       # draw
    assert play(duel, state, "assassin", "assassin") == "a"
    assert state.private["sudden_death"] is False


def test_sudden_death_does_not_hand_back_a_spent_sacrifice(duel):
    state = spent_sacrifice_to_sudden_death(duel)
    assert state.private["sacrifice_used"] == {"a": True, "b": False}
    assert duel._can_sacrifice(state.private, "a") is False
    assert duel._can_sacrifice(state.private, "b") is True   # b never spent it


def test_an_exhausted_hand_is_refreshed_rather_than_stranded(duel):
    """Sudden Death can outlast a hand: five drawn rounds spend every card, and
    a duel with no legal move left would never end."""
    state = to_sudden_death(duel)
    for card in CARD_TYPES:
        play(duel, state, card, card)
    assert hand_types(state, "a") == list(CARD_TYPES)
    commit(duel, state, "normal", "normal")       # neither has sacrificed yet
    assert duel.normalize_choice(state, "king", "a") == "king"


# --- Choice validation ---

@pytest.mark.parametrize("bad", [
    "", "   ", "emperor", "kings", "kin", None, 0, 1, [], {}, ["king"],
    {"choice": "king"}, True, 3.5, "king" * 100, "x" * (MAX_CHOICE_CHARS + 1),
    "sacrifice", "sacrifice:", "sacrifice:c1+c2>c3=", "sacrifice:c9+c8>c7=guard",
    "sacrifice:c1+c2>c3=emperor", "normal normal",
])
def test_illegal_choices_are_rejected_without_raising(duel, bad):
    state = combat_state(duel)
    assert duel.normalize_choice(state, bad, "a") is None


@pytest.mark.parametrize("raw,expected", [
    ("KING", "king"), ("  Guard  ", "guard"), ("AsSaSsIn", "assassin"),
    ("peasant\n", "peasant"), ("\tknight", "knight"),
])
def test_card_choices_are_normalised(duel, raw, expected):
    assert duel.normalize_choice(combat_state(duel), raw, "a") == expected


def test_a_move_without_a_seat_is_not_a_move(duel):
    """Every duel move belongs to a seat; the engine always supplies one."""
    state = combat_state(duel)
    assert duel.normalize_choice(state, "king") is None
    assert duel.normalize_choice(state, "king", "c") is None


def test_a_seat_cannot_play_out_of_the_opponents_hand(duel):
    state = combat_state(duel)
    commit(duel, state, "king", "peasant")
    commit(duel, state, "normal", "normal")  # step over the strategy beat
    # b has spent their Peasant; a has not. The same word is legal for one
    # seat and illegal for the other, which is the whole point of `side`.
    assert duel.normalize_choice(state, "peasant", "a") == "peasant"
    assert duel.normalize_choice(state, "peasant", "b") is None


# --- The reveal rule and the sacrifice secret ---

# Payload keys that name cards *by design*, so scanning them for a leak only
# ever finds the client's own furniture: the card guide, the resolved-round log,
# and the viewer's own hand. Each is asserted directly instead — `hand` by
# comparing it to the viewer's real hand, `log`/`last` by only ever being
# written when a round resolves.
PUBLIC_CARD_KEYS = ("types", "beats", "transform_types", "log", "last", "hand")


def live_view(view: dict) -> str:
    """The parts of a served view that could carry *this* round's move.

    `history` only holds rounds that already resolved, so a leak of the move in
    flight has to surface somewhere else.
    """
    payload = {
        key: value for key, value in view["payload"].items()
        if key not in PUBLIC_CARD_KEYS
    }
    return repr({
        key: value for key, value in view.items()
        if key not in ("payload", "history")
    } | {"payload": payload})


@pytest.mark.parametrize("a", CARD_TYPES)
@pytest.mark.parametrize("b", CARD_TYPES)
def test_no_matchup_leaks_to_either_seat(duel, a, b):
    """Sweep every matchup rather than trusting one sample."""
    state = combat_state(duel)
    state.choices["a"] = duel.normalize_choice(state, a, "a")
    state.choices["b"] = duel.normalize_choice(state, b, "b")
    for side in SIDES:
        view = duel.public(state, side=side, revealed=False)
        assert view["choices"] == {side: state.choices[side]}
        assert view["locked"] == {"a": True, "b": True}
        # The hand in the payload is the viewer's own, entire, and nobody
        # else's — which is why the scan below can skip it.
        assert view["payload"]["hand"] == state.private["hands"][side]
        opponent = state.choices[other_side(side)]
        if opponent != state.choices[side]:
            assert opponent not in live_view(view)


def test_a_grandmaster_sees_neither_card_before_reveal(duel):
    state = combat_state(duel)
    state.choices["a"] = "assassin"
    state.choices["b"] = "guard"
    view = duel.public(state, side=None, revealed=False)
    assert view["choices"] == {}
    assert view["payload"]["hand"] == []      # and no hand to relay either
    assert "assassin" not in live_view(view) and "guard" not in live_view(view)


def test_reveal_shows_both_cards(duel):
    state = combat_state(duel)
    state.choices.update({"a": "king", "b": "peasant"})
    for side in (*SIDES, None):
        view = duel.public(state, side=side, revealed=True)
        assert view["choices"] == {"a": "king", "b": "peasant"}


def test_a_duellist_sees_their_own_hand_and_only_a_count_of_the_other(duel):
    state = duel.new_duel(2)
    view = duel.public(state, side="a", revealed=False)
    assert [card["type"] for card in view["payload"]["hand"]] == list(CARD_TYPES)
    assert view["payload"]["cards_left"] == {"a": 5, "b": 5}
    # b's hand appears nowhere in a's view — only its size does.
    assert "hands" not in repr(view)


def test_the_opponent_learns_that_a_sacrifice_happened(duel):
    state = duel.new_duel(2)
    sacrifice(duel, state, "a", ("c1", "c2"), "c3", "peasant")
    state.choices["b"] = duel.normalize_choice(state, "normal", "b")
    duel.resolve_round(state)
    view = duel.public(state, side="b", revealed=True)
    assert view["choices"] == {"a": "sacrifice", "b": "normal"}
    assert view["payload"]["sacrifice_used"] == {"a": True, "b": False}
    assert view["payload"]["last"]["sacrificed"] == {"a": True, "b": False}


def test_the_opponent_never_learns_what_the_sacrifice_did(duel):
    """The load-bearing secret: which cards burned, which was rewritten, and
    into what. None of it may reach the other seat, before or after reveal."""
    state = duel.new_duel(2)
    sacrifice(duel, state, "a", ("c1", "c2"), "c4", "peasant")
    state.choices["b"] = duel.normalize_choice(state, "normal", "b")

    plan = state.private["pending"]["a"]
    assert plan == {"burn": ["c1", "c2"], "target": "c4", "type": "peasant"}

    def foreign(view: dict) -> str:
        """Everything in a served view except the viewer's own hand — where
        b's own card ids and types legitimately live."""
        payload = dict(view["payload"])
        payload.pop("hand")
        return repr(dict(view, payload=payload))

    for revealed in (False, True):
        for viewer in ("b", None):
            rendered = foreign(duel.public(state, side=viewer, revealed=revealed))
            assert "c1" not in rendered and "c2" not in rendered
            assert "c4" not in rendered
            assert "burn" not in rendered and "pending" not in rendered

    duel.resolve_round(state)
    for viewer in ("b", None):
        view = duel.public(state, side=viewer, revealed=True)
        rendered = foreign(view)
        assert "c1" not in rendered and "c4" not in rendered
        # b sees a's hand *size* drop by two, and learns nothing else about it.
        assert view["payload"]["cards_left"] == {"a": 3, "b": 5}


def test_a_transformed_card_is_only_revealed_by_playing_it(duel):
    state = duel.new_duel(2)
    sacrifice(duel, state, "a", ("c1", "c2"), "c4", "peasant")  # Assassin -> Peasant
    commit(duel, state, None, "normal")
    state.choices["a"] = duel.normalize_choice(state, "peasant", "a")
    before = duel.public(state, side="b", revealed=False)
    assert before["choices"] == {}           # hidden while the round is open
    assert "peasant" not in live_view(before)
    state.choices["b"] = duel.normalize_choice(state, "king", "b")
    after = duel.public(state, side="b", revealed=True)
    assert after["choices"]["a"] == "peasant"   # and now it is simply a card


def test_public_view_shape(duel):
    state = duel.new_duel(5)
    view = duel.public(state, side="b", revealed=False)
    assert set(view) == {
        "duel_game_id", "rules_version", "round", "wins", "history",
        "you", "locked", "choices", "payload",
    }
    assert view["rules_version"] == DUEL_RULES_VERSION
    assert view["you"] == "b"
    assert view["payload"]["kind"] == "crown_duel"
    assert view["payload"]["crowns_needed"] == CROWNS_NEEDED
    assert view["payload"]["can_sacrifice"] is True


def test_public_view_is_a_copy(duel):
    """Mutating a served view must not reach back into live duel state."""
    state = combat_state(duel)
    commit(duel, state, "king", "knight")
    view = duel.public(state, side="a", revealed=True)
    view["wins"]["a"] = 99
    view["payload"]["crowns"]["a"] = 99
    view["payload"]["hand"].clear()
    view["payload"]["log"][0]["winner"] = "b"
    view["payload"]["log"][-1]["crowns"]["b"] = 99
    view["payload"]["cards_left"]["a"] = 0
    assert state.private["crowns"] == {"a": 1, "b": 0}
    assert len(hand_types(state, "a")) == 4
    assert state.private["log"][-1]["winner"] == "a"
    assert state.private["log"][-1]["crowns"] == {"a": 1, "b": 0}


# --- Module hygiene ---

def test_module_is_stateless_across_duels(duel):
    first = combat_state(duel)
    commit(duel, first, "king", "knight")
    sacrifice(duel, first, "a", ("c2", "c3"), "c4", "peasant")
    second = duel.new_duel(1)
    assert second.private["crowns"] == {"a": 0, "b": 0}
    assert second.private["phase"] == "strategy"
    assert hand_types(second, "a") == list(CARD_TYPES)
    assert second.private["pending"] == {"a": None, "b": None}


def test_reset_safe_and_deterministic_after(duel):
    before = duel.new_duel(4).private
    assert duel.reset() is None
    assert duel.reset() is None  # idempotent
    assert duel.new_duel(4).private == before
