"""Test-wide setup.

One job: keep the account database off disk. `backend/accounts.py` opens a real
SQLite file by default, and `backend/main.py` opens it from the app lifespan —
so any test that builds a TestClient would otherwise create `var/relay.db` in
the working tree and write to it. On a developer's machine that is the same
file the dev server uses, which makes a test run capable of touching real
accounts.

This sets RELAY_DB_PATH rather than just opening an in-memory connection,
because suites that want clean rows (tests/test_accounts.py) close the
connection between cases. A fixture that only connected once would be undone by
the first of those, and every later `connect()` would fall back to the file.
The environment variable survives that: there is no path back to disk to fall
back to.
"""

from __future__ import annotations

import os

import pytest

from backend import accounts


@pytest.fixture(autouse=True, scope="session")
def _accounts_in_memory():
    previous = os.environ.get("RELAY_DB_PATH")
    os.environ["RELAY_DB_PATH"] = ":memory:"
    accounts.close()
    accounts.connect()
    yield
    accounts.close()
    if previous is None:
        os.environ.pop("RELAY_DB_PATH", None)
    else:
        os.environ["RELAY_DB_PATH"] = previous
