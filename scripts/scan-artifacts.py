#!/usr/bin/env python3
"""Fail closed if generated report artifacts contain a runtime canary or RPC password."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

MARKER = re.compile(r"ZC-(M|L|T)-[A-F0-9]{24}")
_MARKER_HEX = {
    label: re.compile(
        prefix.encode("ascii").hex() + r"(?:3[0-9]|4[1-6]){24}", re.IGNORECASE
    )
    for label, prefix in (("memo", "ZC-M-"), ("label", "ZC-L-"), ("test_id", "ZC-T-"))
}


def scan(paths: list[Path]) -> dict[str, dict[str, int]]:
    secrets = {
        label: value
        for label, value in (
            ("credential:node", os.environ.get("ZCANARY_ZEBRA_PASSWORD", "")),
            ("credential:zallet", os.environ.get("ZCANARY_ZALLET_PASSWORD", "")),
        )
        if len(value) >= 8
    }
    findings: dict[str, dict[str, int]] = {}
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            findings.setdefault("artifact:unreadable", {})[path.name] = 1
            continue
        counts = {
            "memo": len(re.findall(r"ZC-M-[A-F0-9]{24}", text))
            + len(_MARKER_HEX["memo"].findall(text)),
            "label": len(re.findall(r"ZC-L-[A-F0-9]{24}", text))
            + len(_MARKER_HEX["label"].findall(text)),
            "test_id": len(re.findall(r"ZC-T-[A-F0-9]{24}", text))
            + len(_MARKER_HEX["test_id"].findall(text)),
        }
        for label, value in secrets.items():
            counts[label] = text.count(value) + text.count(value.encode("utf-8").hex())
        for label, count in counts.items():
            if count:
                findings.setdefault(label, {})[path.name] = count
    return findings


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    roots = [Path(arg) for arg in args] if args else [Path("out/reports")]
    paths: set[Path] = set()
    for root in roots:
        if root.is_dir():
            paths.update(root.glob("*.json"))
            paths.update(root.glob("*.md"))
        elif root.is_file():
            paths.add(root)
    ordered_paths = sorted(paths)
    if not ordered_paths:
        print(f"artifact scan failed: no report artifacts found under {', '.join(map(str, roots))}", file=sys.stderr)
        return 2
    findings = scan(ordered_paths)
    if findings:
        for label, sources in sorted(findings.items()):
            for source, count in sorted(sources.items()):
                print(f"artifact scan found {label}: {count} match(es) in {source}", file=sys.stderr)
        return 1
    print(f"artifact scan passed: {len(paths)} report artifact(s); no canary/password markers found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
