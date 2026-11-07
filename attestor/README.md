# AURIX attestor

One instance per operator. Each attestor has its own chain key (listed in `x/smartmoney` params
`anchor_signers`) and its own read-only PayPal credentials. It polls
`GET /aurix/smartmoney/v1/proofs_pending`, and for every proof it has not attested yet:

1. calls PayPal `GET /v2/payments/captures/{capture_ref}` itself (client-credentials OAuth);
2. requires `status == COMPLETED`, `supplementary_data.related_ids.order_id == external_ref`, and
   capture `amount.value` / `amount.currency_code` equal to the proof's `amount` / `currency` (decimal
   strings compared after normalisation to 2 places, so `"12.5" == "12.50"`; currency compared exactly).
   Proofs with an empty amount or currency are refused;
3. recomputes `sha256("{order_id}|{capture_id}")` (lowercase hex) and compares it with the proof hash;
4. signs (SIGN_MODE_DIRECT) and broadcasts its own
   `MsgAnchorProof{signer=1, hash=2, external_ref=3, capture_ref=4, amount=5, currency=6}` (amount/currency
   copied verbatim from the stored proof) with `BROADCAST_MODE_SYNC`.

Anything else is refused and logged. Proofs whose `attestations` already contain the attestor's
address, or that it attested earlier in this process, are skipped.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ATTESTOR_MNEMONIC` | | BIP39 mnemonic (12/24 words), derived at `m/44'/118'/0'/0/0` — same address as `aurixd keys add <name> --recover`. Highest priority |
| `ATTESTOR_KEY_FILE` | | path to a file holding the 32-byte private key in hex (chmod 600; a warning is logged otherwise). Used if no mnemonic |
| `ATTESTOR_SEED` | | **dev only**: key = secp256k1(sha256(seed)); logs `dev seed key — never use on a public network`, refused when `AURIX_ENV=production` |
| `AURIX_ENV` | | set to `production` to forbid the dev seed key |
| `CHAIN_REST` | `http://localhost:1317` | Cosmos REST endpoint |
| `CHAIN_ID` | `aurix-testnet-1` | chain id used in the sign doc |
| `PAYPAL_BASE_URL` | `http://localhost:8090` | PayPal API (simulator or `https://api-m.sandbox.paypal.com`) |
| `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` | `sim-client-id` / `sim-client-secret` | PayPal REST credentials |
| `POLL_INTERVAL` | `5` | seconds between polls |
| `ATTESTOR_PORT` | `8095` | port of `GET /health` (address, attested count, last error) |
| `LOG_LEVEL` | `INFO` | |

One of the three key variables is required. The attestor address must be funded (fees: 2000uaur per attestation) and be in `anchor_signers`.

## Run

```sh
cd attestor
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
ATTESTOR_SEED="operator 2 seed" CHAIN_ID=aurix-testnet-1 .venv/bin/python -m attestor
curl localhost:8095/health
.venv/bin/python -m pytest -q
```

Docker: `docker build -t aurix-attestor attestor/ && docker run -e ATTESTOR_MNEMONIC="..." -e CHAIN_REST=... aurix-attestor`
(or mount a key file: `-v /secure/attestor.key:/run/secrets/attestor.key:ro -e ATTESTOR_KEY_FILE=/run/secrets/attestor.key`).
