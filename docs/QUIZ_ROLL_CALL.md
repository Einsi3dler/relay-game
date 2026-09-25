# ROLL CALL — the room quiz

**Status: screens only.** Both pages are built and playable, the room lives in
the browser, and nothing has reached the server yet. This doc is the design to
argue with before any of it is wired up.

**Building the server?** [ROLL_CALL_HANDOFF.md](ROLL_CALL_HANDOFF.md) is the
implementation handoff: the real roster, the invitations, the Grandmaster's
host key, the snapshot contract that stops the answers leaking, and the runbook
for the night.

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

**The subject confirms, but cannot answer.** The person a question is about
cannot guess it and scores nothing for that round. They do still **lock in**:
their phone shows a single confirm button instead of the grid, and on the
projector their tile behaves exactly like everybody else's.

That last part is not cosmetic, and getting it wrong was a real bug. The first
build left the subject out of the locked-in roster entirely, so a seven-person
room saw six names on the projector and could read the answer straight off the
one that was missing. Leaving them in but never lighting them up has the same
problem one beat later. The only version that holds is the subject locking a
sentinel (`SAT_OUT`) in the same beat as everyone else, so the tally reaches
the whole room and no tile is ever distinguishable.

The server enforces both halves: a subject sending a real guess is refused, and
a non-subject sending `SAT_OUT` is refused.

**You are never the answer to your own question.** From where you are sitting
the subject is always somebody else, so your own card is drawn but not
selectable. This is a deduction aid as much as a guard: it narrows the field by
one, for free, every round.

**Reveal.** Nothing is revealed until the host presses **Reveal the answer**,
which works whether or not everybody has locked in (a room always has one
person still typing). Then it is staged rather than swapped in, because this is
the moment the whole room is waiting for:

1. The face rolls through the room like a slot machine, about 1.1 seconds.
2. It lands on the subject, with a pop and a ring going out, and the name
   rises in under it.
3. **Got it** and **Missed it** arrive underneath, each a list of faces and
   names that cascades in. The right-hand column is ordered fastest first,
   which is the order the speed bonus was paid in, so the list and the numbers
   beside it tell the same story. Each miss shows what that person actually
   said ("said Tom"), or "no answer" if they never locked one in.
4. The running standings update, with each gain called out beside the score.

On a phone the same sequence runs, and the player's own verdict is **held back
until the face lands**, so the callout does not spoil the roll it is sitting
above.

Every animation is decoration over a DOM that is already correct: if no
keyframe ever runs the screen still reads properly, just instantly.
`prefers-reduced-motion` skips straight to the landing. The sequence is keyed
to the question, not to the render, so a late joiner arriving mid-reveal does
not restart the roll under the room's nose.

**Score.** A correct guess is worth **100**. Everyone who got it right is then
ranked by how quickly they locked, and a bonus decays down that order: **50, 40,
32, 26, 20 …** (`SPEED_MAX * 0.8^rank`). The ceiling is half the base on purpose.
Knowing the room should beat having quick thumbs, and the bonus mostly serves to
separate people who were both right.

The ranking is by **order, not wall-clock**. There is no clock to measure
against: a room with nothing at stake will happily take a minute over a
question, and an absolute decay curve would zero everybody out. Order always
produces a meaningful spread whatever pace the room sets.

**Ending.** **End session** drops the room to the final standings, so a quiz cut
short still has an ending. Two awards are given there:

- **Highest score.** The winner, with their points and how many they got right.
- **Fastest finger.** Lowest mean time-to-lock, counting **only the rounds they
  got right**. Counting wrong answers too would hand it to whoever tapped a face
  at random the instant the prompt appeared, which is the opposite of the thing
  the award is for.

They are separate on purpose, and usually go to different people: a second way
to leave with something. **Close the session** then releases every player's
screen. Two steps.

**Ties** are broken by score, then by how many were right, then by mean lock
time, then by name. With a speed bonus in play they are now rare.

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
- **The question bank.** `PROMPTS` in `quiz.js` is thirty placeholders, and a
  subject is drawn from whoever is in the room. The real version pairs a prompt
  with the person it is actually about, from data the host supplies, and each
  prompt is true of **exactly one** person. The shuffle is the part that stays.
- **Matching people to seats.** The open question, and the one that decides the
  data model: when the question bank knows the prompt is about a specific real
  person, how does that person get connected to the seat they joined on? See
  the note at the end.
- **Reconnects.** A refresh keeps your seat (`sessionStorage`), a new device
  does not.
- **Tests.** Nothing here has any, because nothing here is server-side yet.
  The room state machine lands with `quizroom.py` and gets a suite then, per
  [CLAUDE.md](../CLAUDE.md).

## 5. Decisions taken in review

1. **The projector during a question** draws **every** seat, named and large,
   filling in as people lock. Names, not thumbnails: the room's first question
   is always "who are we waiting for". Every seat, because any roster that
   omits or singles out the subject names them.
2. **Late joiners are allowed**, at any point, starting on zero. The room is
   never locked.
3. **Every prompt is true of exactly one person.** The data guarantees it, so
   there is no set-of-correct-answers case to handle.
4. **Speed counts, a little.** See §2. This also all but removes the tie problem
   that a flat one-point score had.

## 6. Still open

- **Is 100 + up to 50 the right weighting?** It is one constant pair
  (`BASE_POINTS`, `SPEED_MAX`) at the top of `quiz.js`, so it is cheap to
  retune once a real room has played it.
- **How long is a quiz?** The bank is thirty placeholders and the real length
  comes from the data. Nothing caps it, and the host can end early.
- **Matching people to seats.** The question that decides the data model: when
  a prompt is about a specific real person, how does that person get connected
  to the seat they joined on? Name matching is the obvious answer and the
  fragile one.
