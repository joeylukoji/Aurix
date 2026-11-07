#!/usr/bin/env bash
# Runs every test suite, then the browser journey against a fresh stack on offset ports.
# PORT_OFFSET (default 10000) picks the e2e stack ports; RUN_DIR defaults to a temp dir.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
export PORT_OFFSET=${PORT_OFFSET:-10000}
export RUN_DIR=${RUN_DIR:-$(mktemp -d /tmp/aurix-test-XXXX)}
export AURIX_NET_HOME=${AURIX_NET_HOME:-$RUN_DIR/chain-net}
export SHOT_DIR=${SHOT_DIR:-$RUN_DIR/shots}; mkdir -p "$SHOT_DIR"
NAMES=(); RESULTS=()
run() { # name cmd...
  local name=$1; shift; echo "=== $name"
  if "$@" >"$RUN_DIR/test-$name.log" 2>&1; then RESULTS+=(PASS); else RESULTS+=(FAIL); tail -30 "$RUN_DIR/test-$name.log"; fi
  NAMES+=("$name"); echo "--- $name: ${RESULTS[-1]}"
}
run chain-go   bash -c "cd '$ROOT/chain' && go test ./..."
run slashing "$ROOT/scripts/slashing-test.sh"
run guardian   bash -c "cd '$ROOT/guardian' && .venv/bin/python -m pytest -q"
run paypal-sim bash -c "cd '$ROOT/paypal-sim' && .venv/bin/python -m pytest -q"
run gateway    bash -c "cd '$ROOT/gateway' && .venv/bin/pip install -q -r requirements-dev.txt && .venv/bin/python -m pytest -q"
run attestor   bash -c "cd '$ROOT/attestor' && { [[ -x .venv/bin/python ]] || python3 -m venv .venv; } && .venv/bin/pip install -q -r requirements-dev.txt -r requirements.txt && .venv/bin/python -m pytest -q"
# Chain scripts with their own stacks (each picks non-colliding ports and cleans up after itself).
run attest     env -u PORT_OFFSET -u RUN_DIR -u AURIX_NET_HOME "$ROOT/scripts/attest-test.sh"
for s in gov-test upgrade-drill; do
  [[ -x "$ROOT/scripts/$s.sh" ]] && run "$s" env -u PORT_OFFSET -u RUN_DIR -u AURIX_NET_HOME "$ROOT/scripts/$s.sh"
done
run dev-up     "$ROOT/scripts/dev-up.sh"
if [[ ${RESULTS[-1]} == PASS ]]; then
  NODES="http://localhost:$((1317+PORT_OFFSET)),http://localhost:$((2317+PORT_OFFSET))"
  run e2e env BASE_URL="http://localhost:$((8080+PORT_OFFSET))" NODES="$NODES" node "$ROOT/tests/e2e/demo-journey.js"
  # Destructive (stops the gateway, then node0): must run last on this stack.
  run gateway-off env GATEWAY_URL="http://localhost:$((8080+PORT_OFFSET))" NODES="$NODES" \
    WEB_PORT=$((8070+PORT_OFFSET)) node "$ROOT/tests/e2e/gateway-off.js"
fi
"$ROOT/scripts/dev-down.sh" >/dev/null
echo; printf '%-12s %s\n' SUITE RESULT; FAIL=0
for i in "${!NAMES[@]}"; do printf '%-12s %s\n' "${NAMES[$i]}" "${RESULTS[$i]}"; [[ ${RESULTS[$i]} == PASS ]] || FAIL=1; done
echo "logs: $RUN_DIR"; exit $FAIL
