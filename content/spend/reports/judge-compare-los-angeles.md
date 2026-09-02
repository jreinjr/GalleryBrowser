# Judge compare - los-angeles / judge_v1

generated 2026-09-02T02:17:20+00:00; params ed4d67949009; N = 12 (params.max_n); See Saw snapshot los-angeles-2026-09-01 (7 entries)

| metric | value |
|---|---|
| shows with both verdicts | 48 |
| Spearman rho (overall, opus vs sonnet) | 0.95 |
| mean abs delta (opus - sonnet) | 0.42 |
| precision@12 vs See Saw - sonnet / opus / mean | 0.00 / 0.00 / 0.00 |
| See Saw overlap (tp) - sonnet / opus / mean | 0 / 0 / 0 |
| $/verdict - sonnet / opus | $0.0081 / $0.0211 |
| total cost - compared rows / all judge rows for this variant | $1.4047 / $1.5693 |
| auto-suggestion | **claude-sonnet-5** - rho 0.95 >= 0.85 and See Saw overlap within 1 (0 vs 0) |

Auto-suggestion rule: rho >= 0.85 and See Saw overlap within 1 -> Sonnet, else Opus. Read the rationales below before deciding.

## Top 10 decision-relevant comparisons

relevance = |delta| + 3*membership_flip + 4*seesaw_flip + 0.5*max_sub_delta + 0.5*cutoff_proximity; member = featured under params with judge = sonnet / opus / mean.

| # | show | venue | sonnet A V C N T -> overall | opus A V C N T -> overall | delta | member S/O/M | rank S/O/M | See Saw | relevance | $ S / O |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Outsiders, Outcasts, Rebels + Weirdos: Punk Culture 1976–86 | Skirball Cultural Center | A2 V5 C2 N6 T3 -> **4** | A2 V5 C2 N5 T7 -> **5** | +1 | N/N/N | 18/14/15 | N | 3.12 | $0.0058 / $0.0220 |
| 2 | The Hunter | Peter Fetterman Gallery | A9 V4 C6 N6 T3 -> **6** | A9 V5 C3 N5 T8 -> **6** | +0 | N/N/N | 14/13/13 | N | 2.75 | $0.0070 / $0.0240 |
| 3 | Joel-Peter Witkin: Selected Works 1974–2018 | Fahey/Klein Gallery | A8 V6 C4 N7 T4 -> **6** | A7 V5 C2 N4 T4 -> **5** | -1 | N/N/N | 15/18/16 | N | 2.60 | $0.0108 / $0.0218 |
| 4 | Marilyn Monroe: Hollywood Icon | Academy Museum of Motion Pictures | A6 V7 C3 N5 T3 -> **5** | A3 V6 C2 N5 T3 -> **4** | -1 | N/N/N | 16/19/19 | N | 2.56 | $0.0157 / $0.0234 |
| 5 | Madonna: The Rise of the Material Girl (1980–1985) | Musichead Gallery | A6 V2 C2 N3 T5 -> **3** | A3 V2 C1 N3 T5 -> **2** | -1 | N/N/N | 39/43/42 | N | 2.52 | $0.0060 / $0.0172 |
| 6 | Now We're There (And We' Only Just Begun) | Los Angeles County Museum of Art (LACMA) | A8 V9 C4 N6 T4 -> **6** | A8 V9 C3 N4 T3 -> **5** | -1 | N/N/N | 7/10/9 | N | 2.12 | $0.0127 / $0.0239 |
| 7 | The Development Center: A Clownache Store | NOON Projects | A2 V2 C2 N5 T6 -> **3** | A4 V3 C2 N5 T7 -> **4** | +1 | N/N/N | 32/27/30 | N | 2.03 | $0.0058 / $0.0231 |
| 8 | Artifacts from an Unborn Empire | Brand Library & Art Center | A2 V3 C1 N5 T4 -> **3** | A3 V3 C1 N4 T2 -> **2** | -1 | N/N/N | 30/33/31 | N | 2.02 | $0.0062 / $0.0199 |
| 9 | All Unfinished | ace/121 Gallery | A2 V2 C1 N3 T4 -> **2** | A1 V2 C0 N2 T2 -> **1** | -1 | N/N/N | 41/44/44 | N | 2.02 | $0.0055 / $0.0150 |
| 10 | Pretty on the Inside | 7811 Gallery | A2 V1 C1 N3 T4 -> **2** | A1 V1 C0 N2 T2 -> **1** | -1 | N/N/N | 45/47/45 | N | 2.01 | $0.0055 / $0.0161 |

### 1. Outsiders, Outcasts, Rebels + Weirdos: Punk Culture 1976–86 - Skirball Cultural Center (`skirball-punk`)

| Sonnet (overall 4, conf 0.4, $0.0058) | Opus (overall 5, conf 0.6, $0.0220) |
|---|---|
| Unnamed group/collection show, so artist_significance is low despite named figures like Cliff Roman in programming. Skirball is a respected mid-tier museum but not an encyclopedic powerhouse, giving moderate venue credit. No signals on file means critical_reception defaults low per rubric. The curatorial angle (Jewish contributors to LA punk) is a genuinely novel framing, boosting ambition_novelty. Show closes in 5 days, which cuts both ways—timely urgency but very late to feature. No evidence of press pickup or reviews despite a 104-day run, a concern. | No signals on file, so coverage cannot be credited beyond the venue's own framing. The premise — LA punk ephemera with a specific lens on Jewish scene-builders — is a genuinely researched thematic survey rather than a rental show, and the Masque/Whisky flyer material has real local resonance, but no individual artist anchors it and archival-ephemera surveys are a familiar format. Skirball is an accredited museum yet sits well below the Broad/LACMA/Hammer/Getty tier in this week's pool, and Westside cultural-campus programming rarely draws the insider crowd. Closing in five days is the strongest argument for now; a fun last-weekend tip, not a must-see over Yoko Ono, Quarles, or Fischer. |
| red flags: no independent coverage/signals despite long run; closes in 5 days - low value for a 'must-see this week' feature; unnamed group/collection show limits artist-significance scoring | red flags: no independent coverage on file after 104 days on view; unnamed group/collection show — no artist stature to assess; description reads partly as press-release framing (curatorial 'scrappiness' claim unverified); closes in 5 days: verify hours/last-weekend access before featuring |

### 2. The Hunter - Peter Fetterman Gallery (`peter-fetterman-moriyama`)

| Sonnet (overall 6, conf 0.6, $0.0070) | Opus (overall 6, conf 0.7, $0.0240) |
|---|---|
| Moriyama is a canonical postwar Japanese photographer, currently active with major international activity: group show at National Museum of Art Osaka [S1], a Fondation HCB solo [S2], and a KYOTOGRAPHIE retrospective [S3], confirming ongoing critical relevance though none are LA-specific. Peter Fetterman is a respected but mid-tier LA gallery, not top-flight (venue_significance capped). Description claims 'first major LA exhibition' but this is a commercial gallery survey, not a museum retrospective - ambition is real but scale modest. Show closes in 4 days, limiting timeliness despite artist's global moment. | Moriyama is canonical — Provoke-era are-bure-boke, collected by MoMA/Tate, and 2026 is clearly a Moriyama year (Osaka group show [S1], Fondation HCB solo [S2], KYOTOGRAPHIE retrospective [S3]). But all three signals are activity elsewhere; nothing independent covers this Bergamot Station presentation. Peter Fetterman is a respected but conservative photography dealer, and the format here reads as a curated selection of vintage/later prints rather than a survey or new body of work — the "first major Los Angeles exhibition" line is unverified and questionable given LA's photo history. Closing Sept 5 gives it real urgency, which is the strongest argument for a slot this week. A recommend, not a headline. |
| red flags: closes in 4 days - urgency but little runway for a 'must-see' recommendation; no LA-specific press coverage signals, only international artist activity | red flags: "first major Los Angeles exhibition" claim unverified and likely overstated; no independent coverage of this specific show; all signals are artist activity abroad; commercial print show framed as a survey |

### 3. Joel-Peter Witkin: Selected Works 1974–2018 - Fahey/Klein Gallery (`fahey-klein-witkin`)

| Sonnet (overall 6, conf 0.55, $0.0108) | Opus (overall 5, conf 0.68, $0.0218) |
|---|---|
| Witkin is a canonical, museum-collected photographer with a five-decade practice; [S1] confirms a concurrent Prague museum solo, evidence of ongoing international relevance though not tied to this specific show. Fahey/Klein is a respected, longstanding LA photography specialist gallery but not a major institution. The survey format (1974-2018) gives real ambition and scholarly framing, but no direct press coverage of this exhibition was found, so critical reception rests on artist reputation rather than confirmed reviews. Mid-run timing tempers timeliness. | Witkin is a genuinely significant, widely collected photographer (MoMA, Getty) whose staged mortality tableaux remain divisive, and [S1] confirms continuing museum-level activity abroad (DOX Prague) — but that is artist activity, not coverage of this LA show, of which there are no signals. Fahey/Klein is a long-running, respected commercial photography gallery rather than a destination kunsthalle. A dealer survey of vintage prints 1974–2018 is a career overview assembled from existing work, not new production or a scholarly retrospective despite the description's framing. Mid-run, closing in three weeks: worth a mention for photography people, not a top-of-week insider pick. |
| red flags: No independent coverage specific to this exhibition, only artist activity elsewhere [S1]; Venue registry tier unknown, relying on general reputation | red flags: Description reads partly as press-release advocacy ('scholarly care usually reserved for painting retrospectives') with no independent coverage of this show; Only signal concerns a different (Prague) exhibition, not this presentation |

### 4. Marilyn Monroe: Hollywood Icon - Academy Museum of Motion Pictures (`marilyn-monroe-academy-museum`)

| Sonnet (overall 5, conf 0.4, $0.0157) | Opus (overall 4, conf 0.6, $0.0234) |
|---|---|
| Academy Museum is a legitimate, high-traffic institution and Monroe's cultural stature is undeniable, but this is a costume/ephemera collection show, not a living artist's career statement, and no signals confirm any critical engagement despite the show having run for three months already. It reads as a popular blockbuster for tourists rather than an art-world must-see; mid-run timing weakens the 'this week' case. No [S#] coverage on file to substantiate press attention. | No signals on file, so coverage is unverified — though a Monroe centennial show at the Academy Museum will get lifestyle and entertainment press, that is not the art-world attention this feed rewards. As a curated archival/costume exhibition there is no artist stature to score: the subject is a film star, not a maker, and the show is credited to a curator and studio loans. Scale (hundreds of objects, first-time-shown ephemera, Travilla and Orry-Kelly costumes) gives real ambition, and the Academy Museum is a serious institution, but this is a tourist-facing blockbuster mid-run with six months left. An insider would send a visiting friend to Yoko Ono or Quarles first. |
| red flags: no independent signals despite high public profile - coverage should exist, worth checking; mid-run show (opened 93 days ago), weak timeliness hook; description reads as promotional press-release language; not really an artist-driven exhibition, more a collection/memorabilia show | red flags: no independent coverage on file; description reads close to a press release; unnamed group/collection show — no artist stature to assess; long run with 180 days remaining, no urgency this week; curator name and 'first time shown' claims unverified |

### 5. Madonna: The Rise of the Material Girl (1980–1985) - Musichead Gallery (`musichead-madonna`)

| Sonnet (overall 3, conf 0.5, $0.0060) | Opus (overall 2, conf 0.75, $0.0172) |
|---|---|
| Madonna is a canonical pop figure, but this is a photography survey by seven commercial photographers, not a Madonna-authored show, capping ambition. Musichead is a niche rock-photography retail gallery on Sunset, not a program with critical or institutional weight. No independent coverage or reviews are on file, and the description reads as press-release framing (tied to album release/Grindr partnership) rather than curatorial ambition. Mid-run timing offers modest currency. This sits well below LA's museum and top-gallery tier this week. | No signals. Musichead is a Sunset Blvd rock-photography retail gallery with celebrity-memorabilia appeal rather than an art-world program (tier unknown, effectively low). The named photographers (Mick Rock, Deborah Feingold, Richard Corman) are recognizable music-photo figures but not museum-stature subjects of critical attention; the show is unnamed-subject/collection in structure, built around Madonna's fame rather than an artistic proposition. Familiar archival prints, no new scholarship or commission. Mid-run with a month left and a topical album/Grindr tie-in gives modest urgency. Fun for a fan, not what an LA insider sends a visiting friend to this week. |
| red flags: no signals/independent coverage found; venue tier unknown/likely low (retail/commercial photography gallery); description reads as promotional tie-in to unrelated album/brand partnership | red flags: no independent coverage on file; unnamed group/collection show organized around a celebrity subject; description echoes gallery press-release framing including curator quote and album/brand tie-in; retail/memorabilia-oriented venue we have not rated |

### 6. Now We're There (And We' Only Just Begun) - Los Angeles County Museum of Art (LACMA) (`lacma-christina-quarles`)

| Sonnet (overall 6, conf 0.55, $0.0127) | Opus (overall 5, conf 0.72, $0.0239) |
|---|---|
| Quarles is a nationally significant painter with concurrent major shows abroad and at Hauser & Wirth LA [S1][S2], confirming she's having a moment - but neither signal reviews this specific LACMA installation. The show itself is a single acquired painting inaugurating a new deep-dive gallery series: institutionally notable but modest in scale, mid-run (opened 121 days ago), not urgent this week. LACMA's stature carries it, but thin, indirect evidence for this particular presentation. | Quarles is genuinely major — Whitney Biennial, Hauser & Wirth representation, international museum solos ([S1] Kistefos, [S2] the H&W LA show that arguably eclipses this) — and LACMA is the city's encyclopedic anchor. But the signals attach to her activity elsewhere, not to this presentation: no reviews or picks for it. A single recently acquired canvas inaugurating the Keck gallery is institutionally interesting yet slight in footprint, not a survey or new body of work. Four months into a seven-month run with no closing urgency. The kind of thing an insider mentions as a stop within a LACMA visit rather than a standalone recommendation this week. |
| red flags: No direct critical coverage found for this specific installation, only concurrent shows elsewhere; Mid-run show, not a fresh opening or closing-soon event | red flags: No independent coverage of this specific installation; both signals are for other exhibitions; Single-painting display — description's institutional framing reads close to press-release language; Mid-run, long-dated show with no this-week hook |

### 7. The Development Center: A Clownache Store - NOON Projects (`noon-projects-chaves`)

| Sonnet (overall 3, conf 0.55, $0.0058) | Opus (overall 4, conf 0.6, $0.0231) |
|---|---|
| Small Chung King Road storefront show; NOON Projects is an unrated, scrappy gallery, not a major program. Chaves has no evident museum/biennial profile - scores low on artist significance. Only evidence is a single listing mention [S1], not a review or critical pick, so reception stays minimal. Concept (residency/retail hybrid, in-progress installation) is mildly novel but modest in scale. Closing in 4 days gives it some timeliness, but this reads as a scrappy, low-stakes gallery event rather than a must-see. | Chaves is a familiar LA painter with a scrappy, well-liked practice (356 Mission-era orbit, Martos), but no museum-level stature; NOON Projects is a small Chung King Road storefront, respectable but minor next to the Broad/LACMA/Deitch tier in this week's pool. Only evidence is a bare weekly listing [S1], not a review or pick, so reception stays low. The live-residency, retail-hybrid format gives real novelty and the ten-day run closing in four days creates genuine urgency — a good insider tip, not a must-see anchor. Note [S1] gives a Sept 6 close vs. our Sept 5. |
| red flags: only one listing-level signal, no reviews or critical coverage; venue tier unknown/unrated; closes in 4 days - narrow window | red flags: Closing date discrepancy: listing [S1] says Sept 6, our record says Sept 5; Only signal is a calendar listing, no independent criticism; Description's press-release framing of the retail/gallery 'flattening' is unverified; Venue not in registry; small storefront space |

### 8. Artifacts from an Unborn Empire - Brand Library & Art Center (`artifacts-from-an-unborn-empire`)

| Sonnet (overall 3, conf 0.6, $0.0062) | Opus (overall 2, conf 0.72, $0.0199) |
|---|---|
| Group show with no marquee names beyond locally-known artists (Ben Cuevas, Elana Mann, Kristine Schomaker) - solid regional roster but not museum-tier. Brand Library is a modest nonprofit venue (tier 3), not a major kunsthalle. No independent coverage or press signals exist to confirm the described ambition; description reads as press-release prose. Timeliness is decent (opens soon, ties to national anniversary) but the long 3-month run dilutes urgency. Conceptually engaged and materially varied, but nothing here rises above a solid community-minded local show. | No signals, no independent coverage on file. Brand Library is a respected Glendale civic art space with a genuine community program, but it sits well below the commercial and museum venues competing for this feed (tier 3, nonprofit). The roster is a wide L.A.-based group — Elana Mann, Ben Cuevas, Rubén Esparza are locally known, none is a draw a visiting insider would be steered toward. Curatorial premise (semiquincentennial skepticism) is timely in theme but the show does not open for another 25 days, so it is not a this-week recommendation, and the anniversary moment itself has already passed. Description reads as press-release advocacy with no external validation. |
| red flags: no independent signals/coverage to verify claims; unnamed 'more than twenty' artists beyond the six named, effectively a large unnamed group show; description reads as press release | red flags: Unnamed group show — no single anchor artist; strongest names are regional; Opens 2026-09-26, not currently on view (25 days out); Description reads as press-release prose with unverified curatorial claims; No independent coverage on file |

### 9. All Unfinished - ace/121 Gallery (`ace121-all-unfinished`)

| Sonnet (overall 2, conf 0.5, $0.0055) | Opus (overall 1, conf 0.75, $0.0150) |
|---|---|
| No signals found; artist Nic Gregory has no evident national profile, and ace/121 is a small, artist-colony-style Glendale Arts space with no museum-level standing (registry tier unknown, kind: gallery). Description reads as routine press-release framing of a single-artist show in a 'modest footprint' venue; no keyword hits for retrospective, first solo, or commission. Show opens in 24 days, giving mild timeliness once live, but nothing suggests this rises above a solid but minor local gallery presentation. | No signals and no trace of Nic Gregory in national or LA critical discourse; ace/121 is a small community gallery run by Glendale Arts, not a program insiders track. The premise — process-forward acrylic panels foregrounding incompleteness — is a familiar painterly conceit with no survey scale, commission, or institutional framing to elevate it. The show has not opened yet (24 days out), so there is no current reason to send a visiting friend, and nothing in the description beyond venue-page-derived prose supports urgency. |
| red flags: no independent coverage or signals on file; venue tier unrated ('unknown') and no museum flag; hype language ('spotlight,' 'fitting complement') unsupported by evidence | red flags: opens 24 days after today — not viewable this week; no independent coverage on file; artist unplaceable in wider LA/national discourse; description reads close to venue program copy |

### 10. Pretty on the Inside - 7811 Gallery (`7811-pretty-on-the-inside`)

| Sonnet (overall 2, conf 0.6, $0.0055) | Opus (overall 1, conf 0.72, $0.0161) |
|---|---|
| No independent signals exist for this show or clear evidence of the artist's broader career stature beyond the moniker Ghost Girl. 7811 is an unrated, storefront-style community gallery, not a program with an international profile. The description's emotional framing reads as press-release prose rather than evidence of critical traction. Mid-run timing (opened 23 days ago, no urgency) offers no special hook. This fits the profile of a solid but very local, low-visibility show rather than one an insider would flag citywide. | No signals and no trace of Faidra Koutsouri / "Ghost Girl" in national or LA critical discourse. 7811 Gallery is an unrated storefront space I cannot place among LA's programmatic galleries; description itself frames it as low-key, community-facing rather than ambitious. The work described — diaristic self-portraiture on mental health — is sincere but not novel in scale or form, with no survey, commission, or institutional framing. Mid-run (opened three weeks ago, six weeks to close) gives no urgency hook. Nothing here an insider would route a visiting friend to over the Broad's Ono, LACMA's Quarles, or Bel Ami/Ghebaly. |
| red flags: no independent coverage/signals on file; venue significance unclear/unrated; claims of raw psychological depth unverified by outside sources | red flags: unplaceable venue with no registry tier; artist has no verifiable exhibition record; descriptive superlatives sourced from press release with no independent coverage |

## Full agreement table

| slug | venue | sonnet | opus | delta | max sub delta | member S/O/M | rank S/O/M | See Saw | flip | relevance |
|---|---|---|---|---|---|---|---|---|---|---|
| `7811-pretty-on-the-inside` | 7811 Gallery | 2 | 1 | -1 | 2 | N/N/N | 45/47/45 | N | - | 2.01 |
| `ace121-all-unfinished` | ace/121 Gallery | 2 | 1 | -1 | 2 | N/N/N | 41/44/44 | N | - | 2.02 |
| `artifacts-from-an-unborn-empire` | Brand Library & Art Center | 3 | 2 | -1 | 2 | N/N/N | 30/33/31 | N | - | 2.02 |
| `autry-life-liberty` | Autry Museum of the American West | 3 | 3 | +0 | 1 | N/N/N | 19/17/18 | N | - | 0.57 |
| `avenue50-sonsonetes` | Avenue 50 Studio | 2 | 2 | +0 | 2 | N/N/N | 42/40/40 | N | - | 1.02 |
| `barnsdall-threads-of-creativity` | Barnsdall Junior Arts Center Gallery | 0 | 1 | +1 | 1 | N/N/N | 48/45/47 | N | - | 1.51 |
| `bel-ami-cars-of-los-angeles` | Bel Ami | 6 | 7 | +1 | 1 | N/N/N | 20/15/17 | Y | - | 1.58 |
| `brad-eberhard-building-an-audience` | Timothy Hawkinson Gallery | 4 | 4 | +0 | 1 | N/N/N | 21/20/21 | N | - | 0.55 |
| `broad-yoko-ono` | The Broad | 9 | 9 | +0 | 1 | Y/Y/Y | 1/1/1 | N | - | 0.54 |
| `caam-gordon-parks` | California African American Museum | 7 | 7 | +0 | 2 | N/N/N | 4/4/4 | N | - | 1.06 |
| `cactus-loteria-xii` | Cactus Gallery | 1 | 1 | +0 | 1 | N/N/N | 47/48/48 | N | - | 0.51 |
| `charlie-james-gallery-lookout-weekend` | Charlie James Gallery | 4 | 5 | +1 | 1 | N/N/N | 28/25/26 | N | - | 1.53 |
| `cordillera-mexicana-louis-stern` | Louis Stern Fine Arts | 5 | 5 | +0 | 1 | N/N/N | 29/29/28 | N | - | 0.53 |
| `corey-helford-20th` | Corey Helford Gallery | 3 | 2 | -1 | 1 | N/N/N | 31/37/33 | N | - | 1.52 |
| `david-hicks-trophies` | Diane Rosenstein Gallery | 4 | 4 | +0 | 1 | N/N/N | 25/26/25 | Y | - | 0.54 |
| `deboer-salvador-dominguez` | de boer | 3 | 3 | +0 | 1 | N/N/N | 36/34/35 | N | - | 0.52 |
| `deitch-urs-fischer` | Jeffrey Deitch | 8 | 8 | +0 | 2 | N/N/N | 5/5/5 | Y | - | 1.06 |
| `f8-group-photography-show` | LA Artcore Union Center for the Arts | 1 | 1 | +0 | 1 | N/N/N | 44/42/43 | N | - | 0.52 |
| `fahey-klein-witkin` | Fahey/Klein Gallery | 6 | 5 | -1 | 3 | N/N/N | 15/18/16 | N | - | 2.60 |
| `foyer-la-gremlin` | FOYER-LA | 3 | 3 | +0 | 1 | N/N/N | 37/35/36 | N | - | 0.52 |
| `getty-odilon-redon` | Getty Center | 6 | 6 | +0 | 1 | N/N/N | 12/12/12 | N | - | 1.00 |
| `ghebaly-patrick-jackson-all-signs-fail` | François Ghebaly | 6 | 6 | +0 | 0 | N/N/N | 23/22/23 | Y | - | 0.04 |
| `hammer-space-is-the-place` | Hammer Museum | 6 | 6 | +0 | 1 | N/N/N | 3/3/3 | N | - | 0.55 |
| `induction-hallauer` | induction gallery | 3 | 2 | -1 | 1 | N/N/N | 26/30/29 | N | - | 1.53 |
| `karma-vanderlinden-fragmented-thought` | Karma | 5 | 5 | +0 | 2 | N/N/N | 24/24/24 | N | - | 1.04 |
| `keystone-vestiges-lopez-iglesias` | Keystone Art Space | 2 | 2 | +0 | 1 | N/N/N | 43/41/41 | N | - | 0.52 |
| `kk-gallery-unbound` | K&K Gallery | 1 | 1 | +0 | 1 | N/N/N | 46/46/46 | N | - | 0.51 |
| `kohn-object-poetry` | Michael Kohn Gallery | 4 | 3 | -1 | 1 | N/N/N | 17/23/20 | N | - | 1.56 |
| `lacma-christina-quarles` | Los Angeles County Museum of Art (LACMA) | 6 | 5 | -1 | 2 | N/N/N | 7/10/9 | N | - | 2.12 |
| `marc-selwyn-wegman` | Marc Selwyn Fine Art | 6 | 6 | +0 | 2 | N/N/N | 8/7/8 | N | - | 1.10 |
| `marilyn-monroe-academy-museum` | Academy Museum of Motion Pictures | 5 | 4 | -1 | 3 | N/N/N | 16/19/19 | N | - | 2.56 |
| `mb-leo-mock` | M+B | 3 | 3 | +0 | 1 | N/N/N | 34/32/34 | N | - | 0.52 |
| `milano-chow-fantasy-street` | Hollyhock House | 6 | 7 | +1 | 0 | N/N/N | 2/2/2 | N | - | 1.05 |
| `moca-expanding-field` | MOCA Grand Avenue | 4 | 4 | +0 | 1 | N/N/N | 22/21/22 | N | - | 0.55 |
| `moran-moran-tuazon` | Morán Morán | 6 | 5 | -1 | 1 | N/N/N | 13/16/14 | N | - | 1.67 |
| `musichead-madonna` | Musichead Gallery | 3 | 2 | -1 | 3 | N/N/N | 39/43/42 | N | - | 2.52 |
| `noon-projects-chaves` | NOON Projects | 3 | 4 | +1 | 2 | N/N/N | 32/27/30 | N | - | 2.03 |
| `parrasch-heijnen-charles-dickson` | Parrasch Heijnen | 6 | 6 | +0 | 1 | N/N/N | 10/8/10 | N | - | 0.67 |
| `peter-fetterman-moriyama` | Peter Fetterman Gallery | 6 | 6 | +0 | 5 | N/N/N | 14/13/13 | N | - | 2.75 |
| `philip-martin-a-certain-slant-of-light` | Philip Martin Gallery | 3 | 3 | +0 | 1 | N/N/N | 40/38/39 | N | - | 0.52 |
| `skirball-punk` | Skirball Cultural Center | 4 | 5 | +1 | 4 | N/N/N | 18/14/15 | N | - | 3.12 |
| `thinkspace-grabelsky` | Thinkspace Projects | 3 | 2 | -1 | 0 | N/N/N | 35/39/38 | N | - | 1.02 |
| `through-the-sun-cyrous` | Reisig and Taylor Contemporary | 4 | 4 | +0 | 1 | N/N/N | 27/28/27 | N | - | 0.53 |
| `tierra-craft-contemporary` | Craft Contemporary | 5 | 6 | +1 | 1 | N/N/N | 9/6/6 | N | - | 1.57 |
| `track16-john-collins` | Track 16 | 3 | 3 | +0 | 1 | N/N/N | 33/31/32 | N | - | 0.52 |
| `vielmetter-roberto-diago` | Vielmetter Los Angeles | 6 | 6 | +0 | 1 | N/N/N | 11/11/11 | N | - | 0.75 |
| `wende-sextant` | Wende Museum | 7 | 6 | -1 | 1 | N/N/N | 6/9/7 | N | - | 1.58 |
| `wonzimer-earth-elements-paint-and-fire` | Wonzimer | 3 | 3 | +0 | 1 | N/N/N | 38/36/37 | N | - | 0.52 |
