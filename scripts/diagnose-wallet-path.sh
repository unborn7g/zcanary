#!/usr/bin/env bash
# Probe the direct and routed Zebra/Zallet RPC paths without logging credentials or full addresses.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$ROOT/scripts/lib/diagnostics.sh"

DIAGNOSTICS_FILE=${ZCANARY_DIAGNOSTICS_FILE:-$ROOT/out/diagnostics/wallet-startup.log}
case "$DIAGNOSTICS_FILE" in
  /*) ;;
  *) DIAGNOSTICS_FILE="$ROOT/$DIAGNOSTICS_FILE" ;;
esac
Z3_DIR=${ZCANARY_Z3_DIR:-$ROOT/z3}
case "$Z3_DIR" in
  /*) ;;
  *) Z3_DIR="$ROOT/$Z3_DIR" ;;
esac
mkdir -p "$(dirname "$DIAGNOSTICS_FILE")"

: "${ZCANARY_NODE_PASSWORD:?set ZCANARY_NODE_PASSWORD in the environment}"
: "${ZCANARY_ZALLET_PASSWORD:?set ZCANARY_ZALLET_PASSWORD in the environment}"
NODE_USER=${ZCANARY_NODE_USER:-zebra}
WALLET_USER=${ZCANARY_ZALLET_USER:-zebra}
ROUTER_USER=${Z3_REGTEST_RPC_ROUTER_USER:-zebra}
ROUTER_PASSWORD=${Z3_REGTEST_RPC_ROUTER_PASSWORD:-$ZCANARY_ZALLET_PASSWORD}
DIAGNOSTIC_BATCH=${ZCANARY_DIAGNOSTIC_BATCH:-manual}
ZCANARY_DIAGNOSTIC_ELAPSED_S=${ZCANARY_DIAGNOSTIC_ELAPSED_S:-0}
case "$DIAGNOSTIC_BATCH" in
  ''|*[!A-Za-z0-9_-]*) DIAGNOSTIC_BATCH=manual ;;
esac
case "$ZCANARY_DIAGNOSTIC_ELAPSED_S" in
  ''|*[!0-9]*) ZCANARY_DIAGNOSTIC_ELAPSED_S=0 ;;
esac
DIAGNOSTIC_ELAPSED_S=${ZCANARY_DIAGNOSTIC_ELAPSED_S}

# Z3's pinned .env.regtest sets COMPOSE_FILE explicitly, so create a separate,
# generated overlay and add it to that list only for the opt-in diagnose run.
if [ "${ZCANARY_ROUTER_LOG_INFO:-0}" = 1 ] && [ -d "$Z3_DIR" ]; then
  cat > "$Z3_DIR/docker-compose.regtest.override.yml" <<'YAML'
services:
  rpc-router:
    environment:
      RUST_LOG: info
YAML
  export COMPOSE_FILE=docker-compose.yml:docker-compose.regtest.yml:docker-compose.regtest.override.yml
fi

PROBE_TEMP_FILES=()
cleanup_probe_temp_files() {
  if ((${#PROBE_TEMP_FILES[@]})); then
    rm -f -- "${PROBE_TEMP_FILES[@]}"
  fi
}
trap cleanup_probe_temp_files EXIT

escape_curl_config() {
  local value="$1"
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  printf '%s' "$value"
}

report_wallet_password_sanity() {
  local password="$ZCANARY_ZALLET_PASSWORD"
  local contains_whitespace=no contains_non_hex=no
  if [[ "$password" =~ [[:space:]] ]]; then
    contains_whitespace=yes
  fi
  if [[ "$password" =~ [^0-9A-Fa-f] ]]; then
    contains_non_hex=yes
  fi
  printf 'Zallet password sanity: length=%d; contains_whitespace=%s; contains_non_hex=%s\n' \
    "${#password}" "$contains_whitespace" "$contains_non_hex"
}

append_redacted_lines() {
  local output_file="$1" label="$2" working_dir="$3" line_count="$4"
  shift 4
  local log_file status
  mkdir -p "$(dirname "$output_file")"
  log_file=$(mktemp "${TMPDIR:-/tmp}/zcanary-diagnostics.XXXXXX") || return 0
  if (cd "$working_dir" && "$@") >"$log_file" 2>&1; then
    status=0
  else
    status=$?
  fi
  {
    printf '==> %s (exit %s); last %s output lines follow\n' "$label" "$status" "$line_count"
    tail -n "$line_count" -- "$log_file" | redact_diagnostic
  } | tee -a "$output_file" >&2 || true
  rm -f "$log_file"
  return 0
}

run_probe() {
  local label="$1" url="$2" verb="$3" rpc_method="$4" auth_mode="$5"
  local auth_user="${6:-}" auth_password="${7:-}"
  local body_file metadata_file curl_error_file config_file="" json_body=""
  local curl_exit=0 http_status=000 elapsed=unknown preview="" interpretation=""
  local escaped_user escaped_password
  local -a curl_command

  body_file=$(mktemp "${TMPDIR:-/tmp}/zcanary-wallet-probe-body.XXXXXX")
  metadata_file=$(mktemp "${TMPDIR:-/tmp}/zcanary-wallet-probe-meta.XXXXXX")
  curl_error_file=$(mktemp "${TMPDIR:-/tmp}/zcanary-wallet-probe-error.XXXXXX")
  PROBE_TEMP_FILES+=("$body_file" "$metadata_file" "$curl_error_file")

  curl_command=(curl --disable --connect-timeout 5 --max-time 10 -sS \
    -o "$body_file" -w '%{http_code} %{time_total}')
  if [ "$auth_mode" = basic ]; then
    escaped_user=$(escape_curl_config "$auth_user")
    escaped_password=$(escape_curl_config "$auth_password")
    config_file=$(mktemp "${TMPDIR:-/tmp}/zcanary-wallet-probe-config.XXXXXX")
    PROBE_TEMP_FILES+=("$config_file")
    chmod 600 "$config_file"
    printf 'user = "%s:%s"\n' "$escaped_user" "$escaped_password" > "$config_file"
    curl_command+=(--config "$config_file")
  fi
  if [ "$verb" = POST ]; then
    json_body=$(printf '{"jsonrpc":"2.0","id":1,"method":"%s","params":[]}' "$rpc_method")
    curl_command+=(-X POST -H 'Content-Type: application/json' -d "$json_body")
  fi
  curl_command+=("$url")

  if "${curl_command[@]}" > "$metadata_file" 2> "$curl_error_file"; then
    curl_exit=0
  else
    curl_exit=$?
  fi
  if read -r http_status elapsed < "$metadata_file"; then
    :
  fi
  [[ "$http_status" =~ ^[0-9]{3}$ ]] || http_status=000
  [[ "$elapsed" =~ ^[0-9]+([.][0-9]+)?$ ]] || elapsed=unknown
  if preview=$(redact_diagnostic < "$body_file" | python3 -c 'import sys; text=sys.stdin.read()[:160]; print("".join(" " if ord(ch)<32 or ord(ch) in (127, 0x2028, 0x2029) else ch for ch in text))'); then
    :
  else
    preview="<body preview unavailable>"
  fi

  if [ "$label" = "h. Zallet direct getinfo (no auth)" ]; then
    case "$http_status:$curl_exit" in
      401:*) interpretation="; interpretation=HTTP layer alive (authentication required)" ;;
      000:28) interpretation="; interpretation=timeout; endpoint not answering" ;;
    esac
  fi

  printf '%s: HTTP status=%s; curl exit=%s; seconds=%s; body="%s"%s\n' \
    "$label" "$http_status" "$curl_exit" "$elapsed" "$preview" "$interpretation" \
    | tee -a "$DIAGNOSTICS_FILE"
}

run_wallet_path_probes() {
  local mapping_output mapping_exit mapping_line host_port
  local wrong_password="zcanary-diagnostic-invalid-password"
  while [ "$wrong_password" = "$ZCANARY_ZALLET_PASSWORD" ] || [ "$wrong_password" = "$ROUTER_PASSWORD" ]; do
    wrong_password="${wrong_password}x"
  done
  export ZCANARY_DIAGNOSTIC_WRONG_PASSWORD="$wrong_password"

  printf "\n==> wallet path probes batch=%s elapsed=${ZCANARY_DIAGNOSTIC_ELAPSED_S}s at %s\n" \
    "$DIAGNOSTIC_BATCH" "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
    | tee -a "$DIAGNOSTICS_FILE"
  report_wallet_password_sanity | tee -a "$DIAGNOSTICS_FILE"
  append_redacted_tail \
    "$DIAGNOSTICS_FILE" \
    "docker compose --env-file .env.regtest ps" \
    "$Z3_DIR" \
    timeout --kill-after=1s 10s docker compose --env-file .env.regtest ps

  if mapping_output=$(cd "$Z3_DIR" && timeout --kill-after=1s 10s \
    docker compose --env-file .env.regtest port zallet 28232 2>/dev/null); then
    mapping_exit=0
  else
    mapping_exit=$?
    mapping_output=""
  fi
  mapping_line=${mapping_output%%$'\n'*}
  host_port=${mapping_line##*:}
  if [[ ! "$host_port" =~ ^[0-9]+$ ]]; then
    host_port=${Z3_ZALLET_HOST_RPC_PORT:-50232}
  fi
  printf 'docker compose --env-file .env.regtest port zallet 28232: %s (exit %s)\n' \
    "${mapping_line:-unavailable}" "$mapping_exit" | tee -a "$DIAGNOSTICS_FILE"

  run_probe "a. Zebra direct" "http://127.0.0.1:29232" POST getblockchaininfo basic \
    "$NODE_USER" "$ZCANARY_NODE_PASSWORD"
  run_probe "b. Router health" "http://127.0.0.1:8181/health" GET "" none
  run_probe "c. Router -> Zebra" "http://127.0.0.1:8181" POST getblockchaininfo basic \
    "$WALLET_USER" "$ZCANARY_ZALLET_PASSWORD"
  run_probe "d. Router -> Zallet" "http://127.0.0.1:8181" POST z_listaccounts basic \
    "$WALLET_USER" "$ZCANARY_ZALLET_PASSWORD"
  run_probe "e. Zallet direct z_listaccounts (wallet credentials)" "http://127.0.0.1:$host_port" \
    POST z_listaccounts basic "$WALLET_USER" "$ZCANARY_ZALLET_PASSWORD"
  run_probe "f. Zallet direct (router credentials)" "http://127.0.0.1:$host_port" \
    POST z_listaccounts basic "$ROUTER_USER" "$ROUTER_PASSWORD"
  run_probe "g. Router -> Zallet (no client auth; one upstream Authorization)" \
    "http://127.0.0.1:8181" POST z_listaccounts none
  run_probe "h. Zallet direct getinfo (no auth)" "http://127.0.0.1:$host_port" \
    POST getinfo none
  run_probe "i. Zallet direct getinfo (Basic auth)" "http://127.0.0.1:$host_port" \
    POST getinfo basic "$WALLET_USER" "$ZCANARY_ZALLET_PASSWORD"
  run_probe "j. Zallet direct getwalletinfo (wallet credentials)" "http://127.0.0.1:$host_port" \
    POST getwalletinfo basic "$WALLET_USER" "$ZCANARY_ZALLET_PASSWORD"
  run_probe "k. Zallet direct help (wallet credentials)" "http://127.0.0.1:$host_port" \
    POST help basic "$WALLET_USER" "$ZCANARY_ZALLET_PASSWORD"
  run_probe "l. Zallet direct rpc.discover (wallet credentials)" "http://127.0.0.1:$host_port" \
    POST rpc.discover basic "$WALLET_USER" "$ZCANARY_ZALLET_PASSWORD"
  run_probe "m. Zallet direct getwalletinfo (wrong password; expect fast 401)" "http://127.0.0.1:$host_port" \
    POST getwalletinfo basic "$WALLET_USER" "$wrong_password"
  run_probe "n. Zallet direct getwalletinfo (zebra:zebra credentials)" "http://127.0.0.1:$host_port" \
    POST getwalletinfo basic zebra zebra

  printf '==> post-probe runtime snapshots\n' | tee -a "$DIAGNOSTICS_FILE"
  append_redacted_tail \
    "$DIAGNOSTICS_FILE" \
    "docker compose --env-file .env.regtest top zallet" \
    "$Z3_DIR" \
    timeout --kill-after=1s 10s docker compose --env-file .env.regtest top zallet
  append_redacted_tail \
    "$DIAGNOSTICS_FILE" \
    "docker stats --no-stream" \
    "$Z3_DIR" \
    timeout --kill-after=1s 10s docker stats --no-stream
  append_redacted_lines \
    "$DIAGNOSTICS_FILE" \
    "docker compose --env-file .env.regtest logs --no-color --tail 200 zallet" \
    "$Z3_DIR" 200 \
    timeout --kill-after=1s 10s docker compose --env-file .env.regtest logs --no-color --tail 200 zallet

  printf '==> wallet path probes complete batch=%s elapsed=%ss\n' \
    "$DIAGNOSTIC_BATCH" "$DIAGNOSTIC_ELAPSED_S" | tee -a "$DIAGNOSTICS_FILE"
}

if [ "${ZCANARY_DIAGNOSE_LIBRARY_ONLY:-0}" != 1 ]; then
  run_wallet_path_probes
fi
