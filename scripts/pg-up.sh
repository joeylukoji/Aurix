#!/usr/bin/env bash
# Starts a throwaway PostgreSQL cluster for the indexer in $RUN_DIR/pg on PG_PORT and prints its URL.
# Runs the server as user `postgres` when invoked as root (initdb refuses to run as root).
# Stop with: pg_ctl -D "$RUN_DIR/pg" stop  (scripts/dev-down.sh does it).
set -euo pipefail
RUN_DIR=${RUN_DIR:?RUN_DIR required}; PG_PORT=${PG_PORT:-5432}; DATA=$RUN_DIR/pg
bin() { command -v "$1" 2>/dev/null || ls -d /usr/lib/postgresql/*/bin/"$1" 2>/dev/null | sort -V | tail -1; }
for b in initdb pg_ctl createdb; do [[ -n "$(bin $b)" ]] || { echo "[pg-up] $b not found: install PostgreSQL or set INDEXER_DATABASE_URL" >&2; exit 1; }; done
AS=(); if [[ $(id -u) == 0 ]]; then AS=(runuser -u postgres --); fi
if [[ ! -f "$DATA/PG_VERSION" ]]; then
  mkdir -p "$DATA"
  if [[ ${#AS[@]} -gt 0 ]]; then
    chown postgres:postgres "$DATA"
    chmod o+x "$RUN_DIR"  # let `postgres` traverse a mktemp (0700) run dir; listing stays denied
  fi
  "${AS[@]}" "$(bin initdb)" -D "$DATA" -U aurix --auth=trust -E UTF8 >/dev/null
fi
"${AS[@]}" "$(bin pg_ctl)" -D "$DATA" -o "-p $PG_PORT -k $DATA -c listen_addresses=127.0.0.1" -l "$DATA/server.log" -w start >/dev/null
"${AS[@]}" "$(bin createdb)" -h 127.0.0.1 -p "$PG_PORT" -U aurix aurix 2>/dev/null || true
echo "postgresql://aurix@127.0.0.1:$PG_PORT/aurix"
