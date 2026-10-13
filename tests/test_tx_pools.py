from zcanary import pools, tx


def test_classify_spike_tx(fx):
    s = tx.classify(fx("spike/rawtx.json"))
    assert s.version == 6 and not s.coinbase
    assert (s.transparent_in, s.transparent_out, s.sprout_joinsplits) == (0, 0, 0)
    assert s.pools_used == ["ironwood"]
    assert s.bundles["ironwood"].value_balance_zat == 25000
    assert s.unknown_keys == []


def test_classify_sapling_cross_tx(fx):
    s = tx.classify(fx("pools/c_sapling_rawtx.json"))
    assert s.pools_used == ["sapling", "ironwood"]
    assert s.bundles["sapling"].value_balance_zat == -10000000


def test_block_deltas_match_bundle_value_balance(fx):
    for case in ("a_orchard_only", "b_unified", "c_sapling"):
        s = tx.classify(fx(f"pools/{case}_rawtx.json"))
        d = pools.block_deltas(fx(f"pools/{case}_block.json"))
        for p in tx.SHIELDED_POOLS:
            assert d.get(p, 0) == -s.bundles[p].value_balance_zat, (case, p)


def test_chaininfo_diff(fx):
    d = pools.nonzero(pools.diff(fx("spike/chaininfo_before.json"), fx("spike/chaininfo_after.json")))
    assert d == {"transparent": 625025000, "ironwood": -25000}


def test_coinbase_detected(fx):
    s = tx.classify(fx("synthetic/tx_coinbase.json"))
    assert s.coinbase and s.transparent_in == 0
