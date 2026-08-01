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
DUEL_RULES_VERSION = 1
SIDES = ("a", "b")          # seats, assigned in config.TEAM_IDS order

@dataclass
class DuelState:
    duel_game_id: str
    round_index: int                  # 1-based
    wins: dict[str, int]              # side -> round wins
    choices: dict[str, str]           # side -> choice. SERVER ONLY until reveal.
    history: list[dict]               # resolved rounds; safe to show anyone
    payload: dict                     # render hints

class DuelModule(Protocol):
    id: str
    name: str
    choice_seconds: int   # the per-round choice window — your game's time cost
    wins_needed: int      # round wins that take the duel

    def new_duel(self, seed: int) -> DuelState: ...
    def normalize_choice(self, state, choice: object) -> str | None: ...
    def resolve_round(self, state) -> str | None: ...   # "a" | "b" | None (tie)
    def public(self, state, side: str | None, revealed: bool) -> dict: ...
    def reset(self) -> None: ...
```

### Method rules

- **`new_duel(seed)`** — deterministic in `seed`. Return a fresh `DuelState`; never
  carry state on the module, which is a long-lived singleton shared by every match.
- **`normalize_choice(state, choice)`** — validate *and* canonicalise in one call.
  Return the canonical move, or `None` if it is illegal. Doing both here is what
  guarantees `DuelState.choices` only ever holds canonical values, so
  `resolve_round` never re-parses client text. Cap the raw input length
  (`MAX_CHOICE_CHARS`) **before** any further work, and never raise — a hostile
  move is simply not legal.
- **`resolve_round(state)`** — pure. `"a"`, `"b"`, or `None` for a tie (the engine
  replays the round). **A missing choice must lose**, and both missing must tie, or
  stalling becomes a strategy.
- **`public(state, side, revealed)`** — delegate to `duel_base.base_public`. See §4.

### Time consequences are yours

`choice_seconds` and `wins_needed` belong to the module, not the engine. This is
the sanctioned way for duel games to differ: same rules, different time cost. The
engine reads them when it schedules the round timer. Everything else — the gap
between duels, the penalty length, the payout — is engine config
(`DUEL_*` in `backend/config.py`) and is the same for every duel game.

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
   will trip on it.
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
