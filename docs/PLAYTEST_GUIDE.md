# The Relay — Playtest Guide (V6 & V7)

This guide is the instrument for **V6** (full playtest) and **V7** (economy &
perk tuning) in [TASK_LIST.md](TASK_LIST.md). V6 needs real humans in real
browsers; V7 sets the timer/currency/perk numbers from what V6 shows. Run V6,
record what you see against the checklists here, then feed the results into the
V7 tuning pass.

Everything you might tune lives in [`backend/config.py`](../backend/config.py)
— the timer, currency, and perk values there are marked **provisional pending
this playtest**. Never hard-code a gameplay number anywhere else.

---

## 1. Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -e ".[test]"
./run.sh                       # uvicorn backend.main:app --reload
```

**Reaching the deepest bomb boards.** In normal play a withheld manual page
starts at level 8, and the dark fuse only on the bonus-only tiers (11–13),
which a team reaches by being at level 8+ and choosing **Bonus** over waiting.
That is a long way to play just to look at them.

To see both in the first few minutes, edit
[`backend/games/game11_bomb_defuse.py`](../backend/games/game11_bomb_defuse.py):

```python
WITHHOLD_FROM_LEVEL = 3      # a withheld page from level 3 (normally 8)
DARK_FUSE_FROM_LEVEL = 1     # every board dark from level 1 (normally 11)
```

**Do not set `WITHHOLD_FROM_LEVEL` below 3.** Levels 1 and 2 field a single
bay, so withholding one page there leaves the Defuser a board with nothing they
can read at all — a dead end the real curve guarantees against, because every
tier from 8 up fields three bays of distinct types. Level 3 is the first with
two bays, so one withheld page still leaves one readable.

Put both back before recording anything: they change the curve you are
measuring.

The server serves everything on one origin (default
`http://127.0.0.1:8000`). To play across machines, run
`./run.sh --host 0.0.0.0` and share your LAN address.

**Full match (the real V6 target):** 10 people — two teams of 4 players + 1
Grandmaster each. One person opens `/` and hosts a match; everyone else joins
with the match code (or the copied invite link). Each team claims a
Grandmaster, who assigns every teammate a **role** and then a **game** from
that role, and the host starts once every playing member has both.

**Reduced smoke (fewer people):** the host lowers *Minimum players per team* in
the lobby; each team still needs a Grandmaster plus that many players. This
exercises the whole loop with as few as 2 tabs per team. For a fast run,
temporarily set `LEVEL_COUNT = 2` in `backend/config.py` and revert before
committing.

**Where to watch for errors:** the uvicorn stdout (server tracebacks) and each
browser's dev-tools console (client errors). Keep both visible.

---

## 2. What to observe

### V6 — does a full match complete cleanly? (**AC: a match completes with no server errors**)

- [ ] Two full teams + Grandmasters play from level 1 to a win.
- [ ] **Zero** server tracebacks in the uvicorn log for the whole match.
- [ ] **Zero** uncaught errors in any browser console.
- [ ] Role/game assignment behaves: the game picker only offers the assigned
      role's games, Generalist offers all, the Duelist offers none (the server
      picks) and forces the other team to field one too, and start
      stays blocked until every playing member has a role *and* a game.
- [ ] Reconnect works: refresh a tab mid-match and the correct view returns
      (a solving player gets a fresh board; cleared/bonus resume their state).
- [ ] Grandmaster handoff (full swap) works once per level, and all 13 perks
      land as expected. Specifically check the ones with awkward edges:
      Reflect (the attack comes home to its buyer, and cannot bounce twice),
      Silence (the victim's own Grandmaster goes blind, and *recovers* on its
      own when the 30s lapse), Clock Burn (burning past the deadline lapses the
      wait immediately), and Insurance (spent only on a failure that costs
      something).
- [ ] Screen-effect perks (wobble/static/mirror/blackout) disrupt without
      breaking: clicks still land on the tile you can see — check MIRROR RUN's
      swipe and THREADLINE's buttons in particular — the countdown and currency
      stay readable, and the effect stops on its own at its deadline.
- [ ] With the OS "reduce motion" setting on, a screen effect still costs the
      victim something (the substitute blur pulse), rather than doing nothing.

### The two-seat bomb — does the second seat actually get used? (**AC: Defusers ask, Grandmasters answer, and neither seat is bored or stranded**)

BOMB DEFUSE is the only game played by two people, and the only one with a
clock and a fail state, so it is the thing most likely to behave differently
with real humans than it does in tests. Every team fields exactly one Defuser,
so every table has one.

- [ ] **Do they actually talk?** The whole design assumes the Defuser describes
      a bay and the Grandmaster reads back the rule. If Defusers just flip to
      their own manual and eat the fuse cost instead, the console is decoration
      — note whether the Grandmaster is ever asked at all, and at which levels.
- [ ] **Levels 1–7 (whole manual):** the Grandmaster should be a *speed
      advantage* here and nothing more. A Defuser working alone should be
      slower, never stuck.
- [ ] **Levels 8–10 (one page withheld):** the Defuser's copy is missing one
      page and the console holds the only copy in the match. Does that produce
      a conversation, or just frustration? Note whether a Defuser ever sat on a
      dead bay because their Grandmaster was busy with four other players.
- [ ] **Bonus tiers 11–13 (the dark fuse):** the timer cell reads `--` and the
      only countdown is on the console. Does the Grandmaster remember to call
      the time out? Watch for a Defuser who never learns how long they have.
- [ ] **Silence on a bomb team.** For its 30 seconds the console blanks and, on
      a dark-fuse board, neither seat has the clock. That is intended — check
      it reads as an attack landing rather than as the app breaking.
- [ ] **A disconnected Grandmaster** is the open-ended case: from level 8 their
      Defuser is genuinely stranded on one bay until they come back. Note how
      often it happens and how bad it felt.
- [ ] **Is the Grandmaster too busy?** They buy perks, watch the roster, and now
      run a manual and a countdown. If the bomb crowds out the rest of the
      role, that is a finding.

### V5 — do the difficulty curves feel right? (**AC: L1 ≈ today, L10 clearly but not brutally harder, bonus harder than the current level**)

For each game, note solve times per level band (1–3, 4–6, 7–10, bonus 11–13):

- [ ] Level 1 feels like the original difficulty (no regression).
- [ ] Level 10 is clearly harder but still **calm** — not frustrating or
      brutal (the design goal is a relaxed race, not a stress test).
- [ ] The bonus board (current level + `BONUS_LEVEL_OFFSET`) feels genuinely
      harder than the level the team is on — **including at levels 8–10**,
      where it comes from the bonus-only tiers 11–13.
- [ ] The bonus tiers aren't a cliff. DECANT, OVERPRINT, SHADOW CAST and
      STACKDROP are near their ceilings by level 10 and had the least room to
      climb; SHADOW CAST's tiers 12 and 13 are identical bar the time hint.
- [ ] Note any game whose curve spikes or flattens badly — that game's
      `MAIN_LEVEL_PARAMS` table (or `_params_for_level`) is where to adjust.
- [ ] **BOMB DEFUSE climbs on two extra axes**, so watch level 10 especially —
      it is the "clearly but not brutally harder" line and it now also carries
      a withheld manual page. If level 10 tips into brutal, raise
      `WITHHOLD_FROM_LEVEL` before touching the fuse table.

### V7 — is the economy worth tuning? (**AC: bonuses feel worth the risk; perks get bought but don't dominate**)

Track per team across the match:

- [ ] **Bonus vs. wait:** how often players take the bonus vs. hold their
      cleared status. If almost nobody risks the bonus, its reward
      (`CURRENCY_BONUS_FIRST`) or the wait length (`WAIT_SECONDS`) is off.
- [ ] **Bonus success rate:** roughly what fraction of bonus attempts succeed.
      Very high → bonus too easy/cheap to attempt; very low → not worth it.
- [ ] **Perks bought per team, and which:** are perks bought at all? Does one
      perk dominate every purchase? Freeze was the suspected dominant buy
      before the catalogue grew — Wobble and Static now also reach bonus
      players for less, so watch whether Freeze still wins. Note that Freeze
      and Scramble behave differently against a **timed** board (BOMB DEFUSE):
      Freeze pushes the board deadline out by as long as it locks the Defuser
      out, and Scramble takes their work but not their clock. Both were
      measurably wrong before that and are worth watching afresh. Does any perk never
      get bought? Reflect at 4 is the most expensive thing in the shop; Skim
      deliberately loses the buyer currency.
- [ ] **Wait timer:** does the 180s hold ever actually lapse, and does that
      feel fair when it does?

---

## 3. Recording results

Fill this in during/after the playtest and bring it to the V7 tuning discussion.

| Metric | Team Alpha | Team Bravo | Notes |
| --- | --- | --- | --- |
| Match completed? (no errors) | | | |
| Avg solve time, L1 / L5 / L10 | | | per game if it varies |
| Bonus attempts / waits | | | |
| Bonus success rate | | | |
| Perks bought (by type) | | | all 13 — note any never bought |
| Wait timer lapses | | | |
| Bomb: times the Grandmaster was asked | | | roughly, per level band |
| Bomb: boards lost to the fuse | | | and at which levels |
| Bomb: Defuser stranded on a withheld page? | | | L8+ only; note how long |
| Felt too easy / too hard where? | | | |

Proposed config changes from this data (V7):

- `WAIT_SECONDS`: keep 180 / change to ___ because ___
- `CURRENCY_BONUS_FIRST` / `_REPEAT`: keep 3 / 1 / change because ___
- Perk costs — attacks (freeze 3 / scramble 2 / clock_burn 3 / skim 2 /
  silence 3 / wobble 2 / static 2 / mirror 3 / blackout 3): ___
- Perk costs — defense (shield 2 / reflect 4 / insurance 2 / extend_wait 1): ___
- Screen-effect durations (wobble 12s / static 10s / mirror 10s / blackout 4s): ___
- Per-game curve tweaks: ___
- BOMB DEFUSE two-seat knobs — `WITHHOLD_FROM_LEVEL` (8) / `DARK_FUSE_FROM_LEVEL`
  (11) / `PUZZLE_GRACE_SECONDS` (5): ___

---

## 4. Filing a bug

When something breaks, capture:

1. **Game id** and whether it was a **main** or **bonus** board.
2. **Team level** at the time.
3. **What the board looked like** — a screenshot (seeds are server-side and not
   visible to the client, so the screenshot is the reproduction).
4. **Expected vs. actual** behaviour.
5. **Server log excerpt** (the uvicorn traceback, if any) and any **browser
   console** errors.
6. **Reproduction steps** — what the player did just before.

Open it as a GitHub issue (or note it in the playtest results) with the game
owner tagged for game-specific bugs, or Core for engine/economy bugs.
