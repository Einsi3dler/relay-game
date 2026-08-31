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
    preview.PREVIEW_PATH,
    f"{preview.PREVIEW_PATH}?key=",
    f"{preview.PREVIEW_PATH}?key=wrong",
    "/api/preview?state=lobby",
    "/api/preview?key=wrong&state=lobby",
])
def test_the_wrong_key_is_a_404_not_a_403(client, path):
    """A 403 would confirm the path exists. A 404 says nothing."""
    assert client.get(path).status_code == 404


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
    """A `/play?preview=` link serves the shell whatever you ask for, so the
    link resolving proves nothing. This asks the API the same question the
    browser will."""
    for href in links(client):
        if "preview=" not in href:
            continue
        query = href.split("?", 1)[1].replace("preview=", "state=")
        response = client.get(f"/api/preview?{query}")
        assert response.status_code == 200, href
        assert response.json()["state"]["id"], href


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
