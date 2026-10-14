import json

from zcanary.canaries import CanarySet
from zcanary.checks import FAIL, NOT_RUN, PASS, c6


def test_generated_canaries_are_distinct_and_exported_only_to_env():
    first = CanarySet.generate()
    second = CanarySet.generate()
    assert len(set(first.values())) == 3
    assert set(first.values()).isdisjoint(second.values())
    assert set(first.as_env()) == {"ZCANARY_MEMO", "ZCANARY_LABEL", "ZCANARY_TEST_ID"}
    assert set(first.by_label()) == {"memo", "label", "test_id"}


def test_c6_pass_reports_sources_and_counts_not_values():
    canaries = {"memo": "ZC-M-test", "label": "ZC-L-test", "test_id": "ZC-T-test"}
    result = c6({"pay.stderr": "no markers", "app.log": "ordinary output"}, canaries)
    assert result.status == PASS
    assert result.evidence["findings_by_label"]["memo"] == {"count": 0, "sources": []}
    assert "ZC-M-test" not in json.dumps(result.to_dict())


def test_c6_fails_and_only_reports_label_count_and_source():
    canaries = {"memo": "ZC-M-test", "label": "ZC-L-test", "test_id": "ZC-T-test"}
    result = c6({"app.log": "memo=ZC-M-test ZC-M-test", "stderr": "ZC-M-test"}, canaries)
    assert result.status == FAIL
    assert result.evidence["findings_by_label"]["memo"] == {"count": 3, "sources": ["app.log", "stderr"]}
    rendered = json.dumps(result.to_dict())
    assert "ZC-M-test" not in rendered and "ZC-L-test" not in rendered


def test_c6_finds_hex_encoded_canary_without_reporting_its_value():
    marker = "ZC-M-0123456789ABCDEF01234567"
    result = c6({"app.logs[1].stdout": marker.encode().hex()}, {"memo": marker})
    assert result.status == FAIL
    assert result.evidence["findings_by_label"]["memo"] == {
        "count": 1,
        "sources": ["app.logs[1].stdout"],
    }
    assert marker not in json.dumps(result.to_dict())


def test_c6_missing_or_failed_capture_is_not_run():
    canaries = {"memo": "ZC-M-test"}
    assert c6({}, canaries, log_capture_configured=False).status == NOT_RUN
    assert c6({"app.log": ""}, canaries, capture_ok=False).status == NOT_RUN
