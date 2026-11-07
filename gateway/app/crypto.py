"""ed25519 keys and canonical sign bytes, byte-compatible with chain/ledger (Go)."""
from __future__ import annotations

import hashlib
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

MSG_FIELDS = ["type", "to", "amount", "arbiter", "deadline_height", "escrow_id", "hash", "external_ref"]
DEFAULT_FEE = 1_000
MICRO = 1_000_000


def key_from_seed(seed: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(seed.encode()).digest())


def pubkey_bytes(k: Ed25519PrivateKey) -> bytes:
    return k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def address_from_pub(pub: bytes) -> str:
    return "aur1" + hashlib.sha256(pub).digest()[:20].hex()


def canonical_msg(msg: dict) -> dict:
    """Same field order as Go's Msg struct; zero values omitted (omitempty)."""
    return {f: msg[f] for f in MSG_FIELDS if msg.get(f) not in (None, "", 0)}


def sign_bytes(chain_id: str, msg: dict, sender: str, nonce: int, fee: int) -> bytes:
    doc = {"chain_id": chain_id, "msg": canonical_msg(msg), "from": sender, "nonce": nonce, "fee": fee}
    return json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode()


def sign_tx(k: Ed25519PrivateKey, chain_id: str, msg: dict, nonce: int, fee: int = DEFAULT_FEE) -> dict:
    pub = pubkey_bytes(k)
    sender = address_from_pub(pub)
    sig = k.sign(sign_bytes(chain_id, msg, sender, nonce, fee))
    return {"msg": canonical_msg(msg), "from": sender, "pub_key": pub.hex(), "nonce": nonce, "fee": fee,
            "signature": sig.hex()}
