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
| `GET` | `/api/config` | — | `{ "teams": ["alpha","bravo"], "players_per_team": 4, "level_count": 10, "wait_seconds": 180, "perks": { ... }, "roles": { ... }, "library": [ {"id","name","role"}, ... ] }` |
| `POST` | `/api/matches` | `{}` | `{ "match": <MatchPublic> }` — creates a match, returns its id |
| `POST` | `/api/matches/{id}/join` | `{ "name": str, "team_id": "alpha"\|"bravo"\|null }` | `{ "player": <PlayerPublic>, "match": <MatchPublic> }` |
| `GET` | `/api/matches/{id}` | — | `{ "match": <MatchPublic> }` (spectate / rejoin lookup) |

- `library` is the registered game catalogue that feeds the leader's assignment
  picker; `roles` is the placeholder role grouping.
- `team_id: null` (the normal client flow) → the player joins **unassigned** and
  picks a team in the lobby (or the host assigns one).
- The **first joiner becomes the match host**.
- Join fails with `400` if the chosen team is full (`players_per_team + 1`,
  the extra seat being the leader), the match is full, or the match has already
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
  dead from then on (`4404` on reconnect attempts).
- `player_id` is the socket's **only credential** — treat it like a session token.

### 2.1 Client → Server messages

| `type` | Fields | Meaning |
| --- | --- | --- |
| `submit_answer` | `puzzle_id: str`, `answer: str` | Submit the current puzzle (level board while `solving`, bonus board while `bonus`). |
| `choose_wait` | — | Cleared player locks in "wait" (clears `choice_pending`; the wait timer keeps running). |
| `choose_bonus` | — | Cleared player takes the bonus: status → `bonus`, a harder instance of their game arrives, the running wait deadline becomes the bonus deadline. |
| `buy_perk` | `perk_id: str`, `target_id?: str` | Leader-only, active match only. `target_id` is required for `extend_wait` (a cleared teammate); attack perks pick a random opponent server-side. |
| `give_leader` | `target_id: str` | Leader-only. Lobby: moves the seat. Active match: full swap, once per team per level (see [GAME_DESIGN.md](GAME_DESIGN.md) §11). |
| `request_state` | — | Ask for a fresh `state_snapshot` (e.g. after reconnect). |
| `heartbeat` | — | Keep-alive; server replies with a `state_snapshot`. |
| `lobby_action` | `action: str` + action fields | Lobby-only. `set_team {team_id}` (self), `claim_leader` (seat empty or holder disconnected), leader-only `assign_game {target_id, game_id}`; host-only: `move {target_id, team_id}`, `kick {target_id}`, `set_min_players {value}`, `start`; `claim_host` (only while the host is gone). |

- `puzzle_id` **must** match the player's current puzzle id, or the server replies
  `error` ("Puzzle is no longer active") and ignores it.
- A **frozen** player's submits are rejected with `error` ("You are frozen")
  until their `frozen_until` deadline passes.
- Submissions arriving faster than `SUBMIT_MIN_INTERVAL_MS` (config, default 300)
  per player → `error` ("Too fast.") and are ignored.
- Unknown `type` → `error` ("Unknown message type.").

### 2.2 Server → Client messages

The client can be correct using **only** `state_snapshot`. The other messages are
lightweight nudges for animations/toasts; never require them for correctness.

| `type` | Fields | When |
| --- | --- | --- |
| `state_snapshot` | `state: <MatchPublic>` | After every state change, on connect, and on `request_state`/`heartbeat`. **The source of truth.** |
| `error` | `error: str` | The last client message was invalid. |
| `event` | `event: <Event>` | A log line to append. **`green`/`lost_green` events go to leader sockets only** (who cleared is leader-only knowledge); everything else is broadcast. |
| `level_advanced` | `team_id: str`, `level: int` | A team advanced — trigger a transition animation. |
| `perk_used` | `perk_id: str`, `by_team_id: str` | A perk fired — toast/flash material. |
| `match_won` | `team_id: str` | A team won; match is over. |

> Minimal client: handle `state_snapshot` (render) and `error` (toast). Everything
> else is polish.

## 3. Public state shapes

These are exactly what `.public()` returns. **No answers ever appear here.**

### MatchPublic

```jsonc
{
  "id": "a1b2c3d4",
  "status": "lobby | active | finished",
  "host_player_id": "p_9f3c2e7b81aa04d6",  // lobby controller (first joiner)
  "min_players": 4,                      // host-set start threshold per team
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
  "me": <PlayerPrivate> | null           // only present for the requesting player
}
```

### TeamView — visibility is leader-exclusive

Snapshots are personalised. Which team shape a viewer gets:

| Viewer | Own team | Opponent team |
| --- | --- | --- |
| anyone, while `status == "lobby"` | **TeamFull** | **TeamFull** (the assignment UI needs rosters) |
| the team's **leader**, active match | **TeamFull** | **TeamSummary** (with `green_count`) |
| a **playing member**, active match | **TeamSummary** (no `green_count`) | `{ "id", "name", "finished" }` only |
| no viewer (plain REST `GET`) | **TeamSummary** | **TeamSummary** |

```jsonc
// TeamFull
{
  "id": "alpha", "name": "Alpha",
  "level": 2,                            // 1..level_count, independent per team
  "roster_size": 4,                      // PLAYING members (leader excluded)
  "finished": false,
  "green_count": 3,                      // cleared players right now
  "currency": 5,                         // the team pool the leader spends
  "shield_active": false,
  "leader_id": "p_...",
  "players": [ <PlayerPublic>, ... ]
}

// TeamSummary
{ "id": "alpha", "name": "Alpha", "level": 2, "roster_size": 4,
  "finished": false, "green_count": 3 /* leaders' opponent view only */ }
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
  "assigned_game": "rewire"              // null for leaders / unassigned
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
  "choice_pending": true,                    // cleared and still owes wait-or-bonus
  "frozen_until": null                       // UTC ISO while frozen by a perk
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
  "payload": { "rows": 4, "cols": 4, "tiles": [ /* ... */ ] }  // game state, never the
                                                               //   solution — see
                                                               //   GAME_MODULE_SPEC §6
}
```

### Event

```jsonc
{ "message": "Ada cleared Level 2.", "kind": "green | lost_green | advance | win | join | perk | info", "created_at": "2026-07-02T12:00:00Z" }
```

## 4. Countdown rendering (client)

- The server sends `timer_deadline` (absolute UTC). The client computes
  `remaining = deadline - Date.now()` and animates a countdown locally.
- When `remaining` hits 0 the client shows "time's up" but **waits for the server**
  to apply the consequence (cleared status lost / bonus failed). The client must
  not itself change status. The same applies to `frozen_until`.
- Clock skew is cosmetic; correctness is always the server's.

## 5. Invariants (test these)

1. No message from the server ever contains a puzzle `answer`.
2. A `state_snapshot` fully determines the UI; dropping every other message type
   still yields a correct (if less animated) client.
3. A playing member's snapshot never reveals opponent progress or own-team
   per-player cleared states — that data appears only in leader snapshots.
4. `me.current_puzzle` is non-null exactly while `solving` or `bonus`.
5. `green_count == number of cleared players` wherever both appear.

Related: [ARCHITECTURE.md](ARCHITECTURE.md) · [GAME_DESIGN.md](GAME_DESIGN.md) · [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md)
