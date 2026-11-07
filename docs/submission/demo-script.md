# Demo video script (< 3 min)

Setup before recording: start the stack (`scripts/dev-up.sh` or `docker compose -f deploy/docker-compose.yml up --build`), open http://localhost:8080 in a clean browser profile. Prepare a second address (recipient / arbiter), e.g. from a second browser profile.

| Time | Screen | Action | Voice-over |
| --- | --- | --- | --- |
| 0:00-0:15 | Title / README | Show architecture diagram | "AURIX: programmable value, verifiable trust. The AI advises, the protocol decides, the human approves. This is a testnet: a single-validator Cosmos SDK chain, AUR has no real value." |
| 0:15-0:40 | **Wallet** tab | Click **Create demo wallet**, then **Get 100 test AUR**, **Refresh** | "The key is generated in the browser and never sent to the server. The balance is read from the chain." |
| 0:40-1:10 | **Send AUR** tab | Paste recipient `aur1…`, amount, click **Check with Neural Guardian**; show score and reasons; confirm | "Before confirming, Neural Guardian gives an advisory risk score with SHAP reasons. The model is trained on synthetic data. I confirm; the transaction is signed locally with SIGN_MODE_DIRECT and included in a block." |
| 1:10-1:40 | **Smart Money** tab | Fill recipient, arbiter, amount, deadline; **Check and lock**; show escrow `LOCKED` in **Escrows**; release it | "Smart Money locks funds in an on-chain escrow. It can be released once, by the sender or arbiter, or refunded after the deadline." |
| 1:40-2:25 | **PayPal (USD)** tab, then the **PayPal simulator buyer page** | Enter USD amount, **Create order & assess risk** (status `SCORED`, risk shown); open the approve link, approve on the simulator page; back in AURIX confirm capture; watch status go `CAPTURED` → `CONFIRMED` → `ANCHORED` in **Orders** | "USD goes through the PayPal Orders flow. Today this is our local PayPal simulator, clearly labelled, until Sandbox credentials are added; the gateway code is the same. No capture without my explicit approval. A signed webhook is verified and de-duplicated, then only a hash of the payment is anchored on AURIX. USD never becomes AUR." |
| 2:25-2:50 | **Explorer** tab | Show **Blocks** and **Transactions** grids; search the proof hash or tx hash | "Everything is auditable in the explorer: transfers, escrows and the PayPal proof." |
| 2:50-3:00 | README | Roadmap | "Next: real PayPal Sandbox, more validators, graph-based AI. Thank you." |

Tips: keep the recording under 2:55, add subtitles, do not show any secret or `.env` file.
