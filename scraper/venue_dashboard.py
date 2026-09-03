"""Render the self-contained gallery-ranking dashboard (galleries-first plan, G5).

    python venue_dashboard.py --city los-angeles [--report path] [--out path]

Reads ``content/curation/<city>/venues_ranked.json`` (written by
``rank_venues.py score``), embeds it as ``window.VENUES`` into
``scraper/dashboard/venue_template.html`` at the ``<!-- VENUE_DATA -->`` marker
(the scoring mirror ``dashboard/venue_core.js`` is spliced in at
``<!-- VENUE_CORE -->``) and writes ``content/spend/reports/venues-<city>.html``.
Every ``content/curation/params/venues-*.json`` is embedded as presets. The page
re-ranks client-side from the embedded raw evidence: nothing here fetches or
scrapes. Share a weighting with the ``#p=`` link; reproduce it with
``rank_venues.py score --city <city> --params <exported json>``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from curation_dashboard import embed_json  # noqa: E402

HERE = Path(__file__).resolve().parent
CONTENT_DIR = HERE.parent / "content"
TEMPLATE = HERE / "dashboard" / "venue_template.html"
CORE = HERE / "dashboard" / "venue_core.js"
MARKER = "<!-- VENUE_DATA -->"
CORE_MARKER = "<!-- VENUE_CORE -->"


def venue_presets() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in sorted((CONTENT_DIR / "curation" / "params").glob("venues-*.json")):
        try:
            raw = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict):
            out[f.stem] = {"params": {k: v for k, v in raw.items() if not k.startswith("_")},
                           "note": raw.get("_note"), "file": f"content/curation/params/{f.name}"}
    return out


def inline_core(page: str) -> str:
    if CORE_MARKER not in page:
        raise SystemExit(f"marker {CORE_MARKER!r} missing from template")
    return page.replace(CORE_MARKER, CORE.read_text(), 1)


def build(city: str, report_path: str | None = None, out_path: str | None = None) -> Path:
    report_file = Path(report_path) if report_path else CONTENT_DIR / "curation" / city / "venues_ranked.json"
    report = json.loads(report_file.read_text())
    report["presets"] = venue_presets()
    template = TEMPLATE.read_text()
    if MARKER not in template:
        raise SystemExit(f"marker {MARKER!r} missing from {TEMPLATE}")
    page = inline_core(template).replace(MARKER, f"<script>window.VENUES = {embed_json(report)};</script>", 1)
    out = Path(out_path) if out_path else CONTENT_DIR / "spend" / "reports" / f"venues-{city}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--city", required=True)
    ap.add_argument("--report", help="venues_ranked.json (default: content/curation/<city>/venues_ranked.json)")
    ap.add_argument("--out", help="output HTML (default: content/spend/reports/venues-<city>.html)")
    args = ap.parse_args(argv)
    out = build(args.city, args.report, args.out)
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")
    print(f"presets embedded: {', '.join(venue_presets()) or 'none'}")
    print(f"file://{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
