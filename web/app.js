// AURIX web wallet. Keys live in this browser only; every AUR movement is signed here.
const API = "";
const MICRO = 1_000_000;
const $ = (id) => document.getElementById(id);
const hex = (u8) => [...u8].map((b) => b.toString(16).padStart(2, "0")).join("");
const fmt = (u) => (u / MICRO).toLocaleString(undefined, { maximumFractionDigits: 6 });
const short = (a) => (a && a.length > 16 ? a.slice(0, 10) + "…" + a.slice(-6) : a || "");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

async function api(path, opts = {}) {
  const r = await fetch(API + path, { headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || data.error || data.log || r.statusText);
  return data;
}

function toast(msg, err = false) {
  const t = $("toast");
  t.textContent = msg; t.className = err ? "err" : ""; t.hidden = false;
  clearTimeout(toast.h); toast.h = setTimeout(() => (t.hidden = true), 5000);
}

async function busy(btn, fn) {
  btn.disabled = true;
  try { await fn(); } catch (e) { toast(e.message, true); } finally { btn.disabled = false; }
}

// ---------- keys (seed string -> sha256 -> ed25519 seed; same as the gateway) ----------
let wallet = null;
async function loadKey(seed) {
  const h = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(seed)));
  const kp = nacl.sign.keyPair.fromSeed(h);
  const ah = new Uint8Array(await crypto.subtle.digest("SHA-256", kp.publicKey));
  return { seed, kp, address: "aur1" + hex(ah.slice(0, 20)) };
}
function store(seed) { try { seed ? localStorage.setItem("aurix.seed", seed) : localStorage.removeItem("aurix.seed"); } catch {} }
function stored() { try { return localStorage.getItem("aurix.seed"); } catch { return null; } }

async function setWallet(seed) {
  wallet = seed ? await loadKey(seed) : null;
  store(seed);
  $("noWallet").hidden = !!wallet; $("hasWallet").hidden = !wallet;
  if (wallet) { $("addr").textContent = wallet.address; refreshWallet(); }
}

// Serialise this wallet's transactions so two clicks never reuse the same nonce.
let txQueue = Promise.resolve();
function signAndSend(msg) {
  const run = txQueue.then(() => doSignAndSend(msg));
  txQueue = run.catch(() => {});
  return run;
}
async function doSignAndSend(msg) {
  const p = await api("/api/txs/prepare", { method: "POST", body: { from: wallet.address, msg } });
  const sig = nacl.sign.detached(new TextEncoder().encode(p.sign_bytes), wallet.kp.secretKey);
  return api("/api/txs", { method: "POST", body: { msg: p.msg, from: wallet.address,
    pub_key: hex(wallet.kp.publicKey), nonce: p.nonce, fee: p.fee, signature: hex(sig) } });
}

function riskHtml(risk, status) {
  if (!risk) return `<div class="risk unavailable"><div class="score">Analysis unavailable</div>
    <p class="muted">Neural Guardian could not be reached. You can still decide.</p></div>`;
  const reasons = risk.reasons.map((r) => `<li>${esc(r.text)} <span class="muted">(+${r.impact})</span></li>`).join("");
  return `<div class="risk ${risk.level}"><div class="score">Risk ${Math.round(risk.risk_score * 100)}% · ${risk.level}</div>
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
  const w = await api(`/api/wallets/${wallet.address}`);
  $("balAur").textContent = fmt(w.balance) + " AUR";
  set("historyGrid", w.history);
}

// ---------- send ----------
async function checkSend() {
  const to = $("sendTo").value.trim(), amt = Math.round(parseFloat($("sendAmt").value) * MICRO);
  if (!wallet) throw new Error("Create a wallet first");
  if (!to.startsWith("aur1") || !(amt > 0)) throw new Error("Enter a valid recipient and amount");
  const msg = { type: "MsgSend", to, amount: amt };
  const p = await api("/api/txs/prepare", { method: "POST", body: { from: wallet.address, msg } });
  $("sendRisk").innerHTML = riskHtml(p.risk, p.risk_status) +
    `<button id="sendGo" class="primary">I approve: send ${fmt(amt)} AUR</button><button id="sendCancel">Cancel</button>`;
  $("sendCancel").onclick = () => ($("sendRisk").innerHTML = "");
  $("sendGo").onclick = (e) => busy(e.target, async () => {
    const r = await signAndSend(msg);
    toast(`Confirmed in block ${r.height}`); $("sendRisk").innerHTML = ""; refreshWallet();
  });
}

// ---------- escrow ----------
async function refreshEscrows() {
  const list = await api("/api/escrows");
  set("escrowGrid", list.reverse());
}
async function checkEscrow() {
  if (!wallet) throw new Error("Create a wallet first");
  const to = $("escTo").value.trim(), arb = $("escArb").value.trim();
  const amt = Math.round(parseFloat($("escAmt").value) * MICRO), blocks = parseInt($("escBlocks").value, 10);
  if (!to.startsWith("aur1") || !(amt > 0) || !(blocks > 0)) throw new Error("Check recipient, amount and deadline");
  const { height } = await api("/api/chain/status");
  const msg = { type: "MsgCreateEscrow", to, amount: amt, deadline_height: height + blocks, ...(arb ? { arbiter: arb } : {}) };
  const p = await api("/api/txs/prepare", { method: "POST", body: { from: wallet.address, msg } });
  $("escRisk").innerHTML = riskHtml(p.risk, p.risk_status) +
    `<button id="escGo" class="primary">I approve: lock ${fmt(amt)} AUR until block ${msg.deadline_height}</button>`;
  $("escGo").onclick = (e) => busy(e.target, async () => {
    const r = await signAndSend(msg);
    toast(`Escrow #${r.escrow_id} locked`); $("escRisk").innerHTML = ""; refreshEscrows(); refreshWallet();
  });
}
async function doEscrowAction(type, id) {
  if (!wallet) throw new Error("Create a wallet first");
  const r = await signAndSend({ type, escrow_id: id });
  toast(`Escrow #${id} ${type === "MsgReleaseEscrow" ? "released" : "refunded"} (block ${r.height})`);
  refreshEscrows(); refreshWallet();
}
window.escrowAction = (type, id, btn) => busy(btn, () => doEscrowAction(type, id));

// ---------- PayPal ----------
async function refreshOrders() { set("ordersGrid", await api("/api/paypal/orders")); }
async function createOrder() {
  const o = await api("/api/paypal/orders", { method: "POST", body: { amount_usd: $("ppAmt").value,
    description: $("ppDesc").value, payer: wallet?.address } });
  const link = o.approve_url && !o.mock ? `<p><a href="${esc(o.approve_url)}" target="_blank" rel="noopener">Open PayPal approval page</a> (buyer logs in with a Sandbox account), then confirm below.</p>` : "";
  $("ppResult").innerHTML = `<p>Order <code>${esc(o.order_id)}</code> · ${esc(o.amount_usd)} USD</p>` + riskHtml(o.risk, o.risk_status) + link +
    `<button id="ppGo" class="primary">I approve: capture ${esc(o.amount_usd)} USD</button><button id="ppNo">Cancel order</button>`;
  $("ppGo").onclick = (e) => busy(e.target, async () => {
    const r = await api(`/api/paypal/orders/${o.order_id}/approve`, { method: "POST", body: { confirm: true } });
    $("ppResult").innerHTML = r.status === "ANCHORED"
      ? `<div class="risk low"><div class="score">Captured &amp; anchored</div><p>Proof <code>${esc(r.proof_hash)}</code> in block ${r.anchor_height}. No AUR was created.</p></div>`
      : `<p>Status: ${esc(r.status)} — waiting for the signed PayPal webhook.</p>`;
    refreshOrders();
  });
  $("ppNo").onclick = (e) => busy(e.target, async () => {
    await api(`/api/paypal/orders/${o.order_id}/cancel`, { method: "POST" }); $("ppResult").innerHTML = ""; refreshOrders();
  });
  refreshOrders();
}

// ---------- explorer ----------
async function refreshExplorer() {
  const [blocks, txs] = await Promise.all([api("/api/explorer/blocks?limit=200"), api("/api/explorer/txs?limit=500")]);
  set("blocksGrid", blocks.map((b) => ({ ...b, ntx: b.txs.length })));
  set("txsGrid", txs);
}

async function refreshStatus() {
  try {
    const s = await api("/api/chain/status");
    $("chainStatus").textContent = `${s.chain_id} · block #${s.height}`;
  } catch { $("chainStatus").textContent = "chain offline"; }
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
    { field: "proof_hash", valueFormatter: (p) => short(p.value) }, { field: "mock", valueFormatter: (p) => (p.value ? "mock" : "sandbox") }]);
  grid("blocksGrid", [{ field: "height", maxWidth: 110 }, { field: "time", valueFormatter: (p) => p.value ? new Date(p.value * 1000).toLocaleTimeString() : "genesis" },
    { field: "ntx", headerName: "Txs", maxWidth: 90 }, { field: "hash", valueFormatter: (p) => short(p.value) }]);
  grid("txsGrid", [{ field: "height", headerName: "Block", maxWidth: 100 }, { field: "type" },
    { field: "from", valueFormatter: (p) => short(p.value) }, { field: "to", valueFormatter: (p) => short(p.value) }, amountCol,
    { field: "escrow_id", headerName: "Escrow", maxWidth: 100 }, statusCol, { field: "hash", valueFormatter: (p) => short(p.value) }]);

  document.querySelectorAll("nav button").forEach((b) => (b.onclick = () => {
    document.querySelectorAll("nav button, .tab").forEach((x) => x.classList.remove("active"));
    b.classList.add("active"); $(b.dataset.tab).classList.add("active");
    ({ wallet: refreshWallet, escrow: refreshEscrows, paypal: refreshOrders, explorer: refreshExplorer })[b.dataset.tab]?.().catch((e) => toast(e.message, true));
  }));
  $("createWallet").onclick = () => setWallet(hex(nacl.randomBytes(32)));
  $("doImport").onclick = () => { const s = $("importSeed").value.trim(); if (s) setWallet(s); };
  $("forget").onclick = () => confirm("Forget this wallet? Without the seed, funds are lost.") && setWallet(null);
  $("showSeed").onclick = () => prompt("Your seed (keep it private):", wallet.seed);
  $("refresh").onclick = (e) => busy(e.target, refreshWallet);
  $("faucet").onclick = (e) => busy(e.target, async () => { const r = await api(`/api/wallets/${wallet.address}/faucet`, { method: "POST" });
    toast(`+100 AUR in block ${r.height}`); refreshWallet(); });
  $("sendCheck").onclick = (e) => busy(e.target, checkSend);
  $("escCheck").onclick = (e) => busy(e.target, checkEscrow);
  $("ppCreate").onclick = (e) => busy(e.target, createOrder);
  api("/api/health").then((h) => ($("ppMode").textContent = h.paypal_mode === "mock"
    ? "Offline mock mode (no Sandbox credentials configured)" : "PayPal Sandbox")).catch(() => {});

  setWallet(stored());
  refreshStatus(); setInterval(refreshStatus, 3000);
}
init();
