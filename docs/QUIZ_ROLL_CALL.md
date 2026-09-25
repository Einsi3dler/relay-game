# ROLL CALL — the room quiz

**Status: screens only.** Both pages are built and playable, the room lives in
the browser, and nothing has reached the server yet. This doc is the design to
argue with before any of it is wired up.

A prompt lands on a shared screen. Everyone in the room decides which *person
in the room* it belongs to and taps their face. There is no clock. The host
paces it and closes the session when the room is done.

- Host screen (the projector): [`/quizhost`](../frontend/quizhost.html)
- Player screen (the phone): [`/quiz`](../frontend/quiz.html)

---

## 1. Why this is not a game module, and not a Match

The obvious first instinct is to write it as a `GameModule`
([GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md)) and let the engine run it. It does
not fit, and forcing it would cost more than it saves:

| The game-module contract assumes | ROLL CALL |
| --- | --- |
| One puzzle per player, generated from a private seed | One prompt, the whole room, everybody sees it |
| `check(puzzle, answer)` is pure and knows nothing about who is playing | The answer *is* a person in the room, so it cannot exist before the room does |
| Difficulty scales with `level`, 1..13 | No levels. No difficulty curve. |
| The engine owns the loop, timers and statuses | A human owns the loop. There are no timers at all. |

So it takes the shape `backend/duelroom.py` already established: **a room is
deliberately not a Match.** No teams, no levels, no currency, no perks, no
Grandmaster, none of the thirty-four `match.status` guards in the engine
acquiring a second meaning. Physical separation is the enforcement.

What it *does* share is the avatar vocabulary (`frontend/avatar.js`) and the
dark `--gm-*` palette, so a quiz looks like the same evening as a race.

## 2. The rules, as built

**Join.** Host opens `/quizhost`, the room gets a four-character code. Players
open `/quiz`, type the code, give a name and pick a face. The code alphabet
drops `0/O` and `1/I`: it is read off a projector and typed on a phone.

**Round.** The host screen shows the prompt and a "locked in" meter. Each phone
shows the prompt and a grid of every face in the room. A tap selects, a second
deliberate press on **Lock it in** commits. There is no timer, so there is no
reason to punish a fat thumb with an instant lock.

**The subject sits out.** The person a question is about cannot answer it, and
is not counted in the "locked in" tally. Their card is still drawn on everyone
else's phone (the room's shape should not shift round to round) and their own
phone says so plainly. They score nothing for that round.

**You are never the answer to your own question.** From where you are sitting
the subject is always somebody else, so your own card is drawn but not
selectable. This is a deduction aid as much as a guard: it narrows the field by
one, for free, every round.

**Reveal.** Nothing is revealed until the host presses **Reveal the answer**,
which works whether or not everybody has locked in (a room always has one
person still typing). The correct face goes up big, the got-it/missed-it split
shows, and the running standings appear.

**Score.** One point for a correct guess, nothing otherwise. Flat, not
speed-weighted: with no clock there is no speed to weigh, and a flat point is
the thing a room can verify at a glance. Ties are honest and stay unbroken.

**Ending.** **End session** drops the room to the final standings, so a quiz cut
short still has an ending. **Close the session** on that screen releases every
player's screen. Two steps on purpose.

## 3. What is mocked, and where the seam is

`frontend/quiz.js` holds the room object and a prototype transport: state in
`localStorage`, sync over a `BroadcastChannel`. Open the host in one tab and a
player in another and the thing genuinely plays. Nothing reaches a server;
nothing survives a different browser.

The seam is drawn so the swap is a transport swap, not a rewrite:

- `RelayQuizRoom.apply(action, fields)` mutates local state today. Wired up it
  posts `{action, fields}` and waits for the push.
- `RelayQuizRoom.subscribe(fn)` fires on a storage event today. Wired up it
  fires on a server push.
- The two view controllers (`quizhost.js`, `quizplay.js`) own no state. They
  render whatever they are handed and send intents back. Neither learns the
  difference.

Every tab writes the room object directly, last write wins. That is wrong for a
network and fine for a human-paced quiz on one machine.

**The prototype controls strip** on the host screen ("Add 6 players",
"Everyone answers", "Reset room") stands in for the phones a real room brings,
so one person can review the whole flow. It goes when the room is wired up.

## 4. What is not built yet

- **The server side.** A `quizroom.py` in the shape of `duelroom.py`, the
  WebSocket messages, and the host's authority over the phase.
- **The question bank.** `PROMPTS` in `quiz.js` is twelve placeholders, and a
  subject is drawn from whoever is in the room. The real version pairs a prompt
  with the person it is actually about, from data the host supplies. The
  shuffle is the part that stays.
- **Matching people to seats.** The open question, and the one that decides the
  data model: when the question bank knows the prompt is about a specific real
  person, how does that person get connected to the seat they joined on? See
  the note at the end.
- **Reconnects.** A refresh keeps your seat (`sessionStorage`), a new device
  does not.
- **Tests.** Nothing here has any, because nothing here is server-side yet.
  The room state machine lands with `quizroom.py` and gets a suite then, per
  [CLAUDE.md](../CLAUDE.md).

## 5. Open questions for the design review

1. **Dead space on the projector.** During a question the host screen is a
   prompt and a meter, and the bottom two thirds are empty. Options: draw the
   room's faces large and grey them in as they lock, or keep it sparse so the
   prompt is the only thing being read aloud.
2. **Late joiners.** Right now anyone can join mid-quiz and starts on zero.
   Should the room lock at the start instead?
3. **Prompts with more than one true answer.** "Who speaks three languages" may
   fit four people in the room. Either the data guarantees one subject per
   prompt, or a prompt carries a set of correct people and any of them scores.
4. **Ties.** Unbroken today. A room with twelve questions and eight players
   will produce them constantly at one point a question.
