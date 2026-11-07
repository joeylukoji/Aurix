#!/usr/bin/env bash
# Operator step: verify the final genesis, configure peers/metrics and start the node.
#
# Usage: operator-start.sh GENESIS_JSON EXPECTED_SHA256 [PEERS_FILE]
#   PEERS_FILE default networks/aurix-testnet-2/peers.txt; lines "seed <id>@<host>:<port>" or
#   "persistent <id>@<host>:<port>" (a bare "<id>@host:port" counts as persistent). Your own id is skipped.
# Env: AURIXD, NODE_HOME (~/.aurix), MIN_GAS_PRICES (0uaur), EXTERNAL_ADDRESS (public host:port),
#      P2P_LADDR (tcp://0.0.0.0:26656), RPC_LADDR (tcp://127.0.0.1:26657 - keep private / behind proxy),
#      API_ADDR (tcp://127.0.0.1:1317), GRPC_ADDR (127.0.0.1:9090), PROM_LADDR (127.0.0.1:26660),
#      LOCAL_TEST=1 (allow private/duplicate IPs, 1s blocks; dry-runs only), NO_START=1 (configure only)
set -euo pipefail
source "$(dirname "$0")/common.sh"
need sha256sum; need jq
GEN=${1:?genesis.json}; EXPECTED=${2:?expected sha256}
PEERS_FILE=${3:-$(cd "$(dirname "$0")/.." && pwd)/peers.txt}
NODE_HOME=${NODE_HOME:-$HOME/.aurix}
CFG="$NODE_HOME/config/config.toml"; APP="$NODE_HOME/config/app.toml"
verify_sha "$GEN" "$EXPECTED"
[[ -f "$CFG" ]] || die "run aurixd init first ($NODE_HOME)"
cp "$GEN" "$NODE_HOME/config/genesis.json"

SELF=$($BIN comet show-node-id --home "$NODE_HOME")
persistent=(); seeds=()
if [[ -f "$PEERS_FILE" ]]; then
  while read -r a b _ || [[ -n "${a:-}" ]]; do
    [[ -z "${a:-}" || "$a" == \#* ]] && continue
    case $a in
      seed) kind=seed; p=${b:-} ;;
      persistent) kind=p; p=${b:-} ;;
      *) kind=p; p=$a ;;
    esac
    [[ "$p" == *@*:* ]] || continue
    [[ "${p%%@*}" == "$SELF" ]] && continue
    if [[ $kind == seed ]]; then seeds+=("$p"); else persistent+=("$p"); fi
  done < "$PEERS_FILE"
fi
PP=$(IFS=,; echo "${persistent[*]:-}"); SD=$(IFS=,; echo "${seeds[*]:-}")
[[ -n "$PP$SD" ]] || echo "warning: no peers/seeds found in $PEERS_FILE" >&2

s() { sed -i.bak "$1" "$2"; rm -f "$2.bak"; }
s "s#^persistent_peers = .*#persistent_peers = \"$PP\"#; s#^seeds = .*#seeds = \"$SD\"#" "$CFG"
s "/^\[rpc\]/,/^\[/ s#^laddr = .*#laddr = \"${RPC_LADDR:-tcp://127.0.0.1:26657}\"#" "$CFG"
s "/^\[p2p\]/,/^\[/ s#^laddr = .*#laddr = \"${P2P_LADDR:-tcp://0.0.0.0:26656}\"#" "$CFG"
s "s#^external_address = .*#external_address = \"${EXTERNAL_ADDRESS:-}\"#" "$CFG"
s "s/^prometheus = .*/prometheus = true/; s#^prometheus_listen_addr = .*#prometheus_listen_addr = \"${PROM_LADDR:-127.0.0.1:26660}\"#" "$CFG"
s "s#^pprof_laddr = .*#pprof_laddr = \"\"#" "$CFG"
if [[ "${LOCAL_TEST:-0}" == 1 ]]; then
  s 's/^addr_book_strict = .*/addr_book_strict = false/; s/^allow_duplicate_ip = .*/allow_duplicate_ip = true/' "$CFG"
  s 's/^timeout_commit = .*/timeout_commit = "1s"/' "$CFG"
fi
s "s/^minimum-gas-prices = .*/minimum-gas-prices = \"${MIN_GAS_PRICES:-0uaur}\"/" "$APP"
s '/^\[api\]/,/^\[/ s/^enable = .*/enable = true/' "$APP"
s "/^\[api\]/,/^\[/ s#^address = .*#address = \"${API_ADDR:-tcp://127.0.0.1:1317}\"#" "$APP"
s "/^\[grpc\]/,/^\[/ s#^address = .*#address = \"${GRPC_ADDR:-127.0.0.1:9090}\"#" "$APP"
s 's/^enabled-unsafe-cors = .*/enabled-unsafe-cors = false/' "$APP"
s 's/^prometheus-retention-time = .*/prometheus-retention-time = 60/' "$APP"

echo "node $SELF configured: ${#persistent[@]} persistent peers, ${#seeds[@]} seeds; genesis_time $(jq -r .genesis_time "$GEN")"
[[ "${NO_START:-0}" == 1 ]] && exit 0
exec $BIN start --home "$NODE_HOME"
