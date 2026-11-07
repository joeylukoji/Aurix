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

// ---------- keys (seed string -> sha256 -> secp256k1; same as the gateway) ----------
let wallet = null;
function store(seed) { try { seed ? localStorage.setItem("aurix.seed", seed) : localStorage.removeItem("aurix.seed"); } catch {} }
function stored() { try { return localStorage.getItem("aurix.seed"); } catch { return null; } }

async function setWallet(seed) {
  wallet = seed ? AurixTx.keyFromSeed(seed) : null;
  store(seed);
  $("noWallet").hidden = !!wallet; $("hasWallet").hidden = !wallet;
  if (wallet) { $("addr").textContent = wallet.address; refreshWallet(); }
}

// Serialise this wallet's transactions so two clicks never reuse the same sequence.
let txQueue = Promise.resolve();
function signAndSend(msg) {
  const run = txQueue.then(async () => {
    const p = await api("/api/txs/prepare", { method: "POST", body: { from: wallet.address, msg } });
    return api("/api/txs", { method: "POST", body: { tx_bytes: AurixTx.signTx(wallet, p) } });
  });
  txQueue = run.catch(() => {});
  return run;
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
  if (!AurixTx.validAddress(to) || !(amt > 0)) throw new Error("Enter a valid aur1… recipient and amount");
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
  if (!AurixTx.validAddress(to) || (arb && !AurixTx.validAddress(arb)) || !(amt > 0) || !(blocks > 0)) throw new Error("Check addresses, amount and deadline");
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
async function waitAnchored(oid, tries = 40) {
  for (let i = 0; i < tries; i++) {
    const o = await api(`/api/paypal/orders/${oid}`);
    if (o.status === "ANCHORED") return o;
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
    $("ppResult").innerHTML = `<p>Captured. Waiting for PayPal's signed webhook…</p>`;
    r = await waitAnchored(o.order_id);
    $("ppResult").innerHTML = r.status === "ANCHORED"
      ? `<div class="risk low"><div class="score">Captured &amp; anchored</div><p>Proof <code>${esc(r.proof_hash)}</code> in block ${r.anchor_height}. No AUR was created.</p></div>`
      : `<p>Status: ${esc(r.status)} — the webhook has not arrived yet. Refresh the orders list later.</p>`;
    refreshOrders();
  });
  $("ppNo").onclick = (e) => busy(e.target, async () => {
    await api(`/api/paypal/orders/${o.order_id}/cancel`, { method: "POST" }); $("ppResult").innerHTML = ""; refreshOrders();
  });
  refreshOrders();
}

// ---------- explorer ----------
async function refreshExplorer() {
  const [blocks, txs] = await Promise.all([api("/api/explorer/blocks?limit=200"), api("/api/explorer/txs?limit=100")]);
  set("blocksGrid", blocks);
  set("txsGrid", txs);
  $("kTxs").textContent = txs.length.toLocaleString();
  refreshKpis();
}
async function refreshKpis() {
  const [st, escs, orders] = await Promise.all([api("/api/chain/status").catch(() => null),
    api("/api/escrows").catch(() => null), api("/api/paypal/orders").catch(() => null)]);
  if (st) $("kHeight").textContent = "#" + st.height.toLocaleString();
  if (escs) { const l = escs.filter((e) => e.status === "LOCKED");
    $("kEsc").textContent = l.length; $("kEscAur").textContent = fmt(l.reduce((a, e) => a + Number(e.amount || 0), 0)) + " AUR locked"; }
  if (orders) $("kProofs").textContent = orders.filter((o) => o.status === "ANCHORED").length;
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
    ["Block", lnk(p.height)], ["Submitter", lnk(p.submitter)]]) + `<p class="muted">Only this hash is on-chain; USD stays on PayPal.</p>`); }
  return card("Result", `<pre>${esc(JSON.stringify(r, null, 2))}</pre>`);
}
async function search(q) {
  q = String(q).trim(); if (!q) return;
  $("searchQ").value = q;
  const d = $("detail");
  d.innerHTML = `<p class="muted">Searching…</p>`;
  try {
    const r = await fetch(`/api/explorer/search?q=${encodeURIComponent(q)}`);
    if (r.status === 404) { d.innerHTML = `<div class="detail"><p class="muted" style="margin:0">Nothing found for <code>${esc(q)}</code>.</p></div>`; return; }
    if (!r.ok) throw new Error(r.statusText);
    d.innerHTML = renderDetail(await r.json());
  } catch (e) { d.innerHTML = `<div class="detail"><p class="muted" style="margin:0">Search unavailable: ${esc(e.message)}</p></div>`; }
  $("detailClose")?.addEventListener("click", () => (d.innerHTML = ""));
  d.scrollIntoView({ block: "nearest", behavior: "smooth" });
}
window.explorerSearch = search;

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
    { field: "proof_hash", valueFormatter: (p) => short(p.value) }, { field: "simulated", headerName: "Rail", valueFormatter: (p) => (p.value ? "simulator" : "sandbox") }]);
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
    ({ wallet: refreshWallet, escrow: refreshEscrows, paypal: refreshOrders, explorer: refreshExplorer })[b.dataset.tab]?.().catch((e) => toast(e.message, true));
  }));
  $("createWallet").onclick = () => setWallet(hex(crypto.getRandomValues(new Uint8Array(32))));
  $("doImport").onclick = () => { const s = $("importSeed").value.trim(); if (s) setWallet(s); };
  $("forget").onclick = () => confirm("Forget this wallet? Without the seed, funds are lost.") && setWallet(null);
  $("showSeed").onclick = () => prompt("Your seed (keep it private):", wallet.seed);
  $("refresh").onclick = (e) => busy(e.target, refreshWallet);
  $("faucet").onclick = (e) => busy(e.target, async () => { const r = await api(`/api/wallets/${wallet.address}/faucet`, { method: "POST" });
    toast(`+100 AUR in block ${r.height}`); refreshWallet(); });
  $("sendCheck").onclick = (e) => busy(e.target, checkSend);
  $("escCheck").onclick = (e) => busy(e.target, checkEscrow);
  $("ppCreate").onclick = (e) => busy(e.target, createOrder);
  api("/api/health").then((h) => {
    $("ppMode").textContent = { simulator: "Local PayPal simulator (tests only — no real PayPal)",
      sandbox: "PayPal Sandbox", disabled: "PayPal not configured" }[h.paypal_mode] || h.paypal_mode;
    $("ppCreate").disabled = h.paypal_mode === "disabled";
  }).catch(() => {});

  setWallet(stored());
  refreshStatus(); setInterval(refreshStatus, 3000);
}
init();
