#!/usr/bin/env bash
# Legacy transaction-pool probe. Run on the disposable regtest only, after funding the sender.
# Writes raw fixtures locally; never upload them because they include wallet and transaction data.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd); cd "$ROOT"
RPC=$ROOT/scripts/rpc.sh
R=${ZCANARY_ZALLET_RPC:-http://127.0.0.1:8181}
Z=${ZCANARY_NODE_RPC:-http://127.0.0.1:29232}
: "${ZCANARY_ZALLET_USER:?set ZCANARY_ZALLET_USER in the environment}"
: "${ZCANARY_ZALLET_PASSWORD:?set ZCANARY_ZALLET_PASSWORD in the environment}"
: "${ZCANARY_NODE_USER:?set ZCANARY_NODE_USER in the environment}"
: "${ZCANARY_NODE_PASSWORD:?set ZCANARY_NODE_PASSWORD in the environment}"
rpc_wallet() { RPC_USER="$ZCANARY_ZALLET_USER" RPC_PASS="$ZCANARY_ZALLET_PASSWORD" "$RPC" "$R" "$@"; }
rpc_node() { RPC_USER="$ZCANARY_NODE_USER" RPC_PASS="$ZCANARY_NODE_PASSWORD" "$RPC" "$Z" "$@"; }
F=${FIX:-fixtures/pools}; mkdir -p "$F"
j() { python3 -c "import json,sys;d=json.load(sys.stdin);print(eval(sys.argv[1]))" "$1"; }
acc() { rpc_wallet z_listaccounts | j "[a for a in d['result'] if a['name']=='$1'][0]['addresses'][0]['ua']"; }
SUA=$(acc sender); RUA=$(acc recipient)
S_ORCH=$(rpc_wallet z_listunifiedreceivers "[\"$SUA\"]" | j "d['result']['orchard']")
rpc_wallet z_listunifiedreceivers "[\"$RUA\"]" > "$F/recipient_receivers.json"
R_ORCH=$(j "d['result']['orchard']" < "$F/recipient_receivers.json")
R_SAP=$(j "d['result']['sapling']" < "$F/recipient_receivers.json")
send() { # case addr policy
  local c=$1 a=$2 p=$3 resp opid txid
  if ! resp=$(rpc_wallet z_sendmany "[\"$S_ORCH\",[{\"address\":\"$a\",\"amount\":0.1}],1,null,\"$p\"]"); then
    echo "$c: rejected by wallet policy"
    return 1
  fi
  opid=$(echo "$resp" | j "d.get('result','')")
  [ -z "$opid" ] && { echo "$c: rejected by wallet policy"; return 1; }
  echo "$resp" > "$F/${c}_sendmany_response.json"
  for i in $(seq 40); do
    rpc_wallet z_getoperationresult "[[\"$opid\"]]" > "$F/${c}_op.json"
    [ "$(j "len(d['result'])" < "$F/${c}_op.json")" -gt 0 ] && break
    sleep 2
  done
  txid=$(j "d['result'][0]['result']['txid']" < "$F/${c}_op.json")
  rpc_node generate '[1]' >/dev/null; sleep 6
  rpc_node getrawtransaction "[\"$txid\",1]" > "$F/${c}_rawtx.json"
  rpc_node getblock "[\"$(j "d['result']['blockhash']" < "$F/${c}_rawtx.json")\",1]" > "$F/${c}_block.json"
  echo "$c: policy=$p completed"
}
rpc_node generate '[1]' >/dev/null; sleep 6   # settle pending change
send a_orchard_only "$R_ORCH" FullPrivacy
send b_unified "$RUA" FullPrivacy
send c_sapling "$R_SAP" FullPrivacy || send c_sapling "$R_SAP" AllowRevealedAmounts
python3 scripts/pool_table.py "$F"
