# Demo video script (< 3 min)

**Story:** AURIX is a trust layer on top of PayPal. It explains risk before you pay, makes "pay when delivered"
work in dollars without ever holding the money, and turns every payment into a proof anyone can verify.

**Setup before recording**
- Start the stack: `ATTESTORS=2 ANCHOR_THRESHOLD=2 scripts/dev-up.sh` (or Docker Compose), open http://localhost:8080
  in a clean browser profile. Optional: an AI model for the advisor (`ADVISOR_LLM_URL`, Gemma 4 via Ollama); without
  one the advisor shows template text, labelled as such.
- Create a demo wallet and claim the faucet once (for the short AUR part).
- **Pre-record the void case** (it needs a 2-minute deadline): create a conditional order with "Demo: 2 minutes",
  approve and authorize it about 3 minutes before the matching scene, so it shows *Voided* when you get there.
- Rehearse once: `E2E_VOID=1 node tests/e2e/demo-journey.js` runs this whole journey automatically.

| Time | Screen | Action | Voice-over |
| --- | --- | --- | --- |
| 0:00-0:15 | Title / architecture | Show the README diagram | "Online, a payment can only be checked by whoever processed it, and 'pay when delivered' needs a middleman. AURIX adds a trust layer on top of PayPal: the AI advises, the protocol decides, the human approves. Testnet: AUR has no real value." |
| 0:15-0:45 | **Conditional (USD)** tab | Amount 120, deadline 7 days, tick the arbiter. **Create conditional order & assess risk**: show the Guardian score and reasons. Open the PayPal simulator page, **Pay now**, back, **I approve: hold 120 USD** | "A buyer orders something for 120 dollars, paid on delivery. Before paying, Neural Guardian gives an advisory risk score with its reasons. The buyer approves on PayPal: PayPal *holds* the funds, nothing is charged yet. This is PayPal's simulator for now, clearly labelled; the code is the same for the Sandbox." |
| 0:45-1:15 | Same tab | Click **Send a forged delivery** (toast: refused), then **Carrier: report delivery**. Watch the status go to *Released: captured on evidence, proof anchored* and the timeline fill in | "Someone tries to fake a delivery: refused, the key is unknown. The carrier, here a simulated one, with its own signing key, signs the delivery. The public rule sees the authorization and the delivery before the deadline: AURIX tells PayPal to capture. Two independent attestors re-check the capture at PayPal before the proof is confirmed on chain." |
| 1:15-1:45 | Same tab | **Verify in browser** (VALID), then **Tamper one byte & verify** (INVALID, decision mismatch) | "Every step is a signed attestation in a proof package. Anyone can verify it, offline, here in the browser, nothing uploaded. It even replays the decision. Change a single digit of the amount… invalid." |
| 1:45-2:05 | Pre-recorded order | Open the order created earlier with the 2-minute deadline: *Voided: the buyer was not charged* | "No delivery before the deadline? AURIX voids the authorization: the buyer is never charged. AURIX never held the money: PayPal did." |
| 2:05-2:25 | AI advisor panel | **Explain this payment** (for the buyer), then **Brief for the arbiter**: summary, questions, suggested action | "The AI advisor, Gemma 4, explains the payment in plain words and briefs the arbiter in a dispute. It only suggests: nothing happens until the arbiter signs." |
| 2:25-2:45 | **Smart Money** + **Explorer** (fast) | Show an AUR escrow in the grid, then the explorer with the anchored proof hash | "Underneath: a 4-validator Cosmos chain with on-chain escrow and proof anchoring, all auditable in the explorer." |
| 2:45-3:00 | README roadmap | Roadmap | "Next: the real PayPal Sandbox, real carriers, and conditions decided by on-chain consensus. AURIX: programmable value, verifiable trust." |

**Rules for the recording**
- Keep it under 2:55, add English subtitles.
- Say "simulated" when the simulator or the demo carrier is on screen.
- Never say "guaranteed" or "irrevocable"; a valid signature proves who signed, not physical delivery.
- Do not show any secret, `.env` file or key file.
