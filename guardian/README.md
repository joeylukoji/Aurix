# AURIX Neural Guardian

Advisory risk-scoring API (logistic regression trained on PaySim with a leakage-free protocol, see `MODEL_CARD.md`). Scores are always advisory and
each one is signed by the Guardian's secp256k1 key so the gateway and the web wallet can check who produced it.

## Key configuration

Priority order:

| Env | Meaning |
|---|---|
| `GUARDIAN_MNEMONIC` | BIP39 mnemonic, path `m/44'/118'/0'/0/0` (same address as `aurixd keys add <name> --recover`) |
| `GUARDIAN_KEY_FILE` | file holding a 32-byte private key in hex (`chmod 600`) |
| `GUARDIAN_SEED` | dev only: private key = sha256(seed). Logs `dev seed key — never use on a public network` |

With none set, a built-in dev seed (`aurix-guardian-dev`) is used so the local stack works unchanged.
With `AURIX_ENV=production`, the seed paths (explicit or default) are refused and the service fails to start.
`GUARDIAN_CHAIN_ID` (default `aurix-testnet-1`) is signed into each attestation; a request may override it
with a `chain_id` field.

## Endpoints

- `GET /pubkey` -> `{"address": "aur1...", "pubkey": "<33-byte compressed hex>"}`
- `POST /v1/risk/score` -> existing fields (`risk_score`, `level`, `reasons`, `model_version`, `advisory_only`) plus:

```json
"attestation": {
  "guardian": "aur1...",
  "pubkey": "02...",
  "payload": "{\"amount\":\"12.5\",\"chain_id\":\"aurix-testnet-1\",\"decision\":\"low\",\"denom\":\"AUR\",\"from\":\"aur1a\",\"issued_at\":1762500000,\"model_version\":\"v1.1-lobm-paysim+1a2b3c4d5e6f\",\"score\":0.0123,\"to\":\"aur1b\"}",
  "signature": "<128 hex chars: r||s, low-S>"
}
```

Payload fields: `chain_id`, `from` (= sender), `to` (= recipient), `amount` (decimal **string**, no exponent,
no trailing zeros), `denom` (= currency), `score` (= risk_score, number), `decision` (= level),
`model_version` (release tag + `+` + first 12 hex of sha256 of the loaded model file; full hash in
`GET /v1/model`), `issued_at` (unix seconds, integer). Keys sorted, separators `,` and `:` with no whitespace,
UTF-8.

## Verifying

Python: `from app.verify import verify_attestation` (needs only `ecdsa`), returns the payload dict or raises
`InvalidAttestation`.

Any language (e.g. JS with `@noble/secp256k1`):

1. `msgHash = sha256(utf8(attestation.payload))` — verify the payload string **as received**; do not
   re-serialize it.
2. `sig = hex(attestation.signature)` is 64 bytes `r || s` (compact). Reject if `s > n/2` (high-S).
3. `secp.verify(sig, msgHash, hex(attestation.pubkey))` (noble verifies low-S by default with `lowS: true`).
4. Check the pubkey is a trusted guardian (pin `/pubkey` values in config), and optionally that
   `attestation.guardian == bech32("aur", ripemd160(sha256(pubkey)))`.
5. `JSON.parse(payload)` and check `from/to/amount/denom/chain_id` match the transaction you are about to sign
   and `issued_at` is recent.

## Tests

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python -m pytest -q
```

The trained model (`models/guardian_logreg.json`, `models/guardian_calibration.json`) is committed, so tests need no training step. Retrain and re-evaluate with `python -m training.evaluate <paysim.csv> --ship` (`requirements-train.txt`).
