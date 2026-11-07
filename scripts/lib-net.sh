# Shared helpers for the throwaway multi-validator test networks (sourced by gov-test, slashing-test,
# upgrade-drill). Expects NET (network home), BASE (port base) and PIDS (node pids) to be set.

# wait_first_blocks MIN_HEIGHT [TIMEOUT_S]: waits for node0 to reach MIN_HEIGHT. On a busy CI runner the
# 4 nodes may take a while to dial each other before the first block, so the default is 120 s. Fails
# fast if a node process died, and prints the tail of every node log on failure.
wait_first_blocks() {
  local min=$1 timeout=${2:-120} h s i
  for ((s = 0; s < timeout; s++)); do
    h=$(curl -fsS "http://127.0.0.1:$((BASE + 57))/status" 2>/dev/null | jq -r .result.sync_info.latest_block_height 2>/dev/null || echo 0)
    [[ "$h" =~ ^[0-9]+$ && "$h" -ge "$min" ]] && return 0
    for i in "${!PIDS[@]}"; do
      if ! kill -0 "${PIDS[$i]}" 2>/dev/null; then
        echo "FAIL: node$i exited before the first blocks" >&2; tail -20 "$NET/node$i.log" >&2; exit 1
      fi
    done
    sleep 1
  done
  echo "FAIL: timed out after ${timeout}s waiting for height $min (last: ${h:-none})" >&2
  for i in "${!PIDS[@]}"; do echo "--- node$i.log" >&2; tail -8 "$NET/node$i.log" >&2; done
  exit 1
}
