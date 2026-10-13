"""valuePools helpers (getblock verbosity 1 / getblockchaininfo)."""
from __future__ import annotations


def block_deltas(block: dict) -> dict[str, int]:
    return {p["id"]: int(p.get("valueDeltaZat", 0)) for p in block.get("valuePools", [])}


def chain_values(info: dict) -> dict[str, int]:
    return {p["id"]: int(p.get("chainValueZat", 0)) for p in info.get("valuePools", [])}


def diff(before: dict, after: dict) -> dict[str, int]:
    a, b = chain_values(before), chain_values(after)
    return {k: b.get(k, 0) - a.get(k, 0) for k in sorted(set(a) | set(b))}


def nonzero(d: dict[str, int]) -> dict[str, int]:
    return {k: v for k, v in d.items() if v}
