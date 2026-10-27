# Deploying The Relay

Everything needed to put the site on a server and keep it there, in order.
Written to be followed top to bottom on a fresh machine by a person or an
agent with a shell on it. Each step says what to run, what it should print,
and what breaks if it is skipped.

The site is one Python process: FastAPI under uvicorn, serving the games hub,
The Relay, Guess Who / Roll Call and Bullshit Quiz, their pages, and their
WebSockets. Accounts and a few game stores are SQLite files. Everything else
(every match, room and draft in progress) lives in the process's memory.

Before anything else, two rules from [CLAUDE.md](../CLAUDE.md) that apply on
the server too:

- **Never mail the Roll Call roster** (`roster.json`) unless the repository
  owner says so in the conversation where it happens:
  [ROLL_CALL_DO_NOT_SEND.md](ROLL_CALL_DO_NOT_SEND.md). Nothing in this
  runbook sends mail to it, and no step should be improvised that would.
- **Never commit the roster or any secret.** The environment file lives in
  `/etc`, outside every checkout.

---

## 0. The shape of it

| | |
| --- | --- |
| Service | `relay-game.service`, user `relay`, uvicorn on `127.0.0.1:8000`, **one worker** |
| Code that is served | `/srv/relay-game`, a git checkout of `main`, owned by `relay` |
| Code the tests run in | `/root/relay-game`, a second checkout (optional; `deploy.sh` uses it) |
| Settings and secrets | `/etc/relay-game.env`, root only, read by systemd |
| Data | `/var/lib/relay/`: `relay.db` (accounts), `bullshit.db`, `rollcall.db` |
| Front door | Caddy (or nginx) on 80/443, TLS, proxying to port 8000, WebSockets included |
| Health check | `GET /api/config` returns 200 |

What a restart keeps and loses:

| Survives a restart | Lost on a restart |
| --- | --- |
| Accounts and sessions | Every Relay match in progress |
| Bullshit Quiz seats, the question bank, the tags, the join code, the round count | A Bullshit Quiz game in progress (the lobby comes back intact) |
| Roll Call registrations | The Guess Who room |

So: **never restart while a game is on.** `deploy.sh` counts live
connections and asks first.

## 1. What the server needs

- Linux with systemd (Ubuntu 22.04+ or Debian 12+ are what this was written against).
- Python **3.11 or newer** with `venv`, and git.
- A domain (or subdomain) whose DNS A/AAAA record points at the server.
- Ports 80 and 443 open to the internet. Port 8000 must **not** be.
- Very little else: the app is light. 1 CPU and 1 GB of RAM is plenty for a
  game night of 30.

## 2. First-time setup

Run as root (or prefix each command with `sudo`).

### 2.1 Packages

```bash
apt update
apt install -y python3 python3-venv python3-pip git sqlite3 debian-keyring debian-archive-keyring apt-transport-https curl
python3 --version          # must say 3.11 or newer
```

Caddy, for HTTPS. It is in Debian 12+ and Ubuntu 24.04+ as `apt install caddy`;
otherwise use the official repository from
<https://caddyserver.com/docs/install#debian-ubuntu-raspbian>.

### 2.2 The service user and the data folder

```bash
useradd --system --home-dir /srv/relay-game --shell /usr/sbin/nologin relay
install -d -m 750 -o relay -g relay /var/lib/relay
```

### 2.3 The code

```bash
git clone https://github.com/Einsi3dler/relay-game.git /srv/relay-game
chown -R relay:relay /srv/relay-game
runuser -u relay -- python3 -m venv /srv/relay-game/.venv
runuser -u relay -- /srv/relay-game/.venv/bin/pip install -q -e /srv/relay-game
```

The served checkout gets the runtime dependencies only. The tests run in a
second checkout, which `deploy.sh` uses as its gate before it touches the
served one:

```bash
git clone https://github.com/Einsi3dler/relay-game.git /root/relay-game
python3 -m venv /root/relay-game/.venv
/root/relay-game/.venv/bin/pip install -q -e "/root/relay-game[test]"
cd /root/relay-game && .venv/bin/python3 -m pytest -q      # all pass, one skipped
```

### 2.4 Settings and secrets

```bash
install -m 600 -o root -g root /srv/relay-game/deploy/relay-game.env.example /etc/relay-game.env
python3 -c "import secrets; print(secrets.token_urlsafe(24))"     # run once per key
nano /etc/relay-game.env
```

Fill in the four host keys, each with its own fresh value, and leave the
database paths as they are. The template explains every line, and section 9
below lists them all. Decide now about mail (section 6): **for a first night,
leave `RELAY_BASE_URL` and every mail line commented out.** Setting the base
URL without working mail makes the server refuse to start, on purpose.

### 2.5 The service

```bash
cp /srv/relay-game/deploy/relay-game.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now relay-game.service
systemctl status relay-game.service --no-pager      # active (running)
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/config   # 200
```

If it is not running, `journalctl -u relay-game -n 50 --no-pager` says why;
section 8 lists the usual reasons.

### 2.6 HTTPS in front

With Caddy:

```bash
cp /srv/relay-game/deploy/Caddyfile.example /etc/caddy/Caddyfile
nano /etc/caddy/Caddyfile        # replace relay.example.com with the real domain
systemctl reload caddy
curl -fsS -o /dev/null -w '%{http_code}\n' https://YOUR.DOMAIN/api/config     # 200
```

Caddy gets and renews the certificate itself and proxies WebSockets with no
extra settings. With nginx instead, the parts that matter are the upgrade
headers and a long read timeout, because a game holds its socket open for
hours:

```nginx
map $http_upgrade $connection_upgrade { default upgrade; '' close; }

server {
    server_name YOUR.DOMAIN;
    # listen 443 ssl; plus certificates, e.g. from certbot --nginx
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 1h;
        proxy_send_timeout 1h;
    }
}
```

### 2.7 Firewall

```bash
ufw allow OpenSSH
ufw allow 80,443/tcp
ufw enable
```

Do not open 8000. The app trusts forwarded headers from localhost only.

### 2.8 Prove it

```bash
/srv/relay-game/.venv/bin/python -m backend.deploycheck --unit relay-game.service
```

Run it from `/srv/relay-game` as root. It reads the unit's real environment,
changes nothing, and must end with **Ready.** (or **Ready, with N
warning(s).** if you skipped mail on purpose). Any **FAIL** line says what
to fix. Then open, from a phone on mobile data rather than the server's own
network:

- `https://YOUR.DOMAIN/`: the games hub.
- `https://YOUR.DOMAIN/signup`: make an account, sign in.
- `https://YOUR.DOMAIN/bullshithost`: asks for the host key; the right key
  opens the lobby with a join code.

## 3. An existing install with data inside the checkout

Older installs kept the databases in `/srv/relay-game/var/`. That works, but
a fresh clone or a `git clean -x` deletes every account, and the hardened unit
cannot write there. Move them once, between game nights:

```bash
systemctl stop relay-game.service
install -d -m 750 -o relay -g relay /var/lib/relay
for db in relay bullshit rollcall; do
  [ -f /srv/relay-game/var/$db.db ] && sqlite3 /srv/relay-game/var/$db.db ".backup '/var/lib/relay/$db.db'"
done
chown relay:relay /var/lib/relay/*.db
# then set RELAY_DB_PATH, RELAY_BULLSHIT_DB and RELAY_ROLLCALL_DB in /etc/relay-game.env
systemctl start relay-game.service
```

`.backup` makes a consistent copy even of a file that was in use. Keep the old
files until you have signed in on the moved database, then delete them. If
the service's current unit sets its environment some other way (an
`Environment=` line, or a different file), `systemctl cat relay-game.service`
shows it; move those settings into `/etc/relay-game.env` so there is one
place to look.

The Roll Call roster can stay at `var/rollcall/roster.json` or move to
`/var/lib/relay/rollcall/roster.json` with `RELAY_ROLLCALL_ROSTER` set. Either
way: `chmod 600`, owned by `relay`, never committed.

## 4. Deploying a new version

From the test checkout, as root:

```bash
cd /root/relay-game && ./deploy.sh
```

It pulls `main` into the test checkout and runs the whole test suite. Only if
that passes does it fast-forward `/srv/relay-game`, reinstall dependencies if
`pyproject.toml` changed, check the mail settings, and run the deployment
check. It then counts live connections, asks before restarting, and
health-checks the result. Useful flags: `--yes` (don't ask), `--skip-tests`,
`--rollback-on-fail` (go back to the previous commit if the restart is not
healthy), `--help`.

When not to deploy: while anybody is playing. The restart drops every game in
progress (section 0). Between games is fine, and so is the day before a
Bullshit Quiz night: the lobby, the question bank and the join code all come
back.

## 5. Running a Bullshit Quiz night on this server

The full host guide is [BULLSHIT_QUIZ.md](BULLSHIT_QUIZ.md), section 0.1. The
parts that touch the server:

- `RELAY_BULLSHIT_HOST_KEY` must be set (section 2.4). The host page shows
  every answer during the review, so this key matters more than it looks.
- **The day before:** open `https://YOUR.DOMAIN/bullshithost`, set **Rounds
  tonight**, copy the link and share it. Players make accounts and write their
  three questions whenever suits them. Review and tag the bank once everyone
  is done. A deploy or a reboot in between changes nothing: the code, the
  rounds and every question are in `bullshit.db`.
- **On the night:** don't deploy. Start the game, run the draft, play. If the
  server does restart mid-game, the lobby and the bank come back but the game
  does not; **Start the game** again deals a fresh draw.
- **Afterwards:** **New game night** on the host page clears the seats and
  questions and makes a new code, so last week's link stops working.
- Bullshit Quiz never sends mail, to anyone.

## 6. Mail (optional)

Without mail the site works: people sign up with a username and password, and
email is optional. What mail adds is email verification, sign-in links and
password resets. What it costs is a sending domain with SPF and DKIM, usually
through a provider such as Resend, Postmark or Mailgun.

To turn it on, set together in `/etc/relay-game.env`: `RELAY_BASE_URL` (the
public https address), `RELAY_MAIL_BACKEND=smtp`, `RELAY_MAIL_FROM`, and the
`RELAY_SMTP_*` lines. Then:

```bash
cd /srv/relay-game
set -a; . /etc/relay-game.env; set +a          # this shell only
.venv/bin/python -m backend.mailcheck --send you@example.com
systemctl restart relay-game.service
```

`mailcheck` checks the settings and the DNS records and sends one real email.
Deliverability (SPF, DKIM, DMARC) is explained at the end of
[.env.example](../.env.example), and accounts in [ACCOUNTS.md](ACCOUNTS.md).
Remember the roster rule at the top: configuring mail must not become a
reason to mail anybody on it.

## 7. Backups

The three SQLite files are the whole state worth keeping. A nightly copy:

```bash
install -d -m 700 /var/backups/relay
cat > /etc/cron.d/relay-backup <<'CRON'
15 4 * * * root for db in relay bullshit rollcall; do [ -f /var/lib/relay/$db.db ] && sqlite3 /var/lib/relay/$db.db ".backup '/var/backups/relay/$db-$(date +\%F).db'"; done; find /var/backups/relay -name '*.db' -mtime +14 -delete
CRON
```

To restore: stop the service, copy a backup over the live file, `chown
relay:relay` it, start the service. Copy the backups off the machine too if
losing the server would hurt.

## 8. When something is wrong

| Symptom | Likely cause and fix |
| --- | --- |
| Service will not start; the log says "Mail configuration will not work as deployed" | `RELAY_BASE_URL` is set but mail is not (or the reverse). Section 6, or comment `RELAY_BASE_URL` out. |
| Service will not start; "unable to open database file" or "read-only file system" | A database path points inside the checkout, or `/var/lib/relay` is not owned by `relay`. Sections 2.2 and 3. |
| Pages load but games never connect, or reconnect in a loop | The proxy is not passing WebSockets. Caddy does it by itself; nginx needs the upgrade headers in 2.6. |
| Players drop out after a minute of quiet | Proxy read timeout too short (nginx's default is 60 s). `proxy_read_timeout 1h`. |
| Anybody can open `/bullshithost`, `/quizhost` or `/god` with "dev" | A host key is unset. Section 2.4, then `systemctl restart relay-game`. |
| Signed in, then signed out again on the next page | Mixed http and https: open the site by its https address only. |
| A Bullshit Quiz link from yesterday says the code is wrong | Somebody pressed **New game night**. Share the new code from the host page. |
| `deploy.sh` says a checkout has uncommitted changes | Someone edited files on the server. Commit them through a pull request or revert them; never edit the served checkout by hand. |

Logs: `journalctl -u relay-game -f` follows the server; tracebacks land there.

## 9. Every setting

All are read from the environment, which for the service means
`/etc/relay-game.env`. Defaults are what the app uses when a variable is unset.

| Variable | Needed | Default | What it does |
| --- | --- | --- | --- |
| `RELAY_BULLSHIT_HOST_KEY` | **yes** | `dev` | Key to `/bullshithost` (Bullshit Quiz host, review included). |
| `RELAY_QUIZ_HOST_KEY` | **yes** | `dev` | Key to `/quizhost` (Guess Who, Roll Call). |
| `RELAY_GOD_KEY` | **yes** | `dev` | Key to `/god`, which controls real Relay matches ([GOD_MODE.md](GOD_MODE.md)). |
| `RELAY_PREVIEW_KEY` | yes | `dev` | Key to `/preview`, the design gallery of dummy matches. |
| `RELAY_DB_PATH` | **yes** | `var/relay.db` in the checkout | Accounts and sessions. |
| `RELAY_BULLSHIT_DB` | **yes** | `var/bullshit.db` in the checkout | Bullshit Quiz seats, question bank, join code, round count. |
| `RELAY_ROLLCALL_DB` | yes | `var/rollcall.db` in the checkout | Roll Call registrations. |
| `RELAY_ROLLCALL_ROSTER` | only for Roll Call | `var/rollcall/roster.json` in the checkout | The roster of real people. chmod 600. Never mailed without the owner's word. |
| `RELAY_BASE_URL` | with mail | unset | The public https address. Builds emailed links and marks cookies Secure. Set it only together with mail. |
| `RELAY_MAIL_BACKEND` | with mail | `console` (prints to the log) | `smtp` to send. |
| `RELAY_MAIL_FROM` | with mail | | The sender, e.g. `"The Relay <no-reply@your.domain>"`. |
| `RELAY_SMTP_HOST`, `_PORT`, `_USER`, `_PASSWORD` | with mail | port 587 | The provider's SMTP credentials. |
| `RELAY_SMTP_SECURITY` | rarely | follows the port | `starttls` (587), `ssl` (465), or `none` for a local catcher only. |

Game tuning (round counts, timers, perk points, the question bank's weights)
is not environment: it lives in `backend/config.py` and ships with the code.
