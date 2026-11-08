"""Authorize now, capture (or void) later: the PayPal flow behind the MVP 0.2 evidence escrow."""
from tests.test_sim import c, delivered, token, wait_for  # noqa: F401  (fixture re-export)


def auth_order(h, value="120.00"):
    o = c.post("/v2/checkout/orders", headers=h, json={
        "intent": "AUTHORIZE", "purchase_units": [{"amount": {"currency_code": "USD", "value": value}}]}).json()
    assert o["intent"] == "AUTHORIZE"
    c.post(f"/sim/orders/{o['id']}/approve")
    r = c.post(f"/v2/checkout/orders/{o['id']}/authorize", headers=h)
    assert r.status_code == 201, r.text
    return o["id"], r.json()["purchase_units"][0]["payments"]["authorizations"][0]


def types(got):
    return [e["event_type"] for e, _ in got]


def test_authorize_then_capture(delivered):
    h = token()
    oid, a = auth_order(h)
    assert a["status"] == "CREATED" and a["amount"] == {"currency_code": "USD", "value": "120.00"}
    assert "_expires" not in a and a["expiration_time"]
    wait_for(lambda: "PAYMENT.AUTHORIZATION.CREATED" in types(delivered))
    r = c.post(f"/v2/payments/authorizations/{a['id']}/capture", headers=h | {"PayPal-Request-Id": "cap-1"})
    assert r.status_code == 201 and r.json()["status"] == "COMPLETED"
    cap = r.json()
    assert cap["supplementary_data"]["related_ids"] == {"order_id": oid, "authorization_id": a["id"]}
    again = c.post(f"/v2/payments/authorizations/{a['id']}/capture", headers=h | {"PayPal-Request-Id": "cap-1"})
    assert again.json()["id"] == cap["id"]  # idempotent retry
    other = c.post(f"/v2/payments/authorizations/{a['id']}/capture", headers=h | {"PayPal-Request-Id": "cap-2"})
    assert other.status_code == 422 and "AUTHORIZATION_ALREADY_CAPTURED" in other.text
    wait_for(lambda: "PAYMENT.CAPTURE.COMPLETED" in types(delivered))
    assert c.get(f"/v2/payments/authorizations/{a['id']}", headers=h).json()["status"] == "CAPTURED"
    assert c.post(f"/v2/payments/authorizations/{a['id']}/void", headers=h).status_code == 422


def test_void_releases_funds(delivered):
    h = token()
    _, a = auth_order(h)
    assert c.post(f"/v2/payments/authorizations/{a['id']}/void", headers=h).status_code == 204
    assert c.post(f"/v2/payments/authorizations/{a['id']}/void", headers=h).status_code == 204  # idempotent
    wait_for(lambda: "PAYMENT.AUTHORIZATION.VOIDED" in types(delivered))
    r = c.post(f"/v2/payments/authorizations/{a['id']}/capture", headers=h)
    assert r.status_code == 422 and "AUTHORIZATION_VOIDED" in r.text


def test_expired_authorization_cannot_be_captured(delivered):
    h = token()
    _, a = auth_order(h)
    assert c.post(f"/sim/authorizations/{a['id']}/expire").json()["status"] == "EXPIRED"
    r = c.post(f"/v2/payments/authorizations/{a['id']}/capture", headers=h)
    assert r.status_code == 422 and "AUTHORIZATION_EXPIRED" in r.text


def test_intent_mismatch_is_refused(delivered):
    h = token()
    oid, _ = auth_order(h)
    r = c.post(f"/v2/checkout/orders/{oid}/capture", headers=h)
    assert r.status_code == 422 and "ACTION_DOES_NOT_MATCH_INTENT" in r.text
    cap = c.post("/v2/checkout/orders", headers=h, json={
        "intent": "CAPTURE", "purchase_units": [{"amount": {"currency_code": "USD", "value": "5.00"}}]}).json()
    c.post(f"/sim/orders/{cap['id']}/approve")
    assert c.post(f"/v2/checkout/orders/{cap['id']}/authorize", headers=h).status_code == 422


def test_authorize_requires_approval(delivered):
    h = token()
    o = c.post("/v2/checkout/orders", headers=h, json={
        "intent": "AUTHORIZE", "purchase_units": [{"amount": {"currency_code": "USD", "value": "9.00"}}]}).json()
    r = c.post(f"/v2/checkout/orders/{o['id']}/authorize", headers=h)
    assert r.status_code == 422 and "ORDER_NOT_APPROVED" in r.text
