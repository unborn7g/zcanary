"""Every check returns a CheckResult; nothing is skipped silently (missing input -> not_run with reason)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

PASS, FAIL, NOT_RUN = "pass", "fail", "not_run"


@dataclass
class CheckResult:
    id: str
    status: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)
    fix: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def not_run(cid: str, why: str) -> CheckResult:
    return CheckResult(cid, NOT_RUN, f"not run: {why}", {}, "Provide the missing input and rerun.")


from .c1_network import check as c1  # noqa: E402
from .c2_receivers import check as c2  # noqa: E402
from .c3_legs import check as c3  # noqa: E402
from .c4_pool import check as c4, check_crossing as c4b  # noqa: E402
from .memo import check as memo  # noqa: E402
from .c6_canaries import check as c6  # noqa: E402

__all__ = ["CheckResult", "PASS", "FAIL", "NOT_RUN", "not_run", "c1", "c2", "c3", "c4", "c4b", "memo", "c6"]
