#!/usr/bin/env bash
# Hardening checks on a real localnet (2 validators, gateway + 2 attestors, threshold 2, 2 guardians):
#  a1. Amount in proofs: the gateway anchors an honest PayPal capture with amount X; a (malicious) attestor
#      key submits MsgAnchorProof for the same hash with another amount -> rejected by the chain
#      (smartmoney code 11, ErrProofMismatch).
#  a2. A real PayPal capture of 5.00 USD is anchored first by attestor 1's key claiming 99.99 USD: attestor 2
#      checks PayPal, refuses it (its /health "refused" count goes up) and the proof stays PENDING.
#  b.  Expiry: with PENDING_TTL_BLOCKS=15 that pending proof is deleted once it expires (REST 404).
#  c.  Guardian quorum: the gateway's risk score carries 2 valid guardian signatures, quorum met.
# Ports: PORT_BASE (default 46000) + small offsets, node i at +i*100. RUN_DIR (default temp dir).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
B=${PORT_BASE:-46000}
export RUN_DIR=${HARDEN_RUN_DIR:-$(mktemp -d /tmp/aurix-harden-XXXX)}
export AURIX_NET_HOME=$RUN_DIR/chain-net VALIDATORS=2 VALIDATOR_PORT_STEP=100 ATTESTORS=2 ANCHOR_THRESHOLD=2 \
  ATTESTOR_POLL_INTERVAL=1 PENDING_TTL_BLOCKS=15 GUARDIANS=2 GUARDIAN_QUORUM=2 PORT_OFFSET=0 \
  RPC_PORT=$((B + 57)) P2P_PORT=$((B + 56)) REST_PORT=$((B + 17)) GRPC_PORT=$((B + 90)) PROM_PORT=$((B + 60)) \
  GUARDIAN_PORT=$((B + 600)) GATEWAY_PORT=$((B + 680)) SIM_PORT=$((B + 690)) ATTESTOR_PORT=$((B + 695)) \
  PAYMENTS_PORT=$((B + 685)) INDEXER_PORT=$((B + 686)) PG_PORT=$((B + 687))
GW=http://127.0.0.1:$GATEWAY_PORT; SIM=http://127.0.0.1:$SIM_PORT; REST=http://127.0.0.1:$REST_PORT
A2=$((ATTESTOR_PORT + 1)); RPC=http://127.0.0.1:$RPC_PORT
log() { echo "[harden-test] $*"; }
fail() { echo "[harden-test] FAIL: $*" >&2; exit 1; }
pause() { kill "-$1" "$(cat "$RUN_DIR/attestor1.pid")" "$(cat "$RUN_DIR/attestor2.pid")"; }
cleanup() { pause CONT 2>/dev/null || true; "$ROOT/scripts/dev-down.sh" >/dev/null; }
trap cleanup EXIT
height() { curl -fsS "$RPC/status" | jq -r .result.sync_info.latest_block_height; }

# anchor <seed> <hash> <external_ref> <capture_ref> <amount> <currency>: signs MsgAnchorProof with the dev
# attestor key and prints the DeliverTx code once included (0 = ok).
anchor() {
  (cd "$ROOT/attestor" && CHAIN_REST=$REST .venv/bin/python - "$@" <<'EOF'
import base64, sys, time, httpx
from attestor import crypto
seed, h, ext, cap, amount, cur = sys.argv[1:7]
key, rest = crypto.Key(seed), __import__("os").environ["CHAIN_REST"]
chain_id = httpx.get(f"{rest}/cosmos/base/tendermint/v1beta1/blocks/latest").json()["block"]["header"]["chain_id"]
info = httpx.get(f"{rest}/cosmos/auth/v1beta1/account_info/{key.address}").json()["info"]
tx = crypto.build_tx(key, [crypto.msg_anchor_proof(key.address, h, ext, cap, amount, cur)], chain_id,
                     int(info["account_number"]), int(info["sequence"]))
r = httpx.post(f"{rest}/cosmos/tx/v1beta1/txs", json={"tx_bytes": base64.b64encode(tx).decode(),
                                                       "mode": "BROADCAST_MODE_SYNC"}).json()["tx_response"]
if int(r.get("code", 0)):
    print(f"checktx:{r['code']}:{r.get('codespace')}"); sys.exit()
for _ in range(60):
    t = httpx.get(f"{rest}/cosmos/tx/v1beta1/txs/{r['txhash']}")
    if t.status_code == 200:
        tr = t.json()["tx_response"]; print(f"{tr.get('code', 0)}:{tr.get('codespace', '')}:{tr.get('raw_log', '')[:160]}"); sys.exit()
    time.sleep(0.5)
print("timeout")
EOF
  )
}

"$ROOT/scripts/dev-up.sh"
params=$(curl -fsS "$REST/aurix/smartmoney/v1/params")
log "params: $(jq -c .params <<<"$params")"
[[ $(jq -r .params.pending_ttl_blocks <<<"$params") == 15 ]] || fail "pending_ttl_blocks is not 15"

# ---------- c. guardian quorum (2 guardians) ----------
o=$(curl -fsS -X POST "$GW/api/paypal/orders" -H 'Content-Type: application/json' -d '{"amount_usd":"12.34"}')
log "gateway risk quorum: $(jq -c '.risk.quorum' <<<"$o") status=$(jq -r .risk_status <<<"$o")"
[[ $(jq -r .risk.quorum.valid <<<"$o") == 2 ]] || fail "expected 2 valid guardian signatures: $(jq -c .risk.guardians <<<"$o")"
[[ $(jq -r .risk.quorum_met <<<"$o") == true && $(jq -r .risk_status <<<"$o") == ok ]] || fail "quorum not met"
[[ $(jq -r '[.risk.guardians[].guardian] | unique | length' <<<"$o") == 2 ]] || fail "guardians not distinct"
oid=$(jq -r .order_id <<<"$o")

# ---------- a1. same hash, different amount -> ErrProofMismatch ----------
pause STOP  # keep the proof at 1/2 so only the gateway's attestation exists
curl -fsS -X POST "$SIM/sim/orders/$oid/approve" >/dev/null
curl -fsS -X POST "$GW/api/paypal/orders/$oid/approve" -H 'Content-Type: application/json' -d '{"confirm":true}' >/dev/null
for _ in $(seq 1 60); do
  o=$(curl -fsS "$GW/api/paypal/orders/$oid"); st=$(jq -r .status <<<"$o")
  [[ $st == ATTESTING || $st == ANCHORED ]] && break; sleep 0.5
done
[[ $st == ATTESTING ]] || fail "expected ATTESTING, got $st"
h=$(jq -r .proof_hash <<<"$o")
p=$(curl -fsS "$REST/aurix/smartmoney/v1/proofs/$h")
log "honest proof: $(jq -c '.proof | {amount, currency, status, expires_height}' <<<"$p")"
[[ $(jq -r .proof.amount <<<"$p") == 12.34 && $(jq -r .proof.currency <<<"$p") == USD ]] || fail "proof amount not 12.34 USD"
res=$(anchor aurix-dev-attestor-1 "$h" "$(jq -r .proof.external_ref <<<"$p")" "$(jq -r .proof.capture_ref <<<"$p")" 99.99 USD)
log "malicious attestation with amount 99.99: $res"
[[ $res == 11:smartmoney:* ]] || fail "expected ErrProofMismatch (code 11), got $res"
pause CONT
for _ in $(seq 1 60); do
  st=$(curl -fsS "$GW/api/paypal/orders/$oid" | jq -r .status); [[ $st == ANCHORED ]] && break; sleep 0.5
done
[[ $st == ANCHORED ]] || fail "honest order not ANCHORED after the attestors resumed"
log "honest order ANCHORED with amount 12.34 despite the mismatching attestation"

# ---------- a2. real capture, first attestation claims a wrong amount ----------
sleep 3
cap=$(cd "$ROOT/attestor" && SIM=$SIM .venv/bin/python - <<'EOF'
import os, httpx
sim = os.environ["SIM"]
tok = httpx.post(f"{sim}/v1/oauth2/token", auth=("sim-client-id", "sim-client-secret"),
                 data={"grant_type": "client_credentials"}).json()["access_token"]
hd = {"Authorization": f"Bearer {tok}"}
o = httpx.post(f"{sim}/v2/checkout/orders", headers=hd, json={"intent": "CAPTURE", "purchase_units": [
    {"amount": {"currency_code": "USD", "value": "5.00"}}]}).json()
httpx.post(f"{sim}/sim/orders/{o['id']}/approve").raise_for_status()
c = httpx.post(f"{sim}/v2/checkout/orders/{o['id']}/capture", headers=hd).json()
print(o["id"], c["purchase_units"][0]["payments"]["captures"][0]["id"])
EOF
)
read -r forder fcap <<<"$cap"
fh=$(printf '%s|%s' "$forder" "$fcap" | sha256sum | cut -d' ' -f1)
before=$(curl -fsS "http://127.0.0.1:$A2/health" | jq .refused)
res=$(anchor aurix-dev-attestor-1 "$fh" "paypal:$forder" "$fcap" 99.99 USD)
[[ $res == 0:* ]] || fail "forged first attestation not included: $res"
log "PayPal captured 5.00 USD ($forder/$fcap); attestor 1 key anchored it claiming 99.99 USD"
fp=$(curl -fsS "$REST/aurix/smartmoney/v1/proofs/$fh")
exp=$(jq -r .proof.expires_height <<<"$fp")
for _ in $(seq 1 30); do
  after=$(curl -fsS "http://127.0.0.1:$A2/health" | jq .refused); ((after > before)) && break; sleep 0.5
done
((after > before)) || fail "attestor 2 did not refuse the wrong-amount proof"
grep -q "refusing proof $fh" "$RUN_DIR/attestor2.log" && log "attestor2: $(grep "refusing proof $fh" "$RUN_DIR/attestor2.log" | head -1 | cut -c1-200)"
fp=$(curl -fsS "$REST/aurix/smartmoney/v1/proofs/$fh")
log "wrong-amount proof: $(jq -c '.proof | {amount, status, attestations, expires_height}' <<<"$fp")"
[[ $(jq -r .proof.status <<<"$fp") == PROOF_STATUS_PENDING ]] || fail "wrong-amount proof was confirmed"
[[ $(jq '.proof.attestations | length' <<<"$fp") == 1 ]] || fail "wrong-amount proof gained attestations"

# ---------- b. expiry ----------
log "waiting for expiry at height $exp (now $(height))"
code=200
for _ in $(seq 1 120); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$REST/aurix/smartmoney/v1/proofs/$fh")
  [[ $code == 404 ]] && break; sleep 0.5
done
[[ $code == 404 ]] || fail "pending proof still present after ttl (height $(height), expires $exp, HTTP $code)"
(($(height) >= exp)) || fail "proof removed before its expiry height"
curl -fsS "$REST/aurix/smartmoney/v1/proofs_pending" | jq -e --arg h "$fh" '[.proofs[].hash] | index($h) == null' >/dev/null \
  || fail "expired proof still listed as pending"
[[ $(curl -fsS "$REST/aurix/smartmoney/v1/proofs/$h" | jq -r .proof.status) == PROOF_STATUS_CONFIRMED ]] \
  || fail "confirmed proof must never expire"
log "expired at height $(height) (expires_height $exp): REST 404; confirmed proof kept"
log PASS
