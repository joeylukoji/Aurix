#!/usr/bin/env bash
# Coordinator step 1: build the pre-genesis for aurix-testnet-2.
#
# Usage: coordinator-pregenesis.sh OPERATORS_CSV ANCHOR_SIGNERS_FILE [OUT_DIR]
#   OPERATORS_CSV        lines "moniker,address" (a "moniker,address" header and # comments are allowed)
#   ANCHOR_SIGNERS_FILE  one aur address per line (3 expected; the first is the gateway by default)
#                        Generate real keys first with scripts/keygen.sh OUT: it writes OUT/anchor_signers.txt
#                        (gateway + attestors) and OUT/addresses.txt (faucet address for FAUCET_ADDR).
# Env:
#   AURIXD (aurixd), CHAIN_ID (aurix-testnet-2), COORD_HOME (scratch home, default ./coordinator-home)
#   FAUCET_ADDR (required), GATEWAY_ADDR (default: first anchor signer)
#   OPERATOR_BALANCE (100000000000uaur = 100k AUR, identical for all operators -> equal stake)
#   FAUCET_BALANCE (1000000000000uaur), GATEWAY_BALANCE (1000000000uaur), ATTESTOR_BALANCE (10000000uaur)
#   GENESIS_TIME (RFC3339, default now + 48h)
#   ANCHOR_THRESHOLD (2), VOTING_PERIOD (24h), EXPEDITED_VOTING_PERIOD (1h), MAX_DEPOSIT_PERIOD (24h),
#   GOV_MIN_DEPOSIT (1000000000uaur), SIGNED_BLOCKS_WINDOW (10000), MIN_SIGNED (0.05), DOWNTIME_JAIL (600s)
# Params whose key is absent from the binary's default genesis are skipped with a warning.
# Output: OUT_DIR/pregenesis.json and OUT_DIR/pregenesis.json.sha256
set -euo pipefail
source "$(dirname "$0")/common.sh"
need jq; need sha256sum
CSV=${1:?operators csv}; SIGNERS=${2:?anchor signers file}; OUT=${3:-.}
COORD_HOME=${COORD_HOME:-./coordinator-home}
: "${FAUCET_ADDR:?FAUCET_ADDR required}"
OPERATOR_BALANCE=${OPERATOR_BALANCE:-100000000000uaur}
FAUCET_BALANCE=${FAUCET_BALANCE:-1000000000000uaur}
GATEWAY_BALANCE=${GATEWAY_BALANCE:-1000000000uaur}
ATTESTOR_BALANCE=${ATTESTOR_BALANCE:-10000000uaur}
GENESIS_TIME=${GENESIS_TIME:-$(date -u -d '+48 hours' +%Y-%m-%dT%H:%M:%SZ)}
ANCHOR_THRESHOLD=${ANCHOR_THRESHOLD:-2}
VOTING_PERIOD=${VOTING_PERIOD:-24h}; EXPEDITED_VOTING_PERIOD=${EXPEDITED_VOTING_PERIOD:-1h}
MAX_DEPOSIT_PERIOD=${MAX_DEPOSIT_PERIOD:-24h}; GOV_MIN_DEPOSIT=${GOV_MIN_DEPOSIT:-1000000000uaur}
SIGNED_BLOCKS_WINDOW=${SIGNED_BLOCKS_WINDOW:-10000}; MIN_SIGNED=${MIN_SIGNED:-0.05}; DOWNTIME_JAIL=${DOWNTIME_JAIL:-600s}

dur_s() { # 24h / 90m / 30s -> "86400s"
  local v=$1
  case $v in *h) echo "$(( ${v%h} * 3600 ))s";; *m) echo "$(( ${v%m} * 60 ))s";; *s) echo "$v";; *) die "bad duration $v";; esac
}

mapfile -t SIGNER_LIST < <(grep -vE '^[[:space:]]*(#|$)' "$SIGNERS" | tr -d ' \r')
(( ${#SIGNER_LIST[@]} >= 1 )) || die "no anchor signers in $SIGNERS"
(( ${#SIGNER_LIST[@]} == 3 )) || echo "warning: expected 3 anchor signers, got ${#SIGNER_LIST[@]}" >&2
(( ANCHOR_THRESHOLD >= 1 && ANCHOR_THRESHOLD <= ${#SIGNER_LIST[@]} )) || die "ANCHOR_THRESHOLD out of range"
[[ $(printf '%s\n' "${SIGNER_LIST[@]}" | sort -u | wc -l) -eq ${#SIGNER_LIST[@]} ]] || die "duplicate anchor signer"
GATEWAY_ADDR=${GATEWAY_ADDR:-${SIGNER_LIST[0]}}

[[ -e "$COORD_HOME/config/genesis.json" ]] && die "$COORD_HOME already initialised; remove it or set COORD_HOME"
$BIN init coordinator --chain-id "$CHAIN_ID" --default-denom uaur --home "$COORD_HOME" >/dev/null 2>&1
G="$COORD_HOME/config/genesis.json"

declare -A SEEN
add() { # addr amount
  [[ -n "${SEEN[$1]:-}" ]] && die "duplicate genesis address $1"
  SEEN[$1]=1
  $BIN genesis add-genesis-account "$1" "$2" --home "$COORD_HOME"
}
n=0
while IFS=, read -r moniker addr _ || [[ -n "${moniker:-}" ]]; do
  moniker=$(echo "$moniker" | tr -d ' \r'); addr=$(echo "${addr:-}" | tr -d ' \r')
  [[ -z "$moniker" || "$moniker" == \#* || "$moniker" == moniker ]] && continue
  [[ "$addr" == aur1* ]] || die "bad address for $moniker: $addr"
  add "$addr" "$OPERATOR_BALANCE"; n=$((n + 1))
done < "$CSV"
(( n >= 4 )) || echo "warning: only $n operators (BFT needs >= 4 to tolerate one fault)" >&2
add "$FAUCET_ADDR" "$FAUCET_BALANCE"
add "$GATEWAY_ADDR" "$GATEWAY_BALANCE"
for s in "${SIGNER_LIST[@]}"; do [[ -n "${SEEN[$s]:-}" ]] || add "$s" "$ATTESTOR_BALANCE"; done

SIGNERS_JSON=$(printf '%s\n' "${SIGNER_LIST[@]}" | jq -R . | jq -s .)
jq_inplace "$G" --arg t "$GENESIS_TIME" --argjson signers "$SIGNERS_JSON" \
  --arg win "$SIGNED_BLOCKS_WINDOW" --arg min "$MIN_SIGNED" --arg jail "$(dur_s "$DOWNTIME_JAIL")" '
  .genesis_time = $t
  | .app_state.staking.params.bond_denom = "uaur"
  | .app_state.smartmoney.params.anchor_signers = $signers
  | .app_state.slashing.params.signed_blocks_window = $win
  | .app_state.slashing.params.min_signed_per_window = $min
  | .app_state.slashing.params.downtime_jail_duration = $jail'

if jq -e '.app_state.smartmoney.params | has("anchor_threshold")' "$G" >/dev/null; then
  jq_inplace "$G" --argjson k "$ANCHOR_THRESHOLD" '.app_state.smartmoney.params.anchor_threshold = $k'
else
  echo "warning: binary has no smartmoney anchor_threshold param; skipped (k-of-n not active)" >&2
fi

if jq -e '.app_state | has("gov")' "$G" >/dev/null; then
  amt=${GOV_MIN_DEPOSIT%uaur}
  jq_inplace "$G" --arg vp "$(dur_s "$VOTING_PERIOD")" --arg evp "$(dur_s "$EXPEDITED_VOTING_PERIOD")" \
    --arg mdp "$(dur_s "$MAX_DEPOSIT_PERIOD")" --arg amt "$amt" '
    .app_state.gov.params |= (
        (if has("voting_period") then .voting_period = $vp else . end)
      | (if has("expedited_voting_period") then .expedited_voting_period = $evp else . end)
      | (if has("max_deposit_period") then .max_deposit_period = $mdp else . end)
      | (if has("min_deposit") then .min_deposit = [{denom: "uaur", amount: $amt}] else . end)
      | (if has("expedited_min_deposit") then .expedited_min_deposit = [{denom: "uaur", amount: (($amt | tonumber) * 5 | tostring)}] else . end))'
else
  echo "warning: binary has no x/gov; gov params skipped" >&2
fi

mkdir -p "$OUT"
cp "$G" "$OUT/pregenesis.json"
h=$(sha "$OUT/pregenesis.json"); echo "$h  pregenesis.json" > "$OUT/pregenesis.json.sha256"
echo "pre-genesis: $OUT/pregenesis.json ($n operators, genesis_time $GENESIS_TIME, chain $CHAIN_ID)"
echo "sha256: $h"
