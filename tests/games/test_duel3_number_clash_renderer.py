"""NUMBER CLASH renderer, driven through the shipped `frontend/duels/number_clash.js`."""

from __future__ import annotations

import shutil

import pytest

from tests.games.duel_renderer_harness import run

SNAPSHOT = r"""
function duel(over) {
  const payload = Object.assign({
    kind: "number_clash", choice_seconds: 8, wins_needed: 4, points_needed: 4,
    normal_rounds: 7, game_round: 1, sudden_death: false,
    points: { a: 0, b: 0 }, numbers: [1, 2, 3, 4, 5, 6, 7, 8, 9],
    used: { a: [], b: [] }, available: [1, 2, 3, 4, 5, 6, 7, 8, 9],
    log: [], last: null,
  }, (over || {}).payload || {});
  return Object.assign({
    id: "d1", duel_game_id: "number_clash", name: "Number Clash",
    phase: "choosing", round: 1, you: "a",
    wins: { a: 0, b: 0 }, locked: { a: false, b: false }, choices: {},
    duellists: { a: "A0", b: "B0" }, history: [],
    last_round: null, winner_side: null, deadline: null,
  }, over || {}, { payload: payload });
}
const renderer = context.window.RelayDuels.number_clash;
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_lock_in_and_the_reveal_rule():
    report = run("number_clash", SNAPSHOT + r"""
const root = element("div");
renderer.mount(root, duel(), api);
report.grid = buttons(root).map((b) => b.textContent);

const seven = labelled(root, "7");
seven.click();
seven.click();                    // a second press must not double-send
report.sent = sent.slice();
report.lockedAfterClick = buttons(root).every((b) => b.disabled);

// The opponent has locked in, but the server sent no number: nothing may
// render one.
renderer.update(duel({
  locked: { a: true, b: true }, choices: { a: "7" },
  payload: { available: [1, 2, 3, 4, 5, 6, 8, 9] },
}));
report.openHands = textsOf(root, "duel-hand");
report.openSpent = textsOf(root, "duel-sub");

renderer.update(duel({
  phase: "reveal", locked: { a: true, b: true }, choices: { a: "7", b: "4" },
  payload: {
    points: { a: 1, b: 0 }, used: { a: [7], b: [4] },
    available: [1, 2, 3, 4, 5, 6, 8, 9],
    log: [{ round: 1, a: 7, b: 4, winner: "a", points: { a: 1, b: 0 } }],
    last: { round: 1, a: 7, b: 4, winner: "a", points: { a: 1, b: 0 } },
  },
}));
report.revealHands = textsOf(root, "duel-hand");
report.scores = textsOf(root, "duel-score");
report.spent = textsOf(root, "duel-sub");
report.log = textsOf(root, "duel-log-pair");
report.status = textsOf(root, "duel-status");
""")

    assert report["grid"] == ["1", "2", "3", "4", "5", "6", "7", "8", "9"]
    assert report["sent"] == [["7", "d1", 1]], "a second press must not resend"
    assert report["lockedAfterClick"] is True

    # The security-critical assertion: their number is not in the tree.
    assert report["openHands"] == ["7", "🔒"], "theirs is a lock, not a number"
    assert report["openSpent"] == ["", ""], "nothing is spent until it resolves"

    assert report["revealHands"] == ["7", "4"], "both numbers are public now"
    assert report["scores"] == ["1", "0"]
    assert report["spent"] == ["spent 7", "spent 4"]
    assert report["log"] == ["7 vs 4"]
    assert report["status"] == ["✅ You take the point."]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_a_spent_number_cannot_be_played_again():
    report = run("number_clash", SNAPSHOT + r"""
const root = element("div");
renderer.mount(root, duel({
  round: 2, payload: {
    game_round: 2, used: { a: [9], b: [3] }, available: [1, 2, 3, 4, 5, 6, 7, 8],
  },
}), api);
report.disabled = buttons(root)
  .filter((b) => b.disabled).map((b) => b.textContent);
labelled(root, "9").click();
report.sent = sent.slice();
report.round = textsOf(root, "duel-round");
""")

    assert report["disabled"] == ["9"], "only the number already spent is dead"
    assert report["sent"] == [], "a spent number sends nothing"
    assert report["round"] == ["Round 2 of 7 · first to 4"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_a_grandmaster_watches_and_unmount_leaves_nothing():
    report = run("number_clash", SNAPSHOT + r"""
const root = element("div");
renderer.mount(root, duel({ you: null, payload: { available: [] } }), api);
report.leaderButtons = buttons(root).length;
report.leaderText = textOf(root);

renderer.update(duel({
  you: null, payload: { sudden_death: true, game_round: 8, available: [] },
}));
report.suddenDeath = textsOf(root, "duel-round");

renderer.unmount();
report.afterUnmount = root.children.length;
renderer.unmount();                 // idempotent
report.doubleUnmountOk = true;
""")

    assert report["leaderButtons"] == 0, "a Grandmaster cannot play the duel"
    assert "Round 1 in progress" in report["leaderText"]
    assert report["suddenDeath"] == ["SUDDEN DEATH — still first to 4"]
    assert report["afterUnmount"] == 0
    assert report["doubleUnmountOk"] is True
