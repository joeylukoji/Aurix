#!/usr/bin/env bash
# Live test of x/gov on a throwaway 4-validator network (docs/governance.md, workstream 4c):
#   a. a proposal with x/smartmoney MsgUpdateParams adds a 2nd anchor signer and sets
#      anchor_threshold=2; 3 of 4 validators vote yes -> PASSED, params changed;
#   b. a 2nd proposal with only 1 yes (3 no) -> REJECTED, params unchanged;
#   c. MsgUpdateParams signed directly by a normal account (not gov) is rejected.
# Voting period 20 s (expedited 10 s). Ports: PORT_BASE (default 42000) + node*100.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck source=lib-net.sh
source "$ROOT/scripts/lib-net.sh"
BIN=${AURIXD:-$ROOT/chain/build/aurixd}
BASE=${PORT_BASE:-42000}
CHAIN_ID=aurix-testnet-1
NET=$(mktemp -d /tmp/aurix-gov-XXXX)
PIDS=()
cleanup() { for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done; wait 2>/dev/null || true; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

[[ -x "$BIN" ]] || (cd "$ROOT/chain" && go build -o "$BIN" ./cmd/aurixd)

ATT_SEED="gov-test-attestor-2"
AURIXD=$BIN AURIX_NET_HOME=$NET VALIDATORS=4 PORT_STEP=100 \
  RPC_PORT=$((BASE + 57)) P2P_PORT=$((BASE + 56)) REST_PORT=$((BASE + 17)) GRPC_PORT=$((BASE + 90)) \
  PROM_PORT=$((BASE + 60)) VOTING_PERIOD=20s EXPEDITED_VOTING_PERIOD=10s \
  "$ROOT/chain/scripts/init-localnet.sh" >/dev/null

for i in 0 1 2 3; do "$BIN" start --home "$NET/node$i" >>"$NET/node$i.log" 2>&1 & PIDS[$i]=$!; done

NODE="tcp://127.0.0.1:$((BASE + 57))"
q() { "$BIN" q "$@" --node "$NODE" -o json; }
height() { curl -fsS "http://127.0.0.1:$((BASE + 57))/status" | jq -r .result.sync_info.latest_block_height; }
tx() { # node-index args...  (broadcast sync, wait for inclusion, print final tx result json)
  local i=$1; shift
  local out; out=$("$BIN" tx "$@" --from "val$i" --keyring-backend test --home "$NET/node$i" \
    --chain-id "$CHAIN_ID" --node "$NODE" --fees 5000uaur --gas 400000 -y -o json)
  [[ $(jq -r .code <<<"$out") == 0 ]] || { echo "$out" >&2; return 1; }
  local hash; hash=$(jq -r .txhash <<<"$out")
  for _ in $(seq 30); do
    if r=$("$BIN" q tx "$hash" --node "$NODE" -o json 2>/dev/null); then echo "$r"; return 0; fi; sleep 1
  done; echo "tx $hash not included" >&2; return 1
}
wait_for() { # seconds description cmd...
  local t=$1 d=$2; shift 2
  for ((s = 0; s < t; s++)); do if "$@"; then return 0; fi; sleep 1; done
  fail "timed out waiting for $d"
}
wait_first_blocks 2
echo "network up at height $(height)"

GOV=$(q auth module-account gov | jq -r '.account.value.address // .account.base_account.address // .account.address')
[[ $GOV == aur1* ]] || fail "could not read gov module address"
ATT2=$("$BIN" seed-address "$ATT_SEED")
BEFORE=$(q smartmoney params | jq -c .params)
echo "gov authority = $GOV; params before = $BEFORE; new attestor = $ATT2"

# ---- a. proposal that passes (3 yes, 1 no) ----
NEWP=$(jq -c --arg a "$ATT2" '.anchor_signers += [$a] | .anchor_threshold = 2' <<<"$BEFORE")
jq -n --arg gov "$GOV" --argjson p "$NEWP" '{
  messages: [{"@type": "/aurix.smartmoney.v1.MsgUpdateParams", authority: $gov, params: $p}],
  metadata: "", deposit: "1000000000uaur",
  title: "Add a second attestor", summary: "Add attestor 2 to anchor_signers and require 2 attestations",
  expedited: false}' >"$NET/prop1.json"
tx 0 gov submit-proposal "$NET/prop1.json" >"$NET/prop1.tx"
P1=$(jq -r '.events[] | select(.type=="submit_proposal") | .attributes[] | select(.key=="proposal_id") | .value' "$NET/prop1.tx" | head -1)
[[ -n $P1 ]] || fail "no proposal id"
[[ $(q gov proposal "$P1" | jq -r '.proposal.status') == PROPOSAL_STATUS_VOTING_PERIOD ]] || fail "proposal $P1 not in voting period"
echo ">> proposal $P1 in voting period"
for i in 0 1 2; do tx $i gov vote "$P1" yes >/dev/null; done
tx 3 gov vote "$P1" no >/dev/null
echo ">> votes: val0-2 yes, val3 no"
wait_for 40 "proposal $P1 to finish" bash -c "[[ \$('$BIN' q gov proposal $P1 --node $NODE -o json | jq -r .proposal.status) != PROPOSAL_STATUS_VOTING_PERIOD ]]"
S1=$(q gov proposal "$P1" | jq -r .proposal.status)
[[ $S1 == PROPOSAL_STATUS_PASSED ]] || fail "proposal $P1 status $S1 (expected PASSED)"
AFTER=$(q smartmoney params | jq -c .params)
jq -e --arg a "$ATT2" '(.anchor_signers | index($a)) != null and (.anchor_threshold|tostring) == "2"' <<<"$AFTER" >/dev/null ||
  fail "params not updated: $AFTER"
echo "OK: proposal $P1 PASSED, params now $AFTER"

# ---- b. proposal that is rejected (1 yes, 3 no) ----
BADP=$(jq -c '.anchor_threshold = 1' <<<"$AFTER")
jq --argjson p "$BADP" '.messages[0].params = $p | .title = "Lower threshold" | .summary = "Back to threshold 1"' \
  "$NET/prop1.json" >"$NET/prop2.json"
tx 0 gov submit-proposal "$NET/prop2.json" >"$NET/prop2.tx"
P2=$(jq -r '.events[] | select(.type=="submit_proposal") | .attributes[] | select(.key=="proposal_id") | .value' "$NET/prop2.tx" | head -1)
tx 0 gov vote "$P2" yes >/dev/null
for i in 1 2 3; do tx $i gov vote "$P2" no >/dev/null; done
echo ">> proposal $P2: val0 yes, val1-3 no"
wait_for 40 "proposal $P2 to finish" bash -c "[[ \$('$BIN' q gov proposal $P2 --node $NODE -o json | jq -r .proposal.status) != PROPOSAL_STATUS_VOTING_PERIOD ]]"
S2=$(q gov proposal "$P2" | jq -r .proposal.status)
[[ $S2 == PROPOSAL_STATUS_REJECTED ]] || fail "proposal $P2 status $S2 (expected REJECTED)"
[[ $(q smartmoney params | jq -c .params) == "$AFTER" ]] || fail "params changed by rejected proposal"
echo "OK: proposal $P2 REJECTED, params unchanged"

# ---- c. direct MsgUpdateParams from a normal account ----
VAL0=$("$BIN" keys show val0 -a --keyring-backend test --home "$NET/node0")
jq -n --arg a "$VAL0" --argjson p "$BADP" '{
  body: {messages: [{"@type": "/aurix.smartmoney.v1.MsgUpdateParams", authority: $a, params: $p}],
         memo: "", timeout_height: "0", extension_options: [], non_critical_extension_options: []},
  auth_info: {signer_infos: [], fee: {amount: [{denom: "uaur", amount: "5000"}], gas_limit: "400000", payer: "", granter: ""}},
  signatures: []}' >"$NET/direct.json"
"$BIN" tx sign "$NET/direct.json" --from val0 --keyring-backend test --home "$NET/node0" \
  --chain-id "$CHAIN_ID" --node "$NODE" -o json >"$NET/direct.signed.json"
OUT=$("$BIN" tx broadcast "$NET/direct.signed.json" --node "$NODE" -o json 2>&1 || true)
CODE=$(jq -r .code <<<"$OUT" 2>/dev/null || echo err)
LOG=$(jq -r .raw_log <<<"$OUT" 2>/dev/null || echo "$OUT")
if [[ $CODE == 0 ]]; then
  H=$(jq -r .txhash <<<"$OUT"); sleep 6
  R=$("$BIN" q tx "$H" --node "$NODE" -o json); CODE=$(jq -r .code <<<"$R"); LOG=$(jq -r .raw_log <<<"$R")
fi
[[ $CODE != 0 ]] || fail "direct MsgUpdateParams was accepted"
[[ $(q smartmoney params | jq -c .params) == "$AFTER" ]] || fail "params changed by direct MsgUpdateParams"
echo "OK: direct MsgUpdateParams from $VAL0 rejected (code $CODE: $LOG), params unchanged"
echo "PASS"
