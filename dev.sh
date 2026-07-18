#!/usr/bin/env bash
# dev.sh — run `agenttic ui` and auto-restart it whenever backend source or the
# prebuilt UI changes. Dependency-free: no entr/watchexec/node needed, just a
# `find` mtime poll. The server itself has no --reload, so we bounce the whole
# process on change (same effect, works for both Python and ui/dist changes).
#
#   ./dev.sh              # watch + serve (port comes from config.yaml → 8700)
#   POLL=2 ./dev.sh       # slower poll (default 1s)
#
# Ctrl-C stops the server and the watcher together.
set -uo pipefail
cd "$(dirname "$0")"

# Paths whose changes should trigger a restart. Add more as needed.
WATCH=(src/agenttic ui/dist config.yaml)
POLL="${POLL:-1}"
BIN=./.venv/bin/agenttic

server_pid=""

start() {
  # LAN=1 ./dev.sh  → bind 0.0.0.0 so other devices on the network can reach it.
  local lan_flag=""
  [ -n "${LAN:-}" ] && lan_flag="--lan"
  "$BIN" ui $lan_flag &
  server_pid=$!
  echo "· agenttic ui started (pid $server_pid)${lan_flag:+  [LAN — bound to 0.0.0.0]}"
}

stop() {
  [ -n "$server_pid" ] || return 0
  kill "$server_pid" 2>/dev/null || return 0
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$server_pid" 2>/dev/null || return 0
    sleep 0.3
  done
  kill -9 "$server_pid" 2>/dev/null || true
}

cleanup() { echo; echo "· stopping…"; stop; exit 0; }
trap cleanup INT TERM

# Adopt the port: stop any agenttic ui instance we didn't start (e.g. one you
# launched by hand) so we don't collide on 8700.
existing="$(pgrep -f '[a]genttic ui' || true)"
if [ -n "$existing" ]; then
  echo "· stopping existing agenttic ui: $existing"
  # shellcheck disable=SC2086
  kill $existing 2>/dev/null || true
  sleep 1
fi

stamp="$(mktemp)"; touch "$stamp"
echo "· watching: ${WATCH[*]}  (poll ${POLL}s)"
start

while true; do
  sleep "$POLL"
  # Restart the watcher's child if it died on its own (e.g. a crash on bad code).
  if [ -n "$server_pid" ] && ! kill -0 "$server_pid" 2>/dev/null; then
    echo "· server exited — restarting"
    start
    touch "$stamp"
    continue
  fi
  changed="$(find "${WATCH[@]}" -type f -newer "$stamp" \
             -not -name '*.pyc' -not -path '*/__pycache__/*' -print -quit 2>/dev/null)"
  if [ -n "$changed" ]; then
    echo "· change: $changed → restart"
    touch "$stamp"
    stop
    start
  fi
done
