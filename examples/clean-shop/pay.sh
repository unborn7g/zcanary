#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
exec python3 "$ROOT/examples/common_pay.py" "clean-shop"
