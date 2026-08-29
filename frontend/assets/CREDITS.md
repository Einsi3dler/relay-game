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

**Modifications:** whitespace collapsed onto one line. Geometry, `viewBox`,
`stroke="currentColor"` and stroke widths are unchanged.

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

## Runtime service — DiceBear (roster avatars)

Roster avatars are generated at runtime from `api.dicebear.com` using the
`pixel-art-neutral` style, seeded from the match id and player id. No avatar
data is stored in the game model and no avatar file is vendored here.

- **Source:** <https://www.dicebear.com/styles/pixel-art-neutral/>
- **Licence:** CC0 1.0 (style), MIT (DiceBear itself)
- **Fallback:** if the request fails or is blocked, the roster falls back to a
  deterministic initials token rendered in CSS, so the dashboard works offline.
