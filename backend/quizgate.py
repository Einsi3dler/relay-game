"""The Grandmaster's door into ROLL CALL.

A third door beside the design gallery and God mode, sharing their lock
(`backend/devgate.py`): a constant-time check of a typed key, and a cookie so
the secret stops riding along in every link.

Its own secret, not `RELAY_GOD_KEY`. Different people may hold them, they
should rotate separately, and the `scope` in the cookie hash means a token
minted for one door never opens another even if somebody sets both to the same
string.

**The default is `"dev"` and this file is in a public repository**, so the
default is not a secret. Set `RELAY_QUIZ_HOST_KEY` in `.env.local` before
running a session with real people in the room.

Treat it as a closed door rather than a locked one. What is behind it is the
Reveal button.
"""

from __future__ import annotations

import os

from backend import devgate

HOST_KEY = os.environ.get("RELAY_QUIZ_HOST_KEY", "dev")
HOST_PATH = "/quizhost"
COOKIE_NAME = "relay_quizhost"
SCOPE = "quizhost"


def enabled(key: str | None) -> bool:
    return devgate.enabled(key, HOST_KEY)


def cookie_token() -> str:
    return devgate.cookie_token(SCOPE, HOST_KEY)


def authorised(key: str | None, cookie: str | None) -> bool:
    return devgate.authorised(key, cookie, SCOPE, HOST_KEY)


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta name="theme-color" content="#07091f">
<title>Roll Call: host</title>
<link rel="stylesheet" href="/static/style.css">
<link rel="stylesheet" href="/static/quiz.css">
<style>
  .gate {{ display: flex; align-items: center; justify-content: center;
          min-height: 100vh; padding: 20px; }}
  .gate__card {{ width: min(440px, 100%); }}
  .gate h1 {{ margin: 16px 0 6px; font-size: 1.6rem; font-weight: 900; }}
</style>
</head>
<body class="quiz">
<div class="gate">
  <div class="qpanel gate__card">
    <span class="quizbar__mark">
      <svg class="quizbar__bolt" viewBox="0 0 24 32" aria-hidden="true" focusable="false">
        <polygon points="15,0 0,19 9,19 7,32 24,12 14,12" fill="currentColor"/>
      </svg>
      <span>RELAY</span>
    </span>
    <h1>Roll Call</h1>
    <p class="quiz-sub" style="margin-bottom: 20px;">The host screen. Everyone
      else joins from the link in their own invitation.</p>
    <form method="post" action="{path}" class="jform">
      <label class="jfield">
        <span>Host key</span>
        <input class="jinput" type="password" name="key" autofocus
               autocomplete="current-password">
      </label>
      {error}
      <button class="qbtn qbtn--go qbtn--big" type="submit">Open the room</button>
    </form>
  </div>
</div>
</body>
</html>
"""

FAILED = '<p class="jerror">That is not the host key.</p>'


def login_html(failed: bool = False) -> str:
    return LOGIN_HTML.format(path=HOST_PATH, error=FAILED if failed else "")
