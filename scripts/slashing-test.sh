#!/usr/bin/env bash
# Live test of x/slashing on a throwaway 4-validator network:
#   1. stop validator 3 -> after the signed-blocks window it is jailed (chain keeps going on 3/4);
#   2. restart it, wait for the downtime jail period, send `tx slashing unjail`;
#   3. it is bonded again and signs blocks.
# Uses a short window (10 blocks, 50%) and a 60 s jail (the SDK minimum) so the run takes ~2 minutes.
# Ports: PORT_BASE (default 40000) + node*100.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
BIN=${AURIXD:-$ROOT/chain/build/aurixd}
BASE=${PORT_BASE:-40000}
NET=$(mktemp -d /tmp/aurix-slash-XXXX)
PIDS=()
cleanup() { for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done; wait 2>/dev/null || true; }
trap cleanup EXIT

[[ -x "$BIN" ]] || (cd "$ROOT/chain" && go build -o "$BIN" ./cmd/aurixd)

AURIXD=$BIN AURIX_NET_HOME=$NET VALIDATORS=4 PORT_STEP=100 \
  RPC_PORT=$((BASE + 57)) P2P_PORT=$((BASE + 56)) REST_PORT=$((BASE + 17)) GRPC_PORT=$((BASE + 90)) \
  PROM_PORT=$((BASE + 60)) SIGNED_BLOCKS_WINDOW=10 MIN_SIGNED=0.5 DOWNTIME_JAIL=60s \
  "$ROOT/chain/scripts/init-localnet.sh" >/dev/null

start() { "$BIN" start --home "$NET/node$1" >>"$NET/node$1.log" 2>&1 & PIDS[$1]=$!; }
for i in 0 1 2 3; do start $i; done

NODE="tcp://127.0.0.1:$((BASE + 57))"
q() { "$BIN" q "$@" --node "$NODE" -o json; }
height() { curl -fsS "http://127.0.0.1:$((BASE + 57))/status" | jq -r .result.sync_info.latest_block_height; }
VAL3=$("$BIN" keys show val3 --bech val -a --keyring-backend test --home "$NET/node3")
jailed() { q staking validator "$VAL3" | jq -r '.validator.jailed // false'; }
wait_for() { # seconds description cmd...
  local t=$1 d=$2; shift 2
  for ((s = 0; s < t; s++)); do if "$@"; then return 0; fi; sleep 1; done
  echo "FAIL: timed out waiting for $d" >&2; exit 1
}

wait_for 60 "first blocks" bash -c "[[ \$(curl -fsS http://127.0.0.1:$((BASE + 57))/status 2>/dev/null | jq -r .result.sync_info.latest_block_height) -ge 3 ]]"
echo "network up at height $(height); validator 3 = $VAL3"
[[ $(jailed) == false ]] || { echo "FAIL: validator 3 jailed at start" >&2; exit 1; }

kill "${PIDS[3]}"; echo ">> validator 3 stopped at height $(height)"
wait_for 90 "validator 3 to be jailed" bash -c "[[ \$('$BIN' q staking validator $VAL3 --node $NODE -o json | jq -r .validator.jailed) == true ]]"
echo "OK: validator 3 jailed at height $(height) (status $(q staking validator "$VAL3" | jq -r .validator.status))"
# Liveness with 3/4: a new block within 20 s. Not 4 s: right after the validator-set change a round
# whose proposer is the stopped validator waits for timeout_propose (3 s, +0.5 s per round).
h=$(height); t0=$SECONDS
wait_for 20 "a new block with 3/4 validators (chain halted?)" bash -c "[[ \$(curl -fsS http://127.0.0.1:$((BASE + 57))/status | jq -r .result.sync_info.latest_block_height) -gt $h ]]"
echo "OK: chain still producing blocks with 3/4 validators (next block after $((SECONDS - t0)) s)"

start 3; echo ">> validator 3 restarted, waiting for the 60 s jail period"
sleep 62
"$BIN" tx slashing unjail --from val3 --keyring-backend test --home "$NET/node3" \
  --chain-id aurix-testnet-1 --node "$NODE" --fees 2000uaur -y -o json >"$NET/unjail.json"
[[ $(jq -r .code "$NET/unjail.json") == 0 ]] || { cat "$NET/unjail.json"; echo "FAIL: unjail rejected" >&2; exit 1; }
wait_for 30 "validator 3 to be unjailed" bash -c "[[ \$('$BIN' q staking validator $VAL3 --node $NODE -o json | jq -r '.validator.jailed // false') == false ]]"
wait_for 30 "validator 3 to be bonded" bash -c "[[ \$('$BIN' q staking validator $VAL3 --node $NODE -o json | jq -r .validator.status) == BOND_STATUS_BONDED ]]"
echo "OK: validator 3 unjailed and bonded again at height $(height)"
METRICS=$(curl -fsS "http://127.0.0.1:$((BASE + 60))/metrics")
grep -q '^cometbft_consensus_height' <<<"$METRICS" || { echo "FAIL: no CometBFT metrics" >&2; exit 1; }
echo "OK: Prometheus metrics served on :$((BASE + 60))"
echo "PASS"
