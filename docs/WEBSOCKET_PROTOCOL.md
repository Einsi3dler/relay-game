# WebSocket & REST Protocol (v2)

The contract between the browser client and the server. Both the Frontend owner and
the Core owner build against this doc; keep it in sync with the code. All payloads
are JSON. All correctness and timing is **server-authoritative** — the client only
sends intents and renders snapshots.

Pair with [ARCHITECTURE.md](ARCHITECTURE.md) and [GAME_DESIGN.md](GAME_DESIGN.md).

---

## 1. REST endpoints (join flow only)

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| `GET` | `/` | — | landing page (`/play` serves the app) |
| `GET` | `/api/config` | — | `{ "teams": ["alpha","bravo"], "players_per_team": 12, "max_players_ceiling": 12, "min_players_default": 4, "min_level_count": 3, "max_level_count": 10, "team_name_max": 20, "level_count": 10, "wait_seconds": 180, "duel_round_seconds_min": 3, "duel_round_seconds_max": 30, "duel_round_seconds_choices": [3,5,8,10,12,15,20,30], "perks": { ... }, "roles": { "<role_id>": {"name": str, "games": [<game_id>, ...] \| null}, ... }, "library": [ {"id","name","role"}, ... ], "duels": [ {"id","name","choice_seconds"}, ... ] }` |
| `POST` | `/api/matches` | `{}` | `{ "match": <MatchPublic> }` — creates a match, returns its id |
| `POST` | `/api/matches/{id}/join` | `{ "name": str, "team_id": "alpha"\|"bravo"\|null }` | `{ "player": <PlayerPublic>, "match": <MatchPublic> }` |
| `POST` | `/api/matches/{id}/rejoin` | `{ "code": str }` | `{ "player": <PlayerPublic>, "match": <MatchPublic> }` — trades a rejoin code for the `player_id` that owns the seat |
| `GET` | `/api/matches/{id}` | — | `{ "match": <MatchPublic> }` (spectate / rejoin lookup) |
| `GET`/`POST` | `/god` | form `key=<RELAY_GOD_KEY>` | the God console, behind a password ([GOD_MODE.md](GOD_MODE.md)). Dev only |
| `POST` | `/god/new` | — | 303 to `/play?god=<observer_id>&match=<id>` — a new match with a God running it |
| `POST` | `/god/watch` | form `match_id=<id>` | 303 to the same, for a match already running |
| `POST` | `/api/duels` | `{ "duel_game_id": str }` | `{ "room_id", "seat_id", "duel_game_id" }` — a link duel room with you in seat "a" ([DUEL_ROOMS.md](DUEL_ROOMS.md)). 404 on an unknown duel |
| `POST` | `/api/duels/{room_id}/join` | — | `{ "room_id", "seat_id", "duel_game_id" }`; `seat_id` is **null** when both seats are taken, which is not an error — you watch |
| `GET` | `/api/duels/{room_id}` | — | `{ "room": <DuelRoomPublic> }` |

- `library` is the registered game catalogue that feeds the Grandmaster's
  assignment picker (each entry's `role` is its specialist role id, or `null`).
  `duels` is the **separate** duel catalogue — the server picks a Duelist's
  game, so these never appear in `library` and no picker offers them. Each
  entry's `choice_seconds` is that game's own round window, which the host's
  `duel_round_seconds_*` control overrides for the whole match.
  `roles` is the full role catalogue: `games` is the list a role may be
  assigned, or `null` for the Generalist (any game); an empty list marks a
  reserved role (`games: []`) that can't be assigned yet. `assign_game` is
  refused outright for a Duelist — the server picks that one.
- `team_id: null` (the normal client flow) → the player joins **unassigned** and
  picks a team in the lobby (or the host assigns one).
- The **first joiner becomes the match host**.
- Join fails with `400` if the chosen team is full (`max_players + 1`,
  the extra seat being the Grandmaster), the match is full, or the match has already
  started/finished. Body: `{ "detail": "<reason>" }`.
- Invite links: `/play?match={id}` routes the visitor straight to the join flow.
- After joining, the client opens the WebSocket (below) using the returned
  `player.id`.

## 2. WebSocket

Connect: `ws(s)://<host>/ws/matches/{match_id}?player_id={player_id}`

- Server rejects with close code `4404` if the match or player is unknown.
- On connect the server marks the player `connected`, sends a `state_snapshot` to
  the new socket, and broadcasts an updated snapshot to everyone. A player who
  reconnects while `solving` **or `bonus`** is served a **fresh** instance first
  (see [GAME_DESIGN.md](GAME_DESIGN.md) §9 — prevents replay-to-rewatch).
- **One socket per player.** A new connection with the same `player_id`
  **supersedes** the old one (old socket closed with `4001`).
- A player kicked by the host is closed with code `4403`; their `player_id` is
  dead from then on (`4404` on reconnect attempts). A player who sends `leave`
  is closed the same way — the client tracks that it asked, so it can say "you
  left" rather than "you were kicked".
- When the host sends `cancel_session`, every socket is closed with `4402` and
  the match is evicted: the code stops resolving, so nobody can rejoin a lobby
  that no longer exists.
- `player_id` is the socket's **only credential** — treat it like a session token.
- **God observers** (dev only, [GOD_MODE.md](GOD_MODE.md)) connect on this same
  endpoint and carry their `g_`-prefixed observer id in the same `player_id`
  slot; the ids are prefix-namespaced and the server asks which it is. Three
  differences: a God is never marked `connected`, receives **one** snapshot on
  connect and triggers **no** broadcast, and may send only `lobby_action`,
  `request_state` and `heartbeat` — anything else is answered with an `error`
  and changes nothing.
- **Rejoin codes.** Every player is also given a short, readable `rejoin_code`
  at join (`config.REJOIN_CODE_*`). It buys the `player_id` back over
  `POST /api/matches/{id}/rejoin`, which works at **any** point in a match —
  unlike `/join`, which is lobby-only. That is the whole point: a browser that
  lost its `player_id` (tab closed, storage cleared, different device) would
  otherwise be locked out of a seat that is still being held for it, and the
  frozen `roster_size` means the team could never advance again.
  - The endpoint **resolves an identity and mutates nothing**. The returned id
    is then used to connect normally, so `on_reconnect` stays the single path
    that touches match state.
  - It is not gated on the player being disconnected: a half-open socket the
    server has not noticed must not lock the real owner out, and the `4001`
    supersede rule above already handles the second socket.
  - A rejoin code is a **credential of the same class as `player_id`**, so it
    reaches exactly two views: your own `me` (`PlayerPrivate.rejoin_code`), and
    your own Grandmaster's roster rows, so they can read it back to you. It
    never appears in the lobby view, an opponent summary, or the finished-match
    view, all of which every player receives.

### 2.1 Client → Server messages

| `type` | Fields | Meaning |
| --- | --- | --- |
| `submit_answer` | `puzzle_id: str`, `answer: str` | Submit the current puzzle (level board while `solving`, bonus board while `bonus`). |
| `duel_choice` | `duel_id: str`, `round: int`, `choice: str` | A Duelist commits a move for the open round. Recorded server-side and **never broadcast** — the round resolves when both have committed or the window lapses. Rejected for anyone not seated in the duel, for a closed or stale round, for a second attempt at the same round, and for an illegal move. |
| `choose_wait` | — | Cleared player locks in "wait" (clears `choice_pending`; the wait timer keeps running). |
| `choose_bonus` | — | Cleared player takes the bonus: status → `bonus`, a harder instance of their game arrives, the running wait deadline becomes the bonus deadline. |
| `buy_perk` | `perk_id: str`, `target_id?: str` | Grandmaster-only, active match only. `target_id` is required for `extend_wait` (a cleared teammate); attack perks pick a random opponent server-side. |
| `give_leader` | `target_id: str` | Grandmaster-only. Lobby: moves the seat. Active match: full swap, once per team per level (see [GAME_DESIGN.md](GAME_DESIGN.md) §11). |
| `request_stake` | `amount: int` | Duelist-only, and only while a staked duel is being funded. Names the number they want out of the team purse. Moves no coins: the Grandmaster answers it. Capped at what the purse holds. |
| `answer_stake` | `amount: int` | Grandmaster-only, once per staked duel. Funds their own champion with **any** amount they choose, more or less than was asked; `0` is a legal answer meaning "bid with nothing". The coins leave the purse here and only winnings come back. Capped at what the purse holds. |
| `request_state` | — | Ask for a fresh `state_snapshot` (e.g. after reconnect). |
| `heartbeat` | — | Keep-alive; server replies with a `state_snapshot`. |
| `lobby_action` | `action: str` + action fields | Mostly lobby-only. `set_team {team_id}` (self), `leave` (self; the host seat passes on), `claim_leader` (seat empty or holder disconnected), `release_leader` (lobby-only; the Grandmaster gives the seat back), God-only `god_set_leader {target_id}` (lobby-only; unlike `claim_leader` it overrides a seated, connected Grandmaster), Grandmaster-only `assign_role {target_id, role_id}` and `assign_game {target_id, game_id}` (a game must fit the target's role); host-only: `move {target_id, team_id}`, `kick {target_id}`, `set_min_players {value}`, `set_max_players {value}` (1..ceiling; pulls `min_players` down with it), `set_level_count {value}` (3..10 rounds to win), `set_duel_seconds {value}` (3..30 seconds a duel round, or `0` to give every duel game its own window back), `set_team_name {team_id, name}`, `start`, `cancel_session`; `claim_host` (only while the host is gone). **Outside the lobby:** `end_session` (host-only, running match) and `claim_host` also work — the host holds the only control that stops a session. |

- `puzzle_id` **must** match the player's current puzzle id, or the server replies
  `error` ("Puzzle is no longer active") and ignores it.
- A **frozen** player's submits are rejected with `error` ("You are frozen")
  until their `frozen_until` deadline passes. `screen_effects` never blocks a
  submit — those perks are cosmetic and client-rendered.
- Submissions arriving faster than `SUBMIT_MIN_INTERVAL_MS` (config, default 300)
  per player → `error` ("Too fast.") and are ignored.
- Unknown `type` → `error` ("Unknown message type.").

### 2.2 Server → Client messages

The client can be correct using **only** `state_snapshot`. The other messages are
lightweight nudges for animations/toasts; never require them for correctness.

| `type` | Fields | When |
| --- | --- | --- |
| `state_snapshot` | `state: <MatchPublic>` | After every state change, on connect, and on `request_state`/`heartbeat`. **The source of truth.** A silenced Grandmaster's client should `request_state` when `silenced_until` lapses: the mask lives in the view layer, so no server timer fires to lift it. |
| `error` | `error: str` | The last client message was invalid. |
| `event` | `event: <Event>` | A log line to append. **`green`/`lost_green` events go to Grandmaster sockets only** (who cleared is Grandmaster-only knowledge); everything else is broadcast. |
| `level_advanced` | `team_id: str`, `level: int` | A team advanced — trigger a transition animation. |
| `perk_used` | `perk_id: str`, `by_team_id: str` | A perk fired — toast/flash material. |
| `duel_result` | `duel_id`, `winner_team_id`, `loser_team_id`, `winner_name`, `loser_name`, `wins`, `streak`, `currency`, `penalty_until` | A duel was decided. A nudge only: the snapshot already carries the outcome. Broadcast to everyone — both teams watched the same duel resolve. |
| `match_won` | `team_id: str` | A team won; match is over. |

> Minimal client: handle `state_snapshot` (render) and `error` (toast). Everything
> else is polish.

### Link duel rooms

Connect: `ws(s)://<host>/ws/duels/{room_id}?seat_id={seat_id}`

A room is not a match and speaks its own four-message dialect on its own socket:
`duel_choice`, `rematch`, `request_state` and `heartbeat`. Everything else is
answered with an `error`. **`rematch` is deliberately absent from the match's
`CLIENT_TYPES`**, so it is "Unknown message type." on `/ws/matches/` rather than
something a match socket quietly accepts.

- Seat ids are `s_`-prefixed and are the socket's only credential, exactly like
  a `player_id`. An omitted or `w_`-prefixed id is a **watcher**: they receive
  snapshots, see neither hand before the reveal, and cannot choose or rematch.
  An `s_` id belonging to some other room is closed `4404`.
- One socket per seat; a second supersedes the first with `4001`.
- The duel opens when **both seats have a live socket**, not when the second
  person claimed one — a five-second round would otherwise be half gone before
  their socket finished opening.
- A disconnect marks the seat away and nothing else. The round keeps running and
  the missing choice loses it; see [DUEL_ROOMS.md](DUEL_ROOMS.md) for why.
- The server sends `duel_room_state`, never `state_snapshot`: a room is not a
  `MatchPublic`.

```jsonc
// DuelRoomPublic
{
  "id": "3f81a2bc",                      // the share link
  "duel_game_id": "rps_duel",
  "status": "waiting" | "duelling" | "done",
  "you": "a" | "b" | null,               // null for a watcher
  "seats_taken": 2,
  "connected": { "a": true, "b": false },
  "duels_played": 1,                     // finished duels; a rematch is next
  "duel": <DuelView> | null              // THE SAME shape §3 documents below,
                                         //   built by the same code
}
```

## 3. Public state shapes

These are exactly what `.public()` returns. **No answers ever appear here.**

### MatchPublic

```jsonc
{
  "id": "a1b2c3d4",
  "status": "lobby | active | finished",
  "host_player_id": "p_9f3c2e7b81aa04d6",  // lobby controller (first joiner)
  "min_players": 4,                      // host-set start threshold per team
  "duel_round_seconds": null,            // host override; null = each duel game's own
  "winner_team_id": null,               // or "alpha" / "bravo" when finished
  "config": {                            // frozen at match start
    "wait_seconds": 180,
    "level_count": 10,
    "players_per_team": 4,
    "currency_per_clear": 1,
    "currency_bonus_first": 3,
    "currency_bonus_repeat": 1,
    "bonus_level_offset": 3,
    "perks": { "freeze": {"name","kind","cost","seconds"}, /* ... */ }
  },
  "teams": { "alpha": <TeamView>, "bravo": <TeamView> },  // shape depends on the viewer!
  "unassigned": [ <PlayerPublic>, ... ], // lobby players without a team yet
  "events": [ <Event>, ... ],           // last ~30, filtered per viewer (§2.2)
  "duel": <DuelView> | null,             // only for the two Duelists and the two Grandmasters
  "pending_stake": <PendingStakeView> | null,  // same four seats; a staked duel
                                         //   being funded. Never set at the
                                         //   same time as `duel`.
  "me": <PlayerPrivate> | null,          // only present for the requesting player
  "god": { "id": "g_...", "name": "God" } | null
                                         // the dev-only God seat (backend/god.py).
                                         //   Null on every other snapshot, and
                                         //   always present rather than appearing
                                         //   only on a God's own — a key that comes
                                         //   and goes is what §5 exists to catch,
                                         //   and null says nothing about whether
                                         //   anyone is watching. A God's `me` is
                                         //   null: they hold no seat.
}
```

### TeamView — visibility is Grandmaster-exclusive

Snapshots are personalised. Which team shape a viewer gets:

| Viewer | Own team | Opponent team |
| --- | --- | --- |
| anyone, while `status == "lobby"` | **TeamFull** | **TeamFull** (the assignment UI needs rosters) |
| the team's **Grandmaster**, active match | **TeamFull** | **TeamSummary** (with `green_count`) |
| a **playing member**, active match | **TeamSummary** (no `green_count`) | `{ "id", "name", "finished" }` only |
| no viewer (plain REST `GET`) | **TeamSummary** | **TeamSummary** |
| a **God observer** (dev only, any status) | **TeamFull**, unsilenced, with rejoin codes | **TeamFull**, same |

A God has no team, so neither side is "the opposition" — the row above is the
one view that is not personalised to a seat. It sees through Silence (an attack
on a Grandmaster's own read-out, not on someone watching from outside it),
keeps the leader-only events, and gets both sides of a `pending_stake`. See
[GOD_MODE.md](GOD_MODE.md).

```jsonc
// TeamFull
{
  "id": "alpha", "name": "Alpha",
  "level": 2,                            // 1..level_count, independent per team
  "roster_size": 4,                      // PLAYING members (Grandmaster excluded)
  "finished": false,
  "green_count": 3,                      // cleared players right now; NULL while
                                         //   silenced (see silenced_until)
  "currency": 5,                         // the team pool the Grandmaster spends
  "shield_active": false,
  "reflect_active": false,               // bounces the next attack at its buyer
  "insurance_active": false,             // next failed bonus keeps its earnings
  "silenced_until": null,                // UTC ISO; while live, THIS team's own
                                         //   Grandmaster loses green_count, every
                                         //   playing member's status/green (they
                                         //   read "hidden"/null), and the
                                         //   green/lost_green event feed
  "leader_id": "p_...",
  "players": [ <PlayerPublic + board_deadline>, ... ]
                                         // each roster entry adds
                                         //   "board_deadline": UTC ISO | null —
                                         //   set only on a `hidden_deadline` board,
                                         //   the deadline is withheld from the
                                         //   player and sent to this seat instead.
                                         //   Null everywhere else (the player has
                                         //   it), and nulled under Silence with the
                                         //   rest of the roster
}

// TeamSummary
{ "id": "alpha", "name": "Alpha", "level": 2, "roster_size": 4,
  "finished": false, "green_count": 3 /* Grandmasters' opponent view only */ }
```

### PlayerPublic

```jsonc
{
  "id": "p_9f3c2e7b81aa04d6",           // long + unguessable — it's the credential (§2)
  "name": "Ada",
  "team_id": "alpha",                    // null while unassigned in the lobby
  "status": "lobby | solving | cleared | bonus | leading | finished",
  "green": true,                         // derived: status == "cleared"
  "connected": true,
  "is_leader": false,
  "role": "logician",                    // config.ROLES id, or null (unset / Grandmaster)
  "assigned_game": "rewire"              // null for Grandmasters / unassigned
}
```

### PlayerPrivate (only in `me` — adds the puzzle you're allowed to see)

```jsonc
{
  // ...all PlayerPublic fields, plus:
  "current_puzzle": <PuzzlePublic> | null,   // level board while solving,
                                             // bonus board while bonus, else null
  "timer_kind": "wait | null",
  "timer_deadline": "2026-07-02T12:03:00Z",  // UTC ISO; null if no active timer
  "puzzle_deadline": null,                   // UTC ISO while solving a board whose
                                             //   game caps itself (GAME_MODULE_SPEC
                                             //   §6 `time_limit_seconds`); null for
                                             //   every other game and every bonus
                                             //   board. Its own timer scope, so it
                                             //   runs alongside timer_deadline.
                                             //   Null on a `hidden_deadline` board: the
                                             //   deadline goes to the team's
                                             //   Grandmaster instead, as
                                             //   TeamPublic.players[].board_deadline
  "choice_pending": true,                    // cleared and still owes wait-or-bonus
  "frozen_until": null,                      // UTC ISO while frozen by a perk
  "screen_effects": { "wobble": "2026-07-02T12:00:12Z" }
                                             // cosmetic sabotage: effect id ->
                                             //   UTC ISO deadline. Private to the
                                             //   victim (never in PlayerPublic),
                                             //   and lapsed entries are dropped
                                             //   rather than sent as past dates
}
```

### PuzzlePublic

```jsonc
{
  "id": "9f8e7d6c5b4a",
  "game_id": "rewire",
  "kind": "main | holding",              // matches always serve "main"; "holding"
                                         //   exists only in practice mode
  "prompt": "Rotate the tiles so power reaches every sink.",
  "payload": { "rows": 4, "cols": 4, "tiles": [ /* ... */ ] },  // game state, never the
                                                                //   solution — see
                                                                //   GAME_MODULE_SPEC §6
  "deadline": null              // present only when the board has one: the same
                                //   instant as PlayerPrivate.puzzle_deadline,
                                //   repeated here because a renderer that draws a
                                //   clock of its own already looks in the puzzle
                                //   and takes no other argument
}
```

### PendingStakeView

A **staked duel** (BID WAR) waiting on both Grandmasters. Present only for the
two Duelists and the two Grandmasters, and only in the gap between the server
picking the duel game and the duel existing: the module cannot open the sale
without knowing what each seat can bid, so there is no `DuelView` yet.

Each viewer sees **their own side of the negotiation and no more**. An
opponent's purse is the one thing worth knowing before the first bid, so it
stays hidden until the duel opens and the module's own payload publishes it.

```jsonc
{
  "duel_game_id": "bid_war",
  "deadline": "2026-07-02T12:00:25Z",    // when the window lapses and the
                                         //   server stakes the default for
                                         //   whoever has not answered
  "side": "a",                           // the viewer's own side: their seat as
                                         //   a Duelist, or their team's as a
                                         //   Grandmaster. Null for neither.
  "duellists": { "a": "Ada", "b": "Bo" }, // names only, never ids
  "ask": 34,                             // what THIS side's Duelist asked for,
                                         //   null until they ask
  "granted": null,                       // what THIS side's Grandmaster gave,
                                         //   null until they answer
  "settled": false                       // whether this side is done
}
```

Rules the client can rely on:

- `ask` and `granted` are always **this viewer's side**. The opposing team's
  numbers never appear in this object at all.
- A Duelist may send `request_stake` until their own side is `settled`.
- A Grandmaster may send `answer_stake` exactly once, and `0` is a real answer.
- If the deadline passes, the server grants `config.DUEL_STAKE_DEFAULT` (capped
  by the purse, so possibly `0`) to whoever has not answered, and the duel
  opens. It never waits indefinitely on an absent Grandmaster.
- The server only deals a staked duel when **both** purses hold at least
  `config.DUEL_STAKE_MIN_PURSE`; otherwise it deals a free duel and this object
  never appears. See [DUEL_MODULE_SPEC.md](DUEL_MODULE_SPEC.md).

### DuelView

The live head-to-head, present only for the two Duelists and the two
Grandmasters (`null` for everyone else — an ordinary solver learns nothing about
the other team). The reveal rule is the point of the shape: **while the round is
open, a viewer's own choice is echoed back and nobody else's is there at all.**

```jsonc
{
  "id": "3f9c1a7d",
  "duel_game_id": "crown_duel",          // which duel game the *server* picked
  "name": "Crown Duel",
  "rules_version": 2,
  "phase": "choosing | reveal | done",
  "round": 3,                            // engine rounds, 1-based
  "round_seconds": 10,                   // the window THIS match runs, host
                                         //   override included — draw the
                                         //   countdown from this, not from
                                         //   payload.choice_seconds
  "deadline": "2026-07-02T12:00:10Z",    // end of the current phase
  "you": "a" | "b" | null,               // your seat; null for a Grandmaster
  "duellists": { "a": "Ada", "b": "Bo" },  // names only, never player ids
  "team_of": { "a": "alpha", "b": "bravo" },
  "wins": { "a": 0, "b": 0 },            // round wins the ENGINE counts; a game
                                         //   that scores itself publishes the
                                         //   real score in payload instead
  "locked": { "a": true, "b": true },    // *that* they chose, never what
  "choices": { "a": "assassin" },        // yours while the round is open;
                                         //   both once it has resolved
  "history": [ {"round": 1, "a": "king", "b": "peasant", "winner": "b"} ],
  "last_round": { /* the round that just resolved, during the reveal beat */ },
  "winner_side": null,                   // "a" | "b" once the duel is decided
  "payload": { /* per-game, built for YOU — see below */ }
}
```

`payload.kind` names the game and the rest is that game's own render data. What
every one of them carries: `choice_seconds` (the module's own default window)
and `wins_needed`. What each adds:

| Game | `payload` carries | Never in it |
| --- | --- | --- |
| **RPS DUEL** (no `kind`) | `moves`, `beats` | — |
| **CROWN DUEL** `crown_duel` | `phase` (`strategy`/`combat`), `game_round`, `crowns`, `sacrifice_used`, `can_sacrifice`, **your own** `hand`, `cards_left` (counts for both), `beats`, `transform_types`, `log`, `last` | the opponent's hand, or anything about what their Royal Sacrifice did |
| **NUMBER CLASH** `number_clash` | `points`, `numbers`, `used` (both sides — every one was revealed when its round resolved), your `available`, `log`, `last` | — |
| **BID WAR** `bid_war` | `staked` (what each side was granted), `coins` (what each still holds), `won` (coins taken, owed back to the team), `auction`, `prize`, `next_prize` (one lot ahead, `null` on the last), `max_bid` (yours), `overtime`, `log`, `last` | the rest of the lot schedule. Seeing all five would settle every bid before the sale opened |

A Grandmaster (`you: null`) gets no hand, no purse and no choices — there is
nothing for them to relay to their champion mid-round.

### Event

```jsonc
{ "message": "Ada cleared Level 2.", "kind": "green | lost_green | advance | win | join | perk | info", "created_at": "2026-07-02T12:00:00Z" }
```

## 4. Countdown rendering (client)

- The server sends `timer_deadline` (absolute UTC). The client computes
  `remaining = deadline - Date.now()` and animates a countdown locally.
- `puzzle_deadline` is drawn on the same bar: a solving player holds no wait
  timer, so it is free. The server kills the board a few seconds *after* the
  deadline it published (`config.PUZZLE_GRACE_SECONDS`), covering an answer
  already in flight — that grace is not the player's time and is not drawn.
- When `remaining` hits 0 the client shows "time's up" but **waits for the server**
  to apply the consequence (cleared status lost / bonus failed / a fresh board).
  The client must not itself change status. The same applies to `frozen_until`.
- Clock skew is cosmetic; correctness is always the server's.

## 5. Invariants (test these)

1. No message from the server ever contains a puzzle `answer`.
2. A `state_snapshot` fully determines the UI; dropping every other message type
   still yields a correct (if less animated) client.
3. A playing member's snapshot never reveals opponent progress or own-team
   per-player cleared states — that data appears only in Grandmaster snapshots,
   and in the dev-only God observer's.
4. `me.current_puzzle` is non-null exactly while `solving` or `bonus`.
5. `green_count == number of cleared players` wherever both appear.
6. `duel` and `pending_stake` are never both non-null: a staked duel is being
   funded or it is being fought, never both.
7. A `pending_stake` never carries the opposing team's `ask` or `granted`, and
   a player who is neither a Duelist nor a Grandmaster is sent `null` for it.
   The God observer is the one exception: it is sent both sides, under `asks`
   and `grants`, and no `ask`/`granted` of its own.
8. A rejoin code appears in exactly two places: your own `me.rejoin_code`, and
   the roster rows of your own Grandmaster's `TeamView` (and of a God's, which
   is the other seat that can read one back to a stranded player). Never in the
   lobby view, an opponent summary, or a finished match.
9. A link duel room carries no team, currency, level or perk in any of its
   states, and never pays a `settlement`. A room is not a match, and the moment
   it can be mistaken for one the engine's guards stop meaning one thing.
10. An observer never appears in a roster, in `unassigned`, in the join capacity
   count, in `host_player_id`, in any event, or in any other viewer's snapshot.
   Connecting and disconnecting one broadcasts nothing. Nobody at the table can
   tell a God is watching.

Related: [ARCHITECTURE.md](ARCHITECTURE.md) · [GAME_DESIGN.md](GAME_DESIGN.md) · [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md)
