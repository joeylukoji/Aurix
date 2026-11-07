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

from .features import online_features
from .model import MODEL_VERSION, RiskModel

app = FastAPI(title="AURIX Neural Guardian", version="0.1.0")
model = RiskModel()
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
    return ScoreResponse(risk_score=a.risk_score, level=a.level, reasons=a.reasons, model_version=MODEL_VERSION)
