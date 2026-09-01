# Design spec (observed from See Saw, cloned by this app)

Documented by walking every screen of the running app. Dark theme throughout
(pure-black backgrounds, forced dark appearance), light-sky-blue accent used
for buttons, selected tab, section headers, and text buttons.

## Global chrome
- Floating pill tab bar with three tabs: **Featured** (star), **List**
  (list.bullet), **Map** (map). Selected tab tinted blue. (On iOS 26 the
  system tab bar renders as floating glass automatically.)
- "Cities" pill button at top-left of Featured/List/Map opens the
  **Select City** sheet: inline centered title, X close button, inset-grouped
  list with a blue checkmark on the current city; a city can carry an
  availability subtitle (e.g. Venice: "Available through Sunday, November 22").
- City selection is global across tabs. This clone adds **Seattle** and makes
  it the default city.

## Featured tab
- Large navigation title = city name; collapses to inline on scroll.
- Feed of full-width cards (~16pt margins, ~24pt corner radius, ~310pt tall):
  - Image carousel with small page dots at **top-left**.
  - Blurred light footer (material) with near-black text: bold display name
    (artist if present, else show title), then "Venue • Street address" in a
    dimmer line. Museum venues get a 🏛 prefix.
- Tapping a card pushes the show detail.

## Show detail
- Pushed: circular dark back-chevron button top-left; top-right a capsule with
  up/down chevrons stepping to the previous/next show of the source list
  (disabled at the ends). Presented from the map: a sheet with an X instead.
- Content: edge-to-edge carousel (dots bottom-center), artist name (regular),
  show title in *italics*, "Through <Weekday, Month D, YYYY>" (or "Opens ..."),
  full-width light-blue capsule **Add to My Shows** with bookmark icon
  (toggles), venue block (bold name, address + floor, hours lines, chevron →
  venue page), divider, long multi-paragraph description.

## Venue page
- Inline centered title = venue name; circular back button.
- Name/address/hours block, rounded non-interactive map card with the venue
  pin, then blue capsule buttons: **Directions to venue** (walking figure,
  opens Maps walking directions), **Open website**, **Call venue**.

## List tab
- Root: Cities pill, large city title, circular search button (top-right).
  - White banner slot at top (🏛 museums entry in NY; promo banner in LA —
    this clone shows the 🏛 museums banner when museum shows exist).
  - "My Shows" row with blue bookmark icon.
  - Inset group: All Current Shows + the city's neighborhoods.
  - Inset group: Opening This Week, Closing This Week, Editor's Picks, Nearby.
- Show lists: large title, plain rows — bold display name, venue line
  (🏛 prefix for museums), gray address, circular bookmark toggle at right.
- Museums screen: inline "Museums" title, blue "On View" section header.
- My Shows empty state: large bookmark glyph, "No Saved Shows Yet", caption
  pointing at Featured/Editor's Picks.
- Search: modal sheet, inline "Search" title, X, rounded field; empty state is
  a large magnifier + "Search Shows"; results under a blue "On View" header.
  Matches titles, artists, and venue names.

## Map tab
- Full-bleed map centered on the city, blue dot annotations per venue
  (museum pins labeled with 🏛 + name).
- Top-left Cities pill; top-right filter pill menu: My Shows / All Shows ✓ /
  Receptions.
- Tapping a pin presents the show detail as a sheet.

## Content model
Each show: title, optional artist, ISO start/end dates, agent-written
description (2-4 paragraphs), venue (name, museum flag, address(+detail),
neighborhood, hours lines, phone, website, lat/lon), reception info, images,
editor's-pick + featured flags, source URLs.
