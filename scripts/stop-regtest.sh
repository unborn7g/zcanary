#!/usr/bin/env bash
# Stop and remove only the pinned disposable Z3 regtest stack/volumes.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
if [ ! -d "$ROOT/z3" ]; then
  echo "Z3 checkout is absent; nothing to stop."
  exit 0
fi
cd "$ROOT/z3"
if [ ! -f .env.regtest ]; then
  echo "Z3 regtest environment file is absent; refusing a broad Docker cleanup." >&2
  exit 1
fi
docker compose --env-file .env.regtest --profile '*' down -v
