"""Minimal JSON-RPC clients for Zebra and Zallet. Stdlib only; never logs credentials."""
from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request
from typing import Any

from .config import Endpoint
from .redact import secret

log = logging.getLogger("zcanary.rpc")


class RpcError(RuntimeError):
    def __init__(self, method: str, message: str, code: int | None = None):
        super().__init__(f"{method}: {message}")
        self.method, self.code = method, code


class RpcClient:
    def __init__(self, endpoint: Endpoint, timeout_s: float = 30.0, name: str = "rpc"):
        self.endpoint, self.timeout_s, self.name = endpoint, timeout_s, name
        self._id = 0

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.endpoint.user:
            tok = base64.b64encode(f"{self.endpoint.user}:{self.endpoint.password()}".encode()).decode()
            h["Authorization"] = f"Basic {tok}"
        return h

    def call(self, method: str, *params: Any) -> Any:
        self._id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": list(params)}).encode()
        req = urllib.request.Request(self.endpoint.url, data=body, headers=self._headers(), method="POST")
        log.debug("%s -> %s %s", self.name, self.endpoint.url, method)  # params not logged (may hold memos)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                payload = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            try:
                payload = json.loads(raw)
            except ValueError:
                raise RpcError(method, secret(f"HTTP {e.code}: {raw[:200]}", self.endpoint.password())) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise RpcError(method, secret(f"transport error: {e}", self.endpoint.password())) from None
        if payload.get("error"):
            err = payload["error"]
            raise RpcError(method, secret(str(err.get("message", err)), self.endpoint.password()), err.get("code"))
        return payload.get("result")


class Zebra(RpcClient):
    def __init__(self, ep: Endpoint, timeout_s: float = 30.0):
        super().__init__(ep, timeout_s, "zebra")

    def chain(self) -> dict: return self.call("getblockchaininfo")
    def rawtx(self, txid: str) -> dict: return self.call("getrawtransaction", txid, 1)
    def rawtx_hex(self, txid: str) -> str: return self.call("getrawtransaction", txid, 0)
    def block(self, h: str) -> dict: return self.call("getblock", h, 1)
    def receivers(self, ua: str) -> dict: return self.call("z_listunifiedreceivers", ua)


class Zallet(RpcClient):
    def __init__(self, ep: Endpoint, timeout_s: float = 30.0):
        super().__init__(ep, timeout_s, "zallet")

    def view(self, txid: str) -> dict: return self.call("z_viewtransaction", txid)
