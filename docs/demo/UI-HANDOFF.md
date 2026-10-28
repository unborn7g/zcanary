# ZCanary demo page: UI handoff

Everything another tool needs to rebuild, view and record the ZCanary demo page in its own sandbox.

- Repo (public): https://github.com/unborn7g/zcanary (branch `main`)
- Live page: https://unborn7g.github.io/zcanary/demo/
- Green CI run: https://github.com/unborn7g/zcanary/actions/runs/40477088823

## 1. Get it running (no build tools needed)

```bash
git clone https://github.com/unborn7g/zcanary
cd zcanary/docs/demo
python3 -m http.server 8000      # then open http://localhost:8000/
```

`docs/demo/index.html` is one static file: HTML, inline CSS, and about 15 lines of inline JS (the theme switch). The only external request is Google Fonts. The report files it links to are in `docs/demo/reports/` (six `.json` and six `.md`).

## 2. Regenerate the page from the reports (optional)

```bash
python3 scripts/build_demo_page.py docs/demo/reports docs/demo/index.html \
  --run-url https://github.com/unborn7g/zcanary/actions/runs/40477088823 --commit e4f6347
```

Standard library only. All styling and copy live in `scripts/build_demo_page.py` (constants `CSS`, `APPS`, `CHECK_MEANING`, `STEPS`). Edit that file, never the generated HTML.

## 3. Design system (summary)

Same system as Prorata: obsidian green-black background, one mint accent, glass panels, heavy display type.

| Token | Dark | Light |
|---|---|---|
| `--bg` | #040B08 | #F2F7F4 |
| `--ink` | #F0FDF6 | #071710 |
| `--muted` | #B5D1C4 | #334B40 |
| `--mint` / `--mint-2` | #2AF598 / #10D47E | #12B86E / #0D9F5E |
| `--amber` (leaks) | #FF7849 | #D9481E |

Fonts: Bricolage Grotesque 800 for headings, Instrument Sans for body, IBM Plex Mono for report lines. Default theme is dark and the choice is stored in `localStorage` under `zcanary-theme`. Rules: mint marks the thing to look at; amber marks a caught leak; cards are 24px radius with a 1px green hairline; one mint button per region; motion is disabled under `prefers-reduced-motion`.

## 4. Page map (anchors and selectors)

| Order | Section | Selector |
|---|---|---|
| 1 | Header, theme switch | `.hdr`, `.theme` (`.theme-opt[data-mode=light]`, `[data-mode=dark]`) |
| 2 | Hero, headline, arc | `.hero`, `h1`, `.arc` |
| 3 | Stats strip (6 apps, 1 clean, 5 caught, 7 checks) | `.stats` |
| 4 | Verdict cards | `#verdicts`, then `#clean-shop`, `#transparent-fallback`, `#wrong-network`, `#log-leaker`, `#memo-dropper`, `#cross-pool-payer` |
| 5 | Check questions (6 feature cards) | `.features` |
| 6 | Four steps | `.steps` |
| 7 | A real find (Zallet finding) | the `.panel` after the steps |
| 8 | What it does not test | the last `.panel` before the closing band |
| 9 | Closing band | `.cta` |

Each verdict card has a `<details>` ("Exact report lines"). Expand it with `document.querySelector('#transparent-fallback details').open = true`.

## 5. Recording the demo (Playwright example)

```js
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1280, height: 720 },
    recordVideo: { dir: 'video', size: { width: 1280, height: 720 } } });
  const p = await ctx.newPage();
  await p.goto('http://localhost:8000/');
  await p.waitForTimeout(8000);
  for (const id of ['verdicts', 'transparent-fallback', 'log-leaker', 'clean-shop']) {
    await p.evaluate(i => {
      const el = document.getElementById(i);
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      const d = el.querySelector('details'); if (d) d.open = true;
    }, id);
    await p.waitForTimeout(9000);
  }
  await ctx.close(); await b.close();
})();
```

Scene order and narration are in the demo-video script. Keep the page in dark mode. Show nothing from any account.

## 6. Facts the narration must not overstate

- Regtest only. Nothing touches mainnet, public testnet, real keys or funds.
- A green report is a smoke test, not an audit; every report lists what is not covered.
- The Zallet beta.3 fix is supported by source and release notes only and was not run.
- Reports show marker labels and counts, never the secret values.
