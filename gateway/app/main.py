"""AURIX API Gateway: orchestrates the wallet, Neural Guardian, PayPal and the chain.

It holds no authority over AUR balances: balances are always read from the chain,
and every AUR movement is a transaction signed by the user's own key.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
import os
import secrets
import threading
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import crypto
from .clients import ChainClient, ChainError, GuardianClient, PayPalClient, PayPalError
from .config import Settings
from .db import DB

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
    faucet_key = crypto.Key(s.faucet_seed)
    gateway_key = crypto.Key(s.gateway_seed)
    signer_lock = threading.Lock()

    app = FastAPI(title="AURIX API Gateway", version="0.1.0")
    app.state.db = db

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
                                  record=False)
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
        if res["type"] in RISKY_TYPES:
            # record in Guardian history (advisory; ignored if unavailable)
            r = guardian.score(context="aur_transfer", sender=res["from"], recipient=res.get("to") or "",
                               amount=(res.get("amount") or 1) / crypto.MICRO, currency="AUR")
            db.save_risk("aur_tx", res["hash"], r)
        return res

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
    def anchor(order_id: str, capture_id: str) -> dict:
        h = hashlib.sha256(f"{order_id}|{capture_id}".encode()).hexdigest()
        res = sign_and_send(gateway_key, crypto.msg_anchor_proof(gateway_key.address, h, f"paypal:{order_id}"))
        db.update_order(order_id, status="ANCHORED", capture_id=capture_id, proof_hash=h,
                        anchor_tx=res["hash"], anchor_height=res["height"])
        return res

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
        if not order or order["status"] == "ANCHORED":
            return "ignored"
        db.update_order(order_id, status="CONFIRMED", capture_id=resource.get("id"))
        anchor(order_id, resource["id"])
        return "anchored"

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
        return db.all("SELECT * FROM paypal_orders ORDER BY created_at DESC")

    @app.get("/api/paypal/orders/{order_id}")
    def get_order(order_id: str):
        o = db.one("SELECT * FROM paypal_orders WHERE order_id=?", order_id)
        if not o:
            raise HTTPException(404, "order not found")
        return o

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
        db.update_order(order_id, status="CAPTURED", capture_id=capture["id"])
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

    web_dir = Path(os.environ.get("WEB_DIR", Path(__file__).resolve().parents[2] / "web"))
    if web_dir.is_dir():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app


app = create_app() if os.environ.get("AURIX_NO_AUTOAPP") != "1" else None
