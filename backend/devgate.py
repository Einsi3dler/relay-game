"""The password gate the developer doors share.

Two doors now stand in front of dev-only tools: the design gallery
(`backend/preview.py`) and God mode (`backend/god.py`). Both want the same
three things — a constant-time check of a typed password, a cookie to hold
onto so the secret stops riding along in every link, and a constant-time check
of that cookie — so they live here once instead of twice.

Each door keeps its **own** secret and its own cookie name. The `scope` that
goes into the cookie hash is what keeps them apart: without it, a deployment
that happened to set both keys to the same string would let a gallery cookie
open God mode, which is precisely the mix-up the two-secret split exists to
prevent.

What this is not: a login system. There are no accounts, nothing is rate
limited, and the defaults are in a public repo. Read the docstrings on the two
modules that use it for what each door actually protects.
"""

from __future__ import annotations

import hashlib
import hmac


def enabled(key: str | None, secret: str) -> bool:
    """True when this key is the password. Constant-time, so the comparison
    cannot be used to learn the secret one character at a time."""
    return bool(key) and hmac.compare_digest(key, secret)


def cookie_token(scope: str, secret: str) -> str:
    """What a correct password earns: a hash of it, not the password itself, so
    the secret is not sitting in a cookie jar in plain text. Anyone who knows
    the key can compute this, which is fine — it only ever claims "this browser
    knew the password", and that is all it needs to say.

    `scope` names the door, so a token minted for one never opens another.
    """
    return hashlib.sha256(f"relay-{scope}:{secret}".encode()).hexdigest()


def authorised(key: str | None, cookie: str | None, scope: str, secret: str) -> bool:
    """Either way in. The cookie is checked in constant time too."""
    if enabled(key, secret):
        return True
    return bool(cookie) and hmac.compare_digest(cookie, cookie_token(scope, secret))
