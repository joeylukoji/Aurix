"""Single-bit tamper experiment on AURIX proof packages (exhaustive over every bit of every file).

Run from the repository root:  PYTHONPATH=verifier:verifier/tests python3 research/paper/tamper_experiment.py
"""
import json
import statistics
import sys
import time

import helpers as h
from aurix_verify.package import read_zip, verify_package


def scenarios():
    yield "delivery-attested/1, RELEASE", h.package()
    yield "conditions/1 (all of 2), RELEASE", h.package(
        t=h.cond_terms(),
        items=[h.signed(h.att("payment.authorized")),
               h.signed(h.att("shipment.delivered", when="2026-11-10T08:00:00Z")),
               h.signed(h.att("inspection.passed", when="2026-11-10T08:30:00Z"))])
    yield "delivery-attested/1, VOID", h.package(
        items=[h.signed(h.att("payment.authorized"))], decided_at="2026-11-16T00:00:01Z")


def main():
    pinned = h.keys()
    out = []
    for name, blob in scenarios():
        files = read_zip(blob)
        base = verify_package(files, trusted_keys=pinned)
        assert base["result"] == "VALID", (name, base["errors"])
        times = []
        for _ in range(200):
            t = time.perf_counter()
            verify_package(files, trusted_keys=pinned)
            times.append((time.perf_counter() - t) * 1e3)
        row = {"scenario": name, "decision": base["decision"]["replayed"] if base["decision"] else None,
               "files": len(files), "bytes": sum(map(len, files.values())), "bits": 0, "detected": 0,
               "verify_ms_median": round(statistics.median(times), 3), "undetected": {}, "crashed": {}}
        for path, data in files.items():
            for i in range(len(data) * 8):
                b = bytearray(data)
                b[i // 8] ^= 1 << (i % 8)
                row["bits"] += 1
                try:
                    rep = verify_package({**files, path: bytes(b)}, trusted_keys=pinned)
                except Exception as exc:  # rejected, but by a crash instead of a report
                    key = type(exc).__name__
                    row["crashed"][key] = row["crashed"].get(key, 0) + 1
                    continue
                if rep["result"] == "INVALID":
                    row["detected"] += 1
                else:
                    row["undetected"][path] = row["undetected"].get(path, 0) + 1
        out.append(row)
        print(json.dumps(row), flush=True)
    json.dump(out, open(sys.argv[1], "w"), indent=1) if len(sys.argv) > 1 else None


if __name__ == "__main__":
    main()
