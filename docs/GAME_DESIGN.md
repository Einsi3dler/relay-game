# The Relay — Game Design (v2)

This is the source of truth for **what the game does**. If code and this document
disagree, this document wins until it is updated by agreement. Keep it current.
(The approved redesign rationale lives in [REDESIGN_PLAN.md](REDESIGN_PLAN.md);
this document is the durable rulebook it produced.)

---

## 1. The pitch

Two teams race to clear **ten levels**. Each team has a non-playing **Grandmaster**
who assigns every teammate their own game, watches the board, and spends the
team's earned **currency** on attack and defense **perks**. The relay rule
still bites: a team only moves to the next level when **every playing member is
simultaneously "cleared"** — and a fast solver must choose between **holding
their cleared status on a timer** or **gambling it on a bonus round** for extra
currency. The **first team to clear level `LEVEL_COUNT` wins.**

It rewards a team that is *evenly* fast — and a Grandmaster who plays the meta well.

## 2. Match structure

| Concept | Value | Notes |
| --- | --- | --- |
| Teams per match | 2 (Alpha, Bravo) | Fixed. |
| Playing members per team | `PLAYERS_PER_TEAM` (default 4) | Plus **one Grandmaster** each — 5 people per team by default. |
| Levels per match | `LEVEL_COUNT` (default 10) | Whole team advances together. |
| Game per player | Chosen by the Grandmaster from the library | **No two teammates play the same game.** |
| Win condition | First team to clear level `LEVEL_COUNT` | See §6. |

- Every playing member plays **their own assigned game** for the whole match,
  level by level, on their **own puzzle instances** (nobody can copy answers).
- Each player has a **role** that gates which games they can be assigned (see
  §2a). Games get **harder each level** — every game scales its main board with
  the team's `level` (see [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md)).
- The **Grandmaster does not play**. They observe from a dashboard (see §10),
  bank currency, buy perks, and assign roles + games.

### 2a. Roles & the Grandmaster

The team's non-playing leader is themed as the **Grandmaster** (the internal
field is still `is_leader`). In the lobby the Grandmaster assigns every playing
teammate a **role**, and the game picker then only offers that role's games.
Roles are defined in `config.ROLES`:

| Role | Games it may be assigned |
| --- | --- |
| Logician | Sweep |
| Technocrat | Rewire, Lane Shift |
| Spatial Reasoner | Mirror Run, Shadow Cast |
| Puzzle Master | Decant, Stackdrop |
| Spymaster | Echo, Overprint |
| Generalist | any registered game |
| Lexicon | *reserved* — no matching game shipped yet, not assignable |

Rules:

- Only the Grandmaster assigns roles and games, and only in the lobby.
- A game must fit the target's role; **Generalist** fits everything. The
  existing "no two teammates play the same game" rule still holds.
- **Duplicate roles per team are allowed** — game uniqueness already prevents
  overlap, and two Generalists is a legitimate setup. (Two players locked to
  the *same* single-game role would leave one unassignable, which `start` will
  refuse until it's fixed — a visible, self-correcting lobby mistake.)
- **Lexicon** is reserved for a future word game; until one ships it has no
  games and cannot be assigned.
- On a Grandmaster handoff the role moves with the seat's game; switching teams
  or claiming the seat clears a player's role.

### Lobby / start (host + Grandmasters)

- The **first player to join a match is its host**. Players join **unassigned**
  and pick a team in the lobby — or the host assigns them.
- Each team's **Grandmaster seat is claimed in the lobby** ("claim Grandmaster"). The seat
  can be claimed while empty or while its holder is disconnected, and a Grandmaster
  can **hand the seat to a teammate** at any time (see §11).
- The **Grandmaster assigns one game per teammate** from the library; a game already
  taken by a teammate can't be assigned twice. Re-assigning a player frees
  their old game.
- The **host** still controls the lobby: move players, kick (`4403`), set the
  **minimum playing members per team** (1..`PLAYERS_PER_TEAM`), and **start**.
- Start is allowed only when every player has a team, **each team has a
  Grandmaster**, each team's playing count is within bounds, and **every playing
  member has an assigned game**.
- If the host disconnects, any player can **claim host** while they're gone.
- Sharing: the lobby exposes an invite URL (`/play?match=<id>`).

## 3. Player status model

Each player has exactly one status at any time. This is the heart of the engine.

| Status | Meaning | Player sees |
| --- | --- | --- |
| `lobby` | Joined, match not started. | Lobby screen. |
| `solving` | Working on their game's **level puzzle**. | Their game + controls. |
| `cleared` | Solved the level; holding cleared status on the **wait timer**. | "Cleared ✅" + countdown, plus the wait-or-bonus choice if still pending. |
| `bonus` | Gambling cleared status on a **bonus board**. | A harder instance of their game + the same countdown. |
| `leading` | The team's Grandmaster, observing. | The Grandmaster dashboard. |
| `finished` | Match over for this team. | Result screen. |

**"Green" = the player counts as ready for advancement.** A player is green
only while `cleared`. A `bonus` player is **not** green — taking the bonus
puts their readiness on the line until they solve it.

```
green(player) := player.status == "cleared"
```

Grandmasters are excluded from readiness entirely: they are never green, never
counted in `roster_size` or `green_count`, and never served puzzles.

## 4. The level loop

This is the exact lifecycle. Implement it precisely; it is covered by tests.

```
LEVEL N BEGINS (for a team)
  └─ every playing member: status = solving, a fresh instance of THEIR
     assigned game at level N. The Grandmaster stays `leading`.

WHILE the team has not advanced:

  ── Player solves their puzzle correctly ─────────────────────────────
     status: solving → cleared, choice_pending = true
     start WAIT timer (WAIT_SECONDS, default 180s)
     +CURRENCY_PER_CLEAR to the team — ONLY on the first clear of this
      level by this player (re-clears after a lapse pay nothing)
     >>> run ADVANCE CHECK for that team

  ── Player answers incorrectly ───────────────────────────────────────
     stays solving, but is assigned a FRESH instance (new seed).
     (Unlimited attempts, no other penalty; a fresh instance per attempt
      keeps retry fair for state-revealing games like ECHO.)

  ── Cleared player chooses WAIT ──────────────────────────────────────
     choice_pending = false; the wait timer keeps running.

  ── Cleared player chooses BONUS ─────────────────────────────────────
     status: cleared → bonus  (loses green!)
     bonus board = THEIR game at level N + BONUS_LEVEL_OFFSET (capped)
     the running wait deadline becomes the bonus deadline (no new timer)

  ── Bonus solved ─────────────────────────────────────────────────────
     +bonus pay to the team: CURRENCY_BONUS_FIRST for the first bonus
      this level, CURRENCY_BONUS_REPEAT for each one after (diminishing)
     status: bonus → cleared, choice_pending = true, FRESH wait timer
     (bonuses chain — wait or gamble again)
     >>> run ADVANCE CHECK

  ── Bonus failed (wrong answer OR deadline expiry) ───────────────────
     >>> FORFEIT all bonus currency earned this level (base pay stays;
         the team balance clamps at 0)
     status: bonus → solving, fresh instance (must re-clear)

  ── WAIT timer expires for a cleared player ──────────────────────────
     >>> player LOSES CLEARED STATUS
     status: cleared → solving, fresh instance (must re-clear;
         currency already earned is kept, nothing new is paid)

ADVANCE CHECK (for a team):
  if ALL playing members are green (cleared):
     cancel the team's timers
     if N == LEVEL_COUNT (last level):
        team wins → everyone incl. the Grandmaster status = finished; MATCH ENDS.
     else:
        team advances → LEVEL N+1 BEGINS (bonus streak/forfeit counters reset).
```

### Key rules to get right

1. **Advancement is checked the instant a player becomes cleared** (including
   bonus success), not only when a timer fires.
2. **The wait timer starts at the moment of clearing**, not when the player
   makes their wait-or-bonus choice — stalling the choice can't hold green.
3. **A bonus player blocks the team** exactly like a solving player: they must
   finish the bonus (or fail back to solving and re-clear) before the team can
   advance past them.
4. **Losing cleared status** (wait expiry, bonus failure) sends you back to a
   **fresh instance of your own game** at the current level.
5. Each team advances **independently** — no shared level clock.
6. All state is **server-authoritative.** Timers, correctness, currency, and
   perk effects live on the server. The client only displays and submits.

## 5. Timers

| Timer | Config key | Default | Behaviour |
| --- | --- | --- | --- |
| Wait / bonus deadline | `WAIT_SECONDS` | 180 | Holds cleared status; doubles as the bonus deadline. Expiry = lose cleared / fail the bonus. |
| Freeze (perk) | `PERKS["freeze"]["seconds"]` | 10 | Not a scheduled timer — a `frozen_until` deadline checked lazily on submit. |

- Timers are **server-authoritative**: the server stores an absolute
  **`deadline`** (UTC ISO) and sends it in the snapshot; the client renders the
  countdown. Expiry consequences are applied by the server.
- The wait deadline is the **only** scheduled timer per player. Any future
  second concurrent deadline must be lazy (like freeze) or the timer key must
  grow — see [ARCHITECTURE.md](ARCHITECTURE.md) §4.
- All durations are tunable via config; never hard-code them.

## 6. Winning and ending

- The **first team** to pass the last-level advance check **wins immediately**;
  the match transitions to `finished` and stops accepting input.
- Ties are impossible: one match is processed one message at a time.

## 7. The economy

| Event | Pay | Config key |
| --- | --- | --- |
| First clear of a level (per player) | +1 | `CURRENCY_PER_CLEAR` |
| First successful bonus of a level (per player) | +3 | `CURRENCY_BONUS_FIRST` |
| Each later bonus that level (per player) | +1 | `CURRENCY_BONUS_REPEAT` |
| Bonus failure / bonus deadline expiry | **forfeit that level's bonus pay** | — |

Currency is a **team pool** spent only by the Grandmaster. Anti-farming rules:
re-clearing a lapsed level pays nothing (first clear only), chained bonuses pay
diminishing returns, and a bonus failure claws back the level's bonus winnings
(clamped so the team balance never goes negative — yes, that means the Grandmaster
can spend loot the solver later forfeits).

### Perks (placeholder catalogue, `config.PERKS`)

| Perk | Kind | Effect |
| --- | --- | --- |
| Freeze | attack | A **random** opponent who is solving or in a bonus can't submit for ~10s (lazy `frozen_until` check). |
| Scramble | attack | A **random** solving opponent gets a forced fresh instance. |
| Shield | defense | Blocks the **next** incoming attack, then is consumed. One at a time. |
| Extend Wait | defense | +60s on a chosen cleared teammate's wait timer. |

Attack targets are picked by the **server at random** among valid opponents
(fog of war — the Grandmaster can't see opponent detail). A blocked attack still
costs the attacker; an attack with **no valid target is rejected and not
charged**. Purchases are Grandmaster-only, during an active match only.

## 8. Explicitly out of scope

Cut on purpose — **do not add these** without a design decision:

- More than 2 teams; persistence / database / accounts.
- Mid-match joining; reconnect "backlog" puzzles.

## 9. Edge cases and their rulings

| Situation | Ruling |
| --- | --- |
| A player disconnects mid-level | Status and timers are untouched — no grace period, no auto-kick. A cleared player's status decays via the normal wait-expiry rule. On reconnect: `cleared` resumes status and timer; a `solving` or `bonus` player is served a **fresh** instance (prevents replay-to-rewatch, esp. ECHO). |
| The Grandmaster disconnects mid-match | The team plays on but can't buy perks or receive a handoff until the Grandmaster returns. There is no mid-match Grandmaster *claim* — only the Grandmaster can give the seat away (§11). |
| Team is all cleared but one player's socket is dead | Advancement still fires (server-authoritative). |
| A frozen player's freeze lapses | Cleared lazily on their next submit; no timer fires. |
| Fewer players (local testing) | Lower `min_players`; the advance check uses the **frozen playing roster** from match start. |

## 10. The Grandmaster dashboard

Progress information is **Grandmaster-exclusive**. Playing members see only their
own game, status, and their team's current level — teams are expected to be on
a voice call, so human relaying is the design, not a gap.

The Grandmaster sees:

- Own team: every member's status (who is cleared / in bonus / solving), their
  assigned games, `green_count`, team level, currency, shield state.
- Opponent: **current level and cleared-count only** — never per-player detail.
- The perk shop, and the the Grandmaster-seat handoff control.
- The event feed, including the Grandmaster-only "X cleared / X lost cleared"
  events (players don't receive those).

## 11. Grandmaster handoff

The Grandmaster can give the seat to a teammate at any time:

- **In the lobby**: the flag moves; the new Grandmaster's game assignment is
  cleared; the old Grandmaster becomes assignable.
- **Mid-match** (**once per team per level**): a **full swap** —
  - the recipient stops playing: puzzles and timer cleared, any cleared status
    **lost** (handoff has a real cost);
  - the old Grandmaster takes over the recipient's assigned game with a **fresh,
    un-cleared instance** at the team's current level;
  - the recipient's economy counters (`earned_level`, bonus streak, forfeitable
    bonus pay) transfer to the old Grandmaster, so a level can't pay base currency
    twice.

---

Related: [REDESIGN_PLAN.md](REDESIGN_PLAN.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md) · [WEBSOCKET_PROTOCOL.md](WEBSOCKET_PROTOCOL.md)
