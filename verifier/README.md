# aurix-verify

Independent, offline verifier for AURIX proof packages (MVP 0.2, evidence-released USD escrow).
Spec: [`docs/architecture/evidence-escrow.md`](../docs/architecture/evidence-escrow.md).

```bash
pip install -e verifier
python -m aurix_verify aurix-proof-ORDER.zip --keys public-keys.json
```

It checks, without network or database access:

- **integrity**: every file listed in the signed manifest is present and unchanged;
- **signatures**: Ed25519 over SHA-256 of the RFC 8785 canonical JSON, against the key registry
  (`--keys`, the issuer's published keys; otherwise the keys inside the package, flagged as self-declared);
  verdicts `valid`, `invalid`, `unknown_key`, `revoked_key`, `valid_but_key_revoked_after_signing`;
- **sources**: which are real and which are simulated, and how each was verified;
- **consistency**: amount/currency mismatches, foreign orders, duplicates, missing evidence;
- **decision**: replays the public rule `delivery-attested/1` on the evidence AURIX had at decision time
  and checks it gives the recorded decision.

Exit code `0` = VALID, `1` = INVALID, `2` = unreadable input. The report always lists its limitations:
a valid signature proves who signed what, not that the goods were physically delivered.

The payments service imports this same package for canonicalisation, signatures and the rule, so the
verifier replays exactly the code that made the decision. The verifier itself imports no server code.

Tests: `cd verifier && python -m pytest -q`.
