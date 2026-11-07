#!/usr/bin/env bash
# Operator step: verify the published pre-genesis and sign your gentx, on YOUR machine.
#
# Usage: operator-gentx.sh PREGENESIS_JSON EXPECTED_SHA256
# Env: AURIXD, CHAIN_ID (aurix-testnet-2), NODE_HOME (~/.aurix), KEY_NAME (validator), MONIKER (required),
#      KEYRING_BACKEND (file | os; "test" is refused unless ALLOW_TEST_KEYRING=1, dry-runs only),
#      SELF_DELEGATION (50000000000uaur, identical for every operator), COMMISSION_RATE (0.10),
#      COMMISSION_MAX_RATE (0.20), COMMISSION_MAX_CHANGE (0.01), OUT_DIR (networks/aurix-testnet-2/gentx)
# Prerequisites: `aurixd init $MONIKER --chain-id $CHAIN_ID --home $NODE_HOME`
#                `aurixd keys add $KEY_NAME --keyring-backend file --home $NODE_HOME`
# Output: $OUT_DIR/$MONIKER.json  -> open a PR adding exactly this file.
set -euo pipefail
source "$(dirname "$0")/common.sh"
need sha256sum; need jq
PRE=${1:?pregenesis.json}; EXPECTED=${2:?expected sha256}
NODE_HOME=${NODE_HOME:-$HOME/.aurix}; KEY_NAME=${KEY_NAME:-validator}; : "${MONIKER:?MONIKER required}"
KR=${KEYRING_BACKEND:-file}
case $KR in
  file|os) ;;
  test) [[ "${ALLOW_TEST_KEYRING:-0}" == 1 ]] || die "keyring 'test' stores keys unencrypted; use file or os" ;;
  *) die "unsupported keyring $KR" ;;
esac
SELF_DELEGATION=${SELF_DELEGATION:-50000000000uaur}
OUT_DIR=${OUT_DIR:-$(cd "$(dirname "$0")/.." && pwd)/gentx}

verify_sha "$PRE" "$EXPECTED"
[[ -f "$NODE_HOME/config/priv_validator_key.json" ]] || die "run aurixd init first ($NODE_HOME)"
[[ "$(jq -r .chain_id "$PRE")" == "$CHAIN_ID" ]] || die "pre-genesis chain_id is not $CHAIN_ID"
cp "$PRE" "$NODE_HOME/config/genesis.json"
mkdir -p "$OUT_DIR"
$BIN genesis gentx "$KEY_NAME" "$SELF_DELEGATION" --chain-id "$CHAIN_ID" --moniker "$MONIKER" \
  --commission-rate "${COMMISSION_RATE:-0.10}" --commission-max-rate "${COMMISSION_MAX_RATE:-0.20}" \
  --commission-max-change-rate "${COMMISSION_MAX_CHANGE:-0.01}" \
  --keyring-backend "$KR" --home "$NODE_HOME" --output-document "$OUT_DIR/$MONIKER.json" 2>&1 | grep -v '^Genesis transaction written' || true
[[ -s "$OUT_DIR/$MONIKER.json" ]] || die "gentx failed"
echo "gentx written: $OUT_DIR/$MONIKER.json"
echo "node id (for peers.txt): $($BIN comet show-node-id --home "$NODE_HOME")"
