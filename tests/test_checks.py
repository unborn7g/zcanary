import pytest

from zcanary import address, checks, pools, tx
from zcanary.checks import FAIL, NOT_RUN, PASS

KEYS = {"id", "status", "evidence", "message", "fix"}


def spike_ua(fx):
    return fx("spike/op.json")[0]["params"]["amounts"][0]["address"]


def assert_shape(r):
    assert set(r.to_dict()) == KEYS and r.status in (PASS, FAIL, NOT_RUN)
    if r.status == FAIL:
        assert r.fix


# ---------- C1
def test_c1_pass(fx):
    r = checks.c1("regtest", fx("spike/chaininfo_after.json"), spike_ua(fx), REGTEST_GENESIS, "regtest", REGTEST_GENESIS); assert_shape(r)
    assert r.status == PASS


def test_c1_fail_node_mainnet(fx):
    r = checks.c1("regtest", fx("synthetic/chaininfo_mainnet.json"), spike_ua(fx), REGTEST_GENESIS, "regtest", REGTEST_GENESIS); assert_shape(r)
    assert r.status == FAIL and "node reports chain 'main'" in r.message


def test_c1_fail_address_wrong_network(fx):
    r = checks.c1("regtest", fx("spike/chaininfo_after.json"), "u1" + "q" * 100, REGTEST_GENESIS, "regtest", REGTEST_GENESIS)
    assert r.status == FAIL and "mainnet" in r.message


def test_c1_fail_config_mismatch(fx):
    r = checks.c1("mainnet", fx("spike/chaininfo_after.json"), spike_ua(fx), REGTEST_GENESIS, "regtest", REGTEST_GENESIS)
    assert r.status == FAIL


def test_c1_not_run():
    assert checks.c1("regtest", None, "x", REGTEST_GENESIS, "regtest", REGTEST_GENESIS).status == NOT_RUN


def test_c1_evidence_redacted(fx):
    ua = spike_ua(fx)
    r = checks.c1("regtest", fx("spike/chaininfo_after.json"), ua, REGTEST_GENESIS, "regtest", REGTEST_GENESIS)
    assert ua not in str(r.evidence)


def test_c1_not_run_without_app_network(fx):
    r = checks.c1("regtest", fx("spike/chaininfo_after.json"), spike_ua(fx), REGTEST_GENESIS)
    assert r.status == NOT_RUN and "app did not report" in r.message


def test_c1_fail_app_network_mismatch(fx):
    r = checks.c1("regtest", fx("spike/chaininfo_after.json"), spike_ua(fx), REGTEST_GENESIS, "testnet", TESTNET_GENESIS)
    assert r.status == FAIL and "app reports network 'testnet'" in r.message


def test_c1_fail_app_genesis_mismatch(fx):
    r = checks.c1("regtest", fx("spike/chaininfo_after.json"), spike_ua(fx), REGTEST_GENESIS, "regtest", TESTNET_GENESIS)
    assert r.status == FAIL and "app wallet endpoint genesis hash" in r.message


# ---------- C2
def test_c2_fail_real_default_ua_has_p2pkh(fx):
    r = checks.c2(spike_ua(fx), fx("spike/receivers_recipient.json")); assert_shape(r)
    assert r.status == FAIL and "p2pkh" in r.message and "address-policy" in r.message
    assert "does not establish" in r.evidence["finding_scope"]


def test_c2_pass_shielded_only(fx):
    rc = {k: v for k, v in fx("spike/receivers_recipient.json").items() if k != "p2pkh"}
    assert checks.c2(spike_ua(fx), rc).status == PASS


def test_c2_fail_transparent_only_ua(fx):
    assert checks.c2(spike_ua(fx), fx("synthetic/receivers_transparent_only.json")).status == FAIL


@pytest.mark.parametrize("a", ["tmHGc6iUGPss75FqukTNnCyLsH6bULzYN6Q", "t1abcdefghijklmnopqrstuvwxyz12345678"])
def test_c2_fail_plain_transparent(a):
    assert checks.c2(a, None).status == FAIL


def test_c2_pass_legacy_sapling(fx):
    sap = fx("spike/receivers_recipient.json")["sapling"]
    assert checks.c2(sap, None).status == PASS


def test_c2_not_run_when_rpc_failed(fx):
    r = checks.c2(spike_ua(fx), None, receivers_error="timeout")
    assert r.status == NOT_RUN and "timeout" in r.message


# ---------- C3
def test_c3_pass(fx):
    assert checks.c3(tx.classify(fx("spike/rawtx.json"))).status == PASS


def test_c3_fail_transparent_leg(fx):
    r = checks.c3(tx.classify(fx("synthetic/tx_transparent_leg.json"))); assert_shape(r)
    assert r.status == FAIL and "1 in, 1 out" in r.message


def test_c3_coinbase_not_run(fx):
    assert checks.c3(tx.classify(fx("synthetic/tx_coinbase.json"))).status == NOT_RUN


def test_c3_not_run():
    assert checks.c3(None).status == NOT_RUN


@pytest.mark.parametrize("case", ["future_top_level", "future_shielded_bundle", "future_ironwood_member"])
def test_checks_fail_closed_on_unrecognised_transaction_format(fx, case):
    raw = fx("spike/rawtx.json")
    if case == "future_top_level":
        raw["futureTxFlag"] = True
    elif case == "future_shielded_bundle":
        raw["futureShieldedPool"] = {"actions": [], "valueBalanceZat": -1}
    else:
        raw["ironwood"]["futureShieldedValueZat"] = -1

    summary = tx.classify(raw)
    assert summary.unknown_keys
    results = [checks.c3(summary), checks.c4(summary, "any-shielded"), checks.c4b(summary)]
    for result in results:
        assert result.status in (FAIL, NOT_RUN)
        assert "unrecognised transaction format; cannot verify" in result.message


# ---------- C4
@pytest.mark.parametrize("case", ["a_orchard_only", "b_unified"])
def test_c4_real_payments_use_ironwood(fx, case):
    s = tx.classify(fx(f"pools/{case}_rawtx.json"))
    d = pools.block_deltas(fx(f"pools/{case}_block.json"))
    assert checks.c4(s, "any-shielded", d).status == PASS
    assert checks.c4(s, "ironwood", d).status == PASS
    r = checks.c4(s, "orchard", d); assert_shape(r)
    assert r.status == FAIL and "ironwood" in r.message  # data-driven, not address-driven
    assert checks.c4(s, "sapling", d).status == FAIL


def test_c4_fail_multi_pool_real(fx):
    s = tx.classify(fx("pools/c_sapling_rawtx.json"))
    assert checks.c4(s, "any-shielded").status == FAIL
    assert checks.c4(s, "sapling").status == FAIL


def test_c4_fail_no_shielded():
    s = tx.classify({"txid": "x", "version": 5, "vin": [{"txid": "a", "vout": 0}], "vout": [{}]})
    assert checks.c4(s, "any-shielded").status == FAIL


# ---------- C4b
def test_c4b_pass_fee_only(fx):
    deltas = pools.block_deltas(fx("spike/block.json"))
    # The block pool delta is negative by exactly the fee for a same-pool send;
    # attribution comes from the decoded tx valueBalance, not that aggregate sign.
    assert deltas["ironwood"] == -25000
    r = checks.c4b(tx.classify(fx("spike/rawtx.json")), deltas)
    assert r.status == PASS
    assert r.evidence["positive_pools"] == {"ironwood": 25000}
    assert r.evidence["negative_pools"] == {}


def test_c4b_fail_real_cross_pool(fx):
    r = checks.c4b(tx.classify(fx("pools/c_sapling_rawtx.json")), pools.block_deltas(fx("pools/c_sapling_block.json")))
    assert_shape(r)
    assert r.status == FAIL and r.evidence["crossed_zat"] == 10000000
    assert r.evidence["negative_pools"] == {"sapling": -10000000}
    assert r.evidence["positive_pools"] == {"ironwood": 10020000}


def test_c4b_fail_synthetic_cross_pool(fx):
    r = checks.c4b(tx.classify(fx("synthetic/tx_cross_pool.json")))
    assert r.status == FAIL and r.evidence["crossed_zat"] == 50000000


def test_c4b_fail_deshield(fx):
    assert checks.c4b(tx.classify(fx("synthetic/tx_transparent_leg.json"))).status == FAIL


# ---------- MEMO
def test_memo_pass(fx):
    v = fx("spike/viewtx.json")
    canary = next(o["memoStr"] for o in v["outputs"] if o.get("memoStr"))
    assert checks.memo(v, canary).status == PASS


def test_memo_fail_wrong_canary(fx):
    r = checks.memo(fx("spike/viewtx.json"), "UNIT-TEST-OTHER-MEMO"); assert_shape(r)
    assert r.status == FAIL


def test_memo_not_run():
    assert checks.memo(None, "x").status == NOT_RUN


# ---------- address
@pytest.mark.parametrize("a,kind,net", [
    ("uregtest1abc", "unified", "regtest"), ("u1abc", "unified", "mainnet"), ("utest1x", "unified", "testnet"),
    ("zregtestsapling1x", "sapling", "regtest"), ("zs1x", "sapling", "mainnet"),
    ("tmabc", "transparent", "testnet|regtest"), ("t1abc", "transparent", "mainnet"), ("???", "unknown", "unknown"),
])
def test_address_classify(a, kind, net):
    assert address.classify(a) == (kind, net)


# ---------- Step 1: C1 genesis hash
REGTEST_GENESIS = "029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327"
TESTNET_GENESIS = "05a60a92d99d85997cce3b87616c089f6124d7342af37106edc76126334a2c38"


def test_c1_pass_checks_genesis(fx):
    r = checks.c1("regtest", fx("spike/chaininfo_after.json"), spike_ua(fx), REGTEST_GENESIS, "regtest", REGTEST_GENESIS)
    assert r.status == PASS and "genesis" in r.message and r.evidence["genesis_network"] == "regtest"


def test_c1_fail_testnet_genesis_with_chain_test(fx):
    # chain="test" alone cannot distinguish testnet from regtest; the genesis hash can.
    r = checks.c1("regtest", fx("synthetic/chaininfo_testnet.json"), spike_ua(fx), TESTNET_GENESIS, "regtest", REGTEST_GENESIS); assert_shape(r)
    assert r.status == FAIL and "node genesis hash is the testnet genesis" in r.message


def test_c1_fail_unknown_genesis(fx):
    r = checks.c1("regtest", fx("spike/chaininfo_after.json"), spike_ua(fx), "ab" * 32, "regtest", REGTEST_GENESIS)
    assert r.status == FAIL and "unknown genesis" in r.message


def test_c1_not_run_without_genesis(fx):
    assert checks.c1("regtest", fx("spike/chaininfo_after.json"), spike_ua(fx), None, "regtest", REGTEST_GENESIS).status == NOT_RUN


# ---------- Step 1: C4b fee derivation
def test_c4b_reports_derived_fee(fx):
    r = checks.c4b(tx.classify(fx("spike/rawtx.json")))
    assert r.status == PASS and r.evidence["fee_zat"] == 25000 and r.evidence["zip317_fee_zat"] == 25000


def test_c4b_fail_deshield_larger_than_fee(fx):
    r = checks.c4b(tx.classify(fx("synthetic/tx_deshield_gt_fee.json"))); assert_shape(r)
    assert r.status == FAIL and r.evidence["fee_zat"] == 30000 and "transparent" in r.message
    assert r.evidence["transparent_out_zat"] > r.evidence["fee_zat"]
    assert r.evidence["zip317_fee_zat"] == 30000


def test_c4b_fail_cross_pool_any_negative(fx):
    r = checks.c4b(tx.classify(fx("synthetic/tx_cross_pool_tiny.json"))); assert_shape(r)
    assert r.status == FAIL and "negative" in r.message and r.evidence["negative_pools"] == {"sapling": -1}


def test_c4b_pass_same_payment_fee_5000_over_zip317_with_warning(fx):
    raw = fx("spike/rawtx.json")
    raw["ironwood"]["valueBalance"] = 0.0003
    raw["ironwood"]["valueBalanceZat"] = 30000
    r = checks.c4b(tx.classify(raw))
    assert r.status == PASS
    assert r.evidence["fee_zat"] == 30000
    assert r.evidence["zip317_fee_zat"] == 25000
    assert "differs" in r.evidence["warning"]


# ---------- Step 1: C4 orchard explanation
def test_c4_orchard_fail_explains_ironwood(fx):
    s = tx.classify(fx("pools/a_orchard_only_rawtx.json"))
    r = checks.c4(s, "orchard", pools.block_deltas(fx("pools/a_orchard_only_block.json")))
    assert r.status == FAIL
    assert "Orchard receivers deliver into the Ironwood pool" in r.message
    assert r.evidence["ironwood_actions"] > 0 and r.evidence["orchard_actions"] == 0
    assert "ironwood" in r.evidence["block_deltas"] and "docs/pool-behavior.md" in r.evidence["reference"]
