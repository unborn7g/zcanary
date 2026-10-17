#!/usr/bin/env bash
# Capture setup output and show only a redacted tail when a command fails.
redact_diagnostic() {
  python3 -c 'import base64,os,re,sys
text=sys.stdin.read()
for name,secret in os.environ.items():
    upper=name.upper()
    if secret and any(part in upper for part in ("PASSWORD", "PASS", "TOKEN", "SECRET", "SEED", "PRIVATE_KEY", "MEMO", "AUTH_HASH", "PWHASH")):
        replacement="<canary>" if "MEMO" in upper else "<redacted>"
        encoded=secret.encode("utf-8")
        for value in (secret, encoded.hex(), encoded.hex().upper(), base64.b64encode(encoded).decode("ascii")):
            text=text.replace(value, replacement)
text=re.sub(r"(?i)(authorization\s*:\s*basic\s+)[A-Za-z0-9+/=]+", r"\1<redacted>", text)
text=re.sub(r"(?i)\b[0-9a-f]{32}\$[0-9a-f]{64}\b", "<redacted-auth-hash>", text)
text=re.sub(r"ZC-[MLT]-[A-F0-9]{24}", "<canary>", text, flags=re.IGNORECASE)
text=re.sub(r"5a432d(?:4d|4c|54)2d(?:3[0-9]|4[1-6]){24}", "<canary>", text, flags=re.IGNORECASE)
address_pattern=r"\b(?:u1|utest1|uregtest1|zs1|ztestsapling1|zregtestsapling1|t1|t3|tm|t2)[0-9a-z]{20,}\b"
def shorten_address(match):
    address=match.group(0)
    return f"{address[:12]}…{address[-6:]}"
text=re.sub(address_pattern, shorten_address, text, flags=re.IGNORECASE)
sys.stdout.write(text)'
}

run_with_tail() {
  local label="$1"
  shift
  local log_file status
  log_file=$(mktemp "${TMPDIR:-/tmp}/zcanary-output.XXXXXX") || return 1
  if "$@" >"$log_file" 2>&1; then
    rm -f "$log_file"
    return 0
  else
    status=$?
    printf '==> %s failed (exit %s); last 20 output lines follow\n' "$label" "$status" >&2
    tail -n 20 -- "$log_file" | redact_diagnostic >&2
    rm -f "$log_file"
    return "$status"
  fi
}

append_redacted_tail() {
  local output_file="$1" label="$2" working_dir="$3"
  shift 3
  local log_file status
  mkdir -p "$(dirname "$output_file")"
  log_file=$(mktemp "${TMPDIR:-/tmp}/zcanary-diagnostics.XXXXXX") || return 0
  if (cd "$working_dir" && "$@") >"$log_file" 2>&1; then
    status=0
  else
    status=$?
  fi
  {
    printf '==> %s (exit %s); last 80 output lines follow\n' "$label" "$status"
    tail -n 80 -- "$log_file" | redact_diagnostic
  } | tee -a "$output_file" >&2 || true
  rm -f "$log_file"
  return 0
}

# Count unique physical socket/core pairs, using cpu cores only when topology is
# unavailable. A final one-core default keeps the CI policy deterministic.
detect_physical_cpu_cores() {
  local cpuinfo="${1:-/proc/cpuinfo}" count fallback
  count=$(awk '/^physical id/{p=$4} /^core id/{print p"-"$4}' "$cpuinfo" 2>/dev/null | sort -u | wc -l) || count=0
  count=${count//[[:space:]]/}
  if [[ ! "$count" =~ ^[0-9]+$ ]] || ((count == 0)); then
    fallback=$(awk -F: '/^cpu cores[[:space:]]*:/ {gsub(/[[:space:]]/, "", $2); if ($2 ~ /^[0-9]+$/) {print $2; exit}}' "$cpuinfo" 2>/dev/null) || fallback=""
    count="$fallback"
  fi
  if [[ ! "$count" =~ ^[1-9][0-9]*$ ]]; then
    count=1
  fi
  printf '%s\n' "$count"
}

physical_cpu_workaround_enabled() {
  local mode="${1:-auto}" cores="${2:-1}"
  case "$mode" in
    on) return 0 ;;
    off) return 1 ;;
    auto)
      if [[ "$cores" =~ ^[0-9]+$ ]] && ((cores < 4)); then
        return 0
      fi
      return 1
      ;;
    *) return 2 ;;
  esac
}

write_zcanary_fake_cpuinfo() {
  local output="$1" processor
  : > "$output"
  for processor in 0 1 2 3 4 5 6 7; do
    cat >> "$output" <<CPUINFO
processor : $processor
vendor_id : GenuineIntel
cpu family : 6
model : 85
model name : Intel(R) Xeon(R) Platinum 8171M CPU @ 2.60GHz
stepping : 4
microcode : 0x1
cpu MHz : 2600.000
cache size : 36608 KB
physical id : 0
siblings : 8
core id : $processor
cpu cores : 8
apicid : $processor
initial apicid : $processor

CPUINFO
  done
  chmod 644 "$output"
}
