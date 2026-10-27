"""`python -m backend.deploycheck` — is this server ready for real people?

Run it ON THE SERVER, with the service's environment, since that is the
configuration that matters. `--unit` reads it straight from systemd (the
unit's Environment= and EnvironmentFile=), which needs root because the
environment file should be readable by root alone:

    sudo /srv/relay-game/.venv/bin/python -m backend.deploycheck --unit relay-game.service

deploy.sh runs exactly that before every restart and prints what it finds.
The full runbook is docs/DEPLOY.md.

It only reads. It opens no database, sends no mail and changes nothing, so it
is safe to run against a live server at any time. What it checks:

  * the host keys are set and are not the public defaults (the repository is
    public, so "dev" opens every host door to anybody who reads it);
  * RELAY_BASE_URL and the mail settings would let the server start at all
    (mailer.configuration_problems: the same rules it enforces on boot);
  * the databases live outside the checkout, where a fresh clone or a
    `git clean -x` cannot delete them, and their folders are writable;
  * the Roll Call roster, if there is one, is readable by its owner alone;
  * the unit runs one worker and no --reload: every match and room lives in
    the process's memory, so a second worker would split them in two.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from backend import config

REPO = Path(__file__).resolve().parent.parent

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
if not sys.stdout.isatty():
    GREEN = RED = YELLOW = DIM = RESET = ""

# Values that ship in this repository: in the code, in .env.example, or in
# the deploy templates. Any of them on a server is as good as no key.
PUBLIC_VALUES = {"", "dev", "change-me", "1234567890", "replace-me"}
SHORT_KEY = 16

# (variable, door, what it opens, how bad a public value is)
KEYS = (
    ("RELAY_BULLSHIT_HOST_KEY", "/bullshithost",
     "runs Bullshit Quiz, including the review that shows every answer", FAIL),
    ("RELAY_QUIZ_HOST_KEY", "/quizhost", "runs Guess Who and Roll Call", FAIL),
    ("RELAY_GOD_KEY", "/god", "controls real Relay matches", FAIL),
    ("RELAY_PREVIEW_KEY", "/preview", "shows the design gallery of dummy matches", WARN),
)

# (variable, default relative to the repo, what is in it)
DATABASES = (
    ("RELAY_DB_PATH", config.ACCOUNTS_DB_PATH, "every account"),
    ("RELAY_BULLSHIT_DB", "var/bullshit.db",
     "Bullshit Quiz seats, the question bank, the join code"),
    ("RELAY_ROLLCALL_DB", "var/rollcall.db", "Roll Call registrations"),
)

ROSTER = ("RELAY_ROLLCALL_ROSTER", "var/rollcall/roster.json")


@dataclass(frozen=True)
class Finding:
    level: str
    message: str
    fix: str = ""


# --- the checks -----------------------------------------------------------

def check_python() -> list[Finding]:
    if sys.version_info < (3, 11):
        return [Finding(FAIL, f"Python {sys.version.split()[0]}; The Relay needs 3.11 or newer.",
                        "Install python3.11 (or newer) and rebuild the venv.")]
    return [Finding(PASS, f"Python {sys.version.split()[0]}")]


def check_keys(env: dict[str, str]) -> list[Finding]:
    found: list[Finding] = []
    seen: dict[str, str] = {}
    for name, door, opens, level in KEYS:
        value = env.get(name, "").strip()
        if value in PUBLIC_VALUES:
            state = "unset, so it is \"dev\"" if not value else f"the public value {value!r}"
            found.append(Finding(level, f"{name} is {state}. {door} {opens}.",
                                 f"Set {name} to a long random string in the service's "
                                 "environment file (docs/DEPLOY.md shows how to make one)."))
            continue
        if len(value) < SHORT_KEY:
            found.append(Finding(WARN, f"{name} is only {len(value)} characters.",
                                 f"Use at least {SHORT_KEY}; it is a password for {door}."))
        else:
            found.append(Finding(PASS, f"{name} is set"))
        if value in seen:
            found.append(Finding(WARN, f"{name} is the same as {seen[value]}.",
                                 "Give every door its own key, so one leaked key opens one door."))
        seen.setdefault(value, name)
    return found


def check_base_url_and_mail(env: dict[str, str]) -> list[Finding]:
    from backend import mailer            # reads os.environ, which main() has set
    problems = mailer.configuration_problems()
    found = [Finding(FAIL, problem, "The server refuses to start like this. See docs/DEPLOY.md, "
                                   "section 6, and `python -m backend.mailcheck`.")
             for problem in problems]
    if problems:
        return found
    base = env.get("RELAY_BASE_URL", "").strip()
    if not base:
        return [Finding(WARN, "RELAY_BASE_URL is unset: session cookies are not marked Secure, "
                              "and nobody can get a forgotten password reset.",
                        "Fine for a first night with no mail. For the full setup, set it to "
                        "https://your.domain and configure mail (docs/DEPLOY.md, section 6).")]
    backend = env.get("RELAY_MAIL_BACKEND", "console").strip() or "console"
    return [Finding(PASS, f"RELAY_BASE_URL is {base}, mail backend {backend}")]


def _resolve(value: str, default: str) -> Path:
    path = Path(value or default)
    return path if path.is_absolute() else REPO / path


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def _writable(folder: Path) -> bool:
    """Whether the folder (or the nearest one that exists, which the server
    would create it in) can be written to by whoever runs this."""
    while not folder.exists() and folder != folder.parent:
        folder = folder.parent
    return os.access(folder, os.W_OK)


def check_databases(env: dict[str, str], as_root: bool) -> list[Finding]:
    found: list[Finding] = []
    for name, default, what in DATABASES:
        path = _resolve(env.get(name, "").strip(), default)
        if _inside(path, REPO):
            found.append(Finding(WARN, f"{name}: {path} is inside the checkout ({what}).",
                                 "A fresh clone or `git clean -x` deletes it. Point it at "
                                 "/var/lib/relay/ (docs/DEPLOY.md, section 3)."))
        elif not as_root and not _writable(path.parent):
            found.append(Finding(FAIL, f"{name}: cannot write to {path.parent}.",
                                 "Create the folder and give it to the service user: "
                                 "chown relay:relay /var/lib/relay"))
        else:
            found.append(Finding(PASS, f"{name}: {path}"))
    return found


def check_roster(env: dict[str, str]) -> list[Finding]:
    name, default = ROSTER
    path = _resolve(env.get(name, "").strip(), default)
    if not path.exists():
        return []
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        return [Finding(WARN, f"The Roll Call roster at {path} is readable by others "
                              f"(mode {mode:o}). It holds real email addresses.",
                        f"chmod 600 {path}")]
    return [Finding(PASS, "The Roll Call roster is readable by its owner only")]


def check_exec_start(exec_start: str) -> list[Finding]:
    """The unit's ExecStart, as `systemctl show -p ExecStart` prints it."""
    if not exec_start:
        return []
    found: list[Finding] = []
    workers = re.search(r"--workers[ =](\d+)", exec_start)
    if workers and int(workers.group(1)) > 1:
        found.append(Finding(FAIL, f"The unit starts {workers.group(1)} workers.",
                             "Use one: every match and room is in memory, and two workers "
                             "would each have half of them."))
    if "--reload" in exec_start:
        found.append(Finding(WARN, "The unit runs uvicorn with --reload.",
                             "That is for development. It restarts the server, and wipes "
                             "every game in progress, whenever a file changes."))
    if "uvicorn" not in exec_start:
        found.append(Finding(WARN, "The unit's ExecStart does not mention uvicorn.",
                             "See deploy/relay-game.service for the expected command."))
    if not found:
        found.append(Finding(PASS, "The unit runs one uvicorn worker, no --reload"))
    return found


def findings(env: dict[str, str], exec_start: str = "", as_root: bool = False) -> list[Finding]:
    return (check_python() + check_keys(env) + check_base_url_and_mail(env)
            + check_databases(env, as_root) + check_roster(env) + check_exec_start(exec_start))


# --- reading the unit -----------------------------------------------------

def parse_environment(environment: str, files: list[str]) -> dict[str, str]:
    """The unit's environment from `systemctl show -p Environment` (one
    shell-quoted line) and the text of each EnvironmentFile, later winning."""
    env: dict[str, str] = {}
    for item in shlex.split(environment):
        if "=" in item:
            key, value = item.split("=", 1)
            env[key] = value
    for text in files:
        for raw in text.splitlines():
            raw = raw.strip()
            if raw and not raw.startswith("#") and "=" in raw:
                key, value = raw.split("=", 1)
                env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _systemctl_show(unit: str, prop: str) -> str:
    done = subprocess.run(["systemctl", "show", unit, "-p", prop, "--value"],
                          capture_output=True, text=True)
    return done.stdout.strip()


def unit_environment(unit: str) -> tuple[dict[str, str], str]:
    """(environment, ExecStart) of a systemd unit."""
    files = []
    for line in _systemctl_show(unit, "EnvironmentFiles").splitlines():
        path = line.split(" (")[0].strip()
        if path and os.path.isfile(path):
            files.append(Path(path).read_text())
    return (parse_environment(_systemctl_show(unit, "Environment"), files),
            _systemctl_show(unit, "ExecStart"))


# --- output ---------------------------------------------------------------

def report(found: list[Finding]) -> int:
    colour = {PASS: GREEN, WARN: YELLOW, FAIL: RED}
    for f in found:
        print(f"  {colour[f.level]}{f.level}{RESET}  {f.message}")
        if f.fix and f.level != PASS:
            print(f"        {DIM}{f.fix}{RESET}")
    fails = sum(1 for f in found if f.level == FAIL)
    warns = sum(1 for f in found if f.level == WARN)
    print()
    if fails:
        print(f"{RED}{fails} problem(s){RESET} to fix before real people use this server"
              + (f", and {warns} warning(s)." if warns else "."))
    elif warns:
        print(f"{YELLOW}Ready, with {warns} warning(s).{RESET} See docs/DEPLOY.md.")
    else:
        print(f"{GREEN}Ready.{RESET}")
    return 1 if fails else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check this server's configuration before real people use it.")
    parser.add_argument("--unit", help="read the environment and ExecStart from this "
                                       "systemd unit (needs root), e.g. relay-game.service")
    args = parser.parse_args()

    exec_start = ""
    env = dict(os.environ)
    if args.unit:
        unit_env, exec_start = unit_environment(args.unit)
        env = unit_env
        # The mail rules read os.environ, so check with the unit's, not ours.
        for key in [k for k in os.environ if k.startswith("RELAY_")]:
            del os.environ[key]
        os.environ.update(unit_env)

    as_root = hasattr(os, "geteuid") and os.geteuid() == 0
    print(f"{DIM}The Relay: deployment check"
          + (f" ({args.unit})" if args.unit else " (this shell's environment)")
          + (". As root, so folder permissions are left to the service's own start,"
             " which deploy.sh health-checks." if as_root else "") + RESET)
    return report(findings(env, exec_start, as_root))


if __name__ == "__main__":
    sys.exit(main())
