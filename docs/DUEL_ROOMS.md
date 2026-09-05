# Link duels

Two people, one link, one duel, outside any match.

The four duels were the only games nobody could try. A solo board is a REST call
and a seed, but half of a duel is not knowing what the other person just did, so
`/explore` said outright that the duels were not there and could not be. The
only way to play one was to assemble two full teams, seat two Grandmasters,
field a mirrored Duelist on each side, and start a match.

A room is the small version.

## Getting in

Go to `/explore`, pick one of the four duels above the solo tabs, and you land
on `/play?duel=<room>&seat=<seat>` with a link to send someone. The duel starts
the moment they open it.

**The link you share carries the room and not your seat.** A seat id is the
socket's only credential, so pasting your own address bar to a friend would hand
them your chair. The box on the waiting screen holds the shareable form; copy
that one. Your own URL keeps your seat, which is how a refresh puts you back in
your own chair rather than making you a spectator of your own duel.

Anyone opening the link after both seats are taken watches instead. A watcher
sees the room and the result but neither hand until the reveal, which is the
same view a Grandmaster gets, through the same code.

## What a room is not

No teams, no Grandmasters, no currency, no levels, no perks, no roster, and
nothing recorded anywhere. When the duel ends you get the result and a rematch
button, and that is the whole of it.

**A room is deliberately not a `Match`.** It would ride the existing store,
locks, broadcast and eviction for free, but `match.status` is read in
thirty-four places in the engine, and a room pretending to be a match would give
every one of those guards a second meaning to get right forever. God mode made
the same call for the same reason: an `Observer` is kept out of `match.players`
so it cannot reach the match rules by accident.

**Seats have no names.** Every duel renderer already falls back to "You" and
"Opponent", so a link duel needs no name form, and nobody who followed a link is
shown a string a stranger typed.

## Two rules worth knowing

**A disconnect forfeits, it does not pause.** If someone closes their tab
mid-round, the clock keeps running and their missing choice loses that round.
Pausing would let whoever is losing freeze the duel by pulling the plug. The
person still there is told what happened. A rematch is the one exception: it
needs both people present, because it opens a round rather than continuing one.

**A link is good for thirty minutes of silence.** A room with nobody looking at
it is swept `DUEL_ROOM_TTL_SECONDS` after its last activity and its link stops
resolving. A tab left open keeps touching the room, so a room somebody is
sitting in lives on.

## BID WAR

BID WAR is the one staked duel, and a room has no purses. Both sides get the
same grant, `config.DUEL_ROOM_STAKE`. A match's stakes are unequal because a
Grandmaster chooses how much to back their champion and that choice is the game;
nobody makes that choice in a room, so an unequal grant would not be a decision,
only an unfair sale. Nothing is paid out at the end: `settlement()` is never
called, because there is nothing to pay it into.

## Where it lives

| Piece | File |
| --- | --- |
| The room, its rules and its store | `backend/duelroom.py` |
| The round loop, shared with the engine | `backend/duelloop.py` |
| Tunables | `config.DUEL_ROOM_*` |
| Routes and the room socket | `backend/main.py` (`/api/duels`, `/ws/duels/{room_id}`) |
| The room's message dialect | `backend/protocol.py` (`parse_room_message`) |
| The picker | `frontend/explore.html`, `frontend/explore.css` |
| The screen | `frontend/app.js` (`renderDuelRoom`), `frontend/index.html` (`#room-card`), `frontend/play.css` |
| Tests | `tests/test_duel_rooms.py`, `tests/test_duelloop.py`, `tests/test_server.py`, `tests/test_app_shell.py` |

A room borrows `#view-play` rather than declaring a view of its own. `#duel-card`
is an id and `duel.css` hangs forty selectors off it, so a second duel card in
the document would be invalid markup and `getElementById` would return the play
view's node anyway.

Related: [DUEL_MODULE_SPEC.md](DUEL_MODULE_SPEC.md) §10 ·
[WEBSOCKET_PROTOCOL.md](WEBSOCKET_PROTOCOL.md) · [GOD_MODE.md](GOD_MODE.md)
