#!/usr/bin/env bash
# Coordinator step 2: collect all gentxs into the final genesis and publish its hash.
#
# Usage: coordinator-finalize.sh [GENTX_DIR] [OUT_DIR]
#   GENTX_DIR default networks/aurix-testnet-2/gentx, OUT_DIR default networks/aurix-testnet-2
# Env: AURIXD, COORD_HOME (same as for pregenesis),
#      PREGENESIS_SHA256 (optional: assert the coordinator's pre-genesis is the published one)
# Output: OUT_DIR/genesis.json and OUT_DIR/genesis.json.sha256
set -euo pipefail
source "$(dirname "$0")/common.sh"
need sha256sum; need jq
NETDIR=$(cd "$(dirname "$0")/.." && pwd)
GENTX_DIR=${1:-$NETDIR/gentx}; OUT=${2:-$NETDIR}
COORD_HOME=${COORD_HOME:-./coordinator-home}
G="$COORD_HOME/config/genesis.json"
[[ -f "$G" ]] || die "no pre-genesis in $COORD_HOME"
if [[ -n "${PREGENESIS_SHA256:-}" ]]; then verify_sha "$G" "$PREGENESIS_SHA256"; fi
shopt -s nullglob; files=("$GENTX_DIR"/*.json)
(( ${#files[@]} > 0 )) || die "no gentx in $GENTX_DIR"
rm -rf "$COORD_HOME/config/gentx"; mkdir -p "$COORD_HOME/config/gentx"
for f in "${files[@]}"; do
  jq -e '.body.messages[0]["@type"] == "/cosmos.staking.v1beta1.MsgCreateValidator"' "$f" >/dev/null \
    || die "$f is not a gentx"
  cp "$f" "$COORD_HOME/config/gentx/"
done
$BIN genesis collect-gentxs --home "$COORD_HOME" --gentx-dir "$COORD_HOME/config/gentx" >/dev/null 2>&1
$BIN genesis validate "$G"
# Equal power sanity check: every self-delegation identical -> nobody above 1/3 with >= 4 validators.
if [[ $(jq -r '.app_state.genutil.gen_txs[].body.messages[0].value.amount' "$G" | sort -u | wc -l) -ne 1 ]]; then
  echo "warning: self-delegations differ between validators" >&2
fi
mkdir -p "$OUT"; cp "$G" "$OUT/genesis.json"
h=$(sha "$OUT/genesis.json"); echo "$h  genesis.json" > "$OUT/genesis.json.sha256"
echo "final genesis: $OUT/genesis.json (${#files[@]} validators)"
echo "sha256: $h"
