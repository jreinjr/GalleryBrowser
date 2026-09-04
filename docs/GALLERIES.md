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
features:     {<name>: {value: float 0..1, basis: str, ts: int}}           # rank_venues.py
rank, score, score_breakdown                                               # rank_venues.py apply
```

`tier` / `notability` keep their meaning (tier 1-3 or null) but are now written by
`rank_venues.py apply` (venue_tiers.py is superseded).

## GalleryReport (`content/venues/reports/<city>/<venue_id>.json`)

Written by `research_venue.py`, tracked in git. Strict shape:

```
{
  "schema": 1, "city", "venue_id", "name", "website", "generated_ts", "model",
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
  "claims_supported": bool, "unsupported_claims": [str], "notes": str|null
}
```

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
