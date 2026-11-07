# AURIX attestor

One instance per operator. Each attestor has its own chain key (listed in `x/smartmoney` params
`anchor_signers`) and its own read-only PayPal credentials. It polls
`GET /aurix/smartmoney/v1/proofs_pending`, and for every proof it has not attested yet:

1. calls PayPal `GET /v2/payments/captures/{capture_ref}` itself (client-credentials OAuth);
2. requires `status == COMPLETED`, a positive amount, and
   `supplementary_data.related_ids.order_id == external_ref`;
3. recomputes `sha256("{order_id}|{capture_id}")` (lowercase hex) and compares it with the proof hash;
4. signs (SIGN_MODE_DIRECT) and broadcasts its own `MsgAnchorProof{signer, hash, external_ref, capture_ref}`
   with `BROADCAST_MODE_SYNC`.

Anything else is refused and logged. Proofs whose `attestations` already contain the attestor's
address, or that it attested earlier in this process, are skipped.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ATTESTOR_SEED` | (required) | key = secp256k1(sha256(seed)), same derivation as the gateway/wallet |
| `CHAIN_REST` | `http://localhost:1317` | Cosmos REST endpoint |
| `CHAIN_ID` | `aurix-testnet-1` | chain id used in the sign doc |
| `PAYPAL_BASE_URL` | `http://localhost:8090` | PayPal API (simulator or `https://api-m.sandbox.paypal.com`) |
| `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` | `sim-client-id` / `sim-client-secret` | PayPal REST credentials |
| `POLL_INTERVAL` | `5` | seconds between polls |
| `ATTESTOR_PORT` | `8095` | port of `GET /health` (address, attested count, last error) |
| `LOG_LEVEL` | `INFO` | |

The attestor address must be funded (fees: 2000uaur per attestation) and be in `anchor_signers`.

## Run

```sh
cd attestor
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
ATTESTOR_SEED="operator 2 seed" CHAIN_ID=aurix-testnet-1 .venv/bin/python -m attestor
curl localhost:8095/health
.venv/bin/python -m pytest -q
```

Docker: `docker build -t aurix-attestor attestor/ && docker run -e ATTESTOR_SEED=... -e CHAIN_REST=... aurix-attestor`.
