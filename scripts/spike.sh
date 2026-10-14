#!/usr/bin/env bash
# Disposable Z3 regtest spike. PREPARE_ONLY=1 starts/funds the stack and writes out/context.json;
# otherwise it performs a shielded payment and verifies the captured transaction.
# Usage: scripts/spike.sh (fresh test stack; wipes only the pinned Z3 regtest volumes)
#        KEEP=1 scripts/spike.sh (reuse a running test stack)
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd); cd "$ROOT"
source "$ROOT/scripts/lib/diagnostics.sh"
Z3_REF=${Z3_REF:-e84ce9fd8e864ff0b2a8a62f6ce14392145db0fb}   # Z3 Compose/config pin
BUSYBOX_REF='busybox:1.36.1@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662'
# Keep the previously runtime-tested beta.1 baseline as the default until beta.3
# passes the complete runtime suite. Both images are pinned by immutable digest.
export Z3_ZEBRA_IMAGE=${Z3_ZEBRA_IMAGE:-zfnd/zebra:6.2.3@sha256:bb2a6029db277ee3a10e951dcc0ddd36b4cbcbe0fad684746d695ee21d53fde2}
export Z3_ZALLET_IMAGE=${Z3_ZALLET_IMAGE:-zodlinc/zallet:v0.1.0-beta.1@sha256:1849b4469875dc0165942c06d15fa6a7da76b2d43bade578cc8e5903a639869d}
OUT=${OUT:-$ROOT/out}; mkdir -p "$OUT"
START=$(date +%s)
RPC=$ROOT/scripts/rpc.sh
ROUTER=${ZCANARY_ZALLET_RPC:-http://127.0.0.1:8181}   # Z3 rpc-router (Zallet wallet API)
ZEBRA=${ZCANARY_NODE_RPC:-http://127.0.0.1:29232}      # direct Zebra RPC
: "${ZCANARY_ZALLET_USER:?set ZCANARY_ZALLET_USER in the environment}"
: "${ZCANARY_ZALLET_PASSWORD:?set ZCANARY_ZALLET_PASSWORD in the environment}"
: "${ZCANARY_NODE_USER:?set ZCANARY_NODE_USER in the environment}"
: "${ZCANARY_NODE_PASSWORD:?set ZCANARY_NODE_PASSWORD in the environment}"
rpc_wallet() { RPC_USER="$ZCANARY_ZALLET_USER" RPC_PASS="$ZCANARY_ZALLET_PASSWORD" "$RPC" "$ROUTER" "$@"; }
rpc_node() { RPC_USER="$ZCANARY_NODE_USER" RPC_PASS="$ZCANARY_NODE_PASSWORD" "$RPC" "$ZEBRA" "$@"; }
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
  HELPER_PULL_START=$(python3 -c 'import time; print(time.monotonic_ns())')
  run_with_tail "pinned regtest-init helper image pull" docker pull "$BUSYBOX_REF"
  HELPER_PULL_END=$(python3 -c 'import time; print(time.monotonic_ns())')
  run_with_tail "tag pinned regtest-init helper image version" docker tag "$BUSYBOX_REF" busybox:1.36.1
  run_with_tail "tag pinned regtest-init helper image latest" docker tag "$BUSYBOX_REF" busybox:latest
  [ -f .env.regtest ] && $DC --profile "*" down -v >/dev/null 2>&1 || true
  docker volume rm -f z3-regtest-zallet z3-regtest-chain >/dev/null 2>&1 || true
  # The pinned Z3 initializer derives Zallet's auth hash from this env value.
  export Z3_REGTEST_RPC_ROUTER_PASSWORD="$ZCANARY_ZALLET_PASSWORD"
  run_with_tail "regtest-init --prepare-only" ./scripts/regtest-init.sh --prepare-only
  ZCANARY_ZALLET_VERSION=${ZCANARY_ZALLET_VERSION:-0.1.0-beta.1}
  case "$ZCANARY_ZALLET_VERSION" in
    0.1.0-beta.1) ;;
    0.1.0-beta.3) ;;
    *) echo "unsupported Zallet version profile" >&2; exit 1 ;;
  esac
  python3 - "config/regtest/zallet.toml" "$ZCANARY_ZALLET_VERSION" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
version = sys.argv[2]
text = path.read_text(encoding="utf-8")
text = text.replace('as_of_version = "0.1.0-beta.1"', f'as_of_version = "{version}"')
if version == "0.1.0-beta.3" and "allow_insecure_remote_bind" not in text:
    needle = "[rpc]\n"
    if needle not in text:
        raise SystemExit("pinned Zallet config has no [rpc] section")
    text = text.replace(needle, needle + "allow_insecure_remote_bind = true\n", 1)
path.write_text(text, encoding="utf-8")
PY
  COMPOSE_PULL_START=$(python3 -c 'import time; print(time.monotonic_ns())')
  run_with_tail "pinned Z3 service images pull" $DC pull --ignore-buildable
  COMPOSE_PULL_END=$(python3 -c 'import time; print(time.monotonic_ns())')
  if [ ! -f "$OUT/image-pull-timing.json" ]; then
    python3 - "$HELPER_PULL_START" "$HELPER_PULL_END" "$COMPOSE_PULL_START" "$COMPOSE_PULL_END" "$OUT/image-pull-timing.json" <<'PY'
import json, sys
helper_start, helper_end, compose_start, compose_end, path = sys.argv[1:]
seconds = (
    int(helper_end) - int(helper_start) + int(compose_end) - int(compose_start)
) / 1_000_000_000
with open(path, "w", encoding="utf-8") as f:
    json.dump({"pull_seconds": round(seconds, 3), "measurement": "sum of pinned regtest-init helper and first Compose service pull durations in this workspace"}, f, indent=2)
    f.write("\n")
PY
  fi
  run_with_tail "regtest-init" ./scripts/regtest-init.sh
  run_with_tail "start regtest services" $DC up -d
fi
$DC ps --format '{{.Service}} {{.Image}}' > "$OUT/images.txt"
cd "$ROOT"

log "waiting for router + wallet"
for i in $(seq 60); do
  rpc_wallet getwalletinfo 2>/dev/null | grep -q '"result"' && break
  [ "$i" = 20 ] && (cd z3 && $DC restart rpc-router >/dev/null 2>&1)  # router can wedge if zallet came up late
  sleep 3
done

# --- 2. accounts
acct() { rpc_wallet z_listaccounts | j "[a for a in d['result'] if a['name']=='$1']"; }
[ "$(acct sender)" = "[]" ] && rpc_wallet z_getnewaccount '["sender"]' >/dev/null
[ "$(acct recipient)" = "[]" ] && rpc_wallet z_getnewaccount '["recipient"]' >/dev/null
SA=$(rpc_wallet z_listaccounts | j "[a for a in d['result'] if a['name']=='sender'][0]['account_uuid']")
RA=$(rpc_wallet z_listaccounts | j "[a for a in d['result'] if a['name']=='recipient'][0]['account_uuid']")
SUA=$(rpc_wallet z_listaccounts | j "[a for a in d['result'] if a['name']=='sender'][0]['addresses'][0]['ua']")
RUA=$(rpc_wallet z_listaccounts | j "[a for a in d['result'] if a['name']=='recipient'][0]['addresses'][0]['ua']")
rpc_wallet z_listunifiedreceivers "[\"$SUA\"]" > "$OUT/receivers_sender.json"
# Recipient receiver-set can be varied only for isolated regtest test apps.
RRT=${RECIPIENT_RECEIVERS:-'["orchard","sapling"]'}
[ "$RRT" = default ] || RUA=$(rpc_wallet z_getaddressforaccount "[\"$RA\",$RRT]" | j "d['result']['address']")
rpc_wallet z_listunifiedreceivers "[\"$RUA\"]" > "$OUT/receivers_recipient.json"
S_ORCH=$(j "d['result']['orchard']" < "$OUT/receivers_sender.json")
S_T=$(j "d['result']['p2pkh']" < "$OUT/receivers_sender.json")
R_T=$(j "d['result'].get('p2pkh','')" < "$OUT/receivers_recipient.json")
R_SAP=$(j "d['result'].get('sapling','')" < "$OUT/receivers_recipient.json")

# --- 3. fund: shielded coinbase to sender's Orchard receiver (lands in Ironwood after NU6.3),
# plus transparent coinbase maturity blocks for the pinned test wallet.
log "mining: 1 shielded-coinbase block to sender, 100 maturity blocks"
rpc_node generatetoaddress "[1,\"$S_ORCH\"]" >/dev/null
rpc_node generatetoaddress "[100,\"$S_T\"]" >/dev/null
sleep 8
rpc_wallet z_getbalanceforaccount "[\"$SA\",1]" | tee "$OUT/balance_sender_before.json"; echo
rpc_node getblockchaininfo > "$OUT/chaininfo_before.json"

if [ "${PREPARE_ONLY:-0}" = 1 ]; then
  python3 - "$OUT/context.json" "$S_ORCH" "$RUA" "$RA" "$SA" "$R_T" "$R_SAP" <<'PY'
import json, sys
path, sender, recipient, account, sender_account, transparent, sapling = sys.argv[1:]
with open(path, "w", encoding="utf-8") as f:
    json.dump({
        "network": "regtest",
        "sender_address": sender,
        "sender_account": sender_account,
        "recipient_address": recipient,
        "recipient_account": account,
        "transparent_receiver": transparent,
        "sapling_receiver": sapling,
    }, f, indent=2)
    f.write("\n")
PY
  log "regtest accounts prepared; context written without secrets or memo markers"
  exit 0
fi

# --- 4. shielded send with a per-run memo marker. The value is never printed or written to result.json.
CANARY=${ZCANARY_MEMO:-ZC-M-$(python3 -c 'import secrets;print(secrets.token_hex(12).upper())')}
AMOUNT=${ZCANARY_AMOUNT:-0.5}
MEMO=$(python3 -c 'import sys;print(sys.argv[1].encode().hex())' "$CANARY")
log "z_sendmany $AMOUNT ZEC sender->recipient, memo marker supplied (value redacted), FullPrivacy"
OPID=$(rpc_wallet z_sendmany "[\"$S_ORCH\",[{\"address\":\"$RUA\",\"amount\":$AMOUNT,\"memo\":\"$MEMO\"}],1,null,\"FullPrivacy\"]" | j "d['result']")
for i in $(seq 40); do
  rpc_wallet z_getoperationresult "[[\"$OPID\"]]" > "$OUT/op.json"
  [ "$(j "len(d['result'])" < "$OUT/op.json")" -gt 0 ] && break
  sleep 2
done
[ "$(j "d['result'][0]['status']" < "$OUT/op.json")" = success ] || { echo "payment operation failed (details withheld)" >&2; exit 1; }
TXID=$(j "d['result'][0]['result']['txid']" < "$OUT/op.json"); echo "$TXID" > "$OUT/txid"
rpc_node generate '[1]' >/dev/null; sleep 8

# --- 5. decode + wallet view. The raw wallet view is local test data, not printed or uploaded.
rpc_node getrawtransaction "[\"$TXID\",1]" > "$OUT/rawtx.json"
rpc_node getrawtransaction "[\"$TXID\",0]" > "$OUT/rawtx_hex.json"
rpc_node getblockchaininfo > "$OUT/chaininfo_after.json"
BH=$(j "d['result']['blockhash']" < "$OUT/rawtx.json")
rpc_node getblock "[\"$BH\",1]" > "$OUT/block.json"
rpc_wallet z_viewtransaction "[\"$TXID\"]" > "$OUT/viewtx.json"
rpc_wallet z_getbalanceforaccount "[\"$RA\",1]" > "$OUT/balance_recipient.json"
APP_CHAIN=$(rpc_wallet getblockchaininfo | j "d['result']['chain']")
APP_GENESIS=$(rpc_wallet getblockhash '[0]' | j "d['result']")
case "$APP_GENESIS" in
  00040fe8ec8471911baa1db1266ea15dd06b4a8a5c453883c000b031973dce08) APP_NETWORK=mainnet ;;
  05a60a92d99d85997cce3b87616c089f6124d7342af37106edc76126334a2c38) APP_NETWORK=testnet ;;
  029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327) APP_NETWORK=regtest ;;
  *) APP_NETWORK="unknown:$APP_CHAIN" ;;
esac
python3 - "$OUT/result.json" "$TXID" "$RUA" "$RA" "$APP_NETWORK" "$APP_GENESIS" <<'PY'
import json, sys
path, txid, recipient, account, app_network, app_genesis = sys.argv[1:]
with open(path, "w", encoding="utf-8") as f:
    json.dump({"txid": txid, "recipient_address": recipient,
               "recipient_account": account, "app_network": app_network,
               "app_genesis_hash": app_genesis}, f, indent=2)
    f.write("\n")
PY

python3 - "$OUT" "$CANARY" "$RA" <<'PY'
import json,sys,os
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
print('recipient outputs',[(o['pool'],o['value'],bool(o.get('memoStr'))) for o in recv])
print('recipient memo decrypted:',memo_ok)
print('block valuePools deltas',{p['id']:p['valueDeltaZat'] for p in blk['valuePools'] if p['valueDeltaZat']})
leak=[f for f in ['rawtx.json','block.json'] if canary in open(os.path.join(out,f)).read() or canary.encode().hex() in open(os.path.join(out,f)).read()]
ok = pools['transparent_vin']==pools['transparent_vout']==0 and memo_ok and not leak
print('canary marker in public node data:',leak or 'none')
print('GATE1', 'PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
PY
echo "elapsed_s=$(( $(date +%s) - START ))"
