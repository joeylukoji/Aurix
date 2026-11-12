"""Cost of verifying a proof package as the number of attestations grows, for both verifiers.

    PYTHONPATH=verifier:verifier/tests python research/paper/experiments/verify_cost.py OUT.json
"""
import base64
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import helpers as h
from aurix_verify.package import read_zip, verify_package

JS = r"""
const fs = require('fs'); const V = require(process.argv[1]);
(async () => {
  const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8')); const out = [];
  for (const c of cases) {
    const files = {}; for (const p of Object.keys(c.files)) files[p] = new Uint8Array(Buffer.from(c.files[p], 'base64'));
    for (let i = 0; i < 50; i++) await V.verifyPackage(files, c.keys);
    const t = [];
    for (let i = 0; i < 300; i++) { const s = performance.now(); const r = await V.verifyPackage(files, c.keys); t.push(performance.now() - s); if (r.result !== 'VALID') throw new Error('not valid'); }
    t.sort((a, b) => a - b); out.push(t[150]);
  }
  console.log(JSON.stringify(out));
})();
"""


def main() -> None:
    pinned, rows, cases = h.keys(), [], []
    for m in (1, 2, 4, 8, 16, 32, 64):
        items = [h.signed(h.att("payment.authorized"))] + [
            h.signed(h.att("shipment.delivered", when="2026-11-10T08:00:00Z", n=i + 1)) for i in range(m)]
        files = read_zip(h.package(items=items))
        assert verify_package(files, pinned)["result"] == "VALID"
        times = []
        for _ in range(400):
            t = time.perf_counter()
            verify_package(files, pinned)
            times.append((time.perf_counter() - t) * 1e3)
        rows.append({"attestations": m + 1, "signatures": m + 5, "bytes": sum(map(len, files.values())),
                     "python_ms": round(statistics.median(times), 3)})
        cases.append({"keys": pinned, "files": {p: base64.b64encode(d).decode() for p, d in files.items()}})
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "cases.json"
        f.write_text(json.dumps(cases))
        js = Path(__file__).resolve().parents[3] / "web" / "verify.js"
        out = subprocess.run(["node", "-e", JS, str(js), str(f)], capture_output=True, text=True, check=True)
    for row, ms in zip(rows, json.loads(out.stdout)):
        row["javascript_ms"] = round(ms, 3)
    Path(sys.argv[1]).write_text(json.dumps(rows, indent=1))
    for row in rows:
        print(row)


if __name__ == "__main__":
    main()
