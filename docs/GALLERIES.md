# Galleries as first-class objects — data contracts

Status: contracts for the galleries-first pipeline (plan of 2026-09-03). Every module
below reads/writes only through these shapes so they can be built and run independently.
Pipeline order: **G0 priors → G1 find → G2 validate → G3 research → G4 rank → G5 dashboard
→ S1 scrape shows from ranked galleries → S2 verify shows → S3 cleanup.**

## Registry (`content/venues/<city>.json`, schema 2)

`venues.empty_venue()` gained these additive blocks; `venues.load_registry` back-fills them
on schema-1 files (`venues.ensure_v2`). `merge_patch` merges them shallowly like `page`.

```
verification: {status: verified|flagged|unverified|null, ts: int|null,
               checks: {places_status, address_match, website_live, site_vouched,
                        exhibitions_page, js_only, closed_notice, ...}}   # validate_venues.py
about:        {text, source_kind: official|secondary|null, source_url, evidence_path,
               written_ts, model, hints: [str]}                            # research_venue.py
facts:        {founded_year, founders[], locations_elsewhere[], program_focus[],
               roster_count, exhibitions_total, exhibitions_per_year,
               first_exhibition_year, solo_share}                          # research_venue.py
research:     {ts, crawl_pages, triaged, report_path, cost_usd, gapfill}   # research_venue.py
kind_source:  default|places|directory|agent|show|research|manual|null      # venues.set_kind (schema 3)
kind_evidence: str|null   # the line that decided `kind`; see venues.KIND_SOURCES for precedence
features:     {<name>: {value: float 0..1, basis: str, ts: int}}           # rank_venues.py
rank, score, score_breakdown                                               # rank_venues.py apply
photos:       {status: done|none, ts, model, confidence, candidates_seen, google_events, note,
               files: [{path, provider: site|google, px, fetched_ts, subject, caption,
                        url + source_page (site) | google_photo + attribution (google)}]}
                                                                           # gallery_photos.py apply
```

`tier` / `notability` keep their meaning (tier 1-3 or null) but are now written by
`rank_venues.py apply` (venue_tiers.py is superseded).

## GalleryReport (`content/venues/reports/<city>/<venue_id>.json`)

Written by `research_venue.py`, tracked in git. Strict shape:

```
{
  "schema": 2, "city", "venue_id", "name", "website", "generated_ts", "model",
  "cost_usd", "crawl": {"pages": int, "sitemap": bool, "rendered": bool, "index_path"},
  "about_text": str|null, "source_kind": "official"|"secondary"|null, "source_urls": [str],
  "founded_year": int|null, "founders": [str], "directors": [str],
  "locations": [{"city": str, "address": str|null, "since": int|null, "current": bool}],
  "program_focus": [str], "hours_text": str|null,
  "roster": [{"name": str, "status": "represented"|"exhibited"|"estate", "source_url": str}],
  "exhibitions": [{"title": str|null, "artists": [str], "start": "YYYY-MM-DD"|null,
                   "end": "YYYY-MM-DD"|null, "year": int|null,
                   "kind": "solo"|"group"|"fair"|"other", "source_url": str}],
  "fairs_self_reported": [str], "memberships_self_reported": [str],
  "press_self_reported": [{"outlet": str, "year": int|null, "url": str|null}],
  "claims_supported": bool, "unsupported_claims": [str], "notes": str|null,
  "venue_kind": kind|null, "kind_confidence": float|null, "kind_evidence": str|null   # schema 2
}
```

`kind` is classified by the profile pass from the venue's own pages (definitions in
`venues.KIND_DEFS`) and written back through `venues.set_kind(..., "research")` when
`kind_confidence >= 0.7`; research outranks every seed, show and agent write, and
never a manual `venues.py set-kind`. `research_venue.py classify --city X [--apply]`
re-types already-researched venues from their saved reports (dry run prints the
proposed changes and writes `content/venues/reports/<city>/kind-review-<ts>.json`).

## Ranking (`content/curation/<city>/venues_ranked.json`)

Written by `rank_venues.py score`; the dashboard embeds it. Params live in
`content/curation/params/venues-*.json` (default `venues-default.json`).

```
{"city", "generated_at", "today", "params_default": {...}, "feature_names": [...],
 "venues": [{"id", "name", "kind", "is_museum", "neighborhood", "status",
             "verification": status, "website", "report_path",
             "features": {name: value}, "basis": {name: str},
             "score", "contrib": {name: weight*value}, "gate": null|str,
             "tier", "rank"}],
 "benchmark": {"seesaw_venue_ids": [...], "auc": float|null},
 "presets": {...}}
```

Params shape (mirrors curate.py): `{"weights": {feature: w}, "gates": {"require_verified": bool,
"kinds": [..], "exclude_status": [..]}, "tiers": {"1": 0.55, "2": 0.30, "3": 0.10},
"refs": {...per-feature saturation constants...}, "manual_order": null | {"1": [ids], "2": [ids],
"3": [ids]}}`. Score = Σ weights·features, linear, so the JS mirror stays one-to-one.

`manual_order` is the curation site's hand-ordered list (drag / ▲▼ on a gallery card freezes the
current ranked set into it). When present, `rank_venues.rank` and `venue_core.rank` give the
listed venues ranks 1..n in exactly that order with the tier of the list they sit in, ignoring
score and gates for them; every other venue is scored and gated as usual but gets tier null and
ranks after the list. Share links (`#g=`), the email/text bodies (numbered list) and the exported
params JSON (`manual_order` + a readable `_order`) all carry it, so `curation_site.py decode` →
`rank_venues.py apply --params` reproduces the exact order.

**Market order file** — `content/curation/<city>/venue_order.json` is a per-market hand ranking
(typically the client's list) that overrides the automatic ranking for that city:

```json
{"city": "los-angeles", "source": "who ranked it, when", "tiers": {"1": 20, "2": 50},
 "entries": [{"rank": 1, "name": "Hauser & Wirth", "id": "hauser-and-wirth",
              "neighborhood": "Arts District", "note": null}, ...]}
```

`rank_venues.py score|apply` turns it into `params.manual_order` (entry rank ≤ tiers.1 → tier 1,
≤ tiers.2 → tier 2, else tier 3) unless a params file set `manual_order` itself — a decoded
curation-site link always carries `manual_order` (the client's edited list, or `null` for
"Back to automatic ranking"), so the client's explicit choice wins — or `--no-order` is passed.
Entries with a null/unknown `id` are resolved by name against the registry at load time; entries
whose venue status is in `gates.exclude_status` (closed, duplicate, out_of_scope) are skipped; a
venue named twice keeps its first rank. The report's `order` block (`file`, `source`, `ranked`,
`unresolved`, `excluded`, `merged`) says exactly what happened, `rank_venues.py order --city C`
prints it, and `rank_venues.py order --city C --names names.txt [--source S] [--tiers 20,50]`
builds the file from a name list (one name per line, or `rank|name|neighborhood|note`). The
curation site shows the live list under a "Hand-ordered list (live)" banner that names the source.
Los Angeles: the client's 200-entry "Los Angeles galleries" list (2026-09-03) is the applied
ranking; `venues-la-ranked200.json` is the automatic preset behind it.

**Verifying the unresolved entries** — `verify_order.py --city C [--apply]` takes every order entry
the registry does not know and asks whether it is a real, open venue the pipeline missed: one
cached Places text search + details per name (a retry with "gallery" appended when the first
result is another business; listings outside the metro do not count), `validate_venues.site_checks`
on the website (live / vouched / closed notice / exhibitions page; a Places "permanently closed"
against a live site with an exhibitions page is flagged for a hand check, not trusted),
a stale-site check (newest dated show across the exhibitions / current / past pages and the
homepage: a live, venue-looking site whose newest show is years old, has no dated show at all, or
only describes online exhibitions is NOT proof of an open programme → `unverified`),
`seed_venues.assign_zone` for the footprint, and a coverage probe of the nets that should have
caught it (Gallery Platform LA, Carla, the cached Places nearby sweep with
`place_skip_reason`, the swept circles, the enumeration logs). Verdicts: `miss` (real, open, in
footprint — `why` names the nets it fell through), `unverified`, `closed`, `not_found`,
`out_of_footprint`, `not_a_venue` (auction / bookstore / advisory / private dealing / co-op …).
`--apply` seeds the misses (status active when verified, `sources.seed.order` carries the list
rank) and pins their ids in `venue_order.json`; then `rank_venues.py apply` ranks them.
`--ranks 60,143` re-checks chosen entries (seeded ones included, using their registry website and
address as hints) without re-seeding. Entries may carry `address` / `website` hints added by hand. Report:
`content/spend/reports/verify-order-<city>-<ts>.json`.

Feature names (all 0..1): hours_breadth, fairs, curated_lists, press, directory, longevity,
roster_size, roster_strength, show_cadence, multi_location, places_popularity, web_presence,
venue_judge, wiki, kind_gallery, kind_nonprofit, kind_museum, seesaw_presence (off).

Research selection (`research_venue.auto_select`): eligible = website + verified (or
active/appointment_only) + not flagged; chosen = prestige-evidence venues ∪ top
`--select-pct` (10) % by prior `score`, cap `--select-cap` (250). Requires
`rank_venues.py apply` first (the driver runs it).

## Signals

`curation_store.SIGNAL_KINDS` gains `list_member` (venue on a curated list / association
roster; artist/title null; `source.id` = the list's sources.json id, kind `curated_list`).
`sources.json` gains entries with `"kind": "curated_list"` (ADAA, CADAN, ...) next to the
existing `"kind": "fair"` entries. `venue_judge.jsonl` rows mirror `judge.jsonl` rows with
`venue_id` instead of `slug`.

## Artists (`content/artists/<city>.json`)

```
{"schema": 1, "city", "updated",
 "artists": [{"artist_id", "name", "aliases": [str],
              "galleries": [{"venue_id", "relation": represented|exhibited|estate|showed,
                             "since": int|null, "source_url"}],
              "shows_current": [{"slug", "venue_id", "title", "start", "end"}],
              "shows_history": [{"venue_id", "title", "start", "end", "year", "kind", "source_url"}],
              "wiki": {...}|null}],
 "review": [...]}   # unresolved merge candidates also mirrored to merge_review.json
```
`content/artists/merges.json` = `{"merge": [[keep_id, drop_id], ...], "split": [...]}` hand-edited.
Report artifact: `content/spend/reports/artists-<city>.{md,json}`.

## Verification checks (validate_venues.py)

verified  = (places OPERATIONAL or no Places row but site vouched) and website_live and
            (address_match or no Places row) and not closed_notice
flagged   = places CLOSED_PERMANENTLY / CLOSED_TEMPORARILY / NOT_FOUND-with-website-dead / closed_notice
unverified = everything else. Only `verified` venues enter research, ranking gates, and S1.

## Quick city (quick_city.py) — LLM-seeded, Places-verified galleries

The stages above take days per city. `scraper/quick_city.py` reaches a city in an afternoon
and leaves a registry in the same shape, so `validate_venues.py`, `research_venue.py` and
`run_galleries.py --stages research,rank` can flesh it out later unchanged.

```
quick_city.py cities                       # both models' top-25 gallery cities -> content/expansion/cities.json
quick_city.py seed     --city C            # both models' top-100 galleries -> content/expansion/C/seed-merged.json
quick_city.py validate --city C --apply    # Places + own-site checks -> registry (all), verification.status
quick_city.py rank     --city C --apply    # content/curation/C/venue_order.json (verified only) -> rank_venues.py apply
quick_city.py shows    --city C            # run_deep.py --venue-ids <verified> --force-due (+ its verify stage)
quick_city.py status                       # per-city counts, spend, audit of shows at non-verified venues
```

**Models** — `llm_clients.py`: `claude-opus-5` (Messages API, json_schema output, adaptive
thinking, effort high) and OpenAI `gpt-5.6-sol` (Responses API, strict json_schema). Raw
answers are kept under `content/expansion/` so a re-run never pays twice (`--refresh` does).
Ledgers: `content/spend/quick-<stage>-<city>-<ts>.json`.

**Blended rank** — `merge_rankings`: the two lists are unioned (same id, same site domain, or
the same distinctive name words); blended = mean of the two positions, a list that omits the
gallery contributes one past its own length; ties break on the better single rank, then name.
Both positions are kept (`rank_claude`, `rank_openai`) and written to the registry under
`sources.seed.quick`. Cities blend the same way; the pilot is the top 10 plus every configured city, minus LA/Tokyo.

**Validation** (`validate_city`, one `verify_order.check_entry` per name, coverage probe off):
`verify_order.places_lookup` (cached 30 days, ~$0.052 per uncached name; English results and
a `locationBias` circle from the city's centre/span so a chain's home branch never answers
for its outpost; a hint-less retry when the address hint returned the building instead of
the tenant; one retry on DEADLINE_EXCEEDED), `validate_venues.site_checks` (vouching also
accepts localised words: galería, 畫廊, 갤러리 … and retries the site root when a branch page
such as davidzwirner.com/hongkong is not in English), `verify_order.stale_check`. A site that
refuses bots (429 / 403 / robots.txt) is `site_blocked`, not dead: the venue still verifies
when the Places listing is complete AND names the same site (Hauser & Wirth). Zone = `seed_venues.assign_zone`
over the geocoded neighborhood centroids (`content/expansion/<city>/zones.json`, via
`seed_venues.geocode_area`) plus any registry venue that already has coordinates; footprint =
inside `center ± span` widened by half a span. The LLM's free-text note is never passed to
the checks (`NOT_A_VENUE_RE` would fire on "dealer"). **verified** needs all of: a
name-matched Places listing inside the metro (`metro_tokens` in cities.py) with
`OPERATIONAL`, an address, coordinates and opening hours; the site live, vouched as an art
venue, no closed notice; a dated show on the site newer than ~2 years; the pin in the
footprint; a zone. Anything else is `unverified` with reason codes
(`places_missing|places_mismatch|places_closed|no_location|no_hours|no_website|site_dead|
site_not_vouched|closed_notice|stale|out_of_footprint|not_a_venue|no_zone`) in
`verification.checks.reasons`. A street-number disagreement between the LLM's address hint
and Places is recorded as `checks.moved_hint` (Places wins). Every checked gallery is written
(`venues.bulk_upsert`, source `seed-quick`, `protect_existing` = status / neighborhood /
next_check / website / exhibitions_url / address_detail): verified -> `status active`, else
`unknown`; hours, address, coordinates, `google` and `verification` are refreshed from the
pass. Report: `content/expansion/<city>/validate-<ts>.json`.

**Rank** — the order file lists only verified venues in blended order with ids pinned
(`note: "claude #a / openai #b"`); `rank_venues.py apply` then gives them ranks 1..n and
tiers by the cutoffs, and every other venue ranks after with tier null. `rank` refuses to
replace an existing `venue_order.json` without `--force` (LA's is the client's list).
`content/expansion/<city>/verified-ids.txt` feeds the show stage.

**Shows** — `run_deep.py --city C --venue-ids @verified-ids.txt --force-due --no-report`
(Sonnet 5): every id must be verified less than 30 days ago and carry a configured zone
(`check_show_preconditions`; with `--venue-ids` the deep scrape drops the unzoned bucket).
run_deep's own verify stage promotes in-window verified shows to `content/<city>.json`;
upcoming ones wait in pending; then `sync_shows.py --apply`.

**Feed and client site** — every quick city's show feed uses the gallery-order preset
(`content/curation/params/publish-galleryorder.json`, docs/CURATION.md "The live preset"):
`curate.py score` + `apply --reorder` per city, then `curation_site.py build --all` renders one
page per city under `webdemo/dist/gallery-browser-curation/<city>/` with a city menu in the
header (Galleries and Shows modes as before) and a root page listing the cities.

**Augmenting a city** — `quick_city.py augment --city C --apply` seeds, keeps only the names the
registry does not know (id / alias / site domain / normalised name), validates and adds those,
scrapes the verified ones' shows and writes `content/expansion/<city>/augment-report.json`. It may
add to a protected city but never ranks it. Existing records keep their `name` (the model's
spelling becomes an alias) and are matched on the model's website, never the Places one; the
city's own name is not a distinctive word when matching a Places result ("Gagosian Tokyo" must not
match "Taka Ishii Gallery Tokyo" — the 2026-09-04 Tokyo pass mis-merged exactly that before these
rules). Tokyo's ranking became the LLM consensus on 2026-09-04 (`rank --city tokyo --apply`).

**Never written**: `los-angeles` (`NEVER_WRITE`, checked on every write path);
`content/curation/params/*`. (`gallery_photos.py` is not part of this pipeline: it sets only
`venue.photos`, on any city.) Adding a city: one `CITIES` entry (`display_name, center, span,
neighborhoods, guidance, metro_tokens` — `cities.json` carries a drafted block), the same
neighborhoods in `GalleryBrowser/Models.swift`, its zone in `webdemo/cityconfig.CITY_TZ`,
optionally `harness.SECOND_SOURCES`. Existing cities keep their neighborhood labels
(published shows, registries and verify sharding key off them); galleries in other districts
map to the nearest centroid.

## Gallery photos (gallery_photos.py)

Up to five photographs of each venue *as a place* (facade, entrance, interior, installation
views where the room is the subject), hero first, under `content/images/venues/<city>/<id>/NN.jpg`
with provenance in `venue.photos` (contract above) and the working state in the same folder's
`_work.json`. Added 2026-09-05 for the top 50 of Los Angeles and Tokyo.

Sources, cheapest first: the venue's own site — og:image plus hero images from the homepage and
the dossier's `about` / `contact_hours` pages (`triage.labels`; `/about`, `/contact`, ... probes when
there is no report), $0, fetched with `refresh.Fetcher` (robots.txt, 2 s per domain, browser-UA
retry when bot-walled) — then Google Places photos: metadata is free on Places API (New)
(`id,photos` = the IDs-only SKU; the project's key blocks the New API, so the script falls back to
legacy Details, a Pro SKU with 5,000 free calls a month) and each download is one Place Photo
event ($7 / 1,000 after 1,000 free a month, tallied in `content/spend/google-photo-events.json`).
Candidates are pre-ranked without cost (og:image first; owner-uploaded, landscape, larger Google
photos first), deduplicated by average hash, then one `claude-sonnet-5` call per venue (candidates
downscaled to 768 px, json_schema verdict: ordered picks with subject + caption, rejects with a
reason) chooses the set. ~$0.015 per venue; ledger `content/spend/gallery-photos-<city>-<ts>.json`.

Stages, resumable: `candidates` → `fetch [--google-per-venue 5] [--google-cap 400] [--dry-run]` →
`judge [--budget USD] [--dry-run]` → `apply`; `run` does all four. Select with `--top N` (rank
order) or `--venue-ids`.

What ships: Google's terms allow caching Places content other than place IDs for 30 days only and
require author attribution, so Google-sourced files stay on disk flagged `provider: google` and the
web build bundles site-sourced photos only (`webdemo/build.py --venue-photos site|all|none`);
`sync_shows.py` copies the site-sourced paths onto embedded show venues as `venue.photos` for iOS.
