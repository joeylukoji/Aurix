// AURIX transaction workflow: the steps of a USD escrow drawn from its condition (never from model text).
// Shared by the wallet (app.js) and the guided demo (demo.js). window.AurixWorkflow.render(terms, order?, {amount, days}).
(function () {
  "use strict";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
  const leaves = (n) => ("evidence" in n ? [n] : (n.all || n.any || n.of || []).flatMap(leaves));
  // ---------- transaction workflow: drawn from the condition itself (never from model text) ----------
  const WF_EVIDENCE = {
    "shipment.shipped": ["Carrier", "Shipment confirmed"], "shipment.delivered": ["Carrier", "Delivery confirmed"],
    "inspection.passed": ["Inspector", "Quality inspection passed"], "customs.cleared": ["Customs", "Customs clearance confirmed"],
  };
  const WF_ORDER = ["shipment.shipped", "inspection.passed", "customs.cleared", "shipment.delivered"];
  // Steps of a USD escrow, from the terms (or a draft) and, for a live order, its state: done / now / todo.
  function render(t, o = null, opts = {}) {
    const cond = typeof t.condition === "object" && t.condition ? t.condition : { evidence: "shipment.delivered", source: t.carrier };
    const kind = "evidence" in cond ? "all" : "all" in cond ? "all" : "any" in cond ? "any" : "at_least";
    const items = leaves(cond).slice().sort((a, b) => WF_ORDER.indexOf(a.evidence) - WF_ORDER.indexOf(b.evidence));
    const valid = (l) => (o?.timeline || []).some((e) => e.type === l.evidence && e.provider === l.source && e.signature === "valid");
    const st = o?.status, dec = o?.decision?.decision, held = !!st && st !== "SCORED" && st !== "AUTHORIZING";
    const released = ["RELEASING", "CAPTURED", "CAPTURE_PENDING", "CONFIRMED", "ATTESTING", "ANCHORED"].includes(st);
    const voided = st === "VOIDING" || st === "VOIDED";
    const evDone = items.filter(valid).length, need = kind === "all" ? items.length : kind === "any" ? 1 : Number(cond.at_least);
    const metNow = held && evDone >= need, windowOpen = metNow && !dec && /dispute window open/.test((o?.last_evaluation?.reasons || []).join(" "));
    const state = (done, now) => (!o ? "todo" : done ? "done" : now ? "now" : "todo");
    const actor = (a, cls) => `<span class="wf-actor ${cls}">${esc(a)}</span>`;
    const step = (n, s, a, cls, title, detail) => `<li class="wf-step ${s}"><span class="wf-n">${n}</span>
      <div class="wf-body">${actor(a, cls)}<b>${esc(title)}</b><p>${detail}</p></div></li>`;
    const amount = t.amount ? `<b>${esc(t.amount.value)} USD</b>` : `<b>${esc(opts.amount)} USD</b>`;
    const days = t.deadline ? `<b>${esc(t.deadline.slice(0, 10))}</b>` : `<b>${esc(t.deadline_days || opts.days)}</b>`;
    const hours = Number(t.dispute_window_hours || 0);
    const group = kind === "all" ? "All of these" : kind === "any" ? "Any one of these" : `At least ${need} of these`;
    let n = 0;
    const ev = items.map((l) => {
      const [who, what] = WF_EVIDENCE[l.evidence] || ["Source", l.evidence];
      const limit = l.within_days ? `<span class="wf-limit">≤ ${esc(l.within_days)} d</span>` : l.before ? `<span class="wf-limit">≤ ${esc(l.before.slice(0, 10))}</span>` : "";
      return `<li class="wf-ev ${o ? (valid(l) ? "done" : held ? "now" : "todo") : "todo"}">${actor(who, "src")}<span>${esc(what)}</span>${limit}
        <code>${esc(l.source)}</code></li>`;
    }).join("");
    return `<section class="wf" aria-label="Transaction workflow">
      <div class="wf-head"><h4>Transaction workflow</h4>
        <span class="wf-meta">${amount} · ${t.deadline ? "deadline" : "deadline (days)"} ${days}${hours ? ` · dispute window <b>${hours} h</b>` : ""}</span></div>
      <ol class="wf-steps">
        ${step(++n, state(true), "Buyer", "buyer", "Order and risk check", "The order is created and Neural Guardian gives an advisory risk score before any money moves.")}
        ${step(++n, state(held, st === "SCORED"), "PayPal", "pp", "Funds authorised and held", "The buyer approves on PayPal: the amount is held, not charged. AURIX never holds the money.")}
        <li class="wf-step ${state(metNow, held)}"><span class="wf-n">${++n}</span><div class="wf-body">${actor("Independent sources", "src")}
          <b>Signed evidence</b><p>${group}, each signed with the source's own key, before the deadline:</p>
          <ul class="wf-evs">${ev}</ul></div></li>
        ${hours ? step(++n, state(metNow && !windowOpen && !!dec, windowOpen), "Buyer", "buyer", "Dispute window", "Once the evidence is complete, the buyer can still dispute. A dispute holds the payment for the arbiter.") : ""}
        ${step(++n, state(!!dec, metNow && !dec && !windowOpen), "AURIX rule", "rule", "Deterministic decision", "The public rule checks signatures, amount, references and deadlines, then signs RELEASE or VOID.")}
      </ol>
      <div class="wf-outcomes">
        <div class="wf-out ok ${released ? "hit" : ""}"><h5>Release</h5><ol>
          <li>PayPal captures the held amount</li><li>Two independent attestors re-check the capture</li>
          <li>The proof is anchored on the AURIX chain</li><li>A proof package anyone can verify offline</li></ol></div>
        <div class="wf-out void ${voided ? "hit" : ""}"><h5>Void</h5><ol>
          <li>Deadline reached without the evidence, or an arbiter decision</li><li>PayPal voids the authorisation</li>
          <li>The buyer is not charged</li></ol></div>
        ${t.arbiter_key_id || hours ? `<div class="wf-out hold ${dec === "HOLD" || /HOLD/.test(o?.last_evaluation?.decision || "") ? "hit" : ""}"><h5>Dispute</h5><ol>
          <li>The payment is held</li><li>The arbiter decides: release or void</li><li>No decision by the deadline: refund</li></ol></div>` : ""}
      </div></section>`;
  }
  window.AurixWorkflow = { render, leaves };
})();
