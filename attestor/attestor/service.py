"""Attestor loop: pending proofs -> independent PayPal check -> MsgAnchorProof."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from . import crypto, keys

log = logging.getLogger("attestor")


@dataclass
class Config:
    seed: str = ""
    mnemonic: str = ""
    key_file: str = ""
    aurix_env: str = ""
    chain_rest: str = "http://localhost:1317"
    chain_id: str = "aurix-testnet-1"
    paypal_base_url: str = "http://localhost:8090"
    paypal_client_id: str = "sim-client-id"
    paypal_secret: str = "sim-client-secret"
    poll_interval: float = 5.0
    port: int = 8095

    @classmethod
    def from_env(cls, env=os.environ) -> "Config":
        return cls(seed=env.get("ATTESTOR_SEED", ""),
                   mnemonic=env.get("ATTESTOR_MNEMONIC", ""),
                   key_file=env.get("ATTESTOR_KEY_FILE", ""),
                   aurix_env=env.get("AURIX_ENV", ""),
                   chain_rest=env.get("CHAIN_REST", cls.chain_rest),
                   chain_id=env.get("CHAIN_ID", cls.chain_id),
                   paypal_base_url=env.get("PAYPAL_BASE_URL", cls.paypal_base_url),
                   paypal_client_id=env.get("PAYPAL_CLIENT_ID", cls.paypal_client_id),
                   paypal_secret=env.get("PAYPAL_CLIENT_SECRET", env.get("PAYPAL_SECRET", cls.paypal_secret)),
                   poll_interval=float(env.get("POLL_INTERVAL", cls.poll_interval)),
                   port=int(env.get("ATTESTOR_PORT", cls.port)))


class Refused(Exception):
    """The capture does not justify an attestation."""


class PayPal:
    """Read-only PayPal Payments v2 client with client-credentials OAuth (as in the gateway)."""

    def __init__(self, base_url: str, client_id: str, secret: str, transport: httpx.BaseTransport | None = None):
        self.http = httpx.Client(base_url=base_url, timeout=20, transport=transport)
        self.auth = (client_id, secret)
        self._token: str | None = None

    def _headers(self) -> dict:
        if not self._token:
            r = self.http.post("/v1/oauth2/token", auth=self.auth, data={"grant_type": "client_credentials"})
            r.raise_for_status()
            self._token = r.json()["access_token"]
        return {"Authorization": f"Bearer {self._token}"}

    def capture(self, capture_id: str) -> dict:
        r = self.http.get(f"/v2/payments/captures/{capture_id}", headers=self._headers())
        if r.status_code == 401:  # token expired: retry once with a fresh one
            self._token = None
            r = self.http.get(f"/v2/payments/captures/{capture_id}", headers=self._headers())
        if r.status_code == 404:
            raise Refused(f"capture {capture_id} not found at PayPal")
        r.raise_for_status()
        return r.json()


def proof_hash(order_id: str, capture_id: str) -> str:
    return hashlib.sha256(f"{order_id}|{capture_id}".encode()).hexdigest()


def normalize_amount(value) -> str:
    """Decimal string normalised to 2 places ("12.5" -> "12.50"); raises Refused if not a positive
    amount with at most 2 decimals."""
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise Refused(f"invalid amount {value!r}") from None
    if not d.is_finite() or d <= 0 or d != d.quantize(Decimal("0.01")):
        raise Refused(f"invalid amount {value!r}")
    return str(d.quantize(Decimal("0.01")))


def verify_capture(proof: dict, cap: dict) -> None:
    """Raises Refused unless the PayPal capture backs this proof."""
    h, order_ref, cap_ref = proof.get("hash", ""), proof.get("external_ref", ""), proof.get("capture_ref", "")
    if cap.get("id") != cap_ref:
        raise Refused(f"capture id {cap.get('id')!r} != capture_ref {cap_ref!r}")
    if cap.get("status") != "COMPLETED":
        raise Refused(f"capture status {cap.get('status')!r} is not COMPLETED")
    p_amount, p_currency = (proof.get("amount") or "").strip(), (proof.get("currency") or "").strip()
    if not p_amount or not p_currency:
        raise Refused("proof has no amount/currency")
    try:
        c_value, c_currency = cap["amount"]["value"], cap["amount"]["currency_code"]
    except (KeyError, TypeError):
        raise Refused(f"capture amount invalid: {cap.get('amount')!r}") from None
    try:
        c_norm = normalize_amount(c_value)
    except Refused:
        raise Refused(f"capture amount invalid: {cap.get('amount')!r}") from None
    if c_norm != normalize_amount(p_amount):
        raise Refused(f"capture amount {c_value!r} != proof amount {p_amount!r}")
    if c_currency != p_currency:
        raise Refused(f"capture currency {c_currency!r} != proof currency {p_currency!r}")
    order_id = ((cap.get("supplementary_data") or {}).get("related_ids") or {}).get("order_id")
    # The gateway stores external_ref as "paypal:<order_id>"; accept both forms.
    if order_id is None or order_id not in (order_ref, order_ref.removeprefix("paypal:")):
        raise Refused(f"capture order id {order_id!r} != external_ref {order_ref!r}")
    if proof_hash(order_id, cap_ref) != h.lower():
        raise Refused(f"sha256(order_id|capture_id) != proof hash {h}")


class Attestor:
    def __init__(self, cfg: Config, transport: httpx.BaseTransport | None = None,
                 paypal_transport: httpx.BaseTransport | None = None):
        self.cfg = cfg
        self.key = keys.load_key(cfg.mnemonic, cfg.key_file, cfg.seed, cfg.aurix_env)
        self.address = self.key.address
        self.chain = httpx.Client(base_url=cfg.chain_rest, timeout=20, transport=transport)
        self.paypal = PayPal(cfg.paypal_base_url, cfg.paypal_client_id, cfg.paypal_secret,
                             transport=paypal_transport or transport)
        self.attested: set[str] = set()
        self.refused: dict[str, str] = {}
        self.last_error: str | None = None
        self.last_poll: float | None = None
        self._seq: tuple[int, int] | None = None  # (account_number, next sequence), reset each cycle

    # ---------- chain ----------
    def pending_proofs(self) -> list[dict]:
        out, key = [], None
        while True:
            params = {"pagination.key": key} if key else None
            r = self.chain.get("/aurix/smartmoney/v1/proofs_pending", params=params)
            r.raise_for_status()
            body = r.json()
            out += body.get("proofs") or []
            key = (body.get("pagination") or {}).get("next_key")
            if not key:
                return out

    def account(self) -> tuple[int, int]:
        r = self.chain.get(f"/cosmos/auth/v1beta1/account_info/{self.address}")
        r.raise_for_status()
        info = r.json()["info"]
        return int(info.get("account_number", 0)), int(info.get("sequence", 0))

    def broadcast_anchor(self, proof: dict) -> str:
        if self._seq is None:
            self._seq = self.account()
        num, seq = self._seq
        msg = crypto.msg_anchor_proof(self.address, proof["hash"], proof["external_ref"], proof["capture_ref"],
                                     proof.get("amount", ""), proof.get("currency", ""))
        tx = crypto.build_tx(self.key, [msg], self.cfg.chain_id, num, seq)
        r = self.chain.post("/cosmos/tx/v1beta1/txs", json={"tx_bytes": base64.b64encode(tx).decode(),
                                                          "mode": "BROADCAST_MODE_SYNC"})
        r.raise_for_status()
        resp = r.json()["tx_response"]
        code = int(resp.get("code", 0))
        if code != 0:
            self._seq = None  # refetch (sequence mismatch etc.)
            raw = resp.get("raw_log", "")
            if "duplicate" in raw.lower():
                self.attested.add(proof["hash"])
            raise RuntimeError(f"broadcast rejected [{resp.get('codespace', 'sdk')}:{code}] {raw}")
        self._seq = (num, seq + 1)
        return resp["txhash"]

    # ---------- loop ----------
    def process(self, proof: dict) -> str:
        """Returns 'attested', 'skipped' or 'refused'."""
        h = proof.get("hash", "")
        if h in self.attested or self.address in (proof.get("attestations") or []):
            self.attested.add(h)
            return "skipped"
        try:
            if not proof.get("capture_ref"):
                raise Refused("proof has no capture_ref")
            verify_capture(proof, self.paypal.capture(proof["capture_ref"]))
        except Refused as e:
            if self.refused.get(h) != str(e):
                log.warning("refusing proof %s: %s", h, e)
            self.refused[h] = str(e)
            return "refused"
        txhash = self.broadcast_anchor(proof)
        self.attested.add(h)
        self.refused.pop(h, None)
        log.info("attested proof %s (order %s, capture %s) tx %s", h, proof["external_ref"], proof["capture_ref"], txhash)
        return "attested"

    def run_once(self) -> dict:
        self._seq = None
        results: dict[str, str] = {}
        try:
            for p in self.pending_proofs():
                try:
                    results[p.get("hash", "")] = self.process(p)
                except Exception as e:  # noqa: BLE001 - one bad proof must not stop the others
                    self.last_error = f"{p.get('hash')}: {e}"
                    log.error("error on proof %s: %s", p.get("hash"), e)
                    results[p.get("hash", "")] = "error"
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            log.error("poll failed: %s", e)
        self.last_poll = time.time()
        return results

    def health(self) -> dict:
        return {"status": "ok", "address": self.address, "attested": len(self.attested),
                "refused": len(self.refused), "last_error": self.last_error, "last_poll": self.last_poll}

    def serve_health(self) -> ThreadingHTTPServer:
        att = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path.rstrip("/") not in ("/health", ""):
                    self.send_error(404)
                    return
                body = json.dumps(att.health()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        srv = ThreadingHTTPServer(("0.0.0.0", self.cfg.port), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    def run_forever(self, stop: threading.Event | None = None):
        stop = stop or threading.Event()
        log.info("attestor %s polling %s every %ss", self.address, self.cfg.chain_rest, self.cfg.poll_interval)
        while not stop.is_set():
            self.run_once()
            stop.wait(self.cfg.poll_interval)
