#!/usr/bin/env bash
# Initialises an N-validator AURIX testnet (default 4) in $AURIX_NET_HOME/node0..node{N-1}.
# Each validator has its own key, equal stake and its own CometBFT node; nodes are
# connected as persistent peers. With N=4, BFT consensus tolerates 1 faulty validator.
#
# Ports of node i = base port + i*PORT_STEP (default step 1000):
#   RPC 26657, P2P 26656, REST 1317, gRPC 9090  -> node1: 27657, 27656, 2317, 10090 ...
# For Docker (one node per container) use PORT_STEP=0 and PEER_HOST=aurix-node%d.
# CometBFT Prometheus metrics are served on PROM_PORT + i*PORT_STEP (default 26660).
# Slashing: SIGNED_BLOCKS_WINDOW / MIN_SIGNED / DOWNTIME_JAIL override the x/slashing
# params (short values make downtime jailing observable in tests).
# Idempotent unless RESET=1.
set -euo pipefail
BIN=${AURIXD:-aurixd}
N=${VALIDATORS:-4}
NET=${AURIX_NET_HOME:-$HOME/.aurix-localnet}
CHAIN_ID=${AURIX_CHAIN_ID:-aurix-testnet-1}
FAUCET_SEED=${AURIX_FAUCET_SEED:-aurix-dev-faucet}
GATEWAY_SEED=${AURIX_GATEWAY_SEED:-aurix-dev-gateway}
STEP=${PORT_STEP:-1000}
RPC=${RPC_PORT:-26657}; P2P=${P2P_PORT:-26656}; REST=${REST_PORT:-1317}; GRPC=${GRPC_PORT:-9090}
PEER_HOST=${PEER_HOST:-127.0.0.1}   # may contain %d (node index), e.g. aurix-node%d
BIND=${BIND_HOST:-127.0.0.1}
TIMEOUT_COMMIT=${TIMEOUT_COMMIT:-1s}
PROM=${PROM_PORT:-26660}
SIGNED_BLOCKS_WINDOW=${SIGNED_BLOCKS_WINDOW:-100}
MIN_SIGNED=${MIN_SIGNED:-0.5}
DOWNTIME_JAIL=${DOWNTIME_JAIL:-600s}

if [[ "${RESET:-0}" == "1" ]]; then rm -rf "$NET"; fi
if [[ -f "$NET/node0/config/genesis.json" ]]; then echo "already initialised: $NET"; exit 0; fi
(( N >= 1 )) || { echo "VALIDATORS must be >= 1" >&2; exit 1; }

home() { echo "$NET/node$1"; }
host() { printf "$PEER_HOST" "$1"; }
kr() { echo --keyring-backend test --home "$(home "$1")"; }

for ((i = 0; i < N; i++)); do
  $BIN init "aurix-val$i" --chain-id "$CHAIN_ID" --default-denom uaur --home "$(home $i)" >/dev/null 2>&1
  $BIN keys add "val$i" $(kr $i) >/dev/null 2>&1
done

G0="$(home 0)/config/genesis.json"
# Fixed supply (no mint): 100,000 AUR per validator, 1,000,000 AUR faucet, 1,000 AUR gateway.
for ((i = 0; i < N; i++)); do
  addr=$($BIN keys show "val$i" -a $(kr $i))
  $BIN genesis add-genesis-account "$addr" 100000000000uaur --home "$(home 0)"
done
GATEWAY=$($BIN seed-address "$GATEWAY_SEED")
$BIN genesis add-genesis-account "$($BIN seed-address "$FAUCET_SEED")" 1000000000000uaur --home "$(home 0)"
$BIN genesis add-genesis-account "$GATEWAY" 1000000000uaur --home "$(home 0)"
jq --arg gw "$GATEWAY" '.app_state.smartmoney.params.anchor_signers = [$gw]
  | .app_state.staking.params.bond_denom = "uaur"
  | .app_state.slashing.params.signed_blocks_window = $win
  | .app_state.slashing.params.min_signed_per_window = $min
  | .app_state.slashing.params.downtime_jail_duration = $jail' \
  --arg win "$SIGNED_BLOCKS_WINDOW" --arg min "$MIN_SIGNED" --arg jail "$DOWNTIME_JAIL" "$G0" > "$G0.tmp" && mv "$G0.tmp" "$G0"

# Every validator signs its own gentx (equal stake) against the shared pre-genesis.
mkdir -p "$(home 0)/config/gentx"
for ((i = 0; i < N; i++)); do
  [[ $i -gt 0 ]] && cp "$G0" "$(home $i)/config/genesis.json"
  $BIN genesis gentx "val$i" 50000000000uaur --chain-id "$CHAIN_ID" --moniker "aurix-val$i" \
    --output-document "$(home 0)/config/gentx/gentx-val$i.json" $(kr $i) >/dev/null 2>&1
done
$BIN genesis collect-gentxs --home "$(home 0)" >/dev/null 2>&1
$BIN genesis validate-genesis --home "$(home 0)" >/dev/null

PEERS=()
for ((i = 0; i < N; i++)); do
  PEERS+=("$($BIN comet show-node-id --home "$(home $i)")@$(host $i):$((P2P + i * STEP))")
done

for ((i = 0; i < N; i++)); do
  h=$(home $i); APP="$h/config/app.toml"; CFG="$h/config/config.toml"
  [[ $i -gt 0 ]] && cp "$G0" "$h/config/genesis.json"
  others=$(printf "%s\n" "${PEERS[@]}" | sed -n "$((i + 1))!p" | paste -sd, -)
  sed -i.bak "s#^laddr = \"tcp://127.0.0.1:26657\"#laddr = \"tcp://$BIND:$((RPC + i * STEP))\"#" "$CFG"
  sed -i.bak "s#^laddr = \"tcp://0.0.0.0:26656\"#laddr = \"tcp://$BIND:$((P2P + i * STEP))\"#" "$CFG"
  sed -i.bak "s#^persistent_peers = .*#persistent_peers = \"$others\"#" "$CFG"
  sed -i.bak 's/^addr_book_strict = true/addr_book_strict = false/; s/^allow_duplicate_ip = false/allow_duplicate_ip = true/' "$CFG"
  sed -i.bak "s/^timeout_commit = .*/timeout_commit = \"$TIMEOUT_COMMIT\"/; s#^pprof_laddr = .*#pprof_laddr = \"\"#" "$CFG"
  sed -i.bak "s/^prometheus = false/prometheus = true/; s#^prometheus_listen_addr = .*#prometheus_listen_addr = \"$BIND:$((PROM + i * STEP))\"#" "$CFG"
  sed -i.bak '/^\[api\]/,/^\[/ s/^enable = false/enable = true/' "$APP"
  sed -i.bak "/^\[api\]/,/^\[/ s#^address = .*#address = \"tcp://$BIND:$((REST + i * STEP))\"#" "$APP"
  sed -i.bak "/^\[grpc\]/,/^\[/ s#^address = .*#address = \"$BIND:$((GRPC + i * STEP))\"#" "$APP"
  sed -i.bak 's/^enabled-unsafe-cors = false/enabled-unsafe-cors = true/' "$APP"
  rm -f "$APP.bak" "$CFG.bak"
done
echo "initialised $N validators in $NET (chain $CHAIN_ID, gateway=$GATEWAY)"
for ((i = 0; i < N; i++)); do echo "  node$i  rpc :$((RPC + i * STEP))  rest :$((REST + i * STEP))  metrics :$((PROM + i * STEP))  p2p :$((P2P + i * STEP))"; done
