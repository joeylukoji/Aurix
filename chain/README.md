# AURIX chain (Cosmos SDK v0.50 + CometBFT v0.38)

| Design layer | Implementation |
| --- | --- |
| `x/aurixbank` (layer 1) | Standard `x/auth` (accounts, sequence = nonce, secp256k1 signatures, fees) + `x/bank` (`uaur` balances, `MsgSend`). No mint module: supply is fixed at genesis. |
| `x/smartmoney` (layer 2) | Custom module: `MsgCreateEscrow`, `MsgReleaseEscrow`, `MsgRefundEscrow`, `MsgAnchorProof`; escrowed funds are held by the module account. |
| Consensus | CometBFT, single validator (testnet), 1 s blocks |

## Run locally

```bash
go build -o build/aurixd ./cmd/aurixd
```

```bash
AURIXD=./build/aurixd scripts/init-local.sh
```

```bash
./build/aurixd start
```

Endpoints: CometBFT RPC `:26657`, REST `:1317` (e.g. `/aurix/smartmoney/v1/escrows`, `/cosmos/bank/v1beta1/balances/{addr}`), gRPC `:9090`.

`aurixd seed-address <seed>` prints the address of the dev key `secp256k1(sha256(seed))`, the derivation used by the gateway and the web wallet. The faucet and gateway seeds (`AURIX_FAUCET_SEED`, `AURIX_GATEWAY_SEED`) are dev-only.

## Develop

```bash
go test ./...
```

Regenerate protobuf code after editing `proto/` (needs `protoc`, `protoc-gen-gocosmos@v1.7.0`, `protoc-gen-grpc-gateway@v1.16.0`):

```bash
scripts/protogen.sh
```

## CLI

`aurixd` exposes x/smartmoney directly (no gateway needed). Common flags: `--node tcp://localhost:26657`, and for txs `--from validator --keyring-backend test --chain-id aurix-testnet-1 --fees 1000uaur -y`.

```sh
aurixd tx smartmoney create-escrow <recipient> 1000000uaur <deadline-height> [--arbiter <addr>] --from validator ...
aurixd tx smartmoney release-escrow <id> --from validator ...   # sender or arbiter
aurixd tx smartmoney refund-escrow <id> --from validator ...    # after deadline
aurixd tx bank send validator <addr> 5000000uaur ...

aurixd query smartmoney escrow <id>
aurixd query smartmoney escrows
aurixd query smartmoney proof <hash>
aurixd query smartmoney params
```
