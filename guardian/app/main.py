"""Neural Guardian — advisory risk scoring API.

The response is ALWAYS advisory (`advisory_only: true`). This service has no
write path to the AURIX ledger.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .model import MODEL_VERSION, RiskModel, build_features

app = FastAPI(title="AURIX Neural Guardian", version="0.1.0")
model = RiskModel()
# Per-sender recent history (recipient, amount, ts). In-memory, demo only.
HISTORY: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))


class ScoreRequest(BaseModel):
    context: Literal["aur_transfer", "paypal_order", "escrow"]
    sender: str
    recipient: str
    amount: float = Field(gt=0)
    currency: Literal["AUR", "USD"]
    timestamp: datetime | None = None
    record: bool = True  # add to history after scoring


class Reason(BaseModel):
    feature: str
    impact: float
    text: str


class ScoreResponse(BaseModel):
    risk_score: float
    level: Literal["low", "medium", "high"]
    reasons: list[Reason]
    model_version: str
    advisory_only: Literal[True] = True


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model_version": MODEL_VERSION}


@app.post("/v1/risk/score", response_model=ScoreResponse)
def score(req: ScoreRequest) -> ScoreResponse:
    ts = req.timestamp or datetime.now(timezone.utc)
    hist = HISTORY[req.sender]
    feats = build_features(req.amount, req.currency, list(hist), req.recipient, ts.timestamp(), ts.hour)
    a = model.score(feats)
    if req.record:
        hist.append((req.recipient, req.amount, ts.timestamp()))
    return ScoreResponse(risk_score=a.risk_score, level=a.level, reasons=a.reasons, model_version=MODEL_VERSION)
