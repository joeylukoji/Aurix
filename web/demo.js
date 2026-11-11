// AURIX guided demo: describe -> AI condition -> PayPal hold -> signed evidence -> decision + AI explanation.
// Talks to the gateway on the same origin and to the simulated sources (delivery-sim).
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
  const AI_TIMEOUT = 180000;  // Gemma 4 takes 30-70 s; same limit as the gateway
  const FINAL = ["ANCHORED", "VOIDED", "AUTH_FAILED", "DENIED"];
  const SIM_PATH = { "shipment.shipped": "ship", "shipment.delivered": "deliver", "inspection.passed": "inspect", "customs.cleared": "customs" };
  const SIM_LABEL = { "shipment.shipped": "Carrier: report shipment", "shipment.delivered": "Carrier: report delivery",
    "inspection.passed": "Inspector: report inspection passed", "customs.cleared": "Customs: report clearance" };
  const PRESETS = {
    en: ["I buy 500 phones from a supplier in Shenzhen, China, delivered to Kinshasa by air freight. I want a quality inspection before shipping and customs clearance.",
      "I import 300 bags of cement from Turkey to Lubumbashi. The goods must pass a quality inspection.",
      "I buy a laptop from a shop in Kinshasa; pay only once it is delivered to me."],
    fr: ["J'achète 500 téléphones à un fournisseur de Shenzhen, en Chine, livrés à Kinshasa par avion. Je veux une inspection qualité avant l'expédition et le dédouanement.",
      "J'importe 300 sacs de ciment de Turquie vers Lubumbashi. La marchandise doit passer une inspection qualité.",
      "J'achète un ordinateur portable dans un magasin de Kinshasa ; je paie seulement une fois livré."],
  };
  const lang = () => (window.AurixI18n ? AurixI18n.lang() : "en");
  const advisorLang = () => (window.AurixI18n ? AurixI18n.advisorLang() : "English");
  let draft = null, order = null, deliveryUrl = "", timer = null;

  function toast(msg, err = false) {
    const t = $("toast");
    t.textContent = msg; t.className = err ? "err" : ""; t.hidden = false;
    clearTimeout(toast.h); toast.h = setTimeout(() => (t.hidden = true), 6000);
  }
  async function api(path, body, timeout = 30000) {
    const ctl = new AbortController(), h = setTimeout(() => ctl.abort(), timeout);
    try {
      const r = await fetch(path, body === undefined ? { signal: ctl.signal }
        : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: ctl.signal });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${r.status}`);
      return data;
    } catch (e) {
      if (e.name === "AbortError") throw new Error(`No answer after ${Math.round(timeout / 1000)} s: try again in a moment`);
      throw e;
    } finally { clearTimeout(h); }
  }
  async function busy(btn, fn) {
    btn.disabled = true;
    try { await fn(); } catch (e) { toast(e.message, true); } finally { btn.disabled = false; }
  }
  // a visible seconds counter while the AI works (Gemma 4 reasons before answering: 30-70 s)
  function ticking(el) {
    const t0 = Date.now(), base = el.textContent;
    const id = setInterval(() => { el.textContent = `${base} ${Math.round((Date.now() - t0) / 1000)} s`; }, 1000);
    return () => { clearInterval(id); el.textContent = base; };
  }
  function stage(n, state) { $(`s${n}`).dataset.state = state; }
  function go(n) {
    for (let i = 1; i <= 5; i++) stage(i, i < n ? "done" : i === n ? "now" : "todo");
    $(`s${n}`).scrollIntoView({ behavior: "smooth", block: "start" });
  }
  const wf = (terms, o) => AurixWorkflow.render(terms, o, { amount: $("amount").value, days: terms.deadline_days });

  // ---- 1. describe ----
  document.querySelectorAll("[data-preset]").forEach((b) => (b.onclick = () => { $("desc").value = PRESETS[lang()][b.dataset.preset]; }));
  $("ask").onclick = () => busy($("ask"), async () => {
    if (!$("desc").value.trim()) throw new Error("Describe the purchase first");
    $("askWait").hidden = false;
    const stop = ticking($("askWait"));
    try {
      draft = await api("/api/escrow/propose", { description: $("desc").value, amount_usd: $("amount").value, lang: advisorLang() }, AI_TIMEOUT);
    } finally { stop(); $("askWait").hidden = true; }
    renderProposal(); go(2);
  });

  // ---- 2. review ----
  function renderProposal() {
    const by = draft.generated_by === "llm" ? `Drafted by ${esc(draft.model)}` : "Template draft (no AI model reachable)";
    $("proposal").innerHTML = `
      <div class="summary"><span class="ai-badge">AI</span><p>${esc(draft.summary)}</p></div>
      ${draft.explanation ? `<p class="expl">${esc(draft.explanation)}</p>` : ""}
      ${draft.warnings?.length ? `<ul class="warns">${draft.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
      <p class="muted small">${by}</p>
      <div id="wf2"></div>
      <details class="edit"><summary>Edit the condition (JSON)</summary>
        <textarea id="cond" rows="9">${esc(JSON.stringify(draft.condition, null, 2))}</textarea></details>
      <label class="check"><input id="fast" type="checkbox" checked> Demo: release right after the evidence (no dispute window)</label>
      <button id="approve" class="primary">Approve this condition and create the order</button>`;
    const draw = () => {
      let condition;
      try { condition = JSON.parse($("cond").value); } catch { return; }
      $("wf2").innerHTML = wf({ condition, deadline_days: draft.deadline_days, dispute_window_hours: $("fast").checked ? 0 : draft.dispute_window_hours,
        arbiter_key_id: $("fast").checked ? null : "demo" });
    };
    $("cond").addEventListener("input", draw); $("fast").addEventListener("change", draw); draw();
    $("approve").onclick = () => busy($("approve"), createOrder);
  }
  async function createOrder() {
    let condition;
    try { condition = JSON.parse($("cond").value); } catch { throw new Error("The condition is not valid JSON"); }
    const window_ = $("fast").checked ? 0 : draft.dispute_window_hours;
    const arbiter = window_ ? (await api("/api/escrow/keys")).keys.find((k) => k.role === "arbiter")?.key_id : null;
    order = await api("/api/escrow/orders", { amount_usd: $("amount").value, deadline_days: draft.deadline_days, condition,
      dispute_window_hours: window_, arbiter_key_id: arbiter || null }, 150000);
    renderPay(); go(3);
  }

  // ---- 3. pay ----
  function renderPay() {
    const where = order.paypal_mode === "simulator" ? "PayPal simulator" : "PayPal Sandbox";
    $("pay").innerHTML = `
      <p>Order <code>${esc(order.order_id)}</code> · <b>${esc(order.terms.amount.value)} USD</b></p>
      ${order.risk ? `<p class="risk-line">Neural Guardian risk: <b>${Math.round(order.risk.risk_score * 100)}%</b> · ${esc(order.risk.level)}</p>` : ""}
      <ol class="mini">
        <li><a href="${esc(order.approve_url)}" target="_blank" rel="noopener">Open the ${where} page</a> and approve as the buyer.</li>
        <li>Come back here and confirm: PayPal holds the funds, nothing is charged yet.</li>
      </ol>
      <button id="hold" class="primary">I approved on PayPal: hold the funds</button>`;
    $("hold").onclick = () => busy($("hold"), async () => {
      await api(`/api/escrow/orders/${order.order_id}/authorize`, { confirm: true }, 150000);
      toast("Funds held by PayPal"); go(4); watch();
    });
  }

  // ---- 4. evidence (and live progress) ----
  async function watch() {
    clearTimeout(timer);
    const o = await api(`/api/escrow/orders/${order.order_id}`).catch(() => null);
    if (o) renderEvidence(o);
    if (!o || !FINAL.includes(o.status)) timer = setTimeout(watch, 3000);
  }
  // a source's evidence already accepted for this order: its demo button is shown as done
  const received = (o, l) => o.timeline.some((e) => e.type === l.evidence && e.provider === l.source && e.signature === "valid");
  function renderEvidence(o) {
    const items = AurixWorkflow.leaves(o.terms.condition);
    const open = o.status === "AUTHORIZED" && !o.decision;
    $("evidence").innerHTML = `
      ${open ? `<div class="sources">${items.map((l, i) => received(o, l)
        ? `<button class="got" disabled>✓ ${esc(SIM_LABEL[l.evidence] || l.evidence)}</button>`
        : `<button data-ev="${i}">${esc(SIM_LABEL[l.evidence] || l.evidence)}</button>`).join("")}
        <button id="forge" class="ghost">Try a forged delivery</button></div>` : ""}
      ${wf({ ...o.terms }, o)}`;
    $("evidence").querySelectorAll("[data-ev]").forEach((b) => (b.onclick = () => busy(b, () => source(items[b.dataset.ev].evidence, false))));
    if ($("forge")) $("forge").onclick = () => busy($("forge"), () => source("shipment.delivered", true));
    if (o.decision || o.status !== "AUTHORIZED") renderResult(o);
  }
  async function source(evidence, forge) {
    if (!deliveryUrl) throw new Error("No simulated sources configured");
    const r = await fetch(`${deliveryUrl}/${SIM_PATH[evidence]}/${encodeURIComponent(order.order_id)}${forge ? "?forge=true" : ""}`, { method: "POST" });
    const b = await r.json();
    if (b.aurix_status >= 400) toast(`AURIX refused the evidence: ${b.aurix_response?.detail || b.aurix_status}`, !forge);
    else toast(`${evidence} accepted: rule says ${b.aurix_response?.evaluation?.decision || "stored"}`);
    await watch();
  }

  // ---- 5. result ----
  const STATUS = {
    RELEASING: "Evidence accepted: capturing at PayPal…", CAPTURED: "Captured: waiting for PayPal's signed webhook…",
    CAPTURE_PENDING: "Captured: waiting for PayPal's signed webhook…", CONFIRMED: "Captured: anchoring the proof…",
    ATTESTING: "Captured: proof waiting for independent attestors…", ANCHORED: "Released: captured on evidence, proof anchored",
    VOIDING: "Deadline passed: releasing the funds…", VOIDED: "Voided: the buyer was not charged", AUTH_FAILED: "PayPal refused the operation",
  };
  function renderResult(o) {
    if ($("s5").dataset.state === "todo") go(5);
    const d = o.decision?.decision || o.last_evaluation?.decision || "";
    const keep = $("advice")?.innerHTML || "";
    $("result").innerHTML = `
      <div class="verdict ${d === "RELEASE" ? "ok" : d === "VOID" ? "void" : ""}"><span>${esc(d || "…")}</span>
        <p>${esc(STATUS[o.status] || o.status)}</p></div>
      ${(o.decision?.reasons || []).length ? `<ul class="reasons">${o.decision.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
      <div class="actions"><button id="explain" class="primary">AI: explain this payment</button>
        <a class="btn-link" href="proof.html?order=${encodeURIComponent(o.order_id)}">See and verify the proof →</a></div>
      <div id="advice">${keep}</div>`;
    $("explain").onclick = () => busy($("explain"), async () => {
      $("advice").innerHTML = `<p class="wait">The AI is writing… (about 30 s)</p>`;
      const stop = ticking($("advice").firstElementChild);
      const r = await api(`/api/escrow/orders/${o.order_id}/explain`, { audience: "buyer", lang: advisorLang() }, AI_TIMEOUT)
        .catch((e) => { $("advice").innerHTML = ""; throw e; }).finally(stop);
      $("advice").innerHTML = `<blockquote class="advice">${esc(r.text)}</blockquote>
        <p class="muted small">${r.generated_by === "llm" ? `Written by ${esc(r.model)}` : "Template text (no AI model reachable)"} · advisory only, may contain errors · saved in the proof package.</p>`;
    });
  }

  // ---- start ----
  api("/api/health").then((h) => { deliveryUrl = (h.delivery_url || "").replace(/\/+$/, ""); }).catch(() => {});
  if (!deliveryUrl) fetch("config.json").then((r) => r.json()).then((c) => { deliveryUrl ||= (c.delivery || "").replace(/\/+$/, ""); }).catch(() => {});
})();
