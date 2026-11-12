# AURIX — Devpost submission

Fields in the order of the Devpost form.

## Project name

AURIX

## Elevator pitch (max 200 characters)

A trust layer on top of PayPal: an AI drafts the payment condition, PayPal holds the money, signed evidence decides, and anyone can verify the decision.

## Try it out (links)

- Code: https://github.com/joeylukoji/Aurix
- Demo: https://aurixfin.duckdns.org (guided demo: `/demo.html`, proof check: `/proof.html`) — or run locally in one command (README)

## Video demo link

Upload `aurix-en.mp4` (2 min 30) to YouTube or Vimeo and paste the link here.

## About the project

### Inspiration

A buyer in Kinshasa orders goods from a supplier in Shenzhen. Who pays first? If she pays upfront, she carries all the risk; if he ships first, the risk is his. Today the money moves before any evidence, disputes come after the fact, and nobody outside can verify what really happened. Letters of credit and escrow agents exist, but they are slow and out of reach for small cross-border traders. We wanted "pay when the evidence says so", on top of the payment rail people already use: PayPal.

### What it does

- **AI-drafted conditions.** The buyer describes the purchase in her own words, in any language. **Gemma 4** turns it into a payment condition: for example *inspection passed AND customs cleared AND delivered, then 72 h to dispute*, and it flags real limits (a PayPal authorization lasts at most 29 days). The output is strictly validated; the summary and the transaction workflow shown to the buyer are computed from the condition itself, never from model text. **The AI proposes, the buyer decides**: nothing applies until she approves, and she can edit or refuse it.
- **PayPal holds the money.** The buyer approves on PayPal (Orders v2, `intent=AUTHORIZE`): the funds are held, not charged. AURIX never holds or moves dollars.
- **Signed, independent evidence.** Each source (carrier, inspector, customs) signs its own events with its own Ed25519 key and may only attest its own kind of event. A forged or misattributed signature is rejected.
- **A public, deterministic rule decides.** When the evidence meets the condition before the deadline, AURIX captures the payment; otherwise it voids the authorization and the buyer is not charged. A human arbiter can hold a disputed payment and decide; the AI's suggestion has no effect until the arbiter signs.
- **Neural Guardian.** A second AI model scores the fraud risk before any money moves, with human-readable reasons, signed by two independent guardians. Advisory only: it never blocks a payment.
- **A proof anyone can verify.** Every step becomes a signed attestation in a proof package. Anyone can re-check it offline or in the browser, which also replays the decision rule. Change one digit and it turns INVALID. Each PayPal capture is re-checked by two independent attestors before its proof is anchored on a 4-validator chain.
- **Simple to try.** Three pages: a home page, a guided 5-step demo and a proof page, in English and French.

### How we built it

- **Chain:** Cosmos SDK v0.50 + CometBFT v0.38, 4 validators (keeps producing blocks with one node down); custom `x/smartmoney` module for escrow and k-of-n proof anchoring.
- **Payments service (Python, FastAPI):** PayPal REST client (OAuth2, Orders v2 authorize / capture / void, webhook signature verification, idempotency), the decision rules `delivery-attested/1` and `conditions/1`, signed receipts so the replay only uses evidence received in time.
- **AI:** Gemma 4 (`gemma-4-26b-a4b-it`) on Google AI Studio through an OpenAI-compatible API, with prompt-injection guards, strict schema validation and a labelled template fallback. Neural Guardian: calibrated logistic regression with exact SHAP reasons, trained on 6.3 M PaySim transactions with a leakage-free evaluation (test PR-AUC 0.973).
- **Verification:** RFC 8785 (JCS) canonical JSON, SHA-256, Ed25519; the same verifier package is used by the service and by `aurix-verify`, and a browser port (WebCrypto) is kept identical by a parity test.
- **Web:** plain JavaScript and CSS, no build step; EN / FR.
- **Delivery:** Docker Compose, a Render blueprint, Let's Encrypt HTTPS, Go and pytest suites, Playwright end-to-end journeys.

### Challenges we ran into

- Letting an LLM write a payment rule without letting it touch the money: the model only drafts, a strict validator and the buyer's approval stand between it and the rule.
- Making a decision verifiable by a third party: deterministic rule, signed inputs, and signed proof of what AURIX had received at decision time.
- Gemma 4's quirks on Google AI Studio (no JSON mode, reasoning blocks in the answer) and latency of 30 to 70 s per answer.
- Unstable networks: connection retries that never resend a payment request, longer timeouts, clear errors naming the unreachable service.

### Accomplishments that we're proud of

- A conditional USD payment that PayPal holds, signed evidence releases, and anyone can re-verify, including replaying the decision.
- Two AI models with clearly separated roles, neither of which can move money.
- A real 4-validator BFT chain with k-of-n anchoring, not a mock ledger.
- A risk model with an honest, leakage-free evaluation.

### What we learned

How to give AI a useful role in payments while keeping the human and a deterministic rule in charge, and how much a verifiable proof changes the trust discussion between strangers.

### What's next for AURIX

- Real carriers and customs systems instead of simulated sources.
- Conditions recorded on chain and decided by consensus.
- Independent validator and attestor operators.
- A public deployment, and a full validation on the PayPal Sandbox then live.

### Honest limitations

- Testnet: AUR has no monetary value; the 4 validators run on one machine.
- PayPal is integrated with a real Sandbox app (OAuth, orders, webhook registration); the full capture and signed-webhook round trip on the Sandbox is still being validated, and the local simulator runs the whole flow.
- The evidence sources are simulated: a valid signature proves who signed, not that goods physically arrived.
- A PayPal authorization lasts at most 29 days, which bounds the conditions.
- AI texts may contain errors; they are advisory and never used by the decision rule.

## Built with

Go, Cosmos SDK, CometBFT, Python, FastAPI, scikit-learn, SHAP, Gemma 4, Google AI Studio, PayPal Orders API, PayPal Webhooks, Ed25519, RFC 8785, WebCrypto, JavaScript, PostgreSQL, SQLite, Docker, Caddy, Let's Encrypt, Render, Playwright
