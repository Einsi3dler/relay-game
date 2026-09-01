"""The perk catalogue's contract with the shipped frontend.

Screen-effect perks are the one place where a backend id has to match a
hand-written CSS class name and a JS array. Nothing else would catch a typo:
the server would stamp a deadline, the client would toggle a class no
stylesheet defines, and the perk would silently do nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend import config
from backend.registry import REGISTERED_DUELS, REGISTERED_MODULES

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
APP_JS = (FRONTEND / "app.js").read_text()
STYLE_CSS = (FRONTEND / "style.css").read_text()
DASHBOARD_CSS = (FRONTEND / "dashboard.css").read_text()


def test_every_effect_perk_names_a_known_effect():
    for perk_id, perk in config.PERKS.items():
        effect = perk.get("effect")
        if effect is not None:
            assert effect in config.SCREEN_EFFECTS, perk_id
            # An effect perk is an attack with a duration, or it can't land.
            assert perk["kind"] == "attack" and perk["seconds"] > 0, perk_id


def test_the_client_knows_every_screen_effect():
    match = re.search(r"var SCREEN_EFFECTS = \[(.*?)\];", APP_JS, re.S)
    assert match, "app.js no longer declares SCREEN_EFFECTS"
    in_js = set(re.findall(r'"([a-z_]+)"', match.group(1)))
    assert in_js == set(config.SCREEN_EFFECTS)


def test_every_screen_effect_has_a_stylesheet_rule():
    for effect in config.SCREEN_EFFECTS:
        assert f".fx-{effect}" in STYLE_CSS, effect


def test_reduced_motion_keeps_a_substitute_for_the_moving_effects():
    """A victim with 'reduce motion' set must still lose something, or buying
    the perk becomes a coin-flip on their OS settings."""
    blocks = STYLE_CSS.split("@media (prefers-reduced-motion: reduce)")
    assert len(blocks) > 1
    reduced = blocks[-1]
    assert "fx-wobble" in reduced and "fx-blur-pulse" in reduced


def test_every_perk_carries_a_shop_description():
    for perk_id, perk in config.PERKS.items():
        assert perk.get("desc"), perk_id
        assert perk["kind"] in ("attack", "defense"), perk_id
        assert perk["cost"] > 0, perk_id


# --- the Grandmaster's game marks ----------------------------------------
#
# The roster names a teammate's assigned game with an icon, and the icon is a
# CSS mask keyed by the game id. Same failure shape as the screen effects: a
# game registered without a rule would draw the fallback board mark forever and
# nothing else would say so.


def _mask_files(css: str, class_name: str) -> list[str]:
    """The files a `.gm-ic--…` rule masks with, if the rule exists at all."""
    block = re.search(
        r"\." + re.escape(class_name) + r"\s*\{([^}]*)\}", css
    )
    if block is None:
        return []
    return re.findall(r'url\("([^"]+)"\)', block.group(1))


def test_every_registered_game_has_a_mark_on_the_dashboard():
    for module in REGISTERED_MODULES:
        assert _mask_files(DASHBOARD_CSS, f"gm-ic--game-{module.id}"), module.id


def test_every_registered_duel_has_a_mark_on_the_dashboard():
    for duel in REGISTERED_DUELS:
        assert _mask_files(DASHBOARD_CSS, f"gm-ic--game-{duel.id}"), duel.id


def test_a_game_without_a_mark_still_draws_something():
    """The bare `.gm-ic--game` fallback, so an id with no rule of its own gets a
    board rather than the solid square an unmasked element would paint."""
    assert _mask_files(DASHBOARD_CSS, "gm-ic--game")


def test_every_mark_the_dashboard_asks_for_is_a_file_that_ships():
    """Masks fail silently in the browser: a wrong path paints nothing at all,
    with no console error and no missing-image box."""
    for path in set(re.findall(r'mask-image:\s*url\("([^"]+)"\)', DASHBOARD_CSS)):
        assert path.startswith("/static/"), path
        assert (FRONTEND / path[len("/static/"):]).is_file(), path
