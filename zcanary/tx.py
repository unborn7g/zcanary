"""Classify a decoded transaction (getrawtransaction verbosity 1, Zebra 6.2.x shape)."""
from __future__ import annotations

from dataclasses import dataclass, field

SHIELDED_POOLS = ("sapling", "orchard", "ironwood")


@dataclass
class Bundle:
    pool: str
    spends: int = 0
    outputs: int = 0
    actions: int = 0
    value_balance_zat: int = 0

    @property
    def used(self) -> bool:
        return bool(self.spends or self.outputs or self.actions or self.value_balance_zat)


@dataclass
class TxSummary:
    txid: str
    version: int
    coinbase: bool
    transparent_in: int
    transparent_out: int
    sprout_joinsplits: int
    bundles: dict[str, Bundle] = field(default_factory=dict)
    unknown_keys: list[str] = field(default_factory=list)
    transparent_out_zat: int = 0
    transparent_in_zat: int | None = 0  # None: inputs present but their values are not in the verbose tx

    @property
    def pools_used(self) -> list[str]:
        return [p for p in SHIELDED_POOLS if self.bundles[p].used]

    def evidence(self) -> dict:
        return {
            "txid": self.txid, "version": self.version, "coinbase": self.coinbase,
            "transparent_in": self.transparent_in, "transparent_out": self.transparent_out,
            "sprout_joinsplits": self.sprout_joinsplits,
            "unknown_keys": self.unknown_keys,
            "bundles": {p: vars(b) for p, b in self.bundles.items()},
        }


_KNOWN = {"hex", "height", "confirmations", "in_active_chain", "vin", "vout", "vShieldedSpend", "vShieldedOutput",
          "vjoinsplit", "orchard", "ironwood", "valueBalance", "valueBalanceZat", "size", "time", "txid",
          "authdigest", "overwintered", "version", "versiongroupid", "locktime", "expiryheight", "blockhash",
          "blocktime", "bindingSig", "joinSplitPubKey", "joinSplitSig"}
_BUNDLE_FIELDS = {
    "orchard": {"actions", "valueBalance", "valueBalanceZat"},
    "ironwood": {"actions", "anchor", "bindingSig", "flags", "proof", "valueBalance", "valueBalanceZat"},
}
_FLAG_FIELDS = {"enableOutputs", "enableSpends"}


def _unknown_fields(tx: dict) -> list[str]:
    unknown = set(tx) - _KNOWN
    for pool, known_fields in _BUNDLE_FIELDS.items():
        if pool not in tx or tx[pool] is None:
            continue
        bundle = tx[pool]
        if not isinstance(bundle, dict):
            unknown.add(pool)
            continue
        unknown.update(f"{pool}.{key}" for key in set(bundle) - known_fields)
        flags = bundle.get("flags")
        if flags is not None:
            if not isinstance(flags, dict):
                unknown.add(f"{pool}.flags")
            else:
                unknown.update(f"{pool}.flags.{key}" for key in set(flags) - _FLAG_FIELDS)
    return sorted(unknown)


def _vin_value(vin: list) -> int | None:
    vals = [i.get("valueZat", (i.get("prevout") or {}).get("valueZat")) for i in vin]
    return None if any(v is None for v in vals) else sum(int(v) for v in vals)


def classify(tx: dict) -> TxSummary:
    vin = tx.get("vin") or []
    coinbase = any("coinbase" in i for i in vin)
    sap = Bundle("sapling", spends=len(tx.get("vShieldedSpend") or []), outputs=len(tx.get("vShieldedOutput") or []),
                 value_balance_zat=int(tx.get("valueBalanceZat") or 0))
    bundles = {"sapling": sap}
    for p in ("orchard", "ironwood"):
        b = tx.get(p) or {}
        bundles[p] = Bundle(p, actions=len(b.get("actions") or []), value_balance_zat=int(b.get("valueBalanceZat") or 0))
    return TxSummary(
        txid=tx.get("txid", ""), version=int(tx.get("version", 0)), coinbase=coinbase,
        transparent_in=0 if coinbase else len(vin), transparent_out=len(tx.get("vout") or []),
        sprout_joinsplits=len(tx.get("vjoinsplit") or []), bundles=bundles,
        unknown_keys=_unknown_fields(tx),
        transparent_out_zat=sum(int(o.get("valueZat") or 0) for o in tx.get("vout") or []),
        transparent_in_zat=0 if (coinbase or not vin) else _vin_value(vin),
    )
