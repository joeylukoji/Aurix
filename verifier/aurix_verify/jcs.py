"""JSON canonicalisation, RFC 8785 (JCS) subset.

AURIX evidence never contains floating-point numbers (amounts are decimal strings), so numbers are
limited to integers; a float is rejected instead of being serialised ambiguously. Within that subset the
output is byte-identical to RFC 8785: keys sorted by UTF-16 code units, no whitespace, ES6 string escaping.
"""
from __future__ import annotations

import hashlib
import json

_MAX_SAFE = 2**53 - 1


def _enc(v, out: list[str]) -> None:
    if v is None or isinstance(v, bool):
        out.append({None: "null", True: "true", False: "false"}[v])
    elif isinstance(v, int):
        if abs(v) > _MAX_SAFE:
            raise ValueError("integer outside the IEEE-754 safe range; use a decimal string")
        out.append(str(v))
    elif isinstance(v, float):
        raise TypeError("floats are not allowed in AURIX evidence; use a decimal string")
    elif isinstance(v, str):
        out.append(json.dumps(v, ensure_ascii=False))
    elif isinstance(v, (list, tuple)):
        out.append("[")
        for i, x in enumerate(v):
            if i:
                out.append(",")
            _enc(x, out)
        out.append("]")
    elif isinstance(v, dict):
        if not all(isinstance(k, str) for k in v):
            raise TypeError("object keys must be strings")
        out.append("{")
        for i, k in enumerate(sorted(v, key=lambda k: k.encode("utf-16-be"))):
            if i:
                out.append(",")
            out.append(json.dumps(k, ensure_ascii=False))
            out.append(":")
            _enc(v[k], out)
        out.append("}")
    else:
        raise TypeError(f"unsupported type {type(v).__name__}")


def canonicalize(obj) -> bytes:
    out: list[str] = []
    _enc(obj, out)
    return "".join(out).encode("utf-8")


def digest(obj) -> str:
    """'sha256:<hex>' of the canonical form."""
    return "sha256:" + hashlib.sha256(canonicalize(obj)).hexdigest()
