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
