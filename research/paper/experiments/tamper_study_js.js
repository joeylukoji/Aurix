// Replication of tamper_study.py with the browser verifier (web/verify.js) under Node.
// Reads the cases written by `tamper_study.py --dump DIR`, repeats every single-bit flip in the same order and
// compares each verdict with the Python verifier's.
//
//   node research/paper/experiments/tamper_study_js.js DIR OUT.json
const fs = require("fs");
const path = require("path");
const V = require(path.join(__dirname, "..", "..", "..", "web", "verify.js"));

(async () => {
  const [dir, outPath] = process.argv.slice(2);
  const rows = [];
  for (const name of fs.readdirSync(dir).filter((f) => /^case\d+\.json$/.test(f)).sort()) {
    const c = JSON.parse(fs.readFileSync(path.join(dir, name), "utf8"));
    const files = {};
    for (const p of Object.keys(c.files).sort()) files[p] = new Uint8Array(Buffer.from(c.files[p], "base64"));
    const base = await V.verifyPackage(files, c.keys);
    if (base.result !== "VALID") throw new Error(`${name}: the untouched package is not VALID`);
    const counts = { rejected: 0, fallback: 0, exception: 0, accepted: 0 };
    let i = 0, disagreements = 0, firstDisagreement = null;
    const t0 = Date.now();
    for (const p of Object.keys(files)) {
      const data = files[p];
      for (let bit = 0; bit < data.length * 8; bit++, i++) {
        const b = Uint8Array.from(data);
        b[bit >> 3] ^= 1 << (bit & 7);
        let o;
        try {
          const rep = await V.verifyPackage({ ...files, [p]: b }, c.keys);
          o = rep.result !== "INVALID" ? "accepted"
            : rep.errors.some((e) => String(e).startsWith("malformed package")) ? "fallback" : "rejected";
        } catch { o = "exception"; }
        counts[o]++;
        // the two verifiers must agree on accept / refuse; which internal check refused may differ
        const py = c.verdicts[i] === "A";
        if (py !== (o === "accepted")) { disagreements++; firstDisagreement ??= { file: p, bit }; }
      }
    }
    const row = { package: c.package, signature_blocks: c.signature_blocks, trials: i, outcomes: counts,
      disagreements_with_python: disagreements, first_disagreement: firstDisagreement, seconds: Math.round((Date.now() - t0) / 1000) };
    rows.push(row);
    console.log(JSON.stringify(row));
  }
  fs.writeFileSync(outPath, JSON.stringify(rows, null, 1));
})().catch((e) => { console.error(e); process.exit(1); });
