"""One builder for `aurix.financial-attestation` v1.0 documents (docs/architecture/mscfa.md §6).

Used by the payments service (AURIX's own attestations, the demo arbiter), the simulated carrier and the
tests, so the signed format is written in one place. It builds the document only; signing is
`crypto.Signer.sign`. Changing the output changes a signed format: bump `schema_version` (CODING_STANDARDS §1.16).
"""
from __future__ import annotations

SCHEMA, SCHEMA_VERSION = "aurix.financial-attestation", "1.0"


def make_attestation(*, attestation_id: str, operation_id: str, provider: str, provider_event_id: str,
                     event_type: str, source_type: str, environment: str, verification_method: str,
                     source_event_time: str, attested_at: str, refs: dict | None = None,
                     provider_status: str | None = None, amount: dict | None = None, action: str | None = None,
                     verified_by: str | None = None, aurix_received_at: str | None = None,
                     provenance: dict | None = None) -> dict:
    """`refs` complete `provider_reference` (which always carries `order_id = operation_id`). Optional parts
    are only present when given, so documents built before this helper keep exactly the same shape."""
    source = {"provider": provider, "source_type": source_type, "environment": environment,
              "provider_event_id": provider_event_id,
              "provider_reference": {"order_id": operation_id, **(refs or {})},
              "verification_method": verification_method}
    if verified_by:
        source["verified_by"] = verified_by
    source["verification_status"] = "verified"
    event: dict = {"type": event_type}
    if provider_status:
        event["provider_status"] = provider_status
    if amount:
        event["amount"] = {"value": str(amount.get("value")),
                           "currency": str(amount.get("currency_code") or amount.get("currency"))}
    if action:
        event["action"] = action
    timestamps = {"source_event_time": source_event_time}
    if aurix_received_at:
        timestamps["aurix_received_at"] = aurix_received_at
    timestamps["attested_at"] = attested_at
    att = {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "attestation_id": attestation_id,
           "operation_id": operation_id, "source": source, "event": event, "timestamps": timestamps}
    if provenance is not None:
        att["provenance"] = provenance
    return att
