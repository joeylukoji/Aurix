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
from .rule import RULE_ID, RULES, Evidence, decide

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
    manifest = {"schema": SCHEMA, "schema_version": "1.0", "order_id": terms["order_id"],
                "rule": terms.get("rule", RULE_ID),
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


def _obj(x) -> dict:
    """x if it is a JSON object, otherwise an empty one (package content is untrusted input)."""
    return x if isinstance(x, dict) else {}


def _digest(obj) -> str | None:
    """Canonical hash, or None when the object has no canonical form (a float, for instance)."""
    try:
        return digest(obj)
    except (TypeError, ValueError):
        return None


def verify_package(files: dict[str, bytes], trusted_keys: dict | None = None) -> dict:
    """Returns a verification report. Never raises on a bad package: problems become report entries, and a
    package malformed in a way no check anticipated is reported INVALID (the verifier fails closed)."""
    try:
        return _verify_package(files, trusted_keys)
    except Exception as exc:  # noqa: BLE001 - deliberate last line of defence, see the docstring
        return _report(None, "invalid", "invalid", "unverified", "incomplete", [], None,
                       [f"malformed package ({type(exc).__name__})"], [],
                       "external" if trusted_keys is not None else "package (self-declared)")


def _verify_package(files: dict[str, bytes], trusted_keys: dict | None) -> dict:
    errors: list[str] = []
    anomalies: list[dict] = []

    def load(path):
        """A file's JSON object, or None (with a report entry) when it is missing or not a JSON object."""
        try:
            doc = json.loads(files[path])
        except KeyError:
            errors.append(f"missing file: {path}")
            return None
        except (ValueError, RecursionError):
            errors.append(f"not valid JSON: {path}")
            return None
        if not isinstance(doc, dict):
            errors.append(f"not a JSON object: {path}")
            return None
        return doc

    manifest = load("manifest.json")
    if manifest is None or manifest.get("schema") != SCHEMA:
        return _report(None, "invalid", "invalid", "unverified", "incomplete", [], None,
                       errors + ["not an AURIX proof package"], [], "none")
    order_id = manifest.get("order_id")

    # 1. Integrity: every listed file present and unchanged; nothing unlisted.
    listed = manifest.get("files")
    integrity_ok = True
    if not isinstance(listed, dict):
        errors.append("manifest.json: no file list")
        listed, integrity_ok = {}, False
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

    def check(obj, sig_path, role, signed_at=None) -> str:
        sig = load(sig_path)
        if obj is None or sig is None:
            sig_verdicts.append("missing")
            return "missing"
        v = registry.verify(obj, sig)
        if v == crypto.VALID and registry.role(sig.get("key_id")) != role:
            errors.append(f"{sig_path}: signed by a {registry.role(sig.get('key_id'))} key, {role} required")
            v = crypto.INVALID
        if v == crypto.VALID and signed_at is not None and sig.get("created_at") != signed_at:
            errors.append(f"{sig_path}: signing time differs from the signed document's")
            v = crypto.INVALID
        sig_verdicts.append(v)
        if v != crypto.VALID:
            errors.append(f"{sig_path}: {v}")
        return v

    # Every other signature file is listed, with its hash, in the manifest. The manifest's own signature file
    # cannot be, so its signing time is tied to the manifest's `created_at`, which the signature covers.
    check(manifest, "manifest.sig.json", "aurix", signed_at=manifest.get("created_at") or "")
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
        key_id = sig.get("key_id") if isinstance(sig.get("key_id"), str) else ""
        evidence.append(Evidence(att, key_id, registry.role(key_id), v))
        src = _obj(att.get("source"))
        prov = src.get("provider") if isinstance(src.get("provider"), str) else "?"
        s = sources.setdefault(prov, {"provider": prov, "type": src.get("source_type", "unknown"),
                                      "environment": src.get("environment"), "verification_method":
                                      src.get("verification_method"), "status": "verified"})
        if v != crypto.VALID or src.get("verification_status") != "verified":
            s["status"] = "unverified"
        declared = _obj(_obj(meta.get("sources")).get(prov)).get("type")
        if declared and declared != s["type"]:
            anomalies.append({"class": 2, "code": "source_type_mismatch", "detail": prov})

    # 4. Consistency between the sources and the terms.
    if terms:
        amount = _obj(terms.get("amount"))
        want = (str(amount.get("value")), str(amount.get("currency")))
        for e in evidence:
            a = e.attestation
            if a.get("operation_id") != order_id:
                anomalies.append({"class": 2, "code": "foreign_order", "detail": a.get("attestation_id")})
            amt = _obj(a.get("event")).get("amount")
            if amt and (str(_obj(amt).get("value")), str(_obj(amt).get("currency"))) != want:
                anomalies.append({"class": 3, "code": "amount_mismatch", "detail": a.get("attestation_id")})
        dup = [k for k, n in Counter((str(_obj(e.attestation.get("source")).get("provider")),
                                      str(_obj(e.attestation.get("source")).get("provider_event_id") or ""))
                                     for e in evidence).items() if n > 1 and k[1]]
        for k in dup:
            anomalies.append({"class": 2, "code": "duplicate_event", "detail": f"{k[0]}:{k[1]}"})
    types = {_obj(e.attestation.get("event")).get("type") for e in evidence if e.verdict == crypto.VALID}
    types = {x for x in types if isinstance(x, str)}
    if "payment.authorized" not in types:
        anomalies.append({"class": 1, "code": "missing_payment_authorization", "detail": order_id})
    if "shipment.delivered" not in types and "payment.voided" not in types:
        anomalies.append({"class": 1, "code": "missing_delivery", "detail": order_id})

    # 5. Replay the decision with the evidence AURIX had received at decision time.
    receipts = load("receipts.json") if "receipts.json" in listed else None
    receipts_ok = receipts is not None and check(receipts, "signatures/receipts.json", "aurix") == crypto.VALID
    received_at = {}
    if receipts_ok:
        entries = receipts.get("receipts")
        for r in entries if isinstance(entries, list) else []:
            when = crypto.try_time(_obj(r).get("received_at"))
            if when is None or not isinstance(_obj(r).get("attestation_hash"), str):
                errors.append("receipts.json: malformed entry")
            else:
                received_at[r["attestation_hash"]] = when
    hashes = {id(e): _digest(e.attestation) for e in evidence}
    for e in evidence:
        rec = received_at.get(hashes[id(e)])
        if rec is None:
            errors.append(f"no AURIX receipt for attestation {e.attestation.get('attestation_id')}")
        elif e.verdict == crypto.VALID and not registry.valid_at(e.key_id, rec):
            # the signer's created_at can be chosen; the key must also be valid when AURIX received it
            errors.append(f"attestation {e.attestation.get('attestation_id')}: key not valid when received")
            e.verdict = crypto.INVALID
            sig_verdicts.append(crypto.INVALID)
    replay = None
    if "decision.json" in listed:
        decision = load("decision.json")
        dec_ok = check(decision, "signatures/decision.json", "aurix") == crypto.VALID
        at = crypto.try_time(decision.get("decided_at")) if decision else None
        if decision is not None and at is None:
            errors.append("decision.json: decided_at missing or not a valid time")
        if decision and terms and terms_ok and at is not None:
            known = []
            for e in evidence:
                rec = received_at.get(hashes[id(e)])
                if rec is not None and rec <= at:
                    e.received_at = crypto.format_time(rec)
                    known.append(e)
            rule = decision.get("rule")
            try:
                out = decide(terms, known, decision["decided_at"])
                inputs = decision.get("inputs")
                same_inputs = isinstance(inputs, list) and all(isinstance(h, str) for h in inputs) and \
                    sorted(digest(a) for a in out.inputs) == sorted(inputs)
                replay = {"recorded": decision.get("decision"), "replayed": out.decision,
                          "matches": dec_ok and out.decision == decision.get("decision") and same_inputs,
                          "rule": rule, "reasons": out.reasons}
            except (ValueError, KeyError, TypeError, AttributeError, ArithmeticError):
                # signed terms or evidence the rule cannot evaluate: the recorded decision is not reproducible
                replay = {"recorded": decision.get("decision"), "replayed": None, "matches": False,
                          "rule": rule, "reasons": ["the rule cannot be evaluated on these terms and evidence"]}
            if not isinstance(rule, str) or rule not in RULES or rule != terms.get("rule", RULE_ID):
                errors.append(f"unknown rule {rule}")
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
