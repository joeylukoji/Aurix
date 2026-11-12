// AURIX proof package verifier for the browser (and Node 20+): a port of verifier/aurix_verify.
// Runs entirely locally: no upload, no network. Checks integrity (signed manifest), Ed25519 signatures over
// SHA-256 of RFC 8785 canonical JSON, sources (real vs simulated), consistency, and replays the decision
// rules "delivery-attested/1" and "conditions/1". Kept in lockstep with the Python verifier by verifier/tests/test_js_parity.py.
(function (root) {
  "use strict";
  const SCHEMA = "aurix.proof-package";
  const RULE_ID = "delivery-attested/1", RULE_CONDITIONS = "conditions/1";
  const VALID = "valid", INVALID = "invalid", UNKNOWN_KEY = "unknown_key", REVOKED_KEY = "revoked_key";
  const VALID_KEY_REVOKED_LATER = "valid_but_key_revoked_after_signing";
  const LIMITATIONS_ALWAYS = [
    "Cryptographic validity proves who signed what and that nothing changed; it does not prove that the " +
    "goods were physically delivered.",
    "The funds were held and moved by PayPal, never by AURIX.",
  ];
  const te = new TextEncoder(), td = new TextDecoder();
  const subtle = (root.crypto || require("crypto").webcrypto).subtle;

  // ---------- RFC 8785 (subset: no floats, like the Python side) ----------
  function canon(v) {
    if (v === null || typeof v === "boolean") return JSON.stringify(v);
    if (typeof v === "number") {
      if (!Number.isInteger(v)) throw new TypeError("floats are not allowed in AURIX evidence");
      if (!Number.isSafeInteger(v)) throw new RangeError("integer outside the safe range");
      return String(v);
    }
    if (typeof v === "string") return JSON.stringify(v);
    if (Array.isArray(v)) return "[" + v.map(canon).join(",") + "]";
    if (typeof v === "object") {
      return "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + canon(v[k])).join(",") + "}";
    }
    throw new TypeError("unsupported type");
  }
  const hexOf = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
  async function sha256Hex(bytes) { return hexOf(await subtle.digest("SHA-256", bytes)); }
  async function digest(obj) { return "sha256:" + await sha256Hex(te.encode(canon(obj))); }
  // Strict base64: one accepted spelling per byte string (alphabet, padding, zero trailing bits).
  const unb64 = (s) => {
    if (typeof s !== "string") throw new Error("base64 value must be a string");
    const bin = atob(s);
    if (btoa(bin) !== s) throw new Error("non-canonical base64");
    return Uint8Array.from(bin, (c) => c.charCodeAt(0));
  };
  const t = (s) => (typeof s === "string" && s ? Date.parse(s) : NaN);
  // A signing time in exactly YYYY-MM-DDTHH:MM:SSZ (a real date), or NaN: the same strings the Python verifier accepts.
  const strictT = (s) => {
    if (typeof s !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(s)) return NaN;
    const ms = Date.parse(s);
    return !Number.isNaN(ms) && new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z") === s ? ms : NaN;
  };
  const isObj = (x) => x !== null && typeof x === "object" && !Array.isArray(x);
  // Bytes a signature block's signature covers. Version 2: the whole block except `signature` (canonical form),
  // so the signing time and key id are authenticated. No version: the earlier format, the hash only.
  const signedMessage = (sig) => {
    if (sig.version === undefined) return te.encode(sig.signed_hash);
    if (sig.version !== 2) throw new Error("unknown signature block version");
    const body = {};
    for (const k of Object.keys(sig)) if (k !== "signature") body[k] = sig[k];
    return te.encode(canon(body));
  };

  // ---------- zip (stored or deflate) ----------
  // Decompresses with a hard cap: the sizes declared in the zip are attacker-controlled (zip bomb).
  async function inflateRaw(data, limit) {
    const reader = new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate-raw")).getReader();
    const chunks = []; let n = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      n += value.length;
      if (n > limit) { await reader.cancel(); throw new Error("package too large (decompressed size exceeds its declared size or the limit)"); }
      chunks.push(value);
    }
    const out = new Uint8Array(n); let o = 0;
    for (const c of chunks) { out.set(c, o); o += c.length; }
    return out;
  }
  async function readZip(buf, maxFiles = 500, maxBytes = 5_000_000) {
    const u8 = new Uint8Array(buf), dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
    let eocd = -1;
    for (let i = u8.length - 22; i >= Math.max(0, u8.length - 65557); i--) if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
    if (eocd < 0) throw new Error("not a zip file");
    const n = dv.getUint16(eocd + 10, true);
    if (n > maxFiles) throw new Error("too many files in package");
    let p = dv.getUint32(eocd + 16, true), total = 0;
    const files = {};
    for (let i = 0; i < n; i++) {
      if (dv.getUint32(p, true) !== 0x02014b50) throw new Error("corrupt zip directory");
      const method = dv.getUint16(p + 10, true), csize = dv.getUint32(p + 20, true), size = dv.getUint32(p + 24, true);
      const nl = dv.getUint16(p + 28, true), xl = dv.getUint16(p + 30, true), cl = dv.getUint16(p + 32, true);
      const off = dv.getUint32(p + 42, true);
      const name = td.decode(u8.subarray(p + 46, p + 46 + nl));
      p += 46 + nl + xl + cl;
      if (name.endsWith("/")) continue;
      if (name.startsWith("/") || name.split("/").includes("..")) throw new Error("unsafe path in package: " + name);
      total += size;
      if (total > maxBytes) throw new Error("package too large");
      const lnl = dv.getUint16(off + 26, true), lxl = dv.getUint16(off + 28, true);
      const data = u8.subarray(off + 30 + lnl + lxl, off + 30 + lnl + lxl + csize);
      let content;
      if (method === 0) content = data.slice();
      else if (method === 8) content = await inflateRaw(data, Math.min(size, maxBytes));
      else throw new Error("unsupported zip compression for " + name);
      if (content.length !== size) throw new Error("zip entry size mismatch: " + name);
      files[name] = content;
    }
    return files;
  }

  // ---------- keys ----------
  class KeyRegistry {
    constructor(doc) { // a registry read from a package is untrusted input: malformed entries are dropped
      const entries = isObj(doc) && Array.isArray(doc.keys) ? doc.keys : [];
      this.keys = new Map(entries.filter((k) => isObj(k) && typeof k.key_id === "string").map((k) => [k.key_id, k]));
    }
    get(id) { return typeof id === "string" ? this.keys.get(id) : undefined; }
    role(id) { const r = this.get(id)?.role; return typeof r === "string" ? r : null; }
    // A validity bound: null when absent, NaN when present but not a time (the key is then unusable).
    static bound(v) { return v === null || v === undefined || v === "" ? null : t(v); }
    validAt(id, when) {
      const k = this.get(id); if (!k) return false;
      const [start, end, revoked] = [k.valid_from, k.valid_until, k.revoked_at].map(KeyRegistry.bound);
      if ([start, end, revoked].some(Number.isNaN)) return false;
      return !((start !== null && when < start) || (end !== null && when > end) || (revoked !== null && when >= revoked));
    }
    async verify(obj, sig) { // never throws on a malformed block: the verdict is then "invalid"
      if (!isObj(sig)) return INVALID;
      const k = this.get(sig.key_id);
      if (!k || k.alg !== "Ed25519" || sig.alg !== "Ed25519") return UNKNOWN_KEY;
      try {
        if (sig.signed_hash !== await digest(obj)) return INVALID;
        const key = await subtle.importKey("raw", unb64(k.public_key), { name: "Ed25519" }, false, ["verify"]);
        if (!await subtle.verify({ name: "Ed25519" }, key, unb64(sig.signature), signedMessage(sig))) return INVALID;
      } catch (e) {
        if (e && /not supported|Unrecognized|NotSupported/i.test(String(e.name) + String(e.message))) throw new Error("ED25519_UNSUPPORTED");
        return INVALID;
      }
      const [start, end, revoked] = [k.valid_from, k.valid_until, k.revoked_at].map(KeyRegistry.bound);
      if ([start, end, revoked].some(Number.isNaN)) return INVALID;
      const signed = strictT(sig.created_at);
      if (Number.isNaN(signed) || (start !== null && signed < start) || (end !== null && signed > end)) return INVALID;
      if (revoked !== null) return signed < revoked ? VALID_KEY_REVOKED_LATER : REVOKED_KEY;
      return VALID;
    }
  }

  // ---------- rules delivery-attested/1 and conditions/1 (port of aurix_verify/rule.py) ----------
  function money(m) {
    if (!m || m.value == null || m.currency == null) return null;
    const s = String(m.value).trim();
    if (!/^[+-]?(\d+(\.\d*)?|\.\d+)$/.test(s)) return null;
    let [i, f = ""] = s.replace(/^\+/, "").split(".");
    const neg = i.startsWith("-"); i = i.replace("-", "").replace(/^0+(?=\d)/, "") || "0"; f = f.replace(/0+$/, "");
    const v = (neg && (i !== "0" || f) ? "-" : "") + i + (f ? "." + f : "");
    return v + " " + String(m.currency).toUpperCase();
  }
  const refs = (a) => a?.source?.provider_reference || {};
  const type = (e) => e.attestation?.event?.type || "";
  const iso = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
  function decide(terms, evidence, now) {
    // Canonical order (reception time, then attestation id; no reception time goes last), so that the outcome and
    // the evidence it cites do not depend on the order in which the caller holds the evidence.
    const key = (e) => { const r = e.received_at ? t(e.received_at) : NaN; return [Number.isNaN(r) ? 1 : 0, Number.isNaN(r) ? 0 : r, String(e.attestation?.attestation_id ?? "")]; };
    evidence = evidence.map((e) => [key(e), e]).sort(([a], [b]) => a[0] - b[0] || a[1] - b[1] || (a[2] < b[2] ? -1 : a[2] > b[2] ? 1 : 0)).map(([, e]) => e);
    return terms.rule === RULE_CONDITIONS ? decideConditions(terms, evidence, now) : decideDelivery(terms, evidence, now);
  }
  function checks(terms, reasons) {
    const orderId = terms.order_id, deadline = t(terms.deadline);
    const trusted = (e, role) => {
      if (e.verdict !== VALID) { reasons.push(`${type(e) || "attestation"} ignored: signature ${e.verdict}`); return false; }
      if (e.role !== role) { reasons.push(`${type(e)} ignored: signed by a ${e.role || "unregistered"} key, ${role} required`); return false; }
      const ref = refs(e.attestation).order_id;
      if (e.attestation.operation_id !== orderId || (ref != null && ref !== orderId)) { reasons.push(`${type(e)} ignored: belongs to another order`); return false; }
      return true;
    };
    const receivedInTime = (e) => {
      const rec = e.received_at ? t(e.received_at) : NaN;
      if (Number.isNaN(rec) || rec > deadline) { reasons.push(`${type(e)} ignored: received by AURIX after the deadline`); return false; }
      return true;
    };
    return [trusted, receivedInTime];
  }
  function arbiterOutcome(terms, evidence, at, trusted, receivedInTime, reasons) {
    const deadline = t(terms.deadline), arbiter = terms.arbiter_key_id;
    const arb = evidence.filter((e) => type(e) === "arbiter.decision" && arbiter && e.key_id === arbiter && trusted(e, "arbiter")
      && ["HOLD", "UNHOLD", "RELEASE", "VOID"].includes(e.attestation.event.action) && t(e.attestation.timestamps.attested_at) <= Math.min(at, deadline)
      && receivedInTime(e))
      .sort((x, y) => t(x.attestation.timestamps.attested_at) - t(y.attestation.timestamps.attested_at));
    if (arb.length) {
      const last = arb[arb.length - 1], action = last.attestation.event.action;
      if (action === "RELEASE" || action === "VOID") return [{ decision: action, reasons: [...reasons, `arbiter decided ${action}`], inputs: [last.attestation] }, arb];
      if (action === "HOLD") {
        if (at > deadline) return [{ decision: "VOID", reasons: [...reasons, "deadline passed with the arbiter's hold still in place"], inputs: [last.attestation] }, arb];
        return [{ decision: "HOLD", reasons: [...reasons, "arbiter hold in place"], inputs: [last.attestation] }, arb];
      }
    }
    return [null, arb];
  }
  function authorized(terms, evidence, trusted, reasons) {
    const expected = money(terms.amount);
    const auth = evidence.filter((e) => type(e) === "payment.authorized" && trusted(e, "aurix"));
    const authOk = auth.filter((e) => money(e.attestation.event.amount) === expected);
    if (auth.length && !authOk.length) reasons.push("payment authorised for a different amount or currency");
    return authOk;
  }
  function decideDelivery(terms, evidence, now) {
    const deadline = t(terms.deadline), at = t(now), carrier = terms.carrier;
    const reasons = [];
    const [trusted, receivedInTime] = checks(terms, reasons);
    const [out] = arbiterOutcome(terms, evidence, at, trusted, receivedInTime, reasons);
    if (out) return out;
    const authOk = authorized(terms, evidence, trusted, reasons);
    const deliveries = [];
    for (const e of evidence) {
      if (type(e) !== "shipment.delivered" || !trusted(e, `source:${carrier}`)) continue;
      if (e.attestation.source?.provider !== carrier) { reasons.push("delivery ignored: not from the agreed carrier"); continue; }
      const st = t(e.attestation.timestamps?.source_event_time);
      if (Number.isNaN(st) || st > deadline) { reasons.push("delivery ignored: recorded after the deadline"); continue; }
      if (st > at) continue;
      if (!receivedInTime(e)) continue;
      deliveries.push(e);
    }
    if (authOk.length && deliveries.length) return { decision: "RELEASE", reasons: [...reasons, "payment authorised and delivery attested before the deadline"], inputs: [authOk[0].attestation, deliveries[0].attestation] };
    if (at > deadline) return { decision: "VOID", reasons: [...reasons, "deadline passed without the required evidence"], inputs: authOk.slice(0, 1).map((e) => e.attestation) };
    if (!authOk.length) reasons.push("waiting for the payment authorisation");
    if (!deliveries.length) reasons.push("waiting for the delivery attestation");
    return { decision: "WAIT", reasons, inputs: [] };
  }
  function decideConditions(terms, evidence, now) {
    const deadline = t(terms.deadline), at = t(now);
    const reasons = [];
    const [trusted, receivedInTime] = checks(terms, reasons);
    const [out, arb] = arbiterOutcome(terms, evidence, at, trusted, receivedInTime, reasons);
    if (out) return out;
    const authOk = authorized(terms, evidence, trusted, reasons);
    const rec = (e) => t(e.received_at);
    const leaf = (node) => {
      const src = node.source, before = node.before ? t(node.before) : NaN;
      const limit = Number.isNaN(before) ? deadline : Math.min(before, deadline);
      const found = [];
      for (const e of evidence) {
        if (type(e) !== node.evidence || !trusted(e, `source:${src}`)) continue;
        if (e.attestation.source?.provider !== src) { reasons.push(`${type(e)} ignored: not from ${src}`); continue; }
        const st = t(e.attestation.timestamps?.source_event_time);
        if (Number.isNaN(st) || st > limit) { reasons.push(`${type(e)} from ${src} ignored: recorded after its time limit`); continue; }
        if (st > at || !receivedInTime(e)) continue;
        found.push([st, e.attestation.attestation_id || "", e]);
      }
      found.sort((x, y) => x[0] - y[0] || (x[1] < y[1] ? -1 : x[1] > y[1] ? 1 : 0));
      return found.length ? found[0][2] : null;
    };
    const missing = [];
    const met = (node) => {
      if ("evidence" in node) {
        const e = leaf(node);
        if (!e) missing.push(`${node.evidence} from ${node.source}`);
        return e ? [e] : null;
      }
      const children = "all" in node ? node.all : "any" in node ? node.any : node.of;
      const ok = children.map(met).filter((p) => p !== null);
      const need = "all" in node ? children.length : "any" in node ? 1 : Number(node.at_least);
      return ok.length >= need ? ok.slice(0, need).flat() : null;
    };
    const used = met(terms.condition);
    const unholds = arb.filter((e) => e.attestation.event.action === "UNHOLD").map((e) => t(e.attestation.timestamps.attested_at));
    const lifted = unholds.length ? Math.max(...unholds) : null;
    const disputes = evidence.filter((e) => type(e) === "buyer.disputed" && trusted(e, "aurix") && receivedInTime(e)
      && (lifted === null || rec(e) > lifted));
    if (authOk.length && used) {
      const metAt = Math.max(...[authOk[0], ...used].map(rec));
      const releaseAt = Math.min(metAt + Math.trunc(Number(terms.dispute_window_hours || 0)) * 3600e3, deadline);
      const open = disputes.filter((e) => rec(e) <= releaseAt);
      if (open.length) {
        if (at > deadline) return { decision: "VOID", reasons: [...reasons, "deadline passed with the buyer's dispute unresolved"], inputs: [open[0].attestation] };
        return { decision: "HOLD", reasons: [...reasons, "buyer disputed: waiting for the arbiter (refund at the deadline)"], inputs: [open[0].attestation] };
      }
      if (at >= releaseAt) return { decision: "RELEASE", reasons: [...reasons, "payment authorised and the condition was met" + (releaseAt > metAt ? ", no dispute in the window" : "")],
        inputs: [authOk[0].attestation, ...used.map((e) => e.attestation)] };
      return { decision: "WAIT", reasons: [...reasons, `condition met; buyer dispute window open until ${iso(releaseAt)}`], inputs: [] };
    }
    if (at > deadline) return { decision: "VOID", reasons: [...reasons, "deadline passed without the required evidence"], inputs: authOk.slice(0, 1).map((e) => e.attestation) };
    if (!authOk.length) reasons.push("waiting for the payment authorisation");
    for (const m of missing) reasons.push(`waiting for ${m}`);
    return { decision: "WAIT", reasons, inputs: [] };
  }

  // ---------- package (port of aurix_verify/package.py verify_package) ----------
  // Never rejects on a bad package: problems become report entries, and a package malformed in a way no check
  // anticipated is reported INVALID (the verifier fails closed). Only a missing Ed25519 implementation throws.
  async function verifyPackage(files, trustedKeys = null) {
    try {
      return await verifyPackageInner(files, trustedKeys);
    } catch (e) {
      if (e && e.message === "ED25519_UNSUPPORTED") throw e;
      return report(null, "invalid", "invalid", "unverified", "incomplete", [], null,
        [`malformed package (${(e && e.name) || "Error"})`], [], trustedKeys ? "external" : "package (self-declared)");
    }
  }
  async function verifyPackageInner(files, trustedKeys) {
    const errors = [], anomalies = [];
    const load = (p) => { // a file's JSON object, or null (with a report entry)
      if (!Object.prototype.hasOwnProperty.call(files, p)) { errors.push(`missing file: ${p}`); return null; }
      let doc;
      try { doc = JSON.parse(td.decode(files[p])); } catch { errors.push(`not valid JSON: ${p}`); return null; }
      if (!isObj(doc)) { errors.push(`not a JSON object: ${p}`); return null; }
      return doc;
    };
    const manifest = load("manifest.json");
    if (!manifest || manifest.schema !== SCHEMA)
      return report(null, "invalid", "invalid", "unverified", "incomplete", [], null, [...errors, "not an AURIX proof package"], [], "none");
    const orderId = manifest.order_id;
    let integrityOk = true, listed = manifest.files;
    if (!isObj(listed)) { errors.push("manifest.json: no file list"); listed = {}; integrityOk = false; }
    for (const [p, h] of Object.entries(listed)) {
      if (!(p in files)) { errors.push(`listed file missing: ${p}`); integrityOk = false; }
      else if ("sha256:" + await sha256Hex(files[p]) !== h) { errors.push(`file modified: ${p}`); integrityOk = false; }
    }
    const extra = Object.keys(files).filter((p) => !(p in listed) && p !== "manifest.json" && p !== "manifest.sig.json").sort();
    if (extra.length) { errors.push(`unlisted files: ${extra.join(", ")}`); integrityOk = false; }
    const trustSource = trustedKeys ? "external" : "package (self-declared)";
    const reg = new KeyRegistry(trustedKeys || load("trust/public-keys.json") || {});
    const meta = load("trust/trust-metadata.json") || {};
    const verdicts = [];
    const check = async (obj, sigPath, role, signedAt = null) => {
      const sig = load(sigPath);
      if (obj == null || sig == null) { verdicts.push("missing"); return "missing"; }
      let v = await reg.verify(obj, sig);
      if (v === VALID && reg.role(sig.key_id) !== role) { errors.push(`${sigPath}: signed by a ${reg.role(sig.key_id)} key, ${role} required`); v = INVALID; }
      if (v === VALID && signedAt !== null && sig.created_at !== signedAt) { errors.push(`${sigPath}: signing time differs from the signed document's`); v = INVALID; }
      verdicts.push(v);
      if (v !== VALID) errors.push(`${sigPath}: ${v}`);
      return v;
    };
    // Every other signature file is listed, with its hash, in the manifest. The manifest's own signature file
    // cannot be, so its signing time is tied to the manifest's created_at, which the signature covers.
    await check(manifest, "manifest.sig.json", "aurix", manifest.created_at || "");
    const terms = load("terms.json");
    const termsOk = (await check(terms, "signatures/terms.json", "aurix")) === VALID;

    const evidence = [], sources = {};
    for (const p of Object.keys(listed).filter((x) => x.startsWith("attestations/")).sort()) {
      const att = load(p); if (!att) continue;
      const aid = p.slice("attestations/".length, -".json".length);
      const sig = load(`signatures/${aid}.json`) || {};
      const v = Object.keys(sig).length ? await reg.verify(att, sig) : "missing";
      verdicts.push(v);
      if (v !== VALID) errors.push(`attestation ${aid}: signature ${v}`);
      const keyId = typeof sig.key_id === "string" ? sig.key_id : "";
      evidence.push({ attestation: att, key_id: keyId, role: reg.role(keyId), verdict: v });
      const src = isObj(att.source) ? att.source : {}, prov = typeof src.provider === "string" ? src.provider : "?";
      const s = sources[prov] ||= { provider: prov, type: src.source_type || "unknown", environment: src.environment ?? null,
        verification_method: src.verification_method ?? null, status: "verified" };
      if (v !== VALID || src.verification_status !== "verified") s.status = "unverified";
      const declared = meta.sources?.[prov]?.type;
      if (declared && declared !== s.type) anomalies.push({ class: 2, code: "source_type_mismatch", detail: prov });
    }
    if (terms) {
      const want = `${terms.amount?.value}|${terms.amount?.currency}`;
      for (const e of evidence) {
        const a = e.attestation;
        if (a.operation_id !== orderId) anomalies.push({ class: 2, code: "foreign_order", detail: a.attestation_id });
        const amt = a.event?.amount;
        if (amt && `${amt.value}|${amt.currency}` !== want) anomalies.push({ class: 3, code: "amount_mismatch", detail: a.attestation_id });
      }
      const seen = {};
      for (const e of evidence) { const k = `${e.attestation.source?.provider}:${e.attestation.source?.provider_event_id}`; seen[k] = (seen[k] || 0) + 1; }
      for (const [k, n] of Object.entries(seen)) if (n > 1 && !k.endsWith(":undefined")) anomalies.push({ class: 2, code: "duplicate_event", detail: k });
    }
    const types = new Set(evidence.filter((e) => e.verdict === VALID).map((e) => e.attestation.event?.type));
    if (!types.has("payment.authorized")) anomalies.push({ class: 1, code: "missing_payment_authorization", detail: orderId });
    if (!types.has("shipment.delivered") && !types.has("payment.voided")) anomalies.push({ class: 1, code: "missing_delivery", detail: orderId });

    const receipts = "receipts.json" in listed ? load("receipts.json") : null;
    const receiptsOk = receipts != null && (await check(receipts, "signatures/receipts.json", "aurix")) === VALID;
    const receivedAt = {};
    if (receiptsOk) for (const r of Array.isArray(receipts.receipts) ? receipts.receipts : []) {
      const when = isObj(r) ? t(r.received_at) : NaN;
      if (Number.isNaN(when) || typeof r.attestation_hash !== "string") errors.push("receipts.json: malformed entry");
      else receivedAt[r.attestation_hash] = when;
    }
    const dig = new Map(); // canonical hash, or null when the object has no canonical form (a float, for instance)
    for (const e of evidence) { try { dig.set(e, await digest(e.attestation)); } catch { dig.set(e, null); } }
    for (const e of evidence) {
      const rec = receivedAt[dig.get(e)];
      if (rec === undefined) errors.push(`no AURIX receipt for attestation ${e.attestation.attestation_id}`);
      else if (e.verdict === VALID && !reg.validAt(e.key_id, rec)) {
        errors.push(`attestation ${e.attestation.attestation_id}: key not valid when received`);
        e.verdict = INVALID; verdicts.push(INVALID);
      }
    }
    let replay = null;
    if ("decision.json" in listed) {
      const decision = load("decision.json");
      const decOk = (await check(decision, "signatures/decision.json", "aurix")) === VALID;
      const at = decision && typeof decision.decided_at === "string" ? t(decision.decided_at) : NaN;
      if (decision && Number.isNaN(at)) errors.push("decision.json: decided_at missing or not a valid time");
      if (decision && terms && termsOk && !Number.isNaN(at)) {
        const known = evidence.filter((e) => dig.get(e) !== null && dig.get(e) in receivedAt && receivedAt[dig.get(e)] <= at)
          .map((e) => ({ ...e, received_at: new Date(receivedAt[dig.get(e)]).toISOString().replace(/\.\d{3}Z$/, "Z") }));
        try {
          const out = decide(terms, known, decision.decided_at);
          const got = (await Promise.all(out.inputs.map(digest))).sort();
          const inputs = decision.inputs;
          const same = Array.isArray(inputs) && inputs.every((h) => typeof h === "string") && JSON.stringify(got) === JSON.stringify([...inputs].sort());
          replay = { recorded: decision.decision, replayed: out.decision, matches: decOk && out.decision === decision.decision && same,
            rule: decision.rule, reasons: out.reasons };
        } catch { // signed terms or evidence the rule cannot evaluate: the recorded decision is not reproducible
          replay = { recorded: decision.decision, replayed: null, matches: false, rule: decision.rule,
            reasons: ["the rule cannot be evaluated on these terms and evidence"] };
        }
        if (![RULE_ID, RULE_CONDITIONS].includes(decision.rule) || decision.rule !== (terms.rule || RULE_ID)) { errors.push(`unknown rule ${decision.rule}`); replay.matches = false; }
        if (!replay.matches) errors.push("recorded decision does not match the replayed rule");
      }
    }
    const signatures = verdicts.length && verdicts.every((v) => v === VALID) ? "valid" : !verdicts.some((v) => v === VALID) ? "invalid" : "partial";
    const st = Object.values(sources).map((s) => s.status);
    const sourceVerification = st.length && st.every((s) => s === "verified") ? "verified" : !st.length || st.every((s) => s !== "verified") ? "unverified" : "partial";
    const consistency = anomalies.some((a) => a.class >= 2) ? "inconsistent" : anomalies.length ? "incomplete" : "consistent";
    const limitations = [...LIMITATIONS_ALWAYS];
    for (const s of Object.values(sources)) {
      if (s.type === "simulated") limitations.unshift(`Source '${s.provider}' is simulated (environment '${s.environment}'): it is not a real organisation's system.`);
      if (s.environment === "sandbox") limitations.push(`Source '${s.provider}' runs in a sandbox environment: no real money moved.`);
    }
    const advisories = [];
    for (const p of Object.keys(listed).filter((x) => x.startsWith("advisory/")).sort()) {
      const a = load(p) || {};
      advisories.push({ file: p, model: a.model ?? null, generated_by: a.generated_by ?? null, generated_at: a.generated_at ?? null });
    }
    if (advisories.length) limitations.push("AI advisor texts (advisory/) are included for information only: the decision rule does not use them, and they may contain errors.");
    if (!trustedKeys) limitations.push("Keys were taken from the package itself; pass the issuer's published keys (--keys) to check who the issuer is.");
    const r = report(orderId, integrityOk ? "valid" : "invalid", signatures, sourceVerification, consistency, Object.values(sources), replay, errors, anomalies, trustSource, limitations);
    r.advisories = advisories;
    return r;
  }
  function report(orderId, integrity, signatures, sourceVerification, consistency, sources, replay, errors, anomalies, trustSource, limitations) {
    const ok = integrity === "valid" && signatures === "valid" && (replay == null || replay.matches);
    return { schema: "aurix.verification-report", schema_version: "1.0", order_id: orderId, result: ok ? "VALID" : "INVALID",
      integrity, signatures, source_verification: sourceVerification, consistency, sources, decision: replay, anomalies, errors,
      trust_source: trustSource, limitations: limitations || [...LIMITATIONS_ALWAYS] };
  }

  const api = { canon, digest, readZip, verifyPackage, decide, KeyRegistry, RULE_ID, RULE_CONDITIONS };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.AurixVerify = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
