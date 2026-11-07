# AURIX on-chain governance

AURIX uses the standard Cosmos SDK `x/gov` v1 and `x/upgrade` modules. Every privileged change goes
through a governance proposal. That covers module parameters such as the `x/smartmoney`
`anchor_signers` / `anchor_threshold` and software upgrades. The authority of every keeper is the
**gov module account**, so a transaction signed directly by any ordinary key, including a validator's
or the gateway's key, is rejected with `invalid authority`.

```bash
aurixd q auth module-account gov -o json | jq -r .account.value.address   # the authority address
```

Two scripts check the procedures in this document on a throwaway 4-validator network:
`scripts/gov-test.sh` (parameter proposals) and `scripts/upgrade-drill.sh` (software upgrade).

## Who can propose and vote

- **Propose:** any account that pays the fee. The proposal enters the voting period once its deposits
  reach `min_deposit`. Anyone can add to the deposit (`aurixd tx gov deposit <id> <amount>`) during
  `max_deposit_period`. The initial deposit must be at least `min_initial_deposit_ratio` of
  `min_deposit`.
- **Vote:** bonded stake. Validators vote with their self-delegation plus the stake delegated to them.
  A delegator can override its validator by voting itself. The options are `yes`, `no`, `no_with_veto`
  and `abstain`.
- **Deposits** are refunded when the proposal passes or is rejected. They are burned when the
  proposal fails to reach quorum or is vetoed (`burn_vote_quorum`, `burn_vote_veto`).

## Parameters (`aurix-testnet-2`)

| Param | Testnet value | Local net (`init-localnet.sh`) |
| --- | --- | --- |
| `min_deposit` | 1,000 AUR (`1000000000uaur`) | `GOV_MIN_DEPOSIT` (default 1000 AUR) |
| `max_deposit_period` | 24 h | `MAX_DEPOSIT_PERIOD` (default 24h) |
| `voting_period` | 24 h (10 min on a demo chain) | `VOTING_PERIOD` (default 24h) |
| `expedited_voting_period` | 1 h | `EXPEDITED_VOTING_PERIOD` (default 1h, must be < voting period) |
| `quorum` | 33.4 % | SDK default |
| `threshold` | 50 % of non-abstain votes | SDK default |
| `veto_threshold` | 33.4 % | SDK default |
| `expedited_threshold` | 66.7 % | SDK default |
| `expedited_min_deposit` | 5,000 AUR | 5 × `GOV_MIN_DEPOSIT` |

To read the live values, run `aurixd q gov params -o json`.

## Timeline

```
submit (deposit >= min_deposit) ──► VOTING_PERIOD ──► voting_end: tally
      │ deposit < min                                  ├─ quorum met, yes > 50 %, veto < 33.4 % → PASSED, messages executed
      └─► DEPOSIT_PERIOD (≤ max_deposit_period)        ├─ otherwise → REJECTED (deposit refunded)
                                                       └─ no quorum / veto → REJECTED/FAILED, deposit burned
```

The messages of a passed proposal run in the EndBlocker of the block in which voting ends. If a
message fails, the status is `PROPOSAL_STATUS_FAILED` and no state changes.

## Drafting a proposal JSON

```json
{
  "messages": [ { "@type": "/<proto package>.Msg...", "authority": "<gov address>", "...": "..." } ],
  "metadata": "",
  "deposit": "1000000000uaur",
  "title": "Short title",
  "summary": "What and why",
  "expedited": false
}
```

To check that a message is well formed, generate it with `--generate-only` where a CLI command
exists. Otherwise copy the exact field names from the proto definitions (proto-JSON, snake_case).
Every message must use the gov address as `authority`.

## Worked example: add an attestor to `anchor_signers`

This is the exact sequence that `scripts/gov-test.sh` verifies. The example adds a second attestor
and raises the threshold to 2. Adding a 4th attestor to a 3-signer set works the same way: append
the address and keep or raise `anchor_threshold`.

```bash
CHAIN="--chain-id aurix-testnet-1 --node tcp://127.0.0.1:26657 --keyring-backend test --fees 5000uaur --gas 400000 -y"
GOV=$(aurixd q auth module-account gov -o json | jq -r .account.value.address)
NEW=$(aurixd seed-address "my-attestor-seed")          # or the operator's own aur1... address
CUR=$(aurixd q smartmoney params -o json | jq -c .params)
NEWP=$(jq -c --arg a "$NEW" '.anchor_signers += [$a] | .anchor_threshold = 2' <<<"$CUR")

jq -n --arg gov "$GOV" --argjson p "$NEWP" '{
  messages: [{"@type": "/aurix.smartmoney.v1.MsgUpdateParams", authority: $gov, params: $p}],
  metadata: "", deposit: "1000000000uaur",
  title: "Add a second attestor", summary: "Add attestor 2 to anchor_signers and require 2 attestations",
  expedited: false}' > prop.json

aurixd tx gov submit-proposal prop.json --from val0 $CHAIN
aurixd q gov proposals -o json | jq '.proposals[-1] | {id, status}'
aurixd tx gov vote <id> yes --from val0 $CHAIN           # each validator votes
aurixd q gov tally <id>
# after voting_end:
aurixd q gov proposal <id> -o json | jq -r .proposal.status   # PROPOSAL_STATUS_PASSED
aurixd q smartmoney params                                  # new signer, anchor_threshold 2
```

`MsgUpdateParams` **replaces** the whole `Params`, so always start from the current params. The params
are validated: each signer must be a valid `aur1` address with no duplicates, and
`1 <= anchor_threshold <= len(anchor_signers)`. If validation fails, the proposal ends as `FAILED`.
Run the new attestor (`attestor/`, with `ATTESTOR_SEED`) **before** you raise the threshold. Until
enough attestors are live, proofs stay `PENDING`.

`gov-test.sh` also checks two negative cases:
- 1 yes and 3 no gives `PROPOSAL_STATUS_REJECTED`, and the params are unchanged.
- A `MsgUpdateParams` signed directly by a validator account is rejected with code 12 (`invalid authority`).

## Software upgrade procedure

1. **Release** a binary that registers the upgrade handler under the plan name. Handlers are listed in
   `chain/app/upgrades.go` (`Upgrades`). The first one is `v2`, which runs `RunMigrations`. New KV
   stores go in `StoreUpgrades`. They are applied by the store loader when the new binary starts at
   the upgrade height.
2. **Propose** the plan at a height H, far enough ahead to cover the voting period and some margin
   (block time is about 1 s on the local net):
   ```bash
   aurixd tx upgrade software-upgrade v2 --upgrade-height H --upgrade-info '{}' --no-validate \
     --title "Upgrade to v2" --summary "..." --deposit 1000000000uaur --from val0 $CHAIN
   aurixd tx gov vote <id> yes --from valN $CHAIN
   aurixd q upgrade plan
   ```
   With `--upgrade-info` you can give binary download URLs and checksums in the cosmovisor format.
   In that case, drop `--no-validate`.
3. **Halt:** at height H the old binary stops with `UPGRADE "v2" NEEDED at height: H` and writes
   `data/upgrade-info.json`. Nothing is committed at H.
4. **Swap binaries and restart.** Stop the node, install the new `aurixd` and start it with the same
   home directory. In its PreBlocker the new binary runs the `v2` handler at H, and the chain continues.
   To check, run `aurixd q upgrade applied v2`, which returns the height H.
5. **Cosmovisor (optional):** put the new binary in
   `$DAEMON_HOME/cosmovisor/upgrades/v2/bin/aurixd` before H, and cosmovisor swaps it automatically.

Use these safety rules:
- Never start a binary that lacks the handler past H. It halts again.
- Never start the new binary before H. A binary that knows "v2" panics if it sees the upgrade plan
  executed early.
- To skip an upgrade in an emergency, use `aurixd start --unsafe-skip-upgrades H` (coordinate this
  with the other validators).

`scripts/upgrade-drill.sh` runs the full cycle. It builds an "old" binary from the same code with
the v2 handler disabled at link time
(`-ldflags "-X github.com/joeylukoji/aurix/chain/app.DisabledUpgrades=v2"`). It then writes state
with a bank send, passes a v2 proposal, and checks that all 4 nodes halt at H. Finally, it restarts
them with `chain/build/aurixd` and checks the result: blocks continue past H,
`q upgrade applied v2 = H`, the balance and the smartmoney params are preserved, and new txs are
accepted.

## Expedited proposals

Set `"expedited": true` in the proposal JSON, or pass `--expedited` to `software-upgrade`. Expedited
proposals vote for `expedited_voting_period` (1 h on testnet) and need a 66.7 % threshold. They also
need the expedited minimum deposit. An expedited proposal that does not reach the expedited threshold
is not rejected. Instead, it converts to a regular proposal and gets the regular voting period. Use
expedited proposals only for emergency fixes, such as removing a compromised attestor from
`anchor_signers` or an urgent security upgrade.
