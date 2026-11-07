# End-to-end browser journey

`demo-journey.js` drives the web UI with Playwright (Chromium): create wallet, faucet,
Guardian-checked send, escrow lock and release, PayPal simulator payment + capture
(proof anchored on chain), explorer. Exits non-zero on any failure.

```bash
scripts/dev-up.sh                       # full stack on default ports (no Docker)
BASE_URL=http://localhost:8080 SHOT_DIR=/tmp/aurix-shots node tests/e2e/demo-journey.js
scripts/dev-down.sh
```

| Env | Default | Meaning |
|-----|---------|---------|
| `BASE_URL` | `http://localhost:8080` | gateway URL |
| `SHOT_DIR` | `.` | where `e2e-paypal.png` / `e2e-explorer.png` are written |

The gateway must run with `PAYPAL_ENV=simulator` (dev-up does this). `scripts/test-all.sh`
runs every unit suite and this journey on a stack at `PORT_OFFSET` (default 10000).
