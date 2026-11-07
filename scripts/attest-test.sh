#!/usr/bin/env bash
# k-of-n PayPal attestation, end to end on a real localnet:
#   ANCHOR_THRESHOLD=2, signers = gateway + 2 independent attestors (attestor/).
# 1. A PayPal (simulator) payment through the gateway API: ATTESTING 1/2 while the attestors are
#    paused, then ANCHORED once an attestor checked the capture at PayPal; chain proof CONFIRMED.
# 2. A forged proof: attestor 1's key anchors a capture PayPal never completed. It stays PENDING with
#    one attestation, and attestor 2 refuses it (its /health "refused" count goes up).
# PORT_OFFSET (default 20000), VALIDATORS (default 2), RUN_DIR (default temp dir).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
export PORT_OFFSET=${ATTEST_PORT_OFFSET:-20000} VALIDATORS=${ATTEST_VALIDATORS:-2}
export RUN_DIR=${ATTEST_RUN_DIR:-$(mktemp -d /tmp/aurix-attest-XXXX)}
export AURIX_NET_HOME=$RUN_DIR/chain-net ATTESTORS=2 ANCHOR_THRESHOLD=2 ATTESTOR_POLL_INTERVAL=1
unset RPC_PORT REST_PORT GRPC_PORT P2P_PORT PROM_PORT GUARDIAN_PORT SIM_PORT GATEWAY_PORT ATTESTOR_PORT PAYMENTS_PORT INDEXER_PORT PG_PORT
GW=http://127.0.0.1:$((8080 + PORT_OFFSET)); SIM=http://127.0.0.1:$((8090 + PORT_OFFSET))
REST=http://127.0.0.1:$((1317 + PORT_OFFSET)); A1=$((8095 + PORT_OFFSET)); A2=$((A1 + 1))
log() { echo "[attest-test] $*"; }
fail() { echo "[attest-test] FAIL: $*" >&2; exit 1; }
cleanup() { kill -CONT "$(cat "$RUN_DIR/attestor1.pid" 2>/dev/null)" "$(cat "$RUN_DIR/attestor2.pid" 2>/dev/null)" 2>/dev/null || true
            "$ROOT/scripts/dev-down.sh" >/dev/null; }
trap cleanup EXIT

"$ROOT/scripts/dev-up.sh"
params=$(curl -fsS "$REST/aurix/smartmoney/v1/params")
log "params: $(jq -c .params <<<"$params")"
[[ $(jq -r .params.anchor_threshold <<<"$params") == 2 ]] || fail "threshold is not 2"
[[ $(jq '.params.anchor_signers | length' <<<"$params") == 3 ]] || fail "expected 3 anchor signers"

# ---------- 1. honest payment ----------
pause() { kill "-$1" "$(cat "$RUN_DIR/attestor1.pid")" "$(cat "$RUN_DIR/attestor2.pid")"; }
pause STOP  # hold the independent attestors so the 1/2 state is observable
oid=$(curl -fsS -X POST "$GW/api/paypal/orders" -H 'Content-Type: application/json' -d '{"amount_usd":"12.34"}' | jq -r .order_id)
log "order $oid created"
curl -fsS -X POST "$SIM/sim/orders/$oid/approve" >/dev/null
curl -fsS -X POST "$GW/api/paypal/orders/$oid/approve" -H 'Content-Type: application/json' -d '{"confirm":true}' >/dev/null
for _ in $(seq 1 60); do
  o=$(curl -fsS "$GW/api/paypal/orders/$oid"); st=$(jq -r .status <<<"$o")
  [[ $st == ATTESTING || $st == ANCHORED ]] && break; sleep 0.5
done
log "after webhook: $(jq -c '{status, attestations}' <<<"$o")"
[[ $st == ATTESTING ]] || fail "expected ATTESTING, got $st"
[[ $(jq -r '"\(.attestations.count)/\(.attestations.threshold)"' <<<"$o") == 1/2 ]] || fail "expected 1/2 attestations"
h=$(jq -r .proof_hash <<<"$o")
[[ $(curl -fsS "$GW/api/proofs/$h" | jq -r .status) == PENDING ]] || fail "chain proof should be PENDING"
pause CONT
for _ in $(seq 1 60); do
  o=$(curl -fsS "$GW/api/paypal/orders/$oid"); st=$(jq -r .status <<<"$o")
  [[ $st == ANCHORED ]] && break; sleep 0.5
done
[[ $st == ANCHORED ]] || fail "order not ANCHORED: $o"
p=$(curl -fsS "$REST/aurix/smartmoney/v1/proofs/$h")
log "chain proof: $(jq -c '.proof | {status, attestations, confirmed_height}' <<<"$p")"
[[ $(jq -r .proof.status <<<"$p") == PROOF_STATUS_CONFIRMED ]] || fail "chain proof not CONFIRMED"
[[ $(jq '.proof.attestations | length' <<<"$p") -ge 2 ]] || fail "fewer than 2 attestations"
log "honest payment: ATTESTING 1/2 -> ANCHORED, proof CONFIRMED with $(jq '.proof.attestations | length' <<<"$p") attestations"

# ---------- 2. forged proof from one attestor key ----------
sleep 3  # let the attestors finish their cycle on the honest proof
before=$(curl -fsS "http://127.0.0.1:$A2/health" | jq .refused)
forged=$(cd "$ROOT/attestor" && CHAIN_REST=$REST .venv/bin/python - <<'EOF'
import base64, hashlib, os, httpx
from attestor import crypto
key = crypto.Key("aurix-dev-attestor-1")
order, cap = "FORGED-ORDER-" + os.urandom(4).hex(), "FORGED-CAPTURE-" + os.urandom(4).hex()
h = hashlib.sha256(f"{order}|{cap}".encode()).hexdigest()
rest = os.environ["CHAIN_REST"]
chain_id = httpx.get(f"{rest}/cosmos/base/tendermint/v1beta1/blocks/latest").json()["block"]["header"]["chain_id"]
info = httpx.get(f"{rest}/cosmos/auth/v1beta1/account_info/{key.address}").json()["info"]
tx = crypto.build_tx(key, [crypto.msg_anchor_proof(key.address, h, f"paypal:{order}", cap, "10.00", "USD")], chain_id,
                     int(info["account_number"]), int(info["sequence"]))
r = httpx.post(f"{rest}/cosmos/tx/v1beta1/txs", json={"tx_bytes": base64.b64encode(tx).decode(),
                                                       "mode": "BROADCAST_MODE_SYNC"}).json()["tx_response"]
assert int(r.get("code", 0)) == 0, r
print(h)
EOF
)
log "forged proof $forged broadcast by attestor 1"
for _ in $(seq 1 40); do
  fp=$(curl -fsS "$REST/aurix/smartmoney/v1/proofs/$forged" 2>/dev/null || true)
  [[ -n $fp ]] && break; sleep 0.5
done
[[ -n $fp ]] || fail "forged proof not on chain"
for _ in $(seq 1 30); do
  after=$(curl -fsS "http://127.0.0.1:$A2/health" | jq .refused)
  ((after > before)) && break; sleep 0.5
done
sleep 4  # several more poll cycles: nobody else may attest it
fp=$(curl -fsS "$REST/aurix/smartmoney/v1/proofs/$forged")
log "forged proof: $(jq -c '.proof | {status, attestations}' <<<"$fp"); attestor2 refused $before -> $after"
[[ $(jq -r .proof.status <<<"$fp") == PROOF_STATUS_PENDING ]] || fail "forged proof was confirmed"
[[ $(jq '.proof.attestations | length' <<<"$fp") == 1 ]] || fail "forged proof gained attestations"
((after > before)) || fail "attestor 2 did not refuse the forged proof"
curl -fsS "$REST/aurix/smartmoney/v1/proofs_pending" | jq -e --arg h "$forged" '[.proofs[].hash] | index($h) != null' >/dev/null \
  || fail "forged proof not listed as pending"
grep -q "refusing proof $forged" "$RUN_DIR/attestor2.log" && log "attestor2 log: $(grep "refusing proof $forged" "$RUN_DIR/attestor2.log" | head -1 | cut -c1-200)"
log PASS
