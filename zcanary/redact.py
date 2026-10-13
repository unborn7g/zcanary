"""Redaction helpers for evidence and logs."""
from __future__ import annotations

import re

_ADDR = re.compile(r"\b(u1|utest1|uregtest1|zs1|ztestsapling1|zregtestsapling1|t1|t3|tm|t2)[0-9a-z]{20,}\b", re.I)


def addr(a: str | None) -> str | None:
    if not a:
        return a
    return a if len(a) <= 20 else f"{a[:12]}…{a[-6:]}"


def text(s: str) -> str:
    return _ADDR.sub(lambda m: addr(m.group(0)), s)


def secret(s: str, *secrets: str) -> str:
    for x in secrets:
        if x:
            s = s.replace(x, "***")
    return s
