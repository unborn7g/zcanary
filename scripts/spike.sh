#!/usr/bin/env bash
# ZCanary Day-1 spike: disposable Z3 regtest -> fund -> shielded send with canary memo -> decode.
# Usage: scripts/spike.sh            (fresh: wipes z3-regtest volumes)
#        KEEP=1 scripts/spike.sh     (reuse running stack)
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd); cd "$ROOT"
Z3_REF=${Z3_REF:-e84ce9fd8e864ff0b2a8a62f6ce14392145db0fb}   # ZcashFoundation/z3 main @ 2026-08-10
OUT=$ROOT/out; mkdir -p "$OUT"
RPC=$ROOT/scripts/rpc.sh
ROUTER=http://127.0.0.1:8181     # rpc-router: Zebra+Zallet merged
ZEBRA=http://127.0.0.1:29232     # direct Zebra
CANARY=${CANARY:-ZCANARY-$(python3 -c 'import secrets;print(secrets.token_hex(4))')}
j() { python3 -c "import json,sys;d=json.load(sys.stdin);print(eval(sys.argv[1]))" "$1"; }
log() { echo "==> $*"; }

# --- 0. host workaround: on this box Docker's legacy iptables FORWARD=DROP blocked
# container<->container traffic on compose bridges. Disabling br_netfilter fixes it.
if [ "$(sysctl -n net.bridge.bridge-nf-call-iptables 2>/dev/null || echo 0)" = 1 ]; then
  log "disabling bridge-nf-call-iptables (container-to-container workaround)"
  sudo sysctl -qw net.bridge.bridge-nf-call-iptables=0 net.bridge.bridge-nf-call-ip6tables=0
fi

# --- 1. Z3 checkout pinned
[ -d z3 ] || git clone -q https://github.com/ZcashFoundation/z3 z3
git -C z3 fetch -q origin && git -C z3 checkout -q "$Z3_REF"
cd z3
DC="docker compose --env-file .env.regtest"
if [ -z "${KEEP:-}" ]; then
  log "resetting regtest stack"
  [ -f .env.regtest ] && $DC --profile "*" down -v >/dev/null 2>&1 || true
  docker volume rm -f z3-regtest-zallet z3-regtest-chain >/dev/null 2>&1 || true
  ./scripts/regtest-init.sh > "$OUT/regtest-init.log" 2>&1
  $DC up -d > "$OUT/compose-up.log" 2>&1
fi
$DC ps --format '{{.Service}} {{.Image}}' > "$OUT/images.txt"
cd "$ROOT"

log "waiting for router + wallet"
for i in $(seq 60); do
  $RPC $ROUTER getwalletinfo 2>/dev/null | grep -q '"result"' && break
  [ $i = 20 ] && (cd z3 && $DC restart rpc-router >/dev/null 2>&1)  # router can wedge if zallet came up late
  sleep 3
done

# --- 2. accounts
acct() { $RPC $ROUTER z_listaccounts | j "[a for a in d['result'] if a['name']=='$1']" ; }
[ "$(acct sender)" = "[]" ] && $RPC $ROUTER z_getnewaccount '["sender"]' >/dev/null
[ "$(acct recipient)" = "[]" ] && $RPC $ROUTER z_getnewaccount '["recipient"]' >/dev/null
SA=$($RPC $ROUTER z_listaccounts | j "[a for a in d['result'] if a['name']=='sender'][0]['account_uuid']")
RA=$($RPC $ROUTER z_listaccounts | j "[a for a in d['result'] if a['name']=='recipient'][0]['account_uuid']")
SUA=$($RPC $ROUTER z_listaccounts | j "[a for a in d['result'] if a['name']=='sender'][0]['addresses'][0]['ua']")
RUA=$($RPC $ROUTER z_listaccounts | j "[a for a in d['result'] if a['name']=='recipient'][0]['addresses'][0]['ua']")
$RPC $ROUTER z_listunifiedreceivers "[\"$SUA\"]" > "$OUT/receivers_sender.json"
$RPC $ROUTER z_listunifiedreceivers "[\"$RUA\"]" > "$OUT/receivers_recipient.json"
S_ORCH=$(j "d['result']['orchard']" < "$OUT/receivers_sender.json")
S_T=$(j "d['result']['p2pkh']" < "$OUT/receivers_sender.json")

# --- 3. fund: shielded coinbase straight to the sender's orchard receiver (lands in ironwood pool
# after NU6.3), plus 100 maturity blocks. (Alt path also tested: mine to p2pkh + z_shieldcoinbase.)
log "mining: 1 shielded-coinbase block to sender, 100 maturity blocks"
$RPC $ZEBRA generatetoaddress "[1,\"$S_ORCH\"]" >/dev/null
$RPC $ZEBRA generatetoaddress "[100,\"$S_T\"]" >/dev/null
sleep 8
$RPC $ROUTER z_getbalanceforaccount "[\"$SA\",1]" | tee "$OUT/balance_sender_before.json"; echo

# --- 4. shielded send with canary memo
MEMO=$(python3 -c "print('$CANARY'.encode().hex())")
log "z_sendmany 0.5 ZEC sender->recipient, memo=$CANARY, FullPrivacy"
OPID=$($RPC $ROUTER z_sendmany "[\"$S_ORCH\",[{\"address\":\"$RUA\",\"amount\":0.5,\"memo\":\"$MEMO\"}],1,null,\"FullPrivacy\"]" | j "d['result']")
for i in $(seq 40); do
  $RPC $ROUTER z_getoperationresult "[[\"$OPID\"]]" > "$OUT/op.json"
  [ "$(j "len(d['result'])" < "$OUT/op.json")" -gt 0 ] && break; sleep 2
done
[ "$(j "d['result'][0]['status']" < "$OUT/op.json")" = success ] || { cat "$OUT/op.json"; exit 1; }
TXID=$(j "d['result'][0]['result']['txid']" < "$OUT/op.json"); echo "$TXID" > "$OUT/txid"
$RPC $ZEBRA generate '[1]' >/dev/null; sleep 8

# --- 5. decode + wallet view
$RPC $ZEBRA getrawtransaction "[\"$TXID\",1]" > "$OUT/rawtx.json"
BH=$(j "d['result']['blockhash']" < "$OUT/rawtx.json")
$RPC $ZEBRA getblock "[\"$BH\",1]" > "$OUT/block.json"
$RPC $ROUTER z_viewtransaction "[\"$TXID\"]" > "$OUT/viewtx.json"
$RPC $ROUTER z_getbalanceforaccount "[\"$RA\",1]" > "$OUT/balance_recipient.json"

python3 - "$OUT" "$CANARY" "$RA" <<'PY'
import json,sys,os,glob
out,canary,ra=sys.argv[1:]
L=lambda f: json.load(open(os.path.join(out,f)))['result']
tx=L('rawtx.json'); vt=L('viewtx.json'); blk=L('block.json')
pools={'transparent_vin':len(tx['vin']),'transparent_vout':len(tx['vout']),
 'sapling_spends':len(tx['vShieldedSpend']),'sapling_outputs':len(tx['vShieldedOutput']),
 'orchard_actions':len(tx.get('orchard',{}).get('actions',[])),
 'ironwood_actions':len(tx.get('ironwood',{}).get('actions',[])),'sprout_js':len(tx['vjoinsplit'])}
print('tx',tx['txid'],'version',tx['version'],pools)
recv=[o for o in vt['outputs'] if o.get('account_uuid')==ra]
memo_ok=any(o.get('memoStr')==canary for o in recv)
print('recipient outputs',[(o['pool'],o['value'],o.get('memoStr')) for o in recv])
print('block valuePools deltas',{p['id']:p['valueDeltaZat'] for p in blk['valuePools'] if p['valueDeltaZat']})
leak=[f for f in ['rawtx.json','block.json'] if canary in open(os.path.join(out,f)).read() or canary.encode().hex() in open(os.path.join(out,f)).read()]
ok = pools['transparent_vin']==pools['transparent_vout']==0 and memo_ok and not leak
print('canary in public node data:',leak or 'none')
print('GATE1', 'PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
PY
