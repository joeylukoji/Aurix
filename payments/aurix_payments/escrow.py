"""Evidence-released USD escrow (MVP 0.2). Spec: docs/architecture/evidence-escrow.md.

PayPal holds the buyer's funds (intent AUTHORIZE). AURIX captures them only when signed evidence meets the
public rule `delivery-attested/1` (aurix_verify.rule), and voids the authorization when the deadline passes
without it. Every step becomes a signed attestation; the proof package can be re-verified offline with
`aurix-verify`, which replays the very same rule code.

AURIX never holds or moves USD itself, and never signs on PayPal's behalf: its attestations say "AURIX
received this PayPal event and verified it with method X".
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable, Mapping

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from aurix_common.clients import PayPalError
from aurix_common.keys import KeyConfigError, is_production
from aurix_verify import crypto
from aurix_verify.jcs import digest
from aurix_verify.package import build_package, read_zip, receipts_doc, verify_package

from .advisor import Advisor, facts
from aurix_verify.rule import HOLD, RELEASE, RULE_ID, VOID, WAIT, Evidence, decide

log = logging.getLogger("aurix.escrow")

DEMO_CARRIER = "aurix_demo_delivery"
MAX_DEADLINE_DAYS = 29  # lifetime of a PayPal authorization
FINAL = {RELEASE, VOID}
LANGS = {"French", "English", "Spanish", "Portuguese", "German", "Swahili"}  # advisor output languages
AUDIENCES = {"buyer", "merchant", "auditor"}
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # attestation ids become file names in the proof package
MAX_EVENT_ID = 200
ADVICE_PER_ORDER = 10        # advisor calls kept per order (oldest of the same kind is replaced)
ADVICE_MIN_INTERVAL = 5.0    # seconds between advisor calls for one order
ADVICE_PER_MINUTE = 30       # advisor calls per minute for the whole service


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _raw_hash(raw) -> str | None:
    """Hash of the provider payload as received (not canonicalised: it may contain any JSON)."""
    if raw is None:
        return None
    return "sha256:" + hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _dev_seed(label: str) -> bytes:
    return hashlib.sha256(label.encode()).digest()


def load_signer(env: Mapping[str, str] | None = None) -> crypto.Signer:
    """ESCROW_KEY_FILE (hex 32-byte Ed25519 seed) > ESCROW_SEED / built-in dev seed (refused in production)."""
    env = os.environ if env is None else env
    key_id = env.get("ESCROW_KEY_ID", "aurix-escrow-dev-1")
    path = env.get("ESCROW_KEY_FILE", "").strip()
    if path:
        try:
            with open(path) as f:
                return crypto.Signer.from_seed(key_id, bytes.fromhex(f.read().strip()))
        except (OSError, ValueError) as e:
            raise KeyConfigError(f"ESCROW_KEY_FILE {path}: {e}") from None
    if is_production(env):
        raise KeyConfigError("ESCROW_KEY_FILE is required when AURIX_ENV=production")
    log.warning("escrow signing key from a dev seed — never use on a public network")
    return crypto.Signer.from_seed(key_id, _dev_seed(env.get("ESCROW_SEED", "aurix-dev-escrow")))


def dev_key_entry(signer: crypto.Signer, role: str, owner: str, source_type: str = "real") -> dict:
    return {"key_id": signer.key_id, "alg": crypto.ALG, "public_key": signer.public_key, "role": role,
            "owner": owner, "source_type": source_type, "valid_from": "2026-01-01T00:00:00Z",
            "valid_until": None, "revoked_at": None, "revocation_reason": None}


DEV_CARRIER = crypto.Signer.from_seed("aurix-demo-delivery-dev-1", _dev_seed("aurix-dev-delivery"))
DEV_ARBITER = crypto.Signer.from_seed("aurix-arbiter-dev-1", _dev_seed("aurix-dev-arbiter"))


def load_external_keys(env: Mapping[str, str] | None = None) -> list[dict]:
    """Keys of the sources and arbiters AURIX accepts (ESCROW_TRUSTED_KEYS_FILE, a public-keys.json).
    Without it, outside production: the dev demo carrier and dev arbiter."""
    env = os.environ if env is None else env
    path = env.get("ESCROW_TRUSTED_KEYS_FILE", "").strip()
    if path:
        with open(path) as f:
            return json.load(f)["keys"]
    if is_production(env):
        return []
    return [dev_key_entry(DEV_CARRIER, f"source:{DEMO_CARRIER}", "AURIX demo carrier (simulated)", "simulated"),
            dev_key_entry(DEV_ARBITER, "arbiter", "AURIX demo arbiter")]


class EscrowOrderReq(BaseModel):
    amount_usd: str
    deadline_days: int = 7
    # Demo only (refused when AURIX_ENV=production): a deadline in minutes, to show a void live.
    deadline_minutes: int | None = None
    carrier: str = DEMO_CARRIER
    arbiter_key_id: str | None = None
    payer: str | None = None
    description: str = "AURIX conditional purchase"


class ConfirmReq(BaseModel):
    confirm: bool = False


class ExplainReq(BaseModel):
    audience: str = "buyer"
    lang: str = "French"


class ArbiterActionReq(BaseModel):
    action: str


class EvidenceReq(BaseModel):
    attestation: dict
    signature: dict


class EscrowService:
    """State machine of an escrow order (paypal_orders.status):

    SCORED -> AUTHORIZED -> RELEASING -> CAPTURED -> CONFIRMED -> ATTESTING -> ANCHORED  (existing anchor path)
                         \\-> VOIDING -> VOIDED
    AUTHORIZED / RELEASING -> AUTH_FAILED  (authorization expired or refused by PayPal)
    A decision, once recorded, is final: RELEASE is never followed by VOID, nor the reverse.
    """

    def __init__(self, db, paypal, guardian, paypal_mode: str, signer: crypto.Signer,
                 external_keys: list[dict], clock: Callable[[], str] = utcnow, chain=None,
                 advisor: Advisor | None = None, demo_arbiter: bool = False):
        self.db, self.paypal, self.guardian = db, paypal, guardian
        self.advisor = advisor or Advisor()
        self.demo_arbiter = demo_arbiter
        self._advice_calls: list[float] = []
        self._advice_last: dict[str, float] = {}
        self.paypal_mode, self.signer, self.clock, self.chain = paypal_mode, signer, clock, chain
        own = dev_key_entry(signer, "aurix", "AURIX escrow service")
        self.keys = {"keys": [own] + [k for k in external_keys if k["key_id"] != signer.key_id]}
        self.registry = crypto.KeyRegistry(self.keys)
        self.lock = threading.RLock()

    # ---------------- helpers ----------------
    @property
    def paypal_source(self) -> tuple[str, str]:
        """(source_type, environment) of PayPal evidence: the local simulator is never presented as PayPal."""
        if self.paypal_mode == "simulator":
            return "simulated", "simulator"
        return "real", self.paypal_mode

    def _terms_row(self, order_id: str) -> dict | None:
        return self.db.one("SELECT * FROM escrow_terms WHERE order_id=?", order_id)

    def is_escrow(self, order_id: str | None) -> bool:
        return bool(order_id and self._terms_row(order_id))

    def _store(self, att: dict, sig: dict, received_at: str | None = None) -> bool:
        """Stores an attestation once per (provider, provider_event_id). False if already known."""
        src = att["source"]
        return self.db.insert_evidence(att["attestation_id"], att["operation_id"], src["provider"],
                                       src["provider_event_id"], att["event"]["type"], json.dumps(att),
                                       json.dumps(sig), received_at or self.clock())

    def attest(self, order_id: str, etype: str, *, provider: str, provider_event_id: str, refs: dict,
               method: str, provider_status: str | None = None, amount: dict | None = None,
               source_event_time: str | None = None, raw=None, source_type: str | None = None,
               environment: str | None = None) -> bool:
        """An attestation issued and signed by AURIX about an event it received and verified."""
        now = self.clock()
        if provider == "paypal":
            st, envr = self.paypal_source
            source_type, environment = source_type or st, environment or envr
        att = {"schema": "aurix.financial-attestation", "schema_version": "1.0",
               "attestation_id": "att_" + hashlib.sha256(f"{provider}|{provider_event_id}".encode()).hexdigest()[:20],
               "operation_id": order_id,
               "source": {"provider": provider, "source_type": source_type or "real",
                          "environment": environment or "testnet", "provider_event_id": provider_event_id,
                          "provider_reference": {"order_id": order_id, **refs}, "verification_method": method,
                          "verified_by": "aurix-payments", "verification_status": "verified"},
               "event": {"type": etype},
               "timestamps": {"source_event_time": source_event_time or now, "aurix_received_at": now,
                              "attested_at": now},
               "provenance": {"connector_id": "aurix-payments/0.2",
                              "raw_payload_hash": _raw_hash(raw)}}
        if provider_status:
            att["event"]["provider_status"] = provider_status
        if amount:
            att["event"]["amount"] = {"value": str(amount.get("value")),
                                      "currency": str(amount.get("currency_code") or amount.get("currency"))}
        return self._store(att, self.signer.sign(att, now), now)

    def evidence(self, order_id: str) -> list[tuple[dict, dict, str]]:
        rows = self.db.all("SELECT attestation, signature, received_at FROM evidence WHERE order_id=?"
                           " ORDER BY received_at, attestation_id", order_id)
        return [(json.loads(r["attestation"]), json.loads(r["signature"]), r["received_at"]) for r in rows]

    def _as_rule_evidence(self, items, until: str | None = None) -> list[Evidence]:
        at = crypto.parse_time(until) if until else None
        out = []
        for a, s, rec in items:
            if at and crypto.parse_time(rec) > at:
                continue
            out.append(Evidence(a, s.get("key_id", ""), self.registry.role(s.get("key_id", "")),
                                self.registry.verify(a, s), rec))
        return out

    # ---------------- lifecycle ----------------
    def create(self, req: EscrowOrderReq) -> dict:
        try:
            amount = Decimal(req.amount_usd)
            if amount <= 0 or amount.as_tuple().exponent < -2:
                raise InvalidOperation
        except InvalidOperation:
            raise HTTPException(422, "amount_usd must be a positive amount with at most 2 decimals")
        if req.deadline_minutes is not None:
            if not self.demo_arbiter:  # demo features are off in production
                raise HTTPException(422, "deadline_minutes is a demo option, disabled in production")
            if not 1 <= req.deadline_minutes <= 24 * 60:
                raise HTTPException(422, "deadline_minutes must be between 1 and 1440")
        elif not 1 <= req.deadline_days <= MAX_DEADLINE_DAYS:
            raise HTTPException(422, f"deadline_days must be between 1 and {MAX_DEADLINE_DAYS} "
                                     "(a PayPal authorization lasts at most 29 days)")
        if not any(k.get("role") == f"source:{req.carrier}" for k in self.keys["keys"]):
            raise HTTPException(422, f"unknown carrier {req.carrier!r}: no registered key")
        if req.arbiter_key_id and self.registry.role(req.arbiter_key_id) != "arbiter":
            raise HTTPException(422, f"unknown arbiter key {req.arbiter_key_id!r}")
        value = f"{amount:.2f}"
        order = self.paypal.create_order(value, req.description, intent="AUTHORIZE")
        risk = self.guardian.score(context="paypal_order", sender=req.payer or "anonymous", recipient="merchant",
                                   amount=float(amount), currency="USD")
        created = self.clock()
        span = timedelta(minutes=req.deadline_minutes) if req.deadline_minutes else timedelta(days=req.deadline_days)
        deadline = (crypto.parse_time(created) + span).strftime("%Y-%m-%dT%H:%M:%SZ")
        terms = {"schema": "aurix.escrow-terms", "schema_version": "1.0", "order_id": order["id"],
                 "amount": {"value": value, "currency": "USD"}, "deadline": deadline, "rule": RULE_ID,
                 "condition": "delivery-attested", "carrier": req.carrier, "arbiter_key_id": req.arbiter_key_id,
                 "created_at": created}
        self.db.exec("INSERT INTO paypal_orders(order_id, amount_usd, payer, description, status, risk_score,"
                     " risk_level, simulated) VALUES (?,?,?,?,?,?,?,?)", order["id"], value, req.payer,
                     req.description, "SCORED", risk and risk["risk_score"], risk and risk["level"],
                     int(self.paypal_mode == "simulator"))
        self.db.save_risk("paypal_order", order["id"], risk)
        self.db.exec("INSERT INTO escrow_terms(order_id, terms, terms_sig) VALUES (?,?,?)", order["id"],
                     json.dumps(terms), json.dumps(self.signer.sign(terms, created)))
        approve = next((l["href"] for l in order.get("links", []) if l["rel"] in ("approve", "payer-action")), None)
        return {"order_id": order["id"], "status": "SCORED", "terms": terms, "approve_url": approve, "risk": risk,
                "advisory_only": True, "paypal_mode": self.paypal_mode}

    def authorize(self, order_id: str, confirm: bool) -> dict:
        """After the buyer approved on PayPal: hold the funds. Nothing is captured."""
        if not confirm:
            raise HTTPException(400, "explicit approval required (confirm=true)")
        self._order(order_id)
        cur = self.db.exec("UPDATE paypal_orders SET status='AUTHORIZING' WHERE order_id=? AND status='SCORED'",
                           order_id)
        if cur.rowcount != 1:  # a concurrent confirm already started (or the order moved on)
            raise HTTPException(409, f"order is {self._order(order_id)['status']}")
        try:
            res = self.paypal.authorize(order_id)
        except Exception:
            self.db.update_order(order_id, status="SCORED")  # nothing held: the buyer may approve again
            raise
        auth = res["purchase_units"][0]["payments"]["authorizations"][0]
        status = "AUTHORIZED" if auth.get("status") in ("CREATED", "PENDING") else "AUTH_FAILED"
        self.db.update_order(order_id, status=status,
                             last_error=None if status == "AUTHORIZED" else f"authorization {auth.get('status')}")
        self.db.exec("UPDATE escrow_terms SET authorization_id=? WHERE order_id=?", auth["id"], order_id)
        if status == "AUTHORIZED":
            self.attest(order_id, "payment.authorized", provider="paypal", provider_event_id=f"authorization:{auth['id']}",
                        refs={"authorization_id": auth["id"]}, method="paypal_api_authenticated",
                        provider_status=auth.get("status"), amount=auth.get("amount"),
                        source_event_time=auth.get("create_time"), raw=auth)
        return self.view(order_id)

    def add_evidence(self, att: dict, sig: dict) -> dict:
        """An attestation signed by an external source (carrier) or an arbiter."""
        src, event, ts = att.get("source"), att.get("event"), att.get("timestamps")
        if att.get("schema") != "aurix.financial-attestation" or not all(isinstance(x, dict) for x in (src, event, ts)):
            raise HTTPException(422, "unsupported attestation: schema, source, event and timestamps are required")
        aid, peid = att.get("attestation_id"), src.get("provider_event_id")
        if not isinstance(aid, str) or not ID_RE.match(aid):
            raise HTTPException(422, "attestation_id must be 1-64 characters among A-Z a-z 0-9 _ -")
        if aid.startswith("att_"):
            raise HTTPException(422, "attestation ids starting with att_ are reserved for AURIX")
        if not isinstance(peid, str) or not 0 < len(peid) <= MAX_EVENT_ID:
            raise HTTPException(422, f"source.provider_event_id is required (at most {MAX_EVENT_ID} characters)")
        if not isinstance(sig, dict) or not isinstance(sig.get("key_id"), str):
            raise HTTPException(422, "signature block with key_id required")
        verdict = self.registry.verify(att, sig)
        if verdict != crypto.VALID:
            log.warning("evidence refused (%s): %s", verdict, aid)
            raise HTTPException(422, f"signature {verdict}")
        received = self.clock()
        if not self.registry.valid_at(sig["key_id"], crypto.parse_time(received)):
            # created_at is the signer's claim; the key must be valid now, when AURIX receives the evidence
            raise HTTPException(422, "signing key is not valid at reception time")
        role = self.registry.role(sig["key_id"]) or ""
        etype, provider = event.get("type", ""), src.get("provider")
        if role.startswith("source:"):
            if provider != role.removeprefix("source:") or not str(etype).startswith("shipment."):
                raise HTTPException(422, f"a {role} key may only attest shipment events of {role[7:]}")
        elif role == "arbiter":
            if etype != "arbiter.decision" or provider != "arbiter":
                raise HTTPException(422, "an arbiter key may only attest arbiter.decision with provider 'arbiter'")
        else:
            raise HTTPException(422, f"keys with role {role!r} cannot submit evidence")
        order_id = att.get("operation_id")
        terms = self._terms(order_id if isinstance(order_id, str) else None)
        if role == "arbiter" and terms.get("arbiter_key_id") != sig["key_id"]:
            raise HTTPException(403, "this key is not the arbiter of this order")
        stored = self._store(att, sig, received)
        result = self.evaluate(order_id) if stored else None
        return {"result": "stored" if stored else "duplicate", "evaluation": result}

    def evaluate(self, order_id: str) -> dict:
        """Runs the rule now; on RELEASE or VOID records a signed, final decision and executes it."""
        with self.lock:
            o = self._order(order_id)
            row = self._terms_row(order_id)
            terms = json.loads(row["terms"])
            if row["decision"]:
                return {"decision": row["decision"], "final": True}
            if o["status"] != "AUTHORIZED":
                return {"decision": WAIT, "final": False, "reasons": [f"order is {o['status']}"]}
            now = self.clock()
            out = decide(terms, self._as_rule_evidence(self.evidence(order_id), until=now), now)
            self.db.exec("UPDATE escrow_terms SET last_eval=? WHERE order_id=?",
                         json.dumps({"decision": out.decision, "reasons": out.reasons, "at": now}), order_id)
            if out.decision not in FINAL:
                return {"decision": out.decision, "final": False, "reasons": out.reasons}
            doc = {"schema": "aurix.escrow-decision", "schema_version": "1.0", "order_id": order_id,
                   "rule": RULE_ID, "decision": out.decision, "inputs": sorted(digest(a) for a in out.inputs),
                   "decided_at": now, "deadline": terms["deadline"], "reasons": out.reasons}
            cur = self.db.exec("UPDATE escrow_terms SET decision=?, decision_doc=?, decision_sig=?"
                               " WHERE order_id=? AND decision IS NULL", out.decision, json.dumps(doc),
                               json.dumps(self.signer.sign(doc, now)), order_id)
            if cur.rowcount != 1:  # someone else decided first
                return {"decision": self._terms_row(order_id)["decision"], "final": True}
            self.db.update_order(order_id, status="RELEASING" if out.decision == RELEASE else "VOIDING")
        self.execute(order_id)
        return {"decision": out.decision, "final": True, "reasons": out.reasons}

    def execute(self, order_id: str) -> None:
        """Carries out a recorded decision at PayPal. Idempotent and serialised: the poller and a request
        thread may both get here; network errors are retried by tick()."""
        with self.lock:
            o = self._order(order_id)
            if o["status"] not in ("RELEASING", "VOIDING"):
                return  # already carried out by another thread
            auth_id = self._terms_row(order_id)["authorization_id"]
            try:
                if o["status"] == "RELEASING":
                    self._capture(order_id, auth_id)
                else:
                    self._void(order_id, auth_id)
            except PayPalError as e:
                done = {"RELEASING": "AUTHORIZATION_ALREADY_CAPTURED", "VOIDING": "AUTHORIZATION_VOIDED"}[o["status"]]
                if e.issue == done:  # a previous attempt succeeded but its result was lost: same outcome
                    if o["status"] == "VOIDING":
                        self._mark_voided(order_id, auth_id)
                    else:
                        self.db.update_order(order_id, last_error="capture already done at PayPal; "
                                                                  "waiting for its webhook")
                elif e.issue in ("AUTHORIZATION_EXPIRED", "AUTHORIZATION_VOIDED", "AUTHORIZATION_ALREADY_CAPTURED",
                                 "INVALID_STATE") or e.status == 422:
                    self.db.update_order(order_id, status="AUTH_FAILED", last_error=f"PayPal: {e.issue}")
                    log.warning("escrow %s: PayPal refused %s: %s", order_id, o["status"], e.issue)
                else:
                    self.db.update_order(order_id, last_error=f"PayPal error, retrying: {e.issue}")
                    log.warning("escrow %s: PayPal error, will retry: %s", order_id, e.issue)
            except httpx.HTTPError as e:
                self.db.update_order(order_id, last_error=f"PayPal unreachable, retrying: {type(e).__name__}")
                log.warning("escrow %s: PayPal unreachable, will retry: %s", order_id, e)

    def _capture(self, order_id: str, auth_id: str) -> None:
        cap = self.paypal.capture_authorization(auth_id, request_id=f"aurix-{order_id}-release")
        status = {"COMPLETED": "CAPTURED", "PENDING": "CAPTURE_PENDING"}.get(cap.get("status"), "DENIED")
        amt = cap.get("amount") or {}
        self.db.update_order(order_id, status=status, capture_id=cap["id"],
                             capture_amount=amt.get("value"), capture_currency=amt.get("currency_code"),
                             last_error=None if status != "DENIED" else f"capture {cap.get('status')}")

    def _void(self, order_id: str, auth_id: str) -> None:
        self.paypal.void_authorization(auth_id, request_id=f"aurix-{order_id}-void")
        self._mark_voided(order_id, auth_id)

    def _mark_voided(self, order_id: str, auth_id: str) -> None:
        self.db.update_order(order_id, status="VOIDED", last_error=None)
        self.attest(order_id, "payment.voided", provider="paypal", provider_event_id=f"void:{auth_id}",
                    refs={"authorization_id": auth_id}, method="paypal_api_authenticated", provider_status="VOIDED")

    def on_paypal_event(self, event: dict, order_id: str | None) -> str | None:
        """Verified PayPal webhook events of escrow orders. Returns a result, or None if not handled here."""
        if not self.is_escrow(order_id):
            return None
        etype, r = event.get("event_type"), event.get("resource", {})
        if etype == "PAYMENT.AUTHORIZATION.CREATED":
            self.attest(order_id, "payment.authorized", provider="paypal", provider_event_id=f"authorization:{r.get('id')}",
                        refs={"authorization_id": r.get("id")}, method="paypal_webhook_signature",
                        provider_status=r.get("status"), amount=r.get("amount"),
                        source_event_time=r.get("create_time"), raw=r)
            return "authorization recorded"
        if etype == "PAYMENT.AUTHORIZATION.VOIDED":
            with self.lock:
                if self._order(order_id)["status"] in ("VOIDING", "AUTHORIZED"):
                    self.db.update_order(order_id, status="VOIDED")
            self.attest(order_id, "payment.voided", provider="paypal", provider_event_id=f"void:{r.get('id')}",
                        refs={"authorization_id": r.get("id")}, method="paypal_webhook_signature",
                        provider_status=r.get("status"), raw=r)
            return "void recorded"
        if etype == "PAYMENT.CAPTURE.COMPLETED":
            self.attest(order_id, "payment.captured", provider="paypal", provider_event_id=f"capture:{r.get('id')}",
                        refs={"capture_id": r.get("id"),
                              "authorization_id": r.get("supplementary_data", {}).get("related_ids", {}).get(
                                  "authorization_id")},
                        method="paypal_webhook_signature", provider_status=r.get("status"),
                        amount=r.get("amount"), source_event_time=r.get("create_time"), raw=r)
            return None  # the existing capture path continues (CONFIRMED -> k-of-n anchoring)
        return None

    def record_anchor(self, order_id: str) -> None:
        """Once the payment proof is CONFIRMED on chain by k-of-n attestors, add it as evidence."""
        o = self._order(order_id)
        if o["status"] != "ANCHORED" or not o.get("proof_hash"):
            return
        signers = json.loads(o.get("attestations") or "[]")
        self.attest(order_id, "payment.anchored", provider="aurix_chain", provider_event_id=f"proof:{o['proof_hash']}",
                    refs={"proof_hash": o["proof_hash"], "capture_id": o.get("capture_id"),
                          "attestors": signers, "threshold": o.get("attest_threshold")},
                    method="aurix_chain_kofn_attestation", provider_status="CONFIRMED",
                    amount={"value": o.get("capture_amount"), "currency": o.get("capture_currency")},
                    source_type="real", environment="testnet")

    def tick(self) -> None:
        """Poller: evaluate waiting orders (deadlines), retry pending executions, record anchors."""
        for r in self.db.all("SELECT t.order_id, t.decision, o.status FROM escrow_terms t JOIN paypal_orders o"
                             " ON o.order_id=t.order_id WHERE o.status IN"
                             " ('AUTHORIZED','RELEASING','VOIDING','ANCHORED')"):
            try:
                if r["status"] == "AUTHORIZED" and r["decision"] in FINAL:
                    # decision recorded but the process stopped before the status changed: resume it
                    self.db.update_order(r["order_id"], status="RELEASING" if r["decision"] == RELEASE else "VOIDING")
                    self.execute(r["order_id"])
                elif r["status"] == "AUTHORIZED":
                    self.evaluate(r["order_id"])
                elif r["status"] == "ANCHORED":
                    self.record_anchor(r["order_id"])
                else:
                    self.execute(r["order_id"])
            except Exception:
                log.exception("escrow tick failed for %s", r["order_id"])

    # ---------------- read side ----------------
    def _order(self, order_id: str) -> dict:
        o = self.db.one("SELECT * FROM paypal_orders WHERE order_id=?", order_id)
        if not o or not self._terms_row(order_id):
            raise HTTPException(404, "escrow order not found")
        return o

    def _terms(self, order_id: str | None) -> dict:
        row = order_id and self._terms_row(order_id)
        if not row:
            raise HTTPException(404, "escrow order not found")
        return json.loads(row["terms"])

    def view(self, order_id: str) -> dict:
        o = self._order(order_id)
        row = self._terms_row(order_id)
        timeline = []
        for a, s, rec in self.evidence(order_id):
            src = a["source"]
            timeline.append({"type": a["event"]["type"], "provider": src["provider"],
                             "source_type": src["source_type"], "environment": src.get("environment"),
                             "verification_method": src["verification_method"],
                             "signature": self.registry.verify(a, s), "key_id": s.get("key_id"),
                             "source_event_time": a["timestamps"].get("source_event_time"), "received_at": rec,
                             "attestation_id": a["attestation_id"]})
        return {"order_id": order_id, "status": o["status"], "amount_usd": o["amount_usd"],
                "terms": json.loads(row["terms"]), "authorization_id": row["authorization_id"],
                "capture_id": o.get("capture_id"), "proof_hash": o.get("proof_hash"),
                "decision": json.loads(row["decision_doc"]) if row["decision_doc"] else None,
                "last_evaluation": json.loads(row["last_eval"]) if row["last_eval"] else None,
                "timeline": timeline, "risk": {"score": o.get("risk_score"), "level": o.get("risk_level")},
                "last_error": o.get("last_error"), "paypal_mode": self.paypal_mode,
                "simulated_sources": sorted({t["provider"] for t in timeline if t["source_type"] == "simulated"})}

    # ---------------- AI advisor (advisory only, never part of the rule) ----------------
    def _facts(self, order_id: str) -> dict:
        report = verify_package(read_zip(self.proof_package(order_id, with_advisories=False)), self.keys)
        return facts(self.view(order_id), report)

    def _save_advice(self, order_id: str, kind: str, advice) -> dict:
        doc = advice.as_dict() | {"kind": kind, "order_id": order_id, "generated_at": self.clock()}
        with self.lock:  # one row per kind is enough for the proof; keep the table bounded
            self.db.exec("DELETE FROM advisories WHERE order_id=? AND kind=?", order_id, kind)
            self.db.exec("INSERT INTO advisories(order_id, kind, doc) VALUES (?,?,?)", order_id, kind, json.dumps(doc))
        return doc

    def _advice_gate(self, order_id: str, lang: str) -> None:
        """Allowlisted language (it goes into the system prompt) and rate limits (each call costs a model run)."""
        if lang not in LANGS:
            raise HTTPException(422, f"lang must be one of {sorted(LANGS)}")
        now = time.monotonic()
        with self.lock:
            recent = [t for t in self._advice_calls if now - t < 60]
            if len(recent) >= ADVICE_PER_MINUTE:
                raise HTTPException(429, "too many advisor requests; try again in a minute")
            if now - self._advice_last.get(order_id, -1e9) < ADVICE_MIN_INTERVAL:
                raise HTTPException(429, "advisor already asked for this order a moment ago")
            n = self.db.one("SELECT COUNT(*) AS n FROM advisories WHERE order_id=?", order_id)["n"]
            if n >= ADVICE_PER_ORDER:
                raise HTTPException(429, "advisor limit reached for this order")
            self._advice_calls = recent + [now]
            self._advice_last[order_id] = now

    def explain(self, order_id: str, audience: str, lang: str) -> dict:
        if audience not in AUDIENCES:
            raise HTTPException(422, f"audience must be one of {sorted(AUDIENCES)}")
        self._terms(order_id)
        self._advice_gate(order_id, lang)
        return self._save_advice(order_id, f"explanation-{audience}",
                                 self.advisor.explain(self._facts(order_id), audience, lang))

    def arbiter_brief(self, order_id: str, lang: str) -> dict:
        if not self._terms(order_id).get("arbiter_key_id"):
            raise HTTPException(409, "this order has no arbiter")
        self._advice_gate(order_id, lang)
        return self._save_advice(order_id, "arbiter-brief", self.advisor.arbiter_brief(self._facts(order_id), lang))

    def demo_arbiter_action(self, order_id: str, action: str) -> dict:
        """Dev/demo only: the server holds the demo arbiter's key and signs on the arbiter's click. In a real
        deployment the arbiter signs with their own key, outside AURIX, and posts to /api/escrow/evidence."""
        if not self.demo_arbiter:
            raise HTTPException(404, "the demo arbiter is disabled")
        action = action.upper()
        if action not in ("HOLD", "UNHOLD", "RELEASE", "VOID"):
            raise HTTPException(422, "action must be HOLD, UNHOLD, RELEASE or VOID")
        terms = self._terms(order_id)
        if terms.get("arbiter_key_id") != DEV_ARBITER.key_id:
            raise HTTPException(409, "the demo arbiter is not the arbiter of this order")
        now = self.clock()
        n = uuid.uuid4().hex[:12]  # unique even for two quick clicks
        att = {"schema": "aurix.financial-attestation", "schema_version": "1.0",
               "attestation_id": f"arb_{order_id}_{n}", "operation_id": order_id,
               "source": {"provider": "arbiter", "source_type": "simulated", "environment": "demo",
                          "provider_event_id": f"arbiter:{order_id}:{n}", "provider_reference": {"order_id": order_id},
                          "verification_method": "ed25519_arbiter_key", "verification_status": "verified"},
               "event": {"type": "arbiter.decision", "action": action},
               "timestamps": {"source_event_time": now, "attested_at": now}}
        return self.add_evidence(att, DEV_ARBITER.sign(att, now))

    def proof_package(self, order_id: str, with_advisories: bool = True) -> bytes:
        self._order(order_id)
        row = self._terms_row(order_id)
        terms = json.loads(row["terms"])
        items = self.evidence(order_id)
        now = self.clock()
        receipts = receipts_doc(order_id, [(a, rec) for a, _, rec in items])
        sources = {}
        for a, _, _ in items:
            sources[a["source"]["provider"]] = {"type": a["source"]["source_type"],
                                                "environment": a["source"].get("environment")}
        meta = {"sources": sources, "issuer": "AURIX escrow service", "paypal_mode": self.paypal_mode,
                "note": "AURIX never held the funds: PayPal authorized, captured or voided them."}
        return build_package(terms=terms, terms_sig=json.loads(row["terms_sig"]),
                             attestations=[(a, s) for a, s, _ in items], receipts=receipts,
                             receipts_sig=self.signer.sign(receipts, now),
                             decision=json.loads(row["decision_doc"]) if row["decision_doc"] else None,
                             decision_sig=json.loads(row["decision_sig"]) if row["decision_sig"] else None,
                             keys=self.keys, trust_metadata=meta, signer=self.signer, created_at=now,
                             advisories=self._latest_advisories(order_id) if with_advisories else None)

    def _latest_advisories(self, order_id: str) -> dict:
        out = {}
        for r in self.db.all("SELECT kind, doc FROM advisories WHERE order_id=? ORDER BY id", order_id):
            out[r["kind"]] = json.loads(r["doc"])  # latest of each kind wins
        return out


def register(app: FastAPI, svc: EscrowService, require_paypal: Callable[[], None]) -> None:
    @app.post("/api/escrow/orders")
    def create_escrow(req: EscrowOrderReq):
        require_paypal()
        return svc.create(req)

    @app.get("/api/escrow/orders")
    def list_escrows():
        return [svc.view(r["order_id"]) for r in svc.db.all(
            "SELECT t.order_id FROM escrow_terms t JOIN paypal_orders o ON o.order_id=t.order_id"
            " ORDER BY o.created_at DESC")]

    @app.get("/api/escrow/orders/{order_id}")
    def get_escrow(order_id: str):
        return svc.view(order_id)

    @app.post("/api/escrow/orders/{order_id}/authorize")
    def authorize_escrow(order_id: str, req: ConfirmReq):
        require_paypal()
        return svc.authorize(order_id, req.confirm)

    @app.post("/api/escrow/orders/{order_id}/evaluate")
    def evaluate_escrow(order_id: str):
        return svc.evaluate(order_id) | {"order": svc.view(order_id)}

    @app.post("/api/escrow/evidence")
    def submit_evidence(req: EvidenceReq):
        return svc.add_evidence(req.attestation, req.signature)

    @app.get("/api/escrow/orders/{order_id}/proof")
    def proof(order_id: str):
        return Response(svc.proof_package(order_id), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="aurix-proof-{order_id}.zip"'})

    @app.post("/api/escrow/orders/{order_id}/explain")
    def explain(order_id: str, req: ExplainReq):
        """AI explanation in plain language (Gemma 4 by default). Advisory only."""
        return svc.explain(order_id, req.audience, req.lang)

    @app.post("/api/escrow/orders/{order_id}/arbiter-brief")
    def arbiter_brief(order_id: str, req: ExplainReq):
        """AI brief for the arbiter: summary, inconsistencies, questions, suggested action. The arbiter decides."""
        return svc.arbiter_brief(order_id, req.lang)

    @app.post("/api/escrow/orders/{order_id}/demo-arbiter")
    def demo_arbiter(order_id: str, req: ArbiterActionReq):
        return svc.demo_arbiter_action(order_id, req.action)

    @app.get("/api/escrow/advisor")
    def advisor_info():
        return {"enabled": svc.advisor.enabled, "model": svc.advisor.cfg.model if svc.advisor.enabled else "template",
                "demo_arbiter": svc.demo_arbiter}

    @app.get("/api/escrow/keys")
    def keys():
        return svc.keys
