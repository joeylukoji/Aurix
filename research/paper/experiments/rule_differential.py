"""Random differential and property testing of the decision function.

Generates random orders (terms of both rule versions, condition trees within the language's bounds, evidence
with valid and invalid signatures, wrong roles, foreign references, times around every boundary) and, on the
inputs the rule is specified for (evidence received by the evaluation time, each with its reception time),

  1. evaluates each with the Python rule (verifier/aurix_verify/rule.py) and the JavaScript port
     (web/verify.js), and compares decision, cited evidence and reasons;
  2. checks properties of the Python rule on the same cases:
       termination        after the deadline the outcome is RELEASE or VOID
       needs-authorisation  a release not ordered by the arbiter disappears when the authorisation is removed
       needs-evidence     ... and when every source attestation is removed
       invalid-is-inert   removing evidence whose signature verdict is not `valid` changes nothing
       order-independent  shuffling the evidence changes neither the outcome nor the evidence cited
       extra-evidence     adding one more valid attestation for a leaf does not undo a release (a probe:
                          the rule does not promise this, see the paper)

    PYTHONPATH=verifier python research/paper/experiments/rule_differential.py OUT.json [N] [SEED]
"""
import json
import random
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aurix_verify.rule import (HOLD, RELEASE, RULE_CONDITIONS, RULE_ID, VOID, WAIT, Evidence, decide,
                               validate_condition)

HERE = Path(__file__).resolve().parent
T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
SOURCES = {"aurix_demo_delivery": ["shipment.shipped", "shipment.delivered"],
           "aurix_demo_inspection": ["inspection.passed"],
           "aurix_demo_customs": ["customs.cleared"],
           "second_lab": ["inspection.passed"]}
VERDICTS = ["invalid", "unknown_key", "revoked_key", "valid_but_key_revoked_after_signing"]
ARBITER = "arbiter-1"


def ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def gen_tree(rng: random.Random, depth: int, budget: list[int], deadline: datetime) -> dict:
    if depth == 4 or budget[0] <= 1 or (depth > 1 and rng.random() < 0.45):
        budget[0] -= 1
        src = rng.choice(list(SOURCES))
        leaf = {"evidence": rng.choice(SOURCES[src]), "source": src}
        if rng.random() < 0.3:
            leaf["before"] = ts(deadline + timedelta(seconds=rng.choice([-432000, -86400, -3600, -1, 0, 1, 86400])))
        return leaf
    n = min(rng.randint(2, 4), budget[0])
    children = [gen_tree(rng, depth + 1, budget, deadline) for _ in range(n) if budget[0] > 0]
    children = children or [gen_tree(rng, 4, [1], deadline)]
    kind = rng.choice(["all", "any", "at_least"])
    if kind == "at_least":
        return {"at_least": rng.randint(1, len(children)), "of": children}
    return {kind: children}


def leaves(node: dict) -> list[dict]:
    if "evidence" in node:
        return [node]
    return [x for c in (node.get("all") or node.get("any") or node.get("of")) for x in leaves(c)]


def gen_case(rng: random.Random, i: int) -> dict:
    order = f"ORD-{i}"
    deadline = T0 + timedelta(seconds=rng.randint(2 * 86400, 20 * 86400))
    amount = rng.choice(["120.00", "80.50", "1500.00"])
    terms = {"schema": "aurix.escrow-terms", "schema_version": "1.0", "order_id": order,
             "amount": {"value": amount, "currency": "USD"}, "deadline": ts(deadline),
             "arbiter_key_id": rng.choice([None, ARBITER, ARBITER]), "created_at": ts(T0)}
    if rng.random() < 0.3:
        terms |= {"rule": RULE_ID, "condition": "delivery-attested", "carrier": "aurix_demo_delivery"}
        wanted = [{"evidence": "shipment.delivered", "source": "aurix_demo_delivery"}]
    else:
        tree = gen_tree(rng, 1, [rng.randint(1, 10)], deadline)
        validate_condition(tree, {s: set(v) for s, v in SOURCES.items()})
        terms |= {"rule": RULE_CONDITIONS, "condition": tree,
                  "dispute_window_hours": rng.choice([0, 0, 0, 24, 72, 200])}
        wanted = leaves(tree)

    def moment(lo=0, hi=2 * 86400) -> datetime:
        if rng.random() < 0.15:
            return deadline + timedelta(seconds=rng.choice([-1, 0, 1]))
        span = int((deadline - T0).total_seconds())
        return T0 + timedelta(seconds=rng.randint(lo, span + hi))

    evidence, n = [], 0

    def add(kind: str, provider: str, role: str, key_id: str, action=None, amt=None):
        nonlocal n
        n += 1
        event_time = moment()
        received = event_time + timedelta(seconds=rng.choice([0, 1, 60, 3600, 86400, 3 * 86400]))
        if rng.random() < 0.1:
            received = deadline + timedelta(seconds=rng.choice([0, 1]))
        refs = {} if rng.random() < 0.03 else {"order_id": order if rng.random() < 0.97 else "ORD-other"}
        event = {"type": kind}
        if amt is not None:
            event["amount"] = {"value": amt, "currency": rng.choice(["USD", "USD", "USD", "usd", "EUR"])}
        if action is not None:
            event["action"] = action
        stamps = {"attested_at": ts(event_time + timedelta(seconds=rng.choice([0, 0, 30])))}
        if rng.random() < 0.98:
            stamps["source_event_time"] = ts(event_time)
        att = {"schema": "aurix.financial-attestation", "schema_version": "1.0",
               "attestation_id": f"{rng.choice(['att', 'dlv', 'x'])}_{rng.randint(0, 99):02d}_{n}",
               "operation_id": order if rng.random() < 0.96 else "ORD-other",
               "source": {"provider": provider, "provider_event_id": f"EV-{n}", "provider_reference": refs},
               "event": event, "timestamps": stamps}
        evidence.append({"attestation": att, "key_id": key_id, "role": role if rng.random() < 0.94 else rng.choice(
                            ["aurix", "arbiter", "source:aurix_demo_delivery", None]),
                         "verdict": "valid" if rng.random() < 0.92 else rng.choice(VERDICTS),
                         "received_at": ts(received)})

    if rng.random() < 0.88:
        add("payment.authorized", "paypal", "aurix", "aurix-1",
            amt=amount if rng.random() < 0.85 else rng.choice([amount.rstrip("0"), "1.00", amount + "0"]))
    for leaf in wanted:
        for _ in range(rng.choice([0, 1, 1, 1, 2])):
            provider = leaf["source"] if rng.random() < 0.96 else rng.choice(list(SOURCES))
            add(leaf["evidence"], provider, f"source:{leaf['source']}", f"{leaf['source']}-key")
    for _ in range(rng.choice([0, 0, 1, 2])):
        src = rng.choice(list(SOURCES))
        add(rng.choice(SOURCES[src]), src, f"source:{src}", f"{src}-key")
    if rng.random() < 0.2:
        add("buyer.disputed", "aurix_buyer", "aurix", "aurix-1")
    for _ in range(rng.choice([0, 0, 0, 1, 2])):
        add("arbiter.decision", "arbiter", "arbiter", ARBITER if rng.random() < 0.9 else "arbiter-2",
            action=rng.choice(["HOLD", "UNHOLD", "RELEASE", "VOID", "HOLD", "PAUSE"]))
    rng.shuffle(evidence)
    now = moment(hi=3 * 86400)
    if evidence and rng.random() < 0.25:
        pick = rng.choice(evidence)["received_at"]
        now = datetime.strptime(pick, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) + timedelta(
            seconds=rng.choice([0, 1, 86400, 72 * 3600]))
    # The rule's contract, kept by the service and by the verifier: it is given the evidence already received
    # at `now`, each piece with its reception time.
    held = [e for e in evidence if e["received_at"] <= ts(now)]
    return {"terms": terms, "evidence": held, "now": ts(now)}


def run(case: dict, evidence: list[dict] | None = None):
    ev = [Evidence(e["attestation"], e["key_id"], e["role"], e["verdict"], e["received_at"])
          for e in (case["evidence"] if evidence is None else evidence)]
    out = decide(case["terms"], ev, case["now"])
    return out.decision, [a["attestation_id"] for a in out.inputs], out.reasons


def by_arbiter(case: dict, cited: list[str]) -> bool:
    types = {e["attestation"]["attestation_id"]: e["attestation"]["event"]["type"] for e in case["evidence"]}
    return any(types.get(i) == "arbiter.decision" for i in cited)


def main() -> None:
    out_path = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 20000
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 20261112
    rng = random.Random(seed)
    cases = [gen_case(rng, i) for i in range(n)]
    py = [run(c) for c in cases]

    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "cases.json"
        f.write_text(json.dumps(cases))
        res = subprocess.run(["node", str(HERE / "rule_differential_js.js"), str(f)], capture_output=True, text=True,
                             check=True)
    js = json.loads(res.stdout)
    diff = {"decision": 0, "cited_evidence": 0, "reasons": 0}
    examples = {}
    for i, (a, b) in enumerate(zip(py, js)):
        for k, j in (("decision", 0), ("cited_evidence", 1), ("reasons", 2)):
            if a[j] != b[j]:
                diff[k] += 1
                examples.setdefault(k, {"case": i, "python": a[j], "javascript": b[j]})

    props = {k: {"checked": 0, "violations": 0} for k in
             ("termination", "needs_authorisation", "needs_evidence", "invalid_is_inert", "order_independent",
              "extra_evidence")}
    prng = random.Random(seed + 1)
    extra_examples = []
    for i, (c, (decision, cited, _)) in enumerate(zip(cases, py)):
        deadline = c["terms"]["deadline"]
        if c["now"] > deadline:
            props["termination"]["checked"] += 1
            props["termination"]["violations"] += decision not in (RELEASE, VOID)
        if decision == RELEASE and not by_arbiter(c, cited):
            src = [e for e in c["evidence"] if e["attestation"]["event"]["type"] != "payment.authorized"]
            props["needs_authorisation"]["checked"] += 1
            props["needs_authorisation"]["violations"] += run(c, src)[0] == RELEASE
            nosrc = [e for e in c["evidence"] if not str(e["role"] or "").startswith("source:")]
            props["needs_evidence"]["checked"] += 1
            props["needs_evidence"]["violations"] += run(c, nosrc)[0] == RELEASE
        valid_only = [e for e in c["evidence"] if e["verdict"] == "valid"]
        props["invalid_is_inert"]["checked"] += 1
        props["invalid_is_inert"]["violations"] += run(c, valid_only)[:2] != (decision, cited)
        shuffled = c["evidence"][:]
        prng.shuffle(shuffled)
        props["order_independent"]["checked"] += 1
        props["order_independent"]["violations"] += run(c, shuffled)[:2] != (decision, cited)
        if decision == RELEASE and not by_arbiter(c, cited) and c["terms"]["rule"] == RULE_CONDITIONS:
            # one more valid attestation for a cited leaf: earlier event time, received just before `now`
            src = [e for e in c["evidence"] if e["attestation"]["attestation_id"] in cited
                   and str(e["role"] or "").startswith("source:")]
            if src:
                twin = json.loads(json.dumps(prng.choice(src)))
                twin["attestation"]["attestation_id"] += "_twin"
                twin["attestation"]["timestamps"]["source_event_time"] = ts(T0)
                twin["received_at"] = c["now"]
                after = run(c, c["evidence"] + [twin])[0]
                props["extra_evidence"]["checked"] += 1
                if after != RELEASE:
                    props["extra_evidence"]["violations"] += 1
                    if len(extra_examples) < 3:
                        extra_examples.append({"case": i, "after": after,
                                               "dispute_window_hours": c["terms"].get("dispute_window_hours")})
    windows = Counter()
    for ex_case, (decision, cited, _) in zip(cases, py):
        windows[decision] += 1
    result = {"cases": n, "seed": seed,
              "by_rule": dict(Counter(c["terms"]["rule"] for c in cases)),
              "decisions": {k: windows.get(k, 0) for k in (WAIT, HOLD, RELEASE, VOID)},
              "evaluated_after_deadline": sum(c["now"] > c["terms"]["deadline"] for c in cases),
              "python_vs_javascript": {"disagreements": diff, "examples": examples},
              "properties": props, "extra_evidence_examples": extra_examples}
    Path(out_path).write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
