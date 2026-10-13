#!/usr/bin/env bash
# Pool probe: run after scripts/spike.sh (stack up, sender funded in ironwood). Sends 0.1 ZEC to
# (a) the recipient's orchard-only receiver, (b) the recipient's full UA, (c) its sapling receiver.
# Each send is mined in its own block. Writes fixtures/pools/<case>_{op,rawtx,block}.json
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd); cd "$ROOT"
RPC=scripts/rpc.sh; R=http://127.0.0.1:8181; Z=http://127.0.0.1:29232
F=${FIX:-fixtures/pools}; mkdir -p "$F"
j() { python3 -c "import json,sys;d=json.load(sys.stdin);print(eval(sys.argv[1]))" "$1"; }
acc() { $RPC $R z_listaccounts | j "[a for a in d['result'] if a['name']=='$1'][0]['addresses'][0]['ua']"; }
SUA=$(acc sender); RUA=$(acc recipient)
S_ORCH=$($RPC $R z_listunifiedreceivers "[\"$SUA\"]" | j "d['result']['orchard']")
$RPC $R z_listunifiedreceivers "[\"$RUA\"]" > "$F/recipient_receivers.json"
R_ORCH=$(j "d['result']['orchard']" < "$F/recipient_receivers.json")
R_SAP=$(j "d['result']['sapling']" < "$F/recipient_receivers.json")
send() { # case addr policy
  local c=$1 a=$2 p=$3 resp opid txid
  resp=$($RPC $R z_sendmany "[\"$S_ORCH\",[{\"address\":\"$a\",\"amount\":0.1}],1,null,\"$p\"]")
  echo "$resp" > "$F/${c}_sendmany_response.json"
  opid=$(echo "$resp" | j "d.get('result','')"); [ -z "$opid" ] && { echo "$c: rejected: $resp"; return 1; }
  for i in $(seq 40); do $RPC $R z_getoperationresult "[[\"$opid\"]]" > "$F/${c}_op.json"
    [ "$(j "len(d['result'])" < "$F/${c}_op.json")" -gt 0 ] && break; sleep 2; done
  txid=$(j "d['result'][0]['result']['txid']" < "$F/${c}_op.json")
  $RPC $Z generate '[1]' >/dev/null; sleep 6
  $RPC $Z getrawtransaction "[\"$txid\",1]" > "$F/${c}_rawtx.json"
  $RPC $Z getblock "[\"$(j "d['result']['blockhash']" < "$F/${c}_rawtx.json")\",1]" > "$F/${c}_block.json"
  echo "$c: policy=$p txid=$txid"
}
$RPC $Z generate '[1]' >/dev/null; sleep 6   # settle pending change
send a_orchard_only "$R_ORCH" FullPrivacy
send b_unified "$RUA" FullPrivacy
send c_sapling "$R_SAP" FullPrivacy || send c_sapling "$R_SAP" AllowRevealedAmounts
python3 scripts/pool_table.py "$F"
