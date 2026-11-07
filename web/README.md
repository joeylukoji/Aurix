# AURIX web wallet (`web/`)

A static site: plain HTML/JS, no build step. Keys are generated and kept in the browser; every
AUR transaction is built and signed here (`cosmos-tx.js`) and broadcast **directly to chain
nodes** (`chain.js`). The gateway is optional: it is only needed for PayPal and Neural Guardian
scoring.

## Files

| File | Role |
|------|------|
| `config.json` | chain id, chain endpoints, optional gateway / faucet / guardian URLs |
| `chain.js` | `window.AurixChain`: REST reads, broadcast, endpoint failover, normalisers, settings storage |
| `cosmos-tx.js` | protobuf tx encoding + secp256k1 signing (SIGN_MODE_DIRECT) |
| `app.js` | UI |

## `config.json`

```json
{
  "chain_id": "aurix-testnet-1",
  "endpoints": [{ "rest": "https://rest.node.example", "rpc": "https://rpc.node.example", "label": "operator A" }],
  "gateway": "",
  "faucet": "",
  "guardian": ""
}
```

- `chain_id`: the wallet refuses to sign if the node it talks to reports another chain id
  (empty = accept whatever the node reports).
- `endpoints`: Cosmos REST (port 1317) of several nodes, tried in order. On a network error,
  a timeout (6 s) or an HTTP 5xx the next one is used, and the last one that worked is tried
  first afterwards. `rpc` is informational for now (CometBFT RPC usually has no CORS);
  blocks are read through REST (`/cosmos/base/tendermint/v1beta1/blocks/...`).
  Nodes must allow the wallet's origin (CORS; localnet uses `enabled-unsafe-cors`).
- `gateway`: URL of the AURIX gateway, for PayPal and risk scoring.
  - empty string: **auto** — the page's own origin is used if it answers `GET /api/health`
    (this is the case when the gateway serves `web/`), otherwise there is no gateway;
  - `"none"`: no gateway even if the origin has one.
  A cross-origin gateway must send CORS headers for the wallet origin.
- `faucet`: base URL of a service exposing `POST /api/wallets/{addr}/faucet` (the gateway's
  route). Empty = the gateway's faucet when a gateway is available; otherwise the faucet button
  is hidden. Testnet only. A cross-origin faucet must send CORS headers.
- `guardian`: optional direct Neural Guardian URL (`POST /v1/risk/score`), used when no gateway
  answers. Without either, the send/escrow screens show "Analysis unavailable" and the payment
  can still be approved.

User overrides (Settings tab, stored in `localStorage`, ignored if storage is blocked):
custom nodes (`aurix.nodes`, tried before the config endpoints) and a gateway URL
(`aurix.gateway`). For tests, `?nodes=http://a:1317,http://b:1317` puts endpoints first too.

## Served by the gateway

`gateway/app/main.py` mounts `web/` at `/` (`StaticFiles`, override with `WEB_DIR`). The
gateway has no chain REST proxy, so the choice is:

1. **Gateway**: same origin, auto-detected (`"gateway": ""`). PayPal, Guardian and the faucet
   go through it as before.
2. **Chain**: the `endpoints` from `config.json` (localnet default `localhost:1317` / `:2317`,
   i.e. node0 and node1 of `scripts/dev-up.sh` without `PORT_OFFSET`).
3. **Fallback**: if no endpoint is reachable (e.g. dev-up with a `PORT_OFFSET`, so the ports in
   `config.json` do not match) and a gateway answers, chain reads and broadcasts fall back to the
   gateway's existing API (`/api/chain/status`, `/api/wallets/{a}`, `/api/escrows`,
   `/api/explorer/*`, `/api/txs`). The header tooltip on the chain status says which path is in
   use. Signing stays in the browser in every case.

## Static deployment (CDN, IPFS)

Copy `web/` as is and edit `config.json` (public TLS endpoints of independent operators, the
public chain id, and optionally a gateway URL). With no gateway the wallet still shows
balances, history, escrows and the explorer, and can send, lock, release and refund; the PayPal
tab is disabled and says so. Paths are relative, so it works from a sub-path or an IPFS gateway.

Local try-out: `cd web && python3 -m http.server 8088`, then open `http://localhost:8088`.

## Attestations

The proof card in the explorer and the PayPal tab show `attestations k/n`, from
`/aurix/smartmoney/v1/proofs/{hash}` (`attestations`, `status`) and
`/aurix/smartmoney/v1/params` (`anchor_threshold`). The PayPal tab prefers the gateway's
`order.attestations` when present. Proofs from a chain without attestation fields are shown as
confirmed by their submitter (1/1).

Limit: the wallet trusts the node it reads from (no light-client verification); use several
independent endpoints.
