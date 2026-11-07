// AURIX chain client for the browser: reads and broadcasts directly against Cosmos REST endpoints
// (with failover), so the wallet works without the gateway. See web/README.md.
(() => {
  const TIMEOUT_MS = 6000;
  const LS_NODES = "aurix.nodes", LS_GATEWAY = "aurix.gateway";
  const ESCROW_STATUS = { ESCROW_STATUS_LOCKED: "LOCKED", ESCROW_STATUS_RELEASED: "RELEASED", ESCROW_STATUS_REFUNDED: "REFUNDED" };
  const PROOF_STATUS = { PROOF_STATUS_PENDING: "PENDING", PROOF_STATUS_CONFIRMED: "CONFIRMED" };
  const trim = (u) => String(u || "").trim().replace(/\/+$/, "");
  const b64hex = (b) => { try { return [...atob(b || "")].map((c) => c.charCodeAt(0).toString(16).padStart(2, "0")).join("").toUpperCase(); } catch { return b || ""; } };

  const ls = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, JSON.stringify(v)); } catch {} },
  };

  class ChainError extends Error { constructor(status, msg) { super(msg); this.status = status; } }

  // ---------- normalisers (ported from gateway/app/clients.py) ----------
  function uaur(coins) {
    if (coins && !Array.isArray(coins)) coins = [coins];
    return (coins || []).filter((c) => c && c.denom === "uaur").reduce((a, c) => a + Number(c.amount), 0);
  }
  function normalizeTx(tr) {
    const msgs = tr?.tx?.body?.messages || [];
    const m = msgs[0] || {};
    const events = {};
    for (const e of tr.events || []) if (!(e.type in events)) {
      events[e.type] = Object.fromEntries((e.attributes || []).map((a) => [a.key, a.value]));
    }
    const row = {
      hash: tr.txhash, height: Number(tr.height || 0), type: String(m["@type"] || "").split(".").pop(),
      from: m.from_address || m.sender || m.signer || null,
      to: m.to_address || m.recipient || null,
      amount: uaur(m.amount) || null,
      escrow_id: m.escrow_id ? Number(m.escrow_id) : null,
      code: Number(tr.code || 0), log: tr.code ? tr.raw_log : "",
      timestamp: tr.timestamp || null,
    };
    for (const ev of ["escrow_created", "escrow_released", "escrow_refunded"]) {
      if (events[ev]) {
        row.escrow_id = Number(events[ev].escrow_id);
        row.to = events[ev].recipient || row.to;
        const amt = events[ev].amount || "";
        if (amt.endsWith("uaur")) row.amount = Number(amt.slice(0, -4));
      }
    }
    return row;
  }
  function normalizeEscrow(e) {
    return { id: Number(e.id), sender: e.sender, recipient: e.recipient, arbiter: e.arbiter || "",
      amount: uaur(e.amount), deadline_height: Number(e.deadline_height),
      status: ESCROW_STATUS[e.status] || e.status, created_height: Number(e.created_height || 0) };
  }
  // Proof + params -> {hash, external_ref, capture_ref, status, attestations[], threshold, height, submitter}
  function normalizeProof(p, threshold) {
    const att = p.attestations && p.attestations.length ? p.attestations : (p.submitter ? [p.submitter] : []);
    const status = PROOF_STATUS[p.status] || p.status || "CONFIRMED"; // pre-attestation chains: anchored = final
    return { hash: p.hash, external_ref: p.external_ref, capture_ref: p.capture_ref || "", status,
      attestations: att, threshold: Number(threshold || p.threshold || 1),
      height: Number(p.first_seen_height || p.height || 0), confirmed_height: Number(p.confirmed_height || 0),
      submitter: p.submitter || att[0] || "" };
  }
  function normalizeBlock(b) {
    const blk = b.block || b.sdk_block || {}, hdr = blk.header || {};
    return { height: Number(hdr.height), time: hdr.time, hash: b64hex(b.block_id?.hash),
      num_txs: (blk.data?.txs || []).length, proposer: b64hex(hdr.proposer_address), chain_id: hdr.chain_id };
  }

  // ---------- config ----------
  const state = { config: null, endpoints: [], active: 0, gateway: "", gatewayOk: false, health: null, chainId: "" };

  function urlNodes() {
    try { const q = new URLSearchParams(location.search).get("nodes"); return q ? q.split(",").map(trim).filter(Boolean) : []; }
    catch { return []; }
  }

  function buildEndpoints() {
    const cfg = state.config || {};
    const list = [];
    const add = (e, source) => { const rest = trim(e.rest); if (rest && !list.some((x) => x.rest === rest)) list.push({ rest, rpc: trim(e.rpc), label: e.label || rest, source }); };
    urlNodes().forEach((rest) => add({ rest, label: "URL ?nodes=" }, "url"));
    ls.get(LS_NODES, []).forEach((e) => add(e, "custom"));
    (cfg.endpoints || []).forEach((e) => add(e, "config"));
    state.endpoints = list;
    state.active = 0;
  }

  async function timedFetch(url, opts = {}, ms = TIMEOUT_MS) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), ms);
    try { return await fetch(url, { ...opts, signal: ctl.signal }); } finally { clearTimeout(t); }
  }

  async function detectGateway() {
    const override = ls.get(LS_GATEWAY, null);
    let gw = override != null ? trim(override) : trim(state.config?.gateway);
    let candidates = gw ? [gw] : (override === "" || !/^https?:$/.test(location.protocol)) ? [] : [trim(location.origin)];
    if (gw === "none") candidates = [];
    state.gateway = ""; state.gatewayOk = false; state.health = null;
    for (const c of candidates) {
      try {
        const r = await timedFetch(c + "/api/health", {}, 3000);
        const h = r.ok && (await r.json());
        if (h && h.status === "ok") { state.gateway = c; state.gatewayOk = true; state.health = h; return; }
      } catch {}
    }
  }

  async function init() {
    let cfg = {};
    try { const r = await timedFetch("config.json", { cache: "no-store" }, 4000); if (r.ok) cfg = await r.json(); } catch {}
    state.config = { chain_id: "", endpoints: [], gateway: "", faucet: "", guardian: "", ...cfg };
    state.chainId = state.config.chain_id || "";
    buildEndpoints();
    await detectGateway();
    return api;
  }

  // ---------- REST with failover ----------
  // Tries endpoints in order starting from the last one that worked; switches on network error,
  // timeout or HTTP 5xx (except gRPC NotFound/InvalidArgument mapped to 500 by some SDK versions).
  async function rest(path, opts = {}) {
    const n = state.endpoints.length;
    if (!n) throw new ChainError(0, "no chain endpoint configured");
    let lastErr;
    for (let i = 0; i < n; i++) {
      const idx = (state.active + i) % n, ep = state.endpoints[idx];
      let r, body;
      try {
        r = await timedFetch(ep.rest + path, opts);
        body = await r.json().catch(() => ({}));
      } catch (e) { ep.down = true; lastErr = new ChainError(0, `${ep.label}: ${e.name === "AbortError" ? "timeout" : "unreachable"}`); continue; }
      if (r.status >= 500 && ![3, 5].includes(body.code) && !/not found/i.test(body.message || "")) {
        ep.down = true; lastErr = new ChainError(r.status, `${ep.label}: HTTP ${r.status}`); continue;
      }
      ep.down = false; state.active = idx;
      if (r.status >= 400) {
        const nf = r.status === 404 || body.code === 5 || /not found/i.test(body.message || "");
        throw new ChainError(nf ? 404 : 400, body.message || r.statusText);
      }
      return body;
    }
    throw lastErr;
  }

  // Gateway JSON API (PayPal, Guardian, faucet, and last-resort chain fallback).
  async function gw(path, opts = {}) {
    if (!state.gatewayOk) throw new ChainError(0, "gateway not available");
    const r = await timedFetch(state.gateway + path, { headers: { "Content-Type": "application/json" }, ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined }, opts.timeout || 30000);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new ChainError(r.status, data.detail || data.error || data.log || r.statusText);
    return data;
  }

  // Runs the direct REST path; when every node fails and a gateway is reachable, uses the gateway API.
  async function direct(fn, fallback) {
    try { return await fn(); } catch (e) {
      if ((e.status === 0 || e.status >= 500) && fallback && state.gatewayOk) { state.viaGateway = true; return fallback(); }
      throw e;
    }
  }
  const ok = (v) => { state.viaGateway = false; return v; };

  // ---------- reads ----------
  const api = {
    ChainError, normalizeTx, normalizeEscrow, normalizeProof, state,
    get config() { return state.config; },
    get gatewayOk() { return state.gatewayOk; },
    get gateway() { return state.gateway; },
    get health() { return state.health; },
    get activeEndpoint() { return state.viaGateway ? null : state.endpoints[state.active] || null; },
    gw, init, detectGateway,

    async status() {
      return direct(async () => {
        const b = normalizeBlock(await rest("/cosmos/base/tendermint/v1beta1/blocks/latest"));
        return ok({ chain_id: b.chain_id, height: b.height, denom: "uaur" });
      }, () => gw("/api/chain/status"));
    },

    async account(addr) {
      return direct(async () => {
        let num = 0, seq = 0, exists = true;
        try {
          const info = (await rest(`/cosmos/auth/v1beta1/account_info/${addr}`)).info || {};
          num = Number(info.account_number || 0); seq = Number(info.sequence || 0);
        } catch (e) { if (e.status !== 404 && e.status !== 400) throw e; exists = false; }
        const bal = (await rest(`/cosmos/bank/v1beta1/balances/${addr}/by_denom?denom=uaur`)).balance || {};
        return ok({ address: addr, account_number: num, sequence: seq, exists, balance: Number(bal.amount || 0) });
      }, async () => { const w = await gw(`/api/wallets/${addr}`); return { ...w, history: undefined }; });
    },

    async searchTxs(query, limit = 100) {
      return direct(async () => {
        const b = await rest(`/cosmos/tx/v1beta1/txs?query=${encodeURIComponent(query)}&order_by=ORDER_BY_DESC&limit=${limit}`);
        return ok((b.tx_responses || []).map(normalizeTx));
      }, () => query === "tx.height>0" ? gw(`/api/explorer/txs?limit=${limit}`) : Promise.resolve([]));
    },

    async history(addr) {
      return direct(async () => {
        const seen = new Set(), out = [];
        for (const q of [`message.sender='${addr}'`, `transfer.recipient='${addr}'`]) {
          const b = await rest(`/cosmos/tx/v1beta1/txs?query=${encodeURIComponent(q)}&order_by=ORDER_BY_DESC&limit=100`);
          for (const t of (b.tx_responses || []).map(normalizeTx)) if (!seen.has(t.hash)) { seen.add(t.hash); out.push(t); }
        }
        return ok(out.sort((a, b) => b.height - a.height));
      }, async () => (await gw(`/api/wallets/${addr}`)).history || []);
    },

    async escrows() {
      return direct(async () => ok(((await rest("/aurix/smartmoney/v1/escrows?pagination.limit=1000")).escrows || []).map(normalizeEscrow)),
        () => gw("/api/escrows"));
    },
    async escrow(id) {
      return direct(async () => ok(normalizeEscrow((await rest(`/aurix/smartmoney/v1/escrows/${id}`)).escrow)),
        () => gw(`/api/escrows/${id}`));
    },
    async params() {
      return direct(async () => ok((await rest("/aurix/smartmoney/v1/params")).params || {}), async () => ({}));
    },
    async proof(hash) {
      hash = String(hash).toLowerCase();
      return direct(async () => {
        const [p, params] = await Promise.all([rest(`/aurix/smartmoney/v1/proofs/${hash}`), this.params().catch(() => ({}))]);
        return ok(normalizeProof(p.proof, params.anchor_threshold));
      }, async () => {
        try { return normalizeProof(await gw(`/api/proofs/${hash}`)); }
        catch { return normalizeProof(await gw(`/api/explorer/proofs/${hash}`)); }
      });
    },
    async tx(hash) {
      return direct(async () => ok(normalizeTx((await rest(`/cosmos/tx/v1beta1/txs/${String(hash).toUpperCase()}`)).tx_response)),
        () => gw(`/api/explorer/txs/${hash}`));
    },
    async block(h) {
      return direct(async () => ok(normalizeBlock(await rest(`/cosmos/base/tendermint/v1beta1/blocks/${h}`))),
        async () => (await gw(`/api/explorer/search?q=${h}`)).block);
    },
    async blocks(limit = 20) {
      return direct(async () => {
        const top = normalizeBlock(await rest("/cosmos/base/tendermint/v1beta1/blocks/latest"));
        const hs = []; for (let h = top.height - 1; h >= 1 && hs.length < limit - 1; h--) hs.push(h);
        const rest_ = await Promise.all(hs.map((h) => this.block(h).catch(() => null)));
        return ok([top, ...rest_.filter(Boolean)]);
      }, () => gw(`/api/explorer/blocks?limit=${limit}`));
    },

    // ---------- signing helpers ----------
    // Verifies the node serves the expected chain before anything is signed.
    async checkChainId() {
      const s = await this.status();
      if (state.chainId && s.chain_id !== state.chainId) {
        throw new ChainError(400, `node reports chain "${s.chain_id}" but this wallet expects "${state.chainId}" — refusing to sign`);
      }
      return s.chain_id;
    },
    // Everything AurixTx.signTx needs, read from the chain instead of the gateway's /api/txs/prepare.
    async prepare(addr, msg) {
      const chain_id = await this.checkChainId();
      const acct = await this.account(addr);
      if (!acct.exists) throw new ChainError(400, "account has no funds yet: use the faucet first");
      return { chain_id, account_number: acct.account_number, sequence: acct.sequence, balance: acct.balance,
        fee: { denom: "uaur", amount: 2000, gas_limit: 300000 }, msg };
    },

    // Broadcast (sync = CheckTx) then poll until the tx is included in a block.
    async broadcast(txB64, waitMs = 30000) {
      return direct(async () => {
        const res = (await rest("/cosmos/tx/v1beta1/txs", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ tx_bytes: txB64, mode: "BROADCAST_MODE_SYNC" }) })).tx_response || {};
        if (Number(res.code || 0) !== 0) {
          const code = Number(res.code);
          throw new ChainError(400, `${res.raw_log || (code === 19 ? "tx already in mempool/cache (replay)" : "tx rejected")} [${res.codespace || "sdk"}:${code}]`);
        }
        const deadline = Date.now() + waitMs;
        while (Date.now() < deadline) {
          try {
            const t = normalizeTx((await rest(`/cosmos/tx/v1beta1/txs/${res.txhash}`)).tx_response);
            if (t.code !== 0) throw new ChainError(400, t.log);
            return ok(t);
          } catch (e) { if (e.status !== 404) throw e; }
          await new Promise((r) => setTimeout(r, 400));
        }
        throw new ChainError(408, `tx ${res.txhash} not included within ${waitMs / 1000}s`);
      }, () => gw("/api/txs", { method: "POST", body: { tx_bytes: txB64 } }));
    },

    // ---------- optional services ----------
    // Advisory risk score: gateway (/api/txs/prepare) or a configured Guardian. null = unavailable.
    async risk(addr, msg, balance) {
      if (state.gatewayOk) {
        try { const p = await gw("/api/txs/prepare", { method: "POST", body: { from: addr, msg }, timeout: 8000 }); return p.risk || null; }
        catch {}
      }
      const g = trim(state.config?.guardian);
      if (g) {
        try {
          const r = await timedFetch(g + "/v1/risk/score", { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ context: msg.type === "MsgCreateEscrow" ? "escrow" : "aur_transfer", sender: addr,
              recipient: msg.to || "", amount: Math.max(msg.amount || 0, 1) / 1e6, currency: "AUR",
              sender_balance: (balance || 0) / 1e6, record: false }) }, 4000);
          if (r.ok) return await r.json();
        } catch {}
      }
      return null;
    },
    faucetUrl() {
      const f = trim(state.config?.faucet);
      return f || (state.gatewayOk ? state.gateway : "");
    },
    async faucet(addr) {
      const f = this.faucetUrl();
      if (!f) throw new ChainError(0, "no faucet configured");
      const r = await timedFetch(`${f}/api/wallets/${addr}/faucet`, { method: "POST" }, 30000);
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new ChainError(r.status, d.detail || r.statusText);
      return d;
    },

    // ---------- settings ----------
    customNodes() { return ls.get(LS_NODES, []); },
    addNode(rest_, rpc) {
      rest_ = trim(rest_);
      if (!/^https?:\/\/[^\s]+$/.test(rest_)) throw new ChainError(400, "enter an http(s):// REST URL");
      const nodes = ls.get(LS_NODES, []).filter((e) => e.rest !== rest_);
      nodes.unshift({ rest: rest_, rpc: trim(rpc), label: "custom: " + rest_ });
      ls.set(LS_NODES, nodes); buildEndpoints();
    },
    removeNode(rest_) { ls.set(LS_NODES, ls.get(LS_NODES, []).filter((e) => e.rest !== rest_)); buildEndpoints(); },
    gatewayOverride() { return ls.get(LS_GATEWAY, null); },
    async setGateway(url) { ls.set(LS_GATEWAY, url == null ? null : trim(url)); await detectGateway(); },
    async probe(ep) {
      try { const r = await timedFetch(ep.rest + "/cosmos/base/tendermint/v1beta1/blocks/latest", {}, 4000);
        if (!r.ok) return { ok: false, error: "HTTP " + r.status };
        const b = normalizeBlock(await r.json()); return { ok: true, chain_id: b.chain_id, height: b.height };
      } catch (e) { return { ok: false, error: e.name === "AbortError" ? "timeout" : "unreachable" }; }
    },
  };

  window.AurixChain = api;
})();
