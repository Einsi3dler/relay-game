"""BID WAR renderer, driven through the shipped `frontend/duels/bid_war.js`.

Besides the reveal rule the other two duels share, this one has a rule of its
own to hold: the page shows the lot on the block and the one after it, and the
prizes past that are not in the payload at all — so there is nothing in the DOM
to read the auction ahead from.
"""

from __future__ import annotations

import shutil

import pytest

from tests.games.duel_renderer_harness import run

SNAPSHOT = r"""
function duel(over) {
  const payload = Object.assign({
    kind: "bid_war", choice_seconds: 10, wins_needed: 1, auctions: 5,
    auction: 1, prize: 3, next_prize: 9, staked: { a: 20, b: 20 },
    coins: { a: 20, b: 20 }, won: { a: 0, b: 0 },
    overtime: false, overtime_round: 0, overtime_coins: { a: 5, b: 5 },
    max_bid: 20, log: [], last: null,
  }, (over || {}).payload || {});
  return Object.assign({
    id: "d1", duel_game_id: "bid_war", name: "Bid War",
    phase: "choosing", round: 1, you: "a",
    wins: { a: 0, b: 0 }, locked: { a: false, b: false }, choices: {},
    duellists: { a: "A0", b: "B0" }, history: [],
    last_round: null, winner_side: null, deadline: null,
  }, over || {}, { payload: payload });
}
const renderer = context.window.RelayDuels.bid_war;
function bidValue(root) { return textsOf(root, "duel-bid-value")[0]; }
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_stepper_names_an_exact_bid_and_locks_once():
    report = run("bid_war", SNAPSHOT + r"""
const root = element("div");
renderer.mount(root, duel(), api);
report.start = bidValue(root);

labelled(root, "+").click();
labelled(root, "+").click();
labelled(root, "+").click();
labelled(root, "−").click();
report.stepped = bidValue(root);          // 0 +1 +1 +1 -1 = 2

labelled(root, "6").click();              // a quick bid overwrites it
report.quick = bidValue(root);
labelled(root, "+").click();
report.exact = bidValue(root);            // ...and is still adjustable: 7

const lock = labelled(root, "Lock bid");
lock.click();
lock.click();                             // a second press must not double-send
report.sent = sent.slice();
report.lockedAfterClick = buttons(root).every((b) => b.disabled);
""")

    assert report["start"] == "0", "a lot opens at no bid, not at last round's"
    assert report["stepped"] == "2"
    assert report["quick"] == "6"
    assert report["exact"] == "7"
    assert report["sent"] == [["7", "d1", 1]], "a second press must not resend"
    assert report["lockedAfterClick"] is True


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_a_bid_cannot_exceed_the_purse():
    report = run("bid_war", SNAPSHOT + r"""
const root = element("div");
renderer.mount(root, duel({ payload: { coins: { a: 3, b: 12 }, max_bid: 3 } }), api);
report.quickBids = buttons(root)
  .filter((b) => String(b.className).indexOf("duel-quick-bid") >= 0)
  .map((b) => b.textContent);
labelled(root, "All in").click();
report.allIn = bidValue(root);
report.upDisabled = labelled(root, "+").disabled;
labelled(root, "Lock bid").click();
report.sent = sent.slice();
""")

    # Only the quick bids you can afford are offered, plus "all in".
    assert report["quickBids"] == ["0", "2", "All in"]
    assert report["allIn"] == "3"
    assert report["upDisabled"] is True, "the stepper stops at the balance"
    assert report["sent"] == [["3", "d1", 1]]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_reveal_rule_and_the_hidden_prize_order():
    report = run("bid_war", SNAPSHOT + r"""
const root = element("div");
renderer.mount(root, duel(), api);
report.prizes = textsOf(root, "duel-prize-value");
report.openText = textOf(root);

// The opponent has locked in, but the server sent no bid: nothing may show it.
renderer.update(duel({ locked: { a: true, b: true }, choices: { a: "7" } }));
report.openHands = textsOf(root, "duel-hand");

renderer.update(duel({
  phase: "reveal", locked: { a: true, b: true }, choices: { a: "7", b: "4" },
  payload: {
    coins: { a: 13, b: 16 }, won: { a: 3, b: 0 },
    log: [{ auction: 1, overtime: false, prize: 3, a: 7, b: 4, winner: "a",
            won: { a: 3, b: 0 }, coins: { a: 13, b: 16 } }],
    last: { auction: 1, overtime: false, prize: 3, a: 7, b: 4, winner: "a",
            won: { a: 3, b: 0 }, coins: { a: 13, b: 16 } },
  },
}));
report.revealHands = textsOf(root, "duel-hand");
report.scores = textsOf(root, "duel-score");
report.purses = textsOf(root, "duel-sub");
report.status = textsOf(root, "duel-status");
report.log = textsOf(root, "duel-log-winner");
""")

    # One lot is named, and only one. The next has not been rolled — its floor
    # depends on what this auction costs the pair of them — so the card shows a
    # single value and the "next" tile stays out of the page entirely.
    assert report["prizes"] == ["3 coins", "9 coins"]
    assert "1 VP" not in report["openText"] and "2 VP" not in report["openText"]

    assert report["openHands"] == ["7", "🔒"], "theirs is a lock, not a bid"
    assert report["revealHands"] == ["7", "4"], "both bids are public now"
    assert report["scores"] == ["3 won", "0 won"]
    assert report["purses"] == ["13 coins", "16 coins"]
    assert report["status"] == ["💰 Sold to you: +3 coins for your team."]
    assert report["log"] == ["+3 to you"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_a_tied_lot_says_where_the_prize_went():
    report = run("bid_war", SNAPSHOT + r"""
const root = element("div");
renderer.mount(root, duel(), api);
renderer.update(duel({
  phase: "reveal", choices: { a: "4", b: "4" },
  payload: {
    prize: 7, next_prize: 9, coins: { a: 16, b: 16 },
    last: { auction: 1, overtime: false, prize: 2, a: 4, b: 4, winner: null,
            won: { a: 0, b: 0 }, coins: { a: 16, b: 16 } },
  },
}));
report.status = textsOf(root, "duel-status");
report.prizes = textsOf(root, "duel-prize-value");
""")

    assert report["status"] == [
        "🤝 Tied bid. Nobody is paid and 2 coins roll into the next lot."
    ]
    # The rolled-up value is on the block, and one lot is still shown behind it.
    assert report["prizes"] == ["7 coins", "9 coins"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_overtime_bids_out_of_the_temporary_purse():
    report = run("bid_war", SNAPSHOT + r"""
const root = element("div");
renderer.mount(root, duel({
  round: 6, payload: {
    auction: 5, prize: 5, next_prize: null, overtime: true, overtime_round: 1,
    coins: { a: 2, b: 0 }, overtime_coins: { a: 5, b: 5 }, max_bid: 5,
    won: { a: 5, b: 5 },
  },
}), api);
report.round = textsOf(root, "duel-round");
report.purses = textsOf(root, "duel-sub");
report.nextHidden = descend(root)
  .filter((n) => String(n.className).indexOf("duel-prize--next") >= 0)
  .map((n) => n.hidden);
labelled(root, "All in").click();
report.allIn = bidValue(root);
""")

    assert report["round"] == ["OVERTIME: a fresh, equal purse decides it"]
    assert report["purses"] == ["5 overtime coins", "5 overtime coins"]
    assert report["nextHidden"] == [True], "there is no next lot in overtime"
    assert report["allIn"] == "5", "the temporary purse is what can be bid"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_a_grandmaster_watches_and_unmount_leaves_nothing():
    report = run("bid_war", SNAPSHOT + r"""
const root = element("div");
renderer.mount(root, duel({ you: null, payload: { max_bid: 0 } }), api);
report.leaderButtons = buttons(root).length;
report.leaderText = textOf(root);

renderer.unmount();
report.afterUnmount = root.children.length;
renderer.unmount();                 // idempotent
report.doubleUnmountOk = true;
""")

    assert report["leaderButtons"] == 0, "a Grandmaster cannot bid"
    assert "Lot 1 under the hammer" in report["leaderText"]
    assert report["afterUnmount"] == 0
    assert report["doubleUnmountOk"] is True
