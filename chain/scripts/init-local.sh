#!/usr/bin/env bash
# Initialises a single-validator AURIX testnet home directory (idempotent unless RESET=1).
set -euo pipefail
BIN=${AURIXD:-aurixd}
HOME_DIR=${AURIX_HOME:-$HOME/.aurix}
CHAIN_ID=${AURIX_CHAIN_ID:-aurix-testnet-1}
FAUCET_SEED=${AURIX_FAUCET_SEED:-aurix-dev-faucet}
GATEWAY_SEED=${AURIX_GATEWAY_SEED:-aurix-dev-gateway}
KR=(--keyring-backend test --home "$HOME_DIR")

if [[ "${RESET:-0}" == "1" ]]; then rm -rf "$HOME_DIR"; fi
if [[ -f "$HOME_DIR/config/genesis.json" ]]; then echo "already initialised: $HOME_DIR"; exit 0; fi

$BIN init aurix-node --chain-id "$CHAIN_ID" --default-denom uaur --home "$HOME_DIR" >/dev/null 2>&1
$BIN keys add validator "${KR[@]}" >/dev/null 2>&1
FAUCET=$($BIN seed-address "$FAUCET_SEED")
GATEWAY=$($BIN seed-address "$GATEWAY_SEED")

# Fixed supply (no mint module): 1,000,000 AUR faucet + 1,000 AUR gateway + 100,000 AUR validator.
$BIN genesis add-genesis-account validator 100000000000uaur "${KR[@]}"
$BIN genesis add-genesis-account "$FAUCET" 1000000000000uaur --home "$HOME_DIR"
$BIN genesis add-genesis-account "$GATEWAY" 1000000000uaur --home "$HOME_DIR"

G="$HOME_DIR/config/genesis.json"
jq --arg gw "$GATEWAY" '.app_state.smartmoney.params.anchor_signers = [$gw]
  | .app_state.staking.params.bond_denom = "uaur"' "$G" > "$G.tmp" && mv "$G.tmp" "$G"

$BIN genesis gentx validator 50000000000uaur --chain-id "$CHAIN_ID" "${KR[@]}" >/dev/null 2>&1
$BIN genesis collect-gentxs --home "$HOME_DIR" >/dev/null 2>&1
$BIN genesis validate-genesis --home "$HOME_DIR"

APP="$HOME_DIR/config/app.toml"; CFG="$HOME_DIR/config/config.toml"
sed -i.bak '/^\[api\]/,/^\[/ s/^enable = false/enable = true/' "$APP"
sed -i.bak '/^\[api\]/,/^\[/ s#^address = .*#address = "tcp://0.0.0.0:1317"#' "$APP"
sed -i.bak 's/^enabled-unsafe-cors = false/enabled-unsafe-cors = true/' "$APP"
sed -i.bak 's#^laddr = "tcp://127.0.0.1:26657"#laddr = "tcp://0.0.0.0:26657"#' "$CFG"
sed -i.bak 's/^timeout_commit = .*/timeout_commit = "1s"/' "$CFG"
rm -f "$APP.bak" "$CFG.bak"
echo "initialised $HOME_DIR  faucet=$FAUCET gateway=$GATEWAY"
