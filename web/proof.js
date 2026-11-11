// AURIX proof page: pick a decided order, verify its proof package in the browser (web/verify.js), tamper with
// one digit to show it fails, and ask the AI advisor for an auditor's explanation.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
  const advisorLang = () => (window.AurixI18n ? AurixI18n.advisorLang() : "English");

  function toast(msg, err = false) {
    const t = $("toast");
    t.textContent = msg; t.className = err ? "err" : ""; t.hidden = false;
    clearTimeout(toast.h); toast.h = setTimeout(() => (t.hidden = true), 6000);
  }
  async function busy(btn, fn) {
    btn.disabled = true;
    try { await fn(); } catch (e) { toast(e.message, true); } finally { btn.disabled = false; }
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
  const oid = () => $("order").value;

  async function load() {
    const orders = (await api("/api/escrow/orders")).filter((o) => o.decision);
    const want = new URLSearchParams(location.search).get("order");
    $("order").innerHTML = orders.length ? orders.map((o) => `<option value="${esc(o.order_id)}">${esc(o.order_id)} · ${esc(o.amount_usd)} USD · ${esc(o.decision.decision)}</option>`).join("")
      : `<option value="">No decided order yet: run the demo first</option>`;
    if (want && orders.some((o) => o.order_id === want)) $("order").value = want;
    await show();
  }
  async function show() {
    $("report").innerHTML = ""; $("advice").innerHTML = "";
    if (!oid()) { $("summary").innerHTML = ""; return; }
    const o = await api(`/api/escrow/orders/${encodeURIComponent(oid())}`);
    $("summary").innerHTML = AurixWorkflow.render(o.terms, o, { amount: o.amount_usd });
  }
  async function files() {
    const r = await fetch(`/api/escrow/orders/${encodeURIComponent(oid())}/proof`);
    if (!r.ok) throw new Error(`proof export failed (${r.status})`);
    return AurixVerify.readZip(await r.arrayBuffer());
  }
  function reportHtml(r, note = "") {
    const ok = r.result === "VALID", d = r.decision;
    const row = (k, v) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`;
    return `<div class="verdict ${ok ? "ok" : "bad"}"><span>${ok ? "VALID" : "INVALID"}</span><p>${note}</p></div>
      <table class="kv">${row("Integrity", r.integrity)}${row("Signatures", r.signatures)}${row("Sources", r.source_verification)}
        ${d ? row("Decision", `recorded ${d.recorded}, replayed ${d.replayed} (${d.matches ? "match" : "MISMATCH"})`) : ""}</table>
      ${r.errors.length ? `<ul class="reasons">${r.errors.slice(0, 4).map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}`;
  }
  async function verify(fs, note) {
    const keys = await api("/api/escrow/keys").catch(() => null);
    try { $("report").innerHTML = reportHtml(await AurixVerify.verifyPackage(fs, keys), note); }
    catch (e) {
      if (String(e.message) === "ED25519_UNSUPPORTED") throw new Error("This browser cannot verify Ed25519 signatures: use a recent Chrome, Edge, Firefox or Safari");
      throw e;
    }
  }
  $("order").onchange = () => show().catch((e) => toast(e.message, true));
  $("verify").onclick = () => busy($("verify"), async () => verify(await files(), "Every signature checks out and the replayed decision matches."));
  $("tamper").onclick = () => busy($("tamper"), async () => {
    const fs = await files();
    const p = Object.keys(fs).find((k) => k.startsWith("attestations/") && new TextDecoder().decode(fs[k]).includes("payment.authorized"));
    fs[p] = new TextEncoder().encode(new TextDecoder().decode(fs[p]).replace(/"value": "(\d)/, (m, d) => `"value": "${(Number(d) + 1) % 10}`));
    await verify(fs, "One digit of the payment amount was changed: the proof no longer holds.");
  });
  $("download").onclick = () => { if (oid()) location.href = `/api/escrow/orders/${encodeURIComponent(oid())}/proof`; };
  $("explain").onclick = () => busy($("explain"), async () => {
    if (!oid()) throw new Error("No decided order yet: run the demo first");
    $("advice").innerHTML = `<p class="wait">The AI is writing… (about 30 s)</p>`;
    const r = await api(`/api/escrow/orders/${encodeURIComponent(oid())}/explain`, { audience: "auditor", lang: advisorLang() }, 180000)
      .catch((e) => { $("advice").innerHTML = ""; throw e; });
    $("advice").innerHTML = `<blockquote class="advice">${esc(r.text)}</blockquote>
      <p class="muted small">${r.generated_by === "llm" ? `Written by ${esc(r.model)}` : "Template text (no AI model reachable)"} · advisory only, may contain errors · saved in the proof package.</p>`;
  });
  load().catch((e) => toast(e.message, true));
})();
