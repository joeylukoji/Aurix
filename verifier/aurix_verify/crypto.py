"""Ed25519 signatures over SHA-256(JCS(object)), and the key registry (trust/public-keys.json).

Signature block, version 2: {alg, created_at, key_id, signed_hash, version, signature}. The signature covers
the canonical form of the whole block except `signature`, so the signing time and the key id are authenticated
together with the hash. A block without `version` is the earlier format, whose signature covers the hash only;
it still verifies (stored orders carry such blocks), but its `created_at` is then the signer's unauthenticated
claim, which is why the package verifier also ties the manifest signature's time to the manifest itself.
"""
from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .jcs import canonicalize, digest

ALG = "Ed25519"
SIGNATURE_VERSION = 2


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def unb64(s: str) -> bytes:
    """Strict base64: one accepted spelling per byte string (standard alphabet, padding, zero trailing bits).
    A lenient decoder would let someone change the text of a signature without changing the bytes it encodes."""
    if not isinstance(s, str):
        raise ValueError("base64 value must be a string")
    raw = base64.b64decode(s, validate=True)
    if b64(raw) != s:
        raise ValueError("non-canonical base64")
    return raw


TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"  # UTC, second precision, "Z" (CODING_STANDARDS §2)


def format_time(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(TIME_FORMAT)


def utcnow() -> str:
    return format_time(datetime.now(timezone.utc))


def parse_time(s: str | None) -> datetime | None:
    """ISO 8601 to an aware datetime; a time without zone is taken as UTC (so comparisons never mix kinds)."""
    if not s:
        return None
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


_STRICT_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def parse_strict_time(s) -> datetime | None:
    """A time in exactly TIME_FORMAT, or None. Used for signing times, so that every verifier (this one and
    the browser port) accepts the same strings; never raises."""
    if not isinstance(s, str) or not _STRICT_TIME.fullmatch(s):
        return None
    try:
        return datetime.strptime(s, TIME_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:  # well-formed but not a real date, e.g. month 13
        return None


def try_time(s) -> datetime | None:
    """parse_time for untrusted input: None when the value is missing or not a time; never raises."""
    try:
        return parse_time(s) if isinstance(s, str) else None
    except ValueError:
        return None


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
        """Detached signature block for obj. The signature covers the whole block (see the module docstring)."""
        block = {"alg": ALG, "key_id": self.key_id, "signed_hash": digest(obj), "created_at": created_at,
                 "version": SIGNATURE_VERSION}
        block["signature"] = b64(self.private_key.sign(signed_message(block)))
        return block


def signed_message(sig: dict) -> bytes:
    """The bytes a signature block's signature covers. Raises ValueError for an unknown version."""
    version = sig.get("version")
    if version is None:  # earlier format: the hash only
        return sig["signed_hash"].encode()
    if version == SIGNATURE_VERSION and not isinstance(version, bool):
        return canonicalize({k: v for k, v in sig.items() if k != "signature"})
    raise ValueError("unknown signature block version")


# Signature verdicts (docs/architecture/evidence-escrow.md §6)
VALID, INVALID, UNKNOWN_KEY, REVOKED_KEY = "valid", "invalid", "unknown_key", "revoked_key"
VALID_KEY_REVOKED_LATER = "valid_but_key_revoked_after_signing"


class KeyRegistry:
    """Keys from trust/public-keys.json: {"keys": [{key_id, alg, public_key, role, owner, valid_from,
    valid_until, revoked_at, revocation_reason}]}."""

    def __init__(self, doc: dict):
        # A registry read from a package is untrusted input: entries that are not well-formed are dropped.
        entries = doc.get("keys") if isinstance(doc, dict) else None
        self.keys = {k["key_id"]: k for k in (entries if isinstance(entries, list) else [])
                     if isinstance(k, dict) and isinstance(k.get("key_id"), str)}

    def get(self, key_id) -> dict | None:
        return self.keys.get(key_id) if isinstance(key_id, str) else None

    def verify(self, obj, sig) -> str:
        """Verdict on a signature block. Never raises: anything malformed is `invalid`."""
        if not isinstance(sig, dict):
            return INVALID
        k = self.get(sig.get("key_id"))
        if not k or k.get("alg") != ALG or sig.get("alg") != ALG:
            return UNKNOWN_KEY
        try:
            if sig.get("signed_hash") != digest(obj):
                return INVALID
            Ed25519PublicKey.from_public_bytes(unb64(k["public_key"])).verify(unb64(sig["signature"]),
                                                                              signed_message(sig))
            start, end, revoked = (parse_time(k.get(f)) for f in ("valid_from", "valid_until", "revoked_at"))
        except (InvalidSignature, ValueError, KeyError, TypeError, AttributeError):
            return INVALID
        signed = parse_strict_time(sig.get("created_at"))
        if signed is None or (start and signed < start) or (end and signed > end):
            return INVALID  # no well-formed signing time, or one outside the key's validity window
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
        try:
            start, end, revoked = (parse_time(k.get(f)) for f in ("valid_from", "valid_until", "revoked_at"))
        except (ValueError, TypeError, AttributeError):
            return False
        return not ((start and when < start) or (end and when > end) or (revoked and when >= revoked))

    def role(self, key_id) -> str | None:
        k = self.get(key_id)
        role = k and k.get("role")
        return role if isinstance(role, str) else None
