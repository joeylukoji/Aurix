// AURIX web wallet. Keys live in this browser only; every AUR movement is signed here.
const C = window.AurixChain;
const MICRO = 1_000_000;
const $ = (id) => document.getElementById(id);
const hex = (u8) => [...u8].map((b) => b.toString(16).padStart(2, "0")).join("");
const fmt = (u) => (u / MICRO).toLocaleString(undefined, { maximumFractionDigits: 6 });
const short = (a) => (a && a.length > 16 ? a.slice(0, 10) + "…" + a.slice(-6) : a || "");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

// Gateway API (optional: PayPal and Guardian). Chain reads/writes go through AurixChain (chain.js).
const api = (path, opts) => C.gw(path, opts);

function toast(msg, err = false) {
  const t = $("toast");
  t.textContent = msg; t.className = err ? "err" : ""; t.hidden = false;
  clearTimeout(toast.h); toast.h = setTimeout(() => (t.hidden = true), 5000);
}

async function busy(btn, fn) {
  btn.disabled = true;
  try { await fn(); } catch (e) { toast(e.message, true); } finally { btn.disabled = false; }
}

// ---------- keys ----------
// Real wallets: BIP39 mnemonic (keys.js), stored only encrypted (aurix.vault) and unlocked with a
// password each session. Demo account: dev key sha256(seed), seed kept in clear (aurix.seed), testnet only.
let wallet = null;
const K = window.AurixKeys;
function store(seed) { try { seed ? localStorage.setItem("aurix.seed", seed) : localStorage.removeItem("aurix.seed"); } catch {} }
function stored() { try { return localStorage.getItem("aurix.seed"); } catch { return null; } }

// Which panel of the wallet card is visible: none | backup | unlock | wallet.
function showPanel(name) {
  for (const [id, n] of [["noWallet", "none"], ["backup", "backup"], ["unlock", "unlock"], ["hasWallet", "wallet"]]) $(id).hidden = n !== name;
}
function useKey(key) {
  wallet = key;
  showPanel("wallet");
  $("addr").textContent = wallet.address;
  const demo = wallet.kind === "demo";
  $("walletKind").textContent = demo ? "demo account: dev key, never use on a public network" : `recovery phrase · ${K.PATH}`;
  $("walletKind").className = "pill " + (demo ? "warn" : "ok");
  $("showSeed").hidden = !demo;
  $("lock").hidden = demo;
  renderTrust(null);
  refreshWallet();
}
async function setWallet(seed) { // demo account path (used by the e2e tests)
  store(seed);
  if (seed) useKey(AurixTx.keyFromSeed(seed)); else showStart();
}
function showStart() {
  wallet = null;
  const v = K.loadVault();
  if (v) { $("unlockAddr").textContent = v.address; $("unlockPw").value = ""; showPanel("unlock"); }
  else showPanel("none");
}

let pendingMnemonic = null; // shown once on the backup screen, then dropped
function startBackup(mnemonic) {
  pendingMnemonic = mnemonic;
  $("backupWords").innerHTML = mnemonic.split(" ").map((w) => `<li>${esc(w)}</li>`).join("");
  $("backupAck").checked = false; $("backupPw").value = $("backupPw2").value = "";
  showPanel("backup");
}
async function saveNewWallet(mnemonic, pw, pw2) {
  if (!pw || pw.length < 8) throw new Error("Choose a password of at least 8 characters");
  if (pw !== pw2) throw new Error("The two passwords differ");
  const key = await K.keyFromMnemonic(mnemonic);
  const saved = K.saveVault(await K.encryptVault(mnemonic, pw, key.address));
  store(null);
  useKey(key);
  toast(saved ? "Wallet saved, encrypted with your password" : "Browser storage is blocked: the wallet lives in this tab only — keep your recovery phrase");
}
async function finishBackup() {
  if (!$("backupAck").checked) throw new Error("Confirm you wrote the 12 words down");
  await saveNewWallet(pendingMnemonic, $("backupPw").value, $("backupPw2").value);
  pendingMnemonic = null; $("backupWords").innerHTML = "";
}
async function importMnemonic() {
  const m = K.normalize($("importMnemonic").value);
  if (!K.validateMnemonic(m)) throw new Error("Invalid recovery phrase (unknown word or bad checksum)");
  await saveNewWallet(m, $("importPw").value, $("importPw2").value);
  $("importMnemonic").value = $("importPw").value = $("importPw2").value = "";
}
async function unlock() {
  const v = K.loadVault();
  if (!v) return showStart();
  const key = await K.keyFromMnemonic(await K.decryptVault(v, $("unlockPw").value));
  $("unlockPw").value = "";
  useKey(key);
}

// ---------- cross-verification badge (H4) ----------
function renderTrust(t) {
  const b = $("trustBadge"), w = $("nodeWarn");
  if (!t) { b.hidden = true; w.hidden = true; return; }
  b.hidden = false;
  const lines = t.nodes.map((n) => `${n.rest}: #${n.height}${wallet ? `, ${fmt(n.balance)} AUR, seq ${n.sequence}` : ""}`);
  const label = { agree: `${t.nodes.length} nodes agree`, disagree: "nodes disagree", single: "single-node trust", none: "no node verified" }[t.trust];
  b.textContent = label;
  b.className = "pill " + (t.trust === "agree" ? "ok" : t.trust === "disagree" ? "bad" : "warn");
  const why = t.trust !== "single" ? "" : t.configured < 2 ? "Only one endpoint is configured: add a second independent node in Settings." : "Only one endpoint answered.";
  b.title = [why, ...lines, ...t.issues].filter(Boolean).join("\n");
  w.hidden = t.trust !== "disagree";
  if (t.trust === "disagree") w.innerHTML = `<strong>Nodes disagree.</strong> Do not rely on this balance until they match.<ul>${t.issues.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`;
}
async function crossCheck() {
  if (C.state.viaGateway) return renderTrust({ nodes: [], trust: "single", issues: ["reading through the gateway, not a node"], configured: 0 });
  renderTrust(await C.crossCheck(wallet?.address).catch(() => null));
}
const finalText = (r) => r.finality?.final ? `final: seen by ${r.finality.seen_by} nodes`
  : `seen by ${r.finality?.seen_by ?? 1} node only, not final until a 2nd node sees it`;

// Serialise this wallet's transactions so two clicks never reuse the same sequence.
let txQueue = Promise.resolve();
function signAndSend(msg) {
  const run = txQueue.then(async () => {
    const p = await C.prepare(wallet.address, msg); // account number, sequence, chain id from the chain
    const res = await C.broadcast(AurixTx.signTx(wallet, p));
    // Direct broadcast bypassed the gateway: let it record the payment in Guardian history.
    // The gateway re-reads the tx from the chain (never trusts us) and dedupes by hash.
    if (C.gatewayOk && res && res.hash && (msg.type === "MsgSend" || msg.type === "MsgCreateEscrow"))
      api("/api/txs/observed", { method: "POST", body: { txhash: res.hash } }).catch(() => {});
    // Final for the wallet only once a second node returns the tx (H4).
    res.finality = res.seen_ep ? await C.confirmTx(res.hash, res.seen_ep) : { seen_by: 1, need: 2, final: false };
    return res;
  });
  txQueue = run.catch(() => {});
  return run;
}

// H3: re-verify each Guardian signature in the browser (secp256k1 over sha256 of the exact payload string),
// independently of the gateway's own check. Returns { ok, decision, guardian } per guardian.
function verifyGuardian(g) {
  const att = g && g.attestation;
  if (!att) return { ok: false, guardian: g && g.guardian, why: g && g.error || "no attestation" };
  try {
    const { secp256k1, sha256 } = window.noble;
    const msg = sha256(new TextEncoder().encode(att.payload));
    const ok = secp256k1.verify(att.signature, msg, att.pubkey, { lowS: true });
    const p = JSON.parse(att.payload);
    return { ok, decision: p.decision, score: p.score, guardian: att.guardian, why: ok ? "" : "bad signature" };
  } catch (e) { return { ok: false, guardian: att.guardian, why: String(e.message || e) }; }
}

function quorumHtml(risk) {
  const gs = risk.guardians || [];
  if (!gs.length) return "";
  const checks = gs.map(verifyGuardian);
  const valid = checks.filter((c) => c.ok);
  const agree = valid.filter((c) => c.decision === risk.level).length;
  const q = risk.quorum || {};
  const met = valid.length >= (q.required || 1);
  const rows = gs.map((g, i) => `<li><code>${esc((checks[i].guardian || g.url || "?").slice(0, 16))}…</code> ${checks[i].ok
    ? `signature ✓ · ${esc(checks[i].decision)} ${Math.round(checks[i].score * 100)}%` : `excluded: ${esc(checks[i].why)}`}</li>`).join("");
  return `<p class="quorum ${met ? "ok" : "warn"}"><b>${agree}/${gs.length} guardians agree</b>
    (${valid.length} valid signature${valid.length === 1 ? "" : "s"} verified in your browser, quorum ${q.required || 1}${met ? " met" : " NOT met"})</p>
    <details><summary class="muted">Guardian signatures</summary><ul class="muted">${rows}</ul></details>`;
}

function riskHtml(risk, status) {
  if (!risk) return `<div class="risk unavailable"><div class="score">Analysis unavailable</div>
    <p class="muted">${C.gatewayOk ? "Neural Guardian could not be reached." : "No gateway configured, so Neural Guardian scoring is off."} You can still decide.</p></div>`;
  const reasons = risk.reasons.map((r) => `<li>${esc(r.text)} <span class="muted">(+${r.impact})</span></li>`).join("");
  return `<div class="risk ${risk.level}"><div class="score">Risk ${Math.round(risk.risk_score * 100)}% · ${risk.level}</div>
    ${quorumHtml(risk)}
    ${reasons ? `<ul>${reasons}</ul>` : `<p class="muted">No risk factor stands out.</p>`}
    <p class="muted">Advisory only (model ${esc(risk.model_version)}). The protocol decides, you approve.</p></div>`;
}

// ---------- grids ----------
const grids = {};
function grid(id, columnDefs) {
  grids[id] = agGrid.createGrid($(id), { columnDefs, rowData: [], defaultColDef: { sortable: true, filter: true, resizable: true, flex: 1, minWidth: 110 },
    pagination: true, paginationPageSize: 20, paginationPageSizeSelector: false });
}
const set = (id, rows) => grids[id]?.setGridOption("rowData", rows);
const amountCol = { field: "amount", headerName: "AUR", valueFormatter: (p) => (p.value ? fmt(p.value) : "") };
const statusCol = { field: "code", headerName: "Result", valueFormatter: (p) => (p.value ? "failed: " + p.data.log : "ok") };

// ---------- wallet ----------
async function refreshWallet() {
  if (!wallet) return;
  const addr = wallet.address;
  const w = await C.account(addr);
  if (addr !== wallet?.address) return;
  $("balAur").textContent = fmt(w.balance) + " AUR";
  crossCheck();
  set("historyGrid", await C.history(addr));
}

// ---------- send ----------
async function checkSend() {
  const to = $("sendTo").value.trim(), amt = Math.round(parseFloat($("sendAmt").value) * MICRO);
  if (!wallet) throw new Error("Create a wallet first");
  if (!AurixTx.validAddress(to) || !(amt > 0)) throw new Error("Enter a valid aur1… recipient and amount");
  const msg = { type: "MsgSend", to, amount: amt };
  const p = await C.prepare(wallet.address, msg);
  $("sendRisk").innerHTML = riskHtml(await C.risk(wallet.address, msg, p.balance)) +
    `<button id="sendGo" class="primary">I approve: send ${fmt(amt)} AUR</button><button id="sendCancel">Cancel</button>`;
  $("sendCancel").onclick = () => ($("sendRisk").innerHTML = "");
  $("sendGo").onclick = (e) => busy(e.target, async () => {
    const r = await signAndSend(msg);
    toast(`Confirmed in block ${r.height} (${finalText(r)})`, !r.finality?.final); $("sendRisk").innerHTML = ""; refreshWallet();
  });
}

// ---------- escrow ----------
async function refreshEscrows() {
  const list = await C.escrows();
  set("escrowGrid", list.reverse());
}
async function checkEscrow() {
  if (!wallet) throw new Error("Create a wallet first");
  const to = $("escTo").value.trim(), arb = $("escArb").value.trim();
  const amt = Math.round(parseFloat($("escAmt").value) * MICRO), blocks = parseInt($("escBlocks").value, 10);
  if (!AurixTx.validAddress(to) || (arb && !AurixTx.validAddress(arb)) || !(amt > 0) || !(blocks > 0)) throw new Error("Check addresses, amount and deadline");
  const { height } = await C.status();
  const msg = { type: "MsgCreateEscrow", to, amount: amt, deadline_height: height + blocks, ...(arb ? { arbiter: arb } : {}) };
  const p = await C.prepare(wallet.address, msg);
  $("escRisk").innerHTML = riskHtml(await C.risk(wallet.address, msg, p.balance)) +
    `<button id="escGo" class="primary">I approve: lock ${fmt(amt)} AUR until block ${msg.deadline_height}</button>`;
  $("escGo").onclick = (e) => busy(e.target, async () => {
    const r = await signAndSend(msg);
    toast(`Escrow #${r.escrow_id} locked (${finalText(r)})`, !r.finality?.final); $("escRisk").innerHTML = ""; refreshEscrows(); refreshWallet();
  });
}
async function doEscrowAction(type, id) {
  if (!wallet) throw new Error("Create a wallet first");
  const r = await signAndSend({ type, escrow_id: id });
  toast(`Escrow #${id} ${type === "MsgReleaseEscrow" ? "released" : "refunded"} (block ${r.height}, ${finalText(r)})`, !r.finality?.final);
  refreshEscrows(); refreshWallet();
}
window.escrowAction = (type, id, btn) => busy(btn, () => doEscrowAction(type, id));

// ---------- PayPal ----------
async function refreshOrders() { if (C.gatewayOk) set("ordersGrid", await api("/api/paypal/orders")); }
// Attestation progress {count, threshold, signers}: from the gateway's order, else from the chain proof.
async function attestationsOf(o) {
  if (o.attestations && typeof o.attestations === "object") return o.attestations;
  if (!o.proof_hash) return null;
  try { const p = await C.proof(o.proof_hash); return { count: p.attestations.length, threshold: p.threshold, signers: p.attestations, status: p.status }; }
  catch { return null; }
}
const attText = (a) => a ? `attestations ${a.count}/${a.threshold}` : "";
async function waitAnchored(oid, tries = 120) {
  for (let i = 0; i < tries; i++) {
    const o = await api(`/api/paypal/orders/${oid}`);
    if (["ANCHORED", "DENIED", "REFUNDED", "CANCELLED"].includes(o.status)) return o;  // final states
    const a = await attestationsOf(o);
    const m = $("ppWait");
    if (m) m.textContent = o.status === "ATTESTING" || a
      ? `Captured. Proof waiting for independent attestors: ${attText(a) || "attestations 0/?"}…`
      : "Captured. Waiting for PayPal's signed webhook…";
    await new Promise((r) => setTimeout(r, 500));
  }
  return api(`/api/paypal/orders/${oid}`);
}

async function createOrder() {
  const o = await api("/api/paypal/orders", { method: "POST", body: { amount_usd: $("ppAmt").value,
    description: $("ppDesc").value, payer: wallet?.address } });
  const who = o.paypal_mode === "simulator" ? "PayPal simulator" : "PayPal Sandbox";
  $("ppResult").innerHTML = `<p>Order <code>${esc(o.order_id)}</code> · ${esc(o.amount_usd)} USD</p>` + riskHtml(o.risk, o.risk_status) +
    `<ol class="steps"><li><a id="ppLink" href="${esc(o.approve_url)}" target="_blank" rel="noopener">Open the ${who} page</a> and pay as the buyer.</li>
      <li>Come back and confirm the capture below.</li></ol>
    <button id="ppGo" class="primary">I approve: capture ${esc(o.amount_usd)} USD</button><button id="ppNo">Cancel order</button><div id="ppMsg"></div>`;
  $("ppGo").onclick = (e) => busy(e.target, async () => {
    let r;
    try {
      r = await api(`/api/paypal/orders/${o.order_id}/approve`, { method: "POST", body: { confirm: true } });
    } catch (err) {
      $("ppMsg").innerHTML = `<p class="muted">${esc(err.message)}</p>`;
      return;
    }
    $("ppResult").innerHTML = `<p id="ppWait">Captured. Waiting for PayPal's signed webhook…</p>`;
    r = await waitAnchored(o.order_id);
    const a = await attestationsOf(r);
    $("ppResult").innerHTML = r.status === "ANCHORED"
      ? `<div class="risk low"><div class="score">Captured &amp; anchored</div><p>Proof <code>${esc(r.proof_hash)}</code> in block ${r.anchor_height}${a ? ` · confirmed by ${a.count}/${a.threshold} attestations` : ""}. No AUR was created.</p></div>`
      : r.status === "DENIED"
      ? `<div class="risk high"><div class="score">Payment denied by PayPal</div><p>${esc(r.last_error || "The capture was denied.")} Nothing was anchored on AURIX.</p></div>`
      : r.status === "REFUNDED"
      ? `<div class="risk medium"><div class="score">Payment refunded</div><p>Refund <code>${esc(r.refund_id || "")}</code>. The proof of the original capture stays on AURIX; the refund is recorded by the payments service.</p></div>`
      : `<p>Status: ${esc(r.status)}${a ? " · " + attText(a) : ""} — the proof is not confirmed yet. Refresh the orders list later.</p>`;
    refreshOrders();
  });
  $("ppNo").onclick = (e) => busy(e.target, async () => {
    await api(`/api/paypal/orders/${o.order_id}/cancel`, { method: "POST" }); $("ppResult").innerHTML = ""; refreshOrders();
  });
  refreshOrders();
}

// ---------- Conditional USD payment (evidence-released escrow, MVP 0.2) ----------
let escrowAdvisor = null; // {enabled, model, demo_arbiter} from payments
const CD_FINAL = ["ANCHORED", "VOIDED", "AUTH_FAILED", "DENIED", "CANCELLED"];
const deliveryUrl = () => (C.config?.delivery || "").replace(/\/+$/, "");
async function refreshCond() { if (C.gatewayOk && !escrowAdvisor) escrowAdvisor = await api("/api/escrow/advisor").catch(() => null);
  const demo = document.querySelector('#cdDays option[data-demo]'); if (demo) demo.hidden = !escrowAdvisor?.demo_arbiter;
  if (C.gatewayOk) set("condGrid", (await api("/api/escrow/orders")).map((o) => ({
  order_id: o.order_id, amount_usd: o.amount_usd, status: o.status, deadline: o.terms.deadline,
  decision: o.decision?.decision || o.last_evaluation?.decision || "", evidence: o.timeline.length,
  simulated: o.simulated_sources.join(", ") }))); }

const SIG_LABEL = { valid: "signature valid", invalid: "signature INVALID", unknown_key: "unknown key", revoked_key: "revoked key" };
function timelineHtml(o) {
  if (!o.timeline.length) return `<p class="muted">No evidence yet.</p>`;
  return `<ol class="timeline">${o.timeline.map((t) => `<li><b>${esc(t.type)}</b> · ${esc(t.provider)}
    <span class="pill ${t.source_type === "simulated" ? "warn" : "ok"}">${esc(t.source_type)}</span>
    <span class="pill ${t.signature === "valid" ? "ok" : "warn"}">${esc(SIG_LABEL[t.signature] || t.signature)}</span>
    <br><span class="muted">event ${esc(t.source_event_time)} · received ${esc(t.received_at)} · ${esc(t.verification_method)}</span></li>`).join("")}</ol>`;
}
function condStatusHtml(o) {
  const ev = o.last_evaluation, d = o.decision;
  const head = {
    SCORED: ["unavailable", "Waiting for the buyer's approval on PayPal"],
    AUTHORIZED: ["medium", `Funds held by PayPal · rule says ${esc(ev?.decision || "WAIT")}`],
    RELEASING: ["medium", "Evidence accepted: capturing at PayPal…"], CAPTURED: ["medium", "Captured: waiting for PayPal's signed webhook…"],
    CONFIRMED: ["medium", "Captured: anchoring the proof…"], ATTESTING: ["medium", "Captured: proof waiting for independent attestors…"],
    ANCHORED: ["low", "Released: captured on evidence, proof anchored"], VOIDING: ["medium", "Deadline passed: releasing the funds…"],
    VOIDED: ["low", "Voided: the buyer was not charged"], AUTH_FAILED: ["high", "PayPal refused the operation"],
  }[o.status] || ["unavailable", esc(o.status)];
  const reasons = (d?.reasons || ev?.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("");
  return `<div class="risk ${head[0]}"><div class="score">${head[1]}</div>
    <p>Order <code>${esc(o.order_id)}</code> · ${esc(o.amount_usd)} USD · deadline ${esc(o.terms.deadline)} · rule <code>${esc(o.terms.rule)}</code></p>
    ${reasons ? `<ul>${reasons}</ul>` : ""}${o.last_error ? `<p>${esc(o.last_error)}</p>` : ""}
    ${o.simulated_sources.length ? `<p class="muted">Simulated sources: ${esc(o.simulated_sources.join(", "))}. A valid signature proves who signed, not that a parcel was physically delivered.</p>` : ""}</div>`;
}
async function renderCond(oid) {
  if (!escrowAdvisor) escrowAdvisor = await api("/api/escrow/advisor").catch(() => null);
  const o = await api(`/api/escrow/orders/${oid}`);
  const canDeliver = o.status === "AUTHORIZED" && deliveryUrl();
  // While funds are held, keep watching: the server voids at the deadline (demo: a 2-minute deadline).
  clearTimeout(renderCond.timer);
  if (["AUTHORIZED", "RELEASING", "CAPTURED", "CONFIRMED", "ATTESTING", "VOIDING"].includes(o.status) && $("cond").classList.contains("active"))
    renderCond.timer = setTimeout(() => renderCond(oid).then((n) => n.status !== o.status && refreshCond()).catch(() => {}), 3000);
  const left = Math.max(0, Math.round((new Date(o.terms.deadline) - Date.now()) / 1000));
  const countdown = o.status === "AUTHORIZED" && left < 3600 ? `<p class="pill warn">Deadline in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}</p>` : "";
  $("cdResult").innerHTML = condStatusHtml(o) + countdown + timelineHtml(o) +
    (canDeliver ? `<p class="muted">Demo carrier (simulated, its own signing key):</p>
      <button id="cdDeliver" class="primary">Carrier: report delivery</button><button id="cdForge">Send a forged delivery</button>` : "") +
    (o.status === "AUTHORIZED" && !deliveryUrl() ? `<p class="muted">No demo carrier configured ("delivery" in config.json).</p>` : "") +
    (o.status !== "SCORED" ? `<button id="cdProof">Download proof package</button>
      <p class="muted">Verify it anywhere, offline: <code>python -m aurix_verify aurix-proof-${esc(oid)}.zip</code></p>` : "");
  if (o.status !== "SCORED") {
    const hasArb = !!o.terms.arbiter_key_id, open = !CD_FINAL.includes(o.status) && !o.decision;
    const box = document.createElement("div");
    box.className = "card advisor";
    box.innerHTML = `<h3>AI advisor <span class="pill">${esc(advisorLabel())}</span></h3>
      <p class="muted">Explains and suggests. It never decides: the decision is the public rule, or the arbiter's signature.</p>
      <label>For<select id="cdAud"><option value="buyer">the buyer</option><option value="merchant">the merchant</option><option value="auditor">an auditor</option></select></label>
      <button id="cdExplain">Explain this payment</button>
      ${hasArb ? `<button id="cdBrief">Brief for the arbiter</button>` : ""}
      <div id="cdAdvice"></div>
      ${hasArb && open && escrowAdvisor?.demo_arbiter ? `<h3>Arbiter</h3>
        <p class="muted">Demo arbiter: in this testnet the server holds the demo arbiter's key and signs on your click.
          A real arbiter signs with their own key, outside AURIX.</p>
        ${["HOLD", "UNHOLD", "RELEASE", "VOID"].map((a) => `<button data-arb="${a}">${a}</button>`).join("")}` : ""}`;
    $("cdResult").append(box);
    $("cdExplain").onclick = (e) => busy(e.target, async () => {
      const r = await api(`/api/escrow/orders/${oid}/explain`, { method: "POST", body: { audience: $("cdAud").value } });
      $("cdAdvice").innerHTML = adviceHtml(r);
    });
    if ($("cdBrief")) $("cdBrief").onclick = (e) => busy(e.target, async () => {
      const r = await api(`/api/escrow/orders/${oid}/arbiter-brief`, { method: "POST", body: {} });
      $("cdAdvice").innerHTML = adviceHtml(r) + (r.inconsistencies?.length ? `<p><b>Inconsistencies</b></p><ul>${r.inconsistencies.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "")
        + (r.questions?.length ? `<p><b>To check outside AURIX</b></p><ul>${r.questions.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "")
        + `<p>Suggested action: <span class="pill warn">${esc(r.suggestion)}</span> ${esc(r.rationale || "")}</p><p class="muted">A suggestion only: nothing happens until the arbiter signs.</p>`;
    });
    box.querySelectorAll("button[data-arb]").forEach((b) => (b.onclick = (e) => busy(e.target, async () => {
      const r = await api(`/api/escrow/orders/${oid}/demo-arbiter`, { method: "POST", body: { action: b.dataset.arb } });
      toast(`Arbiter ${b.dataset.arb}: rule now says ${r.evaluation?.decision}`);
      await followCond(oid);
    })));
  }
  if ($("cdDeliver")) $("cdDeliver").onclick = (e) => busy(e.target, () => carrier(oid, false));
  if ($("cdForge")) $("cdForge").onclick = (e) => busy(e.target, () => carrier(oid, true));
  if ($("cdProof")) $("cdProof").onclick = (e) => busy(e.target, () => downloadProof(oid));
  return o;
}
const advisorLabel = () => escrowAdvisor?.enabled ? `model ${escrowAdvisor.model}` : "template (no model configured)";
function adviceHtml(r) {
  return `<blockquote class="advice">${esc(r.text)}</blockquote>
    <p class="muted">${r.generated_by === "llm" ? `Written by ${esc(r.model)}` : "Template text (no AI model reachable)"} · advisory only, may contain errors · saved in the proof package.</p>`;
}
async function carrier(oid, forge) {
  const r = await fetch(`${deliveryUrl()}/deliver/${encodeURIComponent(oid)}${forge ? "?forge=true" : ""}`, { method: "POST" });
  const b = await r.json();
  if (b.aurix_status >= 400) toast(`AURIX refused the evidence: ${b.aurix_response?.detail || b.aurix_status}`, !forge);
  else toast(`Delivery evidence accepted: ${b.aurix_response?.evaluation?.decision || "stored"}`);
  await followCond(oid);
}
async function followCond(oid, tries = 120) {
  let last = null;
  for (let i = 0; i < tries; i++) {
    const o = await renderCond(oid);
    if (o.status !== last) { last = o.status; refreshCond().catch(() => {}); }
    if (CD_FINAL.includes(o.status) || o.status === "AUTHORIZED" || o.status === "SCORED") break;
    await new Promise((r) => setTimeout(r, 500));
  }
}
async function downloadProof(oid) {
  const r = await fetch(`${C.gateway}/api/escrow/orders/${encodeURIComponent(oid)}/proof`);
  if (!r.ok) throw new Error(`proof export failed (${r.status})`);
  const url = URL.createObjectURL(await r.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: `aurix-proof-${oid}.zip` });
  document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 5000);
}
async function createCond() {
  const keys = $("cdArb").checked ? (await api("/api/escrow/keys")).keys : [];
  const arbiter = keys.find((k) => k.role === "arbiter")?.key_id || null;
  const o = await api("/api/escrow/orders", { method: "POST", body: { amount_usd: $("cdAmt").value,
    ...($("cdDays").value.startsWith("m") ? { deadline_minutes: Number($("cdDays").value.slice(1)) }
      : { deadline_days: Number($("cdDays").value) }), arbiter_key_id: arbiter, payer: wallet?.address } });
  const who = o.paypal_mode === "simulator" ? "PayPal simulator" : "PayPal Sandbox";
  $("cdResult").innerHTML = `<p>Conditional order <code>${esc(o.order_id)}</code> · ${esc(o.terms.amount.value)} USD · deadline ${esc(o.terms.deadline)}</p>` +
    riskHtml(o.risk, o.risk ? "ok" : "unavailable") +
    `<ol class="steps"><li><a href="${esc(o.approve_url)}" target="_blank" rel="noopener">Open the ${who} page</a> and approve as the buyer.</li>
      <li>Come back and authorize: PayPal holds the funds, nothing is charged yet.</li></ol>
    <button id="cdAuth" class="primary">I approve: hold ${esc(o.terms.amount.value)} USD</button><div id="cdMsg"></div>`;
  $("cdAuth").onclick = (e) => busy(e.target, async () => {
    try { await api(`/api/escrow/orders/${o.order_id}/authorize`, { method: "POST", body: { confirm: true } }); }
    catch (err) { $("cdMsg").innerHTML = `<p class="muted">${esc(err.message)}</p>`; return; }
    await renderCond(o.order_id); refreshCond();
  });
  refreshCond();
}

// ---------- explorer ----------
async function refreshExplorer() {
  const [blocks, txs] = await Promise.all([C.blocks(20), C.searchTxs("tx.height>0", 100)]);
  set("blocksGrid", blocks);
  set("txsGrid", txs);
  $("kTxs").textContent = txs.length.toLocaleString();
  refreshKpis();
}
async function refreshKpis() {
  const [st, escs, orders] = await Promise.all([C.status().catch(() => null),
    C.escrows().catch(() => null), C.gatewayOk ? api("/api/paypal/orders").catch(() => null) : null]);
  if (st) $("kHeight").textContent = "#" + st.height.toLocaleString();
  if (escs) { const l = escs.filter((e) => e.status === "LOCKED");
    $("kEsc").textContent = l.length; $("kEscAur").textContent = fmt(l.reduce((a, e) => a + Number(e.amount || 0), 0)) + " AUR locked"; }
  $("kProofs").textContent = orders ? orders.filter((o) => o.status === "ANCHORED").length : "–";
  $("kProofs").title = orders ? "" : "Needs the gateway (PayPal orders)";
}
const lnk = (q, label) => q == null || q === "" ? "–" : `<a class="lnk" data-q="${esc(q)}" title="${esc(q)}">${esc(label ?? q)}</a>`;
const dl = (pairs) => `<dl>${pairs.filter(([, v]) => v != null && v !== "").map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
const txLine = (t) => `<li>${lnk(t.hash, short(t.hash))} · ${esc(t.type)}${t.amount ? " · " + fmt(t.amount) + " AUR" : ""} · block ${lnk(t.height)}</li>`;
const txList = (rows) => rows?.length ? `<ul>${rows.slice(0, 25).map(txLine).join("")}</ul>` : `<p class="muted">No transactions.</p>`;
function renderDetail(r) {
  const card = (title, body) => `<div class="detail"><h3><span>${title}</span><button class="small" id="detailClose">Close</button></h3>${body}</div>`;
  if (r.kind === "tx") { const t = r.tx; return card("Transaction", dl([["Hash", `<code>${esc(t.hash)}</code>`], ["Block", lnk(t.height)], ["Type", esc(t.type)],
    ["From", lnk(t.from)], ["To", lnk(t.to)], ["Amount", t.amount ? fmt(t.amount) + " AUR" : ""], ["Escrow", t.escrow_id != null ? lnk(t.escrow_id, "#" + t.escrow_id) : ""],
    ["Result", t.code ? "failed: " + esc(t.log) : "ok"]])); }
  if (r.kind === "address") return card("Address", dl([["Address", `<code>${esc(r.address)}</code>`], ["Balance", fmt(r.balance) + " AUR"],
    ["Transactions", (r.history || []).length]]) + txList(r.history));
  if (r.kind === "block") { const b = r.block; return card(`Block #${esc(b.height)}`, dl([["Time", esc(new Date(b.time).toLocaleString())],
    ["Hash", `<code>${esc(b.hash)}</code>`], ["Validator", `<code>${esc(b.proposer)}</code>`], ["Txs", esc(b.num_txs)]]) + txList(b.txs)); }
  if (r.kind === "escrow") { const e = r.escrow; return card(`Escrow #${esc(e.id)}`, dl(Object.entries(e).map(([k, v]) =>
    [esc(k), k === "amount" ? fmt(v) + " AUR" : /^aur1/.test(String(v)) ? lnk(v) : esc(v)]))); }
  if (r.kind === "proof") { const p = r.proof; return card("PayPal proof", dl([["Proof hash", `<code>${esc(p.hash)}</code>`], ["PayPal ref", esc(p.external_ref)],
    ["Capture ref", esc(p.capture_ref)], ["Status", `<span class="pill att ${p.status === "CONFIRMED" ? "ok" : ""}">${esc(p.status)} · attestations ${p.attestations.length}/${p.threshold}</span>`],
    ["First seen", lnk(p.height)], ["Confirmed", p.confirmed_height ? lnk(p.confirmed_height) : ""],
    ["Attestors", p.attestations.map((a) => lnk(a, short(a))).join("<br>")]]) +
    `<p class="muted">Only this hash is on-chain; USD stays on PayPal. A proof is final once ${p.threshold} independent attestor(s) checked the capture with PayPal.</p>`); }
  return card("Result", `<pre>${esc(JSON.stringify(r, null, 2))}</pre>`);
}
// Same resolution order as the gateway's /api/explorer/search, straight from the chain. null = not found.
async function lookup(q) {
  const low = q.toLowerCase(), miss = (e) => { if (e.status !== 404 && e.status !== 400) throw e; return null; };
  if (low.startsWith("aur1")) {
    if (!AurixTx.validAddress(low)) return null;
    const [a, history] = await Promise.all([C.account(low), C.history(low)]);
    return { kind: "address", address: low, balance: a.balance, history };
  }
  const m = low.match(/^(?:escrow:\s*|#)(\d+)$/);
  if (m) return C.escrow(Number(m[1])).then((escrow) => ({ kind: "escrow", escrow }), miss);
  if (/^\d+$/.test(q)) {
    const h = Number(q), st = await C.status();
    if (h < 1 || h > st.height) return null;
    const [block, txs] = await Promise.all([C.block(h), C.searchTxs(`tx.height=${h}`, 100)]);
    return { kind: "block", block: { ...block, txs } };
  }
  if (/^[0-9a-fA-F]{64}$/.test(q)) {
    const t = await C.tx(q).catch(miss);
    if (t) return { kind: "tx", tx: t };
    return C.proof(low).then((proof) => ({ kind: "proof", proof }), miss);
  }
  return null;
}
async function search(q) {
  q = String(q).trim(); if (!q) return;
  $("searchQ").value = q;
  const d = $("detail");
  d.innerHTML = `<p class="muted">Searching…</p>`;
  try {
    const r = await lookup(q);
    if (!r) { d.innerHTML = `<div class="detail"><p class="muted" style="margin:0">Nothing found for <code>${esc(q)}</code>.</p></div>`; return; }
    d.innerHTML = renderDetail(r);
  } catch (e) { d.innerHTML = `<div class="detail"><p class="muted" style="margin:0">Search unavailable: ${esc(e.message)}</p></div>`; }
  $("detailClose")?.addEventListener("click", () => (d.innerHTML = ""));
  d.scrollIntoView({ block: "nearest", behavior: "smooth" });
}
window.explorerSearch = search;

let statusTick = 0;
async function refreshStatus() {
  // The gateway is optional and may come and go: re-detect it every ~9 s.
  if (++statusTick % 3 === 0) {
    const was = C.gatewayOk;
    await C.detectGateway().catch(() => {});
    if (was !== C.gatewayOk) { applyGateway(); toast(C.gatewayOk ? "Gateway back online" : "Gateway unreachable: PayPal and risk scoring are off"); }
  }
  try {
    const s = await C.status();
    const ep = C.activeEndpoint;
    $("chainStatus").textContent = `${s.chain_id} · block #${s.height}`;
    $("chainStatus").title = ep ? `via ${ep.label}` : "via gateway (no direct node reachable)";
  } catch { $("chainStatus").textContent = "chain offline"; $("chainStatus").title = ""; }
  $("gwStatus").textContent = C.gatewayOk ? "" : "gateway off: PayPal & risk scoring disabled";
  $("gwStatus").hidden = C.gatewayOk;
  if (wallet && statusTick % 3 === 1) crossCheck();
}

// ---------- init ----------
function init() {
  grid("historyGrid", [{ field: "height", headerName: "Block", maxWidth: 100 }, { field: "type" },
    { field: "from", valueFormatter: (p) => short(p.value) }, { field: "to", valueFormatter: (p) => short(p.value) }, amountCol, statusCol]);
  grid("escrowGrid", [{ field: "id", maxWidth: 80 }, { field: "status" }, amountCol,
    { field: "recipient", valueFormatter: (p) => short(p.value) }, { field: "arbiter", valueFormatter: (p) => short(p.value) },
    { field: "deadline_height", headerName: "Deadline" },
    { headerName: "Actions", minWidth: 200, sortable: false, filter: false, cellRenderer: (p) => p.data.status !== "LOCKED" ? "" :
      `<button onclick="escrowAction('MsgReleaseEscrow',${p.data.id},this)">Release</button><button onclick="escrowAction('MsgRefundEscrow',${p.data.id},this)">Refund</button>` }]);
  grid("ordersGrid", [{ field: "order_id" }, { field: "amount_usd", headerName: "USD" }, { field: "status" },
    { field: "risk_level", headerName: "Risk" }, { field: "anchor_height", headerName: "Proof block" },
    { headerName: "Attestations", valueGetter: (p) => { const a = p.data.attestations; return a && typeof a === "object" ? `${a.count}/${a.threshold}` : p.data.status === "ANCHORED" ? "confirmed" : ""; } },
    { field: "proof_hash", valueFormatter: (p) => short(p.value) }, { field: "simulated", headerName: "Rail", valueFormatter: (p) => (p.value ? "simulator" : "sandbox") }]);
  grid("condGrid", [{ field: "order_id", cellClass: "lnk-cell", onCellClicked: (p) => followCond(p.value).catch((e) => toast(e.message, true)),
    cellRenderer: (p) => `<a class="lnk">${esc(p.value)}</a>` }, { field: "amount_usd", headerName: "USD" }, { field: "status" },
    { field: "decision" }, { field: "deadline" }, { field: "evidence", headerName: "Evidence", maxWidth: 110 },
    { field: "simulated", headerName: "Simulated sources" }]);
  const clickQ = (field) => ({ cellClass: "lnk-cell", onCellClicked: (p) => p.value != null && search(p.value),
    cellRenderer: (p) => p.value == null ? "" : `<a class="lnk">${esc(field === "height" ? p.value : short(p.value))}</a>` });
  grid("blocksGrid", [{ field: "height", maxWidth: 110, ...clickQ("height") }, { field: "time", valueFormatter: (p) => new Date(p.value).toLocaleTimeString() },
    { field: "num_txs", headerName: "Txs", maxWidth: 90 }, { field: "hash", valueFormatter: (p) => short(p.value) },
    { field: "proposer", headerName: "Validator", valueFormatter: (p) => short(p.value) }]);
  grid("txsGrid", [{ field: "height", headerName: "Block", maxWidth: 100, ...clickQ("height") }, { field: "type" },
    { field: "from", ...clickQ() }, { field: "to", ...clickQ() }, amountCol,
    { field: "escrow_id", headerName: "Escrow", maxWidth: 100 }, statusCol, { field: "hash", ...clickQ() }]);
  $("searchForm").onsubmit = (e) => { e.preventDefault(); search($("searchQ").value); };
  $("detail").addEventListener("click", (e) => { const a = e.target.closest("a[data-q]"); if (a) search(a.dataset.q); });
  $("copyAddr").onclick = async () => { try { await navigator.clipboard.writeText(wallet.address); toast("Address copied"); }
    catch { prompt("Copy your address:", wallet.address); } };

  document.querySelectorAll("nav button").forEach((b) => (b.onclick = () => {
    document.querySelectorAll("nav button, .tab").forEach((x) => x.classList.remove("active"));
    b.classList.add("active"); $(b.dataset.tab).classList.add("active");
    ({ wallet: refreshWallet, escrow: refreshEscrows, paypal: refreshOrders, cond: refreshCond, explorer: refreshExplorer, settings: renderSettings })[b.dataset.tab]?.().catch((e) => toast(e.message, true));
  }));
  $("createWallet").onclick = () => setWallet(hex(crypto.getRandomValues(new Uint8Array(32)))); // demo account
  $("doImport").onclick = () => { const s = $("importSeed").value.trim(); if (s) setWallet(s); };
  $("newMnemonic").onclick = () => startBackup(K.generateMnemonic());
  $("backupDone").onclick = (e) => busy(e.target, finishBackup);
  $("backupCancel").onclick = () => { pendingMnemonic = null; $("backupWords").innerHTML = ""; showStart(); };
  $("doImportMnemonic").onclick = (e) => busy(e.target, importMnemonic);
  $("unlockGo").onclick = (e) => busy(e.target, unlock);
  $("unlockPw").onkeydown = (e) => { if (e.key === "Enter") $("unlockGo").click(); };
  $("lock").onclick = () => { renderTrust(null); showStart(); };
  $("forgetVault").onclick = () => confirm("Remove the encrypted wallet from this browser? Without the recovery phrase, funds are lost.") && (K.saveVault(null), showStart());
  $("forget").onclick = () => {
    const demo = wallet?.kind === "demo";
    if (!confirm(demo ? "Forget this demo wallet? Without the seed, funds are lost." : "Remove the encrypted wallet from this browser? Without the recovery phrase, funds are lost.")) return;
    if (!demo) K.saveVault(null);
    setWallet(null);
  };
  $("showSeed").onclick = () => prompt("Your seed (keep it private):", wallet.seed);
  $("refresh").onclick = (e) => busy(e.target, refreshWallet);
  $("faucet").onclick = (e) => busy(e.target, async () => { const r = await C.faucet(wallet.address);
    toast(`+100 AUR in block ${r.height}`); await refreshWallet().catch(() => {});
    // a read node may lag the faucet's node by a block: refresh again shortly
    setTimeout(() => refreshWallet().catch(() => {}), 1500); });
  $("sendCheck").onclick = (e) => busy(e.target, checkSend);
  $("escCheck").onclick = (e) => busy(e.target, checkEscrow);
  $("ppCreate").onclick = (e) => busy(e.target, createOrder);
  $("cdCreate").onclick = (e) => busy(e.target, createCond);
  $("addNode").onclick = (e) => busy(e.target, async () => { C.addNode($("nodeRest").value, $("nodeRpc").value);
    $("nodeRest").value = $("nodeRpc").value = ""; toast("Node added: it is tried first"); renderSettings(); refreshStatus(); });
  $("saveGw").onclick = (e) => busy(e.target, async () => { const v = $("gwUrl").value.trim();
    await C.setGateway(v === "" ? null : v); applyGateway(); renderSettings(); refreshStatus();
    toast(C.gatewayOk ? "Gateway connected" : "No gateway: PayPal and risk scoring are off"); });

  applyGateway();
  // An encrypted wallet takes precedence (unlock screen); otherwise restore a demo account.
  if (!K.loadVault() && stored()) setWallet(stored()); else showStart();
  refreshStatus(); setInterval(refreshStatus, 3000);
}

// Enables or disables what needs optional services (gateway: PayPal + Guardian; faucet).
function applyGateway() {
  const h = C.health;
  $("ppMode").textContent = !C.gatewayOk ? "PayPal unavailable: no gateway configured (see Settings)" :
    ({ simulator: "Local PayPal simulator (tests only — no real PayPal)", sandbox: "PayPal Sandbox", disabled: "PayPal not configured" }[h.paypal_mode] || h.paypal_mode);
  $("ppCreate").disabled = !C.gatewayOk || h.paypal_mode === "disabled";
  $("cdMode").textContent = $("ppMode").textContent;
  $("cdCreate").disabled = $("ppCreate").disabled;
  $("faucet").hidden = !C.faucetUrl();
  $("sendCheck").textContent = C.gatewayOk || C.config.guardian ? "Check with Neural Guardian" : "Review payment";
}

// ---------- settings ----------
async function renderSettings() {
  const cfg = C.config, eps = C.state.endpoints, act = C.activeEndpoint;
  $("setChain").textContent = cfg.chain_id || "(any: taken from the node)";
  $("setGw").textContent = C.gatewayOk ? C.gateway : "none (PayPal and risk scoring disabled)";
  $("setFaucet").textContent = C.faucetUrl() || "none";
  const ov = C.gatewayOverride();
  $("gwUrl").value = ov == null ? "" : ov;
  $("nodeList").innerHTML = eps.length ? eps.map((e, i) => `<li data-i="${i}"><code>${esc(e.rest)}</code> <span class="muted">${esc(e.source)}${act === e ? " · active" : ""}</span>
    <span class="probe muted">…</span>${e.source === "custom" ? ` <button class="small" data-rm="${esc(e.rest)}">Remove</button>` : ""}</li>`).join("")
    : `<li class="muted">No endpoint configured.</li>`;
  $("nodeList").querySelectorAll("[data-rm]").forEach((b) => (b.onclick = () => { C.removeNode(b.dataset.rm); renderSettings(); refreshStatus(); }));
  eps.forEach(async (e, i) => {
    const r = await C.probe(e), el = $("nodeList").querySelector(`li[data-i="${i}"] .probe`);
    if (!el) return;
    const wrong = r.ok && cfg.chain_id && r.chain_id !== cfg.chain_id;
    el.textContent = !r.ok ? `· ${r.error}` : wrong ? `· WRONG CHAIN ${r.chain_id}` : `· ${r.chain_id} #${r.height}`;
    el.className = "probe " + (r.ok && !wrong ? "ok" : "bad");
  });
}

C.init().then(init, (e) => { console.error(e); init(); });
