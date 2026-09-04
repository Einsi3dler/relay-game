# DUEL_MODULE_SPEC — building a game for the Duelist role

The sibling of [GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md). Read that one first:
everything it says about determinism, statelessness and never trusting the client
still applies. This document covers only what is *different* about a duel.

---

## 1. What a duel is

A normal Relay game is a **puzzle owned by one player**, generated and then checked
in isolation. A duel is a **live head-to-head between the two teams' Duelists** —
one object, two players, resolved by the engine round by round.

That difference is why duels have their own interface (`DuelModule`, in
`backend/games/duel_base.py`) and their own registry list (`REGISTERED_DUELS`).
They are not `GameModule`s and they never appear in `GameRegistry.library()`,
because **the Grandmaster does not pick a Duelist's game — the server does.**

## 2. The Duelist role

`config.ROLES["duelist"]` carries `"duel": True`, which makes it behave unlike
every other role:

| | Ordinary role | Duelist |
|---|---|---|
| Who picks the game | the Grandmaster | **the server** (`registry.pick_duel`) |
| Needs an opposite number | no | **yes** — mirrored, or the match can't start |
| Per team | any number | **at most one** |
| What they do | solve puzzles | **only duel** — no puzzle, no bonus |
| How they go green | clear their puzzle | **win the current duel** |

The mirror rule and the one-per-team rule live in `RelayEngine.start_blocker`,
and are mirrored client-side in `startBlocker()` in `frontend/app.js`.

## 3. The interface

```python
DUEL_RULES_VERSION = 2
SIDES = ("a", "b")          # seats, assigned in config.TEAM_IDS order

@dataclass
class DuelState:
    duel_game_id: str
    round_index: int                  # 1-based
    wins: dict[str, int]              # side -> round wins
    choices: dict[str, str]           # side -> choice. SERVER ONLY until reveal.
    history: list[dict]               # resolved rounds; safe to show anyone
    payload: dict                     # render hints — sent to everyone verbatim
    private: dict                     # SERVER ONLY working state. See §4.1.

class DuelModule(Protocol):
    id: str
    name: str
    choice_seconds: int   # the per-round choice window — your game's time cost
    wins_needed: int      # round wins that take the duel

    def new_duel(self, seed: int) -> DuelState: ...
    def normalize_choice(self, state, choice, side=None) -> str | None: ...
    def resolve_round(self, state) -> str | None: ...   # "a" | "b" | None (tie)
    def public(self, state, side: str | None, revealed: bool) -> dict: ...
    def reset(self) -> None: ...
```

### Method rules

- **`new_duel(seed)`** — deterministic in `seed`. Return a fresh `DuelState`; never
  carry state on the module, which is a long-lived singleton shared by every match.
- **`normalize_choice(state, choice, side)`** — validate *and* canonicalise in one
  call. Return the canonical move, or `None` if it is illegal. Doing both here is
  what guarantees `DuelState.choices` only ever holds canonical values, so
  `resolve_round` never re-parses client text. Cap the raw input length
  (`MAX_CHOICE_CHARS`) **before** any further work, and never raise — a hostile
  move is simply not legal. `side` is the seat submitting: a duel whose legal
  moves depend on who is asking (a card only in *your* hand, a bid only *you* can
  afford) must check it rather than take the client's word. RPS ignores it.
- **`resolve_round(state)`** — `"a"`, `"b"`, or `None` for a tie (the engine
  replays the round). **A missing choice must lose**, and both missing must tie, or
  stalling becomes a strategy. It is called exactly once per round, which also
  makes it the one place a duel that carries state between rounds may advance it
  — spend the cards, pay the coins, move to the next auction.
- **`public(state, side, revealed)`** — delegate to `duel_base.base_public`. See §4.

### Time consequences are yours

`choice_seconds` and `wins_needed` belong to the module, not the engine. This is
the sanctioned way for duel games to differ: same rules, different time cost. The
engine reads them when it schedules the round timer. Everything else — the gap
between duels, the penalty length, the payout — is engine config
(`DUEL_*` in `backend/config.py`) and is the same for every duel game.

**The host can override your window.** `host_set_duel_seconds` sets one round
window for the whole match, across every duel game, so a group can run duels at
the pace they want; it is frozen into `config_snapshot` at kickoff, because a
window that moved mid-duel would change the clock under a Duelist who is already
choosing. Your `choice_seconds` is the default when they set nothing, so it still
has to be a sane pace for your game. Do not read it back for display: the
effective window reaches the client as `duel.round_seconds`, and the shell draws
the countdown from that (§7).

## 4. The reveal rule (the one that matters)

**A Duelist must never learn the opponent's move while the round is open.** This is
the whole game; leak it and there is nothing left.

It is enforced in exactly one place — `duel_base.base_public` — and your `public()`
should just call it:

```python
def public(self, state, side, revealed):
    return base_public(state, side, revealed)
```

`base_public` sends a viewer their **own** choice and everyone else's as a bare
`locked: bool`. A Grandmaster (`side=None`) sees *neither* choice, so they cannot
relay a move to their Duelist mid-round. Once `revealed` is true, both are public.

Do not add the raw `state.choices` to `payload`, and do not build a view by hand.

A canonical choice string must itself be safe to show both players, because one
path sends it without passing through `public()` at all: the engine stamps the
resolved round on `DuelSession.last_round`, which `models.DuelSession.public`
forwards verbatim. Crown Duel is the worked example — a Royal Sacrifice
canonicalises to the bare word `sacrifice`, and which cards it burned lives in
`private` instead.

### 4.1 `private` — state the client never sees

`payload` is sent to everyone verbatim; `private` is read by nothing outside your
module. Put a hand of cards, a coin balance or a shuffled prize order there, and
publish what you mean to by replacing `payload` in your own `public()`:

```python
def public(self, state, side, revealed):
    view = base_public(state, side, revealed)
    view["payload"] = self._payload(state, side)   # built per viewer
    return view
```

Building the payload per viewer is what lets a Duelist see their own hand while
the opponent sees only a count. A Grandmaster (`side=None`) gets neither.

### 4.2 Scoring the match yourself

`wins_needed` counts **round wins**, which not every duel is decided by. A game
scored on points (Bid War), or one where the third round settles a best-of-three
(Crown Duel), owns its own score: set `wins_needed = 1`, keep the score in
`private`, and return a side from `resolve_round` **only once that side has taken
the duel**. `None` then means "not decided yet", and the engine's tie path — replay
the round — is what carries the duel from round to round. `state.wins` stops being
a scoreline and becomes a flag, so publish the real score in your payload.

## 5. Lifecycle (what the engine does around you)

```
start_match ──> _start_duel        both Duelists -> "duelling" (not green)
                     │             phase="choosing", deadline = now + choice_seconds
                     ▼
        duel_choice(side, move)    recorded server-side, never broadcast
                     │             both locked OR the window lapses
                     ▼
              _resolve_round       your resolve_round() scores it
                ├── wins < needed ─> phase="reveal", +DUEL_REVEAL_SECONDS, next round
                └── wins == needed ─> _finish_duel
                                        winner -> "cleared" (green, no wait timer)
                                        loser  -> "duelling"
                                        winner team: streak++, pay 2/4/8 capped
                                        loser team: once-per-level advance lock
                                        next duel in DUEL_INTERVAL_SECONDS
```

Two consequences worth knowing:

- **A lost duel blocks the team for free.** `models.green()` is `status == "cleared"`,
  and `_team_all_cleared` requires every playing member green, so a Duelist who
  hasn't won sits in `"duelling"` and the team simply cannot advance. There is no
  separate gate.
- **A duel win holds no wait timer.** The next duel is what takes it away, not a
  lapsing clock — which also means the `extend_wait` perk cannot prolong one.

## 6. Registering a duel game

1. Class in `backend/games/duelN_<name>.py`, importing only from
   `backend.games.duel_base` and the stdlib.
2. Add an instance to `REGISTERED_DUELS` in `backend/registry.py`, and add the id to
   `config.ROLES["duelist"]["games"]`. These are the two sanctioned one-line
   cross-slice edits, same rule as for games.
3. A renderer at `frontend/duels/<id>.js` registering on `window.RelayDuels` (§7),
   plus a `<script>` tag in `frontend/index.html`.
4. Tests at `tests/games/test_duelN_<name>.py` (§8).

Do **not** add it to `REGISTERED_MODULES` — that would put it in the lobby game
picker, where a Grandmaster could assign it to a non-Duelist.

## 7. The renderer

```js
window.RelayDuels = window.RelayDuels || {};
window.RelayDuels["your_duel_id"] = {
  mount(container, duel, api) { /* build DOM */ },
  update(duel) { /* re-render from the new snapshot */ },
  unmount() { /* clean up */ },
};
```

The extra `update()` is the difference from a puzzle renderer: a duel is **one
object that changes phase under the same id** across many snapshots, where a puzzle
is replaced wholesale. Mount once, then update.

- `api.choose(move, duelId, round)` sends `duel_choice`. Never touch the socket.
- The **round clock is the shell's**, drawn for every duel game from
  `duel.round_seconds` and the server's deadline. Don't build a second one.
- Render the opponent's hand **from `duel.choices` alone**. Before the reveal it
  will not be there — show a lock, not a placeholder you could inspect.
- `duel.you` is your seat, or `null` for a Grandmaster: give them no buttons.
- The shell owns the card, the countdown bar and toasts; you own the duel area.
- Vanilla JS, no build step, no framework.

## 8. Tests your duel must ship with

In `tests/games/test_duelN_<name>.py`. Minimum bar:

1. **Determinism** — same seed, same `new_duel` payload.
2. **Every matchup** — sweep the whole move set, both seats.
3. **Symmetry** — swapping the seats swaps the winner. A module must not favour a
   side; it never even learns which team is which.
4. **Forfeit** — a missing choice loses; both missing ties.
5. **Illegal choices** — empty, oversized, wrong type, near-misses: all `None`, no
   exception.
6. **No leakage** — the opponent's move is absent from `public()` for both seats and
   for a Grandmaster while the round is open, and present once revealed. Sweep every
   matchup rather than sampling one. Scope the assertion to the played hands:
   `payload` names every legal move by design, so a naive scan of the whole view
   will trip on it. A duel with `private` state owes one more: the opponent's hand,
   purse or prize order must be absent from every served view, at every phase.
7. **Statelessness** — mutating one `DuelState` doesn't touch the next; the served
   view is a copy.
8. **`reset()`** — returns `None`, is idempotent, and leaves generation unchanged.

A renderer test (`tests/games/test_duelN_<name>_renderer.py`) running the shipped
`.js` in node against a fake DOM is expected too — see
`test_duel1_rps_renderer.py`, which asserts the reveal rule on the real DOM, that
a double click sends once, and that unmount leaves nothing behind.

## 9. The library

| Duel | Moves | Window | Target | Module |
|---|---|---|---|---|
| **RPS DUEL** — rock, paper, scissors | 3 | 5s | first to 2 | `backend/games/duel1_rps.py` |
| **CROWN DUEL** — five characters, one hidden hand rewrite | 5 cards + the Royal Sacrifice | 10s | 3 rounds, most Crowns (2 settles it) | `backend/games/duel2_crown.py` |
| **NUMBER CLASH** — 1–9, each spent once | 9 | 8s | first to 4 points | `backend/games/duel3_number_clash.py` |
| **BID WAR** — **staked**, 5 blind lots | 0–balance | 10s | most coins won | `backend/games/duel4_bid_war.py` |

The last three carry state between rounds and score themselves (§4.1, §4.2).
Crown Duel spends *two* engine rounds on one of its own: a strategy round that
publishes only whether a Royal Sacrifice happened, then the card round it sets
up. That beat is skipped once neither Duelist can legally sacrifice.


---

## Staked duels

A duel module may declare `staked = True`. BID WAR is the only one that does.

A staked duel is **fought with the two teams' own coins**. The engine collects
a stake from each Grandmaster before the duel exists, because the module cannot
open without knowing what each seat can bid.

What the engine guarantees:

- `new_duel(seed, stakes)` is called with `stakes` as `{"a": int, "b": int}` —
  the coins each side was granted. They are **deliberately unequal**: a
  Grandmaster funds their own champion, and the other side's grant is never
  shown to them before the sale opens.
- `settlement(state) -> {"a": int, "b": int}` is read exactly once, when the
  duel ends, and paid into the two team purses. Return **winnings only**. The
  stake left the purse when it was granted and must not be returned, or funding
  a champion stops being a gamble.
- The sale is funded from the stakes:
  `pool = 2 x min(stake_a, stake_b) x config.DUEL_STAKE_POOL_MULTIPLIER`, cut
  into `DUEL_STAKE_LOTS` uneven pieces. Off the **smaller** stake deliberately:
  sizing it off the sum lets a Grandmaster inflate the prize by out-staking,
  which makes "empty the purse" the only correct move.
- A staked duel is fought **once per level**, not twice. `config.DUELS_PER_LEVEL`
  still governs the free duels; a staked one ends its own series, and both
  champions stand down green.
- The engine only deals a staked duel when **both** purses hold at least
  `config.DUEL_STAKE_MIN_PURSE`. Teams open a match on nothing, so without this
  the level-one duel would be fought with two empty hands. When it cannot be
  funded, `registry.pick_duel(seed, free_only=True)` deals a free duel instead.

What the module must still guarantee, unchanged: never raise. `stakes` that is
missing, short, or holds nonsense is a grant of nothing, not an error — a duel
that refused to open would strand both teams.

The negotiation itself is engine-side and no module sees it: `PendingStake` on
the `Match`, the `request_stake` / `answer_stake` client messages, and a
`duel_stake` timer whose lapse auto-grants `config.DUEL_STAKE_DEFAULT`.
