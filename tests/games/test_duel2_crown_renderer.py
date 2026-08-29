"""CROWN DUEL renderer, driven through the shipped `frontend/duels/crown_duel.js`.

The two things that would be bugs rather than blemishes:

  * the opponent's card is nowhere in the DOM while the round is open, and
    neither is their hand — the payload a client receives only ever carries its
    own, and the renderer must not draw a stand-in for the other;
  * the Royal Sacrifice builder sends exactly one move, in the grammar the
    server parses, after three deliberate choices.
"""

from __future__ import annotations

import shutil

import pytest

from tests.games.duel_renderer_harness import run

SNAPSHOT = r"""
const HAND = [
  { id: "c1", origin: "king", type: "king", status: "available" },
  { id: "c2", origin: "knight", type: "knight", status: "available" },
  { id: "c3", origin: "guard", type: "guard", status: "available" },
  { id: "c4", origin: "assassin", type: "assassin", status: "available" },
  { id: "c5", origin: "peasant", type: "peasant", status: "available" },
];

function duel(over) {
  const payload = Object.assign({
    kind: "crown_duel", choice_seconds: 10, wins_needed: 2, crowns_needed: 2,
    normal_rounds: 3, phase: "combat", game_round: 1, sudden_death: false,
    crowns: { a: 0, b: 0 }, sacrifice_used: { a: false, b: false },
    can_sacrifice: true, hand: HAND, cards_left: { a: 5, b: 5 },
    types: ["king", "knight", "guard", "assassin", "peasant"],
    beats: {}, transform_types: ["knight", "guard", "assassin", "peasant"],
    sacrifice_cost: 2, log: [], last: null,
  }, (over || {}).payload || {});
  return Object.assign({
    id: "d1", duel_game_id: "crown_duel", name: "Crown Duel",
    phase: "choosing", round: 1, you: "a",
    wins: { a: 0, b: 0 }, locked: { a: false, b: false }, choices: {},
    duellists: { a: "A0", b: "B0" }, history: [],
    last_round: null, winner_side: null, deadline: null,
  }, over || {}, { payload: payload });
}
const renderer = context.window.RelayDuels.crown_duel;
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_card_round_lock_and_reveal_rule():
    report = run("crown_duel", SNAPSHOT + r"""
const root = element("div");
renderer.mount(root, duel(), api);
report.cards = textsOf(root, "duel-card-label");

const king = labelled(root, "King");
king.click();
king.click();                     // a second press must not double-send
report.sent = sent.slice();
report.lockedAfterClick = buttons(root).every((b) => b.disabled);

// The opponent has locked in, but the server sent no choice: nothing may
// render it, and their hand is not in the payload at all.
renderer.update(duel({ locked: { a: true, b: true }, choices: { a: "king" } }));
report.openHands = textsOf(root, "duel-hand");
report.openSubs = textsOf(root, "duel-sub");

renderer.update(duel({
  phase: "reveal", locked: { a: true, b: true },
  choices: { a: "king", b: "knight" },
  payload: {
    crowns: { a: 1, b: 0 }, cards_left: { a: 4, b: 4 },
    last: { kind: "combat", round: 1, a: "king", b: "knight", winner: "a",
            crowns: { a: 1, b: 0 } },
  },
}));
report.revealHands = textsOf(root, "duel-hand");
report.revealText = textOf(root);
report.revealButtons = buttons(root).length;
""")

    assert report["cards"] == ["King", "Knight", "Guard", "Assassin", "Peasant"]
    assert report["sent"] == [["king", "d1", 1]], "a second press must not resend"
    assert report["lockedAfterClick"] is True

    # The security-critical assertion: their card is not in the tree. A scan
    # of the whole tree would be no use — your own hand draws all five cards
    # by design — so this reads the two seat faces, which is where a played
    # card is the only thing that can appear.
    assert report["openHands"] == ["👑", "🔒"], "theirs is a lock, not a card"
    assert report["openSubs"] == ["5 cards", "5 cards"], "counts, never cards"

    assert report["revealHands"] == ["👑", "⚔️"], "both cards are public now"
    assert "You take the Crown" in report["revealText"]
    assert report["revealButtons"] == 0, "no controls during the reveal beat"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_sacrifice_builder_sends_one_legal_move():
    report = run("crown_duel", SNAPSHOT + r"""
const root = element("div");
renderer.mount(root, duel({ payload: { phase: "strategy" } }), api);
report.strategyButtons = buttons(root)
  .map((b) => textOf(b).replace(/\s+/g, " ").trim());

labelled(root, "Royal Sacrifice").click();
report.step1 = textsOf(root, "duel-step-label");
labelled(root, "King").click();
labelled(root, "Knight").click();
report.step2 = textsOf(root, "duel-step-label");
// The two cards being destroyed cannot also be the one rewritten.
report.burningDisabled = buttons(root)
  .filter((b) => b.classList.contains("burning"))
  .every((b) => b.disabled);

labelled(root, "Assassin").click();
report.step3 = textsOf(root, "duel-step-label");
report.offered = textsOf(root, "duel-card-label");
// A rewrite has to change something: Assassin -> Assassin is not offered.
report.noOpDisabled = labelled(root, "Assassin").disabled;

const confirm = labelled(root, "Confirm sacrifice");
report.confirmBlocked = confirm.disabled;
labelled(root, "Peasant").click();
const go = labelled(root, "Confirm sacrifice");
go.click();
go.click();                                    // must not double-send
report.sent = sent.slice();
report.builderClosed = labelled(root, "Confirm sacrifice") === undefined;
""")

    assert report["strategyButtons"] == ["🃏 Play normally", "⚡ Royal Sacrifice"]
    assert report["step1"] == ["1 of 3 — choose two cards to destroy"]
    assert report["step2"] == ["2 of 3 — choose the card to rewrite"]
    assert report["burningDisabled"] is True
    assert report["step3"] == ["3 of 3 — choose what it becomes"]
    assert report["offered"] == ["Knight", "Guard", "Assassin", "Peasant"], \
        "a sacrifice may never create a King"
    assert report["noOpDisabled"] is True
    assert report["confirmBlocked"] is True, "confirm needs all three choices"
    assert report["sent"] == [["sacrifice:c1+c2>c4=peasant", "d1", 1]]
    assert report["builderClosed"] is True, "a committed sacrifice is not editable"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_a_sacrifice_is_announced_without_saying_what_it_did():
    report = run("crown_duel", SNAPSHOT + r"""
const root = element("div");
renderer.mount(root, duel({ payload: { phase: "strategy" } }), api);
renderer.update(duel({
  phase: "reveal", round: 1, choices: { a: "normal", b: "sacrifice" },
  payload: {
    phase: "combat", sacrifice_used: { a: false, b: true },
    cards_left: { a: 5, b: 3 },
    last: { kind: "strategy", round: 1, sacrificed: { a: false, b: true } },
  },
}));
report.status = textsOf(root, "duel-status");
report.subs = textsOf(root, "duel-sub");
report.cards = textsOf(root, "duel-card-label");
report.hands = textsOf(root, "duel-hand");
""")

    assert report["status"] == ["⚡ Your opponent performed a Royal Sacrifice."]
    # All the other seat's hand ever shows is how many cards are left in it.
    assert report["subs"] == ["5 cards", "3 cards · ⚡"]
    assert report["cards"] == [], "no card of either hand is on the table"
    assert report["hands"] == ["🃏", "⚡"], "only the strategy choices are public"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_a_grandmaster_watches_and_unmount_leaves_nothing():
    report = run("crown_duel", SNAPSHOT + r"""
const root = element("div");
renderer.mount(root, duel({ you: null, payload: { hand: [] } }), api);
report.leaderButtons = buttons(root).length;
report.leaderText = textOf(root);

renderer.unmount();
report.afterUnmount = root.children.length;
renderer.unmount();                 // idempotent
report.doubleUnmountOk = true;
""")

    assert report["leaderButtons"] == 0, "a Grandmaster cannot play the duel"
    assert "Round 1 in progress" in report["leaderText"]
    assert report["afterUnmount"] == 0
    assert report["doubleUnmountOk"] is True
