"""The design gallery: the door, the links, and what is behind them.

The gallery is a dev tool, so the bar is different from the game's: it does not
have to be pretty, but it does have to be *complete* and it must never touch a
real match. Three things are worth pinning.

  * The door. A wrong key is a 404, the same answer any unknown path gives.
  * The links. Every entry the page lists has to resolve, or the gallery rots
    into a page of dead ends exactly when someone is relying on it.
  * The isolation. A preview builds a throwaway match that never enters the
    store, so it cannot collide with a game somebody is playing.

That the entries actually *render* is proved next door, in test_app_shell.py,
by running the shipped client against these same snapshots.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from fastapi.testclient import TestClient

import backend.main as server
from backend import preview
from backend.registry import REGISTERED_DUELS, REGISTERED_MODULES

GALLERY = f"{preview.PREVIEW_PATH}?key={preview.PREVIEW_KEY}"


@pytest.fixture
def client():
    with TestClient(server.app) as test_client:
        yield test_client


def links(client) -> list[str]:
    body = client.get(GALLERY).text
    return re.findall(r'href="([^"]+)"', body)


# --- the door -------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/api/preview?state=lobby",
    "/api/preview?key=wrong&state=lobby",
])
def test_the_api_stays_silent_without_the_key(client, path):
    """A 403 would confirm the path exists. A 404 says nothing, and nothing is
    what a script poking at the API should learn."""
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", [
    preview.PREVIEW_PATH,
    f"{preview.PREVIEW_PATH}?key=",
    f"{preview.PREVIEW_PATH}?key=wrong",
])
def test_the_door_asks_for_the_password(client, path):
    """The page itself is linked from the site, so hiding it behind a 404 would
    be pretending it is not there while pointing at it. It asks instead, and
    gives nothing away until asked correctly."""
    response = client.get(path)
    assert response.status_code == 200
    assert "Developer preview" in response.text
    assert "Password" in response.text
    # The door is a door, not a peephole: none of the gallery is behind it yet.
    assert "Design gallery" not in response.text
    # ...and it does not hand over the answer it is asking for.
    assert f'value="{preview.PREVIEW_KEY}"' not in response.text


def test_the_wrong_password_is_refused_and_sets_no_cookie(client):
    response = client.post(preview.PREVIEW_PATH, data={"key": "not-it"})
    assert response.status_code == 401
    assert "not the password" in response.text
    assert preview.COOKIE_NAME not in response.cookies


def test_the_password_trades_for_a_cookie(client):
    response = client.post(
        preview.PREVIEW_PATH, data={"key": preview.PREVIEW_KEY},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == preview.PREVIEW_PATH
    jar = response.cookies[preview.COOKIE_NAME]
    # The cookie carries a hash, never the password itself.
    assert jar != preview.PREVIEW_KEY
    assert jar == preview.cookie_token()


def test_the_cookie_opens_the_gallery_without_the_key_in_any_link(client):
    """Once the browser is carrying the secret, the links stop carrying it —
    otherwise the password ends up in every href and in the history."""
    client.post(preview.PREVIEW_PATH, data={"key": preview.PREVIEW_KEY})
    body = client.get(preview.PREVIEW_PATH).text
    assert "Design gallery" in body
    assert "key=" not in body
    # ...and the snapshots behind those links open on the cookie alone.
    assert client.get("/api/preview?preview=lobby").status_code == 200


def test_a_forged_cookie_opens_nothing(client):
    client.cookies.set(preview.COOKIE_NAME, "0" * 64)
    assert "Design gallery" not in client.get(preview.PREVIEW_PATH).text
    assert client.get("/api/preview?preview=lobby").status_code == 404


def test_every_duel_can_be_seen_on_its_card_round(client):
    """Crown Duel's first engine round is the secret strategy beat, so the
    gallery used to have no way to reach the hand — the screen the cards
    actually live on. `phase=cards` resolves whatever is open and leaves the
    round behind it live."""
    for duel in REGISTERED_DUELS:
        body = client.get(
            f"/api/preview?preview=duel&game={duel.id}&phase=cards"
            f"&key={preview.PREVIEW_KEY}"
        ).json()["state"]["duel"]
        assert body["phase"] == "choosing", duel.id
        assert body["round"] == 2, duel.id


def test_the_card_round_actually_deals_crown_duel_a_hand(client):
    """The point of the entry: five cards to look at, not a strategy prompt."""
    body = client.get(
        f"/api/preview?preview=duel&game=crown_duel&phase=cards"
        f"&key={preview.PREVIEW_KEY}"
    ).json()["state"]["duel"]["payload"]
    assert body["phase"] == "combat"
    assert [card["type"] for card in body["hand"]] == [
        "king", "knight", "guard", "assassin", "peasant",
    ]


def test_the_key_opens_it(client):
    response = client.get(GALLERY)
    assert response.status_code == 200
    assert "Design gallery" in response.text
    # Search engines are told to stay out even if it is ever reachable.
    assert 'content="noindex, nofollow"' in response.text


def test_an_unknown_scenario_is_a_404(client):
    url = f"/api/preview?key={preview.PREVIEW_KEY}&state=nonsense"
    assert client.get(url).status_code == 404


# --- the links ------------------------------------------------------------

def test_every_link_on_the_page_resolves(client):
    found = links(client)
    assert found, "the gallery listed nothing at all"
    for href in found:
        assert client.get(href).status_code == 200, href


def test_every_registered_game_has_a_practice_link(client):
    found = links(client)
    for module in REGISTERED_MODULES:
        assert f"/explore?game={module.id}" in found, module.id


def test_every_registered_duel_is_shown_in_both_phases(client):
    body = client.get(GALLERY).text
    for duel in REGISTERED_DUELS:
        for phase in ("choosing", "reveal"):
            assert f"preview=duel&game={duel.id}&phase={phase}" in body, duel.id


def test_every_screen_effect_perk_is_shown(client):
    body = client.get(GALLERY).text
    for effect in server.config.SCREEN_EFFECTS:
        assert f"effect={effect}" in body, effect


def test_the_public_pages_are_all_listed(client):
    found = links(client)
    for page in ("/", "/games", "/explore", "/play"):
        assert page in found, page


def test_every_preview_link_has_a_working_snapshot_behind_it(client):
    """The load-bearing test, and the one that was wrong.

    `/play?preview=X` serves the shell whatever you ask it for, so the link
    resolving proves nothing at all: the client then forwards that query string
    **verbatim** to /api/preview, and if the API spells the parameter
    differently every gallery entry 404s into the join view. It shipped that
    way once because this test rewrote the query before asking. So: the query
    goes to the API exactly as the page wrote it, character for character.
    """
    checked = 0
    for href in links(client):
        if "preview=" not in href:
            continue
        query = href.split("?", 1)[1]
        response = client.get(f"/api/preview?{query}")
        assert response.status_code == 200, href
        assert response.json()["state"]["id"], href
        checked += 1
    assert checked >= len(REGISTERED_DUELS) * 2, "the shell entries went missing"


def test_the_client_reads_the_parameter_the_gallery_writes(client):
    """Both ends of the forward, pinned in one place. The gallery writes
    `preview=`, the shipped client looks for `preview`, and the route above
    accepts `preview` — three files that have to agree."""
    app_js = (
        pathlib.Path(__file__).resolve().parents[1] / "frontend" / "app.js"
    ).read_text()
    assert 'get("preview")' in app_js
    # ...and it forwards the whole query rather than rebuilding it, which is
    # what makes the test above a real end-to-end check.
    assert 'fetch("/api/preview" + window.location.search)' in app_js
    assert "preview=" in client.get(GALLERY).text


# --- what is behind them --------------------------------------------------

def snapshot(state: str, **params: str) -> dict:
    built = preview.snapshot(state, **params)
    assert built is not None
    return built


def test_the_lobby_viewer_is_the_host_and_a_grandmaster(client):
    """One viewer, so one page shows both the host controls and the panel a
    Grandmaster assigns from."""
    state = snapshot("lobby")
    assert state["status"] == "lobby"
    assert state["me"]["is_leader"] is True
    assert state["host_player_id"] == state["me"]["id"]
    assert len(state["teams"]["alpha"]["players"]) == 5


def test_a_solving_preview_carries_a_real_board(client):
    me = snapshot("solving")["me"]
    assert me["status"] == "solving"
    assert me["current_puzzle"]["game_id"] in {m.id for m in REGISTERED_MODULES}
    assert me["current_puzzle"]["payload"], "an empty board is not worth looking at"


def test_the_cleared_preview_has_the_choice_open_and_the_clock_running(client):
    me = snapshot("cleared")["me"]
    assert me["status"] == "cleared"
    assert me["choice_pending"] is True
    assert me["timer_kind"] == "wait" and me["timer_deadline"]


def test_the_bonus_preview_is_on_the_gamble(client):
    assert snapshot("bonus")["me"]["status"] == "bonus"


@pytest.mark.parametrize("effect", ["wobble", "static", "mirror", "blackout"])
def test_a_screen_effect_preview_stamps_only_that_effect(client, effect):
    me = snapshot("solving", effect=effect)["me"]
    assert list(me["screen_effects"]) == [effect]


def test_the_leader_preview_can_afford_the_shop(client):
    state = snapshot("leader")
    assert state["me"]["is_leader"] is True
    assert state["teams"]["alpha"]["currency"] > 0, "an empty purse greys the shop out"


@pytest.mark.parametrize("duel", [d.id for d in REGISTERED_DUELS])
@pytest.mark.parametrize("phase", ["choosing", "reveal"])
def test_a_duel_preview_is_the_game_and_phase_that_was_asked_for(client, duel, phase):
    state = snapshot("duel", game=duel, phase=phase)
    view = state["duel"]
    assert view is not None, "the viewer has to be one of the two Duelists"
    assert view["duel_game_id"] == duel
    assert view["phase"] == phase
    # A reveal is only worth looking at with both moves on the table.
    assert bool(view["choices"]) is (phase == "reveal")


def test_the_crown_duel_reveal_shows_cards_not_the_strategy_beat(client):
    """Crown Duel spends a whole round deciding whether to sacrifice. The
    reveal worth redesigning is the one with two cards on the table."""
    view = snapshot("duel", game="crown_duel", phase="reveal")["duel"]
    played = set(view["choices"].values())
    assert played and not (played & {"normal", "sacrifice"})


@pytest.mark.parametrize("state,winner", [("won", "alpha"), ("lost", "bravo")])
def test_the_result_previews_end_the_match_both_ways(client, state, winner):
    built = snapshot(state)
    assert built["status"] == "finished"
    assert built["winner_team_id"] == winner
    assert (built["me"]["team_id"] == winner) is (state == "won")


# --- isolation ------------------------------------------------------------

def test_a_preview_match_never_enters_the_store(client):
    """The gallery must not be able to collide with a game being played."""
    match_id = snapshot("solving")["id"]
    assert client.get(f"/api/matches/{match_id}").status_code == 404


def test_every_preview_is_a_fresh_match(client):
    assert snapshot("solving")["id"] != snapshot("solving")["id"]
