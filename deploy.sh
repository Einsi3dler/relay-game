#!/usr/bin/env bash
# deploy.sh — pull the latest main into both checkouts, prove it with the test
# suite, and restart the live server.
#
#   sudo ./deploy.sh                 # the normal run
#   sudo ./deploy.sh --yes           # don't ask, even if people are connected
#   sudo ./deploy.sh --skip-tests    # you already ran them
#   sudo ./deploy.sh --force         # restart even with nothing new to pull
#   sudo ./deploy.sh --rollback-on-fail   # undo the deploy if it won't come up
#
# There are two checkouts on this box and they are not the same thing:
#
#   /root/relay-game   the dev tree you edit and run the tests in
#   /srv/relay-game    what relay-game.service actually serves, owned by `relay`
#
# Restarting wipes every match in flight — state lives in memory
# (backend/state.py) and the unit runs a single worker on purpose — so this
# counts live connections and asks before pulling the rug.
set -Eeuo pipefail

# This script lives inside the repo it updates, so a pull can rewrite the file
# while bash is still reading it. Re-exec from a private copy first.
if [[ "${RELAY_DEPLOY_REEXEC:-}" != "1" ]]; then
  _copy="$(mktemp /tmp/relay-deploy.XXXXXX.sh)"
  cat "${BASH_SOURCE[0]}" >"$_copy"
  chmod +x "$_copy"
  RELAY_DEPLOY_REEXEC=1 exec "$_copy" "$@"
fi
trap '[[ "$0" == /tmp/relay-deploy.* ]] && rm -f "$0"' EXIT

# --- knobs (override with env vars if the layout ever moves) ----------------
DEV_DIR="${RELAY_DEV_DIR:-/root/relay-game}"
SRV_DIR="${RELAY_SRV_DIR:-/srv/relay-game}"
SRV_USER="${RELAY_SRV_USER:-relay}"
SERVICE="${RELAY_SERVICE:-relay-game.service}"
BRANCH="${RELAY_BRANCH:-main}"
PORT="${RELAY_PORT:-8000}"
HEALTH_URL="${RELAY_HEALTH_URL:-http://127.0.0.1:${PORT}/api/config}"
HEALTH_TRIES="${RELAY_HEALTH_TRIES:-30}"

SKIP_TESTS=0; ASSUME_YES=0; FORCE=0; ROLLBACK=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-tests)       SKIP_TESTS=1 ;;
    -y|--yes)           ASSUME_YES=1 ;;
    --force)            FORCE=1 ;;
    --rollback-on-fail) ROLLBACK=1 ;;
    -h|--help)          awk 'NR>1 && /^#/{sub(/^# ?/,""); print; next} NR>1{exit}' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done

if [[ -t 1 ]]; then
  B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'
else
  B=""; G=""; Y=""; R=""; N=""
fi
step() { printf '\n%s==> %s%s\n' "$B" "$*" "$N"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '%s !! %s%s\n' "$Y" "$*" "$N" >&2; }
die()  { printf '%s !! %s%s\n' "$R" "$*" "$N" >&2; exit 1; }

# git in a given checkout, as a given user ("-" means as ourselves). The server
# tree is owned by `relay`, and running git there as root both trips the
# dubious-ownership guard and leaves root-owned objects behind.
git_in() {
  local dir="$1" user="$2"; shift 2
  if [[ "$user" == "-" ]]; then
    git -C "$dir" "$@"
  else
    runuser -u "$user" -- git -C "$dir" "$@"
  fi
}

# --- preflight --------------------------------------------------------------
step "Preflight"
[[ $EUID -eq 0 ]] || die "run this with sudo — it restarts $SERVICE and touches $SRV_DIR"
systemctl cat "$SERVICE" >/dev/null 2>&1 || die "no such unit: $SERVICE"
[[ -d "$SRV_DIR/.git" ]] || die "$SRV_DIR is not a git checkout"
if [[ ! -d "$DEV_DIR/.git" ]]; then
  (( SKIP_TESTS )) || die "$DEV_DIR is missing — pass --skip-tests to deploy without running them"
  DEV_DIR=""
fi
info "service: $SERVICE   branch: $BRANCH"
info "dev:     ${DEV_DIR:-<none>}"
info "serving: $SRV_DIR (as $SRV_USER)"

# Refuse to move a checkout that has uncommitted work in it — a pull would
# either fail halfway or bury someone's edit.
check_clean() {
  local dir="$1" user="$2" label="$3" branch untracked
  # Only *tracked* edits block a fast-forward. Untracked scratch files are
  # noted and stepped over — git refuses loudly on its own if one of them is
  # actually in the way of an incoming file.
  [[ -z "$(git_in "$dir" "$user" status --porcelain --untracked-files=no)" ]] \
    || die "$label ($dir) has uncommitted changes to tracked files — commit, stash or revert them first"
  untracked="$(git_in "$dir" "$user" ls-files --others --exclude-standard | wc -l)"
  (( untracked == 0 )) || warn "$label has $untracked untracked file(s) — leaving them alone"
  branch="$(git_in "$dir" "$user" rev-parse --abbrev-ref HEAD)"
  [[ "$branch" == "$BRANCH" ]] \
    || die "$label ($dir) is on '$branch', not '$BRANCH' — switch it before deploying"
}

# Fetch and fast-forward. Prints the old SHA on stdout so the caller can diff.
fast_forward() {
  local dir="$1" user="$2" before
  before="$(git_in "$dir" "$user" rev-parse HEAD)"
  git_in "$dir" "$user" fetch --quiet origin "$BRANCH"
  # --ff-only on purpose: a deploy tree must never grow a merge commit of its own.
  git_in "$dir" "$user" merge --quiet --ff-only "origin/$BRANCH"
  printf '%s' "$before"
}

# --- 1. dev checkout + tests -------------------------------------------------
if [[ -n "$DEV_DIR" ]]; then
  step "Updating dev checkout"
  check_clean "$DEV_DIR" - "dev checkout"
  dev_before="$(fast_forward "$DEV_DIR" -)"
  dev_after="$(git_in "$DEV_DIR" - rev-parse HEAD)"
  if [[ "$dev_before" == "$dev_after" ]]; then
    info "already at $(git_in "$DEV_DIR" - rev-parse --short HEAD) — nothing new"
  else
    git_in "$DEV_DIR" - --no-pager log --oneline "$dev_before..$dev_after" | sed 's/^/    /'
  fi

  # Deps first: the tests import them, and a new release may have moved a pin.
  if ! git_in "$DEV_DIR" - diff --quiet "$dev_before" "$dev_after" -- pyproject.toml; then
    step "pyproject.toml changed — reinstalling dev deps"
    "$DEV_DIR/.venv/bin/python3" -m pip install -q -e "$DEV_DIR[test]"
  fi
fi

if (( SKIP_TESTS )); then
  warn "skipping the test suite (--skip-tests)"
else
  step "Running the test suite"
  # The gate: nothing reaches the server until this passes.
  ( cd "$DEV_DIR" && "$DEV_DIR/.venv/bin/python3" -m pytest -q ) \
    || die "tests failed — the server was NOT touched"
fi

# --- 2. what would change on the server -------------------------------------
step "Checking the server checkout"
check_clean "$SRV_DIR" "$SRV_USER" "server checkout"
git_in "$SRV_DIR" "$SRV_USER" fetch --quiet origin "$BRANCH"
srv_before="$(git_in "$SRV_DIR" "$SRV_USER" rev-parse HEAD)"
srv_target="$(git_in "$SRV_DIR" "$SRV_USER" rev-parse "origin/$BRANCH")"

if [[ "$srv_before" == "$srv_target" ]]; then
  if (( FORCE )); then
    warn "server already at $(git_in "$SRV_DIR" "$SRV_USER" rev-parse --short HEAD) — restarting anyway (--force)"
  else
    info "server already at $(git_in "$SRV_DIR" "$SRV_USER" rev-parse --short HEAD) — nothing to deploy"
    info "pass --force to restart it regardless"
    exit 0
  fi
else
  info "$(git_in "$SRV_DIR" "$SRV_USER" rev-parse --short "$srv_before") -> $(git_in "$SRV_DIR" "$SRV_USER" rev-parse --short "$srv_target")"
  git_in "$SRV_DIR" "$SRV_USER" --no-pager log --oneline "$srv_before..$srv_target" | sed 's/^/    /'
fi

# --- 3. who's playing right now ---------------------------------------------
live="$(ss -tn state established "( sport = :$PORT )" 2>/dev/null | tail -n +2 | wc -l)"
step "Live connections on port $PORT: $live"
if (( live > 0 )); then
  warn "restarting drops those sockets and every match in memory with them"
fi
if (( ! ASSUME_YES )); then
  read -r -p "    Restart $SERVICE now? [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || die "aborted — the server checkout is unchanged"
fi

# --- 4. move the server checkout --------------------------------------------
step "Updating $SRV_DIR"
git_in "$SRV_DIR" "$SRV_USER" merge --quiet --ff-only "origin/$BRANCH"
srv_after="$(git_in "$SRV_DIR" "$SRV_USER" rev-parse HEAD)"
info "now at $(git_in "$SRV_DIR" "$SRV_USER" rev-parse --short HEAD)"

# Runtime deps only here — the server has no business carrying pytest.
if ! git_in "$SRV_DIR" "$SRV_USER" diff --quiet "$srv_before" "$srv_after" -- pyproject.toml; then
  step "pyproject.toml changed — reinstalling server deps"
  runuser -u "$SRV_USER" -- "$SRV_DIR/.venv/bin/python3" -m pip install -q -e "$SRV_DIR"
fi

# --- 5. restart and prove it came back --------------------------------------
step "Restarting $SERVICE"
systemctl restart "$SERVICE"

healthy=0
for (( i = 1; i <= HEALTH_TRIES; i++ )); do
  if systemctl is-active --quiet "$SERVICE" \
     && [[ "$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 3 "$HEALTH_URL" 2>/dev/null || true)" == "200" ]]; then
    healthy=1
    info "healthy after ${i}s — $HEALTH_URL returned 200"
    break
  fi
  sleep 1
done

if (( ! healthy )); then
  warn "$SERVICE did not come back healthy"
  journalctl -u "$SERVICE" -n 40 --no-pager || true
  if (( ROLLBACK )); then
    step "Rolling back to $(git_in "$SRV_DIR" "$SRV_USER" rev-parse --short "$srv_before")"
    git_in "$SRV_DIR" "$SRV_USER" reset --hard --quiet "$srv_before"
    systemctl restart "$SERVICE"
    warn "rolled back — investigate before deploying again"
  else
    warn "to undo:  sudo runuser -u $SRV_USER -- git -C $SRV_DIR reset --hard $srv_before && sudo systemctl restart $SERVICE"
  fi
  exit 1
fi

step "${G}Deployed${N}"
info "$(git_in "$SRV_DIR" "$SRV_USER" --no-pager log -1 --format='%h %s')"
