"""Usage: python -m aurix_verify proof.zip [--keys public-keys.json] [--json]

Exit code 0 if the package is VALID, 1 if INVALID, 2 on usage or read errors. Works offline."""
from __future__ import annotations

import argparse
import json
import sys

from .package import read_zip, verify_package


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="aurix-verify", description="Verify an AURIX proof package offline.")
    ap.add_argument("package")
    ap.add_argument("--keys", help="issuer's published key registry (public-keys.json) to trust instead of "
                                   "the keys inside the package")
    ap.add_argument("--json", action="store_true", help="print the full JSON report")
    a = ap.parse_args(argv)
    try:
        with open(a.package, "rb") as f:
            files = read_zip(f.read())
        keys = json.load(open(a.keys)) if a.keys else None
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    r = verify_package(files, keys)
    if a.json:
        print(json.dumps(r, indent=2, ensure_ascii=False))
    else:
        print(f"AURIX proof package — order {r['order_id']}: {r['result']}")
        for k in ("integrity", "signatures", "source_verification", "consistency", "trust_source"):
            print(f"  {k:<20} {r[k]}")
        if r["decision"]:
            d = r["decision"]
            print(f"  {'decision':<20} recorded {d['recorded']}, replayed {d['replayed']} "
                  f"({'match' if d['matches'] else 'MISMATCH'})")
        for s in r["sources"]:
            print(f"  source {s['provider']:<22} {s['type']:<10} {s['status']}")
        for e in r["errors"]:
            print(f"  ! {e}")
        for x in r["anomalies"]:
            print(f"  ~ anomaly class {x['class']}: {x['code']} ({x['detail']})")
        print("  Limitations:")
        for l in r["limitations"]:
            print(f"   - {l}")
    return 0 if r["result"] == "VALID" else 1


if __name__ == "__main__":
    sys.exit(main())
