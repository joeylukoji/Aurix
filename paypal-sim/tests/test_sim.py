import base64
import time

import pytest
from fastapi.testclient import TestClient

from app import main

c = TestClient(main.app)
AUTH = "Basic " + base64.b64encode(f"{main.CLIENT_ID}:{main.CLIENT_SECRET}".encode()).decode()


@pytest.fixture
def delivered(monkeypatch):
    """Captures webhook deliveries instead of POSTing to the gateway."""
    got = []

    class R:
        status_code, text = 200, "ok"

    monkeypatch.setattr(main.httpx, "post", lambda url, json, headers, timeout: got.append((json, headers)) or R())
    monkeypatch.setattr(main, "WEBHOOK_DELAY", 0)
    return got


def token():
    r = c.post("/v1/oauth2/token", data={"grant_type": "client_credentials"}, headers={"Authorization": AUTH})
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def create(h, value="12.50", req_id=None):
    hdr = h | ({"PayPal-Request-Id": req_id} if req_id else {})
    return c.post("/v2/checkout/orders", headers=hdr, json={
        "intent": "CAPTURE", "purchase_units": [{"amount": {"currency_code": "USD", "value": value}}]})


def wait_for(cond, timeout=3):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_oauth_required():
    assert c.post("/v1/oauth2/token", data={"grant_type": "client_credentials"},
                  headers={"Authorization": "Basic eDp5"}).status_code == 401
    assert c.post("/v2/checkout/orders", json={}).status_code == 401


def test_full_flow_with_signed_webhook(delivered):
    h = token()
    o = create(h).json()
    assert o["status"] == "CREATED"
    approve = next(l["href"] for l in o["links"] if l["rel"] == "approve")
    # capture before buyer approval is refused, like PayPal
    r = c.post(f"/v2/checkout/orders/{o['id']}/capture", headers=h)
    assert r.status_code == 422 and r.json()["details"][0]["issue"] == "ORDER_NOT_APPROVED"
    # the buyer approves on the checkout page
    assert "Pay now" in c.get(approve.replace(main.PUBLIC_URL, "")).text
    c.post(f"/checkoutnow/{o['id']}/approve")
    cap = c.post(f"/v2/checkout/orders/{o['id']}/capture", headers=h)
    assert cap.status_code == 201 and cap.json()["status"] == "COMPLETED"
    assert c.post(f"/v2/checkout/orders/{o['id']}/capture", headers=h).status_code == 422  # no double capture
    assert wait_for(lambda: delivered)
    event, headers = delivered[0]
    assert event["event_type"] == "PAYMENT.CAPTURE.COMPLETED"
    assert event["resource"]["supplementary_data"]["related_ids"]["order_id"] == o["id"]
    body = {"auth_algo": headers["PAYPAL-AUTH-ALGO"], "cert_url": headers["PAYPAL-CERT-URL"],
            "transmission_id": headers["PAYPAL-TRANSMISSION-ID"], "transmission_sig": headers["PAYPAL-TRANSMISSION-SIG"],
            "transmission_time": headers["PAYPAL-TRANSMISSION-TIME"], "webhook_id": main.WEBHOOK_ID, "webhook_event": event}
    assert c.post("/v1/notifications/verify-webhook-signature", headers=h, json=body).json()["verification_status"] == "SUCCESS"
    tampered = body | {"webhook_event": event | {"summary": "tampered"}}
    assert c.post("/v1/notifications/verify-webhook-signature", headers=h, json=tampered).json()["verification_status"] == "FAILURE"
    wrong_id = body | {"webhook_id": "OTHER"}
    assert c.post("/v1/notifications/verify-webhook-signature", headers=h, json=wrong_id).json()["verification_status"] == "FAILURE"
    # replay helper re-sends the identical signed event
    c.post(f"/sim/events/{event['id']}/resend")
    assert delivered[-1] == (event, headers)


def test_request_id_idempotent_and_validation():
    h = token()
    a, b = create(h, req_id="r-1").json(), create(h, req_id="r-1").json()
    assert a["id"] == b["id"]
    assert create(h, value="-3").status_code == 400
    assert c.get("/v2/checkout/orders/NOPE", headers=h).status_code == 404


def test_cancel_and_return_urls():
    h = token()
    o = c.post("/v2/checkout/orders", headers=h, json={
        "intent": "CAPTURE", "purchase_units": [{"amount": {"currency_code": "USD", "value": "5.00"}}],
        "payment_source": {"paypal": {"experience_context": {
            "return_url": "https://shop.example/ok", "cancel_url": "https://shop.example/no?x=1"}}}}).json()
    assert "Cancel and return" in c.get(f"/checkoutnow?token={o['id']}").text
    r = c.post(f"/checkoutnow/{o['id']}/cancel", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"https://shop.example/no?x=1&token={o['id']}"
    assert c.get(f"/v2/checkout/orders/{o['id']}", headers=h).json()["status"] == "CREATED"
    cap = c.post(f"/v2/checkout/orders/{o['id']}/capture", headers=h)
    assert cap.status_code == 422 and cap.json()["details"][0]["issue"] == "ORDER_NOT_APPROVED"
    # application_context works too, and Pay redirects to return_url with token + PayerID
    o2 = c.post("/v2/checkout/orders", headers=h, json={
        "intent": "CAPTURE", "purchase_units": [{"amount": {"currency_code": "USD", "value": "5.00"}}],
        "application_context": {"return_url": "https://shop.example/ok"}}).json()
    r = c.post(f"/checkoutnow/{o2['id']}/approve", follow_redirects=False)
    assert r.headers["location"].startswith(f"https://shop.example/ok?token={o2['id']}&PayerID=SIMBUYER")
    # without urls the page just reloads
    o3 = create(h).json()
    r = c.post(f"/checkoutnow/{o3['id']}/cancel", follow_redirects=False)
    assert r.headers["location"] == f"/checkoutnow?token={o3['id']}"


def test_decline_above(delivered, monkeypatch):
    monkeypatch.setattr(main, "DECLINE_ABOVE", 100.0)
    h = token()
    big, small = create(h, value="150.00").json(), create(h, value="99.00").json()
    for o in (big, small):
        c.post(f"/sim/orders/{o['id']}/approve")
    r = c.post(f"/v2/checkout/orders/{big['id']}/capture", headers=h)
    assert r.status_code == 422 and r.json()["details"][0]["issue"] == "INSTRUMENT_DECLINED"
    assert c.post(f"/v2/checkout/orders/{small['id']}/capture", headers=h).status_code == 201
    assert wait_for(lambda: delivered)
    time.sleep(0.1)
    assert [e["resource"]["supplementary_data"]["related_ids"]["order_id"] for e, _ in delivered] == [small["id"]]


def test_dashboard(delivered):
    h = token()
    o = create(h).json()
    c.post(f"/sim/orders/{o['id']}/approve")
    c.post(f"/v2/checkout/orders/{o['id']}/capture", headers=h)
    assert wait_for(lambda: delivered)
    page = c.get("/sim")
    assert page.status_code == 200 and o["id"] in page.text and "COMPLETED" in page.text


def test_get_capture_details(delivered):
    h = token()
    o = create(h, "7.25").json()
    c.post(f"/sim/orders/{o['id']}/approve")
    r = c.post(f"/v2/checkout/orders/{o['id']}/capture", headers=h)
    cap = r.json()["purchase_units"][0]["payments"]["captures"][0]
    r = c.get(f"/v2/payments/captures/{cap['id']}", headers=h)
    assert r.status_code == 200
    b = r.json()
    assert b["id"] == cap["id"] and b["status"] == "COMPLETED"
    assert b["amount"] == {"currency_code": "USD", "value": "7.25"}
    assert b["supplementary_data"]["related_ids"]["order_id"] == o["id"]


def test_capture_amount_consistent_and_normalised(delivered):
    h = token()
    o = create(h, "12.5").json()
    assert o["purchase_units"][0]["amount"] == {"currency_code": "USD", "value": "12.50"}
    c.post(f"/sim/orders/{o['id']}/approve")
    cap = c.post(f"/v2/checkout/orders/{o['id']}/capture", headers=h).json()["purchase_units"][0]
    cap = cap["payments"]["captures"][0]
    got = c.get(f"/v2/payments/captures/{cap['id']}", headers=h).json()
    order = c.get(f"/v2/checkout/orders/{o['id']}", headers=h).json()
    assert cap["amount"] == got["amount"] == order["purchase_units"][0]["amount"] == \
        {"currency_code": "USD", "value": "12.50"}
    assert wait_for(lambda: delivered)
    assert delivered[0][0]["resource"]["amount"] == got["amount"]
    for bad in ("1.005", "abc", "NaN"):
        assert create(h, bad).status_code == 400


def test_get_capture_requires_auth_and_404():
    h = token()
    assert c.get("/v2/payments/captures/NOPE", headers=h).status_code == 404
    assert c.get("/v2/payments/captures/NOPE").status_code == 401
