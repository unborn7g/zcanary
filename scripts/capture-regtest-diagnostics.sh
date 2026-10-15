#!/usr/bin/env bash
# Collect a redacted Compose status and bounded logs for regtest failure artifacts.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUTPUT_FILE=${1:?usage: capture-regtest-diagnostics.sh OUTPUT_FILE [COMPOSE_DIR]}
COMPOSE_DIR=${2:-$ROOT/z3}
source "$ROOT/scripts/lib/diagnostics.sh"

mkdir -p "$(dirname "$OUTPUT_FILE")"
if [ -f "$OUTPUT_FILE" ]; then
  complete=1
  for label in "docker compose ps -a" \
    "docker compose --env-file .env.regtest logs --no-color --tail 80 zebra" \
    "docker compose --env-file .env.regtest logs --no-color --tail 80 zallet" \
    "docker compose --env-file .env.regtest logs --no-color --tail 80 rpc-router"; do
    if ! grep -Fq "$label" "$OUTPUT_FILE"; then
      complete=0
      break
    fi
  done
  [ "$complete" -eq 1 ] && exit 0
fi
append_redacted_tail \
  "$OUTPUT_FILE" \
  "docker compose ps -a" \
  "$COMPOSE_DIR" \
  docker compose --env-file .env.regtest ps -a
for service in zebra zallet rpc-router; do
  append_redacted_tail \
    "$OUTPUT_FILE" \
    "docker compose --env-file .env.regtest logs --no-color --tail 80 $service" \
    "$COMPOSE_DIR" \
    docker compose --env-file .env.regtest logs --no-color --tail 80 "$service"
done
