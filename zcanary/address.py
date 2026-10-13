"""Classify Zcash address strings by prefix (HRP / base58 lead bytes). No checksum validation."""
from __future__ import annotations

# (prefix, kind, network). Order matters: longer prefixes first.
_PREFIXES = [
    ("uregtest1", "unified", "regtest"), ("utest1", "unified", "testnet"), ("u1", "unified", "mainnet"),
    ("zregtestsapling1", "sapling", "regtest"), ("ztestsapling1", "sapling", "testnet"), ("zs1", "sapling", "mainnet"),
    ("texregtest1", "tex", "regtest"), ("textest1", "tex", "testnet"), ("tex1", "tex", "mainnet"),
    ("zc", "sprout", "mainnet"), ("zt", "sprout", "testnet"),
    ("t1", "transparent", "mainnet"), ("t3", "transparent", "mainnet"),
    # testnet and regtest share transparent prefixes
    ("tm", "transparent", "testnet|regtest"), ("t2", "transparent", "testnet|regtest"),
]


def classify(addr: str) -> tuple[str, str]:
    """Return (kind, network) or ("unknown", "unknown")."""
    a = addr.strip()
    for p, kind, net in _PREFIXES:
        if a.lower().startswith(p):
            return kind, net
    return "unknown", "unknown"


def network_matches(addr_net: str, want: str) -> bool:
    return want in addr_net.split("|")
