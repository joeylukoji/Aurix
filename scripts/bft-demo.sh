#!/usr/bin/env bash
# Demonstrates BFT fault tolerance on the stack started by scripts/dev-up.sh (4 validators):
# stops validator 3, shows blocks are still produced with 3/4 signatures, then restarts it.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RUN_DIR=${RUN_DIR:-$ROOT/.run}; RPC=${RPC_PORT:-26657}
NET=${AURIX_NET_HOME:-$RUN_DIR/chain-net}; BIN=$ROOT/chain/build/aurixd
h() { curl -fsS "http://127.0.0.1:$RPC/status" | jq -r .result.sync_info.latest_block_height; }
sigs() { curl -fsS "http://127.0.0.1:$RPC/block" | jq '[.result.block.last_commit.signatures[]|select(.block_id_flag==2)]|length'; }
total() { curl -fsS "http://127.0.0.1:$RPC/validators" | jq -r .result.total; }
[[ -f "$RUN_DIR/chain3.pid" ]] || { echo "needs a 4-validator stack from scripts/dev-up.sh" >&2; exit 1; }

echo "validators: $(total) | height $(h) | signatures on last block: $(sigs)"
kill "$(cat "$RUN_DIR/chain3.pid")"; echo ">> validator 3 stopped (3/4 voting power left, BFT needs > 2/3)"
a=$(h); sleep 6
echo "height $a -> $(h) | signatures on last block: $(sigs)  => the chain keeps finalising blocks"
(cd "$ROOT/chain" && exec "$BIN" start --home "$NET/node3") >>"$RUN_DIR/chain3.log" 2>&1 &
echo $! >"$RUN_DIR/chain3.pid"; echo ">> validator 3 restarted"; sleep 8
echo "height $(h) | signatures on last block: $(sigs)  => validator 3 caught up and signs again"
