"""Assemble A/B arm comparison data for a deep-scrape experiment.

Arms are identified by session-label prefixes (ab-a-, ab-b-, ab-c-, ...).
For each arm this collects: spend/token metrics from its session ledgers,
its TODO assignments (content/spend/todo/<label>.json), what got saved
(venue-name match against the real pools) and skipped (skips.jsonl by
session), verify verdicts for its saved shows, image counts/dimensions,
and description word counts.

Sandbox overlap arms (--sandbox name=dir) contribute same-venue records for
side-by-side prose comparison against the real pools.

Outputs <out>.json (full data, for curation) and <out>.html (quick table
render). The polished comparison artifact is curated from the JSON.

Usage:
    python ab_report.py --city los-angeles --arms a,b,c \
        --sandbox haiku-overlap=../content/spend/ab/haiku-overlap \
        --out ../content/spend/reports/ab-los-angeles
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools  # noqa: E402
from harness import SPEND_DIR  # noqa: E402
from PIL import Image  # noqa: E402

TODO_DIR = tools.CONTENT_DIR / "spend" / "todo"


def _image_info(city: str, show: dict, root: Path) -> list[dict]:
    out = []
    for rel in show.get("images", []):
        p = root / rel
        if not p.is_file():
            continue
        try:
            with Image.open(p) as im:
                out.append({"path": rel, "w": im.size[0], "h": im.size[1],
                            "kb": round(p.stat().st_size / 1024)})
        except Exception:
            continue
    return out


def _show_record(city: str, show: dict, root: Path) -> dict:
    return {
        "slug": show["slug"],
        "venue": show["venue"]["name"],
        "neighborhood": show["venue"]["neighborhood"],
        "title": show["title"],
        "artist": show.get("artist"),
        "dates": f"{show['start_date']} to {show['end_date']}",
        "hours": show["venue"].get("hours"),
        "address": show["venue"].get("address"),
        "description": show["description"],
        "desc_words": len(show["description"].split()),
        "source_urls": show.get("source_urls", []),
        "images": _image_info(city, show, root),
    }


def collect(city: str, arm_prefixes: list[str],
            sandboxes: dict[str, Path]) -> dict:
    pools = (tools._load_shows_file(tools._city_file(city))["shows"]
             + tools._load_shows_file(tools._pending_file(city))["shows"])
    by_venue = {tools._norm_venue(s["venue"]["name"]): s for s in pools}
    verdicts = tools.latest_verdicts()
    skips = tools.load_skips(city)

    arms = {}
    for prefix in arm_prefixes:
        ledgers, todo_names = [], []
        for f in sorted(SPEND_DIR.glob(f"{prefix}*.json")):
            try:
                e = json.loads(f.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if "cost_usd" in e:
                ledgers.append(e)
        for f in sorted(TODO_DIR.glob(f"{prefix}*.json")):
            t = json.loads(f.read_text())
            todo_names += [v["name"] for v in t["venues"]]
        todo_keys = {tools._norm_venue(n) for n in todo_names}
        arm_skips = [s for s in skips if s.get("session", "").startswith(prefix)]
        saved = [by_venue[k] for k in sorted(todo_keys) if k in by_venue]
        saved_records = [_show_record(city, s, tools.CONTENT_DIR) for s in saved]
        arm_verdicts = {s["slug"]: verdicts.get((city, s["slug"])) for s in saved}
        vstat = {"verified": 0, "corrected": 0, "unverified": 0, "none": 0}
        for v in arm_verdicts.values():
            vstat[v["status"] if v else "none"] = vstat.get(
                v["status"] if v else "none", 0) + 1
        widths = [i["w"] for r in saved_records for i in r["images"]]
        cost = sum(l["cost_usd"] for l in ledgers)
        resolved = len(saved) + len(arm_skips)
        arms[prefix] = {
            "sessions": len(ledgers),
            "cost_usd": round(cost, 4),
            "requests": sum(l["requests"] for l in ledgers),
            "web_searches": sum(l["web_searches"] for l in ledgers),
            "output_tokens": sum(l["output_tokens"] for l in ledgers),
            "cache_read_tokens": sum(l["cache_read_tokens"] for l in ledgers),
            "cache_write_tokens": sum(l["cache_write_tokens"] for l in ledgers),
            "models": sorted({l.get("model", "?") for l in ledgers}),
            "todo_assigned": len(todo_keys),
            "saved": len(saved),
            "skipped": len(arm_skips),
            "resolved": resolved,
            "cost_per_resolved": round(cost / resolved, 4) if resolved else None,
            "cost_per_save": round(cost / len(saved), 4) if saved else None,
            "verify": vstat,
            "images_per_save": (round(len(widths) / len(saved), 1) if saved else None),
            "image_width_median": int(median(widths)) if widths else None,
            "image_width_min": min(widths) if widths else None,
            "desc_words_median": (int(median(r["desc_words"] for r in saved_records))
                                  if saved_records else None),
            "skips": [{k: s.get(k) for k in ("venue", "reason", "detail", "url")}
                      for s in arm_skips],
            "saves": saved_records,
        }

    # sandbox overlap pairs: sandbox venue vs the same venue in the real pools
    pairs = []
    for name, root in sandboxes.items():
        sb_shows = (tools._load_shows_file(root / f"{city}.json")["shows"]
                    + tools._load_shows_file(root / "pending" / f"{city}.json")["shows"])
        for s in sb_shows:
            key = tools._norm_venue(s["venue"]["name"])
            real = by_venue.get(key)
            pairs.append({
                "venue": s["venue"]["name"],
                "sandbox_arm": name,
                "sandbox": _show_record(city, s, root),
                "real": _show_record(city, real, tools.CONTENT_DIR) if real else None,
            })
    return {"city": city, "arms": arms, "overlap_pairs": pairs}


def render_html(data: dict) -> str:
    esc = html.escape
    rows = []
    metrics = ["models", "sessions", "cost_usd", "todo_assigned", "saved", "skipped",
               "cost_per_resolved", "cost_per_save", "verify", "images_per_save",
               "image_width_median", "desc_words_median", "web_searches",
               "output_tokens", "cache_read_tokens"]
    arm_names = list(data["arms"])
    head = "<tr><th>metric</th>" + "".join(f"<th>{esc(a)}</th>" for a in arm_names) + "</tr>"
    for m in metrics:
        cells = "".join(f"<td>{esc(json.dumps(data['arms'][a].get(m)))}</td>"
                        for a in arm_names)
        rows.append(f"<tr><td>{m}</td>{cells}</tr>")
    pair_html = []
    for p in data["overlap_pairs"]:
        real = p.get("real")
        cols = []
        for tag, rec in (("real (Sonnet)", real), (p["sandbox_arm"], p["sandbox"])):
            if not rec:
                cols.append("<td>(not captured)</td>")
                continue
            imgs = ", ".join(f"{i['w']}x{i['h']}" for i in rec["images"])
            cols.append(
                f"<td style='vertical-align:top;width:50%'><b>{esc(rec['title'])}</b>"
                f"<br><i>{esc(rec['dates'])}</i> · {rec['desc_words']} words · "
                f"imgs: {esc(imgs) or 'none'}"
                f"<p style='white-space:pre-wrap'>{esc(rec['description'])}</p></td>")
        pair_html.append(f"<h3>{esc(p['venue'])}</h3>"
                         f"<table border=1 cellpadding=8 style='border-collapse:collapse'>"
                         f"<tr>{''.join(cols)}</tr></table>")
    return (f"<title>A/B report {esc(data['city'])}</title>"
            f"<h1>A/B arms — {esc(data['city'])}</h1>"
            f"<table border=1 cellpadding=6 style='border-collapse:collapse'>"
            f"{head}{''.join(rows)}</table>"
            f"<h2>Same-venue pairs</h2>{''.join(pair_html)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", required=True)
    parser.add_argument("--arms", default="a,b,c",
                        help="comma list; arm X reads labels ab-X-*")
    parser.add_argument("--sandbox", action="append", default=[],
                        metavar="NAME=DIR", help="overlap sandbox pool(s)")
    parser.add_argument("--out", default=None,
                        help="output basename (writes .json and .html)")
    args = parser.parse_args()

    prefixes = [f"ab-{a.strip()}-" for a in args.arms.split(",")]
    sandboxes = {}
    for sb in args.sandbox:
        name, _, d = sb.partition("=")
        sandboxes[name] = Path(d)
    data = collect(args.city, prefixes, sandboxes)
    out = Path(args.out) if args.out else \
        tools.CONTENT_DIR / "spend" / "reports" / f"ab-{args.city}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    out.with_suffix(".html").write_text(render_html(data), encoding="utf-8")
    print(out.with_suffix(".json"))
    print(out.with_suffix(".html"))
    for a, m in data["arms"].items():
        print(f"{a}: ${m['cost_usd']:.2f} | todo {m['todo_assigned']} | saved {m['saved']} "
              f"| skipped {m['skipped']} | $/resolved {m['cost_per_resolved']}")


if __name__ == "__main__":
    main()
