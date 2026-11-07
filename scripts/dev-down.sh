#!/usr/bin/env bash
# Stops only the processes started by scripts/dev-up.sh (PIDs in RUN_DIR).
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RUN_DIR=${RUN_DIR:-$ROOT/.run}
for name in gateway paypal-sim guardian chain; do
  f="$RUN_DIR/$name.pid"; [[ -f "$f" ]] || continue
  pid=$(cat "$f")
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"; for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
    kill -9 "$pid" 2>/dev/null || true; echo "[dev-down] stopped $name ($pid)"
  fi
  rm -f "$f"
done
