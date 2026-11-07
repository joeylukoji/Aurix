#!/usr/bin/env bash
# Starts the full AURIX stack without Docker: aurixd chain, guardian, paypal-sim, gateway.
# Ports: set PORT_OFFSET (added to defaults) or each port explicitly:
#   RPC_PORT(26657) REST_PORT(1317) GRPC_PORT(9090) P2P_PORT(26656) PROM_PORT(26660, metrics)
#   GUARDIAN_PORT(8000) SIM_PORT(8090) GATEWAY_PORT(8080)
# VALIDATORS (default 4): number of validator nodes; node i uses the ports above + i*VALIDATOR_PORT_STEP
# (default 1000). The gateway talks to node0. AURIX_NET_HOME (default $RUN_DIR/chain-net),
# RUN_DIR (pids+logs, default $ROOT/.run).
# ATTESTORS (default 0): extra independent PayPal attestors (attestor/). Attestor i (1..N) uses seed
# "aurix-dev-attestor-i" (override ATTESTOR_SEED_PREFIX), talks to node i (mod VALIDATORS), serves
# /health on ATTESTOR_PORT(8095)+i-1. ANCHOR_THRESHOLD (default 1) = attestations needed per proof.
# GUARDIANS (default 1): Neural Guardian instances. Guardian i (1..N) signs scores with dev seed
# "aurix-dev-guardian-i" (override GUARDIAN_SEED_PREFIX; guardian 1 keeps the built-in dev key when N=1)
# and listens on GUARDIAN_PORT+i-1. The gateway gets GUARDIAN_URLS (all of them) and GUARDIAN_QUORUM
# (default: N). PENDING_TTL_BLOCKS: passed to the genesis (pending proof expiry).
# Stop with scripts/dev-down.sh (same RUN_DIR).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OFF=${PORT_OFFSET:-0}
RPC_PORT=${RPC_PORT:-$((26657+OFF))}; REST_PORT=${REST_PORT:-$((1317+OFF))}
GRPC_PORT=${GRPC_PORT:-$((9090+OFF))}; P2P_PORT=${P2P_PORT:-$((26656+OFF))}; PROM_PORT=${PROM_PORT:-$((26660+OFF))}
GUARDIAN_PORT=${GUARDIAN_PORT:-$((8000+OFF))}; SIM_PORT=${SIM_PORT:-$((8090+OFF))}
GATEWAY_PORT=${GATEWAY_PORT:-$((8080+OFF))}
ATTESTORS=${ATTESTORS:-0}; ATTESTOR_PORT=${ATTESTOR_PORT:-$((8095+OFF))}
ATTESTOR_SEED_PREFIX=${ATTESTOR_SEED_PREFIX:-aurix-dev-attestor-}
GUARDIANS=${GUARDIANS:-1}; GUARDIAN_SEED_PREFIX=${GUARDIAN_SEED_PREFIX:-aurix-dev-guardian-}
GUARDIAN_QUORUM=${GUARDIAN_QUORUM:-$GUARDIANS}
RUN_DIR=${RUN_DIR:-$ROOT/.run}; mkdir -p "$RUN_DIR"
export AURIX_NET_HOME=${AURIX_NET_HOME:-$RUN_DIR/chain-net}
VALIDATORS=${VALIDATORS:-4}; STEP=${VALIDATOR_PORT_STEP:-1000}
BIN=$ROOT/chain/build/aurixd

log() { echo "[dev-up] $*"; }
port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
PORTS=($SIM_PORT $GATEWAY_PORT)
for ((i = 0; i < GUARDIANS; i++)); do PORTS+=($((GUARDIAN_PORT + i))); done
for ((i = 0; i < ATTESTORS; i++)); do PORTS+=($((ATTESTOR_PORT + i))); done
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
venv() { # dir — (re)installs requirements when requirements.txt changed since the last install
  if [[ ! -x "$1/.venv/bin/uvicorn" ]]; then log "creating venv in $1"; python3 -m venv "$1/.venv"; fi
  if [[ ! -f "$1/.venv/.req-stamp" || "$1/requirements.txt" -nt "$1/.venv/.req-stamp" ]]; then
    "$1/.venv/bin/pip" install -q -r "$1/requirements.txt" && touch "$1/.venv/.req-stamp"
  fi
}

log "building aurixd"; (cd "$ROOT/chain" && go build -o build/aurixd ./cmd/aurixd)
SEEDS=""; for ((i = 1; i <= ATTESTORS; i++)); do SEEDS+="${SEEDS:+ }$ATTESTOR_SEED_PREFIX$i"; done
INIT_ENV=()
if ((ATTESTORS > 0)); then INIT_ENV+=(ATTESTOR_SEEDS="$SEEDS"); fi
if [[ -n "${ANCHOR_THRESHOLD:-}" ]]; then INIT_ENV+=(ANCHOR_THRESHOLD="$ANCHOR_THRESHOLD"); fi
if [[ -n "${PENDING_TTL_BLOCKS:-}" ]]; then INIT_ENV+=(PENDING_TTL_BLOCKS="$PENDING_TTL_BLOCKS"); fi
env "${INIT_ENV[@]}" AURIXD=$BIN VALIDATORS=$VALIDATORS PORT_STEP=$STEP RPC_PORT=$RPC_PORT REST_PORT=$REST_PORT \
  GRPC_PORT=$GRPC_PORT P2P_PORT=$P2P_PORT PROM_PORT=$PROM_PORT "$ROOT/chain/scripts/init-localnet.sh"
for d in guardian paypal-sim gateway; do venv "$ROOT/$d"; done
if ((ATTESTORS > 0)) && ! "$ROOT/attestor/.venv/bin/python" -c "import httpx, cryptography, Crypto" 2>/dev/null; then
  log "creating venv in $ROOT/attestor"; python3 -m venv "$ROOT/attestor/.venv"
  "$ROOT/attestor/.venv/bin/pip" install -q -r "$ROOT/attestor/requirements.txt"
fi

for ((i = 0; i < VALIDATORS; i++)); do  # ports and peers are written in each node's config
  start "chain$i" "$ROOT/chain" "$BIN" start --home "$AURIX_NET_HOME/node$i"
done
GUARDIAN_URLS=""; GENESIS_CHAIN_ID=$(jq -r .chain_id "$AURIX_NET_HOME/node0/config/genesis.json")
for ((i = 1; i <= GUARDIANS; i++)); do
  gname=guardian; ((i > 1)) && gname=guardian$i
  gseed=(); ((GUARDIANS > 1)) && gseed=(GUARDIAN_SEED="$GUARDIAN_SEED_PREFIX$i")
  start "$gname" "$ROOT/guardian" env "${gseed[@]}" GUARDIAN_CHAIN_ID="$GENESIS_CHAIN_ID" .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$((GUARDIAN_PORT + i - 1))"
  GUARDIAN_URLS+="${GUARDIAN_URLS:+,}http://127.0.0.1:$((GUARDIAN_PORT + i - 1))"
done
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
for ((i = 1; i <= GUARDIANS; i++)); do
  gname=guardian; ((i > 1)) && gname=guardian$i
  wait_for "$gname" "http://127.0.0.1:$((GUARDIAN_PORT + i - 1))/health"
done
wait_for paypal-sim "http://127.0.0.1:$SIM_PORT/sim/health"

CHAIN_RPC_URL="http://127.0.0.1:$RPC_PORT" CHAIN_REST_URL="http://127.0.0.1:$REST_PORT" \
GUARDIAN_URLS="$GUARDIAN_URLS" GUARDIAN_QUORUM=$GUARDIAN_QUORUM DATABASE_URL="sqlite:///$RUN_DIR/gateway.db" \
PAYPAL_ENV=simulator PAYPAL_BASE_URL="http://127.0.0.1:$SIM_PORT" PAYPAL_CLIENT_ID=sim-client-id \
PAYPAL_CLIENT_SECRET=sim-client-secret PAYPAL_WEBHOOK_ID=SIM-WEBHOOK-ID \
SIM_WEBHOOK_URL="http://127.0.0.1:$GATEWAY_PORT/api/paypal/webhook" SIM_PUBLIC_URL="http://localhost:$SIM_PORT" \
  start gateway "$ROOT/gateway" .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$GATEWAY_PORT"
wait_for gateway "http://127.0.0.1:$GATEWAY_PORT/api/health"
CHAIN_ID=$(curl -fsS "http://127.0.0.1:$RPC_PORT/status" | jq -r .result.node_info.network)
for ((i = 1; i <= ATTESTORS; i++)); do
  n=$((i % VALIDATORS))
  ATTESTOR_SEED="$ATTESTOR_SEED_PREFIX$i" CHAIN_REST="http://127.0.0.1:$((REST_PORT + n * STEP))" CHAIN_ID=$CHAIN_ID \
  PAYPAL_BASE_URL="http://127.0.0.1:$SIM_PORT" PAYPAL_CLIENT_ID=sim-client-id PAYPAL_CLIENT_SECRET=sim-client-secret \
  POLL_INTERVAL=${ATTESTOR_POLL_INTERVAL:-1} ATTESTOR_PORT=$((ATTESTOR_PORT + i - 1)) \
    start "attestor$i" "$ROOT/attestor" .venv/bin/python -m attestor
  wait_for "attestor$i" "http://127.0.0.1:$((ATTESTOR_PORT + i - 1))/health"
done
log "AURIX up: http://localhost:$GATEWAY_PORT  (run dir $RUN_DIR)"
