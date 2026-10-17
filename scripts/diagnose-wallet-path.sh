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
ZALLET_METHODS_FILE=${ZCANARY_ZALLET_METHODS_FILE:-$ROOT/out/diagnostics/zallet-methods.txt}
case "$ZALLET_METHODS_FILE" in
  /*) ;;
  *) ZALLET_METHODS_FILE="$ROOT/$ZALLET_METHODS_FILE" ;;
esac
mkdir -p "$(dirname "$DIAGNOSTICS_FILE")" "$(dirname "$ZALLET_METHODS_FILE")"

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
    tail -n "$line_count" -- "$log_file" | redact_sensitive_diagnostic
  } | tee -a "$output_file" >&2 || true
  rm -f "$log_file"
  return 0
}

redact_sensitive_diagnostic() {
  redact_diagnostic | python3 -c 'import base64,os,re,sys
text=sys.stdin.read()
for name,secret in os.environ.items():
    if secret and any(part in name.upper() for part in ("COOKIE","SEED","KEY","PASS","TOKEN","SECRET","HASH")):
        encoded=secret.encode("utf-8")
        for value in (secret, encoded.hex(), encoded.hex().upper(), base64.b64encode(encoded).decode("ascii")):
            text=text.replace(value, "<redacted>")
text=re.sub(r"(?i)(https?://)[^/\s@]+@", r"\1<redacted>@", text)
lines=[]
for line in text.splitlines(keepends=True):
    if re.search(r"(?i)(?:cookie|seed|mnemonic|passphrase|secret|(?:private|wallet|access|api)[\s_-]*key|\bkey\b|token|authorization)", line):
        lines.append("<redacted-sensitive-line>\n")
    elif re.search(r"(?i)\b(?:password|passwd|passphrase)\s*[:=]", line):
        lines.append("<redacted-sensitive-line>\n")
    else:
        line=re.sub(r"(?i)(\b(?:git commit|commit|hash|digest|id)\s*[:=]\s*)[^\s,;]+", r"\1<redacted-hash>", line)
        line=re.sub(r"(?<![A-Za-z0-9])[0-9a-f]{7,}(?![A-Za-z0-9])", "<redacted-hash>", line, flags=re.I)
        lines.append(line)
sys.stdout.write("".join(lines))'
}

append_redacted_tail() {
  local output_file="$1" label="$2" working_dir="$3"
  shift 3
  append_redacted_lines "$output_file" "$label" "$working_dir" 80 "$@"
}

append_redacted_head() {
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
    printf '==> %s (exit %s); first %s output lines follow\n' "$label" "$status" "$line_count"
    head -n "$line_count" -- "$log_file" | redact_sensitive_diagnostic
  } | tee -a "$output_file" >&2 || true
  rm -f "$log_file"
  return 0
}

capture_host_diagnostics() {
  append_redacted_lines \
    "$DIAGNOSTICS_FILE" \
    "docker version" \
    "$Z3_DIR" 80 \
    timeout --kill-after=1s 10s docker version
  append_redacted_head \
    "$DIAGNOSTICS_FILE" \
    "docker info (first 40 lines)" \
    "$Z3_DIR" 40 \
    timeout --kill-after=1s 10s docker info
  append_redacted_lines \
    "$DIAGNOSTICS_FILE" \
    "uname -a" \
    "$Z3_DIR" 20 \
    uname -a
  append_redacted_lines \
    "$DIAGNOSTICS_FILE" \
    "df -h /var/lib/docker" \
    "$Z3_DIR" 20 \
    df -h /var/lib/docker
  append_redacted_lines \
    "$DIAGNOSTICS_FILE" \
    "docker volume inspect z3-regtest-zallet (names and driver only)" \
    "$Z3_DIR" 20 \
    timeout --kill-after=1s 10s docker volume inspect --format '{{.Name}} {{.Driver}}' z3-regtest-zallet
}

extract_method_names() {
  local response_file="$1" jq_result
  if command -v jq >/dev/null 2>&1; then
    if jq_result=$(jq -r '
      def rpc_names:
        if type == "object" then
          if (.methods? | type) == "array" then
            .methods[] | rpc_names
          elif ((.name? // .method? // .command?) | type) == "string" then
            (.name // .method // .command)
          elif has("result") then
            .result | rpc_names
          else
            empty
          end
        elif type == "array" then
          .[] | rpc_names
        elif type == "string" then
          split("\n")[]
          | select((test("^\\s*==") | not))
          | (try capture("^\\s*(?<method>[A-Za-z][A-Za-z0-9_.]*)") catch empty)
          | .method
        else
          empty
        end;
      rpc_names
      | select(type == "string" and test("^[A-Za-z][A-Za-z0-9_.]*$"))
    ' "$response_file" 2>/dev/null) && [ -n "$jq_result" ]; then
      printf '%s\n' "$jq_result"
      return 0
    fi
  fi

  python3 - "$response_file" <<'PYTHON'
import json
import re
import sys
from pathlib import Path

try:
    document = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(0)

valid_name = re.compile(r"[A-Za-z][A-Za-z0-9_.]*\Z")
line_name = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_.]*)")

def names(value):
    if isinstance(value, dict):
        methods = value.get("methods")
        if isinstance(methods, list):
            for method in methods:
                yield from names(method)
        else:
            for key in ("name", "method", "command"):
                candidate = value.get(key)
                if isinstance(candidate, str):
                    yield candidate
                    return
            if "result" in value:
                yield from names(value["result"])
    elif isinstance(value, list):
        for item in value:
            yield from names(item)
    elif isinstance(value, str):
        for line in value.splitlines():
            if line.lstrip().startswith("=="):
                continue
            match = line_name.match(line)
            if match:
                yield match.group(1)

for name in names(document):
    if valid_name.fullmatch(name):
        print(name)
PYTHON
}

capture_zallet_method_names() {
  local host_port="$1" help_file="$2" discover_file help_names discover_names selected_names sorted_file
  local count has_z_listaccounts=no has_z_getnewaccount=no has_listaddresses=no

  discover_file=$(mktemp "${TMPDIR:-/tmp}/zcanary-wallet-method-discover.XXXXXX")
  help_names=$(mktemp "${TMPDIR:-/tmp}/zcanary-wallet-method-help-names.XXXXXX")
  discover_names=$(mktemp "${TMPDIR:-/tmp}/zcanary-wallet-method-discover-names.XXXXXX")
  selected_names=$(mktemp "${TMPDIR:-/tmp}/zcanary-wallet-methods.XXXXXX")
  sorted_file=$(mktemp "$(dirname "$ZALLET_METHODS_FILE")/.zallet-methods.XXXXXX")
  PROBE_TEMP_FILES+=("$discover_file" "$help_names" "$discover_names" "$selected_names" "$sorted_file")

  run_probe "method-name rpc.discover" "http://127.0.0.1:$host_port" \
    POST rpc.discover basic "$WALLET_USER" "$ZCANARY_ZALLET_PASSWORD" 30 '[]' 400 "$discover_file"
  extract_method_names "$discover_file" > "$discover_names" || true
  extract_method_names "$help_file" > "$help_names" || true
  if [ -s "$discover_names" ]; then
    cat "$discover_names" > "$selected_names"
  else
    cat "$help_names" > "$selected_names"
  fi
  LC_ALL=C sort -u "$selected_names" -o "$sorted_file"
  mv -f "$sorted_file" "$ZALLET_METHODS_FILE"

  count=$(wc -l < "$ZALLET_METHODS_FILE")
  count=${count//[[:space:]]/}
  if grep -Fqx -- z_listaccounts "$ZALLET_METHODS_FILE"; then has_z_listaccounts=yes; fi
  if grep -Fqx -- z_getnewaccount "$ZALLET_METHODS_FILE"; then has_z_getnewaccount=yes; fi
  if grep -Fqx -- listaddresses "$ZALLET_METHODS_FILE"; then has_listaddresses=yes; fi
  printf 'Zallet methods: count=%s; z_listaccounts=%s; z_getnewaccount=%s; listaddresses=%s\n' \
    "$count" "$has_z_listaccounts" "$has_z_getnewaccount" "$has_listaddresses" \
    | tee -a "$DIAGNOSTICS_FILE"
}

run_probe() {
  local label="$1" url="$2" verb="$3" rpc_method="$4" auth_mode="$5"
  local auth_user="${6:-}" auth_password="${7:-}"
  local max_time="${8:-10}" params_json="${9:-[]}"
  local preview_limit="${10:-160}" response_copy="${11:-}"
  local body_file metadata_file curl_error_file config_file="" json_body=""
  local curl_exit=0 http_status=000 elapsed=unknown preview="" interpretation=""
  local escaped_user escaped_password
  local -a curl_command

  case "$max_time" in
    10|30|60) ;;
    *) max_time=10 ;;
  esac
  case "$preview_limit" in
    160|400) ;;
    *) preview_limit=160 ;;
  esac
  body_file=$(mktemp "${TMPDIR:-/tmp}/zcanary-wallet-probe-body.XXXXXX")
  metadata_file=$(mktemp "${TMPDIR:-/tmp}/zcanary-wallet-probe-meta.XXXXXX")
  curl_error_file=$(mktemp "${TMPDIR:-/tmp}/zcanary-wallet-probe-error.XXXXXX")
  PROBE_TEMP_FILES+=("$body_file" "$metadata_file" "$curl_error_file")

  curl_command=(curl --disable --connect-timeout 5 --max-time "$max_time" -sS \
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
    json_body=$(printf '{"jsonrpc":"2.0","id":1,"method":"%s","params":%s}' "$rpc_method" "$params_json")
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
  if [ -n "$response_copy" ]; then
    cp -- "$body_file" "$response_copy"
  fi
  if preview=$(redact_sensitive_diagnostic < "$body_file" | python3 -c 'import sys; text=sys.stdin.read()[:int(sys.argv[1])]; print("".join(" " if ord(ch)<32 or ord(ch) in (127, 0x2028, 0x2029) else ch for ch in text))' "$preview_limit"); then
    :
  else
    preview="<body preview unavailable>"
  fi

  if [[ "$label" == *"Zallet direct getinfo (no auth)"* ]]; then
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
  local mapping_output mapping_exit mapping_line safe_mapping_line host_port
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
  capture_host_diagnostics

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
  safe_mapping_line=$(printf '%s\n' "${mapping_line:-unavailable}" | redact_sensitive_diagnostic)
  printf 'docker compose --env-file .env.regtest port zallet 28232: %s (exit %s)\n' \
    "$safe_mapping_line" "$mapping_exit" | tee -a "$DIAGNOSTICS_FILE"

  run_probe "a. Zebra direct" "http://127.0.0.1:29232" POST getblockchaininfo basic \
    "$NODE_USER" "$ZCANARY_NODE_PASSWORD"
  run_probe "b. Router health" "http://127.0.0.1:8181/health" GET "" none
  run_probe "c. Router -> Zebra" "http://127.0.0.1:8181" POST getblockchaininfo basic \
    "$WALLET_USER" "$ZCANARY_ZALLET_PASSWORD"

  local help_response
  help_response=$(mktemp "${TMPDIR:-/tmp}/zcanary-wallet-method-help.XXXXXX")
  PROBE_TEMP_FILES+=("$help_response")
  run_probe "d. Zallet direct getwalletinfo (10-second timeout)" "http://127.0.0.1:$host_port" \
    POST getwalletinfo basic "$WALLET_USER" "$ZCANARY_ZALLET_PASSWORD" 10 '[]' 400
  run_probe "e. Zallet direct z_listaccounts (empty wallet; 10-second timeout)" "http://127.0.0.1:$host_port" \
    POST z_listaccounts basic "$WALLET_USER" "$ZCANARY_ZALLET_PASSWORD" 10 '[]' 400
  run_probe "f. Zallet direct z_getnewaccount (diag account; 60-second timeout)" "http://127.0.0.1:$host_port" \
    POST z_getnewaccount basic "$WALLET_USER" "$ZCANARY_ZALLET_PASSWORD" 60 '["diag"]' 400
  run_probe "g. Zallet direct z_listaccounts (after account creation; 60-second timeout)" "http://127.0.0.1:$host_port" \
    POST z_listaccounts basic "$WALLET_USER" "$ZCANARY_ZALLET_PASSWORD" 60 '[]' 400
  run_probe "h. Zallet direct listaddresses (30-second timeout)" "http://127.0.0.1:$host_port" \
    POST listaddresses basic "$WALLET_USER" "$ZCANARY_ZALLET_PASSWORD" 30 '[]' 400
  run_probe "i. Zallet direct getwalletstatus (30-second timeout)" "http://127.0.0.1:$host_port" \
    POST getwalletstatus basic "$WALLET_USER" "$ZCANARY_ZALLET_PASSWORD" 30 '[]' 400
  run_probe "j. Zallet direct help (30-second timeout)" "http://127.0.0.1:$host_port" \
    POST help basic "$WALLET_USER" "$ZCANARY_ZALLET_PASSWORD" 30 '[]' 400 "$help_response"
  capture_zallet_method_names "$host_port" "$help_response"

  run_probe "k. Router -> Zallet (wallet credentials; after account creation)" \
    "http://127.0.0.1:8181" POST z_listaccounts basic "$WALLET_USER" "$ZCANARY_ZALLET_PASSWORD"
  run_probe "l. Zallet direct z_listaccounts (router credentials; after account creation)" \
    "http://127.0.0.1:$host_port" POST z_listaccounts basic "$ROUTER_USER" "$ROUTER_PASSWORD"
  run_probe "m. Router -> Zallet (no client auth; one upstream Authorization)" \
    "http://127.0.0.1:8181" POST z_listaccounts none
  run_probe "n. Zallet direct getinfo (no auth)" "http://127.0.0.1:$host_port" \
    POST getinfo none
  run_probe "o. Zallet direct getinfo (Basic auth)" "http://127.0.0.1:$host_port" \
    POST getinfo basic "$WALLET_USER" "$ZCANARY_ZALLET_PASSWORD"
  run_probe "p. Zallet direct getwalletinfo (wrong password; expect fast 401)" \
    "http://127.0.0.1:$host_port" POST getwalletinfo basic "$WALLET_USER" "$wrong_password"
  run_probe "q. Zallet direct getwalletinfo (zebra:zebra credentials)" \
    "http://127.0.0.1:$host_port" POST getwalletinfo basic zebra zebra

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
  append_redacted_tail \
    "$DIAGNOSTICS_FILE" \
    "docker compose --env-file .env.regtest exec -T zallet ls -la /var/lib/zallet" \
    "$Z3_DIR" \
    timeout --kill-after=1s 10s docker compose --env-file .env.regtest exec -T zallet ls -la /var/lib/zallet

  printf '==> wallet path probes complete batch=%s elapsed=%ss\n' \
    "$DIAGNOSTIC_BATCH" "$DIAGNOSTIC_ELAPSED_S" | tee -a "$DIAGNOSTICS_FILE"
}

if [ "${ZCANARY_DIAGNOSE_LIBRARY_ONLY:-0}" != 1 ]; then
  run_wallet_path_probes
fi
