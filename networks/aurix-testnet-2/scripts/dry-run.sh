#!/usr/bin/env bash
# Dry-run of the aurix-testnet-2 genesis ceremony with 4 simulated operators on one machine,
# on a throwaway chain id. Runs every real script end to end:
#   init + keys -> coordinator-pregenesis -> operator-gentx x4 -> coordinator-finalize
#   -> operator-start x4 (distinct ports) -> wait for blocks -> stop.
# Env: AURIXD (default chain/build/aurixd), DRY_CHAIN_ID (aurix-dryrun-<epoch>), WORK (mktemp),
#      BASE_PORT (36000), TARGET_HEIGHT (5), KEEP=1 keeps the work dir.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
export AURIXD=${AURIXD:-$ROOT/chain/build/aurixd}
[[ -x "$AURIXD" ]] || { echo "build first: cd chain && go build -o build/aurixd ./cmd/aurixd" >&2; exit 1; }
export CHAIN_ID=${DRY_CHAIN_ID:-aurix-dryrun-$(date +%s)}
WORK=${WORK:-$(mktemp -d -t aurix-dryrun.XXXXXX)}
BASE=${BASE_PORT:-36000}
TARGET=${TARGET_HEIGHT:-5}
N=4
PIDS=()
cleanup() {
  for p in "${PIDS[@]:-}"; do [[ -n "$p" ]] && kill "$p" 2>/dev/null || true; done
  wait 2>/dev/null || true
  if [[ "${KEEP:-0}" == 1 ]]; then echo "work dir kept: $WORK"; else rm -rf "$WORK"; fi
}
trap cleanup EXIT
echo "== dry-run chain $CHAIN_ID in $WORK"

port() { echo $((BASE + $1 * 10 + $2)); }   # node i, slot: 0 p2p 1 rpc 2 rest 3 grpc 4 prom

# 1. Each operator: init + key (test keyring is acceptable ONLY for this throwaway chain).
echo "moniker,address" > "$WORK/operators.csv"
for ((i = 0; i < N; i++)); do
  h="$WORK/op$i"
  $AURIXD init "dry-op$i" --chain-id "$CHAIN_ID" --default-denom uaur --home "$h" >/dev/null 2>&1
  $AURIXD keys add validator --keyring-backend test --home "$h" >/dev/null 2>&1
  echo "dry-op$i,$($AURIXD keys show validator -a --keyring-backend test --home "$h")" >> "$WORK/operators.csv"
done
: > "$WORK/anchor_signers.txt"
for s in gateway attestor1 attestor2; do $AURIXD seed-address "dryrun-$s" >> "$WORK/anchor_signers.txt"; done

# 2. Coordinator: pre-genesis (genesis_time slightly in the future).
export COORD_HOME="$WORK/coordinator"
GENESIS_TIME=$(date -u -d '+15 seconds' +%Y-%m-%dT%H:%M:%SZ) \
FAUCET_ADDR=$($AURIXD seed-address dryrun-faucet) \
SIGNED_BLOCKS_WINDOW=100 MIN_SIGNED=0.5 \
  "$HERE/coordinator-pregenesis.sh" "$WORK/operators.csv" "$WORK/anchor_signers.txt" "$WORK/pub"
PRE_SHA=$(awk '{print $1}' "$WORK/pub/pregenesis.json.sha256")
jq -e '.app_state.smartmoney.params.anchor_signers | length == 3' "$WORK/pub/pregenesis.json" >/dev/null

# 3. Operators: verify hash + gentx.
mkdir -p "$WORK/gentx"
: > "$WORK/peers.txt"
for ((i = 0; i < N; i++)); do
  NODE_HOME="$WORK/op$i" MONIKER="dry-op$i" KEYRING_BACKEND=test ALLOW_TEST_KEYRING=1 OUT_DIR="$WORK/gentx" \
    "$HERE/operator-gentx.sh" "$WORK/pub/pregenesis.json" "$PRE_SHA" >/dev/null
  echo "persistent $($AURIXD comet show-node-id --home "$WORK/op$i")@127.0.0.1:$(port $i 0)" >> "$WORK/peers.txt"
done
# a tampered pre-genesis must be rejected
if NODE_HOME="$WORK/op0" MONIKER=x KEYRING_BACKEND=test ALLOW_TEST_KEYRING=1 OUT_DIR="$WORK/tmp" \
   "$HERE/operator-gentx.sh" "$WORK/pub/pregenesis.json" deadbeef >/dev/null 2>&1; then
  echo "FAIL: wrong hash accepted" >&2; exit 1
fi

# 4. Coordinator: finalize.
PREGENESIS_SHA256=$PRE_SHA "$HERE/coordinator-finalize.sh" "$WORK/gentx" "$WORK/pub"
FINAL_SHA=$(awk '{print $1}' "$WORK/pub/genesis.json.sha256")

# 5. Operators: verify final hash, configure and start on distinct ports.
for ((i = 0; i < N; i++)); do
  NODE_HOME="$WORK/op$i" LOCAL_TEST=1 \
  P2P_LADDR="tcp://127.0.0.1:$(port $i 0)" RPC_LADDR="tcp://127.0.0.1:$(port $i 1)" \
  API_ADDR="tcp://127.0.0.1:$(port $i 2)" GRPC_ADDR="127.0.0.1:$(port $i 3)" PROM_LADDR="127.0.0.1:$(port $i 4)" \
    "$HERE/operator-start.sh" "$WORK/pub/genesis.json" "$FINAL_SHA" "$WORK/peers.txt" > "$WORK/node$i.log" 2>&1 &
  PIDS+=($!)
done

# 6. Wait for TARGET blocks on every node.
height() { curl -s --max-time 2 "http://127.0.0.1:$(port $1 1)/status" | jq -r '.result.sync_info.latest_block_height // 0' 2>/dev/null || echo 0; }
deadline=$((SECONDS + 120))
while :; do
  ok=1; hs=()
  for ((i = 0; i < N; i++)); do h=$(height $i); h=${h:-0}; hs+=("$h"); (( h >= TARGET )) || ok=0; done
  (( ok )) && break
  if (( SECONDS > deadline )); then
    echo "FAIL: heights ${hs[*]} after 120s" >&2; tail -n 20 "$WORK"/node*.log >&2; exit 1
  fi
  sleep 2
done
vals=$(curl -s "http://127.0.0.1:$(port 0 1)/validators" | jq -r '.result.total')
metrics=$(curl -s --max-time 5 "http://127.0.0.1:$(port 0 4)/metrics" || true)
grep -q '^cometbft_consensus_height' <<<"$metrics" || { echo "FAIL: prometheus metrics not served" >&2; exit 1; }
echo "== heights ${hs[*]}, $vals validators, metrics OK"
echo "== DRY-RUN PASSED (pregenesis $PRE_SHA, genesis $FINAL_SHA)"
