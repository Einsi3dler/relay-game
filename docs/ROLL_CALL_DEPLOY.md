# ROLL CALL — deployment runbook

Everything needed to put one session in front of real people, in order, with
the things that bite named before they bite. Nothing here has been done yet.

Read [ROLL_CALL_DO_NOT_SEND.md](ROLL_CALL_DO_NOT_SEND.md) first. It is still in
force: **no mail goes to the roster until the repository owner says so**, and
there is deliberately no code that can send any. Step 4 below hands out links
by other means.

---

## 0. The shape of the thing

| | |
| --- | --- |
| Host screen | `https://<host>/quizhost`, behind `RELAY_QUIZ_HOST_KEY` |
| A participant | `https://<host>/rollcall/<token>`, one per person, from their invitation |
| Public join code | **None.** The room is roster-only and closed. |
| Roster data | `var/rollcall/roster.json`, gitignored, real people |
| Registrations | `var/rollcall.db`, SQLite, survives a restart |
| The live room | **memory only**, dies with the process |

`deploy.sh` serves `/srv/relay-game` through `relay-game.service` on port 8000,
single worker, and pulls `main`. `var/` is gitignored, so a pull does not touch
the roster or the database.

## 1. Before anything, set the host key

```bash
# in the server checkout's .env.local, NOT in the repo
RELAY_QUIZ_HOST_KEY=<something long and not "dev">
```

**The default is `dev` and `backend/quizgate.py` is in a public repository.**
Until this is set, anybody who finds `/quizhost` runs your session: starting,
revealing, ending. This is the single most important line in this document.

It is deliberately a different secret from `RELAY_GOD_KEY`. The `scope` in the
cookie hash means neither cookie opens the other door even if both are set to
the same string, but set them to different strings anyway.

Confirm after deploying:

```bash
cd /srv/relay-game && .venv/bin/python3 -c \
  "from backend import quizgate; print('still the default:', quizgate.HOST_KEY == 'dev')"
```

## 2. Check the rest of the environment

`RELAY_BASE_URL` must already be the public origin, because it is what the
invitation links are built from. On this deployment it is
`https://hotpot.sylvesterdivine.com`, and `mailer.verify_deployment()` refuses
to start the server if it is missing while mail is configured, so a wrong value
is more likely than a missing one. A link built from `http://127.0.0.1:8000`
resolves to the reader's own laptop.

```bash
cd /srv/relay-game && .venv/bin/python3 -c \
  "from backend import mailer; print(mailer.base_url())"
```

## 3. Put the roster on the server

The file is gitignored and must therefore be copied by hand, once:

```bash
scp var/rollcall/roster.json <server>:/srv/relay-game/var/rollcall/roster.json
chown relay:relay /srv/relay-game/var/rollcall/roster.json
chmod 600 /srv/relay-game/var/rollcall/roster.json
```

The app imports it on every boot, idempotently, keyed on email: re-running
updates answers and leaves tokens, names and faces alone. Adding an eleventh
person later means updating the file and restarting.

Then deploy as normal:

```bash
sudo ./deploy.sh
```

## 4. Mint the links, once

```bash
cd /srv/relay-game
sudo -u relay .venv/bin/python3 -m backend.rollcall links
```

**Run this exactly once and keep the output.** Every run mints fresh tokens and
invalidates the previous ones, because what is stored is a hash and a link
cannot be reprinted, only reissued. Running it again the morning after sending
kills every link you sent.

Distributing them is a manual step on purpose, and the links are personal:
one per person, in a one-to-one channel. A link in a group chat is a seat
anybody in that group can take.

If one person loses theirs, reissue only for them rather than re-running the
whole batch:

```python
from backend import rollcall
rollcall.connect()
print(rollcall.reissue("rc_xxxxxxxx"))   # their old link stops working
```

## 5. Before the session

- [ ] `RELAY_QUIZ_HOST_KEY` set, and not `dev`
- [ ] `base_url()` prints the public origin
- [ ] `python -m backend.rollcall list` shows everybody
- [ ] Open `/quizhost` yourself and confirm the key form appears
- [ ] Register one seat end to end on a phone, on mobile data, not office wifi
- [ ] **Do not deploy again today.** See below.

## 6. On the night

1. Open `/quizhost`, enter the host key. The lobby shows `n of 10 here` and
   names who has not arrived.
2. People open their own links. Anyone who has not registered picks a name and
   a face first; anyone who has goes straight through.
3. **Start the quiz** once at least two are in. Thirty questions, shuffled,
   three per person.
4. **Reveal** when the room has stopped moving. It works whether or not
   everybody has locked in, because somebody always has not.
5. **Next question.** Repeat.
6. **End session** drops to the final standings and the two awards. **Close the
   session** then releases everyone's screen.

Latecomers can arrive at any point and start on zero. The question set is fixed
at step 3, so somebody who registers after that is a card but has no questions
of their own.

## 7. What breaks, and what to do

**A restart loses the session.** The room is in memory and the unit runs a
single worker. A deploy, a crash or an OOM puts everybody back in the lobby
with zero points. Registrations survive, so nobody has to sign up again.
There is no recovery beyond starting the quiz over, which is survivable for a
party game and the reason not to deploy on the day.

**If it happens mid-session:** open `/quizhost`, press **Start the quiz**
again. Everyone's screen follows; they do not need new links.

**Somebody cannot get in.** Their link is probably stale because `links` was
run twice. Reissue for that person only.

**Somebody picked an unrecognisable name.** They can change it: their link
reopens the form through "Change my name or face". Do it before step 3.

**The host screen disconnects.** It reconnects on its own and the bar says so
while it is trying. The room is on the server, so nothing is lost.

## 8. Afterwards

Nothing is published and nothing is scraped, but the database now holds real
names against real addresses. Treat `var/rollcall.db` the way you treat
`roster.json`: it is gitignored, it stays on the box, and it does not go in a
backup that leaves the box without thinking about it first.

To wipe the session and start clean, stop the service, delete
`var/rollcall.db`, and start it again. The roster re-imports from the file;
everybody needs a new link.

## 9. Still not built

- **Sending.** On purpose. See
  [ROLL_CALL_DO_NOT_SEND.md](ROLL_CALL_DO_NOT_SEND.md) §"When the time comes".
- **More than one session at a time.** The roster is one list and the room is
  one object. A second concurrent room needs both keyed by session.
- **A host-side reissue button.** Reissuing is a Python one-liner today.

## Related

- [ROLL_CALL_HANDOFF.md](ROLL_CALL_HANDOFF.md) — the implementation spec
- [QUIZ_ROLL_CALL.md](QUIZ_ROLL_CALL.md) — the game design
- [ROLL_CALL_DO_NOT_SEND.md](ROLL_CALL_DO_NOT_SEND.md) — the standing order
