# AURIX deployment

Four services:

| Service | Port | Health | Notes |
|---|---|---|---|
| `chain` (aurixd, Cosmos SDK) | 26657 RPC, 1317 REST | `GET :26657/status` | single validator; state in `/data` (needs persistent disk) |
| `guardian` (Neural Guardian, logistic regression) | 8000 | `GET /health` | internal only |
| `paypal-sim` (PayPal simulator) | 8090 | `GET /sim/health` | **public**: the browser is sent to its approval page |
| `gateway` (FastAPI + web UI) | 8080 | `GET /api/health` | public; serves `web/` |
| `delivery-sim` (SIMULATED carrier, MVP 0.2) | 8087 | `GET /health` | called by the wallet's demo buttons; signs delivery evidence for the conditional USD payment |

## Run locally (Docker Compose)

```bash
docker compose -f deploy/docker-compose.yml up --build
# open http://localhost:8080
```

Services: `chain-init` writes a 4-validator genesis, then `chain0..chain3` run the validators;
`guardian` and `guardian2` are two independently keyed Neural Guardians (the gateway needs
`GUARDIAN_QUORUM=2` valid signed scores); `attestor1` and `attestor2` independently re-check every
PayPal capture, and a proof is `CONFIRMED` once 2 of the 3 anchor signers (gateway + 2 attestors,
`ANCHOR_THRESHOLD=2`) attested it. Start order is enforced with healthchecks: `gateway` starts only
after `chain0`, both guardians and `paypal-sim` are healthy; each attestor waits for its chain node. Chain/gateway state lives in the `chain-data` / `gateway-data`
volumes; reset with `docker compose -f deploy/docker-compose.yml down -v`.

Optional overrides go in a `.env` at the repo root (read by the gateway and by compose).

## Deploy on Render

1. Use `deploy/render.yaml` as a Blueprint (copy it to `render.yaml` at the repo root, or select the path).
2. Fill the `sync: false` secrets (`AURIX_FAUCET_SEED`, `AURIX_GATEWAY_SEED`) in the dashboard.
   Use the **same values** on chain and gateway.
3. `aurix-chain` is a private service **with a persistent disk at `/data`** (paid plan). Without it the chain
   re-creates genesis on every deploy.
4. `aurix-paypal-sim` is a public web service. If its URL is not `https://aurix-paypal-sim.onrender.com`,
   update `SIM_PUBLIC_URL`. Its webhooks go to the gateway over the private network
   (`http://aurix-gateway:8080/api/paypal/webhook`).
5. `aurix-chain` runs one validator via `init-localnet.sh` (`VALIDATORS=1`) whose genesis lists the
   gateway and `aurix-attestor` as anchor signers with `ANCHOR_THRESHOLD=2`, so every PayPal proof
   needs both attestations. `aurix-attestor` is a private service; give it its own key
   (`ATTESTOR_MNEMONIC`) for anything public.
6. The gateway DB is SQLite in `/tmp` (ephemeral) — fine for a demo; attach a disk and set
   `DATABASE_URL=sqlite:////data/aurix.db` to keep it.

All images honour `PORT`, which Render sets for web services.

## Environment variables

| Variable | Service | Default | Purpose |
|---|---|---|---|
| `AURIX_CHAIN_ID` | chain | `aurix-testnet-1` | chain id for genesis |
| `AURIX_HOME` | chain | `/data/aurix` | node home dir |
| `AURIX_FAUCET_SEED` | chain, gateway | `aurix-dev-faucet` | **dev only** faucet key seed (must match) |
| `AURIX_GATEWAY_SEED` | chain, gateway | `aurix-dev-gateway` | **dev only** gateway signer seed (must match) |
| `CHAIN_RPC_URL` | gateway | `http://localhost:26657` | CometBFT RPC |
| `CHAIN_REST_URL` | gateway | `http://localhost:1317` | Cosmos REST |
| `GUARDIAN_URL` | gateway | `http://localhost:8000` | Neural Guardian (single; superseded by `GUARDIAN_URLS`) |
| `GUARDIAN_URLS` | gateway | `$GUARDIAN_URL` | comma separated Guardians; all are queried and their signatures verified |
| `GUARDIAN_QUORUM` | gateway | `1` | valid signed scores needed for `quorum_met` (aggregate = most severe decision) |
| `GUARDIAN_PUBKEYS` | gateway | empty (any key) | optional allowlist of guardian pubkeys (33-byte hex), from `keygen.sh` |
| `AURIX_ENV` | gateway, attestor, guardian | empty | `production` refuses every dev seed key |
| `GATEWAY_MNEMONIC` / `GATEWAY_KEY_FILE` | gateway | — | real gateway signer (BIP39 checksum verified; key file = 32-byte hex, 0600) |
| `FAUCET_MNEMONIC` / `FAUCET_KEY_FILE` | gateway | — | real faucet key |
| `GUARDIAN_MNEMONIC` / `GUARDIAN_KEY_FILE` / `GUARDIAN_SEED` | guardian | built-in dev seed | key that signs risk scores |
| `ATTESTOR_MNEMONIC` / `ATTESTOR_KEY_FILE` / `ATTESTOR_SEED` | attestor | none (exits) | attestor signer |
| `AURIX_GATEWAY_ADDR` / `AURIX_FAUCET_ADDR` / `ATTESTOR_ADDRS` | chain (init) | seed-derived | genesis addresses of real keys |
| `DATABASE_URL` | gateway | `sqlite:////data/aurix.db` (image) | SQLite path |
| `WEB_DIR` | gateway | `/srv/web` (image) | static frontend |
| `PAYPAL_ENV` | gateway | `sandbox` (`simulator` in compose/Render) | PayPal mode |
| `PAYPAL_BASE_URL` | gateway | `https://api-m.sandbox.paypal.com` | PayPal API base |
| `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` | gateway | sim values | PayPal REST credentials |
| `PAYPAL_WEBHOOK_ID` | gateway | `SIM-WEBHOOK-ID` | webhook signature verification |
| `SIM_PUBLIC_URL` | paypal-sim | `http://localhost:8090` | browser-facing URL of the approval page |
| `SIM_WEBHOOK_URL` | paypal-sim | `http://localhost:8080/api/paypal/webhook` | where webhooks are POSTed |
| `SIM_CLIENT_ID` / `SIM_CLIENT_SECRET` / `SIM_WEBHOOK_ID` | paypal-sim | `sim-client-id` / `sim-client-secret` / `SIM-WEBHOOK-ID` | must match gateway `PAYPAL_*` |
| `SIM_WEBHOOK_DELAY` | paypal-sim | `0.5` | seconds before webhook delivery |
| `ESCROW_KEY_FILE` | payments | dev seed (refused in production) | Ed25519 seed (hex) signing escrow attestations, decisions and proof packages |
| `ESCROW_TRUSTED_KEYS_FILE` | payments | dev carrier + dev arbiter (none in production) | `{"keys": [...]}` of the carriers and arbiters accepted as evidence signers |
| `DELIVERY_SIM_KEY_FILE` | delivery-sim | dev seed (refused in production) | Ed25519 seed (hex) of the simulated carrier |
| `AURIX_SECRETS_DIR` | compose | `./secrets` (i.e. `deploy/secrets/`) | host folder mounted read-only at `/run/aurix-secrets`; see [`secrets/README.md`](secrets/README.md) |
| `ADVISOR_LLM_URL` / `ADVISOR_LLM_MODEL` / `ADVISOR_LLM_API_KEY` | payments | empty / `gemma4:e4b` / empty | AI advisor (advisory only), any OpenAI-compatible endpoint; empty URL = template text |
| `DELIVERY_CORS_ORIGINS` | delivery-sim (compose) | `*` | origins allowed to call the demo carrier; set to the wallet's origin when hosted |
| `PORT` | all HTTP images | 8080 / 8000 / 8090 / 8087 | listen port |

## Switching from the simulator to the real PayPal Sandbox

1. Create a Sandbox REST app at developer.paypal.com; note client id/secret.
2. Add a webhook pointing to `https://<gateway-host>/api/paypal/webhook` for `PAYMENT.CAPTURE.COMPLETED`; note its id.
3. Set on the gateway:
   `PAYPAL_ENV=sandbox`, `PAYPAL_BASE_URL=https://api-m.sandbox.paypal.com`,
   `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_WEBHOOK_ID` (in `.env` locally, or as secrets on Render).
4. Remove the `paypal-sim` service (compose: the service and its `depends_on` entry; Render: the
   `aurix-paypal-sim` block).

## Real keys (anything public)

The `*_SEED` variables derive keys from a public string: anyone can recompute them. Before exposing a
deployment, generate real keys with `networks/aurix-testnet-2/scripts/keygen.sh` (fresh BIP39 mnemonics via
`aurixd keys add`, written to a 0700 directory, one 0600 env file per service, only addresses printed):

1. `AURIXD=chain/build/aurixd networks/aurix-testnet-2/scripts/keygen.sh ~/aurix-keys`
2. Chain init: `AURIX_GATEWAY_ADDR`, `AURIX_FAUCET_ADDR` (and `ATTESTOR_ADDRS`) from `~/aurix-keys/addresses.txt`.
3. Gateway: copy `~/aurix-keys/gateway.env` into `../.env` (it sets `AURIX_ENV=production`, the mnemonics,
   `GUARDIAN_PUBKEYS`); each guardian / attestor gets its own `guardian-N.env` / `attestor-N.env`.
4. Back the mnemonics up offline, then delete `~/aurix-keys` from the build machine.
