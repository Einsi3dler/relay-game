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
| Logician | Sweep, Threadline |
| Technocrat | Rewire, Lane Shift |
| Spatial Reasoner | Mirror Run, Shadow Cast |
| Puzzle Master | Decant, Stackdrop |
| Spymaster | Echo, Overprint |
| Generalist | any registered game |
| Defuser | *the role fixes it* — Bomb Defuse. **Every team must field one** (see §2c) |
| Duelist | *the server picks* — duels the other team's Duelist (see §2b) |

Rules:

- Only the Grandmaster assigns roles and games, and only in the lobby.
- A game must fit the target's role; **Generalist** fits everything. The
  existing "no two teammates play the same game" rule still holds.
- **Duplicate roles per team are allowed** — game uniqueness already prevents
  overlap, and two Generalists is a legitimate setup. (Two players locked to
  the *same* single-game role would leave one unassignable, which `start` will
  refuse until it's fixed — a visible, self-correcting lobby mistake.)
- **Duelist** and **Defuser** break several of the rules above on purpose — see
  §2b and §2c. Both carry a game the Grandmaster cannot choose, and neither may
  be doubled up on one team.
- On a Grandmaster handoff the role moves with the seat's game; switching teams
  or claiming the seat clears a player's role.

### 2b. The Duelist

A team may spend one of its four playing slots on a **Duelist** — a champion who
never solves a puzzle. Their only job is beating the other team's Duelist.

The role deliberately breaks the rules above:

- **The server picks their game, not the Grandmaster.** `assign_game` is refused
  for a Duelist. Duel games live in their own catalogue and never appear in the
  lobby picker.
- **It is mirrored.** If one team fields a Duelist, the other must too — a duel
  needs two seats. The match won't start otherwise.
- **At most one per team.**
- **They can't be handed the Grandmaster seat mid-match**, which would vacate a
  duel seat mid-fight.

**How a duel works.** A duel starts at kickoff and again every
`DUEL_INTERVAL_SECONDS` (30s) after the last one resolves. Both Duelists commit a
move inside a window set by the duel game (5s for RPS DUEL) *without seeing each
other's*; the round resolves when both have committed or the window lapses — a
Duelist who lets it lapse forfeits that round, so stalling never pays. Ties replay.
First to the game's win target (2 for RPS) takes the duel.

**What a duel is worth.**

- The **winner goes green** and stays green until the next duel — their team can
  advance while they hold it. There is no wait timer on a duel win, so `extend_wait`
  can't prolong one either.
- The **loser is not green**, which blocks their team by exactly the same mechanism
  a bonus-hunting player does. Nothing special is needed for this.
- The **winning team is paid** `DUEL_WIN_CURRENCY` (2), doubling on each consecutive
  win, capped at `DUEL_CURRENCY_CAP` (8). Any loss resets the streak.
- The **losing team is locked** out of advancing for `DUEL_PENALTY_SECONDS` (60) —
  **once per level**. Losing twice at the same level costs nothing extra beyond
  staying un-green, and the lock clears on level-up. It only bites when the team is
  otherwise ready to advance, and their wait timers keep running while it holds, so
  holding green through it is the real tax.

**Who sees a duel.** The two Duelists and the two Grandmasters, nobody else. This is
a deliberate, minimal exception to the leader-exclusive visibility rule (§9) — a
Duelist has to see who they're fighting. Ordinary solvers still learn nothing about
the other team, and the view carries names, never player ids. Neither Duelist —
nor either Grandmaster — receives the opponent's move until the round has resolved.

Building a new duel game: [DUEL_MODULE_SPEC.md](DUEL_MODULE_SPEC.md).

### 2c. The Defuser

Every team spends one of its four playing slots on a **Defuser**, who plays
**BOMB DEFUSE**. This is the one game no team opts out of.

Where the Duelist is *mirrored* (one team fielding a champion forces the other
to answer), the Defuser is **required** — both teams name one or the match does
not start:

- **The role names the game, not the Grandmaster.** `assign_game` is refused for
  a Defuser; giving someone the role assigns `bomb_defuse` with it. The
  Grandmaster chooses **who** defuses, never **what** they play.
- **Exactly one per team**, and the lobby refuses a second at the click that
  would create it.
- **Everything else is ordinary.** Unlike the Duelist, a Defuser solves, goes
  green, holds a wait timer and takes bonus boards like any other player. The
  level loop needs no special case for them.

A required role costs a slot, and that is the point: a team fielding both a
Defuser and a Duelist has two of its four seats spoken for and only two free
picks. At small table sizes the two collide outright, and the lobby says so
rather than looking like a deadlock.

**The Grandmaster is the Expert.** BOMB DEFUSE is a two-player co-op wearing one
seat: the bomb explains nothing, and the manual that explains it lives on the
**Grandmaster's dashboard**. The Defuser asks; the Grandmaster reads it out.
The Defuser keeps a copy of the manual too — flipping to it costs fuse, so a
Grandmaster on the console is a *speed advantage*, never a dependency. A
Grandmaster who is busy with four other players, or disconnected, slows their
Defuser down; they never strand them.

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
  Grandmaster**, each team's playing count is within bounds, **every playing
  member has an assigned game**, and the **Duelist mirror rule** holds — at most
  one Duelist per team, and either both teams field one or neither does (§2b).
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
| `duelling` | A **Duelist** who does not currently hold a duel win (§2b). | The duel card: the open round, the score, their move buttons. |
| `leading` | The team's Grandmaster, observing. | The Grandmaster dashboard. |
| `finished` | Match over for this team. | Result screen. |

**"Green" = the player counts as ready for advancement.** A player is green
only while `cleared`. A `bonus` player is **not** green — taking the bonus
puts their readiness on the line until they solve it. Nor is a `duelling`
player: a Duelist is green only while holding a duel win, so a lost duel blocks
their team through exactly the same mechanism (§2b).

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
     bonus board = THEIR game at level N + BONUS_LEVEL_OFFSET, capped at
     LEVEL_COUNT + BONUS_LEVEL_OFFSET (= 13). Levels 11..13 exist in every
     game's difficulty table as bonus-only tiers, so a team on the last
     level is still offered a board harder than the one they just cleared.
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
| Screen effects (perks) | `PERKS[...]["seconds"]` | 4–12 | Not scheduled either — deadlines in `Player.screen_effects`, rendered by the client and dropped from the view once past. |
| Silence (perk) | `PERKS["silence"]["seconds"]` | 30 | A `Team.silenced_until` deadline checked in the *view* layer. No timer fires when it lapses, so the blinded client asks for a fresh snapshot itself. |
| Duel round | the duel module's `choice_seconds` | 5 | The window both Duelists commit inside. Expiry resolves the round; a Duelist who didn't commit forfeits it. |
| Duel reveal | `DUEL_REVEAL_SECONDS` | 3 | The beat between rounds where both hands are shown. |
| Next duel | `DUEL_INTERVAL_SECONDS` | 30 | Gap from one duel resolving to the next starting. |
| Duel penalty | `DUEL_PENALTY_SECONDS` | 60 | Advance lock on a team that lost a duel — once per level. |

- Timers are **server-authoritative**: the server stores an absolute
  **`deadline`** (UTC ISO) and sends it in the snapshot; the client renders the
  countdown. Expiry consequences are applied by the server.
- The wait deadline is the **only** scheduled timer per *player*. The duel
  timers are match-level and run on their own scopes (`"duel"`, `"team:<id>"`),
  which is exactly why they can run concurrently with a player's wait timer.
  Any future deadline that must run alongside an existing one needs its own
  scope or must be lazy (like freeze) — see
  [ARCHITECTURE.md](ARCHITECTURE.md) §4.
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
| Winning a duel (per team) | +2, doubling per consecutive win, capped at 8 | `DUEL_WIN_CURRENCY`, `DUEL_CURRENCY_CAP` |
| Losing a duel | streak resets to 0 | — |

Currency is a **team pool** spent only by the Grandmaster. Anti-farming rules:
re-clearing a lapsed level pays nothing (first clear only), chained bonuses pay
diminishing returns, and a bonus failure claws back the level's bonus winnings
(clamped so the team balance never goes negative — yes, that means the Grandmaster
can spend loot the solver later forfeits).

### Perks (`config.PERKS`)

**Attacks — enforced by the server**

| Perk | Cost | Effect |
| --- | --- | --- |
| Freeze | 3 | A **random** opponent who is solving or in a bonus can't submit for ~10s (lazy `frozen_until` check). |
| Scramble | 2 | A **random** solving opponent gets a forced fresh instance. |
| Clock Burn | 3 | Burns 30s off a **random** cleared opponent's wait timer. Burning past *now* is legal — the wait lapses at once and they lose cleared status. |
| Skim | 2 | Steals 1 from the opponent's pool. Costs more than it takes on purpose: attrition and purchase-denial, never farming. |
| Silence | 3 | For 30s the victim team's **own Grandmaster** loses their roster read-out *and* the who-cleared event feed. The enemy leader still sees them — that's the joke. |

**Attacks — screen effects (cosmetic)**

| Perk | Cost | Effect |
| --- | --- | --- |
| Wobble | 2 | A **random** opponent's board and prompt wobble out of phase for 12s. |
| Static | 2 | Animated noise over a random opponent's board for 10s. |
| Mirror | 3 | Flips a random opponent's board horizontally for 10s (the prompt stays readable). |
| Blackout | 3 | Blacks a random opponent's board out entirely for 4s. |

Screen effects are **not enforceable** — the server stamps a deadline in
`Player.screen_effects` and the *client* renders it, so a determined player can
disable one in devtools. They are priced as annoyances, not counters. They never
touch a clock, which is what makes them the safe attack class for any future
game that runs its own timer. Each has a `prefers-reduced-motion` substitute, so
the victim's OS settings can't neutralise the buy.

**Defense**

| Perk | Cost | Effect |
| --- | --- | --- |
| Shield | 2 | Blocks the **next** incoming attack, then is consumed. One at a time. |
| Reflect | 4 | Bounces the next attack back at its buyer, then is consumed. Resolves **before** Shield, and a bounced attack ignores the buyer's own Shield and Reflect — that rule is what stops two Reflects ping-ponging forever. |
| Insurance | 2 | The next failed bonus keeps its earnings instead of forfeiting them. Only spent on a failure that would actually have cost something. |
| Extend Wait | 1 | +60s on a chosen cleared teammate's wait timer. |

The one-at-a-time defenses hold until something consumes them — they do not
lapse at a level boundary.

Attack targets are picked by the **server at random** among valid opponents
(fog of war — the Grandmaster can't see opponent detail, and never learns who
was hit). A Duelist is never a valid target: they sit in `duelling`, which is in
no attack's target statuses. A blocked attack still costs the attacker; an
attack with **no valid target is rejected and not charged**, and a rejected buy
consumes nothing — not the opponent's Shield, not their Reflect, not a coin.
Purchases are Grandmaster-only, during an active match only.

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
| A screen effect lapses | Nothing fires. The deadline simply stops being sent, and the client drops the class. Reconnecting mid-effect resumes with the time left, because the server sends a deadline rather than a duration. |
| The same attack is bought twice on one victim | Deadlines stack **forward** — the later expiry wins, so a second buy can never cut the first one short. |
| An attack is bought and no opponent is a valid target | Rejected, not wasted: no charge, and nothing on the defending team is consumed. A Duelist is never a valid target. |
| An attack hits a team holding both Shield and Reflect | Reflect resolves first and the Shield is left standing. The bounced attack lands on the buyer and ignores the buyer's own Shield and Reflect, so it can never bounce a second time. |
| A bonus fails while the team holds Insurance | The earnings are kept and the Insurance is consumed — but only if there were earnings to lose. A failure that would have cost nothing leaves it held. |
| A silenced Grandmaster's Silence lapses | No server timer fires (the mask lives in the view layer), so the client requests a fresh snapshot at the deadline. |
| Fewer players (local testing) | Lower `min_players`; the advance check uses the **frozen playing roster** from match start. |

## 10. The Grandmaster dashboard

Progress information is **Grandmaster-exclusive**. Playing members see only their
own game, status, and their team's current level — teams are expected to be on
a voice call, so human relaying is the design, not a gap.

The Grandmaster sees:

- Own team: every member's status (who is cleared / in bonus / solving), their
  assigned games, `green_count`, team level, currency, and the active defenses
  (shield / reflect / insurance) — **unless the team is Silenced**, which nulls
  the per-member status, the `green_count` and the who-cleared event feed for
  the length of the perk.
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
