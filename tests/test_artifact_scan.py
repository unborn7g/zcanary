import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("scan_artifacts", ROOT / "scripts" / "scan-artifacts.py")
scan_artifacts = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(scan_artifacts)


def test_artifact_scanner_reports_only_label_path_and_count(tmp_path):
    report = tmp_path / "report.json"
    report.write_text('{"message":"ZC-M-0123456789ABCDEF01234567"}', encoding="utf-8")
    result = scan_artifacts.scan([report])
    assert result == {"memo": {"report.json": 1}}
    assert "0123456789ABCDEF01234567" not in str(result)


def test_artifact_scanner_finds_hex_encoded_canary(tmp_path):
    report = tmp_path / "report.json"
    marker = "ZC-L-0123456789ABCDEF01234567"
    report.write_text(marker.encode().hex(), encoding="utf-8")
    result = scan_artifacts.scan([report])
    assert result == {"label": {"report.json": 1}}
    assert marker not in str(result)


def test_artifact_scanner_checks_configured_password_without_returning_value(tmp_path, monkeypatch):
    report = tmp_path / "report.md"
    report.write_text("safe report; embedded-value-should-not-appear", encoding="utf-8")
    monkeypatch.setenv("ZCANARY_ZEBRA_PASSWORD", "embedded-value-should-not-appear")
    result = scan_artifacts.scan([report])
    assert result == {"credential:node": {"report.md": 1}}
    assert "embedded-value" not in str(result)
