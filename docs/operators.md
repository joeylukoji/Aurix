# Operating an AURIX validator

Onboarding and the genesis ceremony are in `networks/aurix-testnet-2/README.md`. This page covers
monitoring, public endpoints and the failure drill.

## Monitoring

1. `operator-start.sh` enables CometBFT metrics (`prometheus = true`, `127.0.0.1:26660` by default).
   For a remote Prometheus, set `PROM_LADDR=0.0.0.0:26660` and firewall 26660 to the Prometheus host.
2. Use `deploy/monitoring/prometheus.yml`: one target per validator, labelled by operator and moniker.
   Its rule block (save as `aurix.rules.yml`) alerts on:
   - **missed blocks**: `delta(cometbft_consensus_validator_missed_blocks[10m]) > 10`;
   - validator target down, chain halted (no height increase in 5 min), fewer than 2 peers.
   Slashing jails a validator that signs less than `min_signed_per_window` of `signed_blocks_window`
   blocks; the alert must fire well before that.
3. Grafana: add Prometheus as a data source and import the community "Cosmos / CometBFT" dashboard
   (or graph `cometbft_consensus_height`, `cometbft_consensus_validators_power`,
   `cometbft_consensus_validator_missed_blocks`, `cometbft_p2p_peers`). Share one read-only dashboard
   (or an uptime page) for all validators; the 24 h uptime report for acceptance comes from it.
4. Chain-side check without Prometheus:
   `aurixd q slashing signing-infos --node <rpc>` (missed blocks counter, jailed-until).

## Public endpoints

At least one public seed and 2 public RPC/REST endpoints run by **different** operators. Put them behind
`deploy/public-node/Caddyfile` or `deploy/public-node/nginx.conf` (TLS, CORS limited to the wallet origin,
rate limiting, RPC allow-list). The wallet lists them in its `config.json` (see `deploy/static-web.md`).

## Failure drill

Goal: show that BFT holds with 4 validators of equal power (each 25 %). Record the screen (block
explorer or `watch` on the height) for the demo.

| Step | Action | Expected |
| --- | --- | --- |
| 0 | All 4 up. `curl -s <rpc>/status \| jq .result.sync_info.latest_block_height` on 2 nodes | height increases |
| 1 | Operator A: `systemctl stop aurixd` | chain continues (75 % > 2/3); A's missed-blocks alert fires |
| 2 | Operator B also stops | chain **halts** (50 % < 2/3); "chain halted" alert fires; no tx is committed |
| 3 | B restarts | blocks resume within a few seconds, same height continues (no fork) |
| 4 | A restarts | A catches up; check `aurixd q slashing signing-infos`: A not jailed if downtime < window threshold |

Rules for the drill:
- Announce it in the operators channel with the time and who stops; nobody else touches their node.
- Stop with `systemctl stop`, never by copying the node elsewhere: restarting a validator key on another
  machine during the drill is a double-sign.
- If A was jailed (downtime too long): `aurixd tx slashing unjail --from validator --keyring-backend file`
  after the jail duration.
- Write down timestamps and heights for each step in the drill report.

Locally, `scripts/bft-demo.sh` runs the same scenario on the dev 4-node localnet, and
`networks/aurix-testnet-2/scripts/dry-run.sh` rehearses the genesis ceremony.
