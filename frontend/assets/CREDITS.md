# Third-party assets

Every file under `frontend/assets/` and where it came from. Both sources below
permit commercial use. Kenney's CC0 needs no attribution and Lucide's ISC needs
only the licence text kept with the source, but everything is recorded here for
traceability, as the UI handoff rules require.

Nothing else in the frontend is third-party: the landing page artwork and the
Relay wordmark are hand-authored inline SVG.

---

## Kenney — Board Game Icons (1.1)

- **Creator:** Kenney — <https://kenney.nl>
- **Source:** <https://kenney.nl/assets/board-game-icons>
- **Licence:** CC0 1.0 Universal — <http://creativecommons.org/publicdomain/zero/1.0/>
- **Attribution required:** no (recorded voluntarily)

| Local file | Pack file |
|---|---|
| `ui/crown.svg` | `crown_b.svg` |
| `ui/shield.svg` | `shield.svg` |
| `ui/timer.svg` | `hourglass.svg` |
| `ui/bomb.svg` | `exploding.svg` |
| `ui/team.svg` | `pawns.svg` |
| `ui/level.svg` | `flag_square.svg` |
| `perks/scramble.svg` | `cards_shuffle.svg` |
| `perks/clock-burn.svg` | `fire.svg` |
| `perks/skim.svg` | `pouch_remove.svg` |
| `perks/shield.svg` | `shield.svg` |
| `perks/extend-wait.svg` | `hourglass.svg` |
| `logos/knight.svg` | `chess_knight.svg` |
| `logos/rook.svg` | `chess_rook.svg` |
| `logos/bishop.svg` | `chess_bishop.svg` |
| `logos/queen.svg` | `chess_queen.svg` |
| `logos/bow.svg` | `bow.svg` |
| `logos/skull.svg` | `skull.svg` |
| `logos/campfire.svg` | `campfire.svg` |
| `logos/tower.svg` | `structure_tower.svg` |

**Modifications:** the pack ships these paths centred on the origin with no
`viewBox` and a baked `fill="#FFFFFF"`. Each file here has `viewBox="-32 -32 64 64"`
added (one uniform box across the pack, so the icons keep their relative sizes)
and the fill changed to `currentColor`. Geometry is untouched.

---

## Lucide

- **Creators:** Lucide contributors, forked from Feather Icons (Cole Bemis)
- **Source:** <https://lucide.dev> — <https://github.com/lucide-icons/lucide>
- **Licence:** ISC — <https://github.com/lucide-icons/lucide/blob/main/LICENSE>
- **Attribution required:** licence text must accompany the source

| Local file | Icon name |
|---|---|
| `ui/coin.svg` | `coins` |
| `ui/duel.svg` | `swords` |
| `ui/reflect.svg` | `refresh-cw` |
| `ui/insurance.svg` | `umbrella` |
| `ui/warning.svg` | `triangle-alert` |
| `ui/connection-off.svg` | `wifi-off` |
| `ui/cleared.svg` | `circle-check` |
| `ui/spectating.svg` | `eye` |
| `perks/freeze.svg` | `snowflake` |
| `perks/silence.svg` | `volume-x` |
| `perks/wobble.svg` | `audio-waveform` |
| `perks/static.svg` | `radio-tower` |
| `perks/mirror.svg` | `flip-horizontal-2` |
| `perks/blackout.svg` | `eye-off` |
| `perks/reflect.svg` | `refresh-cw` |
| `perks/insurance.svg` | `umbrella` |
| `ui/star.svg` | `star` |
| `ui/key.svg` | `key-round` |

**Modifications:** whitespace collapsed onto one line. Geometry, `viewBox`,
`stroke="currentColor"` and stroke widths are unchanged, except `ui/star.svg`,
whose `fill="none"` became `fill="currentColor"`: it is drawn through a CSS
mask, which uses the shape's alpha, so an unfilled star would render as an
outline at every level of the race track.

### ISC License

Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part of
Feather (MIT). All other copyright (c) for Lucide are held by Lucide Contributors
2022.

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice
and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.

---

## Duel stages — no third-party assets

The duel-mode handoff spec pointed at DiceBear, Kenney and Game-icons.net for
Crown Duel's characters, Bid War's coins and the rest. None was needed, and
nothing was imported:

- Crown Duel's five characters, Number Clash's numerals and Bid War's lots are
  **text and CSS** — the numerals are real text so they stay crisp and
  selectable, and the card frames are borders, not images.
- The card, crown, coin and lock glyphs are **Unicode emoji**, rendered by the
  reader's own system font. No file ships with them.
- Backgrounds and panels reuse the existing Relay tokens in
  `frontend/style.css`.

The supplied mockups were treated as references for layout, not as production
images: no screenshot is served, and there is no image asset behind any duel
control. If a duel ever does need artwork, record it here first, under the same
headings as the packs above.

---

## Game marks — hand-authored, no third party

The Grandmaster's roster names each teammate's assigned game with an icon, and
the duel seats and the shop share the same set. Nothing was imported for it:
`frontend/assets/games/*.svg` are drawn here, in the same idiom as the Lucide
files above — a 24x24 `viewBox`, `fill="none"`, `stroke="currentColor"`, 2px
round-capped strokes — so they mask through `.gm-ic` identically and no licence
travels with them.

| Local file | Game | What it draws |
|---|---|---|
| `games/generic.svg` | (fallback) | a quartered board |
| `games/rewire.svg` | Rewire | a wire bent between two terminals |
| `games/sweep.svg` | Sweep | a flag on a pole |
| `games/mirror_run.svg` | Mirror Run | two chevrons either side of a mirror line |
| `games/decant.svg` | Decant | a tube with a fill line |
| `games/echo.svg` | Echo | four pads, one lit |
| `games/overprint.svg` | Overprint | stacked layers |
| `games/stackdrop.svg` | Stackdrop | a ball falling into a container |
| `games/lane_shift.svg` | Lane Shift | a belt forking into two lanes |
| `games/shadow_cast.svg` | Shadow Cast | a cube |
| `games/threadline.svg` | Threadline | a routed line through two anchors |
| `games/rps_duel.svg` | Rock Paper Scissors | a hand |
| `games/number_clash.svg` | Number Clash | a hash |
| `games/bid_war.svg` | Bid War | a gavel over its block |

Bomb Defuse and Crown Duel have no file of their own: they reuse `ui/bomb.svg`
and `ui/crown.svg`, which are already the right shape and would otherwise be
the same drawing maintained in two places.

Every id in `backend/registry.py` is pinned to a mark by
`tests/test_perk_frontend_parity.py`, and the same suite checks that every
`mask-image` path in `dashboard.css` is a file that actually ships — a mask
with a wrong path paints nothing at all, with no console error to notice.

---

## Roster avatars — generated locally, no third party

Roster avatars are drawn in the browser from a deterministic seed (match id
plus player id): eyes and a mouth on a seeded ground, in the spirit of
DiceBear's `pixel-art-neutral` but with no network request and nothing
vendored. Nothing is stored on the player model, nothing is derived from the
player's name, and the roster renders identically offline.

See `avatarSvg` in `frontend/app.js`.
