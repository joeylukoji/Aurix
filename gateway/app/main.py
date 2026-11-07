"""AURIX API Gateway: orchestrates the wallet, Neural Guardian, PayPal and the chain.

It holds no authority over AUR balances: balances are always read from the chain,
and every AUR movement is a transaction signed by the user's own key.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import threading
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import crypto
from .clients import ChainClient, ChainError, GuardianClient, MockPayPalClient, PayPalClient
from .config import Settings
from .db import DB

RISKY_TYPES = {"MsgSend", "MsgCreateEscrow"}


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
    msg: dict
    sender: str = Field(alias="from")
    pub_key: str
    nonce: int
    fee: int
    signature: str


class OrderReq(BaseModel):
    amount_usd: str
    payer: str | None = None
    description: str = "AURIX demo purchase"


class ApproveReq(BaseModel):
    confirm: bool = False


def create_app(settings: Settings | None = None, chain: ChainClient | None = None,
               guardian: GuardianClient | None = None, paypal=None) -> FastAPI:
    s = settings or Settings()
    chain = chain or ChainClient(s.chain_url)
    guardian = guardian or GuardianClient(s.guardian_url)
    if paypal is None:
        paypal = MockPayPalClient() if s.paypal_mock else PayPalClient(
            s.paypal_base_url, s.paypal_client_id, s.paypal_client_secret, s.paypal_webhook_id)
    db = DB(s.database_path)
    faucet_key = crypto.key_from_seed(s.faucet_seed)
    gateway_key = crypto.key_from_seed(s.gateway_seed)
    signer_lock = threading.Lock()

    app = FastAPI(title="AURIX API Gateway", version="0.1.0")
    app.state.db = db

    @app.exception_handler(ChainError)
    def _chain_err(_: Request, e: ChainError):
        return JSONResponse(status_code=400 if e.status < 500 else 502, content={"detail": e.detail})

    def chain_id() -> str:
        return chain.status()["chain_id"]

    def sign_and_send(key, msg: dict) -> dict:
        with signer_lock:  # serialise nonces of server-held dev keys
            sender = crypto.address_from_pub(crypto.pubkey_bytes(key))
            nonce = chain.account(sender)["nonce"]
            return chain.broadcast(crypto.sign_tx(key, chain_id(), msg, nonce))

    # ---------------- health & wallets ----------------
    @app.get("/api/health")
    def health():
        return {"status": "ok", "paypal_mode": "mock" if paypal.mock else "sandbox",
                }

    @app.get("/api/chain/status")
    def chain_status():
        return chain.status()

    @app.post("/api/wallets")
    def new_wallet():
        """Demo convenience: returns a fresh key. It is NOT stored server-side.
        The web wallet generates keys in the browser instead."""
        seed = secrets.token_hex(32)
        k = crypto.key_from_seed(seed)
        return {"address": crypto.address_from_pub(crypto.pubkey_bytes(k)), "seed": seed,
                "warning": "Demo testnet key. Store it yourself; the gateway does not keep it."}

    @app.get("/api/wallets/{addr}")
    def wallet(addr: str):
        acct = chain.account(addr)  # balance from the chain = source of truth
        history = [t for b in chain.blocks(500) for t in b["txs"] if addr in (t.get("from"), t.get("to"))]
        return {**acct, "balance_aur": acct["balance"] / crypto.MICRO, "history": history}

    @app.post("/api/wallets/{addr}/faucet")
    def faucet(addr: str):
        if not addr.startswith("aur1"):
            raise HTTPException(400, "invalid address")
        return sign_and_send(faucet_key, {"type": "MsgSend", "to": addr, "amount": 100 * crypto.MICRO})

    # ---------------- transactions ----------------
    @app.post("/api/txs/prepare")
    def prepare(req: PrepareReq):
        msg = req.msg.model_dump(exclude_none=True)
        acct = chain.account(req.sender)
        cid = chain_id()
        risk, risk_status = None, "not_applicable"
        if msg["type"] in RISKY_TYPES:
            risk = guardian.score(context="escrow" if msg["type"] == "MsgCreateEscrow" else "aur_transfer",
                                  sender=req.sender, recipient=msg.get("to", ""),
                                  amount=max(msg.get("amount", 0), 1) / crypto.MICRO, currency="AUR",
                                  record=False)
            risk_status = "ok" if risk else "unavailable"
        sb = crypto.sign_bytes(cid, msg, req.sender, acct["nonce"], crypto.DEFAULT_FEE)
        return {"chain_id": cid, "nonce": acct["nonce"], "fee": crypto.DEFAULT_FEE,
                "msg": crypto.canonical_msg(msg), "sign_bytes": sb.decode(),
                "risk": risk, "risk_status": risk_status, "advisory_only": True}

    @app.post("/api/txs")
    def submit(tx: SignedTx):
        body = tx.model_dump(by_alias=True)
        res = chain.broadcast(body)
        m = tx.msg
        if m.get("type") in RISKY_TYPES and res.get("code") == 0:
            # record in Guardian history (advisory; ignored if unavailable)
            r = guardian.score(context="aur_transfer", sender=tx.sender, recipient=m.get("to", ""),
                               amount=m.get("amount", 1) / crypto.MICRO, currency="AUR")
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
        return chain.blocks(limit)

    @app.get("/api/explorer/txs")
    def txs(limit: int = 200):
        out = [t | {"time": b["time"]} for b in chain.blocks(500) for t in b["txs"]]
        return out[:limit]

    @app.get("/api/explorer/proofs/{h}")
    def proof(h: str):
        return chain.proof(h)

    # ---------------- PayPal ----------------
    def anchor(order_id: str, capture_id: str) -> dict:
        h = hashlib.sha256(f"{order_id}|{capture_id}".encode()).hexdigest()
        res = sign_and_send(gateway_key, {"type": "MsgAnchorProof", "hash": h, "external_ref": f"paypal:{order_id}"})
        db.update_order(order_id, status="ANCHORED", capture_id=capture_id, proof_hash=h,
                        anchor_tx=res["hash"], anchor_height=res["height"])
        return res

    def handle_event(event: dict) -> str:
        """Shared by the real webhook and the mock simulation. Idempotent by event id."""
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
        db.exec("INSERT INTO paypal_orders(order_id, amount_usd, payer, description, status, risk_score, risk_level, mock)"
                " VALUES (?,?,?,?,?,?,?,?)", order["id"], value, req.payer, req.description, "SCORED",
                risk and risk["risk_score"], risk and risk["level"], int(paypal.mock))
        db.save_risk("paypal_order", order["id"], risk)
        approve = next((l["href"] for l in order.get("links", []) if l["rel"] in ("approve", "payer-action")), None)
        return {"order_id": order["id"], "status": "SCORED", "amount_usd": value, "approve_url": approve,
                "risk": risk, "risk_status": "ok" if risk else "unavailable", "advisory_only": True,
                "mock": paypal.mock}

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
        o = get_order(order_id)
        if not req.confirm:  # invariant 8: no capture without explicit human approval
            raise HTTPException(400, "explicit approval required (confirm=true)")
        if o["status"] != "SCORED":
            raise HTTPException(409, f"order is {o['status']}")
        cap = paypal.capture(order_id)
        capture = cap["purchase_units"][0]["payments"]["captures"][0]
        db.update_order(order_id, status="CAPTURED", capture_id=capture["id"])
        if paypal.mock:  # simulate the webhook PayPal would send
            handle_event({"id": "WH-MOCK-" + capture["id"], "event_type": "PAYMENT.CAPTURE.COMPLETED",
                          "resource": {"id": capture["id"],
                                       "supplementary_data": {"related_ids": {"order_id": order_id}}}})
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
        event = await request.json()
        if not paypal.verify_webhook(dict(request.headers), event):  # invariant 7
            raise HTTPException(400, "invalid webhook signature")
        return {"result": handle_event(event)}  # invariant 6: duplicates are a 200 no-op

    web_dir = Path(os.environ.get("WEB_DIR", Path(__file__).resolve().parents[2] / "web"))
    if web_dir.is_dir():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app


app = create_app() if os.environ.get("AURIX_NO_AUTOAPP") != "1" else None
