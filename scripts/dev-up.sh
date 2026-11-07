#!/usr/bin/env bash
# Starts the full AURIX stack without Docker: aurixd chain, guardian, paypal-sim, gateway.
# Ports: set PORT_OFFSET (added to defaults) or each port explicitly:
#   RPC_PORT(26657) REST_PORT(1317) GRPC_PORT(9090) P2P_PORT(26656) PROM_PORT(26660, metrics)
#   GUARDIAN_PORT(8000) SIM_PORT(8090) GATEWAY_PORT(8080)
# VALIDATORS (default 4): number of validator nodes; node i uses the ports above + i*VALIDATOR_PORT_STEP
# (default 1000). The gateway talks to node0. AURIX_NET_HOME (default $RUN_DIR/chain-net),
# RUN_DIR (pids+logs, default $ROOT/.run).
# Stop with scripts/dev-down.sh (same RUN_DIR).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OFF=${PORT_OFFSET:-0}
RPC_PORT=${RPC_PORT:-$((26657+OFF))}; REST_PORT=${REST_PORT:-$((1317+OFF))}
GRPC_PORT=${GRPC_PORT:-$((9090+OFF))}; P2P_PORT=${P2P_PORT:-$((26656+OFF))}; PROM_PORT=${PROM_PORT:-$((26660+OFF))}
GUARDIAN_PORT=${GUARDIAN_PORT:-$((8000+OFF))}; SIM_PORT=${SIM_PORT:-$((8090+OFF))}
GATEWAY_PORT=${GATEWAY_PORT:-$((8080+OFF))}
RUN_DIR=${RUN_DIR:-$ROOT/.run}; mkdir -p "$RUN_DIR"
export AURIX_NET_HOME=${AURIX_NET_HOME:-$RUN_DIR/chain-net}
VALIDATORS=${VALIDATORS:-4}; STEP=${VALIDATOR_PORT_STEP:-1000}
BIN=$ROOT/chain/build/aurixd

log() { echo "[dev-up] $*"; }
port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
PORTS=($GUARDIAN_PORT $SIM_PORT $GATEWAY_PORT)
for ((i = 0; i < VALIDATORS; i++)); do
  PORTS+=($((RPC_PORT + i * STEP)) $((REST_PORT + i * STEP)) $((GRPC_PORT + i * STEP)) $((P2P_PORT + i * STEP)) $((PROM_PORT + i * STEP)))
done
for p in "${PORTS[@]}"; do
  if port_busy "$p"; then echo "[dev-up] port $p already in use (set PORT_OFFSET or *_PORT)" >&2; exit 1; fi
done

start() { # name dir cmd...
  local name=$1 dir=$2; shift 2
  (cd "$dir" && exec "$@") >"$RUN_DIR/$name.log" 2>&1 &
  echo $! >"$RUN_DIR/$name.pid"; log "started $name (pid $!, log $RUN_DIR/$name.log)"
}
wait_for() { # name url [jq-filter]
  local name=$1 url=$2 i
  for i in $(seq 1 120); do
    if curl -fsS "$url" >/dev/null 2>&1; then log "$name healthy"; return 0; fi
    if ! kill -0 "$(cat "$RUN_DIR/$name.pid")" 2>/dev/null; then echo "[dev-up] $name exited; see $RUN_DIR/$name.log" >&2; tail -20 "$RUN_DIR/$name.log" >&2; exit 1; fi
    sleep 0.5
  done
  echo "[dev-up] $name not healthy at $url; see $RUN_DIR/$name.log" >&2; exit 1
}
venv() { # dir
  if [[ ! -x "$1/.venv/bin/uvicorn" ]]; then
    log "creating venv in $1"; python3 -m venv "$1/.venv"; "$1/.venv/bin/pip" install -q -r "$1/requirements.txt"
  fi
}

log "building aurixd"; (cd "$ROOT/chain" && go build -o build/aurixd ./cmd/aurixd)
AURIXD=$BIN VALIDATORS=$VALIDATORS PORT_STEP=$STEP RPC_PORT=$RPC_PORT REST_PORT=$REST_PORT \
  GRPC_PORT=$GRPC_PORT P2P_PORT=$P2P_PORT PROM_PORT=$PROM_PORT "$ROOT/chain/scripts/init-localnet.sh"
for d in guardian paypal-sim gateway; do venv "$ROOT/$d"; done

for ((i = 0; i < VALIDATORS; i++)); do  # ports and peers are written in each node's config
  start "chain$i" "$ROOT/chain" "$BIN" start --home "$AURIX_NET_HOME/node$i"
done
start guardian "$ROOT/guardian" .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$GUARDIAN_PORT"
SIM_WEBHOOK_URL="http://127.0.0.1:$GATEWAY_PORT/api/paypal/webhook" SIM_PUBLIC_URL="http://localhost:$SIM_PORT" \
SIM_CLIENT_ID=sim-client-id SIM_CLIENT_SECRET=sim-client-secret SIM_WEBHOOK_ID=SIM-WEBHOOK-ID \
  start paypal-sim "$ROOT/paypal-sim" .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$SIM_PORT"

wait_for chain0 "http://127.0.0.1:$RPC_PORT/status"
for i in $(seq 1 120); do
  h=$(curl -fsS "http://127.0.0.1:$RPC_PORT/status" | jq -r '.result.sync_info.latest_block_height' 2>/dev/null || echo 0)
  [[ "$h" -ge 2 ]] && curl -fsS "http://127.0.0.1:$REST_PORT/aurix/smartmoney/v1/params" >/dev/null 2>&1 && break
  sleep 0.5
done
[[ "$h" -ge 2 ]] || { echo "[dev-up] chain produces no blocks" >&2; exit 1; }
nv=$(curl -fsS "http://127.0.0.1:$RPC_PORT/validators" | jq -r .result.total)
log "chain at height $h with $nv validators"
wait_for guardian "http://127.0.0.1:$GUARDIAN_PORT/health"
wait_for paypal-sim "http://127.0.0.1:$SIM_PORT/sim/health"

CHAIN_RPC_URL="http://127.0.0.1:$RPC_PORT" CHAIN_REST_URL="http://127.0.0.1:$REST_PORT" \
GUARDIAN_URL="http://127.0.0.1:$GUARDIAN_PORT" DATABASE_URL="sqlite:///$RUN_DIR/gateway.db" \
PAYPAL_ENV=simulator PAYPAL_BASE_URL="http://127.0.0.1:$SIM_PORT" PAYPAL_CLIENT_ID=sim-client-id \
PAYPAL_CLIENT_SECRET=sim-client-secret PAYPAL_WEBHOOK_ID=SIM-WEBHOOK-ID \
SIM_WEBHOOK_URL="http://127.0.0.1:$GATEWAY_PORT/api/paypal/webhook" SIM_PUBLIC_URL="http://localhost:$SIM_PORT" \
  start gateway "$ROOT/gateway" .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$GATEWAY_PORT"
wait_for gateway "http://127.0.0.1:$GATEWAY_PORT/api/health"
log "AURIX up: http://localhost:$GATEWAY_PORT  (run dir $RUN_DIR)"
