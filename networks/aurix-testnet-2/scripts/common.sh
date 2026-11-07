# Shared helpers for the aurix-testnet-2 ceremony scripts (sourced, not executed).
BIN=${AURIXD:-aurixd}
CHAIN_ID=${CHAIN_ID:-aurix-testnet-2}
die() { echo "error: $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing dependency: $1"; }
sha() { sha256sum "$1" | awk '{print $1}'; }
verify_sha() { # file expected
  local got; got=$(sha "$1")
  [[ "$got" == "$2" ]] || die "sha256 mismatch for $1: got $got, expected $2"
  echo "sha256 OK: $got"
}
# jq_inplace FILE [jq args...] FILTER
jq_inplace() { local f=$1; shift; jq "$@" "$f" > "$f.tmp" && mv "$f.tmp" "$f"; }
