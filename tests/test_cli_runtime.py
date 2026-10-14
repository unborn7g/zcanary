import os
from pathlib import Path

from zcanary.canaries import CanarySet
from zcanary.cli import NOT_COVERED, _contains_marker, _env_for_run, _markdown, _run_command
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
