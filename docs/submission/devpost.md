# AURIX — Devpost submission

> **Programmable Value, Verifiable Trust.** PayPal AI Hackathon 2026.

## Inspiration

Payments fail people in two ways: money is sent to the wrong person or a fraudster, and conditional deals ("pay when delivered") depend on trusting a middleman. We wanted a payment rail where an AI explains the risk *before* you confirm, where conditions are enforced by the protocol itself, and where it still connects to the real economy through PayPal.

## What it does

- **AURIX Wallet & Transfer**: create a wallet in the browser, get test AUR from a faucet, send AUR. Keys never leave the browser; each transaction is signed locally and confirmed in a CometBFT block.
- **Smart Money**: lock AUR in an on-chain escrow with a recipient, an arbiter and a deadline. It can be released once by the sender or arbiter, or refunded after the deadline.
- **Neural Guardian**: before every send, escrow or USD order, a logistic-regression model returns a calibrated risk score with human-readable SHAP reasons. It is **advisory only**: the AI advises, the protocol decides, the human approves. If the AI is down, payments still work.
- **PayPal connector**: pay in USD with the PayPal Orders v2 flow. The buyer approves on a PayPal page, the user explicitly confirms the capture, a signed webhook is verified and de-duplicated, and a SHA-256 proof of the payment is anchored on AURIX. USD never becomes AUR.
- **Explorer**: blocks, transactions, escrows and proofs in an AG Grid explorer.

## How we built it

- **Chain**: Cosmos SDK v0.50 + CometBFT v0.38 (`aurixd`). `x/auth` + `x/bank` provide balances, sequences (replay protection) and fees; a custom `x/smartmoney` module implements `MsgCreateEscrow`, `MsgReleaseEscrow`, `MsgRefundEscrow` and `MsgAnchorProof`.
- **Gateway**: Python FastAPI + SQLite. Orchestrates the chain (RPC/REST), Guardian and PayPal; verifies webhooks; anchors proofs with its own key. It never sees user keys.
- **Neural Guardian**: FastAPI, scikit-learn logistic regression, exact linear SHAP explanations, isotonic calibration (LightGBM evaluated and rejected).
- **Web**: plain JavaScript, `@noble` secp256k1, hand-written Cosmos protobuf encoding with `SIGN_MODE_DIRECT`, AG Grid.
- **PayPal**: the gateway uses a real PayPal REST client (OAuth2, Orders v2, webhook signature verification). For now it points to a local PayPal API simulator; switching to the Sandbox is configuration only.
- **Delivery**: Docker Compose, Render blueprint, OpenAPI spec, Go/pytest suites and a Playwright end-to-end journey.

## Challenges we ran into

- Signing Cosmos transactions in the browser without CosmJS: getting protobuf bytes and `SignDoc` exactly right.
- Keeping the AI strictly advisory while still making its output visible and useful.
- Webhook idempotency and ordering: the order is only `ANCHORED` after the verified webhook, not after the capture call.
- Testing PayPal end to end without Sandbox credentials, which led to the simulator.

## Accomplishments that we're proud of

- A real 4-validator Cosmos chain (CometBFT BFT consensus, survives a validator going down) with a custom module, not a mock ledger.
- A risk model trained on 6.3 M PaySim transactions with a leakage-free evaluation (train/validation/test split, thresholds and calibration chosen on validation, bootstrap CIs): PR-AUC 0.973 [0.967, 0.978] versus 0.938 for simple business rules.
- Non-custodial browser wallet.
- Ten explicit invariants (no negative balance, no replay, escrow leaves `LOCKED` once, no capture without approval, verified and idempotent webhooks, AI advisory only, USD never minted as AUR…) covered by tests.

## What we learned

Cosmos SDK module wiring, PayPal Orders v2 and webhook verification, and how to present explainable AI scores without giving the model authority over money.

## What's next for AURIX

- **MVP 0.3: multi-source financial attestation (MSCFA).** Combine signed evidence from PayPal, the AURIX chain and other organisations (e.g. carriers) into a portable proof package that anyone can verify offline, without access to the merchant's PayPal account.
- **MVP 0.3: conditional USD escrow.** PayPal keeps the funds (authorize now, capture later); the AURIX protocol decides when they are captured or released back to the buyer, and every step is attested.
- Connect the real PayPal Sandbox (credentials) and remove the simulator.
- Public online demo on Render.
- Multiple validators, an indexer with PostgreSQL/Redis, Kubernetes.
- Graph-based risk analysis (GNN) trained on real, consented data.

## Honest limitations

- Testnet only: **AUR has no monetary value**. 4 validators, but run by one team on one machine.
- Guardian is trained on **PaySim**, a simulator calibrated on real mobile-money logs, not on AURIX or PayPal data; PaySim fraud is easier to detect than real fraud, and PayPal orders (PaySim `PAYMENT`) always score low.
- PayPal runs on a **local simulator** until Sandbox credentials are added (clearly labelled in the UI).

## Built with

Go, Cosmos SDK, CometBFT, Python, FastAPI, scikit-learn, SHAP, SQLite, JavaScript, noble-secp256k1, AG Grid, PayPal Orders API (simulated), Docker, Render, Playwright, OpenAPI.

## Links

- Repository README: [`../../README.md`](../../README.md)
- Demo script: [`demo-script.md`](demo-script.md)
- Architecture: [`../architecture/services.md`](../architecture/services.md)
