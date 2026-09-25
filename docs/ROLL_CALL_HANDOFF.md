# ROLL CALL — server handoff

**What exists:** the two screens, playable, merged. Host projector at `/quizhost`,
player phone at `/quiz`. The room lives in the browser (`localStorage` +
`BroadcastChannel`), the question bank is thirty placeholder prompts, and the
subjects are drawn from whoever is in the room. See
[QUIZ_ROLL_CALL.md](QUIZ_ROLL_CALL.md) for the rules and why it is not a Match.

**What this document is:** everything needed to put a real server behind it and
run one real session, with a real roster, real invitations and a real host.
Decisions are settled; nothing below is a question.

---

## 0. The two rules that must not be broken

**1. The roster never enters this repository.** `github.com/Einsi3dler/relay-game`
is **public**. The roster is ten real people's email addresses and personal
statements about themselves. It lives at `var/rollcall/roster.json`, which
`.gitignore` covers via `var/`. Do not commit it, do not paste it into a test
fixture, do not put a real answer in a docstring. Tests use invented people.

**2. The client never holds an answer it has not earned.** This is the same rule
game modules live under ([GAME_MODULE_SPEC.md](GAME_MODULE_SPEC.md) §2.3): the
payload may carry the state needed to render, never the solution. Today the
prototype fails it completely, and a player with devtools can print all
thirty questions **and** their subjects before question one is asked. §6
is the contract that fixes it, and §11 is the test that proves it.

---

## 1. The data

`var/rollcall/roster.json`, already written and verified:

```json
{
  "version": 1,
  "categories": { "fact": "A random fact",
                  "career": "The most impressive thing",
                  "vision": "In 10 years" },
  "participants": [
    { "email": "…", "answers": { "fact": "…", "career": "…", "vision": "…" } }
  ]
}
```

Ten participants, thirty answers, emails unique, no empty answers, every one
carrying all three categories.

The count is whatever the file holds, not a constant: responses were still
arriving while this was written, and the later ones carry a leading timestamp
column the first batch did not. Read the file, do not hard-code ten anywhere.
Re-run the verification after every import.

**Answers are quoted verbatim.** Do not fix spelling, capitalisation, spacing or
emoji, and do not rewrite first person into a question. The prompt *is* their
sentence, under a category label, and their voice is a legitimate part of the
puzzle. One answer contains a newline; two contain an em dash or an emoji. Treat
the text as opaque UTF-8 and escape it at the DOM boundary, never by editing it.

**Every answer is used, including the weak ones.** Two of the thirty identify
nobody: one career answer that declines to pick anything, and one vision answer
that is a confession of not having one. Each will be a round the whole room
misses. That is the decision: it costs everyone the same and it is over in
twenty seconds. Do not add a filter for them later without saying so.

They are described rather than quoted, here and everywhere else, because the
rule at the top of this document applies to this document.

## 2. Identity: three doors, and nothing else

| Who | How they get in | What they can do |
| --- | --- | --- |
| **Participant** | `GET /rollcall/{token}` — the link in their email, one per person, never expires before the session closes | Register a display name and a face; hold their seat; lock one answer per question |
| **Grandmaster** | `GET /quizhost` behind `RELAY_QUIZ_HOST_KEY` | Import, invite, start, reveal, next, end, close |
| **Everybody else** | Nothing | Nothing. No public join code exists in this mode. |

The room is **roster-only and closed**. Drop the four-character join code from
the host screen; it has nothing to open. The lobby shows `8 of 10 here` and names
who has not arrived instead, which is the thing the Grandmaster actually needs.

The token **is** the credential: whoever holds the link is that person. That is
right for a party game among colleagues and wrong for anything else. Say so in
the module docstring so nobody later mistakes it for auth.

### The host door

Copy `backend/god.py` exactly; it is the same shape. New module
`backend/quizgate.py`:

```python
HOST_KEY  = os.environ.get("RELAY_QUIZ_HOST_KEY", "dev")
HOST_PATH = "/quizhost"
COOKIE    = "relay_quizhost"
SCOPE     = "quizhost"        # keeps this cookie from opening /god
```

using `backend/devgate.py` for the constant-time check and the cookie token.
`?key=` for scripts, a form for humans, cookie thereafter. **The default is
`"dev"` and this file is public**, so set `RELAY_QUIZ_HOST_KEY` in `.env.local`
before the session. A separate secret from `RELAY_GOD_KEY` on purpose: different
people may hold them, and they rotate independently.

## 3. Persistence

Registration happens days before the session and must survive a restart; live
room state need not. So:

- **SQLite at `var/rollcall.db`** (`var/` and `*.db` are both gitignored) for
  participants and answers. Its own file, not the accounts database: a
  participant is not a `User`, and `backend/accounts.py` is a different lane.
- **In memory** for the live room, exactly as `backend/duelroom.py` does it.

```sql
CREATE TABLE participant (
  id            TEXT PRIMARY KEY,   -- "rc_" + 8 hex
  email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
  token_hash    TEXT NOT NULL UNIQUE,   -- sha256 of the raw token; never store the token
  display_name  TEXT,                   -- NULL until they register
  avatar        TEXT,                   -- AVATAR_SLOTS code, NULL for a seeded face
  registered_at TEXT,
  invited_at    TEXT,
  created_at    TEXT NOT NULL
);
CREATE TABLE answer (
  id             TEXT PRIMARY KEY,
  participant_id TEXT NOT NULL REFERENCES participant(id),
  category       TEXT NOT NULL,     -- fact | career | vision
  body           TEXT NOT NULL
);
```

Hash the token the way `accounts._digest` does. The raw token exists once, in
the email, and is never written down.

Import is idempotent, keyed on email: re-running it updates answers and leaves
tokens, names and faces alone. It must be safe to run twice five minutes before
the session.

## 4. Building the questions

At session start, from every participant with a row (registered or not):

```
questions = shuffle([
    Question(id, category, body, subject=participant.id)
    for participant in roster
    for category, body in participant.answers
])
```

Thirty questions, **all three categories shuffled together**, category
shown as a small label above the quote. Shuffle with `secrets`-seeded RNG on the
server; the order must not be derivable by a client.

**Presence does not change the question set.** Everyone who registered is a card
and can be a subject, whether or not they turn up. This matters for §5.

## 5. The room

`backend/quizroom.py`, a state machine in the shape of `duelroom.py`: no teams,
no levels, no currency, no timers, no `TimerService` at all. Phases
`lobby → question → reveal → final → closed`.

Port the rules from `frontend/quiz.js` as-is; they are settled and tested by
hand. Specifically:

- **Scoring.** `QUIZ_BASE_POINTS = 100` for a correct guess. Everyone correct is
  ranked by time-to-lock, and a bonus decays down that order:
  `round(QUIZ_SPEED_MAX * QUIZ_SPEED_DECAY ** rank)` with `50` and `0.8`, giving
  50, 40, 32, 26, 20. Rank, not wall-clock: there is no clock and a slow room
  would zero out under an absolute curve.
- **A lock is final.** A second submission for the same question is refused.
- **The subject locks a sentinel.** They cannot guess and score nothing, but they
  must still lock, because a projector roster missing exactly one person names
  them. Refuse a real guess from the subject and refuse the sentinel from
  everyone else.
- **The locked-in denominator is the connected players**, not the roster. Someone
  who never turned up is not being waited on. An absent subject simply means
  nobody locks a sentinel that round, and that leaks nothing, because subjects
  are fixed by the data and uncorrelated with who showed up.
- **Ties** break on score, then rounds right, then mean lock time, then name.
- **Awards** at the end: highest score, and fastest finger over correct answers
  only.

All constants go in `backend/config.py`. No magic numbers in the room
([CLAUDE.md](../CLAUDE.md)).

## 6. The snapshot contract

`quizroom.public(room, viewer_id)` is the security boundary. One personalised
snapshot per socket, as `broadcast_room` already does for duels.

**Always:** room phase; `you`; `is_host`; the full card list of registered people
as `{id, display_name, avatar, connected, score, correct}`; `index` and `total`.

**Phase `question`** — the live question as **`{category, body}` and nothing
else**. No `subject`, not on any key, not nested, not for the host. The host
screen is projected: it is the *least* trusted viewer in the room, not the most.
Plus, per player, `answered: bool`. Plus, for the viewer only, their own
`answer` and `you_are_subject: bool`.

**Never at any phase:** `questions[]`, the unplayed remainder, or any other
player's choice before the reveal.

**Phase `reveal`** — add `subject`, and each player's `answer`, `gain` and
`bonus`. This is when the got-it and missed-it groups become renderable, and not
one message earlier.

**Phase `final`** — add the awards.

## 7. HTTP and WebSocket surface

```
GET  /quizhost                     host page, or the key form        (gate)
POST /quizhost/login               sets the cookie                   (gate)
GET  /rollcall/{token}             participant page: register, or play
GET  /api/rollcall/me?token=       their registration state
POST /api/rollcall/register        {token, name, avatar}
POST /api/rollcall/import          re-read roster.json               (gate)
POST /api/rollcall/invite          {dry_run}  send the emails        (gate)
WS   /ws/quiz?token=               a participant's socket
WS   /ws/quiz/host                 the Grandmaster's socket          (gate)
```

Its own WebSocket endpoint, not a branch on the match one, and its own parser in
`backend/protocol.py` (`parse_quiz_message`, `QUIZ_MESSAGE_TYPES`) for the same
reason `parse_room_message` exists: the cheapest way to keep the match's message
set closed is for the quiz never to touch it.

Client to server: `quiz_answer {choice}` (the sentinel for a subject),
`request_state`, `heartbeat`. Host only: `quiz_host {action}` where action is
`start | reveal | next | finish | close`. Server to client: `quiz_room_state`,
`error`.

### The registration page

`/rollcall/{token}` captures **two things and no more**: a display name and a
face. Confirmed, so do not grow this form. Everything else about a participant
was collected by the form that produced `roster.json` and is never typed again.

Name and face are **one object**, not two settings. Together they are the card
the room taps, they appear as a pair on the projector, in the locked-in roster,
in the got-it and missed-it lists, in the standings and on the awards. Store
them together, render them together, and never draw one without the other.

Validate `name`: 2 to `QUIZ_NAME_MAX` characters after trimming, unique
case-insensitively across the roster, rejected otherwise. Validate `avatar`
through the same path `accounts.set_avatar` uses, so an unreadable code falls
back to a seeded face rather than reaching a renderer.

Two details that are not decoration:

- **Prefill the name from the local part of their address** (`ada.nwosu` →
  "Ada Nwosu"), editable. A name is only useful here if the room can place it:
  a handle nobody recognises makes that person unguessable and quietly ruins the
  three rounds they are the answer to. A sensible default is cheaper than a
  validation rule that cannot tell a nickname from a name.
- **Show them their own three answers on the page, read-only.** It is their own
  data, it makes the page read as honest rather than as a form harvesting a
  name, and it reminds them what they wrote so they do not give themselves away
  on the night. Read-only on purpose: two people wrote non-answers, and once
  they see the game is real the temptation is to polish, which quietly edits
  data you have already built questions from.

Never show a participant anyone else's answers on this page.

## 8. The invitation

Plain text through `mailer.send(to, subject, body, kind="rollcall")`. It never
raises; check `.delivered`. `mailer.throttled(to, kind)` blocks a repeat to the
same address within `EMAIL_SEND_MIN_INTERVAL_SECONDS` (60), which will not
affect a batch of ten distinct addresses but will bite a re-send, so check it
before minting anything.

```
Subject: Roll Call: pick the name the room will see

Hi,

You filled in three things about yourself. They are about to become a quiz,
and the rest of the room has to work out which answers are yours.

Pick the name and face the room will see:

  https://hotpot.sylvesterdivine.com/rollcall/<token>

That link is yours alone. Keep it: it is also how you join on the night, so
there is no code to type.

You will not be asked to guess your own answers.

If you were not expecting this, ignore it and nothing happens.
```

**Send with `dry_run: true` first.** It renders every message and returns them
without calling `send`, so the copy and the ten links can be read before
anything leaves the building. `RELAY_MAIL_BACKEND=smtp` and `delivers_mail()` is
already `True` on this deployment: a live run reaches real inboxes and cannot be
taken back.

## 9. What changes on the frontend

Mostly the transport. `RelayQuizRoom.apply(action, fields)` becomes a send and
`subscribe(fn)` becomes a socket push; the two view controllers hold no state
and should need no logic changes. Beyond that:

- **Delete the client-side bank, the shuffle and the scoring.** They move to the
  server. What is left of `quiz.js` is drawing and transport.
- **The prototype controls strip goes**, with `seedPlayers` and `autoAnswer`.
- **The join form goes.** `/rollcall/{token}` replaces it: a name field and a
  face picker, no room code.
- **The prompt must survive a paragraph.** `.qprompt` is display type sized for
  "Who taught themselves an instrument?" and the longest vision here is about
  fifty words with a line break in it. Scale the size by length and render the
  body as a blockquote under its category label.
- **The lobby** shows `8 of 10 here` and who is missing, not a join code.

## 10. Runbook

```bash
export RELAY_QUIZ_HOST_KEY=…            # not "dev"
python -m backend.rollcall import       # reads var/rollcall/roster.json
python -m backend.rollcall invite --dry-run
python -m backend.rollcall invite       # real mail, real people
```

On the night: open `/quizhost`, enter the host key, watch the lobby fill, press
**Start the quiz** when the room is in. Reveal and Next are the only two controls
that matter. **End session** goes to the standings and the awards; **Close the
session** releases everyone's screen.

## 11. Tests that must exist

Invented people only, never the roster.

1. **No leak, and this is the one that matters.** Serialise a player snapshot and
   a host snapshot during `question`, walk the whole structure, and assert the
   subject's id appears nowhere in either. Assert the same for every unplayed
   question. This test is the reason §6 exists; write it first.
2. A subject cannot submit a real guess; a non-subject cannot submit the
   sentinel.
3. A second submission does not move a lock.
4. The bonus ladder is 150 / 140 / 132 for the first three correct, and the
   subject scores nothing.
5. Import is idempotent: run it twice, tokens and display names are unchanged.
6. A token that is not in the table gets a 404, not a stack trace.
7. Registration rejects a duplicate display name, case-insensitively.
8. Awards: fastest finger ignores wrong answers.

## 12. Open items

- **Nobody has been invited yet.** No mail has been sent and no token has been
  minted.
- **A participant who loses their link** has no recovery path. Simplest answer is
  for the Grandmaster to re-issue from the host page; not specified here.
- **One session at a time** is assumed throughout. A second concurrent room would
  need the roster keyed by session, and nothing here is built for it.
