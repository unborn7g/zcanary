from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_diagnostic_script_emits_fourteen_bounded_redacted_probes_and_ports():
    script_path = ROOT / "scripts" / "diagnose-wallet-path.sh"
    assert script_path.is_file()
    script = script_path.read_text(encoding="utf-8")

    assert "wallet path probes" in script
    assert "--connect-timeout 5" in script
    assert "--max-time 10" in script
    assert "-sS" in script and "-o" in script
    assert "-w '%{http_code} %{time_total}'" in script
    for method in ("getblockchaininfo", "z_listaccounts", "getinfo", "getwalletinfo", "help", "rpc.discover"):
        assert method in script
    for endpoint in ("127.0.0.1:29232", "127.0.0.1:8181/health", "127.0.0.1:8181"):
        assert endpoint in script
    assert len(re.findall(r'^\s*run_probe\s+"', script, re.MULTILINE)) == 14
    assert 'run_probe "m. Zallet direct getwalletinfo (wrong password; expect fast 401)"' in script
    assert 'run_probe "n. Zallet direct getwalletinfo (zebra:zebra credentials)"' in script
    assert 'local wrong_password="zcanary-diagnostic-invalid-password"' in script
    assert 'export ZCANARY_DIAGNOSTIC_WRONG_PASSWORD="$wrong_password"' in script
    assert "Z3_ZALLET_HOST_RPC_PORT:-50232" in script
    assert "docker compose --env-file .env.regtest port zallet 28232" in script
    assert "docker compose --env-file .env.regtest ps" in script
    assert "report_wallet_password_sanity" in script
    assert "contains_whitespace" in script and "contains_non_hex" in script
    assert "docker compose --env-file .env.regtest top zallet" in script
    assert "docker stats --no-stream" in script
    assert "docker compose --env-file .env.regtest logs --no-color --tail 200 zallet" in script
    assert 'tail -n "$line_count"' in script and '"$Z3_DIR" 200' in script
    assert "redact_diagnostic" in script
    assert "160" in script


def test_basic_auth_uses_private_temp_config_not_process_substitution():
    script = (ROOT / "scripts" / "diagnose-wallet-path.sh").read_text(encoding="utf-8")
    assert "curl_command+=(--config <(" not in script
    assert 'curl_command+=(--config "$config_file")' in script
    assert 'chmod 600 "$config_file"' in script
    assert 'PROBE_TEMP_FILES+=("$config_file")' in script
    assert "ZCANARY_DIAGNOSE_LIBRARY_ONLY" in script


def test_basic_auth_probe_works_against_local_http_server(tmp_path):
    script_path = ROOT / "scripts" / "diagnose-wallet-path.sh"
    script = script_path.read_text(encoding="utf-8")
    assert "ZCANARY_DIAGNOSE_LIBRARY_ONLY" in script
    user = "diagnostic-user"
    password = "local-http-probe-password-not-a-secret"
    expected_auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.server.authorization_headers.append(self.headers.get("Authorization"))
            body = b'{"error":null,"result":{"ok":true}}'
            self.send_response(200 if self.headers.get("Authorization") == expected_auth else 401)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.authorization_headers = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    output = tmp_path / "wallet-path.log"
    temp_dir = tmp_path / "curl-tmp"
    temp_dir.mkdir()
    compose_dir = tmp_path / "z3"
    compose_dir.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "ZCANARY_DIAGNOSE_LIBRARY_ONLY": "1",
            "ZCANARY_DIAGNOSTICS_FILE": str(output),
            "ZCANARY_Z3_DIR": str(compose_dir),
            "ZCANARY_NODE_PASSWORD": password,
            "ZCANARY_ZALLET_USER": user,
            "ZCANARY_ZALLET_PASSWORD": password,
            "TMPDIR": str(temp_dir),
            "TEST_URL": f"http://127.0.0.1:{server.server_port}",
        }
    )
    try:
        proc = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; report_wallet_password_sanity; '
                'run_probe "functional local HTTP" "$TEST_URL" POST getinfo basic '
                '"$ZCANARY_ZALLET_USER" "$ZCANARY_ZALLET_PASSWORD"',
                "bash",
                str(script_path),
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert proc.returncode == 0, proc.stderr
    assert server.authorization_headers == [expected_auth]
    rendered = proc.stdout + proc.stderr + output.read_text(encoding="utf-8")
    assert "HTTP status=200; curl exit=0" in rendered
    assert (
        f"Zallet password sanity: length={len(password)}; "
        "contains_whitespace=no; contains_non_hex=yes"
    ) in rendered
    assert password not in rendered
    assert password[:8] not in rendered and password[-8:] not in rendered
    assert hashlib.sha256(password.encode()).hexdigest() not in rendered
    assert list(temp_dir.iterdir()) == []


def test_diagnostic_probes_use_actual_port_continue_after_timeout_and_redact_bodies(tmp_path):
    script_path = ROOT / "scripts" / "diagnose-wallet-path.sh"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    compose_dir = tmp_path / "z3"
    compose_dir.mkdir()
    trace = tmp_path / "curl-trace.jsonl"
    output = tmp_path / "wallet-startup.log"
    full_address = "uregtest1abcdefghijklmnopqrstuvwxyz0123456789"
    node_password = "node-probe-password-not-a-secret"
    wallet_password = "Q7z! t9L#"
    router_password = "router-probe-password-not-a-secret"

    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env python3
import json, os, re, stat, sys
from pathlib import Path
args = sys.argv[1:]
url = args[-1]
output = Path(args[args.index("-o") + 1])
write_out = args[args.index("-w") + 1]
data = json.loads(args[args.index("-d") + 1]) if "-d" in args else None
method = data["method"] if data else "GET"
headers = [args[i + 1] for i, value in enumerate(args[:-1]) if value == "-H"]
config_mode = None
credentials = None
if "--config" in args:
    config_path = Path(args[args.index("--config") + 1])
    try:
        config_mode = oct(stat.S_IMODE(config_path.stat().st_mode))
        match = re.search(r'(?m)^user = "([^:]*):([^"]*)"$', config_path.read_text())
        if match:
            credentials = (match.group(1), match.group(2))
    except OSError:
        config_mode = "unavailable"
valid_credentials = {
    (os.environ["ZCANARY_ZALLET_USER"], os.environ["ZCANARY_ZALLET_PASSWORD"]),
    (os.environ["Z3_REGTEST_RPC_ROUTER_USER"], os.environ["Z3_REGTEST_RPC_ROUTER_PASSWORD"]),
}
record = {
    "url": url,
    "method": method,
    "auth": "--config" in args or "--user" in args or "-u" in args,
    "config_mode": config_mode,
    "authorization_header": any(h.lower().startswith("authorization:") for h in headers),
    "connect_timeout": args[args.index("--connect-timeout") + 1],
    "max_time": args[args.index("--max-time") + 1],
    "write_out": write_out,
    "disable": args[0] == "--disable",
    "silent_show_error": "-sS" in args,
}
with open(os.environ["CURL_TRACE"], "a", encoding="utf-8") as trace:
    trace.write(json.dumps(record) + "\\n")
body = (
    os.environ["FULL_ADDRESS"] + " " + os.environ["ZCANARY_NODE_PASSWORD"] + " "
    + os.environ["ZCANARY_ZALLET_PASSWORD"] + " "
    + os.environ["Z3_REGTEST_RPC_ROUTER_PASSWORD"] + " probe body"
)
output.write_text(body, encoding="utf-8")
if url == "http://127.0.0.1:8181" and method == "z_listaccounts" and record["auth"]:
    print("000 10.000", end="")
    sys.exit(28)
if url == "http://127.0.0.1:55032" and method == "getinfo" and not record["auth"]:
    print("401 0.012", end="")
    sys.exit(0)
if url == "http://127.0.0.1:55032" and record["auth"] and credentials not in valid_credentials:
    print("401 0.010", end="")
    sys.exit(0)
print("200 0.012", end="")
sys.exit(0)
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
if args[-3:] == ["port", "zallet", "28232"]:
    print("0.0.0.0:55032")
elif args[-1:] == ["ps"]:
    print("NAME SERVICE STATUS PORTS")
    print("z3-regtest-zallet-1 zallet Up 0.0.0.0:55032->28232/tcp")
elif args[-2:] == ["top", "zallet"]:
    print("container-top-lines")
elif args == ["stats", "--no-stream"]:
    print("CONTAINER CPU % MEM USAGE / LIMIT")
elif "logs" in args and args[-1:] == ["zallet"]:
    for index in range(205):
        print(f"zallet-log-line-{index} {os.environ['ZCANARY_ZALLET_PASSWORD']} {os.environ['ZCANARY_DIAGNOSTIC_WRONG_PASSWORD']}")
else:
    sys.exit(2)
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "CURL_TRACE": str(trace),
            "FULL_ADDRESS": full_address,
            "ZCANARY_DIAGNOSTICS_FILE": str(output),
            "ZCANARY_Z3_DIR": str(compose_dir),
            "ZCANARY_NODE_USER": "zebra",
            "ZCANARY_NODE_PASSWORD": node_password,
            "ZCANARY_ZALLET_USER": "zebra",
            "ZCANARY_ZALLET_PASSWORD": wallet_password,
            "Z3_REGTEST_RPC_ROUTER_USER": "router-user",
            "Z3_REGTEST_RPC_ROUTER_PASSWORD": router_password,
        }
    )
    proc = subprocess.run(
        ["bash", str(script_path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    records = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 14
    assert [record["url"] for record in records] == [
        "http://127.0.0.1:29232",
        "http://127.0.0.1:8181/health",
        "http://127.0.0.1:8181",
        "http://127.0.0.1:8181",
        "http://127.0.0.1:55032",
        "http://127.0.0.1:55032",
        "http://127.0.0.1:8181",
        "http://127.0.0.1:55032",
        "http://127.0.0.1:55032",
        "http://127.0.0.1:55032",
        "http://127.0.0.1:55032",
        "http://127.0.0.1:55032",
        "http://127.0.0.1:55032",
        "http://127.0.0.1:55032",
    ]
    assert [record["method"] for record in records] == [
        "getblockchaininfo", "GET", "getblockchaininfo", "z_listaccounts",
        "z_listaccounts", "z_listaccounts", "z_listaccounts", "getinfo", "getinfo",
        "getwalletinfo", "help", "rpc.discover", "getwalletinfo", "getwalletinfo",
    ]
    assert [record["auth"] for record in records] == [
        True, False, True, True, True, True, False, False, True, True, True, True, True, True,
    ]
    for record in records:
        assert record["connect_timeout"] == "5"
        assert record["max_time"] == "10"
        assert record["write_out"] == "%{http_code} %{time_total}"
        assert record["disable"] and record["silent_show_error"]
    assert all(record["config_mode"] == "0o600" for record in records if record["auth"])
    assert all(record["config_mode"] is None for record in records if not record["auth"])
    assert not records[6]["authorization_header"]

    rendered = proc.stdout + proc.stderr + output.read_text(encoding="utf-8")
    for secret in (node_password, wallet_password, router_password, full_address, "zcanary-diagnostic-invalid-password"):
        assert secret not in rendered
    assert wallet_password[:8] not in rendered and wallet_password[-8:] not in rendered
    assert "Zallet password sanity: length=9; contains_whitespace=yes; contains_non_hex=yes" in rendered
    assert "docker compose --env-file .env.regtest ps" in rendered
    assert "docker compose --env-file .env.regtest top zallet" in rendered
    assert "docker stats --no-stream" in rendered
    assert "docker compose --env-file .env.regtest logs --no-color --tail 200 zallet" in rendered
    assert "container-top-lines" in rendered and "CONTAINER CPU % MEM USAGE / LIMIT" in rendered
    assert "last 200 output lines follow" in rendered
    assert "zallet-log-line-5 <redacted>" in rendered
    assert "zallet-log-line-204 <redacted>" in rendered
    assert "zallet-log-line-4 " not in rendered
    assert "55032->28232/tcp" in rendered
    assert re.search(r"Router -> Zallet.*curl exit=28.*seconds=10\.000", rendered)
    assert re.search(r"Zallet direct getinfo \(no auth\).*HTTP status=401.*curl exit=0", rendered)
    assert re.search(r"Zallet direct getinfo \(Basic auth\).*HTTP status=200.*curl exit=0", rendered)
    assert re.search(r"wrong password; expect fast 401.*HTTP status=401.*curl exit=0", rendered)
    assert re.search(r"zebra:zebra credentials.*HTTP status=401.*curl exit=0", rendered)
    for label in (
        "j. Zallet direct getwalletinfo (wallet credentials)",
        "k. Zallet direct help (wallet credentials)",
        "l. Zallet direct rpc.discover (wallet credentials)",
    ):
        assert re.search(rf"{re.escape(label)}.*HTTP status=200.*seconds=0[.]012", rendered)
    assert "uregtest1abc…456789" in rendered


def test_spike_runs_path_probes_at_90_seconds_and_again_on_timeout():
    script = (ROOT / "scripts" / "spike.sh").read_text(encoding="utf-8")
    assert script.count('bash "$ROOT/scripts/diagnose-wallet-path.sh"') == 1
    assert 'WAIT_ELAPSED" -ge 90' in script
    assert 'run_wallet_path_diagnostics "$WAIT_ELAPSED" "continued-failure-90s"' in script
    assert 'run_wallet_path_diagnostics "$WAIT_ELAPSED" "wait-timeout"' in script
    first_failure_match = re.search(
        r'if \[ "\$FIRST_READINESS_FAILURE_CAPTURED" -eq 0 \]; then(.*?)\n  fi\n  WAIT_ELAPSED=',
        script,
        re.DOTALL,
    )
    assert first_failure_match is not None
    assert "FIRST_READINESS_FAILURE_CAPTURED=1" in first_failure_match.group(1)
    assert "diagnose-wallet-path.sh" not in first_failure_match.group(1)
    assert "capture_first_failed_wallet_rpc_zallet_logs" in first_failure_match.group(1)
    assert "elapsed=${ZCANARY_DIAGNOSTIC_ELAPSED_S}" in (ROOT / "scripts" / "diagnose-wallet-path.sh").read_text()


def test_diagnose_run_generates_untracked_router_log_override():
    script = (ROOT / "scripts" / "spike.sh").read_text(encoding="utf-8")
    assert "docker-compose.regtest.override.yml" in script
    assert "RUST_LOG: info" in script
    assert "COMPOSE_FILE=docker-compose.yml:docker-compose.regtest.yml:docker-compose.regtest.override.yml" in script


def test_diagnose_workflow_is_manual_bounded_matrix_and_always_uploads_diagnostics():
    path = ROOT / ".github" / "workflows" / "diagnose.yml"
    assert path.is_file()
    workflow = path.read_text(encoding="utf-8")

    assert re.search(r"(?m)^on:\s*$", workflow)
    assert "workflow_dispatch:" in workflow
    assert not re.search(r"(?m)^\s*(?:push|pull_request):", workflow)
    assert "timeout-minutes: 12" in workflow
    assert 'WALLET_RPC_WAIT_BUDGET: "180"' in workflow
    assert "strategy:" in workflow and "fail-fast: false" in workflow
    assert re.search(r"(?m)^\s+matrix:", workflow)
    assert "zallet_name: beta1" in workflow and "zallet_name: beta3" in workflow
    assert "artifact_name: zcanary-wallet-path-diagnostics-beta1" in workflow
    assert "artifact_name: zcanary-wallet-path-diagnostics-beta3" in workflow
    assert "zallet_version: 0.1.0-beta.1" in workflow and "zallet_version: 0.1.0-beta.3" in workflow
    assert "zodlinc/zallet:v0.1.0-beta.1@sha256:1849b4469875dc0165942c06d15fa6a7da76b2d43bade578cc8e5903a639869d" in workflow
    assert "zodlinc/zallet:v0.1.0-beta.3@sha256:54d1a774317371b1e88c8a54712cdfa4aa7d8f20ae2a0205f9a190e502aef0f6" in workflow
    assert "Z3_ZALLET_IMAGE: ${{ matrix.zallet_image }}" in workflow
    assert "ZCANARY_ZALLET_VERSION: ${{ matrix.zallet_version }}" in workflow
    assert "Z3_REF: e84ce9fd8e864ff0b2a8a62f6ce14392145db0fb" in workflow
    assert "bash scripts/prepare-regtest.sh" in workflow
    assert "ZCANARY_DIAG_PREPARE_STARTED_EPOCH" in workflow
    probe_step = workflow.split("name: Run wallet path probes after prepare", 1)[1].split("      - name:", 1)[0]
    assert "if: always()" in probe_step
    assert "bash scripts/diagnose-wallet-path.sh" in probe_step
    assert "wallet path probes complete" not in probe_step
    assert "post-prepare" in probe_step
    assert "secrets.ZCANARY_ZEBRA_PASSWORD" in workflow
    assert "secrets.ZCANARY_ZALLET_PASSWORD" in workflow
    assert re.search(r"(?m)^\s+Z3_ZALLET_RUST_LOG:\s*debug\s*$", workflow)
    assert "name: ${{ matrix.artifact_name }}" in workflow
    assert "if: always()" in workflow
    assert "path: out/diagnostics" in workflow

    spike = (ROOT / "scripts" / "spike.sh").read_text(encoding="utf-8")
    assert "Z3_ZALLET_IMAGE=${Z3_ZALLET_IMAGE:-zodlinc/zallet:v0.1.0-beta.1@sha256:1849b4469875dc0165942c06d15fa6a7da76b2d43bade578cc8e5903a639869d}" in spike
    assert 'version == "0.1.0-beta.3" and "allow_insecure_remote_bind" not in text' in spike
    assert 'allow_insecure_remote_bind = true' in spike
