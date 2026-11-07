# AURIX deployment

Four services:

| Service | Port | Health | Notes |
|---|---|---|---|
| `chain` (aurixd, Cosmos SDK) | 26657 RPC, 1317 REST | `GET :26657/status` | single validator; state in `/data` (needs persistent disk) |
| `guardian` (Neural Guardian, LightGBM) | 8000 | `GET /health` | internal only |
| `paypal-sim` (PayPal simulator) | 8090 | `GET /sim/health` | **public**: the browser is sent to its approval page |
| `gateway` (FastAPI + web UI) | 8080 | `GET /api/health` | public; serves `web/` |

## Run locally (Docker Compose)

```bash
docker compose -f deploy/docker-compose.yml up --build
# open http://localhost:8080
```

Start order is enforced with healthchecks: `gateway` starts only after `chain`, `guardian`
and `paypal-sim` are healthy. Chain/gateway state lives in the `chain-data` / `gateway-data`
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
5. The gateway DB is SQLite in `/tmp` (ephemeral) — fine for a demo; attach a disk and set
   `DATABASE_URL=sqlite:////data/aurix.db` to keep it.

All images honour `PORT`, which Render sets for web services.

## Environment variables

| Variable | Service | Default | Purpose |
|---|---|---|---|
| `AURIX_CHAIN_ID` | chain | `aurix-testnet-1` | chain id for genesis |
| `AURIX_HOME` | chain | `/data/aurix` | node home dir |
| `AURIX_FAUCET_SEED` | chain, gateway | `aurix-dev-faucet` | faucet key seed (must match) |
| `AURIX_GATEWAY_SEED` | chain, gateway | `aurix-dev-gateway` | gateway signer seed (must match) |
| `CHAIN_RPC_URL` | gateway | `http://localhost:26657` | CometBFT RPC |
| `CHAIN_REST_URL` | gateway | `http://localhost:1317` | Cosmos REST |
| `GUARDIAN_URL` | gateway | `http://localhost:8000` | Neural Guardian |
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
| `PORT` | all HTTP images | 8080 / 8000 / 8090 | listen port |

## Switching from the simulator to the real PayPal Sandbox

1. Create a Sandbox REST app at developer.paypal.com; note client id/secret.
2. Add a webhook pointing to `https://<gateway-host>/api/paypal/webhook` for `PAYMENT.CAPTURE.COMPLETED`; note its id.
3. Set on the gateway:
   `PAYPAL_ENV=sandbox`, `PAYPAL_BASE_URL=https://api-m.sandbox.paypal.com`,
   `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_WEBHOOK_ID` (in `.env` locally, or as secrets on Render).
4. Remove the `paypal-sim` service (compose: the service and its `depends_on` entry; Render: the
   `aurix-paypal-sim` block).
