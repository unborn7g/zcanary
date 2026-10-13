"""zcanary.toml loader. Secrets are never stored in the file: only the *name* of an env var."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

NETWORKS = ("mainnet", "testnet", "regtest")
POOLS = ("any-shielded", "orchard", "ironwood", "sapling")
PRIVACY = ("fully-shielded",)


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Endpoint:
    url: str
    user: str = ""
    password_env: str = ""  # env var name holding the password

    def password(self) -> str:
        return os.environ.get(self.password_env, "") if self.password_env else ""

    def __repr__(self) -> str:  # never show secrets
        return f"Endpoint(url={self.url!r}, user={self.user!r}, password=<{self.password_env or 'none'}>)"


@dataclass(frozen=True)
class Config:
    network: str
    zebra: Endpoint
    zallet: Endpoint
    pay_command: str
    result_file: str
    start_command: str = ""
    expect_privacy: str = "fully-shielded"
    expect_pool: str = "any-shielded"
    report_path: str = "report.json"
    timeout_s: float = 30.0
    base_dir: Path = field(default_factory=Path.cwd)


def _req(d: dict, key: str, where: str):
    if key not in d:
        raise ConfigError(f"missing required key {where}.{key}")
    return d[key]


def _endpoint(d: dict, where: str) -> Endpoint:
    if "password" in d:
        raise ConfigError(f"{where}.password is not allowed; set {where}.password_env to an env var name")
    return Endpoint(url=_req(d, "url", where), user=d.get("user", ""), password_env=d.get("password_env", ""))


def parse(data: dict, base_dir: Path | None = None) -> Config:
    net = _req(data, "network", "")
    name = _req(net, "name", "network")
    if name not in NETWORKS:
        raise ConfigError(f"network.name must be one of {NETWORKS}, got {name!r}")
    app = _req(data, "app", "")
    exp = data.get("expect", {})
    privacy = exp.get("privacy", "fully-shielded")
    pool = exp.get("pool", "any-shielded")
    if privacy not in PRIVACY:
        raise ConfigError(f"expect.privacy must be one of {PRIVACY}")
    if pool not in POOLS:
        raise ConfigError(f"expect.pool must be one of {POOLS}")
    return Config(
        network=name,
        zebra=_endpoint(_req(net, "zebra", "network"), "network.zebra"),
        zallet=_endpoint(_req(net, "zallet", "network"), "network.zallet"),
        start_command=net.get("start_command", ""),
        pay_command=_req(app, "pay_command", "app"),
        result_file=_req(app, "result_file", "app"),
        expect_privacy=privacy,
        expect_pool=pool,
        report_path=data.get("report", {}).get("path", "report.json"),
        timeout_s=float(net.get("timeout_s", 30)),
        base_dir=base_dir or Path.cwd(),
    )


def load(path: str | Path) -> Config:
    p = Path(path)
    with p.open("rb") as f:
        return parse(tomllib.load(f), base_dir=p.resolve().parent)
