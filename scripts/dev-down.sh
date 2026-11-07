#!/usr/bin/env bash
# Stops only the processes started by scripts/dev-up.sh (PIDs in RUN_DIR).
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RUN_DIR=${RUN_DIR:-$ROOT/.run}
shopt -s nullglob
for f in "$RUN_DIR"/attestor*.pid "$RUN_DIR"/gateway.pid "$RUN_DIR"/webstatic.pid "$RUN_DIR"/paypal-sim.pid "$RUN_DIR"/guardian.pid "$RUN_DIR"/chain*.pid; do
  [[ -f "$f" ]] || continue
  name=$(basename "$f" .pid)
  pid=$(cat "$f")
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"; for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
    kill -9 "$pid" 2>/dev/null || true; echo "[dev-down] stopped $name ($pid)"
  fi
  rm -f "$f"
done
