# God mode

A seat that runs a match without playing in it. Dev only, behind a password.

Every viewer of a Relay match is normally a player, and a player who never plays
blocks the start outright: the lobby refuses to begin while anyone is
unassigned. So the one person who most needs to see the whole table, whoever is
running the session, was the one person the rules had no room for. God mode is
that room.

## Getting in

```text
http://127.0.0.1:8000/god?key=dev
```

Two ways past the door and one secret behind both: `?key=` on the URL, or the
password form, which trades the password for a cookie so the key stops riding
along in every link. The console then offers two doors:

- **Create a match to run.** A fresh match with a God seat on it and nobody in
  it yet. Hand out the code that lands in your address bar.
- **Watch a match in progress.** Type an existing match code and sit down behind
  it. Nothing at the table changes when you do.

Either one lands you on `/play?god=<observer_id>&match=<code>`. Keep that link:
it is your way back in, the way `?key=` is for the design gallery. There is no
rejoin code for a God seat, on purpose (see below).

## What the seat can do

- **Hold the host's controls.** Table size, match length, the duel round window,
  team names, moving and kicking people, start, cancel, end session.
- **Name either team's Grandmaster.** The crown on each lobby row. Unlike a
  player's `claim_leader`, this overrides a seated, connected Grandmaster,
  because a table where the wrong person grabbed the seat is a table that
  cannot start. In the feed it reads exactly like an ordinary claim.
- **Watch, unmasked.** Both teams whole in every status: rosters, currency,
  active perks, cleared counts, rejoin codes, the unfiltered event feed, the
  live duel, and both sides of a staked duel, which no seat at the table ever
  sees. It sees through the Silence perk too: that is an attack on a
  Grandmaster's own read-out, not on someone watching from outside it.
- **Sit in either Grandmaster's chair, read-only.** The two switches in the
  floating bar draw that seat's real dashboard, live. Every control on it is
  inert.

## What it cannot do

Play. It cannot solve, choose wait or bonus, buy a perk, fund a stake, hand off
a seat, or assign a role or a game. Roles and games still come from the two
Grandmasters, which means a God can seat them but cannot satisfy the start gate
alone. It does not see any solver's board, only their status.

## Two rules worth knowing

**The God is invisible.** It takes no roster seat, blocks no start, appears in
no roster, no `unassigned`, no capacity count and no event. Connecting and
disconnecting broadcast nothing. Nobody at the table can tell one is watching.

**The God is not the host.** The first player to join still becomes host, and
the host seat only ever names a real player. That is deliberate rather than
incidental: `claim_host` decides whether the seat is up for grabs by looking its
holder up in `match.players` and asking whether they are connected, so an
observer sitting there would make that guard read nothing and let any player
seize the seat at will. The consequence you want is the other one: if you close
your tab, the match carries on.

## The password

`RELAY_GOD_KEY`, and it is **not** the design gallery's `RELAY_PREVIEW_KEY`.
That one only ever exposes throwaway dummy matches; this one controls real ones,
so it is worth being able to hand out and rotate separately. The door name is
folded into the cookie hash, so even a deployment that sets both keys to the
same string cannot open one door with the other's cookie.

The default is `dev` and this repo is public, so the default is not a secret at
all. Set it in `.env.local`, which is gitignored and which `run.sh` sources:

```bash
RELAY_GOD_KEY=something-only-you-know
```

Even set, treat this as a closed door rather than a locked one. It keeps God
mode out of the way of people who should not be poking at it; it is not a
defence against anyone who wants in. What is behind it is a game of puzzles.

One thing that follows from the door being what it is: a God seat holds no
rejoin code. `engine.rejoin` walks the players looking for a matching code, and
a seat with the host's controls that could be bought for six characters would
not be worth gating at all.

## Where it lives

| Piece | File |
| --- | --- |
| The seat, the doors, the console | `backend/god.py` |
| The gate both dev doors share | `backend/devgate.py` |
| `Observer`, `Match.observers`, the God view | `backend/models.py` |
| `add_observer`, `god_set_leader`, the host guard | `backend/engine.py` |
| Routes and the socket's read-only rule | `backend/main.py` |
| The board and the watch mode | `frontend/app.js`, `frontend/god.css` |
| Tests | `tests/test_god_mode.py`, `tests/test_server.py`, `tests/test_app_shell.py` |

Related: [CONTRIBUTING.md](CONTRIBUTING.md) ·
[WEBSOCKET_PROTOCOL.md](WEBSOCKET_PROTOCOL.md) §2, §3, §5 ·
[ARCHITECTURE.md](ARCHITECTURE.md)
