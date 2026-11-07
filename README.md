# AURIX — Independent Intelligent Monetary Protocol

> **Programmable Value, Verifiable Trust.**
> Project submitted to the **PayPal AI Hackathon 2026** — V1 testnet.

AURIX is a testnet monetary protocol where an AI assesses a payment **before** it is confirmed, where payment conditions are enforced by the protocol (escrow), and where **PayPal Sandbox** acts as a bridge to the real economy without ever replacing the native currency **AUR**.

**Core principle: the AI advises, the protocol decides, the human approves.**

---

## Table of contents

- [V1 features](#v1-features)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Repository layout](#repository-layout)
- [Quick start](#quick-start)
- [Implementation status](#implementation-status)
- [Way of working: Scrum](#way-of-working-scrum)
- [Documentation](#documentation)
- [Security](#security)
- [Limitations and scope](#limitations-and-scope)

---

## V1 features

| Module | Description |
| --- | --- |
| **AURIX Wallet & Transfer** | Create a demo wallet, check the balance, send/receive AUR (CometBFT consensus) |
| **Smart Money** | Finite-state escrow: lock, release on condition, refund at deadline |
| **Neural Guardian** | Advisory risk score with explained reasons (SHAP) before confirmation |
| **PayPal connector** | USD payment via PayPal Orders API (local simulator now, Sandbox next), signed and idempotent webhook, tracking proof anchored on AURIX |

## Architecture

Four layers; the core depends on no upper layer:

| Layer | Content | Authority |
| --- | --- | --- |
| 4. External connectivity | PayPal Sandbox | None over AUR balances |
| 3. Intelligence | Neural Guardian | Advisory only |
| 2. Programmable Money | `x/smartmoney` (escrow) | Deterministic execution |
| 1. Monetary protocol | AUR, CometBFT, `x/aurixbank` | Source of truth for balances |

```mermaid
flowchart LR
    U[Web wallet<br/>browser signing] --> GW[API Gateway :8080<br/>FastAPI + SQLite]
    GW --> AI[Neural Guardian :8000]
    GW --> PP[PayPal simulator :8090<br/>Sandbox later]
    PP -- signed webhook --> GW
    GW --> CHAIN[AURIX node aurixd<br/>Cosmos SDK + CometBFT<br/>RPC :26657, REST :1317]
```

Indexer, PostgreSQL and Redis are planned, not built. Full details: [`docs/architecture/services.md`](docs/architecture/services.md).

## Tech stack

| Need | Technology |
| --- | --- |
| Consensus | CometBFT (PoS + BFT) |
| Blockchain core | Cosmos SDK v0.50 + Go (`x/auth`, `x/bank`, `x/staking`, custom `x/smartmoney`) |
| Gateway | FastAPI *(Spring Boot in the original target)* |
| AI | Python, FastAPI, LightGBM + SHAP (PyTorch Geometric on the roadmap) |
| Data | SQLite (V1 index), PostgreSQL + Redis planned |
| Front end | Web wallet + explorer, AG Grid |
| API | OpenAPI (APIMatic) |
| Deployment | Docker Compose, Render (Kubernetes as target) |

## Repository layout

```
aurix/
├── chain/        # Cosmos SDK node: app/, x/smartmoney, proto/, cmd/aurixd
├── gateway/      # API Gateway / orchestration
├── guardian/     # Neural Guardian (FastAPI)
├── paypal-sim/   # Temporary local PayPal simulator (tests only)
├── web/          # Wallet, explorer, AG Grid
├── api/          # openapi.json (generated)
├── deploy/       # docker-compose.yml, render.yaml
├── docs/         # Documentation (Scrum, modelling, diagrams)
├── tests/e2e/    # Playwright demo journey
└── .env.example
```

## Quick start

### With the dev scripts

```bash
scripts/dev-up.sh     # start chain, guardian, paypal-sim, gateway
scripts/test-all.sh   # run all test suites
scripts/dev-down.sh   # stop everything
```

### With Docker Compose

```bash
docker compose -f deploy/docker-compose.yml up --build
```

Then open http://localhost:8080. By default PayPal is served by the **local PayPal simulator** (`paypal-sim/`, port 8090, clearly labelled in the UI): the buyer pays on the simulator's page, the capture is confirmed by a signed webhook, then the proof is anchored. To switch to the real PayPal Sandbox later, follow [`paypal-sim/README.md`](paypal-sim/README.md#switching-to-the-real-paypal-sandbox-and-deleting-this-folder).

### Without Docker

Run the commands below from the repository root, each in its own terminal.

```bash
cd chain && go build -o build/aurixd ./cmd/aurixd && AURIXD=./build/aurixd scripts/init-local.sh && ./build/aurixd start
```

```bash
cd guardian && python -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/uvicorn app.main:app --port 8000
```

```bash
cd paypal-sim && python -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/uvicorn app.main:app --port 8090
```

```bash
cd gateway && python -m venv .venv && .venv/bin/pip install -r requirements.txt && PAYPAL_ENV=simulator PAYPAL_BASE_URL=http://localhost:8090 PAYPAL_CLIENT_ID=sim-client-id PAYPAL_CLIENT_SECRET=sim-client-secret PAYPAL_WEBHOOK_ID=SIM-WEBHOOK-ID .venv/bin/uvicorn app.main:app --port 8080
```

### Tests

```bash
cd chain && go test ./...
```

```bash
cd guardian && .venv/bin/python -m pytest -q
```

```bash
cd paypal-sim && .venv/bin/python -m pytest -q
```

```bash
cd gateway && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest -q
```

The gateway tests build, initialise and start a real `aurixd` node (needs Go and `jq`). The browser journey (wallet → send → escrow → PayPal → explorer) runs with Playwright against a running stack:

```bash
node tests/e2e/demo-journey.js
```

## Implementation status

| Component | Status | Notes |
| --- | --- | --- |
| `chain/` | ✅ V1 | **Cosmos SDK v0.50 + CometBFT v0.38**, **4 validators** by default (BFT: survives 1 faulty node, verified with `scripts/bft-demo.sh`). `x/aurixbank` = `x/auth` + `x/bank` (`uaur`, fixed supply, sequences as nonces, fees); custom `x/smartmoney` module (escrow, proof anchoring). See [`chain/README.md`](chain/README.md). |
| `guardian/` | ✅ V1 | FastAPI + LightGBM, SHAP reasons via native TreeSHAP. **Trained on PaySim** (Kaggle, 6.3 M transactions), out-of-time ROC-AUC 0.978 / PR-AUC 0.939. See [`guardian/MODEL_CARD.md`](guardian/MODEL_CARD.md). |
| `gateway/` | ✅ V1 | FastAPI (variant recommended in the architecture doc). PayPal Orders v2, webhook signature verification, idempotency, proof anchoring. SQLite index (PostgreSQL planned). |
| `paypal-sim/` | 🧪 temporary | Local PayPal API simulator used until the real Sandbox is connected. |
| `web/` | ✅ V1 | secp256k1 keys generated in the browser; Cosmos txs (`SIGN_MODE_DIRECT`) signed locally. AG Grid explorer. |
| `api/openapi.json` | ✅ | Generated from the gateway (`create_app().openapi()`). |
| `deploy/` | ⚠️ | Compose and Render blueprint written; not yet deployed online. See [`deploy/README.md`](deploy/README.md). |
| `scripts/` | ✅ | `dev-up.sh`, `dev-down.sh`, `test-all.sh`. |
| Indexer, PostgreSQL, Redis | ❌ planned | The gateway reads the chain directly and keeps a SQLite index. |
| Real PayPal Sandbox | ❌ pending | Needs Sandbox credentials; config-only switch. |
| Demo video, Devpost | 📝 drafted | [`docs/submission/`](docs/submission/devpost.md) |

## Way of working: Scrum

The project runs on **Scrum with one-day sprints** (November 7–12, 2026):

| Sprint | Date | Goal |
| --- | --- | --- |
| S0 | Nov 7 | Scope freeze, backlog, environment |
| S1 | Nov 8 | Payments: PayPal Orders + AUR transfer |
| S2 | Nov 9 | Smart Money + Neural Guardian |
| S3 | Nov 10 | UI: wallet, explorer, AG Grid |
| S4 | Nov 11 | Stabilisation, README, video |
| S5 | Nov 12 | Devpost submission |

Backlog, user stories, Definition of Done and ceremonies: [`docs/scrum/`](docs/scrum/README.md).

## Documentation

| Document | Content |
| --- | --- |
| [docs/README.md](docs/README.md) | Documentation index |
| [docs/scrum/README.md](docs/scrum/README.md) | Scrum setup, roles, ceremonies, DoR/DoD |
| [docs/scrum/product-backlog.md](docs/scrum/product-backlog.md) | Epics, user stories, priorities, estimates |
| [docs/scrum/sprints.md](docs/scrum/sprints.md) | Detailed sprint plan and burndown |
| [docs/architecture/services.md](docs/architecture/services.md) | Service modelling |
| [docs/architecture/diagrams.md](docs/architecture/diagrams.md) | Use cases, sequences, states, classes, ER, deployment |
| [docs/submission/devpost.md](docs/submission/devpost.md) | Devpost project description |
| [docs/submission/demo-script.md](docs/submission/demo-script.md) | < 3 min demo video script |
| [guardian/MODEL_CARD.md](guardian/MODEL_CARD.md) | Neural Guardian model card |
| [deploy/README.md](deploy/README.md) | Deployment and healthchecks |
| [AURIX_SOFTWARE_ARCHITECTURE.md](AURIX_SOFTWARE_ARCHITECTURE.md) | Reference software architecture ([French original](AURIX_ARCHITECTURE_LOGICIELLE.md)) |
| [AURIX_hackathon_brief.md](AURIX_hackathon_brief.md) | Hackathon pitch file ([French original](AURIX_dossier_hackathon.md)) |

## Security

1. The on-chain ledger is the **single source of truth** for AUR balances.
2. The AI can neither validate, invalidate nor modify a transaction.
3. USD funds (PayPal) never become AUR: only a **proof (hash)** is anchored.
4. PayPal webhooks are signature-verified and idempotent.
5. No secrets or private keys in the repository.

## Limitations and scope

- Demonstration testnet: **AUR has no real monetary value**.
- The 4 validators run on one machine, operated by one team: real BFT consensus, not yet independent multi-operator decentralisation.
- Roadmap: multiple validators, additional regulated gateways, GNN, Kubernetes.
