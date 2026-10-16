#!/usr/bin/env bash
# usage: RPC_USER=... RPC_PASS=... scripts/rpc.sh <url> method [json-params]
# Credentials are required from the process environment; never put them in this file.
set -euo pipefail
url=${1:?usage: rpc.sh <url> method [json-params]}
m=${2:?usage: rpc.sh <url> method [json-params]}
p=${3:-[]}
RPC_MAX_TIME=${RPC_MAX_TIME:-20}
if [[ ! "$RPC_MAX_TIME" =~ ^[0-9]+$ || "$RPC_MAX_TIME" =~ ^0+$ ]]; then
  printf 'RPC_MAX_TIME must be a positive integer number of seconds\n' >&2
  exit 2
fi
: "${RPC_USER:?set RPC_USER in the environment}"
: "${RPC_PASS:?set RPC_PASS in the environment}"
body=$(python3 - "$m" "$p" <<'PY'
import json, sys
method, params = sys.argv[1:]
print(json.dumps({"jsonrpc":"2.0", "id":1, "method":method, "params":json.loads(params)}))
PY
)
rpc_user=${RPC_USER//\\/\\\\}; rpc_user=${rpc_user//\"/\\\"}
rpc_pass=${RPC_PASS//\\/\\\\}; rpc_pass=${rpc_pass//\"/\\\"}
# Supply curl authentication via a process-substitution config so the password is not
# included in curl's command-line arguments. Keep the body out of stdout until the
# response status is known so a non-200 body cannot be mistaken for a JSON-RPC result.
response_file=$(mktemp "${TMPDIR:-/tmp}/zcanary-rpc.XXXXXX")
trap 'rm -f "$response_file"' EXIT
if http_status=$(curl --silent --show-error --fail \
  --connect-timeout 5 --max-time "$RPC_MAX_TIME" \
  --output "$response_file" --write-out '%{http_code}' \
  --config <(printf 'user = "%s:%s"\n' "$rpc_user" "$rpc_pass") \
  -X POST -H 'Content-Type: application/json' -d "$body" "$url"); then
  :
else
  status=$?
  http_status=${http_status:-000}
  printf 'RPC HTTP request expected HTTP 200 (received %s; curl exit %s)\n' "$http_status" "$status" >&2
  exit "$status"
fi
if [ "$http_status" != 200 ]; then
  printf 'RPC HTTP request expected HTTP 200 (received %s; curl exit 0)\n' "$http_status" >&2
  exit 22
fi
cat "$response_file"
