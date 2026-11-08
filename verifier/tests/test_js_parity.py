"""The browser verifier (web/verify.js) must give the same verdicts as aurix_verify on the same packages."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aurix_verify.package import read_zip, verify_package

from helpers import att, keys, package, signed
from test_verify import rewrite

NODE = shutil.which("node")
JS = Path(__file__).resolve().parents[2] / "web" / "verify.js"
RUNNER = """
const fs = require('fs'); const V = require(process.argv[1]);
(async () => {
  const out = {};
  for (const f of process.argv.slice(3)) {
    const files = await V.readZip(fs.readFileSync(f));
    const keys = process.argv[2] === '-' ? null : JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
    out[f] = await V.verifyPackage(files, keys);
  }
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def cases():
    kd_rev = keys(revoked_carrier_at="2026-11-09T00:00:00Z")
    late = [signed(att("payment.authorized")), signed(att("shipment.delivered", when="2026-11-15T20:00:00Z"))]
    wrong_amount = [signed(att("payment.authorized", amount="12.00")),
                    signed(att("shipment.delivered", when="2026-11-10T08:00:00Z"))]
    return {
        "valid": (package(), keys()),
        "self_declared_keys": (package(), None),
        "tampered_byte": (rewrite(package(), "attestations/att_payment_authorized_1.json",
                                  lambda b: b.replace(b'"120.00"', b'"920.00"')), keys()),
        "falsified_decision": (package(decision_override="VOID"), keys()),
        "revoked_carrier": (package(key_doc=kd_rev), kd_rev),
        "late_receipt_void": (package(items=late, decided_at="2026-11-16T00:00:01Z",
                                      received={"att_shipment_delivered_1": "2026-11-16T03:00:00Z"}), keys()),
        "amount_mismatch": (package(items=wrong_amount), keys()),
    }


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_browser_verifier_matches_python(tmp_path):
    results = {}
    for name, (data, kd) in cases().items():
        p = tmp_path / f"{name}.zip"
        p.write_bytes(data)
        if kd is not None:
            kf_case = tmp_path / f"{name}.keys.json"
            kf_case.write_text(json.dumps(kd))
        else:
            kf_case = "-"
        out = subprocess.run([NODE, "-e", RUNNER, str(JS), str(kf_case), str(p)], capture_output=True, text=True,
                             check=True)
        js = json.loads(out.stdout)[str(p)]
        py = verify_package(read_zip(data), kd)
        for k in ("result", "integrity", "signatures", "source_verification", "consistency", "trust_source"):
            assert js[k] == py[k], (name, k, js[k], py[k], js["errors"], py["errors"])
        assert (js["decision"] or {}).get("replayed") == (py["decision"] or {}).get("replayed"), name
        assert (js["decision"] or {}).get("matches") == (py["decision"] or {}).get("matches"), name
        assert sorted(a["code"] for a in js["anomalies"]) == sorted(a["code"] for a in py["anomalies"]), name
        assert js["limitations"] == py["limitations"], name
        results[name] = js["result"]
    assert results["valid"] == "VALID" and results["late_receipt_void"] == "VALID"
    assert results["tampered_byte"] == results["falsified_decision"] == results["revoked_carrier"] == "INVALID"
