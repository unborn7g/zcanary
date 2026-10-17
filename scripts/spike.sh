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
OUT=${OUT:-$ROOT/out}
case "$OUT" in
  /*) ;;
  *) OUT="$ROOT/$OUT" ;;
esac
mkdir -p "$OUT"
STARTUP_DIAGNOSTICS="$OUT/diagnostics/wallet-startup.log"
rm -f "$STARTUP_DIAGNOSTICS"
START=$(date +%s)
RPC=$ROOT/scripts/rpc.sh
ROUTER=${ZCANARY_ZALLET_RPC:-http://127.0.0.1:8181}   # Z3 rpc-router (Zallet wallet API)
ZEBRA=${ZCANARY_NODE_RPC:-http://127.0.0.1:29232}      # direct Zebra RPC
rpc_node() { RPC_USER="$ZCANARY_NODE_USER" RPC_PASS="$ZCANARY_NODE_PASSWORD" "$RPC" "$ZEBRA" "$@"; }
j() { python3 -c "import json,sys;d=json.load(sys.stdin);print(eval(sys.argv[1]))" "$1"; }
log() { echo "==> $*"; }
progress() {
  printf '[%s] [ZCANARY-PROGRESS] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"
}
begin_phase() {
  ACTIVE_PHASE="$1"
  progress "phase=start name=$ACTIVE_PHASE"
}
complete_phase() {
  local completed_phase="${1:-$ACTIVE_PHASE}"
  progress "phase=complete name=$completed_phase"
  ACTIVE_PHASE=""
}
WAIT_REPORT_PID=""
WAIT_STATUS_DIR=""
WAIT_STATUS_FILE=""
WALLET_RPC_MAX_TIME=""
start_wallet_rpc_wait_reporter() {
  WAIT_STATUS_DIR=$(mktemp -d "${TMPDIR:-/tmp}/zcanary-wallet-wait.XXXXXX")
  WAIT_STATUS_FILE="$WAIT_STATUS_DIR/status"
  printf '0|pending|unknown|not-started\n' > "$WAIT_STATUS_FILE"
  python3 - "$WAIT_STATUS_FILE" "$WAIT_START" "$WAIT_REPORT_INTERVAL" <<'PY' &
from datetime import datetime, timezone
from pathlib import Path
import sys
import time

status_file = Path(sys.argv[1])
started = int(sys.argv[2])
interval = float(sys.argv[3])
while True:
    time.sleep(interval)
    try:
        attempt, curl_exit, http_status, reason = status_file.read_text(encoding="utf-8").strip().split("|", 3)
    except (OSError, ValueError):
        attempt, curl_exit, http_status, reason = "?", "unknown", "unknown", "status-unavailable"
    elapsed = max(0, int(time.time()) - started)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(
        f"[{timestamp}] [ZCANARY-PROGRESS] wallet RPC attempt {attempt}: "
        f"curl exit {curl_exit} ({reason}); HTTP status {http_status}; elapsed {elapsed}s",
        flush=True,
    )
PY
  WAIT_REPORT_PID=$!
}
write_wallet_rpc_wait_status() {
  local temporary="$WAIT_STATUS_FILE.tmp"
  printf '%s|%s|%s|%s\n' \
    "$WAIT_ATTEMPT" "$WALLET_RPC_LAST_CURL_EXIT" \
    "$WALLET_RPC_LAST_HTTP_STATUS" "$WALLET_RPC_LAST_REASON" > "$temporary"
  mv -f "$temporary" "$WAIT_STATUS_FILE"
}
stop_wallet_rpc_wait_reporter() {
  if [ -n "$WAIT_REPORT_PID" ]; then
    kill "$WAIT_REPORT_PID" 2>/dev/null || true
    wait "$WAIT_REPORT_PID" 2>/dev/null || true
    WAIT_REPORT_PID=""
  fi
  if [ -n "$WAIT_STATUS_DIR" ]; then
    rm -rf "$WAIT_STATUS_DIR"
    WAIT_STATUS_DIR=""
    WAIT_STATUS_FILE=""
  fi
}
ACTIVE_PHASE=bootstrap
DIAGNOSTICS_CAPTURED=0
capture_wallet_startup_diagnostics() {
  if [ "$DIAGNOSTICS_CAPTURED" -eq 1 ]; then
    return 0
  fi
  DIAGNOSTICS_CAPTURED=1
  timeout --kill-after=5s 60s \
    bash "$ROOT/scripts/capture-regtest-diagnostics.sh" "$STARTUP_DIAGNOSTICS" "$ROOT/z3" || true
}
capture_first_failed_wallet_rpc_zallet_logs() {
  local remaining_seconds="${1:-30}"
  append_redacted_tail \
    "$STARTUP_DIAGNOSTICS" \
    "first failed readiness probe: docker compose --env-file .env.regtest logs --no-color --tail 80 zallet" \
    "$ROOT/z3" \
    timeout --kill-after=1s "${remaining_seconds}s" \
      docker compose --env-file .env.regtest logs --no-color --tail 80 zallet
}
run_wallet_path_diagnostics() {
  local elapsed_seconds="$1" batch_label="$2" timeout_seconds="${3:-120}"
  ZCANARY_DIAGNOSTICS_FILE="$STARTUP_DIAGNOSTICS" \
    ZCANARY_Z3_DIR="$ROOT/z3" \
    ZCANARY_DIAGNOSTIC_BATCH="$batch_label" \
    ZCANARY_DIAGNOSTIC_ELAPSED_S="$elapsed_seconds" \
    timeout --kill-after=2s "${timeout_seconds}s" \
      bash "$ROOT/scripts/diagnose-wallet-path.sh" || true
}
on_exit() {
  local status=$?
  trap - EXIT
  stop_wallet_rpc_wait_reporter
  if [ "$status" -ne 0 ]; then
    progress "phase=failed name=${ACTIVE_PHASE:-unknown} exit=$status"
    capture_wallet_startup_diagnostics
  fi
  exit "$status"
}
trap on_exit EXIT
: "${ZCANARY_ZALLET_USER:?set ZCANARY_ZALLET_USER in the environment}"
: "${ZCANARY_ZALLET_PASSWORD:?set ZCANARY_ZALLET_PASSWORD in the environment}"
: "${ZCANARY_NODE_USER:?set ZCANARY_NODE_USER in the environment}"
: "${ZCANARY_NODE_PASSWORD:?set ZCANARY_NODE_PASSWORD in the environment}"
source "$ROOT/scripts/lib/wallet_rpc.sh"

# --- 0. host workaround: on this box Docker's legacy iptables FORWARD=DROP blocked
# container<->container traffic on compose bridges. Disabling br_netfilter fixes it.
begin_phase "host-workaround"
if [ "$(sysctl -n net.bridge.bridge-nf-call-iptables 2>/dev/null || echo 0)" = 1 ]; then
  log "disabling bridge-nf-call-iptables (container-to-container workaround)"
  sudo sysctl -qw net.bridge.bridge-nf-call-iptables=0 net.bridge.bridge-nf-call-ip6tables=0
fi
complete_phase "host-workaround"

# --- 1. Z3 checkout pinned
begin_phase "z3-checkout"
[ -d z3 ] || git clone -q https://github.com/ZcashFoundation/z3 z3
git -C z3 fetch -q origin && git -C z3 checkout -q "$Z3_REF"
cd z3
complete_phase "z3-checkout"
ROUTER_LOG_INFO=${ZCANARY_ROUTER_LOG_INFO:-0}
ZALLET_TOKIO_WORKER_THREADS=${ZCANARY_ZALLET_TOKIO_WORKER_THREADS:-}
if [ -n "$ZALLET_TOKIO_WORKER_THREADS" ] && [[ ! "$ZALLET_TOKIO_WORKER_THREADS" =~ ^[1-9][0-9]*$ ]]; then
  echo "invalid Zallet Tokio worker-thread count; expected a positive integer" >&2
  exit 2
fi
if [ "$ROUTER_LOG_INFO" = 1 ] || [ -n "$ZALLET_TOKIO_WORKER_THREADS" ]; then
  {
    printf 'services:\n'
    if [ "$ROUTER_LOG_INFO" = 1 ]; then
      printf '  rpc-router:\n    environment:\n      RUST_LOG: info\n'
    fi
    if [ -n "$ZALLET_TOKIO_WORKER_THREADS" ]; then
      printf '  zallet:\n    environment:\n      TOKIO_WORKER_THREADS: "%s"\n' "$ZALLET_TOKIO_WORKER_THREADS"
    fi
  } > "$ROOT/z3/docker-compose.regtest.override.yml"
  export COMPOSE_FILE=docker-compose.yml:docker-compose.regtest.yml:docker-compose.regtest.override.yml
fi
DC="docker compose --env-file .env.regtest"
begin_phase "regtest-start"
if [ -z "${KEEP:-}" ]; then
  begin_phase "stack-reset-prepare"
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
complete_phase "stack-reset-prepare"
  ACTIVE_PHASE="regtest-start"
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
complete_phase "regtest-start"

WAIT_INTERVAL=3
WAIT_BUDGET=${WALLET_RPC_WAIT_BUDGET:-360}
WAIT_REPORT_INTERVAL=${WALLET_RPC_WAIT_REPORT_INTERVAL:-30}
WAIT_START=$(date +%s)
WAIT_READY=0
WAIT_ATTEMPT=0
ROUTER_RESTARTED=0
WALLET_RPC_READY=0
FIRST_READINESS_FAILURE_CAPTURED=0
WALLET_PATH_PROBES_AT_90=0
WALLET_RPC_LAST_CURL_EXIT=pending
WALLET_RPC_LAST_HTTP_STATUS=unknown
WALLET_RPC_LAST_REASON=not-started
begin_phase "wallet-rpc-wait"
log "waiting for router + wallet (wall-clock budget ${WAIT_BUDGET}s; poll interval ${WAIT_INTERVAL}s)"
start_wallet_rpc_wait_reporter
while :; do
  WAIT_ELAPSED=$(( $(date +%s) - WAIT_START ))
  [ "$WAIT_ELAPSED" -lt "$WAIT_BUDGET" ] || break
  WAIT_REMAINING=$(( WAIT_BUDGET - WAIT_ELAPSED ))
  PROBE_MAX_TIME=${RPC_MAX_TIME:-20}
  case "$PROBE_MAX_TIME" in
    ''|0|*[!0-9]*) PROBE_MAX_TIME=20 ;;
  esac
  if [ "$PROBE_MAX_TIME" -gt "$WAIT_REMAINING" ]; then
    PROBE_MAX_TIME="$WAIT_REMAINING"
  fi
  WAIT_ATTEMPT=$((WAIT_ATTEMPT + 1))
  WALLET_RPC_MAX_TIME="$PROBE_MAX_TIME"
  if wallet_accounts_ready; then
    WAIT_READY=1
    WALLET_RPC_READY=1
    WALLET_RPC_MAX_TIME=""
    write_wallet_rpc_wait_status
    break
  fi
  WALLET_RPC_MAX_TIME=""
  write_wallet_rpc_wait_status
  if [ "$FIRST_READINESS_FAILURE_CAPTURED" -eq 0 ]; then
    FIRST_READINESS_FAILURE_CAPTURED=1
    WAIT_ELAPSED=$(( $(date +%s) - WAIT_START ))
    WAIT_REMAINING=$(( WAIT_BUDGET - WAIT_ELAPSED ))
    if [ "$WAIT_REMAINING" -gt 0 ]; then
      [ "$WAIT_REMAINING" -le 10 ] || WAIT_REMAINING=10
      capture_first_failed_wallet_rpc_zallet_logs "$WAIT_REMAINING"
    fi
  fi
  WAIT_ELAPSED=$(( $(date +%s) - WAIT_START ))
  if [ "$WAIT_ELAPSED" -ge 90 ] && [ "$WALLET_PATH_PROBES_AT_90" -eq 0 ]; then
    WALLET_PATH_PROBES_AT_90=1
    WAIT_REMAINING=$(( WAIT_BUDGET - WAIT_ELAPSED ))
    if [ "$WAIT_REMAINING" -gt 0 ]; then
      run_wallet_path_diagnostics "$WAIT_ELAPSED" "continued-failure-90s" "$WAIT_REMAINING"
    fi
  fi
  WAIT_ELAPSED=$(( $(date +%s) - WAIT_START ))
  if [ "$WAIT_ELAPSED" -ge 60 ] && [ "$ROUTER_RESTARTED" -eq 0 ]; then
    log "restarting rpc-router once while wallet RPC is unavailable"
    WAIT_REMAINING=$(( WAIT_BUDGET - WAIT_ELAPSED ))
    if [ "$WAIT_REMAINING" -gt 0 ]; then
      (cd "$ROOT/z3" && timeout --kill-after=1s "${WAIT_REMAINING}s" \
        docker compose --env-file .env.regtest restart rpc-router >/dev/null 2>&1) || true
    fi
    ROUTER_RESTARTED=1
  fi
  [ "$WAIT_ELAPSED" -lt "$WAIT_BUDGET" ] || break
  WAIT_REMAINING=$(( WAIT_BUDGET - WAIT_ELAPSED ))
  if [ "$WAIT_REMAINING" -lt "$WAIT_INTERVAL" ]; then
    sleep "$WAIT_REMAINING"
  else
    sleep "$WAIT_INTERVAL"
  fi
done
stop_wallet_rpc_wait_reporter
WAIT_ELAPSED=$(( $(date +%s) - WAIT_START ))
if [ "$WAIT_READY" -eq 1 ]; then
  log "wallet RPC ready after ${WAIT_ELAPSED}s"
  progress "phase=complete name=wallet-rpc-wait elapsed_s=$WAIT_ELAPSED"
  ACTIVE_PHASE=""
else
  run_wallet_path_diagnostics "$WAIT_ELAPSED" "wait-timeout" 120
  capture_wallet_startup_diagnostics
  printf 'wallet RPC not ready after %s s (budget %s s; HTTP status %s; curl exit %s (%s))\n' \
    "$WAIT_ELAPSED" "$WAIT_BUDGET" "$WALLET_RPC_LAST_HTTP_STATUS" \
    "$WALLET_RPC_LAST_CURL_EXIT" "$WALLET_RPC_LAST_REASON" >&2
  exit 1
fi

# --- 2. accounts
begin_phase "accounts"
acct() { rpc_wallet_j "[a for a in d['result'] if a['name']=='$1']" z_listaccounts; }
[ "$(acct sender)" = "[]" ] && rpc_wallet z_getnewaccount '["sender"]' >/dev/null
[ "$(acct recipient)" = "[]" ] && rpc_wallet z_getnewaccount '["recipient"]' >/dev/null
SA=$(rpc_wallet_j "[a for a in d['result'] if a['name']=='sender'][0]['account_uuid']" z_listaccounts)
RA=$(rpc_wallet_j "[a for a in d['result'] if a['name']=='recipient'][0]['account_uuid']" z_listaccounts)
SUA=$(rpc_wallet_j "[a for a in d['result'] if a['name']=='sender'][0]['addresses'][0]['ua']" z_listaccounts)
RUA=$(rpc_wallet_j "[a for a in d['result'] if a['name']=='recipient'][0]['addresses'][0]['ua']" z_listaccounts)
rpc_wallet z_listunifiedreceivers "[\"$SUA\"]" > "$OUT/receivers_sender.json"
# Recipient receiver-set can be varied only for isolated regtest test apps.
RRT=${RECIPIENT_RECEIVERS:-'["orchard","sapling"]'}
[ "$RRT" = default ] || RUA=$(rpc_wallet_j "d['result']['address']" z_getaddressforaccount "[\"$RA\",$RRT]")
rpc_wallet z_listunifiedreceivers "[\"$RUA\"]" > "$OUT/receivers_recipient.json"
S_ORCH=$(j "d['result']['orchard']" < "$OUT/receivers_sender.json")
S_T=$(j "d['result']['p2pkh']" < "$OUT/receivers_sender.json")
R_T=$(j "d['result'].get('p2pkh','')" < "$OUT/receivers_recipient.json")
R_SAP=$(j "d['result'].get('sapling','')" < "$OUT/receivers_recipient.json")
complete_phase "accounts"

# --- 3. fund: shielded coinbase to sender's Orchard receiver (lands in Ironwood after NU6.3),
# plus transparent coinbase maturity blocks for the pinned test wallet.
begin_phase "mine-shielded-block"
log "mining: 1 shielded-coinbase block to sender"
rpc_node generatetoaddress "[1,\"$S_ORCH\"]" >/dev/null
complete_phase "mine-shielded-block"
begin_phase "mine-maturity-blocks"
log "mining: 100 transparent coinbase maturity blocks"
rpc_node generatetoaddress "[100,\"$S_T\"]" >/dev/null
complete_phase "mine-maturity-blocks"
begin_phase "funding-sync"
sleep 8
rpc_wallet z_getbalanceforaccount "[\"$SA\",1]" | tee "$OUT/balance_sender_before.json"; echo
rpc_node getblockchaininfo > "$OUT/chaininfo_before.json"
complete_phase "funding-sync"

if [ "${PREPARE_ONLY:-0}" = 1 ]; then
  begin_phase "prepare-context"
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
complete_phase "prepare-context"
  log "regtest accounts prepared; context written without secrets or memo markers"
  exit 0
fi

# --- 4. shielded send with a per-run memo marker. The value is never printed or written to result.json.
CANARY=${ZCANARY_MEMO:-ZC-M-$(python3 -c 'import secrets;print(secrets.token_hex(12).upper())')}
AMOUNT=${ZCANARY_AMOUNT:-0.5}
MEMO=$(python3 -c 'import sys;print(sys.argv[1].encode().hex())' "$CANARY")
begin_phase "send-payment"
progress "phase=start name=z_sendmany-submission"
log "z_sendmany $AMOUNT ZEC sender->recipient, memo marker supplied (value redacted), FullPrivacy"
OPID=$(rpc_wallet_j "d['result']" z_sendmany "[\"$S_ORCH\",[{\"address\":\"$RUA\",\"amount\":$AMOUNT,\"memo\":\"$MEMO\"}],1,null,\"FullPrivacy\"]")
progress "phase=complete name=z_sendmany-submission"
complete_phase "send-payment"
begin_phase "wait-operation-result"
OP_WAIT_START=$(date +%s)
for i in $(seq 40); do
  rpc_wallet z_getoperationresult "[[\"$OPID\"]]" > "$OUT/op.json"
  [ "$(j "len(d['result'])" < "$OUT/op.json")" -gt 0 ] && break
  sleep 2
done
OP_WAIT_ELAPSED=$(( $(date +%s) - OP_WAIT_START ))
progress "phase=complete name=wait-operation-result elapsed_s=$OP_WAIT_ELAPSED"
ACTIVE_PHASE="payment-result-check"
[ "$(j "d['result'][0]['status']" < "$OUT/op.json")" = success ] || { echo "payment operation failed (details withheld)" >&2; exit 1; }
ACTIVE_PHASE=""
TXID=$(j "d['result'][0]['result']['txid']" < "$OUT/op.json"); echo "$TXID" > "$OUT/txid"
begin_phase "mine-confirmation-block"
rpc_node generate '[1]' >/dev/null
sleep 8
complete_phase "mine-confirmation-block"

# --- 5. decode + wallet view. The raw wallet view is local test data, not printed or uploaded.
begin_phase "decode-transaction"
rpc_node getrawtransaction "[\"$TXID\",1]" > "$OUT/rawtx.json"
rpc_node getrawtransaction "[\"$TXID\",0]" > "$OUT/rawtx_hex.json"
rpc_node getblockchaininfo > "$OUT/chaininfo_after.json"
BH=$(j "d['result']['blockhash']" < "$OUT/rawtx.json")
rpc_node getblock "[\"$BH\",1]" > "$OUT/block.json"
rpc_wallet z_viewtransaction "[\"$TXID\"]" > "$OUT/viewtx.json"
rpc_wallet z_getbalanceforaccount "[\"$RA\",1]" > "$OUT/balance_recipient.json"
APP_CHAIN=$(rpc_wallet_j "d['result']['chain']" getblockchaininfo)
APP_GENESIS=$(rpc_wallet_j "d['result']" getblockhash '[0]')
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
complete_phase "decode-transaction"
begin_phase "verify-captured-transaction"

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
complete_phase "verify-captured-transaction"
echo "elapsed_s=$(( $(date +%s) - START ))"
