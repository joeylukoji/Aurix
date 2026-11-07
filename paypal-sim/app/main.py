"""PayPal Sandbox simulator — FOR LOCAL TESTS ONLY, to be deleted once the real
PayPal Sandbox is integrated (the gateway then only changes PAYPAL_BASE_URL and
credentials).

Mimics the subset of the PayPal REST API that the AURIX gateway uses:
  POST /v1/oauth2/token                              client-credentials OAuth
  POST /v2/checkout/orders                           create order (intent CAPTURE)
  GET  /v2/checkout/orders/{id}                      order details
  GET  /checkoutnow?token={id}                       buyer approval page (HTML)
  POST /v2/checkout/orders/{id}/capture              capture (only once APPROVED)
  POST /v1/notifications/verify-webhook-signature    verify a delivered webhook
and delivers signed PAYMENT.CAPTURE.COMPLETED webhooks to SIM_WEBHOOK_URL.

Simulator-only helpers live under /sim (approve without a browser, replay a
webhook, list events) to test idempotency and signature checks.
"""
from __future__ import annotations

import base64
import hashlib
import html
import hmac
import json
import os
import secrets
import threading
import time
import uuid
import zlib
from urllib.parse import urlencode
from datetime import datetime, timezone

import httpx
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

CLIENT_ID = os.environ.get("SIM_CLIENT_ID", "sim-client-id")
CLIENT_SECRET = os.environ.get("SIM_CLIENT_SECRET", "sim-client-secret")
WEBHOOK_ID = os.environ.get("SIM_WEBHOOK_ID", "SIM-WEBHOOK-ID")
WEBHOOK_URL = os.environ.get("SIM_WEBHOOK_URL", "http://localhost:8080/api/paypal/webhook")
PUBLIC_URL = os.environ.get("SIM_PUBLIC_URL", "http://localhost:8090").rstrip("/")
WEBHOOK_DELAY = float(os.environ.get("SIM_WEBHOOK_DELAY", "0.5"))
# Test failure mode: captures above this USD amount fail with INSTRUMENT_DECLINED (unset = never).
DECLINE_ABOVE = float(os.environ["SIM_DECLINE_ABOVE"]) if os.environ.get("SIM_DECLINE_ABOVE") else None
_SIGNING_KEY = secrets.token_bytes(32)  # stands in for PayPal's certificate private key

app = FastAPI(title="PayPal Sandbox simulator (AURIX tests only)", version="0.1.0")
TOKENS: set[str] = set()
ORDERS: dict[str, dict] = {}
EVENTS: dict[str, dict] = {}  # event id -> {"event", "headers", "deliveries"}
lock = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def paypal_error(status: int, name: str, message: str, issue: str | None = None):
    detail = {"name": name, "message": message, "debug_id": uuid.uuid4().hex[:13]}
    if issue:
        detail["details"] = [{"issue": issue, "description": message}]
    raise HTTPException(status, detail)


@app.exception_handler(HTTPException)
async def _paypal_style_errors(_: Request, exc: HTTPException):
    from fastapi.responses import JSONResponse
    body = exc.detail if isinstance(exc.detail, dict) else {"name": "ERROR", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=body)


# ---------------- OAuth ----------------
@app.post("/v1/oauth2/token")
async def token(request: Request, grant_type: str = Form(...)):
    auth = request.headers.get("authorization", "")
    try:
        cid, secret = base64.b64decode(auth.removeprefix("Basic ")).decode().split(":", 1)
    except Exception:
        cid = secret = ""
    if cid != CLIENT_ID or secret != CLIENT_SECRET or grant_type != "client_credentials":
        paypal_error(401, "invalid_client", "Client Authentication failed")
    t = "A21AA" + secrets.token_urlsafe(24)
    TOKENS.add(t)
    return {"scope": "https://uri.paypal.com/services/payments/payment", "access_token": t,
            "token_type": "Bearer", "app_id": "APP-SIM", "expires_in": 32400, "nonce": uuid.uuid4().hex}


def bearer(authorization: str = Header(default="")) -> str:
    t = authorization.removeprefix("Bearer ")
    if t not in TOKENS:
        paypal_error(401, "AUTHENTICATION_FAILURE", "Authentication failed due to invalid authentication credentials")
    return t


# ---------------- Orders v2 ----------------
def order_view(o: dict) -> dict:
    links = [{"href": f"{PUBLIC_URL}/v2/checkout/orders/{o['id']}", "rel": "self", "method": "GET"}]
    if o["status"] == "CREATED":
        links.append({"href": f"{PUBLIC_URL}/checkoutnow?token={o['id']}", "rel": "approve", "method": "GET"})
    if o["status"] == "APPROVED":
        links.append({"href": f"{PUBLIC_URL}/v2/checkout/orders/{o['id']}/capture", "rel": "capture", "method": "POST"})
    v = {k: o[k] for k in ("id", "intent", "status", "purchase_units", "create_time")} | {"links": links}
    if o.get("payer"):
        v["payer"] = o["payer"]
    return v


@app.post("/v2/checkout/orders", status_code=201)
async def create_order(request: Request, _: str = Depends(bearer),
                       paypal_request_id: str | None = Header(default=None)):
    body = await request.json()
    with lock:
        if paypal_request_id:  # PayPal-Request-Id makes creation idempotent
            for o in ORDERS.values():
                if o.get("request_id") == paypal_request_id:
                    return order_view(o)
        if body.get("intent") != "CAPTURE":
            paypal_error(422, "UNPROCESSABLE_ENTITY", "Only intent CAPTURE is simulated", "INVALID_PARAMETER_VALUE")
        units = body.get("purchase_units") or []
        try:
            amount = units[0]["amount"]
            if amount["currency_code"] != "USD" or float(amount["value"]) <= 0:
                raise ValueError
        except (KeyError, IndexError, ValueError):
            paypal_error(400, "INVALID_REQUEST", "Invalid purchase_units amount", "INVALID_PARAMETER_VALUE")
        ctx = body.get("application_context") or {}
        ctx = ctx | ((body.get("payment_source") or {}).get("paypal") or {}).get("experience_context", {})
        oid = secrets.token_hex(8).upper()[:17]
        ORDERS[oid] = {"return_url": ctx.get("return_url"), "cancel_url": ctx.get("cancel_url"),"id": oid, "intent": "CAPTURE", "status": "CREATED", "create_time": now(),
                       "purchase_units": [{"reference_id": "default", **units[0]}], "request_id": paypal_request_id}
        return order_view(ORDERS[oid])


def get_or_404(oid: str) -> dict:
    o = ORDERS.get(oid)
    if not o:
        paypal_error(404, "RESOURCE_NOT_FOUND", "The specified resource does not exist.", "INVALID_RESOURCE_ID")
    return o


@app.get("/v2/checkout/orders/{oid}")
def get_order(oid: str, _: str = Depends(bearer)):
    return order_view(get_or_404(oid))


def approve(oid: str) -> dict:
    with lock:
        o = get_or_404(oid)
        if o["status"] == "CREATED":
            o["status"] = "APPROVED"
            o["payer"] = {"payer_id": "SIMBUYER" + secrets.token_hex(3).upper(),
                          "email_address": "sb-buyer@personal.example.com", "name": {"given_name": "Sim", "surname": "Buyer"}}
        return o


@app.get("/checkoutnow", response_class=HTMLResponse)
def checkout_page(token: str):
    o = get_or_404(token)
    amt = o["purchase_units"][0]["amount"]
    done = o["status"] != "CREATED"
    if o.get("payer_cancelled") and not done:
        msg = "<p>You cancelled this payment. You can still pay below.</p>"
    else:
        msg = ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>PayPal Simulator</title>
<style>body{{font:16px system-ui;background:#f5f7fa;margin:0;display:grid;place-items:center;min-height:100vh}}
.c{{background:#fff;border-radius:12px;padding:24px;max-width:380px;width:calc(100% - 32px);box-shadow:0 4px 20px #0001}}
.b{{background:#ffc439;border:0;border-radius:24px;padding:12px;width:100%;font-weight:700;font-size:16px;cursor:pointer}}
.l{{background:none;border:0;color:#0070ba;margin-top:12px;width:100%;cursor:pointer;font-size:14px}}
.w{{background:#fff3cd;border-radius:6px;padding:8px;font-size:13px}}</style></head><body><div class="c">
<p class="w">⚠️ Local PayPal <b>simulator</b> for AURIX tests. No real money.</p>
<h2>Pay {amt['value']} {amt['currency_code']}</h2><p>Order <code>{o['id']}</code> · status <b id="st">{o['status']}</b></p>
{"<p>Payment approved. You can close this tab and return to AURIX.</p>" if done else msg +
 f'<form method="post" action="/checkoutnow/{o["id"]}/approve"><p>Logged in as sb-buyer@personal.example.com</p><button class="b" id="pay">Pay now</button></form>'
 f'<form method="post" action="/checkoutnow/{o["id"]}/cancel"><button class="l" id="cancel">Cancel and return to merchant</button></form>'}
</div></body></html>"""


@app.post("/checkoutnow/{oid}/approve")
def checkout_approve(oid: str):
    o = approve(oid)
    if o.get("return_url"):  # like PayPal: return_url?token=<order id>&PayerID=<payer id>
        q = urlencode({"token": oid, "PayerID": o["payer"]["payer_id"]})
        return RedirectResponse(_with_query(o["return_url"], q), status_code=303)
    return RedirectResponse(f"/checkoutnow?token={oid}", status_code=303)


def _with_query(url: str, q: str) -> str:
    return url + ("&" if "?" in url else "?") + q


@app.post("/checkoutnow/{oid}/cancel")
def checkout_cancel(oid: str):
    """Buyer clicks "Cancel and return": the order stays CREATED (not capturable), like PayPal."""
    with lock:
        o = get_or_404(oid)
        if o["status"] == "CREATED":
            o["payer_cancelled"] = True
    if o.get("cancel_url"):  # like PayPal: cancel_url?token=<order id>
        return RedirectResponse(_with_query(o["cancel_url"], urlencode({"token": oid})), status_code=303)
    return RedirectResponse(f"/checkoutnow?token={oid}", status_code=303)


@app.post("/v2/checkout/orders/{oid}/capture", status_code=201)
def capture(oid: str, _: str = Depends(bearer)):
    with lock:
        o = get_or_404(oid)
        if o["status"] == "COMPLETED":
            paypal_error(422, "UNPROCESSABLE_ENTITY", "Order already captured.", "ORDER_ALREADY_CAPTURED")
        if o["status"] != "APPROVED":
            paypal_error(422, "UNPROCESSABLE_ENTITY", "Payer has not yet approved the Order for payment.",
                         "ORDER_NOT_APPROVED")
        amount = o["purchase_units"][0]["amount"]
        if DECLINE_ABOVE is not None and float(amount["value"]) > DECLINE_ABOVE:
            o["declined"] = True  # order stays APPROVED, no capture, no webhook
            paypal_error(422, "UNPROCESSABLE_ENTITY",
                         "The instrument presented was either declined by the processor or bank, or it can't be "
                         "used for this payment.", "INSTRUMENT_DECLINED")
        cap = {"id": secrets.token_hex(9).upper()[:17], "status": "COMPLETED", "amount": amount,
               "final_capture": True, "create_time": now(),
               "supplementary_data": {"related_ids": {"order_id": oid}}}
        o["status"] = "COMPLETED"
        o["purchase_units"][0]["payments"] = {"captures": [cap]}
    event = {"id": "WH-" + uuid.uuid4().hex[:20].upper(), "event_version": "1.0", "create_time": now(),
             "resource_type": "capture", "event_type": "PAYMENT.CAPTURE.COMPLETED",
             "summary": f"Payment completed for $ {amount['value']} {amount['currency_code']}", "resource": cap}
    threading.Thread(target=_deliver_later, args=(event,), daemon=True).start()
    return order_view(o)


# ---------------- Webhooks ----------------
def _sig_input(tid: str, ttime: str, webhook_id: str, event: dict) -> bytes:
    body = json.dumps(event, separators=(",", ":"), sort_keys=True).encode()
    return f"{tid}|{ttime}|{webhook_id}|{zlib.crc32(body)}".encode()


def sign_event(event: dict) -> dict:
    tid, ttime = str(uuid.uuid4()), now()
    sig = hmac.new(_SIGNING_KEY, _sig_input(tid, ttime, WEBHOOK_ID, event), hashlib.sha256).digest()
    return {"PAYPAL-TRANSMISSION-ID": tid, "PAYPAL-TRANSMISSION-TIME": ttime,
            "PAYPAL-TRANSMISSION-SIG": base64.b64encode(sig).decode(), "PAYPAL-AUTH-ALGO": "SHA256withRSA",
            "PAYPAL-CERT-URL": f"{PUBLIC_URL}/v1/notifications/certs/CERT-SIM"}


def deliver(event_id: str) -> dict:
    rec = EVENTS[event_id]
    try:
        r = httpx.post(WEBHOOK_URL, json=rec["event"], headers=rec["headers"], timeout=10)
        status, body = r.status_code, r.text[:300]
    except httpx.HTTPError as e:
        status, body = 0, str(e)
    rec["deliveries"].append({"time": now(), "status": status, "response": body})
    return rec["deliveries"][-1]


def _deliver_later(event: dict):
    EVENTS[event["id"]] = {"event": event, "headers": sign_event(event), "deliveries": []}
    time.sleep(WEBHOOK_DELAY)
    for attempt in range(3):  # PayPal retries failed deliveries
        if 200 <= deliver(event["id"])["status"] < 300:
            return
        time.sleep(1 + attempt)


@app.post("/v1/notifications/verify-webhook-signature")
async def verify(request: Request, _: str = Depends(bearer)):
    b = await request.json()
    try:
        expected = hmac.new(_SIGNING_KEY, _sig_input(b["transmission_id"], b["transmission_time"], b["webhook_id"],
                                                     b["webhook_event"]), hashlib.sha256).digest()
        ok = (b["webhook_id"] == WEBHOOK_ID and b["auth_algo"] == "SHA256withRSA"
              and b["cert_url"].startswith(PUBLIC_URL)
              and hmac.compare_digest(expected, base64.b64decode(b["transmission_sig"])))
    except (KeyError, ValueError, TypeError):
        ok = False
    return {"verification_status": "SUCCESS" if ok else "FAILURE"}


# ---------------- simulator-only helpers ----------------
@app.get("/sim/health")
def health():
    return {"status": "ok", "simulator": True, "webhook_url": WEBHOOK_URL, "orders": len(ORDERS), "events": len(EVENTS)}


@app.get("/sim", response_class=HTMLResponse)
def sim_dashboard():
    """Tiny demo dashboard: orders, statuses and webhook deliveries."""
    e = lambda v: html.escape(str(v))
    by_order: dict[str, list] = {}
    for eid, rec in list(EVENTS.items()):
        by_order.setdefault(rec["event"]["resource"]["supplementary_data"]["related_ids"]["order_id"], []).append((eid, rec))
    rows = []
    for o in list(ORDERS.values())[::-1]:
        flags = " (payer cancelled)" if o.get("payer_cancelled") and o["status"] == "CREATED" else ""
        flags += " (declined)" if o.get("declined") and o["status"] != "COMPLETED" else ""
        hooks = "<br>".join(f"{e(eid)}: " + (", ".join(f"{e(d['status'])}" for d in rec["deliveries"]) or "pending")
                            for eid, rec in by_order.get(o["id"], [])) or "-"
        amt = o["purchase_units"][0]["amount"]
        rows.append(f"<tr><td><a href='/checkoutnow?token={e(o['id'])}'>{e(o['id'])}</a></td><td>{e(o['create_time'])}</td>"
                    f"<td>{e(amt['value'])} {e(amt['currency_code'])}</td><td>{e(o['status'])}{flags}</td><td>{hooks}</td></tr>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta http-equiv="refresh" content="3">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>PayPal Simulator</title>
<style>body{{font:14px system-ui;margin:16px}}table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #ddd;padding:6px;text-align:left}}</style></head><body>
<h2>PayPal simulator dashboard</h2><p>Webhook URL: <code>{e(WEBHOOK_URL)}</code> · decline above:
{e(DECLINE_ABOVE if DECLINE_ABOVE is not None else "off")} · auto-refresh 3s</p>
<table><tr><th>Order</th><th>Created</th><th>Amount</th><th>Status</th><th>Webhook deliveries (HTTP status)</th></tr>
{"".join(rows) or "<tr><td colspan=5>No orders yet.</td></tr>"}</table></body></html>"""


@app.post("/sim/orders/{oid}/approve")
def sim_approve(oid: str):
    """Approve as the buyer without the browser page."""
    return order_view(approve(oid))


@app.get("/sim/events")
def sim_events():
    return [{"id": k, "event_type": v["event"]["event_type"], "order_id":
             v["event"]["resource"]["supplementary_data"]["related_ids"]["order_id"], "deliveries": v["deliveries"]}
            for k, v in EVENTS.items()]


@app.post("/sim/events/{event_id}/resend")
def sim_resend(event_id: str):
    """Re-deliver the exact same signed webhook (tests idempotency)."""
    if event_id not in EVENTS:
        raise HTTPException(404, "unknown event")
    return deliver(event_id)


@app.post("/sim/events/{event_id}/forge")
def sim_forge(event_id: str):
    """Deliver a tampered copy (amount changed, signature kept) to test signature checks."""
    if event_id not in EVENTS:
        raise HTTPException(404, "unknown event")
    rec = EVENTS[event_id]
    forged = json.loads(json.dumps(rec["event"]))
    forged["id"] = "WH-FORGED-" + uuid.uuid4().hex[:8].upper()
    forged["resource"]["amount"]["value"] = "0.01"
    try:
        r = httpx.post(WEBHOOK_URL, json=forged, headers=rec["headers"], timeout=10)
        return {"status": r.status_code, "response": r.text[:300]}
    except httpx.HTTPError as e:
        return {"status": 0, "response": str(e)}
