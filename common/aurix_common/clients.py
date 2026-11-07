from __future__ import annotations

import base64
import time
import uuid

import httpx


class ChainError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


ESCROW_STATUS = {"ESCROW_STATUS_LOCKED": "LOCKED", "ESCROW_STATUS_RELEASED": "RELEASED",
                 "ESCROW_STATUS_REFUNDED": "REFUNDED"}


def _uaur(coins) -> int:
    if isinstance(coins, dict):
        coins = [coins]
    if not isinstance(coins, list):  # e.g. MsgAnchorProof.amount is a fiat decimal string, not coins
        return 0
    return sum(int(c["amount"]) for c in coins if isinstance(c, dict) and c.get("denom") == "uaur")


def normalize_tx(tr: dict) -> dict:
    """Flattens a Cosmos tx_response into the explorer/wallet row format."""
    msgs = tr.get("tx", {}).get("body", {}).get("messages", [])
    m = msgs[0] if msgs else {}
    events: dict[str, dict] = {}
    for e in tr.get("events", []):
        events.setdefault(e["type"], {a["key"]: a.get("value") for a in e.get("attributes", [])})
    mtype = m.get("@type", "").rsplit(".", 1)[-1]
    row = {
        "hash": tr.get("txhash"), "height": int(tr.get("height", 0)), "type": mtype,
        "from": m.get("from_address") or m.get("sender") or m.get("signer"),
        "to": m.get("to_address") or m.get("recipient"),
        "amount": _uaur(m.get("amount")) or None,
        "escrow_id": int(m["escrow_id"]) if m.get("escrow_id") else None,
        "code": int(tr.get("code", 0)), "log": tr.get("raw_log") if tr.get("code") else "",
        "timestamp": tr.get("timestamp"),
    }
    for ev in ("escrow_created", "escrow_released", "escrow_refunded"):
        if ev in events:
            row["escrow_id"] = int(events[ev]["escrow_id"])
            row["to"] = events[ev].get("recipient") or row["to"]
            amt = events[ev].get("amount", "")
            row["amount"] = int(amt.removesuffix("uaur")) if amt.endswith("uaur") else row["amount"]
    return row


def normalize_escrow(e: dict) -> dict:
    return {"id": int(e["id"]), "sender": e["sender"], "recipient": e["recipient"], "arbiter": e.get("arbiter") or "",
            "amount": _uaur(e["amount"]), "deadline_height": int(e["deadline_height"]),
            "status": ESCROW_STATUS.get(e["status"], e["status"]), "created_height": int(e.get("created_height", 0))}


PROOF_STATUS = {"PROOF_STATUS_PENDING": "PENDING", "PROOF_STATUS_CONFIRMED": "CONFIRMED",
                "PENDING": "PENDING", "CONFIRMED": "CONFIRMED", 1: "PENDING", 2: "CONFIRMED"}


def normalize_proof(p: dict, threshold: int) -> dict:
    """Chain PaymentProof -> {hash, external_ref, capture_ref, status, attestations, threshold, ...}.

    Backwards compatible with chains that predate k-of-n attestation: a proof without a
    status field is CONFIRMED, attested by its single submitter."""
    atts = list(p.get("attestations") or ([p["submitter"]] if p.get("submitter") else []))
    status = "PENDING" if PROOF_STATUS.get(p.get("status")) == "PENDING" else "CONFIRMED"
    return {"hash": p.get("hash"), "external_ref": p.get("external_ref", ""), "capture_ref": p.get("capture_ref", ""),
            "status": status, "attestations": atts, "threshold": threshold,
            "height": int(p.get("height") or 0), "submitter": p.get("submitter", ""),
            "first_seen_height": int(p.get("first_seen_height") or p.get("height") or 0),
            "confirmed_height": int(p.get("confirmed_height") or (p.get("height") if status == "CONFIRMED" else 0) or 0)}


class ChainClient:
    """Reads the Cosmos chain via its REST API (:1317) and CometBFT RPC (:26657)."""

    def __init__(self, rpc_url: str, rest_url: str, transport: httpx.BaseTransport | None = None):
        self.rpc = httpx.Client(base_url=rpc_url, timeout=20, transport=transport)
        self.rest = httpx.Client(base_url=rest_url, timeout=20, transport=transport)

    def _get(self, path: str, **params):
        r = self.rest.get(path, params=params or None)
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code >= 400:
            raise ChainError(r.status_code if r.status_code != 500 or body.get("code") != 5 else 404,
                             body.get("message", r.text))
        return body

    def status(self) -> dict:
        r = self.rpc.get("/status").json()["result"]
        return {"chain_id": r["node_info"]["network"], "height": int(r["sync_info"]["latest_block_height"]),
                "denom": "uaur"}

    def account(self, addr: str) -> dict:
        """Account number + sequence (nonce) + uaur balance. New accounts have none yet."""
        try:
            info = self._get(f"/cosmos/auth/v1beta1/account_info/{addr}")["info"]
            num, seq, exists = int(info.get("account_number", 0)), int(info.get("sequence", 0)), True
        except ChainError as e:
            if e.status not in (404, 400) and "not found" not in e.detail:
                raise
            num, seq, exists = 0, 0, False
        bal = self._get(f"/cosmos/bank/v1beta1/balances/{addr}/by_denom", denom="uaur")["balance"]
        return {"address": addr, "account_number": num, "sequence": seq, "nonce": seq, "exists": exists,
                "balance": int(bal["amount"])}

    def escrows(self) -> list:
        return [normalize_escrow(e) for e in self._get("/aurix/smartmoney/v1/escrows").get("escrows", [])]

    def escrow(self, i: int) -> dict:
        return normalize_escrow(self._get(f"/aurix/smartmoney/v1/escrows/{i}")["escrow"])

    def proof(self, h: str) -> dict:
        p = self._get(f"/aurix/smartmoney/v1/proofs/{h}")["proof"]
        return p | {"height": int(p["height"])}

    def proof_raw(self, h: str) -> dict:
        return self._get(f"/aurix/smartmoney/v1/proofs/{h}")["proof"]

    def anchor_threshold(self) -> int:
        """anchor_threshold from x/smartmoney params; 1 when absent (older chains) or 0."""
        params = self._get("/aurix/smartmoney/v1/params").get("params") or {}
        try:
            return max(int(params.get("anchor_threshold") or 1), 1)
        except (TypeError, ValueError):
            return 1

    def tx(self, h: str) -> dict:
        return normalize_tx(self._get(f"/cosmos/tx/v1beta1/txs/{h}")["tx_response"])

    def search_txs(self, query: str, limit: int = 100) -> list:
        body = self._get("/cosmos/tx/v1beta1/txs", query=query, order_by="ORDER_BY_DESC", limit=limit)
        return [normalize_tx(t) for t in body.get("tx_responses") or []]

    def search_txs_all(self, query: str, page_size: int = 100, max_pages: int = 1000) -> list:
        """Every tx matching `query` (oldest first), following the tx search pages."""
        out: list = []
        for page in range(1, max_pages + 1):
            body = self._get("/cosmos/tx/v1beta1/txs", query=query, order_by="ORDER_BY_ASC",
                             page=page, limit=page_size)
            batch = body.get("tx_responses") or []
            out += [normalize_tx(t) for t in batch]
            total = int(body.get("total") or 0)
            if len(batch) < page_size or (total and len(out) >= total):
                return out
        raise ChainError(500, f"tx search for {query!r} exceeded {max_pages} pages")

    def block(self, height: int) -> dict:
        r = self.rpc.get("/block", params={"height": height}).json()
        if "result" not in r:
            raise ChainError(404, (r.get("error") or {}).get("data") or f"block {height} not found")
        res = r["result"]
        hdr = res["block"]["header"]
        return {"height": int(hdr["height"]), "time": hdr["time"], "hash": res["block_id"]["hash"],
                "num_txs": len(res["block"]["data"].get("txs") or []), "proposer": hdr["proposer_address"]}

    def blocks(self, limit: int = 50) -> list:
        h = self.status()["height"]
        out, top = [], h
        while top >= 1 and len(out) < limit:
            low = max(1, top - 19)
            metas = self.rpc.get("/blockchain", params={"minHeight": low, "maxHeight": top}).json()["result"]["block_metas"]
            out += [{"height": int(m["header"]["height"]), "time": m["header"]["time"], "hash": m["block_id"]["hash"],
                     "num_txs": int(m["num_txs"]), "proposer": m["header"]["proposer_address"]} for m in metas]
            top = low - 1
        return out[:limit]

    def broadcast(self, tx_bytes: bytes, wait_s: float = 15) -> dict:
        """Broadcasts (sync, i.e. CheckTx) then waits until the tx is included in a block."""
        r = self.rest.post("/cosmos/tx/v1beta1/txs", json={"tx_bytes": base64.b64encode(tx_bytes).decode(),
                                                         "mode": "BROADCAST_MODE_SYNC"})
        if r.status_code >= 400:
            raise ChainError(r.status_code, r.json().get("message", r.text))
        resp = r.json()["tx_response"]
        if int(resp.get("code", 0)) != 0:
            code = int(resp["code"])
            detail = resp.get("raw_log") or ("tx already in mempool/cache (replay)" if code == 19 else "tx rejected")
            raise ChainError(400, f"{detail} [{resp.get('codespace', 'sdk')}:{code}]")
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            g = self.rest.get(f"/cosmos/tx/v1beta1/txs/{resp['txhash']}")
            if g.status_code == 200:
                res = normalize_tx(g.json()["tx_response"])
                if res["code"] != 0:
                    raise ChainError(400, res["log"])
                return res
            time.sleep(0.25)
        raise ChainError(504, f"tx {resp['txhash']} not included within {wait_s}s")


from .guardians import GuardianClient  # noqa: E402,F401  (quorum client, re-exported)


class PayPalError(Exception):
    def __init__(self, status: int, issue: str, message: str):
        super().__init__(message)
        self.status, self.issue, self.message = status, issue, message


class PayPalClient:
    """PayPal REST (Orders v2 + webhook signature verification).

    Works unchanged against the real PayPal Sandbox or the local simulator
    (paypal-sim/): only the base URL and credentials differ.
    """

    def __init__(self, base_url: str, client_id: str, secret: str, webhook_id: str,
                 transport: httpx.BaseTransport | None = None):
        self.http = httpx.Client(base_url=base_url, timeout=20, transport=transport)
        self.auth = (client_id, secret)
        self.webhook_id = webhook_id
        self._token: str | None = None

    def _headers(self) -> dict:
        if not self._token:
            r = self.http.post("/v1/oauth2/token", auth=self.auth, data={"grant_type": "client_credentials"})
            r.raise_for_status()
            self._token = r.json()["access_token"]
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def create_order(self, amount_usd: str, description: str) -> dict:
        body = {"intent": "CAPTURE", "purchase_units": [
            {"amount": {"currency_code": "USD", "value": amount_usd}, "description": description[:127]}]}
        r = self.http.post("/v2/checkout/orders", json=body,
                           headers=self._headers() | {"PayPal-Request-Id": str(uuid.uuid4())})
        return self._json(r)

    def capture(self, order_id: str) -> dict:
        return self._json(self.http.post(f"/v2/checkout/orders/{order_id}/capture", headers=self._headers()))

    @staticmethod
    def _json(r: httpx.Response) -> dict:
        if r.status_code >= 400:
            try:
                b = r.json()
            except ValueError:
                b = {}
            issue = (b.get("details") or [{}])[0].get("issue") or b.get("name") or "PAYPAL_ERROR"
            raise PayPalError(r.status_code, issue, b.get("message") or r.text[:200])
        return r.json()

    def verify_webhook(self, headers: dict, event: dict) -> bool:
        h = {k.lower(): v for k, v in headers.items()}
        body = {
            "auth_algo": h.get("paypal-auth-algo"),
            "cert_url": h.get("paypal-cert-url"),
            "transmission_id": h.get("paypal-transmission-id"),
            "transmission_sig": h.get("paypal-transmission-sig"),
            "transmission_time": h.get("paypal-transmission-time"),
            "webhook_id": self.webhook_id,
            "webhook_event": event,
        }
        if not all(body.values()):
            return False
        r = self.http.post("/v1/notifications/verify-webhook-signature", json=body, headers=self._headers())
        return r.status_code == 200 and r.json().get("verification_status") == "SUCCESS"
