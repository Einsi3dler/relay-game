# Accounts: sign up, sign in, sign out

Optional accounts for The Relay. A player can register, sign in with a password
or a one-click email link, reset a forgotten password, and confirm their email
address.

Read this before touching `backend/accounts.py`, `backend/auth.py`,
`backend/mailer.py`, or the `frontend/auth.*` trio.

---

## The one rule: accounts are optional

Guest join works exactly as it always has. Nothing in this feature gates
gameplay, and **the engine never asks who you are** — `backend/engine.py` has
no idea an account exists, and no import from this feature reaches it.

That is deliberate. The Relay is a synchronous race that eight people join
minutes before it starts. A registration wall plus an inbox round trip at that
moment loses players. An account is an upgrade for people who play often, not
a toll gate.

If you are adding something here, the test that keeps this honest is
`test_guest_join_still_works_without_an_account` in `tests/test_auth.py`.

## What this fixes about disconnections, and what it does not

Worth being precise, because it is easy to assume accounts solve reconnection
outright.

**Accounts fix identity.** A seat is tied to a person who can prove who they
are from any device. Before this, recovery meant a 6-character rejoin code held
in browser storage: lose the browser, clear the cache, or pick up a different
phone, and the seat was gone unless someone read the code out loud.

**Accounts do not fix the socket.** All of this is untouched and still matters:

- The WebSocket still drops on a wifi blip or a sleeping phone, and
  `engine.on_reconnect` still restores puzzle state, timers and statuses.
- Match state still lives in memory with a single worker. A server restart
  still ends every match in flight. Signing in afterwards gets you an account,
  not your seat.
- Rejoin codes still exist and still work. Nothing was removed.

So: this replaces the rejoin code as the *recovery experience*. It does not
replace the reconnect machinery underneath, and it does not make matches
durable.

## Why this is the one thing that touches disk

`CLAUDE.md` says in-memory state, no DB in the MVP, and that is still right for
matches: a match lasts an hour and there is nothing to keep afterwards.

An account is the opposite. An in-memory account is deleted by the next deploy,
which leaves a player who registered yesterday locked out with no rejoin code
either — strictly worse than not having accounts at all. So accounts get a
SQLite file and nothing else does.

It is stdlib `sqlite3`, so the dependency list is unchanged: still just FastAPI
and uvicorn.

## The files

| File | What it owns |
| --- | --- |
| `backend/accounts.py` | The store. Users, scrypt hashing, sessions, one-time tokens. Plain functions over a SQLite connection, no web framework in sight. |
| `backend/auth.py` | The routes, as an `APIRouter` under `/api/auth`. Imports the store and the mailer, nothing else. |
| `backend/mailer.py` | Outbound email. One `send`, a console backend and an SMTP backend. |
| `backend/config.py` | Every tunable, in the `Accounts` block at the bottom. |
| `frontend/auth.html` `.css` `.js` | One page behind `/signup`, `/login`, `/forgot`, `/reset`, `/verify`, `/magic` and `/account`. |
| `tests/test_accounts.py` | The store's rules, with no server. |
| `tests/test_auth.py` | The routes end to end, reading links out of the console outbox. |
| `tests/conftest.py` | Pins the test database in memory. See the warning below. |

`backend/main.py` gains two things only: `app.include_router(auth.router)` and
the page routes. The engine is not touched.

## The flows

**Sign up** — first name, last name, email, username, password, confirm.
Creates the account, mails a verification link, and signs the browser in
immediately. Unverified.

**Sign in** — email and password. Or "Email me a sign-in link", which sends a
magic link using whatever address is already typed in the form.

**Forgot password** — `/forgot` asks which of two things to send:

- *A password reset link*, valid 1 hour, single use. Sets a new password and
  signs them in.
- *A sign-in link*, valid 15 minutes, single use. Signs them in and leaves the
  password alone.

**Verify** — a banner on the account page until the address is confirmed, with
a resend button. It blocks nothing. Using a reset or magic link also marks the
address verified, because following one proves the person reads mail there,
which is the only question verification asks.

**Sign out** — deletes the session row, then clears the cookie. Both halves:
clearing only the cookie would leave a live session for anyone who had already
copied it.

## Security decisions worth not undoing

- **Passwords** are scrypt (`hashlib`, stdlib) at n=2^15, about 75ms per hash.
  The stored string carries its own parameters, so raising the cost later does
  not invalidate existing hashes.
- **Sessions and email tokens are stored hashed.** The cookie holds a random
  token; the table holds its SHA-256. A leaked database therefore yields no
  usable cookies or links.
- **Email tokens are single use**, and issuing a new one of the same purpose
  deletes the previous one.
- **Emailed links do not spend their token on GET.** The page loads and its
  script posts the token. Corporate mail scanners follow links before the human
  does, and a token consumed on GET would already be gone.
- **No route reveals whether an address is registered.** `/forgot` answers
  identically either way, and a failed login gives one message for a wrong
  address and a wrong password alike. An unknown address still pays for a hash,
  so response time does not leak it either.
- **The base URL for links is configured, never taken from the Host header.**
  That header is attacker-controlled, and a reset link built from it is the
  standard way to make a server mail a working reset to someone else's domain.
- **`next=` redirects must start with a single `/`.** Otherwise the sign-in
  page becomes an open redirect.

## Configuration

Nothing is required to run locally. Every default works out of the box and mail
prints to the terminal. `.env.example` is the annotated copy of all of this.

| Variable | Default | What it does |
| --- | --- | --- |
| `RELAY_DB_PATH` | `var/relay.db` | Account database. In production point it at storage that survives a redeploy. If the file sits inside the checkout, a fresh clone deletes every account. |
| `RELAY_BASE_URL` | `http://127.0.0.1:8000` (dev only) | The origin in every emailed link, and what decides whether the session cookie is marked `Secure`. **Required for real mail**, see the guard below. |
| `RELAY_MAIL_BACKEND` | `console` | `console` or `smtp`. |
| `RELAY_MAIL_FROM` | `The Relay <no-reply@relay.local>` | The From header. Its domain is the one that needs SPF and DKIM. |
| `RELAY_SMTP_HOST` / `_PORT` / `_USER` / `_PASSWORD` | unset / 587 | Only read when the backend is `smtp`. |
| `RELAY_SMTP_SECURITY` | follows the port | `starttls`, `ssl` or `none`. See below. |

### The guard: the server refuses to start rather than mail localhost links

`mailer.verify_deployment()` runs from the app lifespan. If a real mail backend
is configured but the links it would send are unusable, the server does not come
up. `deploy.sh` health-checks the restart and can roll back, so the mistake
surfaces at deploy time in front of somebody who can fix it — rather than days
later, in the inbox of a player who cannot.

It refuses to start when the mail backend is not `console` and:

- `RELAY_BASE_URL` is unset, so links would point at `127.0.0.1`.
- `RELAY_BASE_URL` points at this machine: `localhost`, a loopback address, a
  private LAN range, or a `.local` / `.internal` name.
- `RELAY_BASE_URL` has no scheme, or is `http` rather than `https`.
- `RELAY_MAIL_BACKEND=smtp` with no `RELAY_SMTP_HOST`.
- `RELAY_SMTP_SECURITY=none` with a password set, which would put credentials
  on the wire in clear text.

The console backend is exempt from every one of these. Nothing it produces
leaves the machine, so a localhost link in a developer's terminal is correct
rather than broken, and the dev experience is unchanged.

`deploy.sh` runs the same check as a preflight, in the environment systemd
would actually give the service, **before** restarting anything. A bad config
therefore fails while the old server is still serving.

### Mail in development

The console backend prints the whole message, link included, to the server log.
That is the intended local workflow: sign up, read the link out of the terminal
you already have `./run.sh` in, paste it into the browser. No provider, no
inbox, no waiting.

`backend/mailer.py` attaches its own stdout handler for this. Do not remove it:
uvicorn configures only the `uvicorn.*` loggers and the root logger defaults to
WARNING, so without it the mail is silently dropped and the console backend is
useless. That bug was real and was caught in a smoke test, not by the suite.

### Connection modes

`RELAY_SMTP_SECURITY` picks how the SMTP connection is encrypted. Left unset it
follows the port, which is right for almost everyone:

| Port | Mode | Used by |
| --- | --- | --- |
| 587 | `starttls` | Submission. Gmail, SendGrid, Mailgun, most providers. |
| 465 | `ssl` | Implicit TLS, encrypted from the first byte. |
| any | `none` | A local catcher (Mailpit, MailHog). Never inferred; ask for it by name. |

Getting 587 and 465 the wrong way round is the most common reason a correct set
of credentials refuses to send, because the symptom is a hang rather than an
error. `none` refuses to run with a password set.

Certificates are verified. A self-signed or expired certificate fails the send
rather than being quietly accepted, and that is worth keeping.

### Turning on real mail, and why credentials are not enough

Set `RELAY_MAIL_BACKEND=smtp` and the SMTP variables. An API provider (Resend,
Postmark, SendGrid) would be a third backend beside `_send_smtp` — one function
taking the same three arguments, with nothing in `backend/auth.py` moving.

**Credentials alone do not get mail delivered.** Sending straight from an
application server was tried against Gmail and refused outright:

```
550 5.7.26 Gmail requires all senders to authenticate with either SPF or DKIM
           DKIM = did not pass
           SPF [relay.local] = did not pass
```

So the domain in `RELAY_MAIL_FROM` needs, in its DNS:

- **SPF** — a TXT record naming whoever sends on your behalf.
- **DKIM** — the signing key the provider gives you.
- **DMARC** — a TXT record at `_dmarc.<domain>`, even just `v=DMARC1; p=none`.

The quickest route is a transactional provider: make an account, verify the
domain by pasting in the DNS records they hand you, and use their SMTP
credentials. Sending direct from the app server will not work — a residential
or cloud IP with no PTR record is refused or filed as spam whatever the headers
say.

One earlier failure is worth knowing about, because it is fixed and must stay
fixed: `EmailMessage` does not add a `Message-ID`, and neither does
`smtplib.send_message`. Gmail rejects a message without one outright
(`550 5.7.1 Messages missing a valid Message-ID header are not accepted`).
`_build_message` sets `Date` and `Message-ID`, and
`test_message_has_date_and_message_id` keeps it that way.

## Gotchas

**Never let a test open the real database.** `backend/main.py` connects from
the app lifespan, so any test building a `TestClient` would otherwise create
`var/relay.db` in the working tree and write to it — the same file the dev
server uses. `tests/conftest.py` sets `RELAY_DB_PATH=:memory:` for the whole
session to make that impossible. It sets the environment variable rather than
just opening a connection because suites that want clean rows close the
connection between cases, and every reconnect after that would fall back to the
file.

**The throttles and the lockout live in memory.** One counter per address, lost
on restart. They exist to blunt a refresh loop and a password-guessing script,
not to survive a botnet. With more than one worker they stop meaning much.

**One worker.** `accounts.py` uses a single connection behind a lock and runs
its queries on the event loop, which is fine for tiny tables and a single
uvicorn worker. A second worker means WAL, a connection pool, and moving these
calls to `asyncio.to_thread`.

## Deliberately not built

Not oversights, just not asked for: OAuth and social sign-in, changing your
email or password while signed in, deleting an account, admin user management,
account-to-seat binding so a signed-in player reclaims their seat automatically
(the identity is there, nothing consumes it yet), and any profile or stats
history.
