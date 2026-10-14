"""C6: search explicitly captured output sources for this run's markers."""
from __future__ import annotations

from collections.abc import Mapping

from ..canaries import marker_variants
from . import FAIL, NOT_RUN, PASS, CheckResult


def check(
    sources: Mapping[str, str],
    canaries: Mapping[str, str],
    *,
    additional_sensitive: Mapping[str, str] | None = None,
    capture_ok: bool = True,
    log_capture_configured: bool = True,
) -> CheckResult:
    """Scan captured text and report only labels, counts, and source names.

    Marker values and matching lines are deliberately excluded. A found marker is a
    failure even if another source could not be captured; otherwise incomplete capture
    or absent log configuration is reported as NOT_RUN rather than as a false pass.
    """
    markers = dict(canaries)
    markers.update(additional_sensitive or {})
    findings: dict[str, dict[str, object]] = {}
    total = 0
    for label, marker in markers.items():
        count = 0
        matched_sources: list[str] = []
        variants = marker_variants(marker)
        if variants:
            for source, text in sources.items():
                matches = sum(text.count(variant) for variant in variants)
                if matches:
                    count += matches
                    matched_sources.append(source)
        findings[label] = {"count": count, "sources": sorted(matched_sources)}
        total += count

    leaked_labels = [label for label, item in findings.items() if item["count"]]
    evidence = {
        "scanned_sources": sorted(sources),
        "findings_by_label": findings,
        "total_matches": total,
    }
    if leaked_labels:
        labels = ", ".join(sorted(leaked_labels))
        return CheckResult(
            "C6",
            FAIL,
            "Canary or configured sensitive marker(s) were found in captured output: " + labels + ".",
            evidence,
            "Remove the leaking log/output path and rerun; inspect the source locally without sharing marker values.",
        )
    if not log_capture_configured:
        return CheckResult(
            "C6",
            NOT_RUN,
            "No app-log capture or artifact source is configured; canary scanning was not run.",
            evidence,
            "Configure app.logs or report.scan_files to capture the app's relevant outputs, then rerun.",
        )
    if not capture_ok:
        return CheckResult(
            "C6",
            NOT_RUN,
            "At least one configured log or artifact source could not be captured.",
            evidence,
            "Make every configured log/artifact source readable and rerun; missing logs are not evidence of no leak.",
        )
    return CheckResult(
        "C6",
        PASS,
        f"No canary markers found in {len(sources)} captured source(s).",
        evidence,
        "",
    )
