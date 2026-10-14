#!/usr/bin/env bash
# usage: RPC_USER=... RPC_PASS=... scripts/rpc.sh <url> method [json-params]
# Credentials are required from the process environment; never put them in this file.
set -euo pipefail
url=${1:?usage: rpc.sh <url> method [json-params]}
m=${2:?usage: rpc.sh <url> method [json-params]}
p=${3:-[]}
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
# included in curl's command-line arguments. The RPC response is returned to the caller.
curl --silent --show-error --fail \
  --config <(printf 'user = "%s:%s"\n' "$rpc_user" "$rpc_pass") \
  -X POST -H 'Content-Type: application/json' -d "$body" "$url"
