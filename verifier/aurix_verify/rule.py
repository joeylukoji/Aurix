"""Decision rules of the evidence-released USD escrow (MVP 0.2).

- "delivery-attested/1": the payment is authorised and the agreed carrier attests delivery before the deadline.
- "conditions/1": a composable condition (all / any / at_least over signed evidence from registered sources),
  with an optional buyer dispute window. The AI advisor may draft the condition, but once the buyer approves it
  it is fixed in the signed terms: the rule itself stays deterministic.

Pure functions: the same terms, evidence and time always give the same decision, so `aurix-verify` can replay
the decision recorded in a proof package. See docs/architecture/evidence-escrow.md §5.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .crypto import VALID, format_time, parse_time

RULE_ID = "delivery-attested/1"
RULE_CONDITIONS = "conditions/1"
RULES = {RULE_ID, RULE_CONDITIONS}
WAIT, RELEASE, VOID, HOLD = "WAIT", "RELEASE", "VOID", "HOLD"
ARBITER_ACTIONS = {"HOLD", "UNHOLD", "RELEASE", "VOID"}
# Evidence types a condition may require, and bounds that keep a condition small enough to read and replay.
EVIDENCE_TYPES = {"shipment.shipped", "shipment.delivered", "inspection.passed", "customs.cleared"}
LEAF_KEYS = {"evidence", "source", "before", "within_days"}
MAX_LEAVES, MAX_DEPTH, MAX_DISPUTE_HOURS = 10, 4, 14 * 24


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


def _canonical_order(e: Evidence):
    """Reception time, then attestation id; evidence without a reception time (never trusted) goes last."""
    rec = parse_time(e.received_at) if e.received_at else None
    return (rec is None, rec or datetime.min.replace(tzinfo=timezone.utc), str(e.attestation.get("attestation_id", "")))


def decide(terms: dict, evidence: list[Evidence], now: str) -> Outcome:
    # The outcome and the evidence it cites must not depend on the order in which a caller happens to hold
    # the evidence: the service reads it by reception time, the verifier by file name. Where several pieces
    # of evidence would do, the rules below take the first in this order.
    evidence = sorted(evidence, key=_canonical_order)
    if terms.get("rule") == RULE_CONDITIONS:
        return _decide_conditions(terms, evidence, now)
    return _decide_delivery(terms, evidence, now)


def _checks(terms: dict, reasons: list[str]):
    """The evidence filters both rules share: trusted(e, role) and received_in_time(e)."""
    order_id = terms["order_id"]
    deadline = parse_time(terms["deadline"])

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

    return trusted, received_in_time


def _arbiter(terms: dict, evidence: list[Evidence], at, trusted, received_in_time,
             reasons: list[str]) -> tuple[Outcome | None, list[Evidence]]:
    """The latest arbiter action at or before `now`, taken and received by the deadline, wins. A HOLD blocks the
    automatic rule until the deadline; a hold still in place then ends in VOID (the buyer is refunded rather than
    the funds staying held past the PayPal authorization). Returns (outcome or None, the valid actions)."""
    deadline = parse_time(terms["deadline"])
    arbiter = terms.get("arbiter_key_id")
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
            return Outcome(action, reasons + [f"arbiter decided {action}"], [last.attestation]), arb
        if action == HOLD:
            if at > deadline:
                return Outcome(VOID, reasons + ["deadline passed with the arbiter's hold still in place"],
                               [last.attestation]), arb
            return Outcome(HOLD, reasons + ["arbiter hold in place"], [last.attestation]), arb
    return None, arb


def _authorized(terms: dict, evidence: list[Evidence], trusted, reasons: list[str]) -> list[Evidence]:
    """The payment must be authorised for exactly the agreed amount."""
    expected = _money(terms.get("amount"))
    auth = [e for e in evidence if e.type == "payment.authorized" and trusted(e, "aurix")]
    auth_ok = [e for e in auth if _money(e.attestation["event"].get("amount")) == expected]
    if auth and not auth_ok:
        reasons.append("payment authorised for a different amount or currency")
    return auth_ok


def _decide_delivery(terms: dict, evidence: list[Evidence], now: str) -> Outcome:
    deadline = parse_time(terms["deadline"])
    at = parse_time(now)
    carrier = terms.get("carrier")
    reasons: list[str] = []
    trusted, received_in_time = _checks(terms, reasons)
    # 1. Arbiter.
    out, _ = _arbiter(terms, evidence, at, trusted, received_in_time, reasons)
    if out:
        return out
    # 2. The payment must be authorised for exactly the agreed amount.
    auth_ok = _authorized(terms, evidence, trusted, reasons)
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


def _decide_conditions(terms: dict, evidence: list[Evidence], now: str) -> Outcome:
    deadline = parse_time(terms["deadline"])
    at = parse_time(now)
    reasons: list[str] = []
    trusted, received_in_time = _checks(terms, reasons)
    out, arb = _arbiter(terms, evidence, at, trusted, received_in_time, reasons)
    if out:
        return out
    auth_ok = _authorized(terms, evidence, trusted, reasons)

    def rec(e: Evidence):
        return parse_time(e.received_at)  # only called on evidence received in time

    def leaf(node: dict) -> Evidence | None:
        """The earliest matching evidence (then by id, so storage order does not matter), or None."""
        src = node["source"]
        limit = min(parse_time(node.get("before")) or deadline, deadline)
        found = []
        for e in evidence:
            if e.type != node["evidence"] or not trusted(e, f"source:{src}"):
                continue
            if e.attestation.get("source", {}).get("provider") != src:
                reasons.append(f"{e.type} ignored: not from {src}")
                continue
            t = parse_time(e.attestation["timestamps"].get("source_event_time"))
            if t is None or t > limit:
                reasons.append(f"{e.type} from {src} ignored: recorded after its time limit")
                continue
            if t > at or not received_in_time(e):
                continue
            found.append((t, e.attestation.get("attestation_id", ""), e))
        return min(found, key=lambda x: (x[0], x[1]))[2] if found else None

    missing: list[str] = []

    def met(node: dict) -> list[Evidence] | None:
        """The evidence that satisfies `node`, or None. Children are tried in the order of the terms."""
        if "evidence" in node:
            e = leaf(node)
            if e is None:
                missing.append(f"{node['evidence']} from {node['source']}")
            return [e] if e else None
        children = node["all"] if "all" in node else node["any"] if "any" in node else node["of"]
        ok = [p for p in (met(c) for c in children) if p is not None]
        need = len(children) if "all" in node else 1 if "any" in node else int(node["at_least"])
        return [e for p in ok[:need] for e in p] if len(ok) >= need else None

    used = met(terms["condition"])
    # A buyer dispute counts unless the arbiter lifted it (UNHOLD) after it arrived.
    lifted = max((parse_time(e.attestation["timestamps"]["attested_at"]) for e in arb
                  if e.attestation["event"]["action"] == "UNHOLD"), default=None)
    disputes = [e for e in evidence if e.type == "buyer.disputed" and trusted(e, "aurix") and received_in_time(e)
                and (lifted is None or rec(e) > lifted)]
    if auth_ok and used:
        met_at = max(rec(e) for e in [auth_ok[0], *used])  # when AURIX had all the required evidence
        release_at = min(met_at + timedelta(hours=int(terms.get("dispute_window_hours") or 0)), deadline)
        open_disputes = [e for e in disputes if rec(e) <= release_at]
        if open_disputes:
            if at > deadline:
                return Outcome(VOID, reasons + ["deadline passed with the buyer's dispute unresolved"],
                               [open_disputes[0].attestation])
            return Outcome(HOLD, reasons + ["buyer disputed: waiting for the arbiter (refund at the deadline)"],
                           [open_disputes[0].attestation])
        if at >= release_at:
            return Outcome(RELEASE, reasons + ["payment authorised and the condition was met"
                                               + (", no dispute in the window" if release_at > met_at else "")],
                           [auth_ok[0].attestation] + [e.attestation for e in used])
        return Outcome(WAIT, reasons + [f"condition met; buyer dispute window open until {format_time(release_at)}"])
    if at > deadline:
        return Outcome(VOID, reasons + ["deadline passed without the required evidence"],
                       [e.attestation for e in auth_ok[:1]])
    if not auth_ok:
        reasons.append("waiting for the payment authorisation")
    reasons += [f"waiting for {m}" for m in missing]
    return Outcome(WAIT, reasons)


def validate_condition(node, sources: dict[str, set[str]] | None = None, depth: int = 1) -> int:
    """Checks a condition tree and returns its number of leaves; raises ValueError with a readable message.
    `sources` maps each registered source to the evidence types it may attest (None: not checked)."""
    if depth > MAX_DEPTH:
        raise ValueError(f"condition nested deeper than {MAX_DEPTH} levels")
    if not isinstance(node, dict):
        raise ValueError("each condition node must be an object")
    if "evidence" in node:
        if set(node) - LEAF_KEYS:
            raise ValueError(f"unknown keys in a condition leaf: {sorted(set(node) - LEAF_KEYS)}")
        if node["evidence"] not in EVIDENCE_TYPES:
            raise ValueError(f"evidence must be one of {sorted(EVIDENCE_TYPES)}")
        if not isinstance(node.get("source"), str):
            raise ValueError("each condition leaf needs a source")
        if sources is not None and node["evidence"] not in sources.get(node["source"], set()):
            raise ValueError(f"source {node['source']!r} cannot attest {node['evidence']}")
        if "before" in node and (not isinstance(node["before"], str) or parse_time(node["before"]) is None):
            raise ValueError("before must be an ISO 8601 time")
        w = node.get("within_days")
        if w is not None and (not isinstance(w, int) or isinstance(w, bool) or w < 1):
            raise ValueError("within_days must be a positive integer")
        return 1
    kinds = [k for k in ("all", "any", "at_least") if k in node]
    if len(kinds) != 1 or set(node) - {"all", "any", "at_least", "of"}:
        raise ValueError("a condition node is a leaf, or exactly one of all / any / at_least (+ of)")
    children = node.get("of") if kinds[0] == "at_least" else node[kinds[0]]
    if not isinstance(children, list) or not children:
        raise ValueError(f"{kinds[0]} needs a non-empty list")
    if kinds[0] == "at_least":
        k = node["at_least"]
        if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= len(children):
            raise ValueError("at_least must be an integer between 1 and the number of options")
    n = sum(validate_condition(c, sources, depth + 1) for c in children)
    if n > MAX_LEAVES:
        raise ValueError(f"a condition may require at most {MAX_LEAVES} pieces of evidence")
    return n
