# WebSocket & REST Protocol (MVP)

The contract between the browser client and the server. Both the Frontend owner and
the Core owner build against this doc; keep it in sync with the code. All payloads
are JSON. All correctness and timing is **server-authoritative** — the client only
sends intents and renders snapshots.

Pair with [ARCHITECTURE.md](ARCHITECTURE.md) and [GAME_DESIGN.md](GAME_DESIGN.md).

---

## 1. REST endpoints (join flow only)

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| `GET` | `/` | — | `index.html` |
| `GET` | `/api/config` | — | `{ "teams": ["alpha","bravo"], "rest_seconds": 15, "holding_seconds": 20, "players_per_team": 4, "stage_count": 4 }` |
| `POST` | `/api/matches` | `{}` | `{ "match": <MatchPublic> }` — creates a match, returns its id |
| `POST` | `/api/matches/{id}/join` | `{ "name": str, "team_id": "alpha"\|"bravo"\|null }` | `{ "player": <PlayerPublic>, "match": <MatchPublic> }` |
| `GET` | `/api/matches/{id}` | — | `{ "match": <MatchPublic> }` (spectate / rejoin lookup) |

- `team_id: null` → auto-balance to the emptier team (tie → `alpha`, so tests are
  deterministic).
- Join fails with `400` if the chosen team is full (`players_per_team` reached) or
  the match has already started/finished. Body: `{ "detail": "<reason>" }`.
- After joining, the client opens the WebSocket (below) using the returned
  `player.id`.

## 2. WebSocket

Connect: `ws(s)://<host>/ws/matches/{match_id}?player_id={player_id}`

- Server rejects with close code `4404` if the match or player is unknown.
- On connect the server marks the player `connected`, sends a `state_snapshot` to
  the new socket, and broadcasts an updated snapshot to everyone. If the player
  reconnects while `solving`, the server serves a **fresh** main puzzle instance
  first (see [GAME_DESIGN.md](GAME_DESIGN.md) §9 — prevents replay-to-rewatch).
- **One socket per player.** A new connection with the same `player_id`
  **supersedes** the old one: the server closes the previous socket (close code
  `4001`) and continues with the new. Two tabs never share a player.
- `player_id` is the socket's **only credential** — treat it like a session token.
  Ids must be long, random, and unguessable (≥ 12 hex chars from a CSPRNG); never
  sequential, never logged in chat/URLs players share.

### 2.1 Client → Server messages

| `type` | Fields | Meaning |
| --- | --- | --- |
| `submit_answer` | `puzzle_id: str`, `answer: str` | Submit the **main** puzzle answer. |
| `submit_holding` | `puzzle_id: str`, `answer: str` | Submit the **holding** puzzle answer. |
| `request_state` | — | Ask for a fresh `state_snapshot` (e.g. after reconnect). |
| `heartbeat` | — | Keep-alive; server replies with a `state_snapshot`. |

- `puzzle_id` **must** match the player's current puzzle id, or the server replies
  `error` ("Puzzle is no longer active") and ignores it. This prevents a stale
  client from answering a puzzle it has already lost.
- Submitting the wrong *kind* (e.g. `submit_holding` while `solving`) → `error`.
- Submissions arriving faster than `SUBMIT_MIN_INTERVAL_MS` (config, default 300)
  per player → `error` ("Too fast.") and are ignored — see
  [GAMES_SPEC.md](GAMES_SPEC.md) §0 rule 6 (anti brute-force).
- Unknown `type` → `error` ("Unknown message type.").

### 2.2 Server → Client messages

The client can be correct using **only** `state_snapshot`. The other messages are
lightweight nudges for animations/toasts; never require them for correctness.

| `type` | Fields | When |
| --- | --- | --- |
| `state_snapshot` | `state: <MatchPublic>` | After every state change, on connect, and on `request_state`/`heartbeat`. **The source of truth.** |
| `error` | `error: str` | The last client message was invalid. |
| `event` | `event: <Event>` | A log line to append (join, went green, lost green, advanced, won). |
| `stage_advanced` | `team_id: str`, `stage: int` | A team advanced — trigger a transition animation. |
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
  "winner_team_id": null,               // or "alpha" / "bravo" when finished
  "config": {                            // frozen at match start
    "rest_seconds": 15,
    "holding_seconds": 20,
    "players_per_team": 4,
    "stage_count": 4
  },
  "teams": {
    "alpha": <TeamPublic>,
    "bravo": <TeamPublic>
  },
  "events": [ <Event>, ... ],           // last ~30
  "me": <PlayerPrivate> | null           // only present for the requesting player
}
```

### TeamPublic

```jsonc
{
  "id": "alpha",
  "name": "Alpha",
  "stage": 2,                            // 1..stage_count, independent per team
  "roster_size": 4,
  "finished": false,
  "green_count": 3,                      // how many players are currently green
  "players": [ <PlayerPublic>, ... ]     // teammates, without private puzzle data
}
```

### PlayerPublic (visible to everyone)

```jsonc
{
  "id": "p_9f3c2e7b81aa04d6",           // long + unguessable — it's the credential (§2)
  "name": "Ada",
  "team_id": "alpha",
  "status": "solving | resting | holding | lobby | finished",
  "green": true,                         // derived: status in {resting, holding}
  "connected": true
}
```

### PlayerPrivate (only in `me` — adds the puzzle you're allowed to see)

```jsonc
{
  // ...all PlayerPublic fields, plus:
  "current_puzzle": <PuzzlePublic> | null,   // the puzzle to render right now
                                             // (main if solving, holding if holding)
  "timer_kind": "rest | holding | null",
  "timer_deadline": "2026-07-02T12:00:15Z"   // UTC ISO; null if no active timer
}
```

> `current_puzzle` is whichever puzzle the player should act on given their status:
> the main puzzle while `solving`, the holding puzzle while `holding`, and `null`
> while `resting`/`lobby`/`finished`. The client does not need to track which is
> which — it just renders `current_puzzle` and submits with the matching message
> type based on `current_puzzle.kind`.

### PuzzlePublic

```jsonc
{
  "id": "9f8e7d6c5b4a",
  "game_id": "rewire",
  "kind": "main | holding",
  "prompt": "Rotate the tiles so power reaches every sink.",
  "payload": { "rows": 4, "cols": 4, "tiles": [ /* ... */ ] }  // game state, never the
                                                               //   solution — see
                                                               //   GAME_MODULE_SPEC §6
}
```

### Event

```jsonc
{ "message": "Ada went green.", "kind": "green | lost_green | advance | win | join | info", "created_at": "2026-07-02T12:00:00Z" }
```

## 4. Countdown rendering (client)

- The server sends `timer_deadline` (absolute UTC). The client computes
  `remaining = deadline - Date.now()` and animates a countdown locally.
- When `remaining` hits 0 the client shows "time's up" but **waits for the server**
  to send the authoritative next `state_snapshot` (the server's timer applies the
  consequence). The client must not itself change status.
- Clock skew: if `remaining` is slightly off, that's cosmetic. Correctness is always
  the server's.

## 5. Invariants (test these)

1. No message from the server ever contains a puzzle `answer`.
2. A `state_snapshot` fully determines the UI; dropping every other message type
   still yields a correct (if less animated) client.
3. `green_count == number of players with green==true` in every `TeamPublic`.
4. `me.current_puzzle.kind` matches `me.status` (`solving→main`, `holding→holding`,
   otherwise `null`).

Related: [ARCHITECTURE.md](ARCHITECTURE.md) · [GAME_DESIGN.md](GAME_DESIGN.md) · [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md)
