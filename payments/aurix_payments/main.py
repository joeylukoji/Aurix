"""AURIX payments service: the PayPal connector.

Creates PayPal orders (with an advisory, signed Neural Guardian risk score), captures them after
explicit human approval, verifies PayPal webhooks (signature + idempotency) and anchors the payment
proof on AURIX as the first k-of-n attestor. It never creates or moves AUR.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import threading
from decimal import Decimal, InvalidOperation

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from aurix_common import crypto
from aurix_common.clients import ChainClient, ChainError, GuardianClient, PayPalClient, PayPalError, normalize_proof
from aurix_common.keys import is_production, load_key

from . import escrow as escrow_mod
from .config import Settings
from .db import DB

log = logging.getLogger("aurix.payments")


def risk_state(risk: dict | None) -> str:
    """ok | quorum_not_met (some valid signed scores, fewer than GUARDIAN_QUORUM) | unavailable."""
    if not risk:
        return "unavailable"
    return "ok" if risk.get("quorum_met", True) else "quorum_not_met"


class OrderReq(BaseModel):
    amount_usd: str
    payer: str | None = None
    description: str = "AURIX demo purchase"


class ApproveReq(BaseModel):
    confirm: bool = False


def create_app(settings: Settings | None = None, chain: ChainClient | None = None,
               guardian: GuardianClient | None = None, paypal=None, escrow_clock=None,
               advisor=None) -> FastAPI:
    s = settings or Settings()
    chain = chain or ChainClient(s.chain_url, s.chain_rest_url)
    guardian = guardian or GuardianClient(s.guardian_urls, quorum=s.guardian_quorum, pubkeys=s.guardian_pubkeys)
    if paypal is None and s.paypal_configured:
        paypal = PayPalClient(s.paypal_base_url, s.paypal_client_id, s.paypal_client_secret, s.paypal_webhook_id)
    paypal_mode = s.paypal_env if paypal is not None else "disabled"
    db = DB(s.database_path)
    gateway_key = load_key("GATEWAY", default_seed=s.gateway_seed)  # anchor signer (see config.py)
    signer_lock = threading.Lock()
    escrow = escrow_mod.EscrowService(db, paypal, guardian, paypal_mode, escrow_mod.load_signer(),
                                      escrow_mod.load_external_keys(), clock=escrow_clock or escrow_mod.utcnow,
                                      advisor=advisor,
                                      demo_arbiter=not is_production(os.environ))

    stop = threading.Event()

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        if s.attest_poll_interval > 0:
            threading.Thread(target=poller, name="attest-poller", daemon=True).start()
        yield
        stop.set()
        db.close()

    app = FastAPI(title="AURIX Payments (PayPal connector)", version="0.1.0", lifespan=lifespan)
    app.state.db, app.state.chain = db, chain
    if s.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["*"],
                           allow_headers=["*"], allow_credentials=False)

    @app.exception_handler(PayPalError)
    def _paypal_err(_: Request, e: PayPalError):
        if e.issue in ("CAPTURE_FULLY_REFUNDED", "INVALID_STATE"):
            return JSONResponse(status_code=409, content={"detail": f"PayPal: {e.message}", "issue": e.issue})
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
        with signer_lock:  # serialise the sequence of the anchor key
            acct = chain.account(key.address)
            tx = crypto.build_tx(key, [msg], chain_id(), acct["account_number"], acct["sequence"])
            return chain.broadcast(tx)

    @app.get("/health")
    def health():
        return {"status": "ok", "paypal_mode": paypal_mode, "anchor_signer": gateway_key.address}

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
        escrow.tick()

    def present(o: dict) -> dict:
        signers = json.loads(o.pop("attestations", None) or "[]")
        thr = o.pop("attest_threshold", None)
        o["attestations"] = {"count": len(signers), "threshold": thr or 1, "signers": signers}
        return o

    def poller():
        while not stop.wait(s.attest_poll_interval):
            try:
                refresh_pending()
            except Exception:  # never let the poller die
                log.exception("attestation poller error")

    def order_id_of(resource: dict) -> str | None:
        """Order id of a capture resource, or of a refund resource (via its capture link)."""
        oid = resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id")
        if oid:
            return oid
        for link in resource.get("links") or []:
            if link.get("rel") == "up" and "/captures/" in link.get("href", ""):
                cid = link["href"].rstrip("/").rsplit("/", 1)[-1]
                o = db.one("SELECT order_id FROM paypal_orders WHERE capture_id=?", cid)
                return o and o["order_id"]
        o = db.one("SELECT order_id FROM paypal_orders WHERE capture_id=?", resource.get("id"))
        return o and o["order_id"]

    def handle_event(event: dict) -> str:
        """Processes a verified PayPal webhook event. Idempotent by event id.

        CAPTURE.COMPLETED -> anchor the proof; CAPTURE.PENDING -> CAPTURE_PENDING; CAPTURE.DENIED -> DENIED
        (before anchoring); CAPTURE.REFUNDED / REVERSED -> REFUNDED (the on-chain proof of the original
        capture stays: it records that the capture happened; the refund is recorded off-chain here)."""
        etype = event.get("event_type")
        resource = event.get("resource", {})
        order_id = order_id_of(resource)
        if not db.record_event_once(event["id"], etype, order_id):
            return "duplicate"
        order = order_id and db.one("SELECT * FROM paypal_orders WHERE order_id=?", order_id)
        if not order:
            return "ignored"
        handled = escrow.on_paypal_event(event, order_id)  # evidence for evidence-released escrows (MVP 0.2)
        if handled:
            return handled
        settled = order["status"] in ("ATTESTING", "ANCHORED")
        if etype == "PAYMENT.CAPTURE.PENDING" and order["status"] == "CAPTURED":
            db.update_order(order_id, status="CAPTURE_PENDING")
            return "pending"
        if etype == "PAYMENT.CAPTURE.DENIED" and not settled and order["status"] != "REFUNDED":
            db.update_order(order_id, status="DENIED", last_error="capture denied by PayPal")
            return "denied"
        if etype in ("PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED"):
            db.update_order(order_id, status="REFUNDED", refund_id=resource.get("id"),
                            last_error=None if etype.endswith("REFUNDED") else "capture reversed")
            return "refunded"
        if etype != "PAYMENT.CAPTURE.COMPLETED" or settled or order["status"] in ("DENIED", "REFUNDED"):
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
                "risk": risk, "risk_status": risk_state(risk), "advisory_only": True,
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
        if escrow.is_escrow(order_id):
            raise HTTPException(409, "conditional order: authorize it with /api/escrow/orders/{id}/authorize")
        try:
            cap = paypal.capture(order_id)
        except PayPalError as e:
            if e.issue == "INSTRUMENT_DECLINED":  # the buyer may pay again with another funding source
                db.update_order(order_id, last_error="payment method declined")
                return JSONResponse(status_code=402, content={
                    "detail": "PayPal declined the buyer's payment method; ask the buyer to approve again "
                              "with another funding source.", "issue": e.issue, "order": get_order(order_id)})
            raise
        capture = cap["purchase_units"][0]["payments"]["captures"][0]
        amount, currency = capture_amount(capture)
        # COMPLETED: ANCHORED once PayPal's signed webhook arrives; PENDING: waits for the COMPLETED webhook.
        status = {"COMPLETED": "CAPTURED", "PENDING": "CAPTURE_PENDING"}.get(capture.get("status"), "DENIED")
        db.update_order(order_id, status=status, capture_id=capture["id"], capture_amount=amount or None,
                        capture_currency=currency or None,
                        last_error=None if status != "DENIED" else f"capture {capture.get('status')}")
        return get_order(order_id)

    @app.post("/api/paypal/orders/{order_id}/refund")
    def refund(order_id: str, req: ApproveReq):
        """Merchant-initiated full refund (explicit confirmation required). The order becomes REFUNDED
        when PayPal's signed PAYMENT.CAPTURE.REFUNDED webhook arrives."""
        require_paypal()
        o = get_order(order_id)
        if not req.confirm:
            raise HTTPException(400, "explicit confirmation required (confirm=true)")
        if o["status"] not in ("CAPTURED", "CONFIRMED", "ATTESTING", "ANCHORED") or not o.get("capture_id"):
            raise HTTPException(409, f"order is {o['status']}: nothing to refund")
        r = paypal.refund(o["capture_id"])
        return {"order_id": order_id, "refund_id": r.get("id"), "refund_status": r.get("status"),
                "note": "the order becomes REFUNDED when PayPal's webhook arrives"}

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

    escrow_mod.register(app, escrow, require_paypal)
    app.state.refresh_pending = refresh_pending
    app.state.escrow = escrow
    app.state.paypal_mode = paypal_mode
    return app


app = create_app() if os.environ.get("AURIX_NO_AUTOAPP") != "1" else None
