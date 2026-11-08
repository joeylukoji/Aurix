"""AURIX API Gateway: the single public edge of the AURIX services.

It serves the wallet API (account reads, tx preparation with the advisory Neural Guardian score,
broadcast, dev faucet) and routes /api/explorer/* to the indexer service and /api/paypal/* and /api/escrow/* to the
payments service. It holds no authority over AUR balances: balances are always read from the chain,
and every AUR movement is a transaction signed by the user's own key.
"""
from __future__ import annotations

import base64
import binascii
import logging
import re
import os
import secrets
import threading
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import crypto
from .clients import ChainClient, ChainError, GuardianClient, normalize_proof
from .config import Settings
from .db import DB
from .keys import load_key

log = logging.getLogger("aurix.gateway")

RISKY_TYPES = {"MsgSend", "MsgCreateEscrow"}


def risk_state(risk: dict | None) -> str:
    """ok | quorum_not_met (some valid signed scores, fewer than GUARDIAN_QUORUM) | unavailable."""
    if not risk:
        return "unavailable"
    return "ok" if risk.get("quorum_met", True) else "quorum_not_met"
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


def create_app(settings: Settings | None = None, chain: ChainClient | None = None,
               guardian: GuardianClient | None = None, indexer: httpx.Client | None = None,
               payments: httpx.Client | None = None) -> FastAPI:
    s = settings or Settings()
    chain = chain or ChainClient(s.chain_url, s.chain_rest_url)
    guardian = guardian or GuardianClient(s.guardian_urls, quorum=s.guardian_quorum, pubkeys=s.guardian_pubkeys)
    indexer = indexer or httpx.Client(base_url=s.indexer_url)
    payments = payments or httpx.Client(base_url=s.payments_url)
    db = DB(s.database_path)
    faucet_key = load_key("FAUCET", default_seed=s.faucet_seed)
    signer_lock = threading.Lock()

    app = FastAPI(title="AURIX API Gateway", version="0.1.0")
    app.state.db = db
    if s.cors_origins:  # a statically deployed wallet (other origin) may call the gateway/faucet
        app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["*"],
                           allow_headers=["*"], allow_credentials=False)

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
        try:  # the web wallet shows the PayPal rail of the payments service
            paypal_mode = payments.get("/health", timeout=3).json().get("paypal_mode", "unknown")
        except (httpx.HTTPError, ValueError):
            paypal_mode = "unavailable"
        return {"status": "ok", "paypal_mode": paypal_mode,
                "guardians": len(guardian.urls) if hasattr(guardian, "urls") else 1,
                "guardian_quorum": getattr(guardian, "quorum", 1)}

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
        return {**acct, "balance_aur": acct["balance"] / crypto.MICRO, "history": address_history(addr)}

    def address_history(addr: str) -> list:
        """History from the indexer (PostgreSQL); falls back to the chain tx search when it is unavailable,
        and merges txs the indexer has not reached yet."""
        seen, history = set(), []
        try:
            r = indexer.get(f"/api/explorer/addresses/{addr}/history", timeout=5)
            r.raise_for_status()
            history = r.json()
            seen = {t["hash"] for t in history}
            top = max((t["height"] for t in history), default=0)
            queries = [f"message.sender='{addr}' AND tx.height>{top}", f"transfer.recipient='{addr}' AND tx.height>{top}"]
        except (httpx.HTTPError, ValueError):
            queries = [f"message.sender='{addr}'", f"transfer.recipient='{addr}'"]
        for q in queries:
            for t in chain.search_txs(q, 100):
                if t["hash"] not in seen:
                    seen.add(t["hash"])
                    history.append(t)
        history.sort(key=lambda t: t["height"], reverse=True)
        return history

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
            risk_status = risk_state(risk)
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

    # ---------------- explorer (indexer service) and PayPal (payments service) ----------------
    def forward(client: httpx.Client, name: str, request: Request, path: str, body: bytes | None) -> Response:
        """Thin reverse proxy: the gateway is the single public edge; the services own the logic."""
        headers = {k: v for k, v in request.headers.items()
                   if k.lower() in ("content-type", "accept") or k.lower().startswith("paypal-")}
        try:
            r = client.request(request.method, path, params=dict(request.query_params), content=body,
                               headers=headers, timeout=30)
        except httpx.HTTPError as e:
            return JSONResponse(status_code=503, content={"detail": f"{name} service unavailable: {type(e).__name__}"})
        keep = {k: v for k, v in r.headers.items() if k.lower() == "content-disposition"}  # proof downloads
        return Response(content=r.content, status_code=r.status_code, headers=keep,
                        media_type=r.headers.get("content-type", "application/json"))

    # Proxied routes are documented in api/indexer.openapi.json and api/payments.openapi.json.
    @app.api_route("/api/explorer/{path:path}", methods=["GET"], include_in_schema=False)
    async def explorer(path: str, request: Request):
        return await run_in_threadpool(forward, indexer, "indexer", request, f"/api/explorer/{path}", None)

    @app.api_route("/api/paypal/{path:path}", methods=["GET", "POST"], include_in_schema=False)
    async def paypal_proxy(path: str, request: Request):
        body = await request.body()
        return await run_in_threadpool(forward, payments, "payments", request, f"/api/paypal/{path}", body)

    @app.api_route("/api/escrow/{path:path}", methods=["GET", "POST"], include_in_schema=False)
    async def escrow_proxy(path: str, request: Request):
        """Evidence-released USD escrow (MVP 0.2), owned by the payments service."""
        body = await request.body()
        return await run_in_threadpool(forward, payments, "payments", request, f"/api/escrow/{path}", body)

    @app.get("/api/proofs/{h}")
    def proof_normalised(h: str):
        """Normalised chain proof: {hash, external_ref, capture_ref, status, attestations, threshold}."""
        if not re.fullmatch(r"[0-9a-fA-F]{64}", h):
            raise HTTPException(400, "proof hash must be 64 hex characters")
        try:
            thr = chain.anchor_threshold()
        except ChainError:
            thr = 1
        return normalize_proof(chain.proof_raw(h.lower()), thr)

    web_dir = Path(os.environ.get("WEB_DIR", Path(__file__).resolve().parents[2] / "web"))
    if web_dir.is_dir():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app


app = create_app() if os.environ.get("AURIX_NO_AUTOAPP") != "1" else None
