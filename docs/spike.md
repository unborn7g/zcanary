# Historical Phase 1 spike: beta.1 baseline (2026-10-13)

This records the earlier runtime run. It is not the current default image pin; see [version-matrix.md](version-matrix.md) for current beta.3 source/runtime status. The old outputs are regression fixtures, not evidence that beta.3 was runtime-tested.

## Pinned versions at the time of this run
| Component | Tag | Digest |
|---|---|---|
| Z3 | git `e84ce9fd8e864ff0b2a8a62f6ce14392145db0fb` (ZcashFoundation/z3) | n/a |
| Zebra | `zfnd/zebra:6.2.3` | `sha256:bb2a6029db277ee3a10e951dcc0ddd36b4cbcbe0fad684746d695ee21d53fde2` |
| Zallet | `zodlinc/zallet:v0.1.0-beta.1` | `sha256:1849b4469875dc0165942c06d15fa6a7da76b2d43bade578cc8e5903a639869d` (pinned in Z3 compose) |
| cookie-permissions | `alpine:3` (Z3 helper, unpinned upstream) | n/a |
| rpc-router | built locally from `z3/rpc-router` at the Z3 commit above | n/a |

Host used: Debian 13 x86_64, Docker Engine 29.9.0, Compose v5.6.0, Python 3.13.

## Commands
```bash
# 0. Docker (Debian/Ubuntu). Skip if already installed.
curl -fsSL https://get.docker.com | sudo sh
sudo service docker start

# 1. Clean Docker state for the regtest project
cd z3 2>/dev/null && docker compose --env-file .env.regtest --profile "*" down -v; cd -
docker volume rm -f z3-regtest-chain z3-regtest-zallet z3-regtest-zaino z3-regtest-cookie

# 2. Run (the script clones z3 at the pinned commit, runs regtest-init.sh, starts the stack,
#    creates accounts, mines, sends, decodes, and checks)
git clone <this repo> zcanary && cd zcanary
scripts/spike.sh            # fresh: wipes the z3-regtest volumes itself
KEEP=1 scripts/spike.sh     # reuse an already-running stack
```

What the historical beta.1 run did, step by step (the current script has since changed its pins and setup flow; see `docs/version-matrix.md`; each RPC step is also available via `scripts/rpc.sh <url> <method> '<json params>'`):
1. If `net.bridge.bridge-nf-call-iptables=1`, set it to 0. On the dev box, leftover iptables-legacy `FORWARD DROP` rules blocked container-to-container traffic.
2. `git clone https://github.com/ZcashFoundation/z3 && git checkout e84ce9fd…`
3. `./scripts/regtest-init.sh`, then `docker compose --env-file .env.regtest up -d`
4. Router `:8181`: `z_getnewaccount ["sender"]`, `z_getnewaccount ["recipient"]`, `z_listaccounts`, `z_listunifiedreceivers [ua]`
5. Zebra `:29232`: `generatetoaddress [1, <sender orchard-only UA>]`, then `generatetoaddress [100, <sender p2pkh>]`
6. `getblockchaininfo` (valuePools before)
7. `z_sendmany [<sender orchard UA>, [{address: <recipient UA>, amount: 0.5, memo: <hex>}], 1, null, "FullPrivacy"]`, then poll `z_getoperationresult [[opid]]`
8. `generate [1]`, then `getrawtransaction [txid,1]`, `getrawtransaction [txid,0]`, `getblockchaininfo`, `getblock [hash,1]`, and `z_viewtransaction [txid]`
9. Checks: no transparent legs, the recipient's memo matches the canary, and the canary is not in the public JSON.

The historical run used Z3's disposable regtest RPC credentials; the literal value is intentionally omitted. The current harness accepts Zebra and Zallet passwords only through environment variables. Wallet seed material remains inside the isolated Zallet Docker volume; this historical run did not perform a comprehensive secret scan.

## Rerun results (2026-10-13, Lagos)
| Run | Starting state | Result | Wall time |
|---|---|---|---|
| 1 | fresh `git clone` of the local repo into `/tmp/zc1`; images cached; script resets the regtest volumes | GATE1 PASS | 1m48s |
| 2 | all containers removed + `docker volume prune -af` (no volumes left), fresh clone into `/tmp/zc2`; images cached | GATE1 PASS | 1m27s |

Images were already pulled for both runs. A cold image pull adds download time that was not measured.

## Fixtures (`fixtures/spike/`, from run 2)
`rawtx_hex.json` (getrawtransaction verbosity 0), `rawtx.json` (verbose), `block.json` (getblock 1),
`chaininfo_before.json` / `chaininfo_after.json` (valuePools before and after the payment block), `viewtx.json` (recipient wallet view),
`receivers_*.json`, `op.json`.
Pool change from before to after, block 103 to 104: transparent +625025000 (coinbase subsidy + fee), ironwood −25000 (fee leaves the shielded pool), all others 0.
