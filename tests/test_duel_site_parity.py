"""The duel catalogue's contract with the shipped website.

A duel game is invisible until someone reads about it: nobody picks one, so a
player only ever meets it when the server deals it to them mid-match. That
makes the written pages part of the feature rather than decoration, and makes
this the only thing that would catch a fifth duel being registered and never
described anywhere.

What each page owes a registered duel:

  * `/games` — its own rules section, plus a line in the Duelist index that
    links to it, since that section is where `/` sends people;
  * `/` — a name in the duel list, so the library on the landing page is the
    real one;
  * the client shell — a display name, or the roster falls back to the raw id.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.registry import REGISTERED_DUELS, REGISTERED_MODULES

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
GAMES_HTML = (FRONTEND / "games.html").read_text()
LANDING_HTML = (FRONTEND / "landing.html").read_text()
EXPLORE_HTML = (FRONTEND / "explore.html").read_text()
APP_JS = (FRONTEND / "app.js").read_text()

# The anchor a duel's rules section lives at. Every duel now has one of its
# own; `#duels` is the shared Duelist overview (the role, the round clock, the
# two-duel series, what a win pays), which is what "how duels work" links to.
ANCHORS = {
    "rps_duel": "rps-duel",
    "crown_duel": "crown-duel",
    "number_clash": "number-clash",
    "bid_war": "bid-war",
}

# The pages call every duel by its module name, with one deliberate exception:
# RPS DUEL is the catalogue name it shipped under, and "Rock Paper Scissors"
# reads as the party game rather than the Duelist's.
ALIASES = {"rps_duel": "RPS Duel"}


def label(duel) -> str:
    return ALIASES.get(duel.id, duel.name)


def test_the_pages_use_the_module_names():
    """One alias, on purpose. Anything else is drift between page and module."""
    assert set(ALIASES) == {"rps_duel"}
    for duel in REGISTERED_DUELS:
        if duel.id not in ALIASES:
            assert label(duel) == duel.name


def test_every_registered_duel_has_an_anchor_here():
    """A new duel joins this suite by being registered, not by hand."""
    assert {duel.id for duel in REGISTERED_DUELS} == set(ANCHORS)


def test_every_duel_has_its_own_rules_section():
    for duel in REGISTERED_DUELS:
        assert f'id="{ANCHORS[duel.id]}"' in GAMES_HTML, duel.id
        # Named in full, in the shouty case the rest of the library uses.
        assert label(duel).upper() in GAMES_HTML, duel.name


def test_the_duelist_section_indexes_every_duel():
    """`/` links to #duels; from there the other three have to be findable."""
    index = GAMES_HTML.split('<ul class="duel-index">')[1].split("</ul>")[0]
    for duel in REGISTERED_DUELS:
        assert f'href="#{ANCHORS[duel.id]}"' in index, duel.id
        assert label(duel) in index, duel.name


def test_the_landing_page_names_every_duel():
    listing = LANDING_HTML.split('<ul class="rl-duel-list">')[1].split("</ul>")[0]
    for duel in REGISTERED_DUELS:
        assert label(duel) in listing, duel.name
        assert f'href="/games#{ANCHORS[duel.id]}"' in listing, duel.id


def test_the_library_count_matches_the_catalogue():
    """The landing page's count is the real one, duels included."""
    count = re.search(
        r"(\d+) action games and (\d+) duels in the current library", LANDING_HTML
    )
    assert count is not None, "the library count line changed shape"
    assert int(count.group(1)) == len(REGISTERED_MODULES)
    assert int(count.group(2)) == len(REGISTERED_DUELS)


def test_practice_mode_offers_every_duel_as_a_room():
    """A duel still cannot be a solo board, but /explore can hand you a room
    and a link to send. Every registered duel has to be on that picker, or a
    fifth one ships invisible — which is what this whole file exists to stop."""
    picker = EXPLORE_HTML.split('id="duel-tabs"')[1].split("</div>")[0]
    for duel in REGISTERED_DUELS:
        assert f'data-duel="{duel.id}"' in picker, duel.id
        assert label(duel) in picker, duel.name
    assert 'href="/games#duels"' in EXPLORE_HTML


def test_practice_mode_never_offers_a_duel_as_a_solo_board():
    """The older half of the rule, still true. `data-game` is the solo picker,
    and half of a duel is not knowing what the other person just did. A room is
    two people; it is not practice."""
    for duel in REGISTERED_DUELS:
        assert f'data-game="{duel.id}"' not in EXPLORE_HTML, duel.id


def test_the_shell_can_name_every_duel():
    """`gameName()` falls back to the raw id, which is what a player would see
    on the roster if a duel were missing from the catalogue."""
    names = APP_JS.split("var DUEL_NAMES = {")[1].split("};")[0]
    for duel in REGISTERED_DUELS:
        assert f"{duel.id}:" in names, duel.id
        assert duel.name in names, duel.name
