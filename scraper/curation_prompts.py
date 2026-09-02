"""Named, versioned prompt variants for curation signal sessions.

A signal session is an agent run (harness.run_city with signal_variant=...)
that has web search/fetch plus ONE client tool, record_signal, and whose only
job is to write press/curatorial evidence about current shows into the
curation store. Variants are first-class objects so runs.jsonl can record
exactly which prompt produced each signal (name + prompt_hash), and so A/B
prompting is a matter of adding a new entry to VARIANTS.

    pubsweep_v1     sweep registered publications for current-show recommendations/reviews
    artist_heat_v1  per pooled show: the artist's recent institutional/press activity
    fairs_v1        art-fair exhibitor lists -> venue-level notability signals
    keyword_v1      (block only) lets deep scrape sessions record significance
                    claims they already read on the venue's own pages

Placeholders available to every system template: {today}, {city}, {pool_inventory},
{sources_block}, {horizon_weeks}, {benchmark_note}.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import curation_store as store

HORIZON_WEEKS = 6          # how far back a mention still counts as "current" coverage
OPENING_HORIZON_DAYS = 30  # shows opening within this many days are worth recording


def pool_inventory(pool: list[dict], limit: int = 400) -> str:
    """Compact slug | artist | title | venue | dates table (cached in the system prompt)."""
    lines = []
    for s in pool[:limit]:
        v = s.get("venue", {})
        lines.append(f"{s['slug']} | {s.get('artist') or '-'} | {s.get('title') or '-'} | "
                     f"{v.get('name')} | {s.get('start_date') or '?'}..{s.get('end_date') or '?'}")
    return "\n".join(lines) if lines else "(pool is empty)"


def sources_block(sources: list[dict]) -> str:
    lines = []
    for s in sources:
        hints = "; ".join(s.get("query_hints") or [])
        urls = ", ".join(s.get("urls") or [])
        lines.append(f"- {s['id']} — {s.get('name')} [{s.get('kind')}]"
                     + (f"\n    pages: {urls}" if urls else "")
                     + (f"\n    search ideas: {hints}" if hints else "")
                     + (f"\n    note: {s['notes']}" if s.get('notes') else ""))
    return "\n".join(lines) if lines else "(none)"


COMMON_RULES = """
RECORDING RULES:
- Call record_signal once per (source page, show) mention. A picks list with 8 shows = 8 calls. Never batch several shows into one call.
- kind: pick = an editorial "shows to see" recommendation; review = a critic's review of the show itself; news = news coverage of the show; listing = a neutral listings roundup entry; fair_exhibitor = the VENUE appears on a fair's exhibitor list (artist/title null); artist_activity = the artist's recent (<= 12 months) museum/biennial show, acquisition, or major profile elsewhere; award = a major prize; press_release_claim = a significance claim taken from the venue's own text.
- strength: headline = the piece is about this one show/artist; featured = one of a handful highlighted; mentioned = one entry in a longer roundup; passing = a brief aside.
- published_at: the page's own date (YYYY-MM-DD) or null. snippet: <= 300 characters quoted or closely paraphrased from the page that justify kind and strength. Do not invent quotes.
- agent_pool_slug: if the mention is one of the POOL shows, give its slug; otherwise null. The deterministic matcher decides — your slug is advisory.
- Shows that ended before {today} are stale: do not record them. Mentions older than about {horizon_weeks} weeks are stale unless the show is still on view.
- Record shows at venues NOT in the POOL too — those are our most valuable discoveries (scrape gaps).
- {benchmark_note}
- Accuracy over volume: only record what you actually read on the page you cite. If a page will not load, move on.
- Efficiency: you have limited searches and fetches. Fetch a source's known pages first; search only with the ideas given or close variants; never fetch the same page twice.
- Give up fast: if a source's pages fail to load twice, or three searches for it return nothing usable, stop working that source and move on — say so in your final summary. Never loop retries through code execution.
- Finish by replying with one line: "<n> signals recorded from <k> sources".
"""

BENCHMARK_NOTE = ("Never use or cite the See Saw app / seesawmap.com as a source — it is our benchmark, "
                  "and citing it would contaminate the test.")


PUBSWEEP_SYSTEM = """You are a research assistant building the evidence base for an automatic curation system: which art exhibitions in {city} are the most significant and interesting right now, as judged by the city's serious art press and picks lists. Today is {today}.

You work through a list of publications (SOURCES in your first message). For each: open its known pages, use the search ideas, and record every CURRENT {city} exhibition it recommends, reviews, or covers (on view now, or opening within {opening_days} days) with the record_signal tool.
{common_rules}
POOL — shows we already track (slug | artist | title | venue | dates):
{pool_inventory}
"""

ARTIST_HEAT_SYSTEM = """You are a research assistant building the evidence base for an automatic curation system: which art exhibitions in {city} are the most significant right now. Today is {today}.

This session measures ARTIST HEAT for a batch of shows we already track (SHOWS in your first message). For each show, run one or two focused searches and record with record_signal:
- kind artist_activity: the artist's museum or biennial exhibitions, major acquisitions, or substantial profiles/interviews in national or international outlets from the last 12 months (one call per distinct item; strength headline for a solo museum show or major profile, featured for a group museum/biennial appearance, mentioned for a passing reference).
- kind award: a major prize or fellowship (MacArthur, Turner, Hugo Boss, Guggenheim Fellowship, biennial prizes...).
- kind review or pick: a critic reviewed or recommended THIS pooled show.
For artist_activity and award, set venue/artist/title to the POOL show's venue, artist and title (so the evidence attaches to that show) and agent_pool_slug to its slug; source_url is the article about the activity; the snippet says what the activity was, where, and when.
Artists with no findable institutional footprint are common and fine — record nothing rather than something weak. Skip group shows with more than three named artists unless one artist clearly leads.
{common_rules}
POOL (for slugs):
{pool_inventory}
"""

FAIRS_SYSTEM = """You are a research assistant building a notability index of {city} art venues. Today is {today}.

Art-fair exhibitor lists are a strong, cheap signal of which galleries the market and curators take seriously. For each fair in your first message, fetch its most recent exhibitor list (the current or most recent edition; use the search ideas if the page moved) and, for every exhibitor that has a gallery space in {city}, call record_signal with kind fair_exhibitor, strength featured, venue = the gallery's name as listed, artist null, title null, source_id = the fair's id, source_url = the exhibitor-list page, snippet = "Exhibitor at <fair name> <edition/year>" plus the fair section if shown (e.g. Focus, Galleries). Skip galleries with no {city} location. One call per gallery per fair.
{common_rules}
POOL (for reference — venue names we already track):
{pool_inventory}
"""

KEYWORD_SIGNALS_BLOCK = """

SIGNIFICANCE SIGNALS (cheap, optional, zero extra searches): while you are already reading a venue's own pages for a show you save, if the text makes a concrete significance claim — first museum / US / Los Angeles solo, retrospective or survey, biennial or major-institution history, a major award, a museum acquisition, a monumental commission, or it quotes a specific recent review or "must-see" listing — call record_signal once per claim: kind "press_release_claim" (or "review"/"pick" when it cites a named outlet's coverage, with that outlet as source_id), strength "featured", source_id "venue-site", source_url = the page you read, venue/artist/title = the show's, snippet = the claim in <= 300 characters, agent_pool_slug = the slug you are saving. Never spend a search or fetch on this; only record what you already read. Do not record anything for venues you skip."""


@dataclass(frozen=True)
class Variant:
    name: str
    stage: str
    unit: str                     # what one item of ctx["items"] is: source | show | fair
    description: str
    system_template: str
    first_message: Callable[[dict], str]
    search_domains: Callable[[dict], dict | None]
    limits: dict = field(default_factory=dict)

    def prompt_hash(self) -> str:
        """sha1 of the prompt text with the volatile {today} left as a placeholder."""
        return hashlib.sha1((self.system_template + COMMON_RULES).encode()).hexdigest()[:12]


def _pubsweep_first(ctx: dict) -> str:
    return (f"SOURCES to sweep this session ({len(ctx['items'])}):\n"
            f"{sources_block(ctx['items'])}\n\n"
            "For each source, in order: fetch its pages, try its search ideas, and record every "
            "current show it recommends, reviews or covers. Then move to the next source. "
            "Reply with the one-line summary when done.")


def _artist_heat_first(ctx: dict) -> str:
    lines = []
    for s in ctx["items"]:
        v = s.get("venue", {})
        lines.append(f"- {s['slug']} | {s.get('artist') or '-'} | {s.get('title') or '-'} | "
                     f"{v.get('name')} | {s.get('start_date') or '?'}..{s.get('end_date') or '?'}")
    return (f"SHOWS to research this session ({len(lines)}):\n" + "\n".join(lines)
            + "\n\nWork through them in order; one or two searches each. Reply with the "
              "one-line summary when done.")


def _fairs_first(ctx: dict) -> str:
    return (f"FAIRS to index this session ({len(ctx['items'])}):\n"
            f"{sources_block(ctx['items'])}\n\n"
            "Fetch each exhibitor list and record every exhibitor with a space in the city. "
            "Reply with the one-line summary when done.")


def _domains_of(items: list[dict]) -> list[str]:
    out: list[str] = []
    for s in items:
        for d in s.get("domains") or []:
            if d not in out:
                out.append(d)
    return out


VARIANTS: dict[str, Variant] = {
    "pubsweep_v1": Variant(
        name="pubsweep_v1", stage="signals", unit="source",
        description="Sweep registered publications for current-show picks/reviews/coverage.",
        system_template=PUBSWEEP_SYSTEM, first_message=_pubsweep_first,
        # Not an allow-list: the API rejects allowed_domains that block Anthropic's
        # crawler (latimes.com did, 400 for the whole session). Block only the benchmark.
        search_domains=lambda ctx: {"blocked_domains": list(store.BENCHMARK_DOMAINS)},
        limits={"group_size": 4, "max_searches": 12, "max_fetches": 12, "max_iterations": 40,
                "budget_usd": 3.0, "fetch_tokens": 8000},
    ),
    "artist_heat_v1": Variant(
        name="artist_heat_v1", stage="signals", unit="show",
        description="Per pooled show: the artist's recent institutional/press activity.",
        system_template=ARTIST_HEAT_SYSTEM, first_message=_artist_heat_first,
        search_domains=lambda ctx: {"blocked_domains": list(store.BENCHMARK_DOMAINS)},
        limits={"group_size": 6, "max_searches": 14, "max_fetches": 10, "max_iterations": 40,
                "budget_usd": 2.5, "fetch_tokens": 6000},
    ),
    "fairs_v1": Variant(
        name="fairs_v1", stage="signals", unit="fair",
        description="Fair exhibitor lists -> venue-level fair_exhibitor signals.",
        system_template=FAIRS_SYSTEM, first_message=_fairs_first,
        search_domains=lambda ctx: None,
        limits={"group_size": 3, "max_searches": 8, "max_fetches": 12, "max_iterations": 40,
                "budget_usd": 3.0, "fetch_tokens": 12000},
    ),
}

KEYWORD_VARIANT = "keyword_v1"


def keyword_prompt_hash() -> str:
    return hashlib.sha1(KEYWORD_SIGNALS_BLOCK.encode()).hexdigest()[:12]


def render(name: str, city_key: str, cfg: dict, ctx: dict) -> tuple[str, str]:
    """(system prompt, first user message) for a variant and its session context.
    ctx: {"items": [...], "pool": [...]} — pool defaults to the city's live pool."""
    var = VARIANTS[name]
    import tools
    pool = ctx.get("pool")
    if pool is None:
        pool = [s for s in tools.all_city_shows(city_key) if not tools.show_expired(s)]
    fields = {
        "today": date.today().isoformat(),
        "city": cfg["display_name"],
        "pool_inventory": pool_inventory(pool),
        "sources_block": sources_block(ctx.get("items", [])) if var.unit != "show" else "",
        "horizon_weeks": HORIZON_WEEKS,
        "opening_days": OPENING_HORIZON_DAYS,
        "benchmark_note": BENCHMARK_NOTE,
    }
    common = COMMON_RULES.format(**fields)
    system = var.system_template.format(common_rules=common, **fields)
    return system, var.first_message(ctx)


def describe() -> str:
    return "\n".join(f"{v.name:<16} {v.unit:<7} hash={v.prompt_hash()}  {v.description}"
                     for v in VARIANTS.values())


if __name__ == "__main__":
    print(describe())
    print(json.dumps({k: v.limits for k, v in VARIANTS.items()}, indent=1))
