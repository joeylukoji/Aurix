# AURIX chain (Cosmos SDK v0.50 + CometBFT v0.38)

| Design layer | Implementation |
| --- | --- |
| `x/aurixbank` (layer 1) | Standard `x/auth` (accounts, sequence = nonce, secp256k1 signatures, fees) + `x/bank` (`uaur` balances, `MsgSend`). No mint module: supply is fixed at genesis. |
| Validator operation | `x/staking` + `x/slashing` (downtime jailing, double-sign slashing) + `x/evidence` + `x/distribution` (fees and rewards to validators). CLI: `aurixd tx staking create-validator/delegate/unbond`, `aurixd tx slashing unjail`. Live test: `scripts/slashing-test.sh`. |
| `x/smartmoney` (layer 2) | Custom module: `MsgCreateEscrow`, `MsgReleaseEscrow`, `MsgRefundEscrow`, `MsgAnchorProof`, `MsgUpdateParams`; escrowed funds are held by the module account. |
| k-of-n payment proofs | `MsgAnchorProof` is one attestation by a member of `params.anchor_signers`. A new hash is `PENDING`; it becomes `CONFIRMED` when `params.anchor_threshold` distinct signers attested the same (order ref, capture ref). Duplicate or mismatching attestations are rejected. Events `proof_attested` (hash, attestor, attestations, threshold) and `proof_confirmed` (hash, height). REST: `/aurix/smartmoney/v1/proofs/{hash}`, `/proofs_pending`, `/params`. Signers = gateway + independent `attestor/` daemons. |
| Governance | `x/gov` v1 + `x/upgrade`: the authority of every keeper (including smartmoney `MsgUpdateParams`) is the gov module account, so signers/threshold and software upgrades change only by proposal. See `docs/governance.md`. |
| Consensus | CometBFT, **4 validators** by default (own key, equal stake, persistent peers), 1 s blocks. Tolerates 1 faulty validator out of 4. |

## Multi-validator testnet

`scripts/init-localnet.sh` creates `VALIDATORS` (default 4) node homes in `$AURIX_NET_HOME/node0..N-1`.
Each validator signs its own gentx with an equal stake (50,000 AUR); nodes are wired as persistent
peers. Node *i* listens on the base ports + *i* × `PORT_STEP` (default 1000): node0 on RPC 26657 /
REST 1317, node1 on 27657 / 2317, and so on. `ATTESTOR_SEEDS` (space separated) adds funded anchor
signers after the gateway and `ANCHOR_THRESHOLD` (default 1) sets the attestations required;
`scripts/dev-up.sh` sets both when `ATTESTORS=N` and starts the attestor daemons. `PENDING_TTL_BLOCKS`
sets `params.pending_ttl_blocks` (pending proof expiry). Real keys instead of dev seeds:
`AURIX_GATEWAY_ADDR`, `AURIX_FAUCET_ADDR`, `ATTESTOR_ADDRS` (from `networks/aurix-testnet-2/scripts/keygen.sh`).

```bash
go build -o build/aurixd ./cmd/aurixd
```

```bash
AURIXD=./build/aurixd AURIX_NET_HOME=$HOME/.aurix-localnet scripts/init-localnet.sh
```

```bash
for i in 0 1 2 3; do ./build/aurixd start --home $HOME/.aurix-localnet/node$i & done
```

Verified behaviour: with 4/4 validators each block carries 4 signatures; with one stopped the chain
keeps finalising (3/4 > 2/3); with two stopped it halts instead of forking; restarted nodes catch up.
`scripts/bft-demo.sh` (repo root) shows this on the dev stack. In Docker Compose, `chain-init` + `chain0..3`
run the same 4-validator network.

**Honest limit:** the 4 validators run on one machine and are operated by one team, so this shows
real BFT consensus and fault tolerance, not independent multi-operator decentralisation yet.

## Run a single node

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

`aurixd seed-address <seed>` prints the address of the dev key `secp256k1(sha256(seed))`, the derivation used by the gateway and the web wallet. The faucet and gateway seeds (`AURIX_FAUCET_SEED`, `AURIX_GATEWAY_SEED`) are dev-only: anyone can recompute those keys. For anything public generate real keys (`networks/aurix-testnet-2/scripts/keygen.sh`, or `aurixd keys add <name>` and feed the mnemonic to `GATEWAY_MNEMONIC` / `FAUCET_MNEMONIC` / `ATTESTOR_MNEMONIC` / `GUARDIAN_MNEMONIC`; same address, coin type 118) and set `AURIX_ENV=production` so the services refuse seed keys.

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
aurixd query smartmoney pending-proofs
aurixd tx smartmoney anchor-proof <hash> <external-ref> <capture-ref> --from <anchor-signer> ...
aurixd query smartmoney params
```
