"""C1: node/app network identity, genesis hashes, and recipient prefix must agree.

Zebra 6.2.3 getblockchaininfo reports chain="test" for both testnet and regtest, so
chain name alone cannot tell them apart. The genesis block hash (getblockhash 0) can;
it is checked against the known network constants and against the app's wallet RPC view.
"""
from __future__ import annotations

from .. import address
from ..redact import addr as r
from . import FAIL, NOT_RUN, PASS, CheckResult, not_run

_CHAIN = {"main": {"mainnet"}, "test": {"testnet", "regtest"}, "regtest": {"regtest"}}

# Regtest hash was observed live from the pinned Z3 Zebra 6.2.3 node; mainnet/testnet
# are the well-known Zcash genesis hashes (their live endpoints are not queried here).
GENESIS = {
    "00040fe8ec8471911baa1db1266ea15dd06b4a8a5c453883c000b031973dce08": "mainnet",
    "05a60a92d99d85997cce3b87616c089f6124d7342af37106edc76126334a2c38": "testnet",
    "029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327": "regtest",
}


def check(
    config_network: str | None,
    chain_info: dict | None,
    recipient: str | None,
    genesis_hash: str | None = None,
    app_network: str | None = None,
    app_genesis_hash: str | None = None,
) -> CheckResult:
    cid = "C1"
    if not config_network or chain_info is None or not recipient or not genesis_hash:
        return not_run(cid, "need expected network, node getblockchaininfo, getblockhash 0, and recipient address")
    chain = chain_info.get("chain")
    node_nets = _CHAIN.get(chain, set())
    gnet = GENESIS.get(genesis_hash)
    app_gnet = GENESIS.get(app_genesis_hash) if app_genesis_hash else None
    kind, addr_net = address.classify(recipient)
    ev = {
        "config": config_network,
        "app_network": app_network,
        "app_genesis_hash": app_genesis_hash,
        "app_genesis_network": app_gnet,
        "node_chain": chain,
        "node_implies": sorted(node_nets),
        "genesis_hash": genesis_hash,
        "genesis_network": gnet,
        "recipient": r(recipient),
        "recipient_kind": kind,
        "recipient_network": addr_net,
    }
    if app_network is None or app_genesis_hash is None:
        return CheckResult(
            cid,
            NOT_RUN,
            "not run: app did not report its actual wallet-RPC network and genesis hash",
            ev,
            "Have the app adapter report the network and genesis it reads from its own wallet endpoint, then rerun.",
        )
    problems = []
    if config_network not in node_nets:
        problems.append(f"node reports chain {chain!r}, config says {config_network!r}")
    if gnet is None:
        problems.append(f"node has unknown genesis hash {genesis_hash}")
    elif gnet != config_network:
        problems.append(f"node genesis hash is the {gnet} genesis, config says {config_network!r}")
    if app_network != config_network:
        problems.append(f"app reports network {app_network!r}, config says {config_network!r}")
    if app_gnet is None:
        problems.append(f"app wallet endpoint has unknown genesis hash {app_genesis_hash}")
    elif app_gnet != config_network:
        problems.append(f"app wallet endpoint genesis hash is the {app_gnet} genesis, config says {config_network!r}")
    if app_genesis_hash != genesis_hash:
        problems.append("app wallet endpoint genesis hash does not match the node genesis hash")
    if not address.network_matches(addr_net, config_network):
        problems.append(f"recipient address is for {addr_net!r}, config says {config_network!r}")
    if problems:
        return CheckResult(
            cid,
            FAIL,
            "; ".join(problems),
            ev,
            "Point the app at the node for the configured network and use a recipient address for it.",
        )
    return CheckResult(
        cid,
        PASS,
        f"node chain {chain!r}, node/app genesis hashes, app network, recipient prefix, and config all agree on {config_network}",
        ev,
    )
