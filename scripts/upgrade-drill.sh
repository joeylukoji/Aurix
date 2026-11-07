#!/usr/bin/env bash
# Upgrade drill on a throwaway 4-validator network (docs/governance.md, workstream 4c):
#   1. start the network with an "old" binary (same code, but the v2 upgrade handler disabled at
#      link time via -X ...app.DisabledUpgrades=v2, i.e. a binary that does not know "v2");
#   2. write some state (bank send to a fresh address), propose software-upgrade "v2" at height H,
#      all validators vote yes, the proposal passes;
#   3. at H every node halts with `UPGRADE "v2" NEEDED`;
#   4. stop the nodes, restart them with the new binary (chain/build/aurixd, registers v2);
#   5. the chain produces blocks past H, `q upgrade applied v2` = H and the state is preserved.
# Ports: PORT_BASE (default 44000) + node*100.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
BIN=${AURIXD:-$ROOT/chain/build/aurixd}
BASE=${PORT_BASE:-44000}
CHAIN_ID=aurix-testnet-1
NET=$(mktemp -d /tmp/aurix-upg-XXXX)
OLD=$NET/aurixd-old
PIDS=()
cleanup() { for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done; wait 2>/dev/null || true; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

(cd "$ROOT/chain" && go build -o "$BIN" ./cmd/aurixd &&
  go build -ldflags "-X github.com/joeylukoji/aurix/chain/app.DisabledUpgrades=v2" -o "$OLD" ./cmd/aurixd)

AURIXD=$OLD AURIX_NET_HOME=$NET VALIDATORS=4 PORT_STEP=100 \
  RPC_PORT=$((BASE + 57)) P2P_PORT=$((BASE + 56)) REST_PORT=$((BASE + 17)) GRPC_PORT=$((BASE + 90)) \
  PROM_PORT=$((BASE + 60)) VOTING_PERIOD=20s EXPEDITED_VOTING_PERIOD=10s \
  "$ROOT/chain/scripts/init-localnet.sh" >/dev/null

start() { "$1" start --home "$NET/node$2" >>"$NET/node$2.log" 2>&1 & PIDS[$2]=$!; }
for i in 0 1 2 3; do start "$OLD" $i; done

NODE="tcp://127.0.0.1:$((BASE + 57))"
q() { "$BIN" q "$@" --node "$NODE" -o json; }
height() { curl -fsS "http://127.0.0.1:$((BASE + 57))/status" | jq -r .result.sync_info.latest_block_height; }
tx() { # node-index args...  (wait for inclusion, print the tx result)
  local i=$1; shift
  local out; out=$("$BIN" tx "$@" --from "val$i" --keyring-backend test --home "$NET/node$i" \
    --chain-id "$CHAIN_ID" --node "$NODE" --fees 5000uaur --gas 400000 -y -o json)
  [[ $(jq -r .code <<<"$out") == 0 ]] || { echo "$out" >&2; return 1; }
  local hash; hash=$(jq -r .txhash <<<"$out")
  for _ in $(seq 30); do
    if r=$("$BIN" q tx "$hash" --node "$NODE" -o json 2>/dev/null); then
      [[ $(jq -r .code <<<"$r") == 0 ]] || { echo "$r" >&2; return 1; }
      echo "$r"; return 0
    fi; sleep 1
  done; echo "tx $hash not included" >&2; return 1
}
wait_for() { # seconds description cmd...
  local t=$1 d=$2; shift 2
  for ((s = 0; s < t; s++)); do if "$@"; then return 0; fi; sleep 1; done
  fail "timed out waiting for $d"
}
wait_for 60 "first blocks" bash -c "[[ \$(curl -fsS http://127.0.0.1:$((BASE + 57))/status 2>/dev/null | jq -r .result.sync_info.latest_block_height) -ge 2 ]]"
echo "network up at height $(height) (old binary, no v2 handler)"

# state written before the upgrade
RCPT=$("$BIN" seed-address "upgrade-drill-recipient")
tx 0 bank send val0 "$RCPT" 123456uaur >/dev/null
BAL=$(q bank balances "$RCPT" | jq -r '.balances[] | select(.denom=="uaur") | .amount')
[[ $BAL == 123456 ]] || fail "pre-upgrade balance $BAL"
PARAMS=$(q smartmoney params | jq -c .params)
echo "OK: pre-upgrade state: $RCPT has ${BAL}uaur; smartmoney params $PARAMS"

# upgrade height: voting (20 s) + margin, measured in blocks, at least 25 blocks ahead
h0=$(height); sleep 5; rate=$(( ($(height) - h0 + 4) / 5 )); ((rate >= 1)) || rate=1
H=$(( $(height) + (rate * 40 > 25 ? rate * 40 : 25) ))
echo ">> proposing upgrade v2 at height $H (current $(height), ~$rate blocks/s)"
tx 0 upgrade software-upgrade v2 --upgrade-height "$H" --upgrade-info '{}' --no-validate \
  --title "Upgrade to v2" --summary "Upgrade drill" --deposit 1000000000uaur >"$NET/prop.tx"
P=$(jq -r '.events[] | select(.type=="submit_proposal") | .attributes[] | select(.key=="proposal_id") | .value' "$NET/prop.tx" | head -1)
[[ -n $P ]] || fail "no proposal id"
for i in 0 1 2 3; do tx $i gov vote "$P" yes >/dev/null; done
wait_for 40 "proposal $P to finish" bash -c "[[ \$('$BIN' q gov proposal $P --node $NODE -o json | jq -r .proposal.status) != PROPOSAL_STATUS_VOTING_PERIOD ]]"
[[ $(q gov proposal "$P" | jq -r .proposal.status) == PROPOSAL_STATUS_PASSED ]] || fail "upgrade proposal not passed"
[[ $(q upgrade plan | jq -r .plan.name) == v2 ]] || fail "no upgrade plan"
(( $(height) < H )) || fail "upgrade height $H already reached before the proposal passed"
echo "OK: proposal $P PASSED, plan v2 at $H"

wait_for 300 "UPGRADE \"v2\" NEEDED in all node logs" bash -c \
  "for i in 0 1 2 3; do grep -q 'UPGRADE \"v2\" NEEDED at height: $H' $NET/node\$i.log || exit 1; done"
sleep 3
HALT=$(height 2>/dev/null || echo "?")
sleep 4
[[ $(height 2>/dev/null || echo "?") == "$HALT" ]] || fail "chain did not halt"
echo "OK: chain halted at height $HALT, all nodes log UPGRADE \"v2\" NEEDED at height: $H"

for i in 0 1 2 3; do kill "${PIDS[$i]}" 2>/dev/null || true; done; wait 2>/dev/null || true
for i in 0 1 2 3; do echo "---- restart with new binary" >>"$NET/node$i.log"; start "$BIN" $i; done
echo ">> nodes restarted with the new binary"
wait_for 90 "blocks past $H" bash -c "[[ \$(curl -fsS http://127.0.0.1:$((BASE + 57))/status 2>/dev/null | jq -r .result.sync_info.latest_block_height) -ge $((H + 3)) ]]"
APPLIED=$(q upgrade applied v2 | jq -r '.height')
[[ $APPLIED == "$H" ]] || fail "q upgrade applied v2 = $APPLIED (expected $H)"
[[ $(q bank balances "$RCPT" | jq -r '.balances[] | select(.denom=="uaur") | .amount') == 123456 ]] || fail "balance lost"
[[ $(q smartmoney params | jq -c .params) == "$PARAMS" ]] || fail "smartmoney params changed"
tx 1 bank send val1 "$RCPT" 1uaur >/dev/null || fail "tx after upgrade rejected"
echo "OK: chain at height $(height) > $H, upgrade v2 applied at $APPLIED, state preserved, txs accepted"
echo "PASS"
