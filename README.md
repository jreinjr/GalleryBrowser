# Gallery Browser

A personal hobby iOS app in the mold of the See Saw gallery guide, plus the
agent pipeline that populates it. Adds **Seattle** as the default city alongside
New York, Los Angeles, Berlin, London, Paris, and Venice.

## Layout

```
GalleryBrowser.xcodeproj   Xcode project (iOS 17+, SwiftUI)
GalleryBrowser/            App source
content/                   Scraped data bundled into the app (folder reference)
  <city>.json              Show records per city
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

```
scraper/.venv/bin/python scraper/run_scrape.py --city seattle --target 9
scraper/.venv/bin/python scraper/run_scrape.py --all-secondary   # other cities, 3 shows each
scraper/.venv/bin/python scraper/run_scrape.py --report          # spend report
```

The agent (Claude Sonnet 5) drives Anthropic server-side web search/fetch plus
local tools that extract candidate image URLs from pages, download and
normalize high-res images, and validate + persist show records. Descriptions
are written by the agent in its own words from its research; venue facts
(addresses, dates, hours) are taken from the venues' own sites. Every API
response is metered; see `content/spend/TOTAL.json`.
