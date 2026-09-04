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
recompression is cached in `webdemo/.cache/`, so rebuilds are fast.

Knobs: `--img-cap 7` (images per show), `--max-width 1080`, `--quality 78` (WebP),
`--full-side 3840`, `--full-quality 80` (full-res variants; `--full-side 0` disables).

Images ship in two tiers: every surface first loads the 1080px proxy, and when the
source out-resolves it (≥1.25×) an `@full.webp` variant (longest side ≤ 3840) is
emitted alongside; `app.js` swaps it in when a card pinch starts or the full-screen
viewer shows a slide (current ± 1). Full-res bytes are only fetched on zoom.

## Layout

- `build.py` — orchestrator: reads `content/`, recompresses images to WebP,
  writes `data.js` + static files.
- `cityconfig.py` — imports the scraper's `CITIES`; cities appear automatically once
  `content/<key>.json` exists.
- `images.py` — image pipeline (cached).
- `template.html`, `styles.css`, `icons.js`, `app.js`, `map_maplibre.js` — the app.
  Vanilla JS; the Map tab and the venue-page map card both use MapLibre GL +
  CARTO's dark-matter vector style; state persists in
  `localStorage` (`selectedCityKey`, `savedShowIDs`, `filter`; same semantics as the iOS app).

## Filters, rank glyphs, map

- **One filter state for all three tabs.** Featured (cards), List (rows) and
  Map (pins) render the same filtered set. Each tab has a sliders button in the
  top right (a badge counts the groups off their defaults); it opens the
  Filters sheet: search (title / artist / venue), Show switches (Active shows —
  running today, on by default, and nullable dates count as running; Saved
  only; Upcoming receptions — the reception line is free text, parsed to a date
  and kept only from today on), three 3-way pickers — **Show Rank** (All Shows /
  Featured / Editor's Picks), **Gallery Rank** (All Galleries / Notable / Top
  Ranked), **Venue Type** (All venues / Galleries / Museums) — Neighborhoods
  (multi-select chips) and Sort (Ranking, Closing soon, Recently opened,
  Reception soon, Venue A–Z, Gallery rank, Nearby). Sort only orders the List,
  so the Map's own filter button opens the sheet without it. Every
  control applies live; the footer's "Show N shows" just closes. Defaults:
  Galleries + Featured + Active; Clear returns to them. The state persists in
  `localStorage` (`filter`, versioned — an older shape falls back to the
  defaults). Ranking comes from the last `curate.py apply`
  (`content/curation/<city>/curated.json` → `ranked`), falling back to file order;
  `build.py` also embeds each venue's registry `kind`, `rank` and `tier`.
  "Galleries" means every non-museum venue (nonprofits and project spaces included).
- **Rank star.** Only the top tier is marked, and only where it is the subject.
  A filled blue star means Editor's Pick for a show and Top Gallery for a
  gallery; Featured and Notable get no mark. List rows star the show name;
  Featured cards carry no star. The show detail stars the show in its header,
  with the words "Editor's Pick"; the gallery named below it carries no mark.
  The gallery page carries its own "Top Gallery" pill under the title, and its
  show rows stay plain. Gallery tiers derive from the registry rank (`rank_venues.py`,
  city-wide): Top = rank ≤ 25, Notable = rank ≤ 100, otherwise Listed
  (`GALLERY_TIER_CUTOFF` in `app.js`); Notable still drives the Gallery Rank
  filter even though it draws nothing.
- **Map** is galleries: one unclustered dot per venue of the filtered set,
  coloured and sized by tier (Listed dots are small, grey and unlabelled; a
  legend sits bottom-left). Name labels are a symbol layer, so MapLibre's
  collision engine keeps them from overlapping (a label is hidden before the
  dot is) and the better tier wins the slot. Tapping any dot opens the venue
  page.
- **Featured cards** carry a white bookmark button in the footer, the same
  control as the list rows.
- **Venue page** leads with Shows, then the blurb, then the address lines. Every
  show the venue is running deals into ONE card — the Featured card in miniature
  (photo, frosted footer, bookmark), 248px tall against the Featured card's full
  width. Sheets peeking out below say how deep the deck is (two at most, however
  many shows there are); swiping the footer left or right deals the next show and
  wraps, the counter beside the bookmark says where you are, and a tap on the card
  or its text opens whichever show is face up. The photo strip above the footer
  keeps its own image carousel. The venue is the subject of the page, so the
  footer's second line carries the run dates rather than repeating the venue name.
- The **venue map card** is a live MapLibre map (same style as the Map tab), not a
  crop of a pre-stitched raster basemap: CARTO's raster tile CDN now requires an
  API key and stamps "API KEY REQUIRED" across every tile. Vector tiles stay crisp
  at any zoom and carry their own attribution. `cooperativeGestures` keeps a
  one-finger drag scrolling the page, so panning takes two fingers (or ⌘/ctrl +
  scroll to zoom); the +/− control covers the gated wheel zoom.

## Local test

```bash
python3 -m http.server -d webdemo/dist/gallery-browser-demo 8000
# then open http://localhost:8000 (or http://<mac-ip>:8000 on an iPhone)
```

## Tests

```bash
scraper/.venv/bin/python webdemo/build.py    # tests run against a fresh dist
NODE_PATH=/opt/homebrew/lib/node_modules node webdemo/tests/gestures.test.js
```

`list.test.js` (filter sheet, shared state, glyphs, card bookmark, sort),
`venue.test.js` (gallery rank sort, venue page) and `map.test.js` (tier dots,
label collision, taps, filter sheet) run the same way; set `DIST=<dir>` to test a build made
with `build.py --out <dir>`.

Raw-CDP multitouch choreography (feed pinch/pan lifecycle, viewer touch
gestures, edge-swipe coexistence, desktop wheel/drag pan) in headless Chrome
via the globally installed Playwright (`channel: 'chrome'`, no browser
download). CDP can't reproduce iOS's gesture arbitration or batched touch
events, so a green run is necessary but not sufficient — finish with real
pinches on an iPhone.
