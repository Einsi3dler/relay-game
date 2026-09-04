"""CROWN DUEL — five characters, counters, and one hidden hand rewrite.

Both Duelists hold the same five cards — King, Knight, Guard, Assassin,
Peasant. Every round both commit one of them; only the *same* card against
itself draws, so every other matchup has a winner and a Crown. Three rounds,
most Crowns wins — two of them settles it early, and a match still level after
the third goes to Sudden Death on fresh hands, first non-draw round taking it.

The twist is **Royal Sacrifice**, once per Duelist per match: destroy two of
your unused cards to rewrite a third into a different character — anything but
a King. Your opponent is told that a sacrifice happened. They are never told
what it did. That asymmetry is the whole game, and it is why this module keeps
its hands in `DuelState.private` (which `base_public` cannot reach) rather than
in `payload` (which is sent to everyone verbatim).

Two shapes here are worth reading before changing anything:

  * **A Crown Duel round is two engine rounds.** The engine gives a duel one
    committed choice per round, and Crown Duel needs two: the secret strategy
    choice (play normally, or sacrifice) and then the card. So a strategy round
    resolves to nobody — it only publishes *that* a sacrifice happened, which
    is exactly the announcement the rules want before cards are chosen — and
    the combat round that follows scores the Crown. A strategy round is skipped
    entirely once neither Duelist can legally sacrifice any more, which is
    public information (`sacrifice_used` and the hand *counts* both are).

  * **The module owns the score.** `resolve_round` returns a side only when
    that side has taken the duel, so `wins_needed` is 1 and `state.wins` means
    "decided". Crowns live here, because a three-round match with draws, a
    Sudden Death, and hands that refresh when they run out is more than the
    engine's round-win counter can express. The engine's tie path — replay the
    round — is what carries the duel from one round to the next.

Anti-cheat (docs/GAMES_SPEC.md): there is no answer to look up. The one thing
worth knowing — what the opponent holds, and what their sacrifice did — is
never sent to anyone, and the card they just played only reaches the other side
through `base_public` once the round has already resolved.
"""

from __future__ import annotations

from typing import Any

from backend.games.duel_base import (
    MAX_CHOICE_CHARS,
    SIDES,
    DuelState,
    base_public,
)

CARD_TYPES = ("king", "knight", "guard", "assassin", "peasant")

# type -> the types it defeats. Total over every ordered pair of *different*
# types, so no matchup is undecided and only a mirror draws:
#   King beats all three fighters, Peasant beats King, all three fighters beat
#   Peasant, and the fighters cycle Guard > Knight > Assassin > Guard.
DEFEATS: dict[str, tuple[str, ...]] = {
    "king": ("knight", "guard", "assassin"),
    "peasant": ("king",),
    "knight": ("assassin", "peasant"),
    "guard": ("knight", "peasant"),
    "assassin": ("guard", "peasant"),
}

# A sacrifice may make any of these. Never a King: the crown can be thrown away
# but not minted, so the card that beats a Peasant stays scarce.
TRANSFORM_TYPES = ("knight", "guard", "assassin", "peasant")

CHOICE_SECONDS = 10   # the window both Duelists choose inside
CROWNS_NEEDED = 2     # Crowns that take the duel — best of three
NORMAL_ROUNDS = 3     # after these, a tied duel is in Sudden Death
SACRIFICE_COST = 2    # cards destroyed to rewrite one other
# Sacrificing costs two cards and spends a third to play, so it needs three.
SACRIFICE_MIN_CARDS = SACRIFICE_COST + 1

PLAY_NORMALLY = "normal"
SACRIFICE = "sacrifice"

# The canonical strategy choice is one of those two words and nothing else.
# The detail — which cards burned, which was rewritten, into what — is parked
# in `private` and never becomes a choice string, because a choice string does
# reach both clients: the engine puts the resolved round in `last_round`, which
# `models.DuelSession.public` sends verbatim to everyone who can see the duel.
SACRIFICE_PREFIX = "sacrifice:"


def _fresh_hand() -> list[dict[str, str]]:
    """One of each card. Ids are per-hand and stable, so two Peasants made by a
    rewrite are still separable when the next sacrifice names one of them."""
    return [
        {"id": f"c{index + 1}", "origin": card, "type": card, "status": "available"}
        for index, card in enumerate(CARD_TYPES)
    ]


def _available(hand: list[dict[str, str]]) -> list[dict[str, str]]:
    return [card for card in hand if card["status"] == "available"]


def _copy_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """A round-log entry deep enough to hand out: its nested score/flag dicts
    are copied too, so a served view can never write back into live state."""
    return {
        key: dict(value) if isinstance(value, dict) else value
        for key, value in entry.items()
    }


def resolve_crown_cards(a: str, b: str) -> str | None:
    """Winner of one card matchup: "a", "b", or None when the cards match."""
    if a == b:
        return None
    return "a" if b in DEFEATS[a] else "b"


class CrownDuel:
    """Best of three, one hidden hand rewrite each, only a mirror draws."""

    id = "crown_duel"
    name = "Crown Duel"
    choice_seconds = CHOICE_SECONDS
    # The module scores the match itself (see the header), so one returned
    # winner *is* the duel.
    wins_needed = 1
    staked = False   # fought with the team's coins? Not this one.

    # --- lifecycle -------------------------------------------------------

    def new_duel(
        self, seed: int, stakes: dict[str, int] | None = None
    ) -> DuelState:
        # Nothing is randomised: both Duelists start from the same five cards,
        # and `seed` is part of the contract for duel games that do.
        return DuelState(
            duel_game_id=self.id,
            private={
                "phase": "strategy",       # "strategy" | "combat"
                "game_round": 1,           # Crown Duel rounds, not engine rounds
                "crowns": {"a": 0, "b": 0},
                "hands": {side: _fresh_hand() for side in SIDES},
                "sacrifice_used": {"a": False, "b": False},
                # A validated sacrifice waiting for the strategy round to
                # resolve. Never published, and cleared the moment it is spent.
                "pending": {"a": None, "b": None},
                "sudden_death": False,
                "log": [],                 # public round log
                "last": None,              # the round that just resolved
            },
        )

    # --- choices ---------------------------------------------------------

    def normalize_choice(
        self, state: DuelState, choice: object, side: str | None = None
    ) -> str | None:
        """The canonical move for this seat, or None if it is illegal.

        Crown Duel is the reason `side` exists in this interface: whether a
        move is legal depends entirely on whose hand it is. Without the seat
        this could only take the client's word for it.

        A legal `sacrifice:` submission is canonicalised to the bare word
        `sacrifice` and its detail stashed in `private["pending"]`. This is the
        one method here that writes to state, and it does so precisely so the
        secret never has to travel as a choice string.
        """
        try:
            raw = str(choice)
            if len(raw) > MAX_CHOICE_CHARS:
                return None  # cap before any further work
            move = raw.strip().lower()
            private = state.private
            if side not in SIDES:
                return None  # a duel move always belongs to a seat

            if private["phase"] == "strategy":
                if move == PLAY_NORMALLY:
                    return PLAY_NORMALLY
                if not move.startswith(SACRIFICE_PREFIX):
                    return None
                plan = self._parse_sacrifice(private, side, move)
                if plan is None:
                    return None
                private["pending"][side] = plan
                return SACRIFICE

            # Combat: you name a *character*, not a card id. Two Peasants play
            # identically, so which instance is spent is the module's business
            # — and an identity is what the reveal shows anyway.
            if move not in CARD_TYPES:
                return None
            held = {card["type"] for card in _available(private["hands"][side])}
            return move if move in held else None
        except Exception:
            return None  # a hostile or malformed move is simply not legal

    def _parse_sacrifice(
        self, private: dict[str, Any], side: str, move: str
    ) -> dict[str, Any] | None:
        """`sacrifice:c1+c2>c4=knight` -> the validated plan, or None.

        Rejects, in the order the rules state them: a second sacrifice, a hand
        too short to pay for one, anything but exactly two cards destroyed, a
        card that is not yours or not unused, a target among the cards being
        destroyed, a new King, and a rewrite that changes nothing.
        """
        if private["sacrifice_used"][side]:
            return None
        hand = private["hands"][side]
        by_id = {card["id"]: card for card in _available(hand)}
        if len(by_id) < SACRIFICE_MIN_CARDS:
            return None

        body = move[len(SACRIFICE_PREFIX):]
        burn_part, _, rest = body.partition(">")
        target_id, _, new_type = rest.partition("=")
        burn_ids = burn_part.split("+")
        if len(burn_ids) != SACRIFICE_COST or len(set(burn_ids)) != SACRIFICE_COST:
            return None
        if not all(card_id in by_id for card_id in burn_ids):
            return None
        target = by_id.get(target_id)
        if target is None or target_id in burn_ids:
            return None
        if new_type not in TRANSFORM_TYPES:
            return None          # King is not on the list, by design
        if new_type == target["type"]:
            return None          # a rewrite that rewrites nothing
        return {"burn": list(burn_ids), "target": target_id, "type": new_type}

    # --- resolution ------------------------------------------------------

    def resolve_round(self, state: DuelState) -> str | None:
        """Score the open round; return a side only once it has the duel.

        Called once per round by the engine, which makes it the one place the
        hands, the Crowns and the phase move forward.
        """
        if state.private["phase"] == "strategy":
            return self._resolve_strategy(state)
        return self._resolve_combat(state)

    def _resolve_strategy(self, state: DuelState) -> str | None:
        """Spend the sacrifices and announce that they happened — no more.

        A Duelist who let the window lapse simply played normally: there is no
        Crown at stake in a strategy round, so nothing is forfeited by silence.
        """
        private = state.private
        sacrificed = {side: False for side in SIDES}
        for side in SIDES:
            plan = private["pending"][side]
            if state.choices.get(side) == SACRIFICE and plan is not None:
                self._apply_sacrifice(private, side, plan)
                sacrificed[side] = True
        private["pending"] = {"a": None, "b": None}

        entry = {
            "kind": "strategy",
            "round": private["game_round"],
            "sacrificed": sacrificed,
        }
        private["log"].append(entry)
        private["last"] = dict(entry)
        private["phase"] = "combat"
        return None

    def _apply_sacrifice(
        self, private: dict[str, Any], side: str, plan: dict[str, Any]
    ) -> None:
        hand = {card["id"]: card for card in private["hands"][side]}
        for card_id in plan["burn"]:
            hand[card_id]["status"] = "sacrificed"
        hand[plan["target"]]["type"] = plan["type"]
        private["sacrifice_used"][side] = True

    def _resolve_combat(self, state: DuelState) -> str | None:
        private = state.private
        played = {side: state.choices.get(side) for side in SIDES}
        for side, card_type in played.items():
            if card_type is not None:
                self._spend(private["hands"][side], card_type)

        if played["a"] is None and played["b"] is None:
            winner = None                       # a double no-show costs nobody
        elif played["a"] is None:
            winner = "b"                        # letting the window lapse loses
        elif played["b"] is None:
            winner = "a"
        else:
            winner = resolve_crown_cards(played["a"], played["b"])

        if winner is not None:
            private["crowns"][winner] += 1
        entry = {
            "kind": "combat",
            "round": private["game_round"],
            "a": played["a"],
            "b": played["b"],
            "winner": winner,
            "crowns": dict(private["crowns"]),
        }
        private["log"].append(entry)
        private["last"] = dict(entry)

        decided = self._decide(private, winner)
        if decided is not None:
            return decided  # the duel is over; the engine finishes it

        self._open_next_round(private)
        return None

    def _decide(self, private: dict[str, Any], winner: str | None) -> str | None:
        """Has the duel just been settled? The three ways it can be:

        two Crowns (decisive before a third round is even needed), the third
        round ending with one Duelist ahead, or — once the match is level after
        three rounds — the first Sudden Death round that isn't a draw.
        """
        crowns = private["crowns"]
        if winner is not None:
            if crowns[winner] >= CROWNS_NEEDED or private["sudden_death"]:
                return winner
        if private["game_round"] >= NORMAL_ROUNDS and not private["sudden_death"]:
            if crowns["a"] != crowns["b"]:
                return "a" if crowns["a"] > crowns["b"] else "b"
        return None

    def _spend(self, hand: list[dict[str, str]], card_type: str) -> None:
        """Consume one available card of that identity — both cards go, even on
        a draw. Duplicates are interchangeable, so the first will do."""
        for card in hand:
            if card["status"] == "available" and card["type"] == card_type:
                card["status"] = "played"
                return

    def _open_next_round(self, private: dict[str, Any]) -> None:
        """Advance the Crown Duel round, refresh exhausted hands, and decide
        whether the next round needs a strategy phase at all."""
        private["game_round"] += 1
        entering_sudden_death = (
            private["game_round"] > NORMAL_ROUNDS and not private["sudden_death"]
        )
        empty = any(not _available(private["hands"][side]) for side in SIDES)
        if entering_sudden_death:
            # Tied after three rounds: both Duelists get a clean five-card hand
            # and play on a round at a time. A sacrifice already spent stays
            # spent; one still in hand stays available.
            private["sudden_death"] = True
            self._refresh_hands(private)
        elif empty:
            # Draws can eat a hand before anyone reaches two Crowns.
            self._refresh_hands(private)
        private["phase"] = (
            "strategy"
            if any(self._can_sacrifice(private, side) for side in SIDES)
            else "combat"   # nobody can rewrite anything, so skip the beat
        )

    def _refresh_hands(self, private: dict[str, Any]) -> None:
        private["hands"] = {side: _fresh_hand() for side in SIDES}

    def _can_sacrifice(self, private: dict[str, Any], side: str) -> bool:
        return (
            not private["sacrifice_used"][side]
            and len(_available(private["hands"][side])) >= SACRIFICE_MIN_CARDS
        )

    # --- the client view -------------------------------------------------

    def public(
        self, state: DuelState, side: str | None, revealed: bool
    ) -> dict[str, Any]:
        """`base_public` for the reveal rule, plus a payload built per viewer.

        A viewer's own hand is in there; the opponent's is never more than a
        count. A Grandmaster (side=None) gets no hand at all, so there is
        nothing for them to relay.
        """
        view = base_public(state, side, revealed)
        view["payload"] = self._payload(state, side)
        return view

    def _payload(self, state: DuelState, side: str | None) -> dict[str, Any]:
        private = state.private
        hand = (
            [dict(card) for card in private["hands"][side]]
            if side in SIDES else []
        )
        return {
            "kind": self.id,
            "choice_seconds": CHOICE_SECONDS,
            "wins_needed": CROWNS_NEEDED,
            "crowns_needed": CROWNS_NEEDED,
            "normal_rounds": NORMAL_ROUNDS,
            "phase": private["phase"],
            "game_round": private["game_round"],
            "sudden_death": private["sudden_death"],
            "crowns": dict(private["crowns"]),
            "sacrifice_used": dict(private["sacrifice_used"]),
            "can_sacrifice": side in SIDES and self._can_sacrifice(private, side),
            "hand": hand,
            # Counts only. How many cards the opponent holds is public — it
            # follows from the rounds played and whether they sacrificed — but
            # *which* cards never is.
            "cards_left": {
                seat: len(_available(private["hands"][seat])) for seat in SIDES
            },
            "types": list(CARD_TYPES),
            "beats": {card: list(beaten) for card, beaten in DEFEATS.items()},
            "transform_types": list(TRANSFORM_TYPES),
            "sacrifice_cost": SACRIFICE_COST,
            "log": [_copy_entry(entry) for entry in private["log"]],
            "last": _copy_entry(private["last"]) if private["last"] else None,
        }

    def reset(self) -> None:
        return None  # stateless
