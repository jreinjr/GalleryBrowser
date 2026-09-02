# Curation: ranking the feed against See Saw

The pipeline decides *which* shows exist. This document covers what decides
their **order**, and what we learned trying to reproduce See Saw's editorial
judgment. Written 2026-09-02 from the Los Angeles workshop.

Code: `scraper/curate.py` (scoring + gates), `scraper/judge.py` (the LLM
verdict), `scraper/curation_dashboard.py` (the tuning dashboard). Presets live
in `content/curation/params/*.json`. Benchmark snapshots live in
`content/curation/<city>/seesaw/`.

## The model

Ten features per show, each in roughly `[0, 1]`, combined linearly:

```
score = Σ weights[f] × features[f]
```

then a gate chain (`curate.apply_gates`) decides what survives:

```
overrides.exclude → in-window → published_only → require_open
→ score ≥ threshold → max_per_venue → exclude_museums
→ max_per_neighborhood → max_museum_share → overrides.pin → max_n
```

`featured` = the survivors; `editors_pick` = the top `editors_pick_top` of
them. `curate.py apply --reorder` writes those flags into the published file
in rank order, and `webdemo/build.py` renders that order as the app's Featured
tab. The formula is mirrored one-to-one in the dashboard's JS — change one,
change the other.

## What actually predicts a See Saw pick

Measured as single-feature AUC over the 189-show in-window LA pool, against
two targets: See Saw's picks vs. the shows See Saw *covers but passed over*
(the hard test), and vs. the rest of the pool (the easy one).

| feature | vs. passed-over | vs. rest of pool | verdict |
|---|---|---|---|
| `judge` | **0.737** | 0.845 | the only feature that discriminates taste |
| `closing_soon` | 0.633 | 0.763 | real but see the caveat below |
| `quality` | 0.590 | 0.737 | strongest *dense* feature |
| `venue` | 0.567 | 0.570 | weak — the tier map is barely populated |
| `press` | 0.567 | 0.620 | weak |
| `wiki_heat` | 0.513 | 0.532 | chance |
| `artist_heat` | 0.480 | 0.570 | chance |
| `opening_recency` | 0.443 | 0.483 | noise |
| `keyword` | **0.397** | 0.457 | *anti*-correlated |
| `museum` | 0.467 | 0.302 | strongly negative |

Five things worth internalising:

1. **The judge carries the ranking.** Nothing else separates a show See Saw
   features from one it merely lists. Its rubric is explicitly written against
   See Saw's Featured feed, which is why it transfers.
2. **`press` is close to useless for gallery shows.** It was the largest weight
   in `default.json` (0.35). Every in-pool See Saw Featured show has press
   ≤ 0.14 — press fires on museum shows, which See Saw does not feature.
3. **`quality` was weighted 0** and is the strongest dense signal. It measures
   scrape completeness, not merit — `0.5·min(1, images/5) + 0.5·min(1,
   words/250)` — but a gallery with a real press release and an image set is a
   gallery running a real program. Its top end is noise (vanity galleries with
   fat pages), so keep the weight moderate.
4. **`keyword` actively hurts.** It matches institutional survey/retrospective
   language that See Saw's gallery picks do not use.
5. **Museums are the single biggest gate effect.** See Saw's Featured feed is
   0% museums; the pool is 35%. Turning `exclude_museums` off costs as much as
   zeroing the judge weight.

### The judge is a hard dependency

`judge_v1` covers a show only if it was judged. Coverage is not automatic:
`judge.py run --limit N` slices `tools.all_city_shows` in **file order**, so a
partial run judges the top of the file, not the best shows. An unjudged show
scores near zero and disappears from the feed.

Full Sonnet coverage of the 321-show LA pool cost **$3.26** (277 verdicts,
~$0.012 each). Run it before every scoring pass:

```
scraper/.venv/bin/python scraper/judge.py run --city los-angeles \
    --variant judge_v1 --models claude-sonnet-5 --workers 8
```

Verdicts are cached on `sha256(slug|variant|prompt|model|effort|evidence)`, so
re-running only judges what is new or has changed.

Dense coverage is what unlocked the benchmark. Karma's "Dictation from the
Other Side" sat at rank 138 and was **provably unreachable** — Pareto-dominated
on all ten features by 13 other shows, so no non-negative weighting could lift
it. It had no verdict. With one, it ranks 18. When the ranking is wrong, check
for missing evidence before reaching for the weights.

## The two publication gates

`show_in_window` admits shows opening within `OPEN_WINDOW_DAYS = 7`, and the
scoring pool is published + pending. Neither is what the app wants:

- **`published_only`** — `webdemo/build.py` reads `content/<city>.json` only.
  A pending show that wins a feed slot silently leaves the Featured tab short.
  (22 in-window LA shows were in that pool.)
- **`require_open`** — the Featured tab should only carry shows a visitor can
  walk into today. (21 published LA shows were not yet open.) Undated shows are
  gated too: we cannot assert they are open.

Both default to `false` so benchmark presets are unaffected; the publication
presets turn them on.

## Presets

| preset | fitted to | use |
|---|---|---|
| `default.json` | nothing | the shipped defaults; press-heavy, unfitted |
| `seesaw-gallery.json` | 2026-09-01, first pass | superseded |
| `seesaw-overlap.json` | "which of our shows does See Saw list at all" | superseded |
| `seesaw-taste.json` | "which shows does See Saw *feature*" | benchmark |
| `publish-la.json` | galleries-only publication | alternative app feed |
| **`seesaw-complete.json`** | completion + graded precision | **live** |

Every preset carries a `_note` with its fitting method, results, and caveats.
Read it before trusting the numbers.

### The live preset

`seesaw-complete.json` optimises two things at once: **completion** (every show
See Saw lists survives the gates) and **graded precision** (See Saw's shows
rank high, Featured above Editor's Picks above merely-listed, scored by nDCG
over grades 3/2/1).

```
judge .44 | venue .14 | quality .08 | closing_soon .04 | press .04
museum -0.20   with exclude_museums: false
threshold 0.22 | max_n null | max_per_venue 2 | published_only + require_open
```

Verified through `curate.rank`: **25/25 completion**, 6/6 Featured, 9/9
in-pool Editor's Picks, nDCG@5/@10/@25 of 0.581/0.717/0.751, **P@5 = P@10 =
1.00** — the first ten feed slots are ten See Saw shows. Feed length 76.

**The museum trick is the useful finding.** Completion *requires* museums,
because See Saw lists The Broad's Yoko Ono; a hard galleries-only filter caps
you at 24/25 no matter what else you do. But museums scoring high crowd the
top and wreck precision. `exclude_museums: false` with `museum: -0.20` does
both jobs — they stay in the feed and sink (Yoko Ono lands at Featured-tab
position 26 despite a judge score of 9). This beat every galleries-only variant
searched.

**Completion sets the cutoff.** Threshold 0.22 is pinned by the single weakest
See Saw show (Monte Vista Projects, "Moth To A Flame", judge 2/10, score
0.227). Same weights, higher cutoffs:

| threshold | feed | completion | Featured |
|---|---|---|---|
| **0.22** | 76 | **25/25** | 6/6 |
| 0.26 | 59 | 24/25 | 6/6 |
| 0.30 | 40 | 21/25 | 6/6 |
| 0.34 | 28 | 16/25 | 6/6 |
| 0.38 | 21 | 13/25 | 5/6 |

All six Featured survive to 0.34, so raise the threshold for a shorter feed
without losing them.

## How to trust the numbers

This is where it is easiest to fool yourself. Four rules earned the hard way.

**Say which target a precision number is against.** `P@5 = 1.00` for
`seesaw-taste` is against Featured **∪** Editor's Picks — ten shows. Against
See Saw's Featured list *alone*, the same feed gives P@5 = 0.20 and P@10 =
0.30. Both are real; only one is flattering. See Saw's Featured shows sit at
our pool ranks 2, 10, 18, 25, 56, 60 — one in our top ten.

**Separating See Saw's picks from the rest of the pool is the easy task.**
It is won by excluding museums and preferring well-scraped pages. The
discriminating test is ranking See Saw's picks above the shows See Saw
*covers but passed over*: `seesaw-overlap` scored 0.600 AUC there —
indistinguishable from the untuned default's 0.627. Dense judge coverage, not
weighting, is what moved that to 0.760.

**Fitted numbers are not held-out numbers.** Leave-one-venue-out — refit
without a venue's shows, then score only that venue — costs roughly 0.05–0.09
AUC. With 25 positives and 10 in the taste tier, one week's number is noisy;
per-venue spread runs 0.40–0.93.

**Two captures a day apart are one sample.** The 2026-09-01 and 2026-09-02
snapshots resolve to an identical 25-show set and an identical 10-show taste
set, despite Featured shrinking 7 → 5. Re-scoring on the second changes
nothing. A genuine out-of-sample read needs a capture after the shows turn
over — roughly monthly.

## The benchmark is not ground truth

See Saw featured **1301PE, "Lull Between Storms"** at position 1 on both
capture days. The gallery's own site says otherwise, three ways: the rendered
current-exhibition page carries no exhibition at all, past-exhibitions lists
the show as April 15 – June 13 2026, and future-exhibitions lists only "As the
Moonbird Flies" from September 25. 1301PE is between shows. Our absence is
correct and See Saw is stale.

Verify with the renderer, not a plain fetch — the site is JS-built:

```
NODE_PATH=/opt/homebrew/lib/node_modules node scraper/render_fetch.js <url>
```

A related trap: "See Saw only lists open shows" is **false**, and the check
that appeared to confirm it was circular — it only examined entries that
matched our pool, and the one entry that does not match is precisely the stale
one. 25 of 26 are open.

Treat the benchmark as a strong signal with real errors in it, and never feed
See Saw data back into features or scraper rules. `venue_tiers.py` keeps
`--seesaw-weight` at 0 by default for exactly this reason.

## Capturing a snapshot

The lists come from the iOS app (bundle `com.seesawmap.seesaw`) via computer
use — there is no web version, no public API, and no archive; `seesawmap.com`
is a one-page app landing site. Transcribe each tab and record it:

```
scraper/.venv/bin/python scraper/seesaw_snapshot.py --city los-angeles \
    --date YYYY-MM-DD --tab featured|editors_picks|all \
    --from-text feed.txt --match
```

Snapshots are immutable (`--force` to overwrite). Note that See Saw sometimes
displays an artist name where it previously showed a title ("The Woods" →
"Dan Mitchell & Richard Sides"); the matcher tries the primary line as both,
so record it verbatim.

## Rebuilding the feed end to end

```
scraper/.venv/bin/python scraper/judge.py run --city los-angeles \
    --variant judge_v1 --models claude-sonnet-5 --workers 8
scraper/.venv/bin/python scraper/curate.py match --city los-angeles
scraper/.venv/bin/python scraper/curate.py score --city los-angeles \
    --params content/curation/params/seesaw-complete.json \
    --snapshot los-angeles-2026-09-02
scraper/.venv/bin/python scraper/curate.py apply --city los-angeles \
    --params content/curation/params/seesaw-complete.json --reorder
scraper/.venv/bin/python scraper/curation_dashboard.py --city los-angeles
scraper/.venv/bin/python webdemo/build.py
cd webdemo/dist/gallery-browser-demo && vercel deploy --prod --yes
scraper/.venv/bin/python scraper/curation_site.py build --city los-angeles
cd webdemo/dist/gallery-browser-curation && vercel deploy --prod --yes
```

Pass the snapshot id explicitly — `latest` sorts lexically. The dashboard
embeds `content/curation/params/*.json` at render time, so re-run it after
adding a preset or the new file will not appear in its Presets panel.

`apply` also writes every published show's rank into `curated.json`
(`ranked`), which `webdemo/build.py` embeds so the app's List tab can sort by
ranking; run `apply` before `build.py` or the list falls back to file order.

## The client site

https://gallery-browser-curation.vercel.app is the client-facing twin of the
dev dashboard, built by `scraper/curation_site.py` from the same score report
(`content/spend/reports/curation-<city>.json`) and the live params in
`content/curation/<city>/curated.json` — so its default settings are, by
construction, exactly what shipped. The builder refuses to run when the report
is older than `curated.json` or was scored under different params: re-run
`curate.py score` with the live preset first. It exposes only the feed gates
(cutoff, max per venue, exclude museums) and the six feature weights, each with a one-line explanation; everything else is pinned to the
live values. Every show in the pool is listed — pending and not-yet-open ones
with a plain-language status — with the same evidence drawer as the dashboard.

There is no preset save/load. The settings live in the URL hash (`#p=` is the
diff against the live params), so **Copy link / Email link / Text link** share
a reproducible state. To turn a received link into a params file:

```
scraper/.venv/bin/python scraper/curation_site.py decode '<link>' --out params.json
scraper/.venv/bin/python scraper/curate.py apply --city los-angeles --params params.json --reorder
```

then rebuild and deploy both sites as above. The email button's recipient is
`--contact-email` (defaults to the project owner's address); `--site-url`
overrides the base URL baked into share links.
