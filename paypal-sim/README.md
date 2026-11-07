# PayPal Sandbox simulator (tests only)

A small local stand-in for the PayPal REST API, so AURIX can be tested end to end
before the real PayPal Sandbox is connected. **It is meant to be deleted** once
the real integration is done; the gateway code does not change.

## What it simulates

| Endpoint | Behaviour |
| --- | --- |
| `POST /v1/oauth2/token` | Client-credentials OAuth (`sim-client-id` / `sim-client-secret`) |
| `POST /v2/checkout/orders` | Creates an order (`intent: CAPTURE`, USD), honours `PayPal-Request-Id` |
| `GET /v2/checkout/orders/{id}` | Order details |
| `GET /checkoutnow?token={id}` | Buyer approval page ("Pay now") |
| `POST /v2/checkout/orders/{id}/capture` | Refused with `ORDER_NOT_APPROVED` until the buyer pays, then `COMPLETED` |
| Webhook delivery | Signed `PAYMENT.CAPTURE.COMPLETED` POSTed to `SIM_WEBHOOK_URL`, retried on failure |
| `POST /v1/notifications/verify-webhook-signature` | `SUCCESS` / `FAILURE` |

Simulator-only helpers: `POST /sim/orders/{id}/approve` (approve without a browser),
`GET /sim/events`, `POST /sim/events/{id}/resend` (replay → tests idempotency),
`POST /sim/events/{id}/forge` (tampered payload → tests signature checks).

The signature is an HMAC with a per-process key, not PayPal's RSA certificate.

## Run

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
SIM_WEBHOOK_URL=http://localhost:8080/api/paypal/webhook .venv/bin/uvicorn app.main:app --port 8090
```

Then start the gateway with `PAYPAL_ENV=simulator PAYPAL_BASE_URL=http://localhost:8090 PAYPAL_CLIENT_ID=sim-client-id PAYPAL_CLIENT_SECRET=sim-client-secret PAYPAL_WEBHOOK_ID=SIM-WEBHOOK-ID`.

## Switching to the real PayPal Sandbox (and deleting this folder)

1. On developer.paypal.com create a Sandbox REST app and a webhook pointing to
   `https://<your-gateway>/api/paypal/webhook` subscribed to `PAYMENT.CAPTURE.COMPLETED`.
2. In `.env`: `PAYPAL_ENV=sandbox`, `PAYPAL_BASE_URL=https://api-m.sandbox.paypal.com`,
   `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_WEBHOOK_ID`.
3. Delete `paypal-sim/`, the `paypal-sim` service and its `PAYPAL_*` defaults in
   `deploy/docker-compose.yml`, its CI job, and the simulator-based tests at the end of
   `gateway/tests/test_gateway.py` (replace them with Sandbox tests if wanted).
