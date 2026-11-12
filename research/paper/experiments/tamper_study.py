"""Exhaustive single-bit corruption of proof packages.

For each package and each file in it, every bit is flipped in turn and the verifier is run with pinned keys.
A trial ends in one of four ways:
  rejected   the report says INVALID, produced by a specific check
  fallback   the report says INVALID, produced by the verifier's last line of defence ("malformed package")
  exception  the verifier raised instead of returning a report
  accepted   the report says VALID

The script only uses the verifier's public interface and its test helpers, so the same file runs against any
revision of the verifier:

    PYTHONPATH=<rev>/verifier:<rev>/verifier/tests python research/paper/experiments/tamper_study.py OUT.json [--dump DIR]

`--dump DIR` also writes each case (files, pinned keys, verdict string) for the JavaScript replication
(tamper_study_js.js).
"""
import base64
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path
from unittest import mock

import helpers as h
from aurix_verify import crypto
from aurix_verify.package import read_zip, verify_package

CODE = {"rejected": "r", "fallback": "f", "exception": "x", "accepted": "A"}


def file_class(path: str) -> str:
    if path.startswith("attestations/"):
        return "attestations/*"
    if path.startswith("signatures/"):
        return "signatures/*"
    if path.startswith("trust/"):
        return "trust/*"
    return path


def scenarios():
    release = dict(items=[h.signed(h.att("payment.authorized")),
                          h.signed(h.att("shipment.delivered", when="2026-11-10T08:00:00Z"))])
    cond = dict(t=h.cond_terms(),
                items=[h.signed(h.att("payment.authorized")),
                       h.signed(h.att("shipment.delivered", when="2026-11-10T08:00:00Z")),
                       h.signed(h.att("inspection.passed", when="2026-11-10T08:30:00Z"))])
    void = dict(items=[h.signed(h.att("payment.authorized"))], decided_at="2026-11-16T00:00:01Z")
    yield "delivery-attested/1, release", "current", h.package(**release)
    yield "conditions/1, release", "current", h.package(**cond)
    yield "delivery-attested/1, void", "current", h.package(**void)
    legacy_sign = getattr(h, "legacy_sign", None)
    if legacy_sign is not None:  # revisions that sign version-2 blocks: also test a package in the earlier format
        with mock.patch.object(crypto.Signer, "sign", legacy_sign):
            yield "delivery-attested/1, release", "earlier", h.package(
                items=[h.signed(h.att("payment.authorized")),
                       h.signed(h.att("shipment.delivered", when="2026-11-10T08:00:00Z"))])


def trial(files: dict, path: str, data: bytes, bit: int, pinned: dict) -> str:
    b = bytearray(data)
    b[bit // 8] ^= 1 << (bit % 8)
    try:
        rep = verify_package({**files, path: bytes(b)}, trusted_keys=pinned)
    except Exception:  # noqa: BLE001 - counting these is the point
        return "exception"
    if rep["result"] != "INVALID":
        return "accepted"
    return "fallback" if any(str(e).startswith("malformed package") for e in rep["errors"]) else "rejected"


def main() -> None:
    out_path = sys.argv[1]
    dump = Path(sys.argv[sys.argv.index("--dump") + 1]) if "--dump" in sys.argv else None
    pinned = h.keys()
    rows = []
    for n, (name, block_format, blob) in enumerate(scenarios()):
        files = dict(sorted(read_zip(blob).items()))
        base = verify_package(files, trusted_keys=pinned)
        assert base["result"] == "VALID", (name, base["errors"])
        times = []
        for _ in range(300):
            t = time.perf_counter()
            verify_package(files, trusted_keys=pinned)
            times.append((time.perf_counter() - t) * 1e3)
        row = {"package": name, "signature_blocks": block_format, "decision": base["decision"]["replayed"],
               "files": len(files), "bytes": sum(map(len, files.values())), "trials": 0,
               "outcomes": {k: 0 for k in CODE}, "by_file_class": {}, "verify_ms_median": round(statistics.median(times), 3)}
        verdicts = []
        for path, data in files.items():
            cls = row["by_file_class"].setdefault(file_class(path), {k: 0 for k in CODE} | {"bytes": 0})
            cls["bytes"] += len(data)
            per_byte = []
            for bit in range(len(data) * 8):
                o = trial(files, path, data, bit, pinned)
                row["trials"] += 1
                row["outcomes"][o] += 1
                cls[o] += 1
                verdicts.append(CODE[o])
                per_byte.append(CODE[o])
            if path == "manifest.sig.json":  # the one file no hash protects: keep the outcome of every bit
                row["manifest_sig"] = {"text": data.decode(), "outcome_per_bit": "".join(per_byte)}
        string = "".join(verdicts)
        row["verdicts_sha256"] = hashlib.sha256(string.encode()).hexdigest()
        rows.append(row)
        print(json.dumps({k: row[k] for k in ("package", "signature_blocks", "trials", "outcomes", "verify_ms_median")}),
              flush=True)
        if dump is not None:
            dump.mkdir(parents=True, exist_ok=True)
            (dump / f"case{n}.json").write_text(json.dumps({
                "package": name, "signature_blocks": block_format, "keys": pinned,
                "files": {p: base64.b64encode(d).decode() for p, d in files.items()}, "verdicts": string}))
    Path(out_path).write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
