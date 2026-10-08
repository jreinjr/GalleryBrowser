"""Agent harness: runs one Claude-driven scrape session per city.

The model (Claude Sonnet 5) drives Anthropic server-side web_search/web_fetch
tools to research current gallery shows, and client-side tools defined here to
extract image URLs from pages, download high-res images, and persist validated
show records. Every API response's token usage is metered and priced.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from pathlib import Path

import anthropic

import tools
import curation_store
from cities import CITIES

DEFAULT_MODEL = "claude-sonnet-5"
# Output cap per response. Exhaustive sessions batch dozens of tool calls in
# one turn (an enumeration hit 23K output tokens); 8000 truncated them.
MAX_TOKENS = 16000

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
    # Judge A/B arm (curation); not used for scraping (user preference: Sonnet).
    "claude-opus-5": {
        "in": 5.00, "out": 25.00, "cache_write": 6.25, "cache_read": 0.50,
        "web_tools": "20260209", "supports_effort": True,
    },
}
PRICE_PER_SEARCH = 10.00 / 1000.0

SPEND_DIR = tools.CONTENT_DIR / "spend"


class CostMeter:
    def __init__(self, label: str, model: str = DEFAULT_MODEL, batch: bool = False):
        self.label = label
        self.model = model
        self.batch = batch  # Message Batches API: 50% off every token price
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
        tokens = (
            self.input_tokens / 1e6 * self.prices["in"]
            + self.output_tokens / 1e6 * self.prices["out"]
            + self.cache_write_tokens / 1e6 * self.prices["cache_write"]
            + self.cache_read_tokens / 1e6 * self.prices["cache_read"]
        )
        return tokens * (0.5 if self.batch else 1.0) + self.web_searches * PRICE_PER_SEARCH

    def summary(self) -> dict:
        return {
            "session": self.label,
            "model": self.model,
            "batch": self.batch,
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
- Spread saves across venues, but a venue running several concurrent exhibitions may contribute more than one; never re-save a show on the ALREADY SAVED list.
- Imagery is a hard requirement: if a show has no downloadable image at least 500px wide (ideally 1400px+), SKIP that venue entirely and move on — do not save image-poor entries and do not waste budget fighting the image validator.
- Set featured = true only for roughly the best 1 in 6 shows you save (the Featured feed is curated; the List and Map tabs carry everything). Use editors_pick sparingly for true standouts."""


EXHAUSTIVE_BLOCK = f"""

EXHAUSTIVE MODE — this session is part of a sweep of this city's ENTIRE publicly viewable art scene:
- Cover EVERY kind of space on your TODO list: blue-chip and mid-size galleries, artist-run and project spaces, nonprofits, university and photography galleries, museums. Small or obscure is GOOD — the only out-of-scope spaces are ones the public cannot walk into (private dealers and appointment-only viewing rooms with no public hours).
- Work venue by venue through the TODO list in your first message. For each venue: open its own exhibitions page and save EVERY distinct exhibition it lists that is on view now or opens within {tools.FUTURE_SAVE_DAYS} days — multi-room galleries and museums often run several at once, and each is its own save_show with its own slug and images. If nothing qualifies, call log_skip with the closest reason and one line of detail. EVERY TODO venue must end in at least one save_show of a show on view NOW, or exactly one log_skip — no silent skips. The CURRENT show is mandatory; an upcoming show is saved in addition to it, never instead of it, and a save of an UPCOMING show alone does not resolve the venue.
- Rotating exhibitions only: skip permanent-collection displays, long-term installations with no end date, gift-shop or online-only presentations.
- A TODO venue may list shows ALREADY SAVED (shown under it): do not re-save those; save its other current shows. If a TODO venue turns out to be the same physical space as one already listed under another name, log_skip it with reason "duplicate".
- A confirmed current or upcoming show whose venue page publishes no closing date STILL qualifies: save it with end_date null and a dates_note quoting the page (it is held in pending, not displayed, until the date is filled). Never log_skip such a show as unverifiable and never invent a date.
- RENDER_FETCH: if web_fetch of a venue's exhibitions or show page returns no exhibition content, or exhibition text with no dates, call render_fetch on that URL once before deciding — many gallery sites render dates with JavaScript.
- A TODO venue tagged CANDIDATE was discovered automatically from a fetched domain and is unconfirmed: first confirm it is a public art venue located in this zone; if not, log_skip it with reason "out_of_scope".
- Imagery is a hard requirement: if a show has no downloadable image at least 500px wide (1400px+ ideal), log_skip with reason "no_image" or "low_res_only" and move on — do not fight the image validator.
- A venue between shows with a confirmed exhibition opening within the next {tools.FUTURE_SAVE_DAYS} days: SAVE that future show (it is published automatically once its opening window arrives) AND ALSO log_skip the venue closed_or_between_shows noting the opening date, so the record says nothing is on view now. Between shows with nothing confirmed: log_skip with reason "closed_or_between_shows" and note any reopening info you found.
- Set featured = true for roughly the best 1 in 6 shows you save; editors_pick only for true standouts."""


def _fetch_text(block) -> tuple[str | None, str | None]:
    """(url, plain text) from a web_fetch_tool_result block, else (url, None)."""
    c = getattr(block, "content", None)
    if c is None or getattr(c, "type", "") != "web_fetch_result":
        return None, None
    url = getattr(c, "url", None)
    src = getattr(getattr(c, "content", None), "source", None)
    data = getattr(src, "data", None)
    if isinstance(data, str) and getattr(src, "type", "") == "text":
        return url, data
    return url, None


def _search_results(block, server_inputs: dict) -> dict | None:
    """{query, results[{url,title,page_age}]} from a web_search_tool_result."""
    c = getattr(block, "content", None)
    if not isinstance(c, list):
        return None  # error object, not a result list
    q = (server_inputs.get(getattr(block, "tool_use_id", ""), {}) or {}).get("query")
    return {"query": q, "results": [
        {"url": r.url, "title": r.title, "page_age": getattr(r, "page_age", None)}
        for r in c if getattr(r, "type", "") == "web_search_result"]}


PAUSE_RE = re.compile(r"next (turn|message|reply)|resume|continue (in|with)|call limit|"
                      r"tool limit|rate limit", re.I)


def _resolution_nudge_text(pending: list[tuple[dict, str]]) -> str:
    """User turn listing TODO venues the deep-session contract still considers
    open (venues.SessionTrace.unresolved)."""
    lines = []
    for v, status in pending:
        if status == "upcoming_only":
            lines.append(f"- {v['name']} (only an upcoming show saved — what is on view NOW? save "
                         "it, or log_skip closed_or_between_shows with the reopening date)")
        else:
            lines.append(f"- {v['name']} (nothing saved or skipped)")
    return ("These TODO venues are unresolved:\n" + "\n".join(lines)
            + "\n\nResolve each one now with save_show or log_skip, then stop with your summary. "
              "If you already resolved one of them under a different venue name, say which in one line.")


def accuracy_block(horizon_days: int = 7) -> str:
    on_view = ("it is on view now (or opens within 7 days)" if horizon_days <= 7 else
               f"it is on view now or has a confirmed opening date within the next {horizon_days} days")
    return f"""

ACCURACY — NON-NEGOTIABLE:
- The venue's OWN website is ground truth. Before saving any show you must have fetched the venue's own page for that exhibition and confirmed: the dates the venue publishes (exact when it gives them), that {on_view}, the street address, and the venue's current opening hours from its visit/hours page.
- If the venue's OWN page confirms the show is on view now (or gives its opening date) but publishes no closing date — including when dates only render via JavaScript and render_fetch still shows none — SAVE it with end_date null (start_date null too if unknown) and a one-line dates_note quoting the page. Do NOT log_skip it as unverifiable, and never guess a date; a later pass fills the closing date.
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
        goal = (f"GOAL: work through the venue TODO list in your first message — for every venue, "
                f"save each show on view NOW (plus any confirmed show opening within the next "
                f"{tools.FUTURE_SAVE_DAYS} days), and log_skip every venue with nothing on view. "
                f"The list has {target_shows} venues; resolving every one of them matters more "
                f"than the save count.")
        step1 = ("1. For each TODO venue, go to its own website first (the TODO line lists it when "
                 "known) and find the current or next exhibition. web_search only when the site is "
                 "missing, broken, or unhelpful.")
        finish = ("- Work until every TODO venue is resolved (save_show or log_skip), then stop and "
                  "reply with a one-paragraph summary of what you saved and skipped.")
        mix = "- Multiple shows per venue are expected — save every qualifying exhibition a TODO venue lists (see EXHAUSTIVE MODE below)."
        curation = "Follow EXHAUSTIVE MODE below for featured and editors_pick."
    else:
        goal = (f"GOAL: research and save {target_shows} notable gallery/museum exhibitions that "
                "are ON VIEW right now (or opening within the next week) in this city, each with "
                "high-resolution imagery and accurate venue facts.")
        step1 = ("1. web_search for current exhibitions (listings sites, the venue's own site, art "
                 "press). Prefer the venue's own exhibition page as ground truth for titles and dates.")
        finish = (f"- Keep going until you have saved {target_shows} shows; then stop and reply "
                  "with a one-paragraph summary of what you saved.")
        mix = ("- Mix of venues (spread saves across venues; a venue with several concurrent "
               "shows may contribute more than one). Include at least one museum show when "
               "the city has one on view (venue.is_museum = true).")
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
- Write about the SHOW, not the gallery: do not describe the gallery's history, program, roster, founders or space in the show description — the venue record carries that. One sentence of venue context is fine only when it explains the show (e.g. the show inaugurates a new space).

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

RENDER_FETCH_TOOL = {   # non-strict (strict-schema complexity budget)
    "name": "render_fetch",
    "description": "Load a page in a headless browser and return its rendered text plus every date string on it — including dates hidden in script data that web_fetch strips. Use once per page when web_fetch shows no exhibition content or exhibition text without dates.",
    "input_schema": {
        "type": "object",
        "required": ["url"],
        "properties": {"url": {"type": "string", "description": "Page URL to render"}},
    },
}

RECORD_VENUE_TOOL = {
    "name": "record_venue",
    "description": "Add one venue to the city's durable venue directory. Call once per distinct venue you find while enumerating; a later pass researches its shows.",
    "strict": True,
    "input_schema": tools.RECORD_VENUE_SCHEMA,
}


def build_enumerate_prompt(city_key: str, cfg: dict, zone: str,
                           known_names: list[str],
                           missing_anchors: list[str] | None = None) -> str:
    g = cfg["guidance"]
    zone_notes = g.get(zone, "") if isinstance(g, dict) else g
    known = ("\nALREADY KNOWN in this zone (do NOT record these again, and do NOT web_search "
             "for them — record_venue rejects them anyway):\n"
             + "; ".join(sorted(known_names))) if known_names else ""
    areas = ((cfg.get("zones") or {}).get(zone) or {}).get("areas") or []
    plan = ("\nSEARCH PLAN — run each of these once, then the directory fetches:\n"
            + "\n".join(f'- "art galleries {a} {cfg["display_name"]}"' for a in areas)) if areas else ""
    missing = ("\nKNOWN VENUES NOT FOUND YET — find each one (its address/website) and the "
               "venues around it:\n- " + "\n- ".join(missing_anchors)) if missing_anchors else ""
    dirs = cfg.get("directories") or []
    dir_hint = ("e.g. " + ", ".join(dirs) + ", neighborhood art-walk guides"
                if dirs else "city gallery directories, neighborhood art-walk guides")
    return f"""You are building a complete directory of publicly viewable art venues for a gallery-guide app.
Today is {date.today().strftime('%A, %B %d, %Y')}.

CITY: {cfg['display_name']} (city key: "{city_key}")
ZONE: {zone}{' — ' + zone_notes if zone_notes else ''}

GOAL: enumerate EVERY venue in this zone where the public can walk in and see art: commercial galleries of every size, museums, nonprofits, university galleries, photography galleries, artist-run and project spaces.

METHOD:
1. web_search broad queries ("{zone} {cfg['display_name']} art galleries", "art galleries {zone} {date.today().year}", district gallery guides, art-walk sites) and web_fetch at least 3 good directory/listing pages ({dir_hint}) BEFORE your first record_venue. Directory pages beat individual venue sites here.
2. record_venue once per venue, with address/website when the page shows them. Do NOT fetch each venue's own site — a later pass researches shows and verifies details. Completeness beats precision: recording a venue that turns out closed is fine; MISSING one is the failure mode.
3. web_search is for discovering NEW names only — never search a name you have already recorded or one on the ALREADY KNOWN list, and never run the same query twice.
4. EXCLUDE: appointment-only private dealers with no public hours, framers/art-supply shops, tattoo/design studios, one-off pop-ups that already ended, and venues outside this zone (record only venues actually located in {zone}).
{plan}{missing}{known}

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
    # quick-city expansion (quick_city.py)
    "hong-kong": "Artsy (artsy.net) or Ocula (ocula.com)",
    "seoul": "Artsy (artsy.net) or Ocula (ocula.com)",
    "shanghai": "Ocula (ocula.com) or Artsy (artsy.net)",
    "mexico-city": "Artsy (artsy.net)",
    "brussels": "Artsy (artsy.net) or Contemporary Art Daily",
    "milan": "Artsy (artsy.net) or ATP Diary",
    "chicago": "Artsy (artsy.net) or Newcity Art",
    "san-francisco": "Artsy (artsy.net) or SF/Arts",
    "miami": "Artsy (artsy.net)",
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


VENUE_VERIFIED_FRESH_DAYS = 30   # a venue-level verification this recent skips venue checks


def venue_verified_date(city_key: str, show: dict, registry: dict | None = None,
                        today: date | None = None) -> str | None:
    """ISO date of the show's venue's deterministic verification
    (validate_venues.py -> registry `verification`) when it is "verified" and
    younger than VENUE_VERIFIED_FRESH_DAYS, else None. The verify agent then
    checks the SHOW only (docs/GALLERIES.md, stage S2)."""
    vid = show.get("venue_id")
    if not vid:
        return None
    if registry is None:
        import venues
        registry = venues.index_by_id(venues.load_registry(city_key))
    ver = (registry.get(vid) or {}).get("verification") or {}
    if ver.get("status") != "verified" or not ver.get("ts"):
        return None
    today = today or date.today()
    when = date.fromtimestamp(int(ver["ts"]))
    if (today - when).days > VENUE_VERIFIED_FRESH_DAYS:
        return None
    return when.isoformat()


def build_verify_prompt(city_key: str, cfg: dict, shows: list[dict]) -> str:
    crosscheck = {}
    cc_path = tools.CONTENT_DIR / "spend" / "crosscheck.json"
    if cc_path.exists():
        crosscheck = json.loads(cc_path.read_text())
    try:
        import venues
        registry = venues.index_by_id(venues.load_registry(city_key))
    except Exception:  # registry is optional for verification
        registry = {}
    verified_on = {s["slug"]: venue_verified_date(city_key, s, registry) for s in shows}

    def _venue_bits(s: dict) -> str:
        d = verified_on.get(s["slug"])
        if d:
            return (f" | VENUE FACTS VERIFIED {d} — check the SHOW only (dates, on view, second "
                    "source); do not re-check hours/phone/website/address")
        return (f" | {s['venue']['address']}"
                f" | hours: {' / '.join(s['venue']['hours'])} | phone: {s['venue'].get('phone')}")

    inventory = "\n".join(
        f"- slug: {s['slug']} | {s.get('artist') or ''} \"{s['title']}\" at {s['venue']['name']}"
        f" | {s.get('start_date') or '?'}..{s.get('end_date') or 'END UNKNOWN'}"
        + (f" | dates_note: {s['dates_note']}" if s.get('dates_note') else "")
        + _venue_bits(s)
        + f" | site: {s['venue'].get('website')} | sources: {', '.join(s['source_urls'][:2])}"
        + ("" if verified_on.get(s["slug"]) else _crosscheck_line(city_key, s['slug'], crosscheck))
        for s in shows
    )
    n_venue_verified = sum(1 for d in verified_on.values() if d)
    venue_rule = ("" if not n_venue_verified else
                  "\nVENUE FACTS ALREADY VERIFIED: a show whose line says VENUE FACTS VERIFIED had its "
                  "venue (existence, address, hours, phone, website) confirmed deterministically "
                  "within the last 30 days. For those shows skip step 2's venue checks entirely — "
                  "spend no fetch or search on hours/phone/website/address — and verify only the "
                  "exhibition itself (real, dates right, on view or opening within 7 days, second "
                  "source). Still report a venue correction if the exhibition page itself shows the "
                  "venue moved or closed.\n")
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
{venue_rule}
CROSS-CHECK RULES (each show may carry a "cross-check ->" line of Google Maps / OSM data gathered today):
- Google businessStatus CLOSED_PERMANENTLY or CLOSED_TEMPORARILY: mark the show unverified, unless the venue's own site currently and explicitly says it is open (explain in reason).
- Google NOT FOUND, a COORDS_OFF flag, or COORDS_UNRESOLVED: apply extra scrutiny — confirm the address on the venue's own site and correct it if it differs (pins are geocoded automatically from the confirmed address; there is no coordinates field to correct). For galleries with multiple spaces, make sure the address is the space hosting THIS show.
- Hours: the venue's own current visit/hours page is primary. If the venue site posts no hours, adopt Google's hours as the correction. If the two conflict, prefer the venue site only when its page is demonstrably current; note the conflict in reason.
- Second source for the SHOW itself: besides the venue's own site, look for it on {second_source} or the venue's official press page; note in reason when a show rests on the venue site alone.

DATE FILL (shows whose inventory line says END UNKNOWN or '?'):
1. web_fetch the venue's own page for the show; if it shows no dates or looks JS-rendered, call render_fetch on it once and read date_strings (script payloads often carry exact ISO dates).
2. If the venue page still gives no closing date, look for the show on {second_source}: two independent listings agreeing on the same dates count as confirmation.
3. Found: status "corrected" with corrections.end_date (and start_date if it was '?'); say the source in reason.
4. Still unknown but the venue page confirms the show is on view: status "verified", corrections null, reason "venue site still lists no closing date" — it stays held. NEVER estimate a date.

RULES:
- Never invent data; corrections must come from the venue's own pages (or Google Maps data above, for hours as described).
- A show that appears only on aggregators/press but not the venue's own site is unverified, unless the venue site is clearly broken or JS-only AND two independent authoritative sources agree on the details.
- A real, confirmed show that has not opened yet is "verified" (or "corrected"), never "unverified" — publication timing (the on-view/opening-soon window) is enforced automatically after your verdict.
- Do not modify anything except through confirm_show. Work through every show, then reply with one line: counts of verified / corrected / unverified."""


RECORD_SIGNAL_TOOL = {
    "name": "record_signal",
    "description": "Record one press/curatorial signal about one show (or one venue-level "
                   "fair signal) into the curation evidence store. One call per (source page, "
                   "show) mention. Returns whether it matched a pooled show or became a "
                   "scrape-gap candidate.",
    "strict": True,
    "input_schema": curation_store.RECORD_SIGNAL_SCHEMA,
}
# Non-strict twin for deep scrape sessions (strict-schema complexity budget);
# the handler validates.
RECORD_SIGNAL_TOOL_LOOSE = {k: v for k, v in RECORD_SIGNAL_TOOL.items() if k != "strict"}


VERIFY_TOOLS = [
    {
        "name": "confirm_show",
        "description": "Record the verification verdict for one saved show, optionally applying field corrections sourced from the venue's own site. Call exactly once per show.",
        "strict": True,
        "input_schema": tools.CONFIRM_SHOW_SCHEMA,
    },
    RENDER_FETCH_TOOL,
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
             session_label: str | None = None,
             signal_variant: str | None = None,
             signal_ctx: dict | None = None,
             search_domains: dict | None = None,
             keyword_signals: bool = False,
             missing_anchors: list[str] | None = None,
             todo_venues: list[dict] | None = None,
             verify_slugs: list[str] | None = None) -> dict:
    """Run one agent session. Modes (first match wins): verify, enrich,
    enumerate_zone, signal_variant (curation signal collection), else scrape
    (deep when `deep`). `search_domains` = {"allowed_domains": [...]} or
    {"blocked_domains": [...]} applied to both server web tools.
    `keyword_signals` adds the non-strict record_signal tool + prompt block to
    deep sessions so agents log significance claims they already read."""
    cfg = dict(CITIES[city_key])
    if neighborhoods:
        bad = [n for n in neighborhoods if n not in cfg["neighborhoods"]]
        if bad:
            raise ValueError(f"unknown neighborhoods for {city_key}: {bad} "
                             f"(valid: {cfg['neighborhoods']})")
        # save_show validates against this list, so the shard is hard-enforced
        cfg["neighborhoods"] = list(neighborhoods)
    # bounded per-request timeout: a stalled call (seen 2026-10-08) otherwise hangs a session for the better part of an hour
    client = anthropic.Anthropic(api_key=api_key, timeout=480.0) if api_key else anthropic.Anthropic(timeout=480.0)
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
    if search_domains:
        for t in server_tools:
            for k in ("allowed_domains", "blocked_domains"):
                if search_domains.get(k):
                    t[k] = list(search_domains[k])
    if verify:
        all_tools = server_tools + VERIFY_TOOLS
    elif enumerate_zone:
        all_tools = server_tools + [RECORD_VENUE_TOOL]
    elif signal_variant:
        all_tools = server_tools + [RECORD_SIGNAL_TOOL]   # single strict tool
    elif deep:
        # attach_images is enrich-only; dropping it keeps the strict-schema
        # budget under the limit alongside the non-strict log_skip
        all_tools = server_tools + [t for t in CLIENT_TOOLS
                                    if t["name"] != "attach_images"] + [LOG_SKIP_TOOL,
                                                                        RENDER_FETCH_TOOL]
        if keyword_signals:
            all_tools.append(RECORD_SIGNAL_TOOL_LOOSE)
    else:
        all_tools = server_tools + CLIENT_TOOLS

    def execute(name: str, args: dict) -> str:
        trace.cost_now, trace.searches_now = meter.dollars, meter.web_searches
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
        if name == "render_fetch":
            return tools.render_fetch(args["url"])
        if name == "record_venue":
            return tools.record_venue(args, city_key, cfg["neighborhoods"], meter.label)
        if name == "record_signal":
            import curation_prompts
            if signal_variant:
                variant, phash = signal_variant, curation_prompts.VARIANTS[signal_variant].prompt_hash()
            else:
                variant, phash = curation_prompts.KEYWORD_VARIANT, curation_prompts.keyword_prompt_hash()
            return curation_store.record_signal(args, city_key, run_id=meter.label,
                                                variant=variant, model=model, prompt_hash=phash)
        raise ValueError(f"unknown tool {name}")

    if verify:
        saved_shows = (tools._load_shows_file(tools._pending_file(city_key))["shows"]
                       if verify_pending_only else tools.all_city_shows(city_key))
        saved_shows = [s for s in saved_shows if not tools.show_expired(s)]
        if verify_pending_only:
            verdicts_now = tools.latest_verdicts()
            saved_shows = [s for s in saved_shows
                           if tools.verify_candidate(city_key, s, verdicts_now)]
        if neighborhoods:
            saved_shows = [s for s in saved_shows if s["venue"]["neighborhood"] in neighborhoods]
        if verify_slugs is not None:
            saved_shows = [s for s in saved_shows if s["slug"] in set(verify_slugs)]
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
        system = build_enumerate_prompt(city_key, cfg, enumerate_zone, known,
                                        missing_anchors=missing_anchors)
        messages = [{
            "role": "user",
            "content": f"Enumerate the {enumerate_zone} zone of {cfg['display_name']} "
                       "following your instructions.",
        }]
    elif signal_variant:
        import curation_prompts
        system, first = curation_prompts.render(signal_variant, city_key, cfg, signal_ctx or {})
        messages = [{"role": "user", "content": first}]
    else:
        system = build_system_prompt(city_key, cfg, target_shows,
                                     campaign=campaign, deep=deep)
        if keyword_signals and deep:
            import curation_prompts
            system += curation_prompts.KEYWORD_SIGNALS_BLOCK
        if first_user_message is not None:
            messages = [{"role": "user", "content": first_user_message}]
        else:
            existing_note = ""
            prior = tools.all_city_shows(city_key)  # published + pending
            if prior:
                lines = "\n".join(f'- "{s["title"]}" at {s["venue"]["name"]}' for s in prior)
                existing_note = (
                    f"\n\nALREADY SAVED SHOWS for this city (do NOT research or re-save these; "
                    f"other shows at the same venues are welcome — add {target_shows} NEW "
                    f"shows):\n{lines}"
                )
            messages = [{
                "role": "user",
                "content": f"Populate the {cfg['display_name']} section: research and save "
                           f"{target_shows} current shows following your instructions." + existing_note,
            }]

    import venues
    trace = venues.SessionTrace(meter.label, city_key)
    trace.zone = enumerate_zone or (cfg["neighborhoods"][0] if len(cfg["neighborhoods"]) == 1 else None)
    trace.todo_venues = list(todo_venues or [])
    tools.set_session(trace)
    repeat_queries: list[str] = []   # advisory: the model re-ran a search
    server_inputs: dict[str, dict] = {}   # server_tool_use id -> input (queries)
    evidence_ok = tools.SANDBOX_DIR is None

    nudged = False
    continue_nudges = 0
    resolution_nudges = 0
    final_text = ""
    container_id = None
    stop_reason = "not_started"
    for iteration in range(max_iterations):
        trace.iteration = iteration
        def create(cid: str | None):
            kwargs: dict = {
                "model": model,
                "max_tokens": MAX_TOKENS,
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
                inp = block.input if isinstance(block.input, dict) else {}
                server_inputs[block.id] = inp
                if block.name == "web_fetch":
                    trace.add_url(inp.get("url"), "web_fetch")
                elif block.name == "web_search":
                    q = str(inp.get("query", ""))
                    qn = re.sub(r"\s+", " ", q.strip().lower())
                    if qn and qn in {re.sub(r"\s+", " ", x.strip().lower()) for x in trace.queries}:
                        repeat_queries.append(q)
                    trace.queries.append(q)
            elif block.type == "tool_use":
                print(f"  [{city_key} #{iteration}] tool:{block.name} {json.dumps(block.input)[:140]}")
            elif block.type == "web_fetch_tool_result" and evidence_ok:
                # Already paid for: keep the fetched text for refresh/curation reuse.
                url, text = _fetch_text(block)
                if url and text:
                    try:
                        venues.write_evidence(city_key, url, "web_fetch", text, meter.label)
                    except Exception as exc:
                        print(f"  [{city_key}] evidence write failed: {exc}")
                    # unknown venue domain fetched -> registry candidate (never fails a turn)
                    try:
                        hit = venues.on_fetch(city_key, url, text, trace, cfg)
                        if hit:
                            print(f"  [{city_key}] registry {hit} from {url[:80]}")
                    except Exception as exc:
                        tools.log_event({"session": meter.label, "city": city_key,
                                         "kind": "registry_error", "hook": "on_fetch",
                                         "error": str(exc)[:300]})
            elif block.type == "web_search_tool_result" and evidence_ok:
                res = _search_results(block, server_inputs)
                if res and res["results"]:
                    try:
                        venues.write_evidence(city_key, "search:" + (res["query"] or ""),
                                              "web_search", json.dumps(res, ensure_ascii=False),
                                              meter.label)
                    except Exception as exc:
                        print(f"  [{city_key}] evidence write failed: {exc}")

        stop_reason = response.stop_reason
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue

        if response.stop_reason == "refusal":
            print(f"  [{city_key}] refusal: {response.stop_details}")
            tools.log_event({"session": meter.label, "city": city_key, "kind": "refusal",
                             "detail": str(response.stop_details)})
            break

        cut_off = response.stop_reason == "max_tokens"
        has_tool_calls = any(b.type == "tool_use" for b in response.content)
        if response.stop_reason == "end_turn" and not has_tool_calls and continue_nudges < 2 \
                and meter.dollars < budget_usd and PAUSE_RE.search(final_text or ""):
            # The web tools have a per-turn call cap; the model sometimes stops
            # and says it will "resume next turn". Give it that turn (twice max).
            continue_nudges += 1
            print(f"  [{city_key}] model paused for a new turn; nudging ({continue_nudges}/2)")
            tools.log_event({"session": meter.label, "city": city_key, "kind": "continue_nudge"})
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": "Continue — this is your next turn; the tool "
                             "limits have reset. Keep working through your instructions."})
            continue
        if (response.stop_reason == "end_turn" and not has_tool_calls and deep and todo_venues
                and resolution_nudges < 1 and meter.dollars < budget_usd):
            # Deep-session contract: every TODO venue ends in a save of a show
            # on view NOW or a log_skip. An upcoming-only save (1301PE) or a
            # venue the model simply forgot gets one more turn.
            pending_v = trace.unresolved(todo_venues)
            if pending_v:
                resolution_nudges += 1
                print(f"  [{city_key}] {len(pending_v)} TODO venue(s) unresolved; nudging")
                tools.log_event({"session": meter.label, "city": city_key, "kind": "resolution_nudge",
                                 "venues": [{"name": v["name"], "status": st}
                                            for v, st in pending_v][:20]})
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": _resolution_nudge_text(pending_v)})
                continue
        if response.stop_reason != "tool_use" and not (cut_off and has_tool_calls):
            break  # end_turn (or max_tokens with nothing to execute) — session over
        # A max_tokens response still carries every complete tool_use block the
        # model emitted; execute them and let it continue rather than losing
        # a whole batch of saves/records.

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
        if repeat_queries:
            qs = "; ".join(f"'{q}'" for q in repeat_queries[-3:])
            results.append({"type": "text", "text": f"NOTE: you already ran the search {qs} "
                            "earlier this session — never repeat a search; move on to new names "
                            "or directory pages."})
            repeat_queries.clear()
        if cut_off:
            print(f"  [{city_key}] output limit hit with {len(results)} tool call(s) executed; continuing")
            tools.log_event({"session": meter.label, "city": city_key, "kind": "max_tokens_continue",
                             "tool_calls": len(results)})
            results.append({"type": "text", "text": "NOTE: your previous turn was cut off by the output "
                            "limit after the tool calls above. Continue from where you left off; "
                            "issue fewer tool calls per turn."})
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
    tools.set_session(None)
    shows_total = len(tools.all_city_shows(city_key))
    shows_added = shows_total - shows_before
    tools.log_event({"session": meter.label, "city": city_key, "kind": "session_end",
                     "stop_reason": stop_reason, "shows_added": shows_added,
                     "cost_usd": round(meter.dollars, 4),
                     "unresolved": ([v["name"] for v, _ in trace.unresolved(todo_venues)]
                                    if (deep and todo_venues) else None)})
    return {"city": city_key, "shows_saved": shows_total, "shows_added": shows_added,
            "final_message": final_text, "stop_reason": stop_reason, **meter.summary()}
