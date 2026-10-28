#!/usr/bin/env python3
"""Build the static demo page (docs/demo/index.html) from real ZCanary JSON reports.

usage: scripts/build_demo_page.py <reports-dir> <output.html> [--run-url URL] [--commit SHA]

The page is plain HTML and CSS (no scripts, no network requests). It only reads the
sanitized reports ZCanary already wrote; it never reads logs, wallet data or credentials.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

CHECK_ORDER = ["C1", "C2", "C3", "C4", "C4b", "MEMO", "C6"]
CHECK_LABEL = {
    "C1": "Network",
    "C2": "Address",
    "C3": "Public legs",
    "C4": "Pools",
    "C4b": "Pool moves",
    "MEMO": "Memo",
    "C6": "Logs",
}
CHECK_MEANING = [
    ("C1 Network", "Is everyone talking about the same network, or is the app quietly pointed somewhere else?"),
    ("C2 Address", "Does the address you pay offer a public (transparent) door as well as private ones?"),
    ("C3 Public legs", "Does the real transaction have any public inputs or outputs?"),
    ("C4 / C4b Pools", "Which shielded pools did the money use, and did value cross between them where the amount becomes visible?"),
    ("MEMO Memo", "Could the receiving wallet actually read the secret note we attached?"),
    ("C6 Logs", "Did our secret markers show up anywhere in the logs or files the app wrote?"),
]

# Order, plain-language story and what to look for, per sample app.
APPS = [
    {
        "id": "clean-shop",
        "title": "clean-shop",
        "story": "A shop that does everything right: pays the private side of the address, attaches the note, and keeps its logs clean.",
        "expected": [],
        "found": "All seven checks pass. The money moved through one shielded pool, the transaction has no public legs, the recipient could read the memo, and none of our secret markers appeared in the logs.",
    },
    {
        "id": "transparent-fallback",
        "title": "transparent-fallback",
        "story": "The address has a public door and a private one. This app walks through the public one.",
        "expected": ["C2", "C3", "C4b", "MEMO"],
        "found": "ZCanary decoded the real transaction and found a public output of 0.5 test ZEC. Anyone can see that payment. The memo never reached the recipient, because a public output cannot carry one.",
    },
    {
        "id": "wrong-network",
        "title": "wrong-network",
        "story": "The test expects the practice network. The app is quietly pointed at another one.",
        "expected": ["C1"],
        "found": "The app reported testnet while the test expected regtest, and its wallet's genesis hash was the testnet one. The transaction checks did not run, because they would be judging the wrong network.",
    },
    {
        "id": "log-leaker",
        "title": "log-leaker",
        "story": "The payment itself is private. But the app writes the payment's details into its own log file.",
        "expected": ["C6"],
        "found": "Every on-chain check passes, yet ZCanary found all three secret markers (memo, label and test id) in what the app logged. The report names which markers leaked, and never prints their values.",
    },
    {
        "id": "memo-dropper",
        "title": "memo-dropper",
        "story": "A private payment that loses the note: the customer pays, but the memo never arrives.",
        "expected": ["MEMO"],
        "found": "The transaction is fully shielded, but the recipient's wallet could not find the memo in what it decrypted.",
    },
    {
        "id": "cross-pool-payer",
        "title": "cross-pool-payer",
        "story": "Moves money from one shielded pool into another. Both are private, but the crossing makes the amount public.",
        "expected": ["C4", "C4b"],
        "found": "0.5 test ZEC left one shielded pool and entered another, so the amount is visible on-chain. This is the same kind of crossing ZCanary found Zallet v0.1.0-beta.1 allowing under the FullPrivacy setting.",
    },
]

CSS = """
:root{--bg:#f6f4ee;--surface:#fff;--ink:#1c1b19;--muted:#5d5a52;--line:#ddd8cb;--accent:#1f5c4a;
--pass:#1b6e3c;--pass-bg:#e3f3e8;--fail:#a4262c;--fail-bg:#fbe7e7;--skip:#6b665a;--skip-bg:#ebe8de;--code:#efece3}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#15150f;--surface:#1f1e17;--ink:#f1eee4;--muted:#b4afa0;--line:#38362b;--accent:#7fd1b0;
--pass:#7fd49a;--pass-bg:#173424;--fail:#ff9a9a;--fail-bg:#3b1b1b;--skip:#b4afa0;--skip-bg:#2b2a21;--code:#2a291f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:980px;margin:0 auto;padding:0 16px 64px}
header{padding:48px 0 24px}
h1{font-size:clamp(2rem,5vw,3rem);line-height:1.1;margin:0 0 12px;letter-spacing:-.02em}
.lead{font-size:1.2rem;color:var(--muted);max-width:42em;margin:0 0 8px}
.eyebrow{font-weight:600;color:var(--accent);letter-spacing:.06em;text-transform:uppercase;font-size:.8rem;margin:0 0 8px}
.steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:24px 0;padding:0;list-style:none;counter-reset:s}
.steps li{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px 16px;counter-increment:s}
.steps li::before{content:counter(s);display:inline-grid;place-items:center;width:1.6em;height:1.6em;border-radius:50%;background:var(--accent);color:var(--bg);font-weight:700;font-size:.85rem;margin-right:8px}
.strip{display:flex;flex-wrap:wrap;gap:8px 20px;align-items:center;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:0 0 28px}
.strip b{font-size:1.1rem}.strip a{color:var(--accent)}
h2{font-size:1.5rem;margin:40px 0 12px;letter-spacing:-.01em}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr));gap:16px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px 18px 14px}
.card h3{margin:0;font-size:1.15rem;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.top{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:6px}
.badge{font-weight:700;font-size:.8rem;border-radius:999px;padding:3px 10px;white-space:nowrap}
.badge.pass{color:var(--pass);background:var(--pass-bg)}.badge.fail{color:var(--fail);background:var(--fail-bg)}
.story{margin:6px 0 12px;color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 12px;padding:0;list-style:none}
.chip{font-size:.78rem;font-weight:600;border-radius:8px;padding:3px 8px;border:1px solid transparent}
.chip.pass{color:var(--pass);background:var(--pass-bg)}.chip.fail{color:var(--fail);background:var(--fail-bg)}.chip.not_run{color:var(--skip);background:var(--skip-bg)}
.found{margin:0 0 10px}.found strong{display:block;font-size:.8rem;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);margin-bottom:2px}
.ok{font-size:.9rem;color:var(--pass);margin:0 0 8px}
details{border-top:1px solid var(--line);padding-top:8px;margin-top:6px}
summary{cursor:pointer;color:var(--muted);font-size:.9rem}
.raw{margin:8px 0 0;padding:0;list-style:none;font-size:.85rem}
.raw li{padding:4px 0;border-bottom:1px dashed var(--line)}
.raw code{background:var(--code);padding:1px 6px;border-radius:6px;font-size:.8rem}
dl.meaning{display:grid;grid-template-columns:max-content 1fr;gap:6px 16px;margin:0}
dl.meaning dt{font-weight:600}dl.meaning dd{margin:0;color:var(--muted)}
@media (max-width:560px){dl.meaning{grid-template-columns:1fr}dl.meaning dt{margin-top:8px}}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px}
.panel ul{margin:8px 0 0;padding-left:1.2em}.panel li{margin:4px 0}
footer{margin-top:40px;color:var(--muted);font-size:.9rem}footer a,.panel a{color:var(--accent)}
a:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
"""


def esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def load_reports(directory: Path) -> dict[str, dict]:
    reports: dict[str, dict] = {}
    for app in APPS:
        path = directory / f"{app['id']}.json"
        if not path.is_file():
            raise SystemExit(f"missing report: {path}")
        reports[app["id"]] = json.loads(path.read_text(encoding="utf-8"))
    return reports


def check_map(report: dict) -> dict[str, dict]:
    return {c["id"]: c for c in report["checks"]}


def render_card(app: dict, report: dict) -> str:
    checks = check_map(report)
    failed = [c for c in CHECK_ORDER if checks.get(c, {}).get("status") == "fail"]
    passed_overall = report["overall"] == "pass"
    matches = sorted(failed) == sorted(app["expected"])
    badge = '<span class="badge pass">PASS</span>' if passed_overall else '<span class="badge fail">CAUGHT</span>'
    chips = "".join(
        f'<li class="chip {esc(checks[c]["status"])}">{esc(c)} {esc(CHECK_LABEL[c])}: {esc(checks[c]["status"].replace("_", " ").upper())}</li>'
        for c in CHECK_ORDER
        if c in checks
    )
    if app["expected"]:
        expect_text = "Designed to fail: " + ", ".join(app["expected"]) + ". ZCanary failed exactly these" if matches else "Designed to fail: " + ", ".join(app["expected"])
    else:
        expect_text = "Designed to pass everything. It did" if matches else "Designed to pass everything"
    raw = "".join(
        f'<li><code>{esc(c)}</code> {esc(checks[c]["message"])}</li>' for c in CHECK_ORDER if c in checks
    )
    return f"""
<article class="card" id="{esc(app['id'])}">
  <div class="top"><h3>{esc(app['title'])}</h3>{badge}</div>
  <p class="story">{esc(app['story'])}</p>
  <ul class="chips" aria-label="Check results">{chips}</ul>
  <p class="found"><strong>What ZCanary found</strong>{esc(app['found'])}</p>
  <p class="ok">{'&#10003; ' if matches else ''}{esc(expect_text)}.</p>
  <details><summary>Exact report lines</summary><ul class="raw">{raw}</ul>
  <p class="story">Report files: <a href="reports/{esc(app['id'])}.md">Markdown</a>, <a href="reports/{esc(app['id'])}.json">JSON</a>.</p></details>
</article>"""


def build(reports_dir: Path, run_url: str, commit: str) -> str:
    reports = load_reports(reports_dir)
    cards = "".join(render_card(app, reports[app["id"]]) for app in APPS)
    caught = sum(1 for app in APPS if reports[app["id"]]["overall"] != "pass")
    passed = len(APPS) - caught
    meaning = "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in CHECK_MEANING)
    not_covered = "".join(f"<li>{esc(item)}</li>" for item in reports["clean-shop"]["not_covered"])
    run_link = f'<a href="{esc(run_url)}">CI run (commit {esc(commit)})</a>' if run_url else "CI run"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ZCanary demo</title>
<meta name="description" content="Real ZCanary reports for six sample Zcash apps: one clean, five broken on purpose.">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header>
  <p class="eyebrow">ZCanary &middot; privacy smoke test for Zcash apps</p>
  <h1>Your app says &ldquo;private payment.&rdquo; Did it really do that?</h1>
  <p class="lead">ZCanary checks the receipt, not the sign. It builds a throwaway Zcash network, makes your app pay with a secret marker attached, and inspects what actually happened.</p>
  <ol class="steps">
    <li><b>Start</b> a practice network on your machine. No real money, seeds or keys.</li>
    <li><b>Pay</b> through your app, with a secret marker in the payment.</li>
    <li><b>Inspect</b> the real transaction, the receiving wallet and your app's logs.</li>
    <li><b>Report</b> what passed, what failed, and what was never tested.</li>
  </ol>
  <div class="strip">
    <span><b>{len(APPS)}</b> sample apps</span>
    <span><b>{passed}</b> clean, <b>{caught}</b> caught</span>
    <span>Real results from the {run_link}</span>
  </div>
</header>

<h2>Six apps, six verdicts</h2>
<p class="lead" style="font-size:1rem">One app is correct. Five are broken on purpose, each in one specific way. ZCanary has to fail exactly the right checks for each one, and the project&rsquo;s test suite requires it.</p>
<div class="grid">{cards}
</div>

<h2>What the checks mean</h2>
<div class="panel"><dl class="meaning">{meaning}</dl></div>

<h2>A real find</h2>
<div class="panel">
  <p>While building ZCanary, the pool check caught something real. Zallet <code>v0.1.0-beta.1</code> let a payment sent with the <code>FullPrivacy</code> setting move value from one shielded pool into another, which made the amount publicly visible. It reproduced on three separate test chains. The source and release notes suggest <code>v0.1.0-beta.3</code> fixes it, but that was not run here.</p>
  <p><a href="https://github.com/unborn7g/zcanary/blob/main/docs/finding-fullprivacy.md">Read the evidence and caveats</a></p>
</div>

<h2>What ZCanary does not test</h2>
<div class="panel">
  <p>A green report is a smoke test, not an audit. Every report ends with this list.</p>
  <ul>{not_covered}</ul>
</div>

<footer>
  <p>Open source under the MIT license. <a href="https://github.com/unborn7g/zcanary">Source code and README</a>. Regtest only: nothing here touches mainnet, public testnet, real keys or real funds.</p>
  <p>This page was generated by <code>scripts/build_demo_page.py</code> from the sanitized reports in <code>docs/demo/reports/</code>.</p>
</footer>
</div>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--run-url", default="")
    parser.add_argument("--commit", default="")
    args = parser.parse_args(argv)
    page = build(args.reports_dir, args.run_url, args.commit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(page, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
