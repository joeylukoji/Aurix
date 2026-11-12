// Evaluates the cases written by rule_differential.py with the JavaScript port of the decision function
// and prints [decision, cited attestation ids, reasons] for each, as JSON. AURIX_VERIFY_JS selects another
// revision of web/verify.js.
const fs = require("fs");
const path = require("path");
const V = require(process.env.AURIX_VERIFY_JS || path.join(__dirname, "..", "..", "..", "web", "verify.js"));
const cases = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const out = cases.map((c) => {
  const r = V.decide(c.terms, c.evidence, c.now);
  return [r.decision, r.inputs.map((a) => a.attestation_id), r.reasons];
});
process.stdout.write(JSON.stringify(out));
