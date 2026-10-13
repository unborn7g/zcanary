import json, sys, os
F = sys.argv[1]
L = lambda f: json.load(open(os.path.join(F, f)))["result"]
print("| case | tx bundles (count, valueBalanceZat) | block valuePools valueDeltaZat (non-zero) |")
print("|---|---|---|")
for c in ["a_orchard_only", "b_unified", "c_sapling"]:
    if not os.path.exists(os.path.join(F, f"{c}_rawtx.json")): print(f"| {c} | not run | |"); continue
    t = L(f"{c}_rawtx.json"); b = L(f"{c}_block.json")
    bund = [f"transparent vin={len(t['vin'])} vout={len(t['vout'])}",
            f"sapling spends={len(t['vShieldedSpend'])} outputs={len(t['vShieldedOutput'])} vb={t['valueBalanceZat']}",
            f"orchard actions={len(t['orchard']['actions'])} vb={t['orchard']['valueBalanceZat']}",
            f"ironwood actions={len(t.get('ironwood',{}).get('actions',[]))} vb={t.get('ironwood',{}).get('valueBalanceZat',0)}"]
    d = {p['id']: p['valueDeltaZat'] for p in b['valuePools'] if p['valueDeltaZat']}
    print(f"| {c} | {'; '.join(bund)} | {d} (block {b['height']}, nTx={b['nTx']}) |")
