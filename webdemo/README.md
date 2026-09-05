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
  writes `data.js` + static files, and emits the Discover function bundle
  (`api/`, `package.json`, `vercel.json`; see below).
- `cityconfig.py` — imports the scraper's `CITIES`; cities appear automatically once
  `content/<key>.json` exists. Also loads `content/lists/<city>.json` (curated
  lists) and holds `CITY_TZ`.
- `discover_corpus.py` — the per-city corpus the Discover function reads
  (`api/_data/<city>.json`, gitignored): shows with a parsed reception date and
  kind, venues with hours by weekday (Google `weekday_text` first, the agent's
  lines second, via `scraper/hours.py`), roster and program focus from gallery
  reports, and the artists dataset trimmed to relations and history.
- `images.py` — image pipeline (cached).
- `template.html`, `styles.css`, `icons.js`, `app.js`, `map_maplibre.js` — the app.
  Vanilla JS; the Map tab and the venue-page map card both use MapLibre GL +
  CARTO's dark-matter vector style; state persists in
  `localStorage` (`selectedCityKey`, `savedShowIDs`, `favoriteVenueIDs`,
  `galleryOrder`, `filter`, `lists`; same semantics as the iOS app where it
  has them).
- `api/discover.js` + `api/_lib/` — the Discover endpoint (a Vercel Function,
  ESM; `api/package.json` scopes the module type and the SDK dependency).
- `devserver.mjs` — local server that serves `dist` and mounts the function.

## Lists

The Lists tab is a library (design lab frame L1a): the pinned **All shows in
<City>** row is today's filtered list; **My Shows** appears once a running show is
bookmarked; **Favorite galleries** is always pinned (the shows on view at the
galleries the person follows; empty, it leads to the ranking page); **Your
lists** appears once the person has a list; a **Curated** shelf (Top 20
galleries, Arts District walk, Openings this weekend, Video art now) with
**See all** behind it. Top 20 galleries and Openings this weekend are rules
over the data; the others are authored in `content/lists/<city>.json`
(`{id, name, desc, kind: list|route, entries: [{slug, note}]}`) and built into
`data.js` as `lists[city]`. A gear beside the Lists tab's filter button opens
**Settings** (see "Galleries" below).

- A list detail page (L3a) shows a collage, the byline, **Map** and **Edit**
  (owned) or **Save** (curated and Discover answers). Each row has a caret that
  expands the entry's note (curator's or Discover's why-line, else the show's
  first sentence). Rows of shows that have closed are greyed with a "closed" pill.
- **A list hides once none of its shows is still running** (library, shelf,
  filter picker); it is not deleted. An empty list of the person's own stays
  visible so it can be filled from Edit.
- Edit (L3d): name, description, cover, drag to reorder, remove, Add shows, Delete.
- Bookmarking a show shows a toast with **Add to list…** (L4); the show detail
  has the same link under the capsule.
- The filter sheet's **List** group (L5a) replaced the Saved-only switch: All
  shows, My Shows, Favorite galleries (once a gallery is followed), the
  person's lists. The chosen list is the context on every
  tab: a bar under the Featured title, a pill on the Map (its venues highlighted
  over the dimmed filter set; a route list also draws a dashed line through its
  stops in order), and it restricts the flat list. User lists live in
  `localStorage['lists']` (versioned); My Shows stays `savedShowIDs`, Favorite
  galleries is derived from `favoriteVenueIDs`.

## Discover

A fourth tab: a chat over the city's data, answered by `POST /api/discover`.
Every answer that is a set of shows arrives as a **list** (an unsaved draft the
person can open, save, or put on the Map); an itinerary is a list of kind
`route` with the times only in the chat card, never in the list. The idle
state offers four prompts; suggestion chips appear only when the model returns
them. The transcript lives per city in `sessionStorage`; the server is stateless
and receives a compact history (assistant text plus the ids it presented).

The function (`api/discover.js`, `api/_lib/agent.js`) runs a manual streaming
loop on `claude-opus-5` (adaptive thinking, effort `medium`, `fallbacks:
"default"`) with five custom tools over the in-memory corpus — `find_shows`
(filter + BM25 search with concept expansion and an honest relaxation ladder),
`find_venues`, `get_details`, `plan_route` (greedy walking route checked
against hours by weekday) and the strict terminal tool `present_list` — plus
the server-side `web_search` / `web_fetch`. The system prompt (app description,
data provenance, output contract) ends with a compact catalog of the city's
running and upcoming shows and is cached (5-minute TTL by default,
`DISCOVER_CACHE_TTL=1h` to switch); the date, filter, list context and saved
lists go in a per-request system message after the user turn. Responses stream
as server-sent events (`status`, `text`, `list`, `route`, `note`, `done`,
`error`); ids are validated against the corpus before a list is emitted.

Guards: same-origin check (Origin/Referer host), body limits, a per-IP token
bucket and a per-instance daily fuse (both reset on cold start), a loop cap,
`max_tokens`, tool `max_uses`, `maxDuration` 120 s. **The real spend guard is
the key**: use a dedicated API key in a workspace with a monthly spend limit,
set as `ANTHROPIC_API_KEY` on the Vercel project (never in git or the bundle).
Typical cost is $0.04–0.15 per question with a warm cache. Env knobs:
`DISCOVER_MODEL`, `DISCOVER_EFFORT`, `DISCOVER_CACHE_TTL`, `DISCOVER_DEBUG=1`
(error details in the stream), `DISCOVER_EVAL=1` (honors an `x-discover-today`
header), `DISCOVER_ALLOW_NO_ORIGIN=1` (curl and the eval).

## Galleries: the rank, the tiers, favorites, settings

**The gallery rank is the app's only ranking signal** (2026-09-04). Shows are
not ranked: `build.py` publishes no `rank`, `featured` or `editors_pick` on a
show, and nothing in `app.js` or the Discover function reads one. Everything
"best first" is by the venue's city-wide rank from `rank_venues.py`
(`content/venues/<city>.json` → `rank`): the Featured feed (one card per
gallery, best-ranked first), the List's default sort, the map dots, the tiers.

- **Tiers** derive from the rank: Top = the 20 best, Notable = the 50 best,
  Listed = the rest or unranked (`GALLERY_TIER_CUTOFF` in `app.js`, mirrored in
  `discover_corpus.py` and `api/_lib/corpus.js`). This matches the market-order
  file's default cutoffs (`docs/GALLERIES.md`, `tiers: {"1": 20, "2": 50}`).
- **Venue records** ship for every vouched-for venue of a city, shows or not:
  `data.js` `venues[city][venueId]` = id, name, kind, neighborhood, address,
  hours, phone, website, coordinates, blurb, rank, tier (`cityconfig.mappable`:
  pinned, not closed/duplicate/out-of-scope, not flagged, and verified, status
  active or in a tier; venues with a published show always ship). Keyed per
  city because registry ids repeat across cities.
- **Personal ranking.** Settings (the gear on the Lists tab) → **Gallery
  ranking** lists every venue of the city in the order the app uses, with its
  number, its tier dot and a heart; a search field finds one. **Edit** turns on
  drag-to-reorder (the list autoscrolls near its edges) and "tap the number to
  move it to a rank"; **Save** stores the order per city in
  `localStorage['galleryOrder']` (versioned), and from then on it *is* the rank:
  the feed, the list, the map dots, the tiers, the Top 20 / Top 50 filter and
  the gallery page's pill all follow it. Venues the payload no longer has drop
  out; new ones join at the end in app order. **Reset to app ranking** discards
  it. The Map's legend carries a "Rank galleries" link that opens the same page
  as a sheet. The Discover function keeps using the app's ranking.
- **Favorites.** A heart on a gallery page (also the sheet a map dot opens) or
  on a ranking row follows the gallery (`localStorage['favoriteVenueIDs']`,
  `"<city>/<venueId>"`). **Favorite galleries** — the shows on view at those
  galleries, best-ranked first — is a pinned default list in the library, an
  option in the filter sheet's List group (so it can be the context on
  Featured, the List and the Map) and a row in Settings.
- **Glyphs.** Only the top tier is marked, and only the gallery is ever rated:
  a filled blue star leads the gallery's name on a show row and in the show
  detail's venue block; Featured cards stay plain. The gallery page carries a
  pill under its title — "#4 · Top 20", "#33 · Top 50", "#120" — with "· your
  ranking" once a personal order is saved.

## Filters and the map

- **One filter state for all three tabs.** Featured (cards), List (rows) and
  Map (pins) render the same filtered set. Each tab has a sliders button in the
  top right (a badge counts the groups off their defaults); it opens the
  Filters sheet: search (title / artist / venue), Show switches (Active shows —
  running today, on by default, and nullable dates count as running; Upcoming
  receptions — the reception line is free text, parsed to a date and kept only
  from today on), two 3-way pickers — **Gallery rank** (All / Top 50 / Top 20),
  **Venue Type** (All venues / Galleries / Museums) — Neighborhoods
  (multi-select chips) and Sort (Gallery rank, Closing soon, Recently opened,
  Reception soon, Venue A–Z, Nearby). Sort only orders the List, so the Map's
  own filter button opens the sheet without it. Every control applies live;
  the footer's "Show N shows" just closes. Defaults: Galleries + Active, gallery
  rank order; Clear returns to them. The state persists in `localStorage`
  (`filter`, versioned — an older shape falls back to the defaults; v7 dropped
  the Show Rank group).
  The Venue Type filter is strict (2026-09-03): "Galleries" is the `gallery` kind
  only and "Museums" the `museum` kind only; nonprofits, university galleries,
  project spaces and other venues appear under "All venues" alone. The Discover
  tools' `venue_kind` / `kind` follow the same rule (`any` reaches everything).
- **Map** is galleries: one unclustered dot per venue, coloured and sized by
  the gallery's tier (Listed dots are small, grey and unlabelled; a legend sits
  bottom-left). With **Active shows on** (the default) the dots are the venues
  of the filtered shows. With it **off**, every gallery of the city the other
  filters admit is on the map too, and a venue with nothing on view is drawn
  semi-transparent (dot and label; the legend gains a "Nothing on view" row).
  Upcoming receptions or a list as context narrow back to shows. Name labels
  are a symbol layer, so MapLibre's collision engine keeps them from
  overlapping (a label is hidden before the dot is); the better tier wins the
  slot, then an active venue over a faded one, then the rank. Tapping any dot
  opens the venue page — a gallery with nothing on view gets its full page
  ("Nothing on view right now.", blurb, address, hours, map card, the heart).
- **Featured cards** carry a white bookmark button in the footer, the same
  control as the list rows.
- **Featured is one card per venue, not per show** (`showDeck` in `app.js`). A
  gallery running several shows at once deals them through a single card instead
  of repeating itself down the feed: sheets peek out below to say how deep the
  deck is (two at most, however many shows), swiping the footer left or right
  deals the next show and wraps, a counter beside the bookmark says where you
  are, and a tap opens whichever show is face up — the detail page still gets a
  stepper over every show in the filtered set. The photo strip keeps its own
  image carousel, and `touch-action: pan-y` on the footer means a vertical drag
  still scrolls the feed. A venue with one show is a plain card: no sheets, no
  counter, nothing to swipe.
- **Venue page** leads with Shows, then the blurb, then the address lines, and
  lists the venue's shows one card each — the same card at 248px, about two
  thirds of a Featured card. No deck here: the venue is already the subject, so
  the shows are laid out rather than stacked, and the footer's second line
  carries the run dates instead of repeating the venue name.
- Venues are identified by the registry `venueId`, not by name + coordinate:
  shows at one gallery are geocoded per show and drift a metre or two, which a
  coordinate key splits into two venues (two feed rows, two map pins). The
  venue page and the map dot use the show's embedded venue copy with the
  registry record filling any field it lacks.
- The **venue map card** is a live MapLibre map (same style as the Map tab), not a
  crop of a pre-stitched raster basemap: CARTO's raster tile CDN now requires an
  API key and stamps "API KEY REQUIRED" across every tile. Vector tiles stay crisp
  at any zoom and carry their own attribution. `cooperativeGestures` keeps a
  one-finger drag scrolling the page, so panning takes two fingers (or ⌘/ctrl +
  scroll to zoom); the +/− control covers the gated wheel zoom.

## Local test

```bash
cd webdemo/api && npm install                # once: the SDK for the function
node webdemo/devserver.mjs                   # http://localhost:8000 with /api/discover
```

The dev server reads `ANTHROPIC_API_KEY` from `.env` at the repo root (in a
worktree, from the main checkout's `.env`). `python3 -m http.server -d
webdemo/dist/gallery-browser-demo 8000` still works for everything but Discover.
From a git worktree, run `build.py` with the main checkout's interpreter and
symlink `webdemo/.cache` and `webdemo/dist` to the main checkout's copies.

## Tests

```bash
scraper/.venv/bin/python webdemo/build.py    # tests run against a fresh dist
NODE_PATH=/opt/homebrew/lib/node_modules node webdemo/tests/gestures.test.js
```

`list.test.js` (filter sheet, shared state, gallery-rank order and star, card
bookmark, sort), `lists.test.js` (library, add-to-list, edit, List filter
group, context on Featured and Map, hidden-when-ended), `discover.test.js` (the
tab against the dev server's mocked endpoint, `DISCOVER_MOCK=1` replaying
`tests/fixtures/*.sse`), `venue.test.js` (gallery rank sort, venue page, the
rank pill), `map.test.js` (tier dots, faded venues with Active shows off,
label collision, taps, the heart on a map venue, filter sheet, the ranking
sheet) and `ranking.test.js` (Settings, the ranking page, Edit / move / drag /
Save / Cancel / Reset, favorites as a list and a filter context) run the same
way; set `DIST=<dir>` to test a build made with `build.py --out <dir>`. On
Windows, `NODE_PATH` can point at any directory holding a `playwright` install
(`npm i playwright` with `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`; the tests drive
the installed Chrome via `channel: 'chrome'`). `node webdemo/tests/corpus.test.mjs` checks the
Discover corpus and tools without the API. `node webdemo/tests/discover.eval.mjs`
asks the real model twelve pinned questions (`--only 1,3`, `--effort low`,
`--model …`; about $1 on Opus 5) and checks the answers against the corpus.

## Deploy

`build.py` writes everything the function needs into `dist/` (`api/`,
`api/_data/<city>.json`, `package.json`, `vercel.json` with `maxDuration` 120);
`vercel deploy --prod --yes` from `dist/gallery-browser-demo` installs the SDK
and deploys the function with the static site. The key is a project env var:
`vercel env add ANTHROPIC_API_KEY production` (once).

Raw-CDP multitouch choreography (feed pinch/pan lifecycle, viewer touch
gestures, edge-swipe coexistence, desktop wheel/drag pan) in headless Chrome
via the globally installed Playwright (`channel: 'chrome'`, no browser
download). CDP can't reproduce iOS's gesture arbitration or batched touch
events, so a green run is necessary but not sufficient — finish with real
pinches on an iPhone.
