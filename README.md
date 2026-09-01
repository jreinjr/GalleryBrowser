# Gallery Browser

A personal hobby iOS app in the mold of the See Saw gallery guide, plus the
agent pipeline that populates it. Adds **Seattle** as the default city alongside
New York, Los Angeles, Berlin, London, Paris, and Venice.

## Layout

```
GalleryBrowser.xcodeproj   Xcode project (iOS 17+, SwiftUI)
GalleryBrowser/            App source
content/                   Scraped data bundled into the app (folder reference)
  <city>.json              PUBLISHED (verified) show records per city
  pending/<city>.json      Scraped shows awaiting verification (never displayed)
  images/<city>/<slug>/    Downloaded high-res show imagery (JPEG)
  spend/                   Per-session API cost ledgers + TOTAL.json
scraper/                   Claude-powered research agent + harness
docs/DESIGN.md             Observed design spec the app follows
.env                       ANTHROPIC_API_KEY (private)
```

## Running the app

Requires Xcode 16+ (not installed on this machine at the time of writing).
Open `GalleryBrowser.xcodeproj`, pick an iPhone simulator, Run. The `content/`
folder ships inside the app bundle, so re-running the scraper and rebuilding
refreshes the in-app data.

## Running the scraper agent

The one-command pipeline scrapes, verifies, rebuilds the web demo, and
deploys — safe to run unattended because only verified shows are published:

```
scraper/.venv/bin/python scraper/pipeline.py --city seattle --target 9
scraper/.venv/bin/python scraper/pipeline.py --all-secondary        # other cities
scraper/.venv/bin/python scraper/pipeline.py --campaign --city los-angeles --max-shows 50
scraper/.venv/bin/python scraper/pipeline.py --verify-only          # promote pending + ship
scraper/.venv/bin/python scraper/pipeline.py --verify-only --full   # re-audit everything
```

Add `--no-deploy` / `--no-build` to stop earlier. The underlying stages remain
runnable on their own (`run_scrape.py`, `run_campaign.py`, `run_verify.py`,
`webdemo/build.py`); a bare `run_scrape.py`/`run_campaign.py` auto-verifies
what it scraped unless passed `--no-verify`. `run_scrape.py --report` prints
the spend ledger.

New shows land in `content/pending/<city>.json` and are promoted to the
published `content/<city>.json` only when a fact-check agent verifies them
against the venue's own site AND their pin resolves deterministically
(Google Geocoding cross-checked against the venue's Places listing — the
LLM never supplies coordinates). Unverified shows are demoted back to
pending, never deleted.

The agent (Claude Sonnet 5) drives Anthropic server-side web search/fetch plus
local tools that extract candidate image URLs from pages, download and
normalize high-res images, and validate + persist show records. Descriptions
are written by the agent in its own words from its research; venue facts
(addresses, dates, hours) are taken from the venues' own sites. Every API
response is metered; see `content/spend/TOTAL.json`.
