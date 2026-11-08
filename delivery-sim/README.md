# delivery-sim — AURIX demo carrier (SIMULATED)

Stands for a carrier independent of PayPal and of AURIX, for the evidence-released USD escrow (MVP 0.2,
[`docs/architecture/evidence-escrow.md`](../docs/architecture/evidence-escrow.md)). It signs its own
delivery events with its own Ed25519 key and sends them to `payments` (`POST /api/escrow/evidence`).
Every event says `source_type: "simulated"`: never present it as a real carrier.

| Route | Effect |
| --- | --- |
| `POST /deliver/{order_id}` | `shipment.delivered`: the evidence that releases an escrow |
| `POST /deliver/{order_id}?when=<ISO>` | same, with a chosen event time (deadline scenarios) |
| `POST /deliver/{order_id}?forge=true` | signed with an unregistered key: AURIX must refuse it |
| `POST /ship/{order_id}` | `shipment.shipped` (informational) |
| `GET /key` | this carrier's public key entry, for AURIX's `ESCROW_TRUSTED_KEYS_FILE` |

Config: `PAYMENTS_URL` (default `http://localhost:8085`), `DELIVERY_SIM_KEY_FILE` (hex 32-byte Ed25519 seed;
required with `AURIX_ENV=production`, otherwise a dev seed matching payments' built-in demo carrier),
`CORS_ORIGINS` (default `*`). Port 8087.

Tests: `cd delivery-sim && python -m pytest -q`.
