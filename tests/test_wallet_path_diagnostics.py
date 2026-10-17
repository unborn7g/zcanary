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


def test_diagnostic_script_emits_early_probes_and_safe_process_snapshots():
    script_path = ROOT / "scripts" / "diagnose-wallet-path.sh"
    assert script_path.is_file()
    script = script_path.read_text(encoding="utf-8")

    assert "wallet path probes" in script
    assert "--connect-timeout 5" in script
    assert '--max-time "$max_time"' in script
    assert 'max_time="${8:-10}"' in script
    assert 'preview_limit="${10:-160}"' in script
    assert "5|10|30|45|60" in script
    assert "400" in script
    assert "-sS" in script and "-o" in script
    assert "-w '%{http_code} %{time_total}'" in script
    assert "early z_getnewaccount (attempt ${attempt}/6)" in script
    assert "POST z_getnewaccount basic" in script
    assert '"$WALLET_USER" "$ZCANARY_ZALLET_PASSWORD" 5 ' in script
    assert "'[\"early\"]'" in script
    assert "for attempt in 1 2 3 4 5 6" in script
    assert "sleep 1" in script
    assert 'sleep 8' in script
    assert "45" in script
    assert 'wait "$inflight_probe_pid"' in script
    assert 'capture_zallet_process_snapshot "$zallet_container_name" "idle"' in script
    assert 'capture_zallet_process_snapshot "$zallet_container_name" "in-flight z_getnewaccount"' in script
    port_mapping_pos = script.index("docker compose --env-file .env.regtest port zallet 28232")
    early_call_pos = script.index('  run_early_z_getnewaccount_probes "$host_port"')
    assert port_mapping_pos < early_call_pos < script.index('  run_probe "a. Zebra direct"')
    start_background_pos = script.index('  run_probe "f. Zallet direct z_getnewaccount (diag; in-flight, 45-second timeout)"')
    background_pid_pos = script.index("  inflight_probe_pid=$!", start_background_pos)
    sleep_pos = script.index("  sleep 8", background_pid_pos)
    snapshot_pos = script.index('  capture_zallet_process_snapshot "$zallet_container_name" "in-flight z_getnewaccount"', sleep_pos)
    wait_pos = script.index('  wait "$inflight_probe_pid"', snapshot_pos)
    assert start_background_pos < background_pid_pos < sleep_pos < snapshot_pos < wait_pos

    ordered_labels = (
        'run_probe "d. Zallet direct getwalletinfo (10-second timeout)"',
        'run_probe "e. Zallet direct z_listaccounts (after early probes; 10-second timeout)"',
        'run_probe "f. Zallet direct z_getnewaccount (diag; in-flight, 45-second timeout)"',
        'run_probe "g. Zallet direct z_listaccounts (after account creation; 60-second timeout)"',
        'run_probe "h. Zallet direct listaddresses (30-second timeout)"',
        'run_probe "i. Zallet direct getwalletstatus (30-second timeout)"',
        'run_probe "j. Zallet direct help (30-second timeout)"',
    )
    assert all(label in script for label in ordered_labels)
    positions = [script.index(label) for label in ordered_labels]
    assert positions == sorted(positions)

    for method in (
        "getblockchaininfo", "z_listaccounts", "getinfo", "getwalletinfo", "help",
        "rpc.discover", "listaddresses", "getwalletstatus", "z_getnewaccount",
    ):
        assert method in script
    assert "capture_zallet_method_names" in script
    assert "command -v jq" in script and "python3 -" in script
    assert "$ROOT/out/diagnostics/zallet-methods.txt" in script
    assert "Zallet methods: count=%s; z_listaccounts=%s; z_getnewaccount=%s; listaddresses=%s" in script
    assert "nproc" in script
    assert "grep -c '^processor' /proc/cpuinfo" in script
    assert "docker info --format '{{.NCPU}} {{.MemTotal}}'" in script
    assert "docker info (first 40 lines)" in script
    assert "docker volume inspect --format '{{.Name}} {{.Driver}}' z3-regtest-zallet" in script
    assert "ps -q zallet" in script
    assert "docker inspect --format '{{.Name}}'" in script
    assert "--pid=container:$container_name" in script
    assert "--cap-add SYS_PTRACE" in script
    assert "grep -E '^(Threads|State):' /proc/1/status" in script
    assert "/proc/1/task/*" in script and "/proc/1/fd" in script
    assert "ls -l /proc/1/fd" in script
    assert "wchan" in script and "comm" in script
    assert "cat /proc/1/fd" not in script
    assert "cat /var/lib/zallet" not in script
    assert "docker compose --env-file .env.regtest exec -T zallet ls -la /var/lib/zallet" not in script
    busybox = re.search(r"(?m)^BUSYBOX_REF='([^']+)'$", (ROOT / "scripts" / "spike.sh").read_text())
    assert busybox is not None and busybox.group(1) in script
    assert "redact_sensitive_diagnostic" in script


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
    docker_trace = tmp_path / "docker-trace.jsonl"
    compose_trace = tmp_path / "compose-trace.jsonl"
    host_trace = tmp_path / "host-trace.jsonl"
    snapshot_trace = tmp_path / "snapshot-trace.jsonl"
    sleep_trace = tmp_path / "sleep-trace.jsonl"
    inflight_started = tmp_path / "inflight-started"
    inflight_done = tmp_path / "inflight-done"
    fake_container_id = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    full_address = "uregtest1abcdefghijklmnopqrstuvwxyz0123456789"
    node_password = "node-probe-password-not-a-secret"
    wallet_password = "Q7z! t9L#"
    router_password = "router-probe-password-not-a-secret"

    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env python3
import json, os, re, stat, sys, time
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
if method == "z_getnewaccount" and params == ["early"]:
    print("000 5.000", end="")
    sys.exit(28)
if method == "z_getnewaccount" and params == ["diag"]:
    Path(os.environ["INFLIGHT_STARTED_FILE"]).write_text("started", encoding="utf-8")
    time.sleep(0.5)
    Path(os.environ["INFLIGHT_DONE_FILE"]).write_text("done", encoding="utf-8")
    print("000 30.000", end="")
    sys.exit(52)
if url == "http://127.0.0.1:55032" and method == "z_listaccounts":
    counter_path = Path(os.environ["ZLIST_COUNTER_FILE"])
    count = int(counter_path.read_text()) if counter_path.exists() else 0
    counter_path.write_text(str(count + 1), encoding="utf-8")
    if count == 0:
        print("000 10.000", end="")
        sys.exit(28)
    if count == 1:
        print("000 30.000", end="")
        sys.exit(52)
if url == "http://127.0.0.1:8181" and method == "z_listaccounts" and record["auth"]:
    print("000 10.000", end="")
    sys.exit(28)
if url == "http://127.0.0.1:55032" and method == "getinfo" and not record["auth"]:
    print("401 0.012", end="")
    sys.exit(0)
if url == "http://127.0.0.1:55032" and record["auth"] and credentials not in valid_credentials:
    print("401 0.010", end="")
    sys.exit(0)
if url == "http://127.0.0.1:55032" and method in {"listaddresses", "getwalletstatus"}:
    print("000 30.000", end="")
    sys.exit(28)
print("200 0.001" if method in {"help", "rpc.discover", "getwalletinfo"} else "200 0.012", end="")
sys.exit(0)
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["DOCKER_TRACE"], "a", encoding="utf-8") as trace:
    trace.write(json.dumps(args) + "\\n")
with open(os.environ["COMPOSE_TRACE"], "a", encoding="utf-8") as trace:
    trace.write(json.dumps(os.environ.get("COMPOSE_FILE")) + "\\n")
if args == ["version"]:
    print("Client:")
    print(" Version: 27.5.1")
    print(" Git commit: a1b2c3d4e5f60718")
    print("Server:")
    print(" Version: 28.0.4")
    print(" Git commit: 0f1e2d3c4b5a6978")
    print(" Cookie: DOCKER_VERSION_COOKIE_SENTINEL")
elif args == ["info", "--format", "{{.NCPU}} {{.MemTotal}}"]:
    print("8 8589934592")
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
elif args[-3:] == ["ps", "-q", "zallet"]:
    print(os.environ["FAKE_CONTAINER_ID"])
elif args == ["inspect", "--format", "{{.Name}}", os.environ["FAKE_CONTAINER_ID"]]:
    print("/z3-regtest-zallet-1")
elif args[:1] == ["run"]:
    phase = os.environ.get("ZCANARY_PROCESS_SNAPSHOT_PHASE", "missing")
    started = Path(os.environ["INFLIGHT_STARTED_FILE"]).exists()
    done = Path(os.environ["INFLIGHT_DONE_FILE"]).exists()
    with open(os.environ["SNAPSHOT_TRACE"], "a", encoding="utf-8") as trace:
        trace.write(json.dumps({"phase": phase, "inflight_active": started and not done}) + "\\n")
    print("State: S (sleeping)")
    print("Threads: 5")
    print("task=1 comm=zallet state=S wchan=futex_wait_queue")
    print("task=2 comm=tokio-runtime-w state=S wchan=ep_poll")
    print("lrwxrwxrwx 1 root root 64 /proc/1/fd/3 -> /dev/null")
elif args[-3:] == ["port", "zallet", "28232"]:
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
    fake_nproc = fake_bin / "nproc"
    fake_nproc.write_text(
        """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["HOST_TRACE"], "a", encoding="utf-8") as trace:
    trace.write(json.dumps({"command": "nproc", "args": sys.argv[1:]}) + "\\n")
print("4")
""",
        encoding="utf-8",
    )
    fake_nproc.chmod(0o755)
    fake_sleep = fake_bin / "sleep"
    fake_sleep.write_text(
        """#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["SLEEP_TRACE"], "a", encoding="utf-8") as trace:
    trace.write(json.dumps(args) + "\\n")
if args == ["8"]:
    deadline = time.monotonic() + 3
    while not Path(os.environ["INFLIGHT_STARTED_FILE"]).exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not Path(os.environ["INFLIGHT_STARTED_FILE"]).exists():
        sys.exit(2)
    time.sleep(0.05)
elif args != ["1"]:
    sys.exit(2)
""",
        encoding="utf-8",
    )
    fake_sleep.chmod(0o755)
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
            "COMPOSE_TRACE": str(compose_trace),
            "HOST_TRACE": str(host_trace),
            "SNAPSHOT_TRACE": str(snapshot_trace),
            "SLEEP_TRACE": str(sleep_trace),
            "INFLIGHT_STARTED_FILE": str(inflight_started),
            "INFLIGHT_DONE_FILE": str(inflight_done),
            "FAKE_CONTAINER_ID": fake_container_id,
            "ZLIST_COUNTER_FILE": str(tmp_path / "zlist-counter"),
            "ZCANARY_ZALLET_TOKIO_WORKER_THREADS": "8",
            "ZCANARY_ROUTER_LOG_INFO": "1",
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
    assert len(records) == 24
    old_methods = [
        "getblockchaininfo", "GET", "getblockchaininfo", "getwalletinfo",
        "z_listaccounts", "z_getnewaccount", "z_listaccounts", "listaddresses",
        "getwalletstatus", "help", "rpc.discover", "z_listaccounts",
        "z_listaccounts", "z_listaccounts", "getinfo", "getinfo",
        "getwalletinfo", "getwalletinfo",
    ]
    assert [record["method"] for record in records] == ["z_getnewaccount"] * 6 + old_methods
    assert [record["url"] for record in records] == [host_url] * 6 + [
        "http://127.0.0.1:29232",
        "http://127.0.0.1:8181/health",
        "http://127.0.0.1:8181",
        host_url, host_url, host_url, host_url, host_url, host_url, host_url,
        host_url, "http://127.0.0.1:8181", host_url, "http://127.0.0.1:8181",
        host_url, host_url, host_url, host_url,
    ]
    assert [record["auth"] for record in records] == [True] * 6 + [
        True, False, True, True, True, True, True, True, True, True,
        True, True, True, False, False, True, True, True,
    ]
    assert all(record["params"] == ["early"] for record in records[:6])
    assert records[10]["params"] == []
    assert records[11]["params"] == ["diag"]
    assert records[12]["params"] == []
    assert [record["max_time"] for record in records] == ["5"] * 6 + [
        "10", "10", "10", "10", "10", "45", "60", "30", "30", "30",
        "30", "10", "10", "10", "10", "10", "10", "10",
    ]
    for record in records:
        assert record["connect_timeout"] == "5"
        assert record["max_time"] in {"5", "10", "30", "45", "60"}
        assert record["write_out"] == "%{http_code} %{time_total}"
        assert record["disable"] and record["silent_show_error"]
    assert all(record["config_mode"] == "0o600" for record in records if record["auth"])
    assert all(record["config_mode"] is None for record in records if not record["auth"])
    assert not records[19]["authorization_header"]

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
    for attempt in range(1, 7):
        assert re.search(
            rf"early z_getnewaccount \(attempt {attempt}/6\): HTTP status=000; curl exit=28; seconds=5\.000",
            rendered,
        )
    early_probe_lines = {
        line for line in rendered.splitlines()
        if line.startswith("early z_getnewaccount (attempt ")
    }
    assert len(early_probe_lines) == 6
    assert "Zallet direct getwalletinfo (10-second timeout): HTTP status=200; curl exit=0" in rendered
    assert re.search(r"after early probes; 10-second timeout.*HTTP status=000; curl exit=28; seconds=10\.000", rendered)
    assert re.search(r"diag; in-flight, 45-second timeout.*HTTP status=000; curl exit=52; seconds=30\.000", rendered)
    assert re.search(r"after account creation; 60-second timeout.*HTTP status=000; curl exit=52; seconds=30\.000", rendered)
    assert re.search(r"listaddresses \(30-second timeout\).*HTTP status=000; curl exit=28; seconds=30\.000", rendered)
    assert re.search(r"getwalletstatus \(30-second timeout\).*HTTP status=000; curl exit=28; seconds=30\.000", rendered)
    assert re.search(r"help \(30-second timeout\).*HTTP status=200; curl exit=0; seconds=0\.001", rendered)
    assert "method-name rpc.discover: HTTP status=200; curl exit=0; seconds=0.001" in rendered
    assert re.search(r"==> nproc \(exit 0\);.*\n4\n", rendered)
    nproc_labels = {line for line in rendered.splitlines() if line.startswith("==> nproc ")}
    assert len(nproc_labels) == 1
    assert "grep -c '^processor' /proc/cpuinfo" in rendered
    assert re.search(r"==> grep -c '\^processor' /proc/cpuinfo \(exit 0\);.*\n[0-9]+\n", rendered)
    assert "docker info --format '{{.NCPU}} {{.MemTotal}}' (exit 0)" in rendered
    assert re.search(r"docker info --format.*\nNCPU=8; MemTotal=8,589,934,592 bytes\n", rendered)
    assert "Zallet process snapshot (idle)" in rendered
    assert "Zallet process snapshot (in-flight z_getnewaccount)" in rendered
    assert "Threads: 5" in rendered and "State: S (sleeping)" in rendered
    assert "task=1 comm=zallet state=S wchan=futex_wait_queue" in rendered
    assert "task=2 comm=tokio-runtime-w state=S wchan=ep_poll" in rendered
    assert "-> /dev/null" in rendered
    snapshot_records = [json.loads(line) for line in snapshot_trace.read_text(encoding="utf-8").splitlines()]
    assert snapshot_records == [
        {"phase": "idle", "inflight_active": False},
        {"phase": "in-flight z_getnewaccount", "inflight_active": True},
    ]
    sleep_calls = [json.loads(line) for line in sleep_trace.read_text(encoding="utf-8").splitlines()]
    assert sleep_calls.count(["1"]) == 5
    assert sleep_calls.count(["8"]) == 1
    assert "docker compose --env-file .env.regtest exec" not in rendered
    ordered_probe_prefixes = (
        "d. Zallet direct getwalletinfo",
        "e. Zallet direct z_listaccounts (after early probes",
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
    override = compose_dir / "docker-compose.regtest.override.yml"
    assert override.read_text(encoding="utf-8") == (
        "services:\n"
        "  rpc-router:\n"
        "    environment:\n"
        "      RUST_LOG: info\n"
        "  zallet:\n"
        "    environment:\n"
        "      TOKIO_WORKER_THREADS: \"8\"\n"
    )
    docker_calls = [json.loads(line) for line in docker_trace.read_text(encoding="utf-8").splitlines()]
    assert docker_calls.count(["version"]) == 1
    assert docker_calls.count(["info"]) == 1
    assert docker_calls.count(["info", "--format", "{{.NCPU}} {{.MemTotal}}"]) == 1
    assert docker_calls.count(["volume", "inspect", "--format", "{{.Name}} {{.Driver}}", "z3-regtest-zallet"]) == 1
    assert docker_calls.count(["compose", "--env-file", ".env.regtest", "ps", "-q", "zallet"]) == 1
    assert docker_calls.count(["inspect", "--format", "{{.Name}}", fake_container_id]) == 1
    helper_calls = [call for call in docker_calls if call[:1] == ["run"]]
    assert len(helper_calls) == 2
    assert all("--cap-add" in call and "SYS_PTRACE" in call for call in helper_calls)
    assert all(any(arg.startswith("--pid=container:") for arg in call) for call in helper_calls)
    assert not any("exec" in call for call in docker_calls)
    compose_files = [json.loads(line) for line in compose_trace.read_text(encoding="utf-8").splitlines()]
    expected_compose_file = "docker-compose.yml:docker-compose.regtest.yml:docker-compose.regtest.override.yml"
    assert compose_files == [expected_compose_file] * len(docker_calls)
    host_calls = [json.loads(line) for line in host_trace.read_text(encoding="utf-8").splitlines()]
    assert host_calls == [
        {"command": "nproc", "args": []},
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


def test_diagnose_run_generates_untracked_router_and_zallet_thread_overrides():
    spike = (ROOT / "scripts" / "spike.sh").read_text(encoding="utf-8")
    diagnostic = (ROOT / "scripts" / "diagnose-wallet-path.sh").read_text(encoding="utf-8")
    for script in (spike, diagnostic):
        assert "docker-compose.regtest.override.yml" in script
        assert "RUST_LOG: info" in script
        assert 'TOKIO_WORKER_THREADS: "%s"' in script
        assert "ZCANARY_ZALLET_TOKIO_WORKER_THREADS" in script
        assert "COMPOSE_FILE=docker-compose.yml:docker-compose.regtest.yml:docker-compose.regtest.override.yml" in script
    assert spike.index('TOKIO_WORKER_THREADS: "%s"') < spike.index('DC="docker compose')
    assert diagnostic.index("export COMPOSE_FILE=") < diagnostic.index("run_wallet_path_probes()")


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
    assert workflow.count("zallet_name:") == 3
    assert "zallet_name: beta1" in workflow
    assert "zallet_name: beta1-threads8" in workflow
    assert "zallet_name: beta3-threads8" in workflow
    assert "artifact_name: zcanary-wallet-path-diagnostics-beta1" in workflow
    assert "artifact_name: zcanary-wallet-path-diagnostics-beta1-threads8" in workflow
    assert "artifact_name: zcanary-wallet-path-diagnostics-beta3-threads8" in workflow
    assert workflow.count("artifact_name:") == 3
    assert "zallet_name: beta3\n" not in workflow
    assert "artifact_name: zcanary-wallet-path-diagnostics-beta3\n" not in workflow
    assert workflow.count('tokio_worker_threads: ""') == 1
    assert workflow.count('tokio_worker_threads: "8"') == 2
    assert "ZCANARY_ZALLET_TOKIO_WORKER_THREADS: ${{ matrix.tokio_worker_threads }}" in workflow
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
