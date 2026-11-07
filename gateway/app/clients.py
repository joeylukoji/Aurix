from __future__ import annotations

import uuid

import httpx


class ChainError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


class ChainClient:
    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None):
        self.http = httpx.Client(base_url=base_url, timeout=20, transport=transport)

    def _get(self, path: str, **params):
        r = self.http.get(path, params=params or None)
        if r.status_code >= 400:
            raise ChainError(r.status_code, r.json().get("error", r.text))
        return r.json()

    def status(self) -> dict: return self._get("/status")
    def account(self, addr: str) -> dict: return self._get(f"/accounts/{addr}")
    def blocks(self, limit: int = 50) -> list: return self._get("/blocks", limit=limit)
    def escrows(self) -> list: return self._get("/escrows")
    def escrow(self, i: int) -> dict: return self._get(f"/escrows/{i}")
    def proof(self, h: str) -> dict: return self._get(f"/proofs/{h}")
    def tx(self, h: str) -> dict: return self._get(f"/txs/{h}")

    def broadcast(self, tx: dict) -> dict:
        r = self.http.post("/txs", json=tx)
        body = r.json()
        if r.status_code >= 400:
            raise ChainError(r.status_code, body.get("error") or body.get("log") or r.text)
        return body


class GuardianClient:
    """Advisory only: any failure returns None and never blocks a payment."""

    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None):
        self.http = httpx.Client(base_url=base_url, timeout=3, transport=transport)

    def score(self, **body) -> dict | None:
        try:
            r = self.http.post("/v1/risk/score", json=body)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError):
            return None


class PayPalClient:
    """PayPal REST (Orders v2 + webhook signature verification)."""

    mock = False

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
        r.raise_for_status()
        return r.json()

    def capture(self, order_id: str) -> dict:
        r = self.http.post(f"/v2/checkout/orders/{order_id}/capture", headers=self._headers())
        r.raise_for_status()
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


class MockPayPalClient:
    """Offline stand-in used when no Sandbox credentials are configured.

    Clearly flagged as mock in API responses. Webhooks are simulated internally
    after capture; external webhooks are always rejected in mock mode.
    """

    mock = True

    def create_order(self, amount_usd: str, description: str) -> dict:
        oid = "MOCK-" + uuid.uuid4().hex[:12].upper()
        return {"id": oid, "status": "CREATED",
                "links": [{"rel": "approve", "href": f"https://www.sandbox.paypal.com/checkoutnow?token={oid}"}]}

    def capture(self, order_id: str) -> dict:
        cid = "MOCKCAP-" + uuid.uuid4().hex[:10].upper()
        return {"id": order_id, "status": "COMPLETED",
                "purchase_units": [{"payments": {"captures": [{"id": cid, "status": "COMPLETED"}]}}]}

    def verify_webhook(self, headers: dict, event: dict) -> bool:
        return False
