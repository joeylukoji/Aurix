"""Minimal protobuf (proto3) wire encoder for the few Cosmos messages we build."""
from __future__ import annotations


def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _tag(field: int, wire: int) -> bytes:
    return varint(field << 3 | wire)


def uint(field: int, n: int) -> bytes:
    return _tag(field, 0) + varint(n) if n else b""


def bytes_(field: int, b: bytes) -> bytes:
    return _tag(field, 2) + varint(len(b)) + b if b else b""


def string(field: int, s: str) -> bytes:
    return bytes_(field, s.encode()) if s else b""


def msg(field: int, b: bytes) -> bytes:
    """Embedded message: emitted even when empty."""
    return _tag(field, 2) + varint(len(b)) + b


def coin(denom: str, amount: int) -> bytes:
    return string(1, denom) + string(2, str(amount))


def any_(type_url: str, value: bytes) -> bytes:
    return string(1, type_url) + bytes_(2, value)


ANCHOR_PROOF_TYPE_URL = "/aurix.smartmoney.v1.MsgAnchorProof"


def msg_anchor_proof(signer: str, hash_hex: str, external_ref: str, capture_ref: str = "") -> bytes:
    """aurix.smartmoney.v1.MsgAnchorProof {signer=1, hash=2, external_ref=3, capture_ref=4} (one attestation)."""
    return string(1, signer) + string(2, hash_hex) + string(3, external_ref) + string(4, capture_ref)
