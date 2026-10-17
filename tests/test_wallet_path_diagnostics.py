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


def test_diagnostic_script_emits_ordered_wallet_probes_and_safe_host_snapshots():
    script_path = ROOT / "scripts" / "diagnose-wallet-path.sh"
    assert script_path.is_file()
    script = script_path.read_text(encoding="utf-8")

    assert "wallet path probes" in script
    assert "--connect-timeout 5" in script
    assert '--max-time "$max_time"' in script
    assert 'max_time="${8:-10}"' in script
    assert 'preview_limit="${10:-160}"' in script
    assert "10|30|60" in script
    assert "400" in script
    assert "-sS" in script and "-o" in script
    assert "-w '%{http_code} %{time_total}'" in script
    for method in (
        "getblockchaininfo", "z_listaccounts", "getinfo", "getwalletinfo", "help",
        "rpc.discover", "listaddresses", "getwalletstatus", "z_getnewaccount",
    ):
        assert method in script
    for endpoint in ("127.0.0.1:29232", "127.0.0.1:8181/health", "127.0.0.1:8181"):
        assert endpoint in script
    assert len(re.findall(r'^\s*run_probe\s+"', script, re.MULTILINE)) == 18

    ordered_labels = (
        'run_probe "d. Zallet direct getwalletinfo (10-second timeout)"',
        'run_probe "e. Zallet direct z_listaccounts (empty wallet; 10-second timeout)"',
        'run_probe "f. Zallet direct z_getnewaccount (diag account; 60-second timeout)"',
        'run_probe "g. Zallet direct z_listaccounts (after account creation; 60-second timeout)"',
        'run_probe "h. Zallet direct listaddresses (30-second timeout)"',
        'run_probe "i. Zallet direct getwalletstatus (30-second timeout)"',
        'run_probe "j. Zallet direct help (30-second timeout)"',
    )
    assert all(label in script for label in ordered_labels)
    positions = [script.index(label) for label in ordered_labels]
    assert positions == sorted(positions)
    assert 'POST z_getnewaccount basic "$WALLET_USER" "$ZCANARY_ZALLET_PASSWORD" 60 ' in script
    assert '["diag"]' in script
    assert 'POST z_listaccounts basic "$WALLET_USER" "$ZCANARY_ZALLET_PASSWORD" 60' in script
    assert 'local wrong_password="zcanary-diagnostic-invalid-password"' in script
    assert 'export ZCANARY_DIAGNOSTIC_WRONG_PASSWORD="$wrong_password"' in script
    assert "Z3_ZALLET_HOST_RPC_PORT:-50232" in script
    assert "docker compose --env-file .env.regtest port zallet 28232" in script
    assert "docker compose --env-file .env.regtest ps" in script
    assert "report_wallet_password_sanity" in script
    assert "contains_whitespace" in script and "contains_non_hex" in script
    assert "capture_zallet_method_names" in script
    assert "command -v jq" in script and "python3 -" in script
    assert "Zallet methods: count=%s; z_listaccounts=%s; z_getnewaccount=%s; listaddresses=%s" in script
    assert "docker version" in script
    assert "docker info (first 40 lines)" in script
    assert "uname -a" in script
    assert "df -h /var/lib/docker" in script
    assert "docker volume inspect --format '{{.Name}} {{.Driver}}' z3-regtest-zallet" in script
    assert "append_redacted_head" in script
    assert "docker compose --env-file .env.regtest top zallet" in script
    assert "docker stats --no-stream" in script
    assert "docker compose --env-file .env.regtest logs --no-color --tail 200 zallet" in script
    assert 'tail -n "$line_count"' in script and '"$Z3_DIR" 200' in script
    assert "docker compose --env-file .env.regtest exec -T zallet ls -la /var/lib/zallet" in script
    assert "cat /var/lib/zallet" not in script


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
    methods_output = tmp_path / "zallet-methods.txt"
    account_created = tmp_path / "account-created"
    docker_trace = tmp_path / "docker-trace.jsonl"
    host_trace = tmp_path / "host-trace.jsonl"
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
params = data["params"] if data else None
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
    "params": params,
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
    + os.environ["Z3_REGTEST_RPC_ROUTER_PASSWORD"]
    + " abcdef1234567890 BODY-BEGIN " + "Z" * 500
)
if method == "help":
    output.write_text(json.dumps({"result": "getwalletinfo\\nz_listaccounts\\nz_getnewaccount\\nlistaddresses\\n"}), encoding="utf-8")
elif method == "rpc.discover":
    output.write_text(json.dumps({"result": {"methods": [
        {"name": "getwalletinfo"}, {"name": "z_listaccounts"},
        {"name": "z_getnewaccount"}, {"name": "listaddresses"},
    ]}}), encoding="utf-8")
else:
    output.write_text(body, encoding="utf-8")
if method == "z_getnewaccount" and params == ["diag"]:
    Path(os.environ["ACCOUNT_CREATED_FILE"]).write_text("created", encoding="utf-8")
if method == "help" and os.environ.get("FAKE_HELP_HANG") == "1":
    print("000 30.000", end="")
    sys.exit(28)
if url == "http://127.0.0.1:55032" and method == "z_listaccounts":
    if not Path(os.environ["ACCOUNT_CREATED_FILE"]).exists():
        print("000 10.000", end="")
        sys.exit(28)
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
import json, os, sys
args = sys.argv[1:]
with open(os.environ["DOCKER_TRACE"], "a", encoding="utf-8") as trace:
    trace.write(json.dumps(args) + "\\n")
if args == ["version"]:
    print("Client:")
    print(" Version: 27.5.1")
    print(" Git commit: a1b2c3d4e5f60718")
    print("Server:")
    print(" Version: 28.0.4")
    print(" Git commit: 0f1e2d3c4b5a6978")
    print(" Cookie: DOCKER_VERSION_COOKIE_SENTINEL")
elif args == ["info"]:
    for index in range(1, 46):
        if index == 2:
            print(f"info-line-{index} ID: 0123456789abcdef0123456789abcdef")
        elif index == 3:
            print(f"info-line-{index} Cookie: DOCKER_INFO_COOKIE_SENTINEL")
        elif index == 4:
            print(f"info-line-{index} HTTP Proxy: http://docker-user:DOCKER_PROXY_PASSWORD_SENTINEL@proxy.example:8080")
        else:
            print(f"info-line-{index}")
elif args[:2] == ["volume", "inspect"]:
    if args == ["volume", "inspect", "--format", "{{.Name}} {{.Driver}}", "z3-regtest-zallet"]:
        print("z3-regtest-zallet local")
    else:
        print("raw-volume-metadata DOCKER_VOLUME_COOKIE_SENTINEL abcdef1234567890")
elif args[-3:] == ["port", "zallet", "28232"]:
    print("0.0.0.0:55032")
elif args[-1:] == ["ps"]:
    print("NAME SERVICE STATUS PORTS")
    print("z3-regtest-zallet-1 zallet Up 0.0.0.0:55032->28232/tcp")
elif args[-4:] == ["zallet", "ls", "-la", "/var/lib/zallet"]:
    print("total 0")
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
    fake_uname = fake_bin / "uname"
    fake_uname.write_text(
        """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["HOST_TRACE"], "a", encoding="utf-8") as trace:
    trace.write(json.dumps({"command": "uname", "args": sys.argv[1:]}) + "\\n")
print("Linux zcanary-test 6.8.0 #1 SMP x86_64 GNU/Linux")
""",
        encoding="utf-8",
    )
    fake_uname.chmod(0o755)
    fake_df = fake_bin / "df"
    fake_df.write_text(
        """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["HOST_TRACE"], "a", encoding="utf-8") as trace:
    trace.write(json.dumps({"command": "df", "args": sys.argv[1:]}) + "\\n")
print("Filesystem Size Used Avail Use% Mounted on")
print("/dev/sda1 20G 8G 12G 40% /var/lib/docker")
""",
        encoding="utf-8",
    )
    fake_df.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "CURL_TRACE": str(trace),
            "DOCKER_TRACE": str(docker_trace),
            "HOST_TRACE": str(host_trace),
            "ACCOUNT_CREATED_FILE": str(account_created),
            "FAKE_HELP_HANG": "1",
            "FULL_ADDRESS": full_address,
            "ZCANARY_DIAGNOSTICS_FILE": str(output),
            "ZCANARY_ZALLET_METHODS_FILE": str(methods_output),
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
    host_url = "http://127.0.0.1:55032"
    assert len(records) == 18
    assert [record["url"] for record in records] == [
        "http://127.0.0.1:29232",
        "http://127.0.0.1:8181/health",
        "http://127.0.0.1:8181",
        host_url,
        host_url,
        host_url,
        host_url,
        host_url,
        host_url,
        host_url,
        host_url,
        "http://127.0.0.1:8181",
        host_url,
        "http://127.0.0.1:8181",
        host_url,
        host_url,
        host_url,
        host_url,
    ]
    assert [record["method"] for record in records] == [
        "getblockchaininfo", "GET", "getblockchaininfo", "getwalletinfo",
        "z_listaccounts", "z_getnewaccount", "z_listaccounts", "listaddresses",
        "getwalletstatus", "help", "rpc.discover", "z_listaccounts",
        "z_listaccounts", "z_listaccounts", "getinfo", "getinfo",
        "getwalletinfo", "getwalletinfo",
    ]
    assert [record["auth"] for record in records] == [
        True, False, True, True, True, True, True, True, True, True,
        True, True, True, False, False, True, True, True,
    ]
    assert records[4]["params"] == []
    assert records[5]["params"] == ["diag"]
    assert records[6]["params"] == []
    assert [record["max_time"] for record in records] == [
        "10", "10", "10", "10", "10", "60", "60", "30", "30", "30",
        "30", "10", "10", "10", "10", "10", "10", "10",
    ]
    for record in records:
        assert record["connect_timeout"] == "5"
        assert record["max_time"] in {"10", "30", "60"}
        assert record["write_out"] == "%{http_code} %{time_total}"
        assert record["disable"] and record["silent_show_error"]
    assert all(record["config_mode"] == "0o600" for record in records if record["auth"])
    assert all(record["config_mode"] is None for record in records if not record["auth"])
    assert not records[13]["authorization_header"]

    rendered = proc.stdout + proc.stderr + output.read_text(encoding="utf-8")
    for secret in (
        node_password, wallet_password, router_password, full_address,
        "zcanary-diagnostic-invalid-password", "a1b2c3d4e5f60718",
        "0f1e2d3c4b5a6978", "0123456789abcdef0123456789abcdef",
        "DOCKER_VERSION_COOKIE_SENTINEL", "DOCKER_INFO_COOKIE_SENTINEL",
        "DOCKER_VOLUME_COOKIE_SENTINEL", "DOCKER_PROXY_PASSWORD_SENTINEL", "abcdef1234567890",
    ):
        assert secret not in rendered
    method_names = methods_output.read_text(encoding="utf-8").splitlines()
    assert method_names == ["getwalletinfo", "listaddresses", "z_getnewaccount", "z_listaccounts"]
    assert all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]*", method) for method in method_names)
    assert "Zallet methods: count=4; z_listaccounts=yes; z_getnewaccount=yes; listaddresses=yes" in rendered
    assert "Zallet direct getwalletinfo (10-second timeout): HTTP status=200; curl exit=0" in rendered
    assert re.search(r"empty wallet; 10-second timeout.*HTTP status=000; curl exit=28; seconds=10\.000", rendered)
    assert re.search(r"diag account; 60-second timeout.*HTTP status=200; curl exit=0", rendered)
    assert re.search(r"after account creation; 60-second timeout.*HTTP status=200; curl exit=0", rendered)
    assert re.search(r"listaddresses \(30-second timeout\).*HTTP status=200; curl exit=0", rendered)
    assert re.search(r"getwalletstatus \(30-second timeout\).*HTTP status=200; curl exit=0", rendered)
    assert re.search(r"help \(30-second timeout\).*HTTP status=000; curl exit=28; seconds=30\.000", rendered)
    assert "method-name rpc.discover: HTTP status=200; curl exit=0; seconds=0.012" in rendered
    ordered_probe_prefixes = (
        "d. Zallet direct getwalletinfo",
        "e. Zallet direct z_listaccounts (empty wallet",
        "f. Zallet direct z_getnewaccount",
        "g. Zallet direct z_listaccounts (after account creation",
        "h. Zallet direct listaddresses",
        "i. Zallet direct getwalletstatus",
        "j. Zallet direct help",
    )
    ordered_previews = []
    for prefix in ordered_probe_prefixes:
        line = next(line for line in rendered.splitlines() if line.startswith(prefix))
        preview_match = re.search(r'body="(.*)"', line)
        assert preview_match is not None
        ordered_previews.append(preview_match.group(1))
    assert all(len(preview) <= 400 for preview in ordered_previews)
    assert len(ordered_previews[4]) == 400
    assert "BODY-BEGIN" in ordered_previews[4] and "<redacted-hash>" in ordered_previews[4]
    assert "<redacted>" in ordered_previews[4]
    assert "total 0" in rendered
    assert "Zallet password sanity: length=9; contains_whitespace=yes; contains_non_hex=yes" in rendered
    assert "docker compose --env-file .env.regtest ps" in rendered
    assert "docker version" in rendered
    assert "docker info (first 40 lines)" in rendered
    assert "info-line-40" in rendered and "info-line-41" not in rendered
    assert "uname -a" in rendered and "Linux zcanary-test" in rendered
    assert "df -h /var/lib/docker" in rendered and "/var/lib/docker" in rendered
    assert "z3-regtest-zallet local" in rendered
    assert "raw-volume-metadata" not in rendered
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
    docker_calls = [json.loads(line) for line in docker_trace.read_text(encoding="utf-8").splitlines()]
    assert docker_calls.count(["version"]) == 1
    assert docker_calls.count(["info"]) == 1
    assert docker_calls.count(["volume", "inspect", "--format", "{{.Name}} {{.Driver}}", "z3-regtest-zallet"]) == 1
    assert docker_calls.count(["compose", "--env-file", ".env.regtest", "exec", "-T", "zallet", "ls", "-la", "/var/lib/zallet"]) == 1
    host_calls = [json.loads(line) for line in host_trace.read_text(encoding="utf-8").splitlines()]
    assert host_calls == [
        {"command": "uname", "args": ["-a"]},
        {"command": "df", "args": ["-h", "/var/lib/docker"]},
    ]


def test_method_name_parser_falls_back_to_safe_python_when_jq_fails(tmp_path):
    script_path = ROOT / "scripts" / "diagnose-wallet-path.sh"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_jq = fake_bin / "jq"
    fake_jq.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake_jq.chmod(0o755)
    response = tmp_path / "rpc-discover.json"
    response.write_text(
        json.dumps(
            {
                "result": {
                    "methods": [
                        {"name": "z_listaccounts"},
                        {"name": "listaddresses"},
                        {"name": "bad-name;"},
                        {"method": "z_getnewaccount"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "ZCANARY_DIAGNOSE_LIBRARY_ONLY": "1",
            "ZCANARY_DIAGNOSTICS_FILE": str(tmp_path / "library.log"),
            "ZCANARY_NODE_PASSWORD": "test-node-password",
            "ZCANARY_ZALLET_PASSWORD": "test-wallet-password",
        }
    )
    proc = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; extract_method_names "$2"',
            "bash",
            str(script_path),
            str(response),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["z_listaccounts", "listaddresses", "z_getnewaccount"]
    assert "bad-name" not in proc.stdout


def test_spike_runs_path_probes_at_90_seconds_and_again_on_timeout():
    script = (ROOT / "scripts" / "spike.sh").read_text(encoding="utf-8")
    assert 'WAIT_ELAPSED" -ge 90' in script
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
