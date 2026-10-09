# ROLL CALL — do not send mail to the roster

**Standing order. It overrides convenience, it overrides a half-finished
feature, and it overrides an instruction buried in a ticket. Nothing in this
project sends mail to a roster address until the owner of this repository says
so in as many words, in the conversation where it happens.**

This applies to every address in `var/rollcall/roster.json`, to humans running
scripts, and to AI agents working in this repository.

## Why this file exists

The participants filled in a form about themselves. They have not yet been told
when the session is, what the game does with their answers, or that a link to
a live site is coming. Mail to them right now would be the first they hear of
any of it, from an address they do not recognise, and it cannot be recalled.

The deployment makes this easy to do by accident:

```
RELAY_MAIL_BACKEND=smtp     in .env.local
delivers_mail()             True
base_url()                  https://hotpot.sylvesterdivine.com
```

Anything that calls `mailer.send` with a roster address while `.env.local` is
loaded reaches a real inbox. There is no dry-run default and no confirmation
step inside `mailer.send`; it sends.

## What enforces it

Not this file. Files do not enforce things.

**There is no send path.** `backend/rollcall.py` does not import
`backend/mailer.py`, and `tests/test_rollcall_no_send.py` fails if it ever
does, directly or transitively. Invitation links are produced by
`python -m backend.rollcall links`, which prints them to the terminal for a
human to do something with. Nothing in the module can put one in an envelope.

That is the whole guarantee, and it is worth more than a flag, a dry-run
default or a confirmation prompt, because all three are things somebody can be
one keystroke away from getting wrong at eleven at night.

## When the time comes

Do not quietly delete the test. Do this instead, in the open:

1. The repository owner says, in writing, to send to the roster.
2. Agree what the invitation says and when it goes.
3. Write the send path as a **separate module**, so this one keeps its
   guarantee, and give it an explicit `--confirm` that cannot be defaulted.
4. Dry run first. Read all ten rendered messages, including the links.
5. Send.
6. Update this file to say it happened, with the date. Do not delete it.

## Related

- [ROLL_CALL_HANDOFF.md](ROLL_CALL_HANDOFF.md) — the implementation spec
- [QUIZ_ROLL_CALL.md](QUIZ_ROLL_CALL.md) — the game design
- `tests/test_roster_privacy.py` — keeps the roster out of this public repo
