"""Render the self-contained curation dashboard (plan 2.6).

    python curation_dashboard.py --city los-angeles [--report path] [--out path]

Reads ``content/spend/reports/curation-<city>.json`` (written by ``curate.py
score``), embeds it as ``window.CURATION`` into
``scraper/dashboard/curation_template.html`` at the ``<!-- CURATION_DATA -->``
marker (the shared scoring JS, ``dashboard/curation_core.js``, is spliced in at
``<!-- CURATION_CORE -->``) and writes ``content/spend/reports/curation-<city>.html``. When the
report's ``judge_compare`` block is empty and
``content/curation/<city>/judge_compare.json`` (or the report-dir twin) exists,
that file is embedded instead. Every ``content/curation/params/*.json`` is
embedded as ``report.presets`` so the page's "Presets & export" panel can load
the on-disk presets, not just the ones saved in that browser. No template
engine, no server: open the printed ``file://`` URL.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTENT_DIR = HERE.parent / "content"
TEMPLATE = HERE / "dashboard" / "curation_template.html"
CORE = HERE / "dashboard" / "curation_core.js"
MARKER = "<!-- CURATION_DATA -->"
CORE_MARKER = "<!-- CURATION_CORE -->"


def embed_json(obj) -> str:
    """JSON that is safe inside a <script> element: ``</`` would end the
    element early and ``<!--`` opens an HTML comment in script data."""
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return (text.replace("</", "<\\/").replace("<!--", "<\\!--")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def inline_core(page: str) -> str:
    """Splice ``dashboard/curation_core.js`` (the shared scoring mirror) into a
    template at its ``<!-- CURATION_CORE -->`` marker, which sits inside the
    page's main ``<script>``. Both the dev dashboard and the client site
    (``curation_site.py``) go through this so there is exactly one copy of the
    JS scoring code."""
    if CORE_MARKER not in page:
        raise SystemExit(f"marker {CORE_MARKER!r} missing from template")
    return page.replace(CORE_MARKER, CORE.read_text(), 1)


def params_presets() -> dict[str, dict]:
    """``content/curation/params/*.json`` -> {stem: {params, note}}. Keys starting
    with ``_`` are stripped from ``params`` (``_note`` is kept alongside it as the
    dropdown's tooltip). Unreadable files are skipped, not fatal."""
    out: dict[str, dict] = {}
    for f in sorted((CONTENT_DIR / "curation" / "params").glob("*.json")):
        try:
            raw = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        out[f.stem] = {"params": {k: v for k, v in raw.items() if not k.startswith("_")},
                       "note": raw.get("_note"), "file": f"content/curation/params/{f.name}"}
    return out


def judge_compare_files(city: str) -> list[Path]:
    return [CONTENT_DIR / "curation" / city / "judge_compare.json",
            CONTENT_DIR / "spend" / "reports" / f"judge-compare-{city}.json"]


def build(city: str, report_path: str | None = None, out_path: str | None = None) -> Path:
    report_file = Path(report_path) if report_path else CONTENT_DIR / "spend" / "reports" / f"curation-{city}.json"
    report = json.loads(report_file.read_text())
    if not report.get("judge_compare"):
        for p in judge_compare_files(city):
            if p.exists():
                report["judge_compare"] = json.loads(p.read_text())
                report["judge_compare_source"] = str(p.relative_to(CONTENT_DIR.parent))
                break
    report["presets"] = params_presets()
    template = TEMPLATE.read_text()
    if MARKER not in template:
        raise SystemExit(f"marker {MARKER!r} missing from {TEMPLATE}")
    page = inline_core(template).replace(MARKER, f"<script>window.CURATION = {embed_json(report)};</script>", 1)
    out = Path(out_path) if out_path else CONTENT_DIR / "spend" / "reports" / f"curation-{city}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--city", required=True)
    ap.add_argument("--report", help="report JSON (default: content/spend/reports/curation-<city>.json)")
    ap.add_argument("--out", help="output HTML (default: content/spend/reports/curation-<city>.html)")
    args = ap.parse_args(argv)
    out = build(args.city, args.report, args.out)
    presets = params_presets()
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")
    print(f"presets embedded ({len(presets)}): {', '.join(presets) or 'none'}")
    print(f"file://{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
