"""Artist bio cards: render content/artists/bios/<city>.json to square PNGs
and put them in each show's image set, second, after the lead artwork.

    scraper/.venv/bin/python scraper/bio_cards.py --city los-angeles [--slug SLUG ...]

A card reads, top to bottom: the artist's age (very large; the life span for
an artist who has died), education, recent museum shows, then the most recent
solo gallery shows. Lines with no sourced data are left out. Cards are drawn
by headless Google Chrome to content/images/<city>/<slug>/bio-<artist>.png;
re-running replaces a show's earlier bio cards.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SIZE = 2160

esc = html.escape


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def is_bio(rel: str) -> bool:
    return Path(rel).name.startswith("bio-")


def age_block(a: dict) -> str:
    # Living: the age (the year difference, so it can run one high before the
    # birthday), with the birth year beside it. Deceased: the life span.
    born, died = a.get("born"), a.get("died")
    if not born:
        return ""
    place = f" in {esc(a['birthplace'])}" if a.get("birthplace") else ""
    if died:
        # no "died at N": a year difference can run one high
        cap = f'<div class="cap">Born{place}</div>' if place else ""
        return f'<div class="age span"><div class="big">{born}–{died}</div>{cap}</div>'
    return (f'<div class="age"><div class="big">{dt.date.today().year - born}</div>'
            f'<div class="cap">years old<br>Born {born}{place}</div></div>')


def section(label: str, rows: list[str]) -> str:
    return f'<div class="sec"><div class="h">{label}</div>{"".join(rows)}</div>' if rows else ""


def show_row(s: dict) -> str:
    # an untitled show reads as just its venue
    title = f'<i>{esc(s["title"])}</i>' if s.get("title") else ""
    venue = f'<span class="ven">{esc(s["venue"])}</span>' if s.get("venue") else ""
    tag = '<span class="tag">group</span>' if s.get("kind") == "group" else ""
    return (f'<div class="row"><span class="yr">{s.get("year") or ""}</span>'
            f'<span class="txt">{title}{venue}{tag}</span></div>')


def card_html(a: dict) -> str:
    edu = [f'<div class="row"><span class="txt">{esc(e)}</span></div>' for e in (a.get("education") or [])[:3]]
    recent = lambda shows: sorted(shows or [], key=lambda s: -(s.get("year") or 0))[:3]
    mus = [show_row(s) for s in recent(a.get("museum_shows"))]
    sol = [show_row(s) for s in recent(a.get("solo_gallery_shows"))]
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
  html, body {{ margin: 0; width: {SIZE}px; height: {SIZE}px; background: #161618; overflow: hidden; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif; color: #fff;
         -webkit-font-smoothing: antialiased; }}
  /* The Shows tab lays its name bar and dots over the bottom ~20% of the square,
     and the show page crops ~5% off the top and bottom, so the text lives in
     the band from 6% to 79% down. */
  #band {{ position: absolute; left: 0; right: 0; top: {round(SIZE * 0.06)}px; height: {round(SIZE * 0.73)}px;
          display: flex; align-items: center; justify-content: center; }}
  #c {{ width: {SIZE - 320}px; max-height: 100%; overflow: hidden; font-size: 80px; line-height: 1.22; }}
  .name {{ font-size: 1.15em; font-weight: 600; color: rgba(255,255,255,0.9); }}
  .age {{ display: flex; align-items: flex-end; gap: 0.5em; margin: 0.05em 0 0.5em; }}
  .big {{ font-size: 5.6em; font-weight: 800; line-height: 0.86; letter-spacing: -0.04em; }}
  .age.span {{ flex-direction: column; align-items: flex-start; gap: 0.25em; }}
  .age.span .big {{ font-size: 3.4em; white-space: nowrap; }}
  .cap {{ color: rgba(255,255,255,0.62); font-size: 0.95em; padding-bottom: 0.15em; }}
  .sec {{ margin-top: 0.6em; }}
  .h {{ color: #61ADF2; font-size: 0.78em; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; margin-bottom: 0.15em; }}
  .row {{ display: flex; gap: 0.55em; margin: 0.1em 0; }}
  .yr {{ flex: 0 0 2.5em; color: rgba(255,255,255,0.5); font-variant-numeric: tabular-nums; }}
  .txt {{ flex: 1; min-width: 0; }}
  .ven {{ color: rgba(255,255,255,0.62); }}
  i + .ven::before {{ content: " · "; }}
  .tag {{ color: rgba(255,255,255,0.4); font-size: 0.8em; margin-left: 0.4em; }}
</style></head><body><div id="band"><div id="c">
  <div class="name">{esc(a["artist"])}</div>
  {age_block(a)}
  {section("Education", edu)}
  {section("Museum shows", mus)}
  {section("Recent solo gallery shows", sol)}
</div></div><script>
  // shrink the type until the block fits its safe area
  const c = document.getElementById('c');
  for (let fs = 80; fs > 40 && c.scrollHeight > c.clientHeight + 1; fs -= 2) c.style.fontSize = fs + 'px';
</script></body></html>"""


def render(a: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "card.html"
        page.write_text(card_html(a))
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        "--force-device-scale-factor=1", f"--window-size={SIZE},{SIZE}",
                        f"--user-data-dir={tmp}/profile", f"--screenshot={tmp}/card.png", page.as_uri()],
                       check=True, capture_output=True)
        # a few flat colors plus antialiasing: a 128-color palette keeps the type
        # crisp at about a third of the full-color PNG
        Image.open(f"{tmp}/card.png").convert("RGB").quantize(colors=128).save(out, optimize=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", required=True)
    ap.add_argument("--slug", nargs="*", default=[])
    ap.add_argument("--bios", type=Path, help="bios file (default content/artists/bios/<city>.json)")
    ap.add_argument("--render-only", type=Path, help="write PNGs under this dir; leave the shows alone")
    args = ap.parse_args()

    bios = json.loads((args.bios or CONTENT / "artists" / "bios" / f"{args.city}.json").read_text())
    cards: dict[str, list[str]] = {}
    for slug, artists in bios["shows"].items():
        if args.slug and slug not in args.slug:
            continue
        for a in artists:
            rel = f"images/{args.city}/{slug}/bio-{slugify(a['artist'])}.png"
            render(a, (args.render_only or CONTENT) / rel)
            cards.setdefault(slug, []).append(rel)
    if args.render_only:
        return

    shows_path = CONTENT / f"{args.city}.json"
    data = json.loads(shows_path.read_text())
    found = set()
    for s in data["shows"]:
        if s["slug"] in cards:
            found.add(s["slug"])
            # the bio card is the second picture, right after the lead artwork
            art = [r for r in s["images"] if not is_bio(r)]
            s["images"] = art[:1] + cards[s["slug"]] + art[1:]
    shows_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    for slug, rels in cards.items():
        print(f"  {slug}: {len(rels)} card(s)" + ("" if slug in found else "  (no such show)"))


if __name__ == "__main__":
    main()
