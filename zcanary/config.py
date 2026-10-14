"""zcanary.toml loader. Secrets are never stored in the file: only env-var names."""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

NETWORKS = ("mainnet", "testnet", "regtest")
POOLS = ("any-shielded", "orchard", "ironwood", "sapling")
PRIVACY = ("fully-shielded",)
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
    log_file: str = ""
    start_command: str = ""  # test-network lifecycle
    stop_command: str = ""
    context_file: str = ""
    app_start_command: str = ""
    log_commands: tuple[str, ...] = ()
    app_stop_command: str = ""
    amount: str = "0.1"
    expect_privacy: str = "fully-shielded"
    expect_pool: str = "any-shielded"
    report_path: str = "report.json"
    markdown_path: str = "report.md"
    scan_files: tuple[str, ...] = ()
    timeout_s: float = 30.0
    command_timeout_s: float = 1200.0
    base_dir: Path = field(default_factory=Path.cwd)


def _req(d: dict, key: str, where: str):
    if key not in d:
        raise ConfigError(f"missing required key {where}.{key}".lstrip("."))
    return d[key]


def _command(value: object, where: str, *, optional: bool = False) -> str:
    if value is None and optional:
        return ""
    if not isinstance(value, str):
        raise ConfigError(f"{where} must be a string")
    if not optional and not value.strip():
        raise ConfigError(f"{where} must not be empty")
    return value


def _string_list(value: object, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    elif isinstance(value, tuple):
        value = list(value)
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ConfigError(f"{where} must be a string or a list of non-empty strings")
    return tuple(value)


def _endpoint(d: dict, where: str) -> Endpoint:
    if not isinstance(d, dict):
        raise ConfigError(f"{where} must be a table")
    if "password" in d:
        raise ConfigError(f"{where}.password is not allowed; set {where}.password_env to an env var name")
    url = _req(d, "url", where)
    if not isinstance(url, str):
        raise ConfigError(f"{where}.url must be a string")
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ConfigError(f"{where}.url must be an http(s) URL with a host")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigError(f"{where}.url must not contain credentials; use user and password_env fields")
    user = d.get("user", "")
    password_env = d.get("password_env", "")
    if not isinstance(user, str) or not isinstance(password_env, str):
        raise ConfigError(f"{where}.user and {where}.password_env must be strings")
    if password_env and not _ENV_NAME.fullmatch(password_env):
        raise ConfigError(f"{where}.password_env must be an environment variable name")
    return Endpoint(url=url, user=user, password_env=password_env)


def parse(data: dict, base_dir: Path | None = None) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("top-level config must be a table")
    net = _req(data, "network", "")
    if not isinstance(net, dict):
        raise ConfigError("network must be a table")
    name = _req(net, "name", "network")
    if name not in NETWORKS:
        raise ConfigError(f"network.name must be one of {NETWORKS}, got {name!r}")
    app = _req(data, "app", "")
    if not isinstance(app, dict):
        raise ConfigError("app must be a table")
    exp = data.get("expect", {})
    report = data.get("report", {})
    if not isinstance(exp, dict) or not isinstance(report, dict):
        raise ConfigError("expect and report must be tables")
    privacy = exp.get("privacy", "fully-shielded")
    pool = exp.get("pool", "any-shielded")
    if privacy not in PRIVACY:
        raise ConfigError(f"expect.privacy must be one of {PRIVACY}")
    if pool not in POOLS:
        raise ConfigError(f"expect.pool must be one of {POOLS}")

    pay = app.get("pay", app.get("pay_command"))
    pay = _command(pay, "app.pay")
    amount = app.get("amount", "0.1")
    if not isinstance(amount, (str, int, float)) or isinstance(amount, bool):
        raise ConfigError("app.amount must be a positive decimal amount")
    amount = str(amount)
    try:
        if Decimal(amount) <= 0:
            raise ConfigError("app.amount must be positive")
    except InvalidOperation as e:
        raise ConfigError("app.amount must be a positive decimal amount") from e

    report_path = report.get("path", "report.json")
    if not isinstance(report_path, str) or not report_path:
        raise ConfigError("report.path must be a non-empty path")
    markdown_path = report.get("markdown_path")
    if markdown_path is None:
        markdown_path = str(Path(report_path).with_suffix(".md"))
    if not isinstance(markdown_path, str) or not markdown_path:
        raise ConfigError("report.markdown_path must be a non-empty path")

    try:
        timeout_s = float(net.get("timeout_s", 30))
        command_timeout_s = float(net.get("command_timeout_s", 1200))
    except (TypeError, ValueError) as e:
        raise ConfigError("network timeouts must be numeric") from e
    if timeout_s <= 0 or command_timeout_s <= 0:
        raise ConfigError("network timeouts must be positive")

    return Config(
        network=name,
        zebra=_endpoint(_req(net, "zebra", "network"), "network.zebra"),
        zallet=_endpoint(_req(net, "zallet", "network"), "network.zallet"),
        start_command=_command(net.get("start_command", ""), "network.start_command", optional=True),
        stop_command=_command(net.get("stop_command", ""), "network.stop_command", optional=True),
        context_file=_command(net.get("context_file", ""), "network.context_file", optional=True),
        pay_command=pay,
        result_file=_command(_req(app, "result_file", "app"), "app.result_file"),
        log_file=_command(app.get("log_file", ""), "app.log_file", optional=True),
        app_start_command=_command(app.get("start", ""), "app.start", optional=True),
        log_commands=_string_list(app.get("logs", ()), "app.logs"),
        app_stop_command=_command(app.get("stop", ""), "app.stop", optional=True),
        amount=amount,
        expect_privacy=privacy,
        expect_pool=pool,
        report_path=report_path,
        markdown_path=markdown_path,
        scan_files=_string_list(report.get("scan_files", ()), "report.scan_files"),
        timeout_s=timeout_s,
        command_timeout_s=command_timeout_s,
        base_dir=base_dir or Path.cwd(),
    )


def load(path: str | Path) -> Config:
    p = Path(path)
    with p.open("rb") as f:
        return parse(tomllib.load(f), base_dir=p.resolve().parent)
