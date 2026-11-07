"""Neural Guardian — advisory risk scoring API.

The response is ALWAYS advisory (`advisory_only: true`). This service has no
write path to the AURIX ledger.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from decimal import Decimal
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .features import online_features
from .keys import load_key
from .model import MODEL_VERSION, RiskModel
from .verify import canonical

app = FastAPI(title="AURIX Neural Guardian", version="0.1.0")
model = RiskModel()
KEY = load_key()  # raises at startup if AURIX_ENV=production without a real key
CHAIN_ID = os.environ.get("GUARDIAN_CHAIN_ID", "aurix-testnet-1")
# Per-sender amounts and per-recipient counters seen by this process. In-memory, demo only.
HISTORY: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
RECEIVED: dict[str, int] = defaultdict(int)
TX_TYPE = {"aur_transfer": "TRANSFER", "escrow": "TRANSFER", "paypal_order": "PAYMENT"}


class ScoreRequest(BaseModel):
    context: Literal["aur_transfer", "paypal_order", "escrow"]
    sender: str
    recipient: str
    amount: float = Field(gt=0)
    currency: Literal["AUR", "USD"]
    timestamp: datetime | None = None
    sender_balance: float | None = Field(default=None, ge=0, description="sender balance before the payment, if known")
    record: bool = True  # add to history after scoring
    chain_id: str | None = None  # signed into the attestation; defaults to GUARDIAN_CHAIN_ID


class Reason(BaseModel):
    feature: str
    impact: float
    text: str


class Attestation(BaseModel):
    guardian: str
    pubkey: str
    payload: str
    signature: str


class ScoreResponse(BaseModel):
    risk_score: float
    level: Literal["low", "medium", "high"]
    reasons: list[Reason]
    model_version: str
    advisory_only: Literal[True] = True
    attestation: Attestation


def amount_str(x: float) -> str:
    """Plain decimal string, no exponent, no trailing zeros (20.0 -> "20", 12.5 -> "12.5")."""
    d = Decimal(repr(x)).normalize()
    return format(d, "f")


def attest(req: ScoreRequest, risk_score: float, level: str) -> Attestation:
    payload = canonical({
        "chain_id": req.chain_id or CHAIN_ID, "from": req.sender, "to": req.recipient,
        "amount": amount_str(req.amount), "denom": req.currency, "score": risk_score, "decision": level,
        "model_version": model.signed_version, "issued_at": int(time.time()),
    })
    return Attestation(guardian=KEY.address, pubkey=KEY.pubkey.hex(), payload=payload,
                       signature=KEY.sign(payload.encode()).hex())


@app.get("/pubkey")
def pubkey() -> dict:
    return {"address": KEY.address, "pubkey": KEY.pubkey.hex()}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model_version": MODEL_VERSION,
            "roc_auc": model.metrics["roc_auc"], "pr_auc": model.metrics["pr_auc"],
            "training_data": "PaySim", "advisory_only": True}


@app.get("/v1/model")
def model_info() -> dict:
    return model.info()


@app.post("/v1/risk/score", response_model=ScoreResponse)
def score(req: ScoreRequest) -> ScoreResponse:
    ts = req.timestamp or datetime.now(timezone.utc)
    hist = HISTORY[req.sender]
    feats = online_features(req.amount, req.sender_balance, TX_TYPE[req.context], ts.hour,
                            list(hist), RECEIVED[req.recipient])
    a = model.score(feats)
    if req.record:
        hist.append(req.amount)
        RECEIVED[req.recipient] += 1
    return ScoreResponse(risk_score=a.risk_score, level=a.level, reasons=a.reasons, model_version=MODEL_VERSION,
                         attestation=attest(req, a.risk_score, a.level))
