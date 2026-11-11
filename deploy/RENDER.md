# Deploying AURIX on Render

This guide deploys the demo (home, guided AI demo, proof page) on [Render](https://render.com) from the Blueprint
[`deploy/render.yaml`](render.yaml). Allow about 30 minutes, most of it waiting for the first builds.

## What gets deployed

| Service | Type | Plan | Role |
| --- | --- | --- | --- |
| `aurix-gateway` | Web (public) | Free | Website and API; the URL you share |
| `aurix-paypal-sim` | Web (public) | Free | PayPal simulator: the buyer's "Pay now" page |
| `aurix-delivery-sim` | Web (public) | Free | Simulated carrier, inspector and customs (called by the demo page) |
| `aurix-payments` | Private | Starter | PayPal connector, conditional escrow, AI advisor (Gemma 4) |
| `aurix-chain` | Private + 1 GB disk | Starter | AURIX chain (single validator) |
| `aurix-guardian` | Private | Starter | Neural Guardian risk score |
| `aurix-indexer` | Private | Starter | Explorer read model |
| `aurix-attestor` | Private | Starter | Independent PayPal attestor (2-of-2 anchoring with payments) |
| `aurix-indexer-db` | PostgreSQL | Basic 256 MB | Indexer database |

Private services and disks are **not available on Render's free tier**: this Blueprint needs a paid workspace (6
Starter services plus a small database). Check the current prices on render.com before deploying.

## 1. Before you start

- A Render account, with a payment method if you keep the Starter services.
- The GitHub repository `joeylukoji/Aurix` pushed with the branch to deploy (`main`).
- Your **Google AI Studio API key** for Gemma 4 (optional: without it the AI advisor answers with a labelled template).

## 2. Create the Blueprint

1. Render dashboard → **New** → **Blueprint**.
2. Connect GitHub and choose the repository **`joeylukoji/Aurix`**, branch **`main`**.
3. **Blueprint path:** `deploy/render.yaml`.
4. Render lists the 8 services and the database. It then asks for the values marked `sync: false`:

| Variable | Service | What to enter |
| --- | --- | --- |
| `ADVISOR_LLM_API_KEY` | aurix-payments | Your Google AI Studio key (or leave empty) |
| `AURIX_FAUCET_SEED` | chain, gateway | A secret phrase, **identical on both services** (testnet faucet key) |
| `AURIX_GATEWAY_SEED` | chain, payments | Another secret phrase, **identical on both services** (key that anchors PayPal proofs) |
| `GATEWAY_MNEMONIC`, `ATTESTOR_MNEMONIC` | payments, attestor | Leave empty for the demo (the seeds above are used) |

5. Click **Apply**. The first builds take 10 to 20 minutes (the chain is compiled from source).

Never paste secrets into `render.yaml`: enter them only in the Render dashboard.

## 3. Check the public URLs

The Blueprint assumes these default addresses:

- `https://aurix-gateway.onrender.com`
- `https://aurix-paypal-sim.onrender.com`
- `https://aurix-delivery-sim.onrender.com`

If Render gave a service a different address (for example with a random suffix), update the variables that point to
it, in the Render dashboard (**Environment** tab of the service), then redeploy that service:

| If this URL differs | Update |
| --- | --- |
| aurix-paypal-sim | `SIM_PUBLIC_URL` on **aurix-paypal-sim** |
| aurix-delivery-sim | `DELIVERY_PUBLIC_URL` on **aurix-gateway** |

## 4. Verify the deployment

1. `https://aurix-gateway.onrender.com/api/health` returns `"status": "ok"` and `"paypal_mode": "simulator"`.
2. Open `https://aurix-gateway.onrender.com/demo.html` and run the five steps:
   ask the AI → approve the condition → **Pay now** on the simulator page → hold the funds → send the evidence →
   the decision becomes **RELEASE**, then "proof anchored".
3. Open **Proof**, click **Verify in browser** (VALID), then **Change one digit** (INVALID).

Free web services sleep after a period of inactivity: **open the site a few minutes before a presentation**, the first
request can take about a minute to wake them up.

## 5. Use the domain aurixfin.duckdns.org (optional)

1. Render → **aurix-gateway** → **Settings** → **Custom Domains** → add `aurixfin.duckdns.org`.
2. Render shows the DNS record to create. DuckDNS can only store an IP address (A record), not a CNAME: use the
   **IP address** Render gives for an A record, and enter it in the DuckDNS dashboard as the IP of `aurixfin`.
3. Stop any DuckDNS updater running on your own machine, otherwise it will overwrite that IP.
4. Wait for Render to verify the domain; it issues the HTTPS certificate itself. The `https` Compose profile
   (Caddy) is not needed on Render.

## 6. Switch to the real PayPal Sandbox (optional)

On **aurix-payments** and **aurix-attestor** set `PAYPAL_BASE_URL=https://api-m.sandbox.paypal.com`, the Sandbox
`PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`; on aurix-payments also `PAYPAL_ENV=sandbox` and `PAYPAL_WEBHOOK_ID`.
In the PayPal developer dashboard, create a webhook on
`https://aurix-gateway.onrender.com/api/paypal/webhook` with the `PAYMENT.AUTHORIZATION.*` and `PAYMENT.CAPTURE.*`
events, and copy its id into `PAYPAL_WEBHOOK_ID`. The `aurix-paypal-sim` service can then be deleted.

## Limits of this deployment

- **One validator** on Render (the 4-validator network runs with Docker Compose): no fault tolerance online.
- The payments and gateway databases are SQLite files in `/tmp`: **orders are lost when these services restart**.
  The chain keeps its state on its disk.
- Testnet only: AUR has no value, the evidence sources are simulated, and anyone with the URL can use the faucet and
  the demo arbiter.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| The demo shows "No simulated sources configured" | `DELIVERY_PUBLIC_URL` on aurix-gateway does not match the aurix-delivery-sim URL |
| "Pay now" opens a page that does not load | `SIM_PUBLIC_URL` on aurix-paypal-sim does not match its real URL |
| The AI shows "Template draft (no AI model reachable)" | `ADVISOR_LLM_API_KEY` missing or invalid on aurix-payments |
| The decision stays on "Captured: anchoring the proof…" | aurix-attestor not running, or its `PAYPAL_BASE_URL` differs from payments' |
| First page load takes a minute | Free web services were asleep; reload once they are awake |
