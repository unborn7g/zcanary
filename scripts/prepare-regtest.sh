#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
unset KEEP
mkdir -p out
rm -f out/app.log out/result.json out/context.json
PREPARE_ONLY=1 exec scripts/spike.sh
