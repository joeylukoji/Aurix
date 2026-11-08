"""Decision rule "delivery-attested/1" of the evidence-released USD escrow (MVP 0.2).

A pure, deterministic function: the same terms, evidence and time always give the same decision, so
`aurix-verify` can replay the decision recorded in a proof package. See docs/architecture/evidence-escrow.md §5.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .crypto import VALID, parse_time

RULE_ID = "delivery-attested/1"
WAIT, RELEASE, VOID, HOLD = "WAIT", "RELEASE", "VOID", "HOLD"
ARBITER_ACTIONS = {"HOLD", "UNHOLD", "RELEASE", "VOID"}


@dataclass
class Evidence:
    """A signed attestation as seen by the rule: its content and who signed it (already verified)."""
    attestation: dict
    key_id: str
    role: str | None  # role of the signing key in the registry: "aurix", "source:<provider>", "arbiter"
    verdict: str      # signature verdict from KeyRegistry.verify
    received_at: str | None = None  # when AURIX received it (signed receipt); None = unknown, never trusted

    @property
    def type(self) -> str:
        return self.attestation.get("event", {}).get("type", "")


@dataclass
class Outcome:
    decision: str
    reasons: list[str] = field(default_factory=list)
    inputs: list[dict] = field(default_factory=list)  # the attestations the decision rests on


def _money(m: dict | None) -> tuple[Decimal, str] | None:
    try:
        return Decimal(str(m["value"])), str(m["currency"]).upper()
    except (TypeError, KeyError, InvalidOperation):
        return None


def _refs(a: dict) -> dict:
    return a.get("source", {}).get("provider_reference") or {}


def decide(terms: dict, evidence: list[Evidence], now: str) -> Outcome:
    order_id = terms["order_id"]
    deadline = parse_time(terms["deadline"])
    at = parse_time(now)
    expected = _money(terms.get("amount"))
    carrier = terms.get("carrier")
    arbiter = terms.get("arbiter_key_id")
    reasons: list[str] = []

    def trusted(e: Evidence, role: str) -> bool:
        if e.verdict != VALID:
            reasons.append(f"{e.type or 'attestation'} ignored: signature {e.verdict}")
            return False
        if e.role != role:
            reasons.append(f"{e.type} ignored: signed by a {e.role or 'unregistered'} key, {role} required")
            return False
        ref = _refs(e.attestation).get("order_id")
        if e.attestation.get("operation_id") != order_id or (ref is not None and ref != order_id):
            reasons.append(f"{e.type} ignored: belongs to another order")
            return False
        return True

    def received_in_time(e: Evidence) -> bool:
        """Evidence counts only if AURIX received it by the deadline: a signer's own timestamp can be backdated."""
        rec = parse_time(e.received_at) if e.received_at else None
        if rec is None or rec > deadline:
            reasons.append(f"{e.type} ignored: received by AURIX after the deadline")
            return False
        return True

    # 1. Arbiter: the latest action at or before `now`, taken and received by the deadline, wins. A HOLD
    #    blocks the automatic rule until the deadline; a hold still in place then ends in VOID (the buyer is
    #    refunded rather than the funds staying held past the PayPal authorization).
    arb = [e for e in evidence if e.type == "arbiter.decision" and arbiter and e.key_id == arbiter
           and trusted(e, "arbiter")
           and e.attestation.get("event", {}).get("action") in ARBITER_ACTIONS
           and parse_time(e.attestation["timestamps"]["attested_at"]) <= min(at, deadline)
           and received_in_time(e)]
    arb.sort(key=lambda e: parse_time(e.attestation["timestamps"]["attested_at"]))
    if arb:
        last = arb[-1]
        action = last.attestation["event"]["action"]
        if action in (RELEASE, VOID):
            return Outcome(action, reasons + [f"arbiter decided {action}"], [last.attestation])
        if action == HOLD:
            if at > deadline:
                return Outcome(VOID, reasons + ["deadline passed with the arbiter's hold still in place"],
                               [last.attestation])
            return Outcome(HOLD, reasons + ["arbiter hold in place"], [last.attestation])

    # 2. The payment must be authorised for exactly the agreed amount.
    auth = [e for e in evidence if e.type == "payment.authorized" and trusted(e, "aurix")]
    auth_ok = [e for e in auth if _money(e.attestation["event"].get("amount")) == expected]
    if auth and not auth_ok:
        reasons.append("payment authorised for a different amount or currency")
    # 3. Delivery attested by the agreed carrier, signed with its registered key, before the deadline.
    deliveries = []
    for e in evidence:
        if e.type != "shipment.delivered" or not trusted(e, f"source:{carrier}"):
            continue
        if e.attestation.get("source", {}).get("provider") != carrier:
            reasons.append("delivery ignored: not from the agreed carrier")
            continue
        t = parse_time(e.attestation["timestamps"].get("source_event_time"))
        if t is None or t > deadline:
            reasons.append("delivery ignored: recorded after the deadline")
            continue
        if t > at:
            continue  # not yet happened at the evaluation time
        if not received_in_time(e):
            continue
        deliveries.append(e)

    if auth_ok and deliveries:
        return Outcome(RELEASE, reasons + ["payment authorised and delivery attested before the deadline"],
                       [auth_ok[0].attestation, deliveries[0].attestation])
    if at > deadline:
        return Outcome(VOID, reasons + ["deadline passed without the required evidence"],
                       [e.attestation for e in auth_ok[:1]])
    if not auth_ok:
        reasons.append("waiting for the payment authorisation")
    if not deliveries:
        reasons.append("waiting for the delivery attestation")
    return Outcome(WAIT, reasons)
