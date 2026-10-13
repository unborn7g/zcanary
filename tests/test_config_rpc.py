import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from zcanary import config
from zcanary.rpc import RpcClient, RpcError

BASE = {"network": {"name": "regtest", "zebra": {"url": "http://x", "user": "u", "password_env": "ZC_TEST_PW"},
                    "zallet": {"url": "http://y"}},
        "app": {"pay_command": "true", "result_file": "r.json"}}


def test_config_defaults():
    c = config.parse(BASE)
    assert c.expect_pool == "any-shielded" and c.expect_privacy == "fully-shielded"


def test_config_rejects_inline_password():
    bad = json.loads(json.dumps(BASE)); bad["network"]["zebra"]["password"] = "hunter2"
    with pytest.raises(config.ConfigError):
        config.parse(bad)


@pytest.mark.parametrize("path,val", [(("network", "name"), "mainnetz"), (("expect", "pool"), "sprout")])
def test_config_rejects_bad_values(path, val):
    d = json.loads(json.dumps(BASE)); d.setdefault(path[0], {})[path[1]] = val
    with pytest.raises(config.ConfigError):
        config.parse(d)


def test_repo_config_loads():
    from pathlib import Path
    c = config.load(Path(__file__).resolve().parent.parent / "zcanary.toml")
    assert c.network == "regtest" and "hunter" not in repr(c)


def test_endpoint_repr_hides_password(monkeypatch):
    monkeypatch.setenv("ZC_TEST_PW", "s3cr3t-pw")
    ep = config.parse(BASE).zebra
    assert ep.password() == "s3cr3t-pw" and "s3cr3t" not in repr(ep)


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        auth = self.headers.get("Authorization", "")
        if req["method"] == "echo_auth":
            body = {"result": auth}
        elif req["method"] == "boom":
            body = {"error": {"code": -1, "message": "bad thing with s3cr3t-pw inside"}}
        else:
            body = {"result": req["params"]}
        data = json.dumps(body).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def server():
    s = HTTPServer(("127.0.0.1", 0), _H)
    t = threading.Thread(target=s.serve_forever, daemon=True); t.start()
    yield f"http://127.0.0.1:{s.server_port}"
    s.shutdown()


def test_rpc_roundtrip_and_redaction(server, monkeypatch):
    monkeypatch.setenv("ZC_PW", "s3cr3t-pw")
    c = RpcClient(config.Endpoint(server, "zebra", "ZC_PW"), timeout_s=2)
    assert c.call("anything", 1, "a") == [1, "a"]
    assert c.call("echo_auth").startswith("Basic ")
    with pytest.raises(RpcError) as e:
        c.call("boom")
    assert "s3cr3t" not in str(e.value) and "***" in str(e.value)


def test_rpc_transport_error_timeout():
    c = RpcClient(config.Endpoint("http://127.0.0.1:9"), timeout_s=0.5)
    with pytest.raises(RpcError):
        c.call("x")
