# aurix-testnet-2: operator package

`aurix-testnet-2` is the first AURIX network whose validators are run by **independent operators**
(distinct people, IPs and providers). The single-machine 4-validator setup (`scripts/dev-up.sh`)
stays for local development only.

| Item | Value |
| --- | --- |
| Chain id | `aurix-testnet-2` |
| Denom | `uaur` (1 AUR = 10^6 uaur), bech32 prefix `aur` |
| Binary | `aurixd` from a tagged GitHub release (linux amd64/arm64, `SHA256SUMS`) or the `ghcr.io/<org>/aurixd:<tag>` image |
| Stake | every operator gets the same balance (100 000 AUR) and self-delegates the same amount (50 000 AUR), so nobody exceeds 1/3 of the voting power with >= 4 validators |
| Proof anchoring | 3 anchor signers, threshold 2 (when the binary supports `anchor_threshold`) |

## Hardware and network

- 2 vCPU, 4 GB RAM, 50 GB SSD (NVMe preferred), Linux x86_64 or arm64, NTP-synced clock.
- Ports:

| Port | Service | Exposure |
| --- | --- | --- |
| 26656 | P2P | **public** (validator behind sentries: only to its sentries) |
| 26657 | CometBFT RPC | private, or behind the reverse proxy in `deploy/public-node/` |
| 1317 | REST (API) | private, or behind the reverse proxy |
| 9090 | gRPC | private |
| 26660 | Prometheus metrics | private: only your Prometheus server |

## Ceremony scripts (`scripts/`)

| Script | Who | What |
| --- | --- | --- |
| `coordinator-pregenesis.sh operators.csv anchor_signers.txt [out]` | coordinator | equal-stake accounts from `moniker,address` lines, faucet + gateway + attestor accounts, slashing/gov/smartmoney params, `genesis_time` = now + 48 h (`GENESIS_TIME` overrides), prints the SHA-256 |
| `operator-gentx.sh pregenesis.json <sha256>` | each operator | verifies the hash, signs the gentx with the `file`/`os` keyring, writes `gentx/<moniker>.json` |
| `coordinator-finalize.sh` | coordinator | `collect-gentxs`, `genesis validate`, writes `genesis.json` + `genesis.json.sha256` |
| `operator-start.sh genesis.json <sha256>` | each operator | verifies the final hash, sets peers/seeds from `peers.txt`, enables Prometheus, starts the node |
| `dry-run.sh` | anyone | the whole ceremony with 4 simulated operators on a throwaway chain id, on one machine |

Params the binary does not know yet (e.g. x/gov or `anchor_threshold` on an older build) are skipped
with a warning: check the pre-genesis output before publishing it. Always run `dry-run.sh` with the
release binary before the real ceremony.

## Operator checklist

1. [ ] Download the release binary, check it against `SHA256SUMS`, install as `/usr/local/bin/aurixd`.
2. [ ] `aurixd init <moniker> --chain-id aurix-testnet-2 --home ~/.aurix`
3. [ ] `aurixd keys add validator --keyring-backend file --home ~/.aurix` and **write the mnemonic down offline**.
4. [ ] Back up `~/.aurix/config/priv_validator_key.json` and `node_key.json` offline (encrypted).
5. [ ] Send the coordinator **only** `moniker,address` (`aurixd keys show validator -a --keyring-backend file`). Never a key or mnemonic.
6. [ ] When the pre-genesis is published: `MONIKER=<moniker> KEYRING_BACKEND=file scripts/operator-gentx.sh pregenesis.json <published sha256>`
7. [ ] Open a PR adding `gentx/<moniker>.json` and your line in `peers.txt` (sentry address if you use sentries).
8. [ ] When the final genesis is merged: `EXTERNAL_ADDRESS=<public-ip>:26656 scripts/operator-start.sh genesis.json <published sha256>` (run it under systemd for production; `NO_START=1` only configures).
9. [ ] Firewall: only 26656 open to the world; scrape 26660 from your Prometheus only.
10. [ ] Start **before** `genesis_time`; check `curl -s localhost:26657/status | jq .result.sync_info`.
11. [ ] Add your node to the shared monitoring (see `docs/operators.md`) and join the operators channel.

## Security

- **Key backup.** `priv_validator_key.json` signs blocks; the account mnemonic controls the stake.
  Keep both backed up offline and encrypted. Losing them means losing the validator.
- **Never double-run.** Never start the same `priv_validator_key.json` on two machines at once, not even
  briefly during a migration: two signers double-sign, and the validator is slashed (5 %) and tombstoned
  forever. To migrate: stop the old node, make sure it cannot restart (disable the service), copy the key
  **and** `data/priv_validator_state.json`, then start the new one.
- **Sentry layout (recommended).** The validator only peers with 2+ sentry nodes you own
  (`pex = false`, `persistent_peers = <sentries>`), and sentries list it in `private_peer_ids`.
  Only sentries are public and listed in `peers.txt`.
- **tmkms (optional).** Keep the consensus key in [tmkms](https://github.com/iqlusioninc/tmkms) (softsign or
  a YubiHSM) and set `priv_validator_laddr` in `config.toml`; tmkms keeps its own double-sign state.
- **Keyring.** Use `--keyring-backend file` (passphrase-encrypted) or `os`. The `test` keyring stores keys
  unencrypted: `operator-gentx.sh` refuses it except in `dry-run.sh`.
- **RPC.** Keep 26657/1317/9090 bound to localhost or a private network. Public endpoints go through
  `deploy/public-node/` (TLS, CORS limited to the wallet origin, rate limiting); never set
  `enabled-unsafe-cors = true` on a public node.
- Keep the OS patched, SSH by key only, and the clock synced (chrony).

## Ceremony timeline

1. T-7 d: operators send `moniker,address`. Coordinator collects them in `operators.csv`.
2. T-48 h: coordinator runs `coordinator-pregenesis.sh`, publishes `pregenesis.json` and its hash.
3. T-24 h: operators submit gentx PRs. Coordinator runs `coordinator-finalize.sh`, commits `genesis.json` + hash.
4. T-2 h: operators run `operator-start.sh`. Nodes wait for `genesis_time`, then the chain starts once
   more than 2/3 of the power is online.
