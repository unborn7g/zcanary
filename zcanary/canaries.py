"""Ephemeral canary tokens used for one disposable regtest run.

The token values are sensitive test markers: keep them in process memory, pass them only
through child-process environment variables, and never include them in reports or logs.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass


def marker_variants(value: str) -> tuple[str, ...]:
    """Representations commonly exposed by JSON-RPC logging: raw UTF-8 and hex."""
    if not value:
        return ()
    encoded = value.encode("utf-8").hex()
    return tuple(dict.fromkeys((value, encoded, encoded.upper())))


@dataclass(frozen=True)
class CanarySet:
    memo: str
    label: str
    test_id: str

    @classmethod
    def generate(cls) -> "CanarySet":
        return cls(
            memo=f"ZC-M-{secrets.token_hex(12).upper()}",
            label=f"ZC-L-{secrets.token_hex(12).upper()}",
            test_id=f"ZC-T-{secrets.token_hex(12).upper()}",
        )

    def by_label(self) -> dict[str, str]:
        return {"memo": self.memo, "label": self.label, "test_id": self.test_id}

    def as_env(self) -> dict[str, str]:
        return {
            "ZCANARY_MEMO": self.memo,
            "ZCANARY_LABEL": self.label,
            "ZCANARY_TEST_ID": self.test_id,
        }

    def values(self) -> tuple[str, ...]:
        return (self.memo, self.label, self.test_id)
