"""Agent harness: runs one Claude-driven scrape session per city.

The model (Claude Sonnet 5) drives Anthropic server-side web_search/web_fetch
tools to research current gallery shows, and client-side tools defined here to
extract image URLs from pages, download high-res images, and persist validated
show records. Every API response's token usage is metered and priced.
"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

import anthropic

import tools
from cities import CITIES

DEFAULT_MODEL = "claude-sonnet-5"

# Per-model pricing (USD per million tokens) and capabilities. web_tools
# "20260209" = the dynamic-filtering search/fetch variants (run code execution
# under the hood — the container-id echo below); "basic" = the older variants
# for models without them (Haiku 4.5).
MODELS = {
    "claude-sonnet-5": {
        "in": 2.00, "out": 10.00, "cache_write": 2.50, "cache_read": 0.20,
        "web_tools": "20260209", "supports_effort": True,
    },
    "claude-haiku-4-5": {
        "in": 1.00, "out": 5.00, "cache_write": 1.25, "cache_read": 0.10,
        "web_tools": "basic", "supports_effort": False,
    },
}
PRICE_PER_SEARCH = 10.00 / 1000.0

SPEND_DIR = tools.CONTENT_DIR / "spend"


class CostMeter:
    def __init__(self, label: str, model: str = DEFAULT_MODEL):
        self.label = label
        self.model = model
        self.prices = MODELS[model]
        self.requests = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_write_tokens = 0
        self.cache_read_tokens = 0
        self.web_searches = 0

    def record(self, usage) -> None:
        self.requests += 1
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        server = getattr(usage, "server_tool_use", None)
        if server is not None:
            self.web_searches += getattr(server, "web_search_requests", 0) or 0

    @property
    def dollars(self) -> float:
        return (
            self.input_tokens / 1e6 * self.prices["in"]
            + self.output_tokens / 1e6 * self.prices["out"]
            + self.cache_write_tokens / 1e6 * self.prices["cache_write"]
            + self.cache_read_tokens / 1e6 * self.prices["cache_read"]
            + self.web_searches * PRICE_PER_SEARCH
        )

    def summary(self) -> dict:
        return {
            "session": self.label,
            "model": self.model,
            "requests": self.requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "web_searches": self.web_searches,
            "cost_usd": round(self.dollars, 4),
        }

    def save(self) -> None:
        SPEND_DIR.mkdir(parents=True, exist_ok=True)
        (SPEND_DIR / f"{self.label}.json").write_text(json.dumps(self.summary(), indent=2))


CAMPAIGN_BLOCK = """

CAMPAIGN MODE — this session is part of a wider effort to cover this city's gallery scene:
- Prioritize the city's BEST, most popular, and most interesting venues that have exhibitions on view right now: blue-chip and institutionally notable spaces first, then well-regarded mid-size galleries. Skip obscure or weak programs.
- Distinct venues only — never save a second show from a venue that already has one saved (including the ALREADY SAVED list in the first message).
- Imagery is a hard requirement: if a show has no downloadable image at least 500px wide (ideally 1400px+), SKIP that venue entirely and move on — do not save image-poor entries and do not waste budget fighting the image validator.
- Set featured = true only for roughly the best 1 in 6 shows you save (the Featured feed is curated; the List and Map tabs carry everything). Use editors_pick sparingly for true standouts."""


EXHAUSTIVE_BLOCK = f"""

EXHAUSTIVE MODE — this session is part of a sweep of this city's ENTIRE publicly viewable art scene:
- Cover EVERY kind of space on your TODO list: blue-chip and mid-size galleries, artist-run and project spaces, nonprofits, university and photography galleries, museums. Small or obscure is GOOD — the only out-of-scope spaces are ones the public cannot walk into (private dealers and appointment-only viewing rooms with no public hours).
- Work venue by venue through the TODO list in your first message. For each venue: check its own website for the current or next exhibition; if it qualifies, save it; if it does not, call log_skip with the closest reason and one line of detail. EVERY TODO venue must end in exactly one of save_show or log_skip — no silent skips.
- One show per venue, ever: never save at a venue on the SAVED VENUES list; if a TODO venue turns out to be one already saved under another name, log_skip it with reason "duplicate".
- Imagery is a hard requirement: if a show has no downloadable image at least 500px wide (1400px+ ideal), log_skip with reason "no_image" or "low_res_only" and move on — do not fight the image validator.
- A venue between shows with a confirmed exhibition opening within the next {tools.FUTURE_SAVE_DAYS} days: SAVE that future show (it is published automatically once its opening window arrives). Between shows with nothing confirmed: log_skip with reason "closed_or_between_shows" and note any reopening info you found.
- Set featured = true for roughly the best 1 in 6 shows you save; editors_pick only for true standouts."""


def accuracy_block(horizon_days: int = 7) -> str:
    on_view = ("it is on view now (or opens within 7 days)" if horizon_days <= 7 else
               f"it is on view now or has a confirmed opening date within the next {horizon_days} days")
    return f"""

ACCURACY — NON-NEGOTIABLE:
- The venue's OWN website is ground truth. Before saving any show you must have fetched the venue's own page for that exhibition and confirmed: the exact dates, that {on_view}, the street address, and the venue's current opening hours from its visit/hours page.
- Never trust aggregators, old press coverage, or search snippets for dates, hours, addresses, or phone numbers — they are frequently stale.
- Confirm the gallery is currently operating (no closure notice, not "by appointment only" unless you record that as its hours).
- If you cannot verify the venue and its show this way, DO NOT save it — skip it and move on. An accurate shorter list beats a padded inaccurate one."""


ACCURACY_BLOCK = accuracy_block()


LANGUAGE_BLOCK = """

LANGUAGE & FORMATTING:
- Write all app text in English regardless of the city's local language.
- Artist and venue names: standard romanized form; add native script in parentheses only where genuinely helpful, e.g. "Tomio Koyama Gallery (小山登美夫ギャラリー)" — never script-only.
- Exhibition titles: use the venue's official English title when one exists; otherwise romanize and add a short English gloss.
- venue.hours: English lines in the app's format, e.g. "Tue - Sat 10:30am to 5:30pm".
- venue.address: romanized, Western display order (street/building, district, city) suitable for an English-language app. It must still be geocodable — keep the street number / chōme-banchi-gō block intact."""


def _guidance_text(cfg: dict) -> str:
    """City guidance; zone-keyed dicts render only the (possibly sharded)
    neighborhoods in cfg, so a shard never reads other zones' venue notes."""
    g = cfg["guidance"]
    if isinstance(g, str):
        return g
    parts = [g["*"]] if "*" in g else []
    parts += [f"{hood} — {g[hood]}" for hood in cfg["neighborhoods"] if hood in g]
    return " ".join(parts)


def build_system_prompt(city_key: str, cfg: dict, target_shows: int,
                        campaign: bool = False, deep: bool = False) -> str:
    if deep:
        goal = (f"GOAL: work through the venue TODO list in your first message — save a show for "
                f"every venue that qualifies (on view now, or confirmed to open within the next "
                f"{tools.FUTURE_SAVE_DAYS} days), log_skip every venue that does not. The list has "
                f"{target_shows} venues; resolving every one of them matters more than the save count.")
        step1 = ("1. For each TODO venue, go to its own website first (the TODO line lists it when "
                 "known) and find the current or next exhibition. web_search only when the site is "
                 "missing, broken, or unhelpful.")
        finish = ("- Work until every TODO venue is resolved (save_show or log_skip), then stop and "
                  "reply with a one-paragraph summary of what you saved and skipped.")
        mix = "- One show per venue, ever (see EXHAUSTIVE MODE below)."
        curation = "Follow EXHAUSTIVE MODE below for featured and editors_pick."
    else:
        goal = (f"GOAL: research and save {target_shows} notable gallery/museum exhibitions that "
                "are ON VIEW right now (or opening within the next week) in this city, each with "
                "high-resolution imagery and accurate venue facts.")
        step1 = ("1. web_search for current exhibitions (listings sites, the venue's own site, art "
                 "press). Prefer the venue's own exhibition page as ground truth for titles and dates.")
        finish = (f"- Keep going until you have saved {target_shows} shows; then stop and reply "
                  "with a one-paragraph summary of what you saved.")
        mix = ("- Mix of venues (do not save two shows from the same venue). Include at least one "
               "museum show when the city has one on view (venue.is_museum = true).")
        curation = ("Follow CAMPAIGN MODE below for featured and editors_pick." if campaign else
                    "Mark 1-2 of the strongest entries editors_pick = true. Set featured = true for all saved shows.")
    mode_block = EXHAUSTIVE_BLOCK if deep else (CAMPAIGN_BLOCK if campaign else "")
    return f"""You are an art-world research agent populating a gallery-guide iOS app.
Today is {date.today().strftime('%A, %B %d, %Y')}.

CITY: {cfg['display_name']} (city key: "{city_key}")
{goal}

CITY NOTES: {_guidance_text(cfg)}
NEIGHBORHOODS (each venue must be assigned to exactly one): {', '.join(cfg['neighborhoods'])}

WORKFLOW for each show:
{step1}
2. web_fetch the exhibition page to gather facts: exact title, artist(s), start/end dates, venue address, hours, phone, opening reception if any.
3. extract_image_urls on the exhibition page (and related pages: artwork checklists, artist pages, press pages) to find imagery. IMAGERY IS A TOP PRIORITY: aim for 4-7 images per show that together cover as much of the art in the show as possible — individual artworks especially, plus 1-2 installation views. Users will view these full screen and zoom in, so always pick the highest-resolution version of each image you can find (1400px+ wide is the bar; bigger is better).
4. download_image each candidate. The tool rejects low-resolution files and reports the pixel size of what it stored; if a version is small, hunt for the original/full-size file (og:image, srcset largest, linked originals) before settling.
5. save_show with the complete record. The slug you pass to save_show MUST match the show_slug you used for download_image.

WRITING THE DESCRIPTION — important:
- Write 2-4 original paragraphs in your own words, in the informed, plainspoken tone of a good gallery guide: what the show is, what kinds of works are in it, context about the artist, and why it's worth seeing.
- Synthesize facts from your research. Do NOT copy or lightly paraphrase the venue's press release or any article. If sources offer little text, write the description yourself from what the images and listings tell you.

QUALITY BAR:
- Real shows, with dates verified against the venue's site. Never invent shows, dates, addresses, or images.
{mix}
- {curation}
- Map pins are geocoded automatically from venue.address — never estimate coordinates; get the address exactly right from the venue's own site instead. Saved shows enter a pending pool and are displayed only after a verification pass confirms them.
{finish}
- Be efficient with searches and fetches — you have limited uses. Do not fetch the same page twice.{accuracy_block(tools.FUTURE_SAVE_DAYS if deep else 7)}{LANGUAGE_BLOCK}{mode_block}"""


CLIENT_TOOLS = [
    {
        "name": "extract_image_urls",
        "description": "Fetch a web page and list candidate image URLs found in its HTML (og:image, <img> srcset largest variants, linked image files), with alt text where available. Use this to locate high-res exhibition imagery on a page.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["url"],
            "properties": {"url": {"type": "string", "description": "Page URL to scan"}},
        },
    },
    {
        "name": "download_image",
        "description": "Download one image URL, verify it is high resolution, normalize it, and store it for the app. Returns the stored relative path and the image's original pixel size. Rejects images that are too small.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["url", "show_slug"],
            "properties": {
                "url": {"type": "string"},
                "show_slug": {"type": "string", "description": "Kebab-case show id; use the same slug for every image of one show and in save_show"},
            },
        },
    },
    {
        "name": "save_show",
        "description": "Validate and persist one completed show record. Call this once per show after its images are downloaded.",
        "strict": True,
        "input_schema": tools.SAVE_SHOW_SCHEMA,
    },
    {
        "name": "attach_images",
        "description": "Update an already-saved show so its images list includes every image downloaded for its slug (in filename order). Use after downloading additional images for an existing show.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["city", "slug"],
            "properties": {
                "city": {"type": "string"},
                "slug": {"type": "string"},
            },
        },
    },
]


# Not strict: a fifth strict tool pushes the request past the API's combined
# strict-schema complexity limit ("Schema is too complex"). The handler
# validates the fields itself instead.
LOG_SKIP_TOOL = {
    "name": "log_skip",
    "description": "Record a venue you decided NOT to save a show for, with the reason — every skipped venue must be logged so the coverage report can account for it. Call once per skipped venue.",
    "input_schema": tools.LOG_SKIP_SCHEMA,
}

RECORD_VENUE_TOOL = {
    "name": "record_venue",
    "description": "Add one venue to the city's durable venue directory. Call once per distinct venue you find while enumerating; a later pass researches its shows.",
    "strict": True,
    "input_schema": tools.RECORD_VENUE_SCHEMA,
}


def build_enumerate_prompt(city_key: str, cfg: dict, zone: str,
                           known_names: list[str]) -> str:
    g = cfg["guidance"]
    zone_notes = g.get(zone, "") if isinstance(g, dict) else g
    known = ("\nALREADY KNOWN in this zone (do NOT record these again):\n"
             + "; ".join(sorted(known_names))) if known_names else ""
    return f"""You are building a complete directory of publicly viewable art venues for a gallery-guide app.
Today is {date.today().strftime('%A, %B %d, %Y')}.

CITY: {cfg['display_name']} (city key: "{city_key}")
ZONE: {zone}{' — ' + zone_notes if zone_notes else ''}

GOAL: enumerate EVERY venue in this zone where the public can walk in and see art: commercial galleries of every size, museums, nonprofits, university galleries, photography galleries, artist-run and project spaces.

METHOD:
1. web_search broad queries ("{zone} Los Angeles art galleries", "art galleries {zone} 2026", district gallery guides, art-walk sites) and web_fetch 2-4 good directory/listing pages (e.g. Gallery Platform LA, Artsy's gallery lists, neighborhood art-walk guides). Directory pages beat individual venue sites here.
2. record_venue once per venue, with address/website when the page shows them. Do NOT fetch each venue's own site — a later pass researches shows and verifies details. Completeness beats precision: recording a venue that turns out closed is fine; MISSING one is the failure mode.
3. EXCLUDE: appointment-only private dealers with no public hours, framers/art-supply shops, tattoo/design studios, one-off pop-ups that already ended, and venues outside this zone (record only venues actually located in {zone}).
{known}

Stop when further searches stop yielding new names. Then reply with one line: how many venues you recorded."""


def build_enrich_prompt(city_key: str, cfg: dict, shows: list[dict], min_images: int) -> str:
    inventory = "\n".join(
        f"- slug: {s['slug']} | {s.get('artist') or ''} \"{s['title']}\" at {s['venue']['name']}"
        f" | currently {len(s['images'])} images | sources: {', '.join(s['source_urls'][:3])}"
        for s in shows
    )
    return f"""You are an art-world research agent improving the imagery for a gallery-guide iOS app.
Today is {date.today().strftime('%A, %B %d, %Y')}.

CITY: {cfg['display_name']} (city key: "{city_key}")
GOAL: every saved show below should end up with at least {min_images} HIGH-RESOLUTION images that together cover as much of the art in the show as possible. Users view these full screen and zoom in, so resolution and coverage both matter.

SAVED SHOWS:
{inventory}

WORKFLOW for each show that has fewer than {min_images} images:
1. extract_image_urls on its source pages, plus related pages you find (the venue's artwork/checklist pages for the show, the artist's page, press coverage). web_search / web_fetch if you need to locate better pages.
2. download_image the strongest candidates using the EXACT slug listed above. Prioritize individual artworks in the show (different works, not crops of the same one), then installation views. Always chase the highest-resolution version (1400px+ wide is the bar; the tool reports stored pixel sizes).
3. When a show's downloads are done, call attach_images with the city key and slug to update its record.

RULES:
- Only images genuinely from these shows (or works confirmed to be in them). Never pad with unrelated art.
- Do not modify anything else about the records; attach_images handles the update.
- If a show's sources simply offer no more usable high-res imagery, move on rather than lowering quality.
- Work through every show, then reply with a one-paragraph summary of what you added."""


SECOND_SOURCES = {
    "tokyo": "Tokyo Art Beat (tokyoartbeat.com)",
    "kyoto": "Tokyo Art Beat (tokyoartbeat.com)",
    "osaka": "Tokyo Art Beat (tokyoartbeat.com)",
    "los-angeles": "Artsy (artsy.net) or Gallery Platform LA",
    "new-york": "Artsy (artsy.net)",
    "london": "Artsy (artsy.net) or ArtRabbit",
    "paris": "Artsy (artsy.net)",
    "berlin": "Artsy (artsy.net) or Index Berlin",
    "seattle": "Artsy (artsy.net) or visitseattle/art guides",
    "venice": "the Biennale/institutional listings",
}


def _crosscheck_line(city_key: str, slug: str, crosscheck: dict) -> str:
    e = crosscheck.get(f"{city_key}/{slug}")
    if not e:
        return ""
    parts = []
    g = e.get("google")
    if g:
        if g.get("found"):
            parts.append(f"Google Maps: {g.get('status')}, addr \"{g.get('address')}\", "
                         f"hours {g.get('hours')}, phone {g.get('phone')}, "
                         f"{g.get('distance_m', '?')}m from saved coords")
        elif g.get("found") is False:
            parts.append("Google Maps: NOT FOUND")
    o = e.get("osm", {})
    if o.get("found"):
        parts.append(f"OSM geocode {o.get('distance_m', '?')}m from saved coords")
    if e.get("flags"):
        parts.append("FLAGS: " + ", ".join(e["flags"]))
    return ("\n    cross-check -> " + " | ".join(parts)) if parts else ""


def build_verify_prompt(city_key: str, cfg: dict, shows: list[dict]) -> str:
    crosscheck = {}
    cc_path = tools.CONTENT_DIR / "spend" / "crosscheck.json"
    if cc_path.exists():
        crosscheck = json.loads(cc_path.read_text())
    inventory = "\n".join(
        f"- slug: {s['slug']} | {s.get('artist') or ''} \"{s['title']}\" at {s['venue']['name']}"
        f" | {s['start_date']}..{s['end_date']} | {s['venue']['address']}"
        f" | hours: {' / '.join(s['venue']['hours'])} | phone: {s['venue'].get('phone')}"
        f" | site: {s['venue'].get('website')} | sources: {', '.join(s['source_urls'][:2])}"
        + _crosscheck_line(city_key, s['slug'], crosscheck)
        for s in shows
    )
    second_source = SECOND_SOURCES.get(city_key, "Artsy (artsy.net)")
    return f"""You are an art-world fact checker auditing a gallery guide's saved records.
Today is {date.today().strftime('%A, %B %d, %Y')}.

CITY: {cfg['display_name']} (city key: "{city_key}")
GOAL: verify every show below against PRIMARY sources — above all the venue's own website. Accuracy outranks retention: a wrong listing is worse than a missing one, and anything you cannot confirm must be reported unverified so the app can drop it.

SAVED SHOWS:
{inventory}

For EACH show, in order:
1. web_fetch the venue's own page for the exhibition (start from the listed site/source URLs; web_search only when those fail).
2. Confirm ALL of the following:
   - The venue exists, is currently operating at the listed street address (no closure notice).
   - The exhibition is real and its dates are right; it is on view today or opens within 7 days. The venue's own site outranks any aggregator or press article.
   - The venue's CURRENT posted opening hours (check its visit/hours page) match the saved hours; correct them if they differ.
   - Phone and website are correct.
3. Then call confirm_show exactly once for that show:
   - status "verified": everything checked out against the venue's own site.
   - status "corrected": the show is real and current but you fixed one or more fields (fill only the changed fields in corrections; null for the rest; say what changed in reason).
   - status "unverified": you could NOT confirm the show on the venue's own site (or another authoritative primary source when the venue site is genuinely broken), OR the venue appears closed, OR the show has already ended. Give a one-line reason.

CROSS-CHECK RULES (each show may carry a "cross-check ->" line of Google Maps / OSM data gathered today):
- Google businessStatus CLOSED_PERMANENTLY or CLOSED_TEMPORARILY: mark the show unverified, unless the venue's own site currently and explicitly says it is open (explain in reason).
- Google NOT FOUND, a COORDS_OFF flag, or COORDS_UNRESOLVED: apply extra scrutiny — confirm the address on the venue's own site and correct it if it differs (pins are geocoded automatically from the confirmed address; there is no coordinates field to correct). For galleries with multiple spaces, make sure the address is the space hosting THIS show.
- Hours: the venue's own current visit/hours page is primary. If the venue site posts no hours, adopt Google's hours as the correction. If the two conflict, prefer the venue site only when its page is demonstrably current; note the conflict in reason.
- Second source for the SHOW itself: besides the venue's own site, look for it on {second_source} or the venue's official press page; note in reason when a show rests on the venue site alone.

RULES:
- Never invent data; corrections must come from the venue's own pages (or Google Maps data above, for hours as described).
- A show that appears only on aggregators/press but not the venue's own site is unverified, unless the venue site is clearly broken or JS-only AND two independent authoritative sources agree on the details.
- A real, confirmed show that has not opened yet is "verified" (or "corrected"), never "unverified" — publication timing (the on-view/opening-soon window) is enforced automatically after your verdict.
- Do not modify anything except through confirm_show. Work through every show, then reply with one line: counts of verified / corrected / unverified."""


VERIFY_TOOLS = [
    {
        "name": "confirm_show",
        "description": "Record the verification verdict for one saved show, optionally applying field corrections sourced from the venue's own site. Call exactly once per show.",
        "strict": True,
        "input_schema": tools.CONFIRM_SHOW_SCHEMA,
    },
]


def run_city(city_key: str, target_shows: int, max_searches: int, max_fetches: int,
             max_iterations: int, budget_usd: float, api_key: str | None = None,
             enrich_min_images: int | None = None, neighborhoods: list[str] | None = None,
             campaign: bool = False, verify: bool = False,
             verify_pending_only: bool = False,
             model: str = DEFAULT_MODEL, deep: bool = False,
             first_user_message: str | None = None,
             enumerate_zone: str | None = None,
             fetch_content_tokens: int = 20000,
             effort: str | None = None,
             context_editing: bool = False,
             session_label: str | None = None) -> dict:
    cfg = dict(CITIES[city_key])
    if neighborhoods:
        bad = [n for n in neighborhoods if n not in cfg["neighborhoods"]]
        if bad:
            raise ValueError(f"unknown neighborhoods for {city_key}: {bad} "
                             f"(valid: {cfg['neighborhoods']})")
        # save_show validates against this list, so the shard is hard-enforced
        cfg["neighborhoods"] = list(neighborhoods)
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    mcfg = MODELS[model]
    meter = CostMeter(
        session_label or f"{'verify-' if verify else ''}{city_key}-{int(time.time())}",
        model=model)
    shows_before = len(tools.all_city_shows(city_key))

    if mcfg["web_tools"] == "20260209":
        server_tools = [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches},
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": max_fetches,
             "max_content_tokens": fetch_content_tokens},
        ]
    else:  # models without the 20260209 tools (Haiku 4.5) use the basic variants
        server_tools = [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": max_searches},
            {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": max_fetches,
             "max_content_tokens": fetch_content_tokens},
        ]
    if verify:
        all_tools = server_tools + VERIFY_TOOLS
    elif enumerate_zone:
        all_tools = server_tools + [RECORD_VENUE_TOOL]
    elif deep:
        # attach_images is enrich-only; dropping it keeps the strict-schema
        # budget under the limit alongside the non-strict log_skip
        all_tools = server_tools + [t for t in CLIENT_TOOLS
                                    if t["name"] != "attach_images"] + [LOG_SKIP_TOOL]
    else:
        all_tools = server_tools + CLIENT_TOOLS

    def execute(name: str, args: dict) -> str:
        if name == "extract_image_urls":
            return tools.extract_image_urls(args["url"])
        if name == "download_image":
            return tools.download_image(args["url"], city_key, args["show_slug"])
        if name == "save_show":
            return tools.save_show(args, city_key, cfg["neighborhoods"])
        if name == "attach_images":
            return tools.attach_images(city_key, args["slug"])
        if name == "confirm_show":
            return tools.confirm_show(args, city_key)
        if name == "log_skip":
            return tools.log_skip(args, city_key, meter.label)
        if name == "record_venue":
            return tools.record_venue(args, city_key, cfg["neighborhoods"], meter.label)
        raise ValueError(f"unknown tool {name}")

    if verify:
        saved_shows = (tools._load_shows_file(tools._pending_file(city_key))["shows"]
                       if verify_pending_only else tools.all_city_shows(city_key))
        saved_shows = [s for s in saved_shows if not tools.show_expired(s)]
        if verify_pending_only:
            verdicts_now = tools.latest_verdicts()
            saved_shows = [s for s in saved_shows
                           if not tools.awaiting_window_only(city_key, s, verdicts_now)]
        if neighborhoods:
            saved_shows = [s for s in saved_shows if s["venue"]["neighborhood"] in neighborhoods]
        system = build_verify_prompt(city_key, cfg, saved_shows)
        messages = [{
            "role": "user",
            "content": f"Audit the {len(saved_shows)} saved {cfg['display_name']} shows "
                       "following your instructions.",
        }]
    elif enrich_min_images is not None:
        saved_shows = tools.all_city_shows(city_key)
        system = build_enrich_prompt(city_key, cfg, saved_shows, enrich_min_images)
        messages = [{
            "role": "user",
            "content": f"Enrich the imagery for the saved {cfg['display_name']} shows "
                       "following your instructions.",
        }]
    elif enumerate_zone:
        known = sorted(
            {v["name"] for v in tools.load_directory(city_key).values()
             if v["neighborhood"] == enumerate_zone}
            | {s["venue"]["name"] for s in tools.all_city_shows(city_key)
               if s["venue"]["neighborhood"] == enumerate_zone})
        system = build_enumerate_prompt(city_key, cfg, enumerate_zone, known)
        messages = [{
            "role": "user",
            "content": f"Enumerate the {enumerate_zone} zone of {cfg['display_name']} "
                       "following your instructions.",
        }]
    else:
        system = build_system_prompt(city_key, cfg, target_shows,
                                     campaign=campaign, deep=deep)
        if first_user_message is not None:
            messages = [{"role": "user", "content": first_user_message}]
        else:
            existing_note = ""
            prior = tools.all_city_shows(city_key)  # published + pending
            if prior:
                lines = "\n".join(f'- "{s["title"]}" at {s["venue"]["name"]}' for s in prior)
                existing_note = (
                    f"\n\nALREADY SAVED for this city (do NOT research or re-save these shows, "
                    f"and do NOT save any show at these venues — add {target_shows} NEW shows "
                    f"at OTHER venues only):\n{lines}"
                )
            messages = [{
                "role": "user",
                "content": f"Populate the {cfg['display_name']} section: research and save "
                           f"{target_shows} current shows following your instructions." + existing_note,
            }]

    nudged = False
    final_text = ""
    container_id = None
    stop_reason = "not_started"
    for iteration in range(max_iterations):
        def create(cid: str | None):
            kwargs: dict = {
                "model": model,
                "max_tokens": 8000,
                "system": system,
                "messages": messages,
                "tools": all_tools,
                "cache_control": {"type": "ephemeral"},
            }
            if cid:
                kwargs["container"] = cid
            if effort and mcfg["supports_effort"]:
                kwargs["output_config"] = {"effort": effort}
            if mcfg["web_tools"] == "basic":
                # the basic web_fetch variant still gates on its original beta flag
                kwargs["extra_headers"] = {"anthropic-beta": "web-fetch-2025-09-10"}
            if context_editing:
                kwargs["context_management"] = {
                    "edits": [{"type": "clear_tool_uses_20250919"}]}
                kwargs["betas"] = ["context-management-2025-06-27"]
                return client.beta.messages.create(**kwargs)
            return client.messages.create(**kwargs)

        def create_with_retry(cid: str | None):
            # transient failures (connection drops, 429s, 5xx) get 3 attempts;
            # anything else — including the container-expiry 400 handled by the
            # caller — propagates immediately
            for attempt in range(3):
                try:
                    return create(cid)
                except anthropic.APIConnectionError as exc:
                    if attempt == 2:
                        raise
                    err = type(exc).__name__
                except anthropic.APIStatusError as exc:
                    if attempt == 2 or not (exc.status_code == 429 or exc.status_code >= 500):
                        raise
                    err = f"{type(exc).__name__} {exc.status_code}"
                wait = 5 * (2 ** attempt)
                print(f"  [{city_key}] transient API error ({err}); retrying in {wait}s",
                      flush=True)
                time.sleep(wait)

        try:
            response = create_with_retry(container_id)
        except anthropic.BadRequestError as exc:
            if container_id and "container" in str(exc).lower():
                container_id = None  # expired container — retry without it
                response = create_with_retry(None)
            else:
                raise
        meter.record(response.usage)
        meter.save()
        # The 20260209 web tools run code execution under the hood; continuing a
        # turn with pending code-exec tool uses requires echoing the container id.
        container = getattr(response, "container", None)
        if container is not None and getattr(container, "id", None):
            container_id = container.id

        for block in response.content:
            if block.type == "text" and block.text.strip():
                final_text = block.text.strip()
                print(f"  [{city_key} #{iteration}] {final_text[:200]}")
            elif block.type == "server_tool_use":
                print(f"  [{city_key} #{iteration}] server:{block.name} {json.dumps(block.input)[:140]}")
            elif block.type == "tool_use":
                print(f"  [{city_key} #{iteration}] tool:{block.name} {json.dumps(block.input)[:140]}")

        stop_reason = response.stop_reason
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue

        if response.stop_reason == "refusal":
            print(f"  [{city_key}] refusal: {response.stop_details}")
            tools.log_event({"session": meter.label, "city": city_key, "kind": "refusal",
                             "detail": str(response.stop_details)})
            break

        if response.stop_reason != "tool_use":
            break  # end_turn / max_tokens — session over

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            try:
                out = execute(block.name, block.input)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
            except Exception as exc:
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": f"Error: {exc}", "is_error": True})
                ctx = {k: block.input[k] for k in ("slug", "show_slug", "url")
                       if isinstance(block.input.get(k), str)}
                v = block.input.get("venue")
                if isinstance(v, dict) and isinstance(v.get("name"), str):
                    ctx["venue"] = v["name"]
                elif isinstance(v, str):
                    ctx["venue"] = v
                tools.log_event({"session": meter.label, "city": city_key,
                                 "kind": "tool_error", "tool": block.name,
                                 "error": str(exc)[:400], **ctx})
        messages.append({"role": "user", "content": results})

        if meter.dollars > budget_usd and not nudged:
            nudged = True
            tools.log_event({"session": meter.label, "city": city_key,
                             "kind": "budget_nudge", "spent": round(meter.dollars, 4)})
            messages.append({
                "role": "user",
                "content": "SYSTEM BUDGET NOTICE: your research budget is nearly exhausted. "
                           "Immediately save_show every show you have enough material for, "
                           "then stop and summarize.",
            })
        if meter.dollars > budget_usd * 1.5:
            print(f"  [{city_key}] hard budget stop at ${meter.dollars:.2f}")
            tools.log_event({"session": meter.label, "city": city_key,
                             "kind": "budget_hard_stop", "spent": round(meter.dollars, 4)})
            stop_reason = "budget_hard_stop"
            break

    meter.save()
    shows_total = len(tools.all_city_shows(city_key))
    shows_added = shows_total - shows_before
    tools.log_event({"session": meter.label, "city": city_key, "kind": "session_end",
                     "stop_reason": stop_reason, "shows_added": shows_added,
                     "cost_usd": round(meter.dollars, 4)})
    return {"city": city_key, "shows_saved": shows_total, "shows_added": shows_added,
            "final_message": final_text, **meter.summary()}
