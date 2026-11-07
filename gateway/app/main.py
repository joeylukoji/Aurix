"""AURIX API Gateway: orchestrates the wallet, Neural Guardian, PayPal and the chain.

It holds no authority over AUR balances: balances are always read from the chain,
and every AUR movement is a transaction signed by the user's own key.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import re
import os
import secrets
import threading
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import crypto
from .clients import ChainClient, ChainError, GuardianClient, PayPalClient, PayPalError, normalize_proof
from .config import Settings
from .db import DB
from .keys import load_key

log = logging.getLogger("aurix.gateway")

RISKY_TYPES = {"MsgSend", "MsgCreateEscrow"}
TYPE_URLS = {
    "MsgSend": "/cosmos.bank.v1beta1.MsgSend",
    "MsgCreateEscrow": "/aurix.smartmoney.v1.MsgCreateEscrow",
    "MsgReleaseEscrow": "/aurix.smartmoney.v1.MsgReleaseEscrow",
    "MsgRefundEscrow": "/aurix.smartmoney.v1.MsgRefundEscrow",
}


class Msg(BaseModel):
    type: str
    to: str | None = None
    amount: int | None = Field(default=None, ge=0, description="uaur")
    arbiter: str | None = None
    deadline_height: int | None = None
    escrow_id: int | None = None


class PrepareReq(BaseModel):
    sender: str = Field(alias="from")
    msg: Msg


class SignedTx(BaseModel):
    tx_bytes: str = Field(description="base64 protobuf TxRaw signed by the user's key (SIGN_MODE_DIRECT)")


class ObservedTx(BaseModel):
    txhash: str = Field(description="hash of a tx the wallet broadcast directly to a chain node")


class OrderReq(BaseModel):
    amount_usd: str
    payer: str | None = None
    description: str = "AURIX demo purchase"


class ApproveReq(BaseModel):
    confirm: bool = False


def create_app(settings: Settings | None = None, chain: ChainClient | None = None,
               guardian: GuardianClient | None = None, paypal=None) -> FastAPI:
    s = settings or Settings()
    chain = chain or ChainClient(s.chain_url, s.chain_rest_url)
    guardian = guardian or GuardianClient(s.guardian_url)
    if paypal is None and s.paypal_configured:
        paypal = PayPalClient(s.paypal_base_url, s.paypal_client_id, s.paypal_client_secret, s.paypal_webhook_id)
    paypal_mode = s.paypal_env if paypal is not None else "disabled"
    db = DB(s.database_path)
    faucet_key = load_key("FAUCET", default_seed=s.faucet_seed)
    gateway_key = load_key("GATEWAY", default_seed=s.gateway_seed)
    signer_lock = threading.Lock()

    app = FastAPI(title="AURIX API Gateway", version="0.1.0")
    app.state.db = db
    if s.cors_origins:  # a statically deployed wallet (other origin) may call the gateway/faucet
        app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["*"],
                           allow_headers=["*"], allow_credentials=False)

    @app.exception_handler(PayPalError)
    def _paypal_err(_: Request, e: PayPalError):
        if e.issue == "ORDER_NOT_APPROVED":
            return JSONResponse(status_code=409, content={"detail": "The buyer has not approved this order on PayPal yet.",
                                                          "issue": e.issue})
        return JSONResponse(status_code=502, content={"detail": f"PayPal error: {e.message}", "issue": e.issue})

    def require_paypal():
        if paypal is None:
            raise HTTPException(503, "PayPal is not configured (set PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET)")

    @app.exception_handler(ChainError)
    def _chain_err(_: Request, e: ChainError):
        code = 404 if e.status == 404 else 400 if e.status < 500 else 504 if e.status == 504 else 502
        return JSONResponse(status_code=code, content={"detail": e.detail})

    @app.exception_handler(httpx.HTTPError)
    def _chain_down(_: Request, e: httpx.HTTPError):
        return JSONResponse(status_code=502, content={"detail": f"chain node unreachable: {type(e).__name__}"})

    def chain_id() -> str:
        return chain.status()["chain_id"]

    def sign_and_send(key: crypto.Key, msg: bytes) -> dict:
        with signer_lock:  # serialise sequences of server-held dev keys
            acct = chain.account(key.address)
            tx = crypto.build_tx(key, [msg], chain_id(), acct["account_number"], acct["sequence"])
            return chain.broadcast(tx)

    # ---------------- health & wallets ----------------
    @app.get("/api/health")
    def health():
        return {"status": "ok", "paypal_mode": paypal_mode,
                }

    @app.get("/api/chain/status")
    def chain_status():
        return chain.status()

    @app.post("/api/wallets")
    def new_wallet():
        """Demo convenience: returns a fresh key. It is NOT stored server-side.
        The web wallet generates keys in the browser instead."""
        seed = secrets.token_hex(32)
        return {"address": crypto.Key(seed).address, "seed": seed,
                "warning": "Demo testnet key. Store it yourself; the gateway does not keep it."}

    def check_addr(addr: str):
        if not crypto.valid_address(addr):
            raise HTTPException(400, f"invalid address {addr!r}")

    @app.get("/api/wallets/{addr}")
    def wallet(addr: str):
        check_addr(addr)
        acct = chain.account(addr)  # balance from the chain = source of truth
        seen, history = set(), []
        for q in (f"message.sender='{addr}'", f"transfer.recipient='{addr}'"):
            for t in chain.search_txs(q, 100):
                if t["hash"] not in seen:
                    seen.add(t["hash"])
                    history.append(t)
        history.sort(key=lambda t: t["height"], reverse=True)
        return {**acct, "balance_aur": acct["balance"] / crypto.MICRO, "history": history}

    @app.post("/api/wallets/{addr}/faucet")
    def faucet(addr: str):
        check_addr(addr)
        return sign_and_send(faucet_key, crypto.msg_send(faucet_key.address, addr, 100 * crypto.MICRO))

    # ---------------- transactions ----------------
    @app.post("/api/txs/prepare")
    def prepare(req: PrepareReq):
        """Returns what the browser needs to build and sign a Cosmos tx, plus the advisory risk score."""
        msg = req.msg.model_dump(exclude_none=True)
        if msg["type"] not in TYPE_URLS:
            raise HTTPException(400, f"unsupported message type {msg['type']}")
        check_addr(req.sender)
        for f in ("to", "arbiter"):
            if msg.get(f):
                check_addr(msg[f])
        acct = chain.account(req.sender)
        if not acct["exists"]:
            raise HTTPException(400, "account has no funds yet: use the faucet first")
        risk, risk_status = None, "not_applicable"
        if msg["type"] in RISKY_TYPES:
            risk = guardian.score(context="escrow" if msg["type"] == "MsgCreateEscrow" else "aur_transfer",
                                  sender=req.sender, recipient=msg.get("to", ""),
                                  amount=max(msg.get("amount", 0), 1) / crypto.MICRO, currency="AUR",
                                  sender_balance=acct["balance"] / crypto.MICRO, record=False)
            risk_status = "ok" if risk else "unavailable"
        return {"chain_id": chain_id(), "account_number": acct["account_number"], "sequence": acct["sequence"],
                "fee": {"denom": crypto.DENOM, "amount": crypto.FEE, "gas_limit": crypto.GAS_LIMIT},
                "type_url": TYPE_URLS[msg["type"]], "msg": msg,
                "risk": risk, "risk_status": risk_status, "advisory_only": True}

    @app.post("/api/txs")
    def submit(tx: SignedTx):
        try:
            raw = base64.b64decode(tx.tx_bytes, validate=True)
        except binascii.Error:
            raise HTTPException(400, "tx_bytes must be base64")
        res = chain.broadcast(raw)
        record_history(res)
        return res

    def record_history(res: dict) -> bool:
        """Records an included AUR transfer/escrow in Guardian history (advisory; ignored if unavailable).
        Idempotent per tx hash."""
        if res["type"] not in RISKY_TYPES or res.get("code") or \
                db.one("SELECT id FROM risk_scores WHERE context='aur_tx' AND ref=?", res["hash"]):
            return False
        r = guardian.score(context="aur_transfer", sender=res["from"], recipient=res.get("to") or "",
                           amount=(res.get("amount") or 1) / crypto.MICRO, currency="AUR")
        db.save_risk("aur_tx", res["hash"], r)
        return r is not None

    @app.post("/api/txs/observed")
    def observed(req: ObservedTx):
        """Called by the wallet after it broadcast a tx directly to a node. The gateway fetches the tx
        from the chain itself and never trusts client-supplied fields."""
        h = req.txhash.strip().upper()
        if not re.fullmatch(r"[0-9A-F]{64}", h):
            raise HTTPException(400, "txhash must be 64 hex characters")
        try:
            res = chain.tx(h)
        except ChainError as e:
            if e.status >= 500 and "not found" not in e.detail.lower():
                raise
            raise HTTPException(404, "tx not found on chain (not yet included?)")
        return {"tx": res, "recorded": record_history(res)}

    @app.get("/api/escrows")
    def escrows():
        return chain.escrows()

    @app.get("/api/escrows/{eid}")
    def escrow(eid: int):
        return chain.escrow(eid)

    # ---------------- explorer ----------------
    @app.get("/api/explorer/blocks")
    def blocks(limit: int = 50):
        return chain.blocks(min(limit, 200))

    @app.get("/api/explorer/txs")
    def txs(limit: int = 100):
        return chain.search_txs("tx.height>0", min(limit, 100))

    @app.get("/api/explorer/proofs/{h}")
    def proof(h: str):
        return chain.proof(h)

    @app.get("/api/proofs/{h}")
    def proof_normalised(h: str):
        """Normalised chain proof: {hash, external_ref, capture_ref, status, attestations, threshold}."""
        if not re.fullmatch(r"[0-9a-fA-F]{64}", h):
            raise HTTPException(400, "proof hash must be 64 hex characters")
        return normalize_proof(chain.proof_raw(h.lower()), threshold())

    @app.get("/api/explorer/txs/{h}")
    def tx_by_hash(h: str):
        try:
            return chain.tx(h.upper())
        except ChainError as e:
            if e.status >= 500 and e.status != 404:
                raise
            raise HTTPException(404, "not found")

    def address_history(addr: str) -> list:
        seen, history = set(), []
        for q in (f"message.sender='{addr}'", f"transfer.recipient='{addr}'"):
            for t in chain.search_txs(q, 100):
                if t["hash"] not in seen:
                    seen.add(t["hash"])
                    history.append(t)
        history.sort(key=lambda t: t["height"], reverse=True)
        return history

    def _missing(e: ChainError) -> bool:
        return e.status < 500 or "not found" in e.detail.lower()

    @app.get("/api/explorer/search")
    def search(q: str = ""):
        q = q.strip()
        if not q:
            raise HTTPException(400, "empty query")
        low = q.lower()
        try:
            if low.startswith("aur1"):
                if not crypto.valid_address(low):
                    raise HTTPException(404, "not found")
                return {"kind": "address", "address": low, "balance": chain.account(low)["balance"],
                        "history": address_history(low)}
            m = re.fullmatch(r"(?:escrow:\s*|#)(\d+)", low)
            if m:
                return {"kind": "escrow", "escrow": chain.escrow(int(m.group(1)))}
            if q.isdigit():
                h = int(q)
                if h < 1 or h > chain.status()["height"]:
                    raise HTTPException(404, "not found")
                return {"kind": "block", "block": chain.block(h) | {"txs": chain.search_txs(f"tx.height={h}", 100)}}
            if re.fullmatch(r"[0-9a-fA-F]{64}", q):
                try:
                    return {"kind": "tx", "tx": chain.tx(q.upper())}
                except ChainError as e:
                    if not _missing(e):
                        raise
                return {"kind": "proof", "proof": chain.proof(low)}
        except ChainError as e:
            if not _missing(e):
                raise
        raise HTTPException(404, "not found")

    # ---------------- PayPal ----------------
    def threshold() -> int:
        try:
            return chain.anchor_threshold()
        except ChainError:
            return 1

    def capture_amount(capture: dict) -> tuple[str, str]:
        """(value, currency_code) of a PayPal capture, as PayPal returned them."""
        amt = capture.get("amount") or {}
        return str(amt.get("value") or ""), str(amt.get("currency_code") or "")

    def anchor(order_id: str, capture_id: str, amount: str = "", currency: str = "") -> dict:
        """Submits the gateway's attestation (it is the first attestor, not the authority).
        The order stays ATTESTING until the chain proof is CONFIRMED by k-of-n attestors."""
        h = hashlib.sha256(f"{order_id}|{capture_id}".encode()).hexdigest()
        ref = f"paypal:{order_id}"
        try:  # a retry after a lost broadcast result: our attestation may already be on chain
            existing = chain.proof_raw(h)
        except (ChainError, httpx.HTTPError):
            existing = None
        if existing and gateway_key.address in (existing.get("attestations") or [existing.get("submitter")]):
            res = {"hash": None, "height": int(existing.get("first_seen_height") or existing.get("height") or 0)}
        else:
            if not amount or not currency:  # fall back to what was captured / ordered
                o = db.one("SELECT amount_usd, capture_amount, capture_currency FROM paypal_orders WHERE order_id=?",
                           order_id) or {}
                amount = amount or o.get("capture_amount") or o.get("amount_usd") or ""
                currency = currency or o.get("capture_currency") or ("USD" if o.get("amount_usd") else "")
            # Newest MsgAnchorProof first; older chains reject unknown fields: amount/currency (5/6), then
            # capture_ref (4, chain predating k-of-n attestation).
            variants = [(capture_id, amount, currency), (capture_id, "", ""), ("", "", "")]
            for i, (cap, amt, cur) in enumerate(variants):
                try:
                    res = sign_and_send(gateway_key, crypto.msg_anchor_proof(gateway_key.address, h, ref, cap, amt, cur))
                    break
                except ChainError as e:
                    if i == len(variants) - 1 or "unknownfield" not in e.detail.lower().replace(" ", ""):
                        raise
                    log.warning("chain rejected MsgAnchorProof fields, retrying with fewer: %s", e.detail)
        db.update_order(order_id, status="ATTESTING", capture_id=capture_id, proof_hash=h,
                        capture_amount=amount or None, capture_currency=currency or None,
                        anchor_tx=res["hash"], anchor_height=res["height"])
        refresh_attestation(order_id)
        return res

    def refresh_attestation(order_id: str) -> None:
        """Reads the chain proof of an ATTESTING order; ANCHORED once the proof is CONFIRMED."""
        o = db.one("SELECT order_id, status, proof_hash FROM paypal_orders WHERE order_id=?", order_id)
        if not o or o["status"] != "ATTESTING" or not o["proof_hash"]:
            return
        try:
            p = normalize_proof(chain.proof_raw(o["proof_hash"]), threshold())
        except (ChainError, httpx.HTTPError) as e:
            log.warning("attestation refresh for %s failed: %s", order_id, e)
            return
        fields = {"attestations": json.dumps(p["attestations"]), "attest_threshold": p["threshold"]}
        if p["status"] == "CONFIRMED":
            fields["status"] = "ANCHORED"
        db.update_order(order_id, **fields)

    def retry_unanchored() -> None:
        """CONFIRMED orders (PayPal capture webhook verified) whose anchoring failed: try again."""
        for o in db.all("SELECT order_id, capture_id FROM paypal_orders WHERE status='CONFIRMED'"
                        " AND capture_id IS NOT NULL AND proof_hash IS NULL"):
            try:
                anchor(o["order_id"], o["capture_id"])
                log.info("anchored %s on retry", o["order_id"])
            except (ChainError, httpx.HTTPError) as e:
                log.warning("anchor retry for %s failed: %s", o["order_id"], e)

    def refresh_pending() -> None:
        retry_unanchored()
        for o in db.all("SELECT order_id FROM paypal_orders WHERE status='ATTESTING'"):
            refresh_attestation(o["order_id"])

    def present(o: dict) -> dict:
        signers = json.loads(o.pop("attestations", None) or "[]")
        thr = o.pop("attest_threshold", None)
        o["attestations"] = {"count": len(signers), "threshold": thr or 1, "signers": signers}
        return o

    stop = threading.Event()

    def poller():
        while not stop.wait(s.attest_poll_interval):
            try:
                refresh_pending()
            except Exception:  # never let the poller die
                log.exception("attestation poller error")

    @app.on_event("startup")
    def _start_poller():
        if s.attest_poll_interval > 0:
            threading.Thread(target=poller, name="attest-poller", daemon=True).start()

    @app.on_event("shutdown")
    def _stop_poller():
        stop.set()

    def handle_event(event: dict) -> str:
        """Processes a verified PayPal webhook event. Idempotent by event id."""
        etype = event.get("event_type")
        resource = event.get("resource", {})
        order_id = resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id")
        if not db.record_event_once(event["id"], etype, order_id):
            return "duplicate"
        if etype != "PAYMENT.CAPTURE.COMPLETED" or not order_id:
            return "ignored"
        order = db.one("SELECT * FROM paypal_orders WHERE order_id=?", order_id)
        if not order or order["status"] in ("ATTESTING", "ANCHORED"):
            return "ignored"
        amount, currency = capture_amount(resource)
        db.update_order(order_id, status="CONFIRMED", capture_id=resource.get("id"),
                        capture_amount=amount or None, capture_currency=currency or None)
        try:
            anchor(order_id, resource["id"], amount, currency)
        except Exception:
            # Forget the event so PayPal's redelivery is processed again (the poller also retries
            # CONFIRMED orders without a proof); the webhook answers 5xx, which makes PayPal retry.
            db.forget_event(event["id"])
            raise
        return "anchored" if get_order(order_id)["status"] == "ANCHORED" else "attesting"

    @app.post("/api/paypal/orders")
    def create_order(req: OrderReq):
        require_paypal()
        try:
            amount = Decimal(req.amount_usd)
            if amount <= 0 or amount.as_tuple().exponent < -2:
                raise InvalidOperation
        except InvalidOperation:
            raise HTTPException(422, "amount_usd must be a positive amount with at most 2 decimals")
        value = f"{amount:.2f}"
        order = paypal.create_order(value, req.description)
        risk = guardian.score(context="paypal_order", sender=req.payer or "anonymous", recipient="merchant",
                              amount=float(amount), currency="USD")
        db.exec("INSERT INTO paypal_orders(order_id, amount_usd, payer, description, status, risk_score, risk_level, simulated)"
                " VALUES (?,?,?,?,?,?,?,?)", order["id"], value, req.payer, req.description, "SCORED",
                risk and risk["risk_score"], risk and risk["level"], int(paypal_mode == "simulator"))
        db.save_risk("paypal_order", order["id"], risk)
        approve = next((l["href"] for l in order.get("links", []) if l["rel"] in ("approve", "payer-action")), None)
        return {"order_id": order["id"], "status": "SCORED", "amount_usd": value, "approve_url": approve,
                "risk": risk, "risk_status": "ok" if risk else "unavailable", "advisory_only": True,
                "paypal_mode": paypal_mode}

    @app.get("/api/paypal/orders")
    def list_orders():
        refresh_pending()
        return [present(o) for o in db.all("SELECT * FROM paypal_orders ORDER BY created_at DESC")]

    @app.get("/api/paypal/orders/{order_id}")
    def get_order(order_id: str):
        o = db.one("SELECT * FROM paypal_orders WHERE order_id=?", order_id)
        if not o:
            raise HTTPException(404, "order not found")
        if o["status"] == "ATTESTING":  # lazy refresh on read
            refresh_attestation(order_id)
            o = db.one("SELECT * FROM paypal_orders WHERE order_id=?", order_id)
        return present(o)

    @app.post("/api/paypal/orders/{order_id}/approve")
    def approve(order_id: str, req: ApproveReq):
        require_paypal()
        o = get_order(order_id)
        if not req.confirm:  # invariant 8: no capture without explicit human approval
            raise HTTPException(400, "explicit approval required (confirm=true)")
        if o["status"] != "SCORED":
            raise HTTPException(409, f"order is {o['status']}")
        cap = paypal.capture(order_id)
        capture = cap["purchase_units"][0]["payments"]["captures"][0]
        # The order is ANCHORED only once PayPal's signed webhook arrives (see /api/paypal/webhook).
        amount, currency = capture_amount(capture)
        db.update_order(order_id, status="CAPTURED", capture_id=capture["id"],
                        capture_amount=amount or None, capture_currency=currency or None)
        return get_order(order_id)

    @app.post("/api/paypal/orders/{order_id}/cancel")
    def cancel(order_id: str):
        o = get_order(order_id)
        if o["status"] != "SCORED":
            raise HTTPException(409, f"order is {o['status']}")
        db.update_order(order_id, status="CANCELLED")
        return get_order(order_id)

    @app.post("/api/paypal/webhook")
    async def webhook(request: Request):
        require_paypal()
        event = await request.json()
        if not await run_in_threadpool(paypal.verify_webhook, dict(request.headers), event):  # invariant 7
            raise HTTPException(400, "invalid webhook signature")
        return {"result": await run_in_threadpool(handle_event, event)}  # invariant 6: duplicates are a 200 no-op

    app.state.refresh_pending = refresh_pending
    web_dir = Path(os.environ.get("WEB_DIR", Path(__file__).resolve().parents[2] / "web"))
    if web_dir.is_dir():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app


app = create_app() if os.environ.get("AURIX_NO_AUTOAPP") != "1" else None
