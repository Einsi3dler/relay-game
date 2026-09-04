"""God mode: run a match you are not playing in, and watch it happen.

Every viewer of a Relay match is normally a player. To see a board you must
hold a seat, and a person holding a seat they never play blocks the start
outright — the lobby refuses to begin while anyone is unassigned. So the one
person who most needs to see the whole table, the developer running the
session, is the one person the rules have no room for.

A God seat is a viewer that is not a player. It costs no roster slot, blocks no
start, and appears in nobody's snapshot. What it can do:

  * **Create a match** and hand out the code, without joining it.
  * **Hold the host's controls** — table size, match length, the duel window,
    team names, moving and kicking people, start, cancel, end. It does not hold
    the host *seat*: the first player to join still becomes host, so the match
    survives the God closing their tab.
  * **Name either team's Grandmaster**, overriding a seated one. This is the
    only move in the game itself a God is given, and it exists because a table
    where the wrong person grabbed the seat is a table that cannot start.
  * **Watch both Grandmaster dashboards, live and read-only.** Unmasked: a God
    sees through the Silence perk, reads both cleared counts, and sees both
    sides of a staked duel, which no seat at the table ever does.

What it deliberately cannot do: play, solve, buy a perk, fund a stake, hand off
a seat, or see a solver's board. Watching is not playing, and a dev tool that
could change who wins is not a dev tool.

Behind a password. Two ways in, and they are the same secret:

  * `?key=` on the URL, for scripts.
  * A form at `/god`, which sets a cookie so the key stops riding along in every
    link and in the browser history.

The secret is `RELAY_GOD_KEY`, and it is **not** the design gallery's key. That
one only ever exposes throwaway dummy matches; this one controls real ones, so
it is worth being able to hand out and rotate separately. **The default is
`"dev"` and this file is on GitHub**, so the default is not a secret at all —
set the environment variable (see `.env.local`, which is gitignored).

Even set, treat this as a closed door rather than a locked one. It keeps God
mode out of the way of people who should not be poking at it; it is not a
defence against anyone who wants in. What is behind it is a game of puzzles.
"""

from __future__ import annotations

import os

from backend import devgate

GOD_KEY = os.environ.get("RELAY_GOD_KEY", "dev")
GOD_PATH = "/god"
COOKIE_NAME = "relay_god"
SCOPE = "god"  # names this door in the cookie hash; see backend/devgate.py


# One-argument shapes, like preview.py's: this door, this secret.

def enabled(key: str | None) -> bool:
    return devgate.enabled(key, GOD_KEY)


def cookie_token() -> str:
    return devgate.cookie_token(SCOPE, GOD_KEY)


def authorised(key: str | None, cookie: str | None) -> bool:
    return devgate.authorised(key, cookie, SCOPE, GOD_KEY)


PAGE_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>God mode</title>
<link rel="stylesheet" href="/static/style.css">
<style>
  body {{ background: var(--gm-bg); color: var(--gm-text); }}
  .gd-wrap {{
    display: flex; align-items: center; justify-content: center;
    min-height: 100vh; padding: 20px;
  }}
  .gd-card {{
    width: min(460px, 100%);
    padding: 30px 28px;
    border: 1px solid var(--gm-line);
    border-radius: var(--gm-radius-lg);
    background: var(--gm-panel);
  }}
  .gd-mark {{
    display: flex; align-items: center; gap: 9px;
    font-weight: 900; font-size: 1.05rem; letter-spacing: 0.04em;
    text-transform: uppercase;
  }}
  .gd-mark svg {{ width: 15px; height: 20px; color: var(--gm-yellow); }}
  h1 {{ margin: 18px 0 6px; font-size: 1.5rem; font-weight: 900; }}
  p.sub {{ margin: 0 0 22px; color: var(--gm-muted); font-size: 0.9rem; line-height: 1.5; }}
  label {{
    display: block; margin-bottom: 8px; font-size: 0.7rem; font-weight: 800;
    letter-spacing: 0.14em; text-transform: uppercase; color: var(--gm-muted);
  }}
  input {{
    width: 100%; min-height: 48px; padding: 10px 14px;
    border: 1px solid var(--gm-line); border-radius: 12px;
    background: rgba(7, 9, 31, 0.6); color: var(--gm-text);
    font: inherit; font-weight: 700;
  }}
  input:focus {{ outline: none; border-color: var(--gm-cyan); }}
  button {{
    width: 100%; margin-top: 14px; min-height: 48px;
    border: 0; border-radius: 12px;
    background: linear-gradient(180deg, #ffd85a, var(--gm-yellow));
    color: #241a05; font: inherit; font-weight: 900; font-size: 1rem;
    text-transform: uppercase; letter-spacing: 0.04em; cursor: pointer;
  }}
  button.gd-ghost {{
    background: transparent; border: 1px solid var(--gm-line);
    color: var(--gm-text);
  }}
  .gd-error {{
    margin: 14px 0 0; color: var(--gm-red); font-size: 0.88rem; font-weight: 800;
  }}
  .gd-split {{
    margin: 26px 0 0; padding-top: 20px;
    border-top: 1px solid var(--gm-line-soft);
  }}
  .gd-foot {{
    margin: 22px 0 0; padding-top: 16px;
    border-top: 1px solid var(--gm-line-soft);
    color: var(--gm-muted); font-size: 0.78rem; line-height: 1.5;
  }}
  a {{ color: var(--gm-cyan); }}
  code {{
    background: rgba(7, 9, 31, 0.6); padding: 1px 5px; border-radius: 4px;
  }}
</style>
</head>
<body>
<div class="gd-wrap">
  <div class="gd-card">
    <span class="gd-mark">
      <svg viewBox="0 0 24 32" aria-hidden="true" focusable="false">
        <polygon points="15,0 0,19 9,19 7,32 24,12 14,12" fill="currentColor"/>
      </svg>
      <span>Relay</span>
    </span>
"""

PAGE_FOOT = """  </div>
</div>
</body>
</html>
"""

LOGIN_BODY = """    <h1>God mode</h1>
    <p class="sub">Run a match you are not playing in, and watch both
      Grandmaster boards while it happens. Not part of the game.</p>
    <form method="post" action="{path}">
      <label for="gd-key">Password</label>
      <input id="gd-key" name="key" type="password" autocomplete="current-password"
             autofocus required>
      <button type="submit">Open God mode</button>
    </form>
    {error}
    <p class="gd-foot">A God seat holds the host's controls and can name a
      Grandmaster. It cannot play, spend, or touch a board.
      <a href="/">Back to the site</a></p>
"""

CONSOLE_BODY = """    <h1>God mode</h1>
    <p class="sub">Start a new match to run, or watch one that is already
      going. Either way you take no seat and nobody at the table sees you.</p>
    <form method="post" action="/god/new" id="gd-new">
      <button type="submit">Create a match to run</button>
    </form>
    <div class="gd-split">
      <form method="post" action="/god/watch" id="gd-watch">
        <label for="gd-match">Watch a match in progress</label>
        <input id="gd-match" name="match_id" type="text" autocomplete="off"
               placeholder="match code" required>
        <button type="submit" class="gd-ghost">Watch it</button>
      </form>
    </div>
    {error}
    <p class="gd-foot">The password lives in <code>RELAY_GOD_KEY</code>. The
      default is in the source and the source is public, so this is a closed
      door rather than a locked one. <a href="/preview">Design gallery</a></p>
"""


def login_html(failed: bool = False) -> str:
    error = '<p class="gd-error">That is not the password.</p>' if failed else ""
    return PAGE_HEAD + LOGIN_BODY.format(path=GOD_PATH, error=error) + PAGE_FOOT


def console_html(error: str = "") -> str:
    """The two doors in: make a match, or watch one that exists."""
    block = f'<p class="gd-error">{error}</p>' if error else ""
    return PAGE_HEAD + CONSOLE_BODY.format(error=block) + PAGE_FOOT
