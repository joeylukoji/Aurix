"""Feature definitions shared by offline training (PaySim) and the online API.

Only features the gateway can know *before* a payment is confirmed are used:
amount, the sender's balance before the payment, the transaction type, the hour,
and counters/averages from earlier transactions. PaySim's post-transaction balances
and destination balances are deliberately NOT used (they leak the outcome or are
simulation artefacts).
"""
from __future__ import annotations

import math

FEATURES = [
    "log_amount",            # log(1 + amount)
    "amount_to_balance",     # amount / sender balance before the payment, capped at 10 (NaN if unknown or 0)
    "drains_account",        # 1 if the payment empties the balance exactly (ratio within 1%)
    "tx_type",               # 0 TRANSFER, 1 CASH_OUT, 2 PAYMENT, 3 CASH_IN, 4 DEBIT
    "hour",                  # hour of day (PaySim step % 24)
    "sender_prior_txs",      # payments previously sent by this sender
    "amount_vs_sender_avg",  # amount / mean of the sender's previous amounts (NaN if none)
    "recipient_prior_txs",   # payments previously received by this recipient
]
TX_TYPES = {"TRANSFER": 0, "CASH_OUT": 1, "PAYMENT": 2, "CASH_IN": 3, "DEBIT": 4}
CATEGORICAL = ["tx_type"]


RATIO_CAP = 10.0


def _ratio(num: float, den: float | None) -> float:
    if not den or den <= 0 or math.isnan(den):
        return float("nan")
    return min(num / den, RATIO_CAP)


def online_features(amount: float, sender_balance: float | None, tx_type: str, hour: int,
                    sender_amounts: list[float], recipient_prior: int) -> dict[str, float]:
    ratio = _ratio(amount, sender_balance)
    avg = sum(sender_amounts) / len(sender_amounts) if sender_amounts else None
    return {
        "log_amount": math.log1p(amount),
        "amount_to_balance": ratio,
        "drains_account": float(not math.isnan(ratio) and 0.99 <= ratio <= 1.01),
        "tx_type": float(TX_TYPES[tx_type]),
        "hour": float(hour),
        "sender_prior_txs": float(len(sender_amounts)),
        "amount_vs_sender_avg": _ratio(amount, avg),
        "recipient_prior_txs": float(recipient_prior),
    }
