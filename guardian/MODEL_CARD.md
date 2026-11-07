# Neural Guardian — Model Card (v0.2-lgbm-synthetic)

## Intended use
Advisory fraud/risk signal for AURIX payments (AUR transfers, PayPal orders, escrow).
Every response has `advisory_only: true`. The service has no write path to the ledger
and must never block or reverse a payment on its own; a human or the user decides.

## Training data — SYNTHETIC, not real payment data
6000 transactions generated in `app/model.py::generate_synthetic` (seed 42). Labels are
sampled from a hand-written logistic rule that encodes simple heuristics: payments much
larger than the sender's average, first payments to a new recipient (especially combined
with a large ratio), many payments in the last hour, night-time payments, and (weakly)
larger absolute amounts. Currency (`is_usd`) has no effect on the label. Base fraud rate
is ~21%, far above reality. No real users, merchants or PayPal data were used.

## Features
| feature | meaning |
|---|---|
| log_amount | log(1 + amount) |
| amount_vs_history | amount / sender's average past amount (capped at 50; 1.0 with no history) |
| new_recipient | 1 if the sender never paid this recipient (per in-memory history) |
| tx_last_hour | sender payments in the previous hour |
| night_time | 1 if UTC hour < 06:00 |
| is_usd | 1 if USD (PayPal rail) |

Model: LightGBM binary classifier (150 rounds, 15 leaves). Reasons come from LightGBM
TreeSHAP contributions; the top positive contributors (max 3) are returned, and a
medium/high score always gets at least one reason.

## Evaluation (80/20 random hold-out, 1200 rows; live values at `GET /v1/model`)
- ROC-AUC: 0.745
- Threshold 0.4 (medium+): precision 0.48, recall 0.28, flags 12% of payments
- Threshold 0.7 (high): precision 0.78, recall 0.11, flags 3% of payments

The labels are noisy by construction, so these numbers measure how well the model
recovers the synthetic rule, not real-world fraud detection performance.

## Limitations
- Synthetic data only; metrics say nothing about real fraud.
- History is in-memory per process and lost on restart; a new sender has no baseline.
- Time of day uses UTC, not the user's local time.
- No fairness, drift or adversarial testing; easily gamed by splitting payments.
