# Product Backlog

MoSCoW priority: **M** (Must), **S** (Should), **C** (Could), **W** (Won't, V1).

## Epics

| ID | Epic | Layer | Main service |
| --- | --- | --- | --- |
| E1 | Wallet & AUR transfers | 1 | AURIX node (`x/aurixbank`) |
| E2 | Smart Money (escrow) | 2 | AURIX node (`x/smartmoney`) |
| E3 | Neural Guardian | 3 | Guardian |
| E4 | PayPal connector | 4 | API Gateway |
| E5 | Explorer & UI | — | Web + Indexer |
| E6 | Infrastructure & delivery | — | Deploy |

## User stories

| ID | Epic | User story | Acceptance criteria | Prio | Pts | Status |
| --- | --- | --- | --- | --- | --- | --- |
| US-01 | E6 | As a developer, I want a Docker Compose environment so that I can start all services with one command | `docker compose up` starts chain, gateway, guardian, indexer, postgres, redis, web | M | 5 | Done (Compose: chain, guardian, paypal-sim, gateway+web; no indexer/postgres/redis) |
| US-02 | E1 | As a user, I want to create a demo wallet so that I get an `aur1…` address | Address returned; private key never stored in plain text on the server | M | 3 | Done (keys generated in browser) |
| US-03 | E1 | As a user, I want to see my AUR balance so that I know my funds | Balance read from the chain, not from PostgreSQL | M | 2 | Done |
| US-04 | E1 | As a user, I want to send AUR so that I can pay a peer | Signed `MsgSend`, fees charged, nonce incremented, replay rejected, balance never negative | M | 8 | Done |
| US-05 | E2 | As a buyer, I want to lock AUR in escrow so that I can pay conditionally | `MsgCreateEscrow` → `LOCKED` state, funds debited | M | 5 | Done |
| US-06 | E2 | As an arbiter/sender, I want to release an escrow so that the recipient gets paid | `LOCKED → RELEASED` only once | M | 3 | Done |
| US-07 | E2 | As a sender, I want a refund at the deadline so that I don't lose my funds | `LOCKED → REFUNDED` if block height ≥ deadline; no release afterwards | M | 3 | Done |
| US-08 | E3 | As a user, I want a risk score before confirming so that I avoid mistakes or fraud | `POST /v1/risk/score` returns score, level, reasons, `advisory_only: true` | M | 8 | Done |
| US-09 | E3 | As a user, I want to understand why an alert is raised | At least 2 readable SHAP reasons displayed | S | 3 | Done |
| US-10 | E3 | As a user, I want to proceed if the AI is unavailable | "Analysis unavailable" message, transaction still possible | M | 2 | Done |
| US-11 | E4 | As a buyer, I want to create a PayPal Sandbox order in USD | Orders API called, order ID returned, Guardian score attached | M | 5 | Partial (PayPal simulator; Sandbox pending credentials) |
| US-12 | E4 | As a buyer, I want to explicitly approve before capture | No capture without approval (invariant 8) | M | 3 | Done |
| US-13 | E4 | As the system, I want to verify PayPal webhooks | Invalid signature rejected; duplicate ignored (HTTP 200) | M | 5 | Done (against simulator) |
| US-14 | E4 | As an auditor, I want a tracking proof on AURIX | `MsgAnchorProof` with hash; no AUR created | M | 3 | Done |
| US-15 | E5 | As a user, I want a history and a block explorer | AG Grid table of blocks and transactions | S | 5 | Done |
| US-16 | E5 | As a user, I want to visually distinguish USD from AUR | Separate balances and colours | M | 2 | Done |
| US-17 | E6 | As a judge, I want an online demo | Render deployment reachable | S | 5 | Partial (render.yaml written, not deployed) |
| US-18 | E6 | As a judge, I want an OpenAPI spec | Valid `api/openapi.yaml`, importable into APIMatic | S | 3 | Done (api/openapi.json, JSON not YAML) |
| US-19 | E6 | As the team, I want a video under 3 min | Video hosted, subtitled | M | 5 | Not done (script ready: docs/submission/demo-script.md) |
| US-20 | E3 | Graph-based relational analysis (GNN) | — | W | 13 | Won't (V1) |
| US-21 | E6 | Kubernetes deployment | — | W | 8 | Won't (V1) |
| US-22 | E1 | Multi-validator network | — | W | 13 | Won't (V1) |

**V1 total (M + S)**: 78 story points + 3 points of setup tasks (S0) = **81 points**.

Status as of Nov 7, 2026 (end of S0). Remaining V1 work: real PayPal Sandbox (US-11/13), online deployment (US-17), video (US-19).
