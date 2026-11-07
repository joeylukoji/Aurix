"""Guardian quorum (contract H3): query every configured Guardian, verify each signed risk score, aggregate.

Signature verification is a copy of guardian/app/verify.py re-implemented with `cryptography` (the gateway
never imports the guardian package): payload = canonical JSON (sorted keys, no spaces), signature = 64-byte
r||s low-S ECDSA secp256k1 over sha256(payload utf-8), address = bech32("aur", ripemd160(sha256(pubkey))).

Aggregate = most severe decision (then highest score) among VALID attestations only. `quorum_met` is true
when at least GUARDIAN_QUORUM distinct guardian keys returned a valid attestation. Still advisory only.
"""
from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from Crypto.Hash import RIPEMD160
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from .crypto import PREFIX, bech32_encode

N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
PAYLOAD_KEYS = ("amount", "chain_id", "decision", "denom", "from", "issued_at", "model_version", "score", "to")
SEVERITY = {"low": 0, "medium": 1, "high": 2}
MAX_AGE_S = 300  # attestation freshness window (also tolerates this much clock skew into the future)


class InvalidAttestation(ValueError):
    pass


def canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def address_from_pubkey(pub33: bytes) -> str:
    return bech32_encode(PREFIX, RIPEMD160.new(hashlib.sha256(pub33).digest()).digest())


def verify_attestation(att: dict, expected_pubkey: str | None = None) -> dict:
    """Returns the decoded payload; raises InvalidAttestation."""
    try:
        pub, sig, payload = bytes.fromhex(att["pubkey"]), bytes.fromhex(att["signature"]), att["payload"]
        if not isinstance(payload, str):
            raise TypeError
    except (KeyError, ValueError, TypeError) as e:
        raise InvalidAttestation("malformed attestation") from e
    if expected_pubkey and att["pubkey"].lower() != expected_pubkey.lower():
        raise InvalidAttestation("unexpected guardian pubkey")
    if len(pub) != 33 or len(sig) != 64:
        raise InvalidAttestation("bad pubkey/signature length")
    r, s = int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
    if not (0 < r < N and 0 < s <= N // 2):
        raise InvalidAttestation("high-S or out of range signature")
    try:
        key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256K1(), pub)
        key.verify(encode_dss_signature(r, s), payload.encode(), ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError) as e:
        raise InvalidAttestation("bad signature") from e
    try:
        data = json.loads(payload)
    except ValueError as e:
        raise InvalidAttestation("payload is not JSON") from e
    if not isinstance(data, dict) or canonical(data) != payload or tuple(sorted(data)) != PAYLOAD_KEYS:
        raise InvalidAttestation("payload not canonical")
    if data["decision"] not in SEVERITY:
        raise InvalidAttestation("unknown decision")
    if att.get("guardian") and att["guardian"] != address_from_pubkey(pub):
        raise InvalidAttestation("guardian address does not match pubkey")
    return data


class GuardianClient:
    """Advisory only: any failure returns None and never blocks a payment.

    `base_url` may be one URL or a comma separated list (GUARDIAN_URLS). `pubkeys` (GUARDIAN_PUBKEYS,
    optional) restricts accepted attestations to known guardian keys."""

    def __init__(self, base_url: str | list[str], transport: httpx.BaseTransport | None = None,
                 quorum: int = 1, pubkeys: list[str] | None = None, timeout: float = 3):
        urls = base_url.split(",") if isinstance(base_url, str) else list(base_url)
        self.urls = [u.strip().rstrip("/") for u in urls if u.strip()]
        self.quorum = max(1, int(quorum))
        self.pubkeys = {p.strip().lower() for p in (pubkeys or []) if p.strip()}
        self.http = httpx.Client(timeout=timeout, transport=transport)

    def _query(self, url: str, body: dict) -> dict:
        entry = {"url": url, "valid": False}
        try:
            r = self.http.post(f"{url}/v1/risk/score", json=body)
            r.raise_for_status()
            resp = r.json()
        except (httpx.HTTPError, ValueError) as e:
            return entry | {"error": f"unreachable: {type(e).__name__}"}
        entry["response"] = resp
        att = resp.get("attestation") if isinstance(resp, dict) else None
        if not att:
            return entry | {"error": "unsigned response"}
        try:
            data = verify_attestation(att)
            if self.pubkeys and att["pubkey"].lower() not in self.pubkeys:
                raise InvalidAttestation("guardian pubkey not in GUARDIAN_PUBKEYS")
            if data["from"] != body.get("sender") or data["to"] != body.get("recipient") \
                    or data["denom"] != body.get("currency"):
                raise InvalidAttestation("attestation is for another payment")
            if body.get("chain_id") and data["chain_id"] != body["chain_id"]:
                raise InvalidAttestation("attestation is for another chain")
            if abs(time.time() - int(data["issued_at"])) > MAX_AGE_S:
                raise InvalidAttestation("stale attestation")
        except InvalidAttestation as e:
            return entry | {"error": str(e), "guardian": att.get("guardian") if isinstance(att, dict) else None}
        return entry | {"valid": True, "guardian": att.get("guardian") or address_from_pubkey(bytes.fromhex(att["pubkey"])),
                        "pubkey": att["pubkey"].lower(), "risk_score": float(data["score"]),
                        "decision": data["decision"], "attestation": att}

    def score(self, **body) -> dict | None:
        """Queries all guardians in parallel. Returns None when no guardian returned a valid signed score;
        otherwise the most severe valid response (same fields as Guardian's) plus `quorum` and `guardians`."""
        if not self.urls:
            return None
        with ThreadPoolExecutor(max_workers=min(8, len(self.urls))) as ex:
            results = list(ex.map(lambda u: self._query(u, body), self.urls))
        valid, seen = [], set()
        for res in results:
            if res["valid"]:
                if res["pubkey"] in seen:  # one key answering at two URLs counts once
                    res.update(valid=False, error="duplicate guardian key")
                    continue
                seen.add(res["pubkey"])
                valid.append(res)
        if not valid:
            return None
        top = max(valid, key=lambda r: (SEVERITY[r["decision"]], r["risk_score"]))
        out = dict(top["response"])
        out["risk_score"], out["level"] = top["risk_score"], top["decision"]  # signed values are authoritative
        agree = sum(1 for r in valid if r["decision"] == top["decision"])
        out["quorum"] = {"configured": len(self.urls), "valid": len(valid), "agree": agree,
                         "required": self.quorum, "quorum_met": len(valid) >= self.quorum,
                         "decision": top["decision"]}
        out["quorum_met"] = out["quorum"]["quorum_met"]
        out["guardians"] = [{k: v for k, v in r.items() if k != "response"} |
                            ({"level": r["response"].get("level")} if isinstance(r.get("response"), dict) else {})
                            for r in results]
        return out
