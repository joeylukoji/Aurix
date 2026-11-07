#!/usr/bin/env bash
# Runs every test suite, then the browser journey against a fresh stack on offset ports.
# PORT_OFFSET (default 10000) picks the e2e stack ports; RUN_DIR defaults to a temp dir.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
export PORT_OFFSET=${PORT_OFFSET:-10000}
export RUN_DIR=${RUN_DIR:-$(mktemp -d /tmp/aurix-test-XXXX)}
export AURIX_HOME=${AURIX_HOME:-$RUN_DIR/chain-home}
export SHOT_DIR=${SHOT_DIR:-$RUN_DIR/shots}; mkdir -p "$SHOT_DIR"
NAMES=(); RESULTS=()
run() { # name cmd...
  local name=$1; shift; echo "=== $name"
  if "$@" >"$RUN_DIR/test-$name.log" 2>&1; then RESULTS+=(PASS); else RESULTS+=(FAIL); tail -30 "$RUN_DIR/test-$name.log"; fi
  NAMES+=("$name"); echo "--- $name: ${RESULTS[-1]}"
}
run chain-go   bash -c "cd '$ROOT/chain' && go test ./..."
run guardian   bash -c "cd '$ROOT/guardian' && .venv/bin/python -m pytest -q"
run paypal-sim bash -c "cd '$ROOT/paypal-sim' && .venv/bin/python -m pytest -q"
run gateway    bash -c "cd '$ROOT/gateway' && .venv/bin/pip install -q -r requirements-dev.txt && .venv/bin/python -m pytest -q"
run dev-up     "$ROOT/scripts/dev-up.sh"
if [[ ${RESULTS[-1]} == PASS ]]; then
  run e2e env BASE_URL="http://localhost:$((8080+PORT_OFFSET))" node "$ROOT/tests/e2e/demo-journey.js"
fi
"$ROOT/scripts/dev-down.sh" >/dev/null
echo; printf '%-12s %s\n' SUITE RESULT; FAIL=0
for i in "${!NAMES[@]}"; do printf '%-12s %s\n' "${NAMES[$i]}" "${RESULTS[$i]}"; [[ ${RESULTS[$i]} == PASS ]] || FAIL=1; done
echo "logs: $RUN_DIR"; exit $FAIL
