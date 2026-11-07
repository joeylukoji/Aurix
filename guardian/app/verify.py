"""Standalone verification of a Guardian attestation (depends only on `ecdsa`, pure Python).

    from app.verify import verify_attestation
    payload = verify_attestation(resp["attestation"])  # -> dict, raises InvalidAttestation
"""
from __future__ import annotations

import hashlib
import json

from ecdsa import SECP256k1, BadSignatureError, VerifyingKey
from ecdsa.util import sigdecode_string

N = SECP256k1.order
PAYLOAD_KEYS = ("amount", "chain_id", "decision", "denom", "from", "issued_at", "model_version", "score", "to")


class InvalidAttestation(ValueError):
    pass


def canonical(payload: dict) -> str:
    """Sorted keys, no whitespace, UTF-8 (non-ASCII kept as-is)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def verify_attestation(att: dict, expected_pubkey: str | None = None) -> dict:
    try:
        pub, sig, payload = bytes.fromhex(att["pubkey"]), bytes.fromhex(att["signature"]), att["payload"]
    except (KeyError, ValueError, TypeError) as e:
        raise InvalidAttestation("malformed attestation") from e
    if expected_pubkey and att["pubkey"].lower() != expected_pubkey.lower():
        raise InvalidAttestation("unexpected guardian pubkey")
    if len(pub) != 33 or len(sig) != 64:
        raise InvalidAttestation("bad pubkey/signature length")
    if int.from_bytes(sig[32:], "big") > N // 2:
        raise InvalidAttestation("high-S signature")
    try:
        VerifyingKey.from_string(pub, curve=SECP256k1).verify(
            sig, payload.encode(), hashfunc=hashlib.sha256, sigdecode=sigdecode_string)
    except (BadSignatureError, ValueError) as e:
        raise InvalidAttestation("bad signature") from e
    data = json.loads(payload)
    if canonical(data) != payload or tuple(sorted(data)) != PAYLOAD_KEYS:
        raise InvalidAttestation("payload not canonical")
    if att.get("guardian"):
        try:
            from .keys import address_from_pubkey
        except ImportError:  # verifier used standalone without bip_utils
            return data
        if att["guardian"] != address_from_pubkey(pub):
            raise InvalidAttestation("guardian address does not match pubkey")
    return data
