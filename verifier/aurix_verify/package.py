"""Proof package: build (used by the payments service) and verify (used by anyone, offline).

Layout of aurix-proof-<order_id>.zip:
  manifest.json            files and their sha256, order id, rule, creation time
  manifest.sig.json        AURIX signature of the manifest
  terms.json               escrow terms signed by AURIX at order creation   (+ signatures/terms.json)
  attestations/<id>.json   one per attestation                              (+ signatures/<id>.json)
  receipts.json            when AURIX received each attestation             (+ signatures/receipts.json)
  decision.json            the decision and the hashes of its inputs        (+ signatures/decision.json)
  trust/public-keys.json   keys declared by the issuer
  trust/trust-metadata.json  which sources are real or simulated
  advisory/<kind>.json     optional AI advisor texts: integrity-protected, never used by the rule
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections import Counter

from . import crypto
from .jcs import canonicalize, digest
from .rule import RULE_ID, Evidence, decide

SCHEMA = "aurix.proof-package"
LIMITATIONS_ALWAYS = [
    "Cryptographic validity proves who signed what and that nothing changed; it does not prove that the "
    "goods were physically delivered.",
    "The funds were held and moved by PayPal, never by AURIX.",
]
FORBIDDEN_WORDS = ("guaranteed", "irrevocable", "garanti", "irrévocable")


def _dump(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"


def receipts_doc(order_id: str, received: list[tuple[dict, str]]) -> dict:
    """AURIX's record of when it received each attestation (an attestation cannot carry that itself:
    a carrier signs before AURIX sees it). The decision replay only uses evidence received in time."""
    return {"schema": "aurix.receipts", "schema_version": "1.0", "order_id": order_id,
            "receipts": sorted(({"attestation_hash": digest(a), "received_at": t} for a, t in received),
                               key=lambda r: (r["received_at"], r["attestation_hash"]))}


def build_package(*, terms: dict, terms_sig: dict, attestations: list[tuple[dict, dict]],
                  receipts: dict, receipts_sig: dict, decision: dict | None, decision_sig: dict | None,
                  keys: dict, trust_metadata: dict, signer: crypto.Signer, created_at: str,
                  advisories: dict[str, dict] | None = None) -> bytes:
    files: dict[str, bytes] = {"terms.json": _dump(terms), "signatures/terms.json": _dump(terms_sig),
                               "receipts.json": _dump(receipts), "signatures/receipts.json": _dump(receipts_sig),
                               "trust/public-keys.json": _dump(keys),
                               "trust/trust-metadata.json": _dump(trust_metadata)}
    for att, sig in attestations:
        aid = att["attestation_id"]
        files[f"attestations/{aid}.json"] = _dump(att)
        files[f"signatures/{aid}.json"] = _dump(sig)
    for kind, doc in (advisories or {}).items():
        files[f"advisory/{kind}.json"] = _dump(doc)
    if decision is not None:
        files["decision.json"] = _dump(decision)
        files["signatures/decision.json"] = _dump(decision_sig)
    manifest = {"schema": SCHEMA, "schema_version": "1.0", "order_id": terms["order_id"], "rule": RULE_ID,
                "created_at": created_at,
                "files": {p: "sha256:" + hashlib.sha256(b).hexdigest() for p, b in sorted(files.items())}}
    files["manifest.json"] = _dump(manifest)
    files["manifest.sig.json"] = _dump(signer.sign(manifest, created_at))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(files):
            z.writestr(p, files[p])
    return buf.getvalue()


def read_zip(data: bytes, max_files: int = 500, max_bytes: int = 5_000_000) -> dict[str, bytes]:
    out, total = {}, 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos = z.infolist()
        if len(infos) > max_files:
            raise ValueError("too many files in package")
        for i in infos:
            if i.is_dir():
                continue
            if i.filename.startswith("/") or ".." in i.filename.split("/"):
                raise ValueError(f"unsafe path in package: {i.filename}")
            total += i.file_size
            if total > max_bytes:
                raise ValueError("package too large")
            out[i.filename] = z.read(i)
    return out


def verify_package(files: dict[str, bytes], trusted_keys: dict | None = None) -> dict:
    """Returns a verification report. Never raises on a bad package: problems become report entries."""
    errors: list[str] = []
    anomalies: list[dict] = []

    def load(path):
        try:
            return json.loads(files[path])
        except KeyError:
            errors.append(f"missing file: {path}")
        except ValueError:
            errors.append(f"not valid JSON: {path}")
        return None

    manifest = load("manifest.json")
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        return _report(None, "invalid", "invalid", "unverified", "incomplete", [], None,
                       errors + ["not an AURIX proof package"], [], "none")
    order_id = manifest.get("order_id")

    # 1. Integrity: every listed file present and unchanged; nothing unlisted.
    listed = manifest.get("files") or {}
    integrity_ok = True
    for path, h in listed.items():
        if path not in files:
            errors.append(f"listed file missing: {path}")
            integrity_ok = False
        elif "sha256:" + hashlib.sha256(files[path]).hexdigest() != h:
            errors.append(f"file modified: {path}")
            integrity_ok = False
    extra = set(files) - set(listed) - {"manifest.json", "manifest.sig.json"}
    if extra:
        errors.append(f"unlisted files: {', '.join(sorted(extra))}")
        integrity_ok = False

    # 2. Keys: an external registry if given, otherwise the package's own (self-declared).
    trust_source = "external" if trusted_keys is not None else "package (self-declared)"
    registry = crypto.KeyRegistry(trusted_keys if trusted_keys is not None else (load("trust/public-keys.json") or {}))
    meta = load("trust/trust-metadata.json") or {}

    sig_verdicts: list[str] = []

    def check(obj, sig_path, role) -> str:
        sig = load(sig_path)
        if obj is None or sig is None:
            sig_verdicts.append("missing")
            return "missing"
        v = registry.verify(obj, sig)
        if v == crypto.VALID and registry.role(sig.get("key_id")) != role:
            errors.append(f"{sig_path}: signed by a {registry.role(sig.get('key_id'))} key, {role} required")
            v = crypto.INVALID
        sig_verdicts.append(v)
        if v != crypto.VALID:
            errors.append(f"{sig_path}: {v}")
        return v

    check(manifest, "manifest.sig.json", "aurix")
    terms = load("terms.json")
    terms_ok = check(terms, "signatures/terms.json", "aurix") == crypto.VALID

    # 3. Attestations.
    evidence: list[Evidence] = []
    sources: dict[str, dict] = {}
    for path in sorted(p for p in listed if p.startswith("attestations/")):
        att = load(path)
        if att is None:
            continue
        aid = path.removeprefix("attestations/").removesuffix(".json")
        sig = load(f"signatures/{aid}.json") or {}
        v = registry.verify(att, sig) if sig else "missing"
        sig_verdicts.append(v)
        if v != crypto.VALID:
            errors.append(f"attestation {aid}: signature {v}")
        evidence.append(Evidence(att, sig.get("key_id", ""), registry.role(sig.get("key_id", "")), v))
        src = att.get("source", {})
        prov = src.get("provider", "?")
        s = sources.setdefault(prov, {"provider": prov, "type": src.get("source_type", "unknown"),
                                      "environment": src.get("environment"), "verification_method":
                                      src.get("verification_method"), "status": "verified"})
        if v != crypto.VALID or src.get("verification_status") != "verified":
            s["status"] = "unverified"
        declared = (meta.get("sources") or {}).get(prov, {}).get("type")
        if declared and declared != s["type"]:
            anomalies.append({"class": 2, "code": "source_type_mismatch", "detail": prov})

    # 4. Consistency between the sources and the terms.
    if terms:
        want = (str(terms.get("amount", {}).get("value")), str(terms.get("amount", {}).get("currency")))
        for e in evidence:
            a = e.attestation
            if a.get("operation_id") != order_id:
                anomalies.append({"class": 2, "code": "foreign_order", "detail": a.get("attestation_id")})
            amt = a.get("event", {}).get("amount")
            if amt and (str(amt.get("value")), str(amt.get("currency"))) != want:
                anomalies.append({"class": 3, "code": "amount_mismatch", "detail": a.get("attestation_id")})
        dup = [k for k, n in Counter((e.attestation.get("source", {}).get("provider"),
                                      e.attestation.get("source", {}).get("provider_event_id"))
                                     for e in evidence).items() if n > 1 and k[1]]
        for k in dup:
            anomalies.append({"class": 2, "code": "duplicate_event", "detail": f"{k[0]}:{k[1]}"})
    types = {e.type for e in evidence if e.verdict == crypto.VALID}
    if "payment.authorized" not in types:
        anomalies.append({"class": 1, "code": "missing_payment_authorization", "detail": order_id})
    if "shipment.delivered" not in types and "payment.voided" not in types:
        anomalies.append({"class": 1, "code": "missing_delivery", "detail": order_id})

    # 5. Replay the decision with the evidence AURIX had received at decision time.
    receipts = load("receipts.json") if "receipts.json" in listed else None
    receipts_ok = receipts is not None and check(receipts, "signatures/receipts.json", "aurix") == crypto.VALID
    received_at = {r["attestation_hash"]: crypto.parse_time(r["received_at"])
                   for r in (receipts or {}).get("receipts", [])} if receipts_ok else {}
    for e in evidence:
        if digest(e.attestation) not in received_at:
            errors.append(f"no AURIX receipt for attestation {e.attestation.get('attestation_id')}")
    replay = None
    if "decision.json" in listed:
        decision = load("decision.json")
        dec_ok = check(decision, "signatures/decision.json", "aurix") == crypto.VALID
        if decision and terms and terms_ok:
            at = crypto.parse_time(decision["decided_at"])
            known = [e for e in evidence
                     if digest(e.attestation) in received_at and received_at[digest(e.attestation)] <= at]
            out = decide(terms, known, decision["decided_at"])
            same_inputs = sorted(digest(a) for a in out.inputs) == sorted(decision.get("inputs", []))
            replay = {"recorded": decision.get("decision"), "replayed": out.decision,
                      "matches": dec_ok and out.decision == decision.get("decision") and same_inputs,
                      "rule": decision.get("rule"), "reasons": out.reasons}
            if decision.get("rule") != RULE_ID:
                errors.append(f"unknown rule {decision.get('rule')}")
                replay["matches"] = False
            if not replay["matches"]:
                errors.append("recorded decision does not match the replayed rule")

    signatures = "valid" if sig_verdicts and all(v == crypto.VALID for v in sig_verdicts) else \
        "invalid" if not any(v == crypto.VALID for v in sig_verdicts) else "partial"
    src_status = [s["status"] for s in sources.values()]
    source_verification = "verified" if src_status and all(s == "verified" for s in src_status) else \
        "unverified" if not src_status or all(s != "verified" for s in src_status) else "partial"
    consistency = "inconsistent" if any(a["class"] >= 2 for a in anomalies) else \
        "incomplete" if anomalies else "consistent"
    limitations = list(LIMITATIONS_ALWAYS)
    for s in sources.values():
        if s["type"] == "simulated":
            limitations.insert(0, f"Source '{s['provider']}' is simulated (environment '{s.get('environment')}'): "
                                  "it is not a real organisation's system.")
        if s.get("environment") == "sandbox":
            limitations.append(f"Source '{s['provider']}' runs in a sandbox environment: no real money moved.")
    advisories = []
    for path in sorted(p for p in listed if p.startswith("advisory/")):
        a = load(path) or {}
        advisories.append({"file": path, "model": a.get("model"), "generated_by": a.get("generated_by"),
                           "generated_at": a.get("generated_at")})
    if advisories:
        limitations.append("AI advisor texts (advisory/) are included for information only: the decision rule "
                           "does not use them, and they may contain errors.")
    if trusted_keys is None:
        limitations.append("Keys were taken from the package itself; pass the issuer's published keys "
                           "(--keys) to check who the issuer is.")
    r = _report(order_id, "valid" if integrity_ok else "invalid", signatures, source_verification,
                consistency, list(sources.values()), replay, errors, anomalies, trust_source, limitations)
    r["advisories"] = advisories
    return r


def _report(order_id, integrity, signatures, source_verification, consistency, sources, replay, errors,
            anomalies, trust_source, limitations=None) -> dict:
    ok = integrity == "valid" and signatures == "valid" and (replay is None or replay["matches"])
    return {"schema": "aurix.verification-report", "schema_version": "1.0", "order_id": order_id,
            "result": "VALID" if ok else "INVALID", "integrity": integrity, "signatures": signatures,
            "source_verification": source_verification, "consistency": consistency, "sources": sources,
            "decision": replay, "anomalies": anomalies, "errors": errors, "trust_source": trust_source,
            "limitations": limitations or list(LIMITATIONS_ALWAYS)}


__all__ = ["build_package", "read_zip", "verify_package", "canonicalize", "FORBIDDEN_WORDS"]
