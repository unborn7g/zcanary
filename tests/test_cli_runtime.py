import os
import re
from pathlib import Path

from zcanary import cli as cli_module
from zcanary.canaries import CanarySet
from zcanary.cli import EXIT_ERROR, NOT_COVERED, _contains_marker, _env_for_run, _markdown, _run_command
from zcanary.config import parse


BASE = {
    "network": {
        "name": "regtest",
        "zebra": {"url": "http://127.0.0.1:29232", "user": "zebra", "password_env": "ZC_ZEBRA_PW"},
        "zallet": {"url": "http://127.0.0.1:8181", "user": "zebra", "password_env": "ZC_ZALLET_PW"},
    },
    "app": {"pay": "true", "result_file": "out/result.json", "amount": "0.5"},
}


def test_child_command_output_is_captured_not_displayed(tmp_path, capsys):
    step, sources = _run_command("app.logs[1]", "printf 'captured marker'", tmp_path, os.environ.copy(), 5)
    terminal = capsys.readouterr().out
    assert step["status"] == "ok"
    assert sources["app.logs[1].stdout"] == "captured marker"
    assert "captured marker" not in terminal
    assert "app.logs[1]: ok" in terminal


def test_network_start_streams_timestamped_progress_but_not_raw_child_output(tmp_path, capsys):
    child_marker = "private-network-start-output"
    command = (
        "printf '[2030-01-01T00:00:00Z] [ZCANARY-PROGRESS] phase=mining\\n'; "
        f"printf '{child_marker}\\n'"
    )
    step, sources = _run_command("network.start", command, tmp_path, os.environ.copy(), 5)
    terminal = capsys.readouterr().out

    assert step["status"] == "ok"
    assert re.search(r"\[\d{4}-\d{2}-\d{2}T[^]]+Z\] \[ZCANARY-PROGRESS\]", terminal)
    assert "phase=mining" in terminal
    assert child_marker not in terminal
    assert child_marker in sources["network.start.stdout"]


def test_run_environment_contains_canaries_and_uses_password_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ZC_ZEBRA_PW", "test-node-password")
    monkeypatch.setenv("ZC_ZALLET_PW", "test-wallet-password")
    cfg = parse(BASE, base_dir=tmp_path)
    canaries = CanarySet.generate()
    env = _env_for_run(cfg, canaries)
    assert env["ZCANARY_NODE_PASSWORD"] == "test-node-password"
    assert env["ZCANARY_ZALLET_PASSWORD"] == "test-wallet-password"
    assert env["ZCANARY_AMOUNT"] == "0.5"
    assert env["ZCANARY_MEMO"] == canaries.memo
    assert canaries.label not in str(cfg)


def test_cli_network_start_failure_creates_wallet_diagnostics_artifact(tmp_path, monkeypatch, capsys):
    config_dir = tmp_path / "examples" / "configs"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "failed-start.toml"
    config_path.write_text(
        '''[network]
name = "regtest"
timeout_s = 0.1
command_timeout_s = 5
start_command = "printf private-start-output; exit 7"

[network.zebra]
url = "http://127.0.0.1:9"

[network.zallet]
url = "http://127.0.0.1:9"

[app]
pay = "true"
result_file = "../../out/result.json"
amount = "0.5"

[report]
path = "../../out/reports/failed-start.json"
markdown_path = "../../out/reports/failed-start.md"
''',
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_module, "_try", lambda *_args: (None, "offline"))

    assert cli_module.run(str(config_path)) == EXIT_ERROR
    capsys.readouterr()

    diagnostics = tmp_path / "out" / "diagnostics" / "wallet-startup.log"
    assert diagnostics.is_file()
    contents = diagnostics.read_text(encoding="utf-8")
    assert "network.start failed" in contents
    assert "exit_code=7" in contents
    assert "private-start-output" not in contents
    assert "docker compose ps -a" in contents
    for service in ("zebra", "zallet", "rpc-router"):
        assert f"docker compose logs --no-color --tail 80 {service}" in contents


def test_report_markdown_contains_not_covered_and_selfscan_is_marker_aware():
    report = {
        "overall": "pass",
        "network": "regtest",
        "zcanary": "0.0.1",
        "seconds": 1.2,
        "checks": [{"id": "C6", "status": "pass", "message": "No markers found."}],
        "steps": {},
        "execution_errors": [],
        "not_covered": NOT_COVERED,
    }
    markdown = _markdown(report)
    assert "## NOT COVERED" in markdown
    assert "not a general leak-prevention guarantee" in markdown
    markers = {"memo": "ZC-M-ABCDEF0123456789ABCDEF01"}
    assert not _contains_marker(markdown, markers)
    assert _contains_marker(markdown + markers["memo"], markers)
    assert _contains_marker(markdown + markers["memo"].encode().hex(), markers)
