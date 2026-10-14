"""Regenerate hand-made failing fixtures from the real spike fixtures. Run: python fixtures/synthetic/make.py"""
import copy, json
from pathlib import Path

H = Path(__file__).parent
spike = H.parent / "spike"
tx = json.loads((spike / "rawtx.json").read_text())["result"]
info = json.loads((spike / "chaininfo_after.json").read_text())["result"]


def dump(name, obj, note):
    (H / name).write_text(json.dumps({"_synthetic": note, "result": obj}, indent=1) + "\n")


t = copy.deepcopy(tx)
t["vin"] = [{"txid": "11" * 32, "vout": 0, "scriptSig": {"asm": "<sig> <pubkey>", "hex": "00"}, "sequence": 4294967295}]
t["vout"] = [{"value": 0.1, "valueZat": 10000000, "n": 0,
              "scriptPubKey": {"asm": "OP_DUP OP_HASH160 00 OP_EQUALVERIFY OP_CHECKSIG", "hex": "76a9", "type": "pubkeyhash",
                               "addresses": ["tmHGc6iUGPss75FqukTNnCyLsH6bULzYN6Q"]}}]
t["ironwood"]["valueBalanceZat"] = 10000000 + 10000; t["ironwood"]["valueBalance"] = 0.1001
dump("tx_transparent_leg.json", t, "spike tx with a transparent input and a 0.1 ZEC transparent output added")

t = copy.deepcopy(tx)
t["vShieldedOutput"] = [{"cv": "00", "cmu": "00", "ephemeralKey": "00", "encCiphertext": "00", "outCiphertext": "00", "proof": "00"}]
t["valueBalanceZat"] = -50000000; t["valueBalance"] = -0.5
t["ironwood"]["valueBalanceZat"] = 50025000; t["ironwood"]["valueBalance"] = 0.50025
dump("tx_cross_pool.json", t, "spike tx edited: 0.5 ZEC moves ironwood -> sapling")

t = copy.deepcopy(tx)
t["vin"] = [{"coinbase": "5a00", "sequence": 4294967295}]
dump("tx_coinbase.json", t, "spike tx edited to look like a coinbase")

i = copy.deepcopy(info); i["chain"] = "main"
dump("chaininfo_mainnet.json", i, "spike chaininfo with chain=main")

dump("receivers_transparent_only.json", {"p2pkh": "tmHGc6iUGPss75FqukTNnCyLsH6bULzYN6Q"}, "UA with only a p2pkh receiver")

# --- Step 1 fixtures (C4b fee derivation)
t = copy.deepcopy(tx)
t["vout"] = [{"value": 0.5, "valueZat": 50000000, "n": 0,
              "scriptPubKey": {"asm": "OP_DUP OP_HASH160 00 OP_EQUALVERIFY OP_CHECKSIG", "hex": "76a9", "type": "pubkeyhash",
                               "addresses": ["tmHGc6iUGPss75FqukTNnCyLsH6bULzYN6Q"]}}]
t["ironwood"]["valueBalanceZat"] = 50000000 + 30000; t["ironwood"]["valueBalance"] = 0.5003
dump("tx_deshield_gt_fee.json", t, "spike tx edited: 0.5 ZEC deshielded from ironwood to a transparent output (vb >> fee)")

t = copy.deepcopy(tx)
t["vShieldedOutput"] = [{"cv": "00", "cmu": "00", "ephemeralKey": "00", "encCiphertext": "00", "outCiphertext": "00", "proof": "00"}] * 2
t["valueBalanceZat"] = -1; t["valueBalance"] = -0.00000001
t["ironwood"]["valueBalanceZat"] = 35001; t["ironwood"]["valueBalance"] = 0.00035001
dump("tx_cross_pool_tiny.json", t, "spike tx edited: 1 zat crosses ironwood -> sapling (sum still equals ZIP-317 fee)")

t = copy.deepcopy(tx)
t["ironwood"]["valueBalanceZat"] = 40000; t["ironwood"]["valueBalance"] = 0.0004
dump("tx_fee_mismatch.json", t, "spike tx edited: ironwood vb 40000 but ZIP-317 fee for 5 actions is 25000, no transparent legs")

i = copy.deepcopy(info); i["chain"] = "test"
dump("chaininfo_testnet.json", i, "spike chaininfo; chain=test is reported for BOTH testnet and regtest by Zebra 6.2.3")
