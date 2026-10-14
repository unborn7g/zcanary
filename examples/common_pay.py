"""Small regtest-only example app shared by the six explicitly authorized fixtures.

This code is intentionally boring: it demonstrates the adapter contract and uses wallet
RPC credentials exclusively from the environment. It never prints RPC responses, memos,
canary values, addresses, or credentials.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


class AppError(RuntimeError):
    pass


def rpc(method: str, *params: Any) -> Any:
    url = os.environ.get("ZCANARY_ZALLET_RPC", "")
    user = os.environ.get("ZCANARY_ZALLET_USER", "")
    password = os.environ.get("ZCANARY_ZALLET_PASSWORD", "")
    if not url or not user or not password:
        raise AppError("wallet RPC environment is incomplete")
    request_body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": list(params)}
    ).encode("utf-8")
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        url,
        data=request_body,
        headers={"Content-Type": "application/json", "Authorization": f"Basic {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        raise AppError("wallet RPC request failed") from None
    if not isinstance(payload, dict) or payload.get("error"):
        raise AppError("wallet RPC method failed")
    return payload.get("result")


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise AppError(f"required environment variable {name} is missing")
    return value


def _amount() -> float:
    raw = _env("ZCANARY_AMOUNT")
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        raise AppError("configured amount is invalid") from None
    if amount <= 0:
        raise AppError("configured amount must be positive")
    return float(amount)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _receivers(address: str) -> dict[str, str]:
    result = rpc("z_listunifiedreceivers", address)
    if not isinstance(result, dict):
        raise AppError("wallet did not return receiver information")
    return {str(k): str(v) for k, v in result.items()}


def _app_identity() -> tuple[str, str]:
    # Report the chain/genesis actually seen by the app's wallet RPC, not the
    # harness's expected-network environment variable. Zebra uses chain="test"
    # for both testnet and regtest, so the genesis hash is decisive.
    info = rpc("getblockchaininfo")
    genesis = rpc("getblockhash", 0)
    if not isinstance(info, dict) or not isinstance(genesis, str):
        raise AppError("wallet RPC did not return a usable network identity")
    known = {
        "029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327": "regtest",
        "05a60a92d99d85997cce3b87616c089f6124d7342af37106edc76126334a2c38": "testnet",
        "00040fe8ec8471911baa1db1266ea15dd06b4a8a5c453883c000b031973dce08": "mainnet",
    }
    return known.get(genesis, f"unknown:{info.get('chain')}"), genesis


def _pay(mode: str) -> None:
    _env("ZCANARY_NETWORK")  # required environment contract; app network is read independently below
    requested_address = _env("ZCANARY_RECIPIENT_ADDRESS")
    sender = _env("ZCANARY_SENDER_ADDRESS")
    recipient_account = _env("ZCANARY_RECIPIENT_ACCOUNT")
    result_file = Path(_env("ZCANARY_RESULT_FILE"))
    log_file = Path(os.environ.get("ZCANARY_LOG_FILE") or "out/app.log")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("", encoding="utf-8")
    memo = _env("ZCANARY_MEMO")

    if mode == "wrong-network":
        _write_json(
            result_file,
            {
                "app_network": "testnet",
                "recipient_address": requested_address,
                "recipient_account": recipient_account,
                "app_genesis_hash": "05a60a92d99d85997cce3b87616c089f6124d7342af37106edc76126334a2c38",
            },
        )
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("app configured for a different network\n", encoding="utf-8")
        return

    receiver_types = _receivers(requested_address)
    app_network, app_genesis_hash = _app_identity()
    payment_address = requested_address
    policy = "FullPrivacy"
    amounts: list[dict[str, Any]]
    if mode == "transparent-fallback":
        payment_address = os.environ.get("ZCANARY_TRANSPARENT_RECEIVER", "")
        if not payment_address:
            payment_address = receiver_types.get("p2pkh", "")
        if not payment_address:
            raise AppError("test context lacks the requested transparent receiver")
        # Deliberately reproduce transparent routing; FullPrivacy should not pick this path.
        policy = "AllowRevealedAmounts"
        amounts = [{"address": payment_address, "amount": _amount()}]
    elif mode == "cross-pool-payer":
        payment_address = os.environ.get("ZCANARY_SAPLING_RECEIVER", "")
        if not payment_address:
            payment_address = receiver_types.get("sapling", "")
        if not payment_address:
            raise AppError("test context lacks a Sapling receiver")
        # This explicitly permits a pool crossing so C4/C4b can inspect the actual transaction.
        policy = "AllowRevealedAmounts"
        amounts = [{"address": payment_address, "amount": _amount(), "memo": memo.encode().hex()}]
    elif mode == "memo-dropper":
        # Deliberate negative fixture: shielded payment, but do not attach the generated memo.
        amounts = [{"address": payment_address, "amount": _amount()}]
    else:
        amounts = [{"address": payment_address, "amount": _amount(), "memo": memo.encode().hex()}]

    opid = rpc("z_sendmany", sender, amounts, 1, None, policy)
    if not isinstance(opid, str) or not opid:
        raise AppError("wallet did not return an operation id")
    completed = None
    for _ in range(90):
        operation_result = rpc("z_getoperationresult", [opid])
        if isinstance(operation_result, list) and operation_result:
            completed = operation_result[0]
            break
        time.sleep(2)
    if not isinstance(completed, dict) or completed.get("status") != "success":
        raise AppError("wallet payment operation did not complete successfully")
    txid = completed.get("result", {}).get("txid")
    if not isinstance(txid, str) or not txid:
        raise AppError("wallet operation did not return a transaction id")

    _write_json(
        result_file,
        {
            "txid": txid,
            "recipient_address": requested_address,
            "payment_recipient": payment_address,
            "recipient_account": recipient_account,
            "app_network": app_network,
            "app_genesis_hash": app_genesis_hash,
        },
    )
    log_file.parent.mkdir(parents=True, exist_ok=True)
    if mode == "log-leaker":
        # Intentional fixture: only runtime markers (never fixed values) go into this log.
        log_file.write_text(
            "memo=" + memo + "\n"
            + "label=" + _env("ZCANARY_LABEL") + "\n"
            + "test_id=" + _env("ZCANARY_TEST_ID") + "\n",
            encoding="utf-8",
        )
    else:
        log_file.write_text("payment completed\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in {
        "clean-shop",
        "transparent-fallback",
        "wrong-network",
        "log-leaker",
        "memo-dropper",
        "cross-pool-payer",
    }:
        print("expected one known example name", file=sys.stderr)
        return 2
    try:
        _pay(args[0])
    except AppError as e:
        print(f"example app failed: {e}", file=sys.stderr)
        return 1
    print("example app completed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
