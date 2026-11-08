# AURIX coding standards

The rules reviewers check code against (`code-review` skill, Standards axis). Most come from decisions already
made in this repository; each cites where. A documented rule here overrides any generic pattern or "smell".

## 1. Protocol invariants (never break)

From [`AURIX_SOFTWARE_ARCHITECTURE.md`](AURIX_SOFTWARE_ARCHITECTURE.md) §8, each covered by tests:

1. AUR supply changes only through planned issuance. 2. Locked funds = sum of `LOCKED` escrows.
3. An escrow is released or refunded at most once. 4. Nonces strictly increase; replays are rejected.
5. No negative balance. 6. A replayed webhook has no additional effect. 7. A webhook with an invalid signature
is rejected. 8. No capture without explicit human approval (`confirm=true`). 9. The AI modifies no transaction.
10. AUR transfers and escrows work when PayPal is down. 11. An attestation with a different amount/currency is
rejected; a pending proof expires. 12. A forged or stale Guardian signature is excluded from the quorum.

Evidence escrow (MVP 0.2, [`docs/architecture/evidence-escrow.md`](docs/architecture/evidence-escrow.md)):

13. AURIX never holds or moves USD: PayPal authorizes, captures or voids.
14. A recorded escrow decision is final: RELEASE is never followed by VOID, nor the reverse.
15. The decision rule (`verifier/aurix_verify/rule.py`) is a pure, deterministic function. No clock reads, I/O,
    randomness or LLM call inside it. `web/verify.js` mirrors it; `verifier/tests/test_js_parity.py` must pass.
16. Signed formats (JCS canonicalisation, signature blocks, attestation, decision, receipts and manifest
    schemas) only change with a new `schema_version` and a matching verifier change.
17. Evidence is linked by explicit identifiers only, never by similar amounts or times.

## 2. Money, time, identifiers

- Amounts are decimal strings (`Decimal` in Python). No binary floats for money anywhere in signed data or
  decisions (the JCS encoder rejects floats on purpose). The only float allowed is the advisory Guardian input.
- Times are UTC ISO 8601 with `Z`; compare parsed datetimes, not strings.
- Provider statuses are stored as received; AURIX's own interpretation goes in a separate field.

## 3. AI

- The AI advises, the protocol decides, the human approves. Model output is displayed (escaped) or stored as
  advisory; it never feeds a decision, a state transition or a signature.
- Only minimised, structured facts go to a model: no payer identity, no raw payloads, no free text from
  external parties. A template fallback must keep features working without a model.

## 4. Security

- No secrets or private keys in Git, logs, exports or error messages. Keys load from `*_KEY_FILE` / `*_MNEMONIC`;
  dev seeds log a warning and are refused when `AURIX_ENV=production`.
- Demo-only features (demo arbiter, minute deadlines, faucet) are disabled in production, and tested so.
- External input (webhooks, evidence, uploaded packages, zip paths) is validated before use; signatures are
  verified before any effect; idempotency comes from database constraints.
- Simulated components are labelled `simulated` in data, UI and reports. Never present them as real.
- Wording: never "guaranteed" or "irrevocable"; a valid signature proves who signed, not physical delivery.

## 5. Structure

- Services own their logic; the gateway is a thin edge (reverse proxy), never a second implementation.
- Shared code lives in `common/` (`aurix_common`) or `verifier/` (`aurix_verify`). The verifier imports no
  server code.
- Go chain code follows Cosmos SDK conventions (keeper, `msg_server`, `query_server`, genesis). Generated files
  (`*.pb.go`, `*.pb.gw.go`) are never edited by hand: change the `.proto` and regenerate.
- Prefer small pure functions for rules and formats, with I/O at the edges (routes, clients, pollers).
- No new dependency without a reason stated in the PR; pin versions in `requirements*.txt`.

## 6. Tests and docs

- Every behaviour change ships with a test in the owning service; bug fixes start with a failing test.
- Before pushing: the touched service's `pytest -q` / `go test ./...`; for cross-service changes
  `scripts/test-all.sh` (includes the Playwright journey `tests/e2e/demo-journey.js`).
- Update the spec or README status table when behaviour or scope changes. Product versions are named
  MVP 0.1 / 0.2 / 0.3, not V1 / V2.
