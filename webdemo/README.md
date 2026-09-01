# Web demo

A faithful web port of the Gallery Browser iOS app, deployed as a static site on Vercel.

**Production URL:** https://gallery-browser-demo.vercel.app

## Build & deploy

```bash
scraper/.venv/bin/python webdemo/build.py          # -> webdemo/dist/gallery-browser-demo/
cd webdemo/dist/gallery-browser-demo && vercel deploy --prod --yes
```

Normally you don't run these by hand: `scraper/pipeline.py` chains
scrape -> verify -> build -> deploy in one command (see the root README).
Only published (verified) shows in `content/<city>.json` are built;
`content/pending/` is ignored. Same URL on every deploy. Image
recompression and map tiles are cached in `webdemo/.cache/`, so rebuilds are fast.

Knobs: `--img-cap 7` (images per show), `--max-width 1080`, `--quality 78` (WebP),
`--full-side 3840`, `--full-quality 80` (full-res variants; `--full-side 0` disables).

Images ship in two tiers: every surface first loads the 1080px proxy, and when the
source out-resolves it (≥1.25×) an `@full.webp` variant (longest side ≤ 3840) is
emitted alongside; `app.js` swaps it in when a card pinch starts or the full-screen
viewer shows a slide (current ± 1). Full-res bytes are only fetched on zoom.

## Layout

- `build.py` — orchestrator: reads `content/`, recompresses images to WebP, stitches a
  CARTO dark basemap per city (venue-page map card), writes `data.js` + static files.
- `cityconfig.py` — imports the scraper's `CITIES`; cities appear automatically once
  `content/<key>.json` exists.
- `images.py` / `mapgen.py` — image + basemap pipelines (cached).
- `template.html`, `styles.css`, `icons.js`, `app.js`, `map_maplibre.js` — the app.
  Vanilla JS; the Map tab uses MapLibre GL + CARTO dark style; state persists in
  `localStorage` (`selectedCityKey`, `savedShowIDs`, same semantics as the iOS app).

## Local test

```bash
python3 -m http.server -d webdemo/dist/gallery-browser-demo 8000
# then open http://localhost:8000 (or http://<mac-ip>:8000 on an iPhone)
```

## Gesture tests

```bash
scraper/.venv/bin/python webdemo/build.py    # tests run against a fresh dist
NODE_PATH=/opt/homebrew/lib/node_modules node webdemo/tests/gestures.test.js
```

Raw-CDP multitouch choreography (feed pinch/pan lifecycle, viewer touch
gestures, edge-swipe coexistence, desktop wheel/drag pan) in headless Chrome
via the globally installed Playwright (`channel: 'chrome'`, no browser
download). CDP can't reproduce iOS's gesture arbitration or batched touch
events, so a green run is necessary but not sufficient — finish with real
pinches on an iPhone.
