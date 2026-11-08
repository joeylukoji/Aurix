"""Ed25519 signatures over SHA-256(JCS(object)), and the key registry (trust/public-keys.json)."""
from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .jcs import digest

ALG = "Ed25519"


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def unb64(s: str) -> bytes:
    return base64.b64decode(s, validate=True)


def parse_time(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def public_key_b64(priv: Ed25519PrivateKey) -> str:
    return b64(priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def fingerprint(pub_b64: str) -> str:
    return hashlib.sha256(unb64(pub_b64)).hexdigest()[:16]


@dataclass
class Signer:
    key_id: str
    private_key: Ed25519PrivateKey

    @classmethod
    def from_seed(cls, key_id: str, seed: bytes) -> "Signer":
        if len(seed) != 32:
            raise ValueError("Ed25519 seed must be 32 bytes")
        return cls(key_id, Ed25519PrivateKey.from_private_bytes(seed))

    @property
    def public_key(self) -> str:
        return public_key_b64(self.private_key)

    def sign(self, obj, created_at: str) -> dict:
        """Detached signature block for obj (the block itself is not part of what is signed)."""
        h = digest(obj)
        return {"alg": ALG, "key_id": self.key_id, "signed_hash": h,
                "signature": b64(self.private_key.sign(h.encode())), "created_at": created_at}


# Signature verdicts (docs/architecture/mscfa.md §7)
VALID, INVALID, UNKNOWN_KEY, REVOKED_KEY = "valid", "invalid", "unknown_key", "revoked_key"
VALID_KEY_REVOKED_LATER = "valid_but_key_revoked_after_signing"


class KeyRegistry:
    """Keys from trust/public-keys.json: {"keys": [{key_id, alg, public_key, role, owner, valid_from,
    valid_until, revoked_at, revocation_reason}]}."""

    def __init__(self, doc: dict):
        self.keys = {k["key_id"]: k for k in doc.get("keys", [])}

    def get(self, key_id: str) -> dict | None:
        return self.keys.get(key_id)

    def verify(self, obj, sig: dict) -> str:
        k = self.get(sig.get("key_id", ""))
        if not k or k.get("alg") != ALG or sig.get("alg") != ALG:
            return UNKNOWN_KEY
        if sig.get("signed_hash") != digest(obj):
            return INVALID
        try:
            Ed25519PublicKey.from_public_bytes(unb64(k["public_key"])).verify(unb64(sig["signature"]),
                                                                              sig["signed_hash"].encode())
        except (InvalidSignature, ValueError, KeyError):
            return INVALID
        signed = parse_time(sig.get("created_at"))
        start, end, revoked = (parse_time(k.get(f)) for f in ("valid_from", "valid_until", "revoked_at"))
        if signed is None or (start and signed < start) or (end and signed > end):
            return INVALID  # signature claims a time outside the key's validity window
        if revoked:
            # created_at is the signer's own claim: without an external timestamp it cannot prove the
            # signature predates the revocation, so this verdict is indicative only.
            return VALID_KEY_REVOKED_LATER if signed < revoked else REVOKED_KEY
        return VALID

    def valid_at(self, key_id: str, when: datetime) -> bool:
        """Was the key inside its validity window and not revoked at `when` (a time the verifier trusts,
        e.g. AURIX's receipt time), as opposed to the signer's own `created_at` claim?"""
        k = self.get(key_id)
        if not k:
            return False
        start, end, revoked = (parse_time(k.get(f)) for f in ("valid_from", "valid_until", "revoked_at"))
        return not ((start and when < start) or (end and when > end) or (revoked and when >= revoked))

    def role(self, key_id: str) -> str | None:
        k = self.get(key_id)
        return k and k.get("role")
