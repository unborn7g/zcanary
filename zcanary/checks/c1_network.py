"""C1: node chain name, recipient address prefix, and app config agree on the network."""
from __future__ import annotations

from .. import address
from ..redact import addr as r
from . import FAIL, PASS, CheckResult, not_run

# Zebra getblockchaininfo.chain: "main" for mainnet; "test" for testnet AND regtest (observed on Zebra 6.2.3).
_CHAIN = {"main": {"mainnet"}, "test": {"testnet", "regtest"}, "regtest": {"regtest"}}


def check(config_network: str | None, chain_info: dict | None, recipient: str | None) -> CheckResult:
    cid = "C1"
    if not config_network or chain_info is None or not recipient:
        return not_run(cid, "need config network, node getblockchaininfo, and recipient address")
    chain = chain_info.get("chain")
    node_nets = _CHAIN.get(chain, set())
    kind, addr_net = address.classify(recipient)
    ev = {"config": config_network, "node_chain": chain, "node_implies": sorted(node_nets),
          "recipient": r(recipient), "recipient_kind": kind, "recipient_network": addr_net}
    problems = []
    if config_network not in node_nets:
        problems.append(f"node reports chain {chain!r}, config says {config_network!r}")
    if not address.network_matches(addr_net, config_network):
        problems.append(f"recipient address is for {addr_net!r}, config says {config_network!r}")
    if problems:
        return CheckResult(cid, FAIL, "; ".join(problems), ev,
                           "Point the app at the node for the configured network and use a recipient address for it.")
    return CheckResult(cid, PASS, f"node, address, and config agree on {config_network}", ev)
