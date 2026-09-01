"""Build the Gallery Browser web demo into dist/gallery-browser-demo/.

Usage:
    scraper/.venv/bin/python webdemo/build.py [--img-cap 7] [--max-width 1080] [--quality 78]
                                              [--full-side 3840] [--full-quality 80]

Each image ships as a 1080px proxy plus, when the source out-resolves it, an
@full variant (longest side capped at --full-side) the app swaps in on zoom.

Re-runnable: image and map-tile work is cached, so post-scrape rebuilds are fast.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import cityconfig
import images as image_pipe
import mapgen

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist" / "gallery-browser-demo"
STATIC = ["styles.css", "icons.js", "app.js", "map_maplibre.js"]


def human(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


def cache_bust() -> None:
    """Version local asset URLs in index.html so phones never run a stale bundle."""
    index = DIST / "index.html"
    html = index.read_text(encoding="utf-8")
    for name in STATIC + ["data.js"]:
        digest = hashlib.md5((DIST / name).read_bytes()).hexdigest()[:10]
        html = html.replace(f'"{name}"', f'"{name}?v={digest}"')
    index.write_text(html, encoding="utf-8")


def write_icons() -> None:
    """App icon: guideBlue field with the 🏛 glyph (falls back to plain blue)."""
    from PIL import Image, ImageDraw, ImageFont
    base = Image.new("RGB", (1024, 1024), "#61ADF2")
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Apple Color Emoji.ttc", 160)
        glyph = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        ImageDraw.Draw(glyph).text((10, 10), "🏛", font=font, embedded_color=True)
        glyph = glyph.resize((640, 640), Image.LANCZOS)
        base.paste(glyph, ((1024 - 640) // 2, (1024 - 640) // 2 - 20), glyph)
    except Exception as exc:
        print(f"  note: emoji icon unavailable ({exc}) — plain color icon")
    base.resize((512, 512), Image.LANCZOS).save(DIST / "icon-512.png")
    base.resize((180, 180), Image.LANCZOS).save(DIST / "apple-touch-icon.png")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--img-cap", type=int, default=7, help="max images per show")
    parser.add_argument("--max-width", type=int, default=1080)
    parser.add_argument("--quality", type=int, default=78)
    parser.add_argument("--full-side", type=int, default=3840,
                        help="longest-side cap for full-res variants (0 disables them)")
    parser.add_argument("--full-quality", type=int, default=80)
    args = parser.parse_args()

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)

    cities = cityconfig.discover()
    all_shows: list[dict] = []
    img_bytes = 0
    full_bytes = 0
    full_count = 0
    map_bytes = 0

    for cty in cities:
        shows = cityconfig.load_shows(cty["key"])
        if shows and not any(s.get("featured") for s in shows):
            print(f"  note: {cty['key']} has no featured shows — featuring the first 3")
            for s in shows[:3]:
                s["featured"] = True

        # city basemap for the venue-page card
        points = [(s["venue"]["latitude"], s["venue"]["longitude"]) for s in shows]
        img, meta = mapgen.build_city_map(points or [(cty["center"]["lat"], cty["center"]["lng"])],
                                          (cty["center"]["lat"], cty["center"]["lng"]))
        map_rel = f"maps/{cty['key']}.webp"
        map_path = DIST / map_rel
        map_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(map_path, "WEBP", quality=70, method=6)
        map_bytes += map_path.stat().st_size
        cty["map"] = {"src": map_rel, "w": img.width, "h": img.height}

        for s in shows:
            out_imgs = []
            for rel in s["images"][: args.img_cap]:
                src = cityconfig.CONTENT_DIR / rel
                if not src.is_file():
                    print(f"  warning: missing image {rel} — skipped")
                    continue
                out_rel = str(Path(rel).with_suffix(".webp"))
                dest = DIST / out_rel
                image_pipe.process(src, dest, args.max_width, args.quality)
                img_bytes += dest.stat().st_size
                entry = {"src": out_rel}
                if args.full_side:
                    full_rel = str(Path(rel).with_suffix("")) + "@full.webp"
                    full_dest = DIST / full_rel
                    if image_pipe.process_full(src, full_dest, args.max_width,
                                               args.full_side, args.full_quality):
                        full_bytes += full_dest.stat().st_size
                        full_count += 1
                        entry["full"] = full_rel
                out_imgs.append(entry)
            if not out_imgs:
                print(f"  warning: show {s['city']}/{s['slug']} has no usable images — skipped")
                continue
            v = s["venue"]
            map_x, map_y = mapgen.project(v["latitude"], v["longitude"], meta)
            all_shows.append({
                "city": s["city"], "slug": s["slug"], "title": s["title"],
                "artist": s.get("artist"), "startDate": s["start_date"], "endDate": s["end_date"],
                "description": s["description"], "editorsPick": s["editors_pick"],
                "featured": s["featured"], "reception": s.get("reception"),
                "images": out_imgs, "sourceUrls": s.get("source_urls", []),
                "venue": {
                    "name": v["name"], "isMuseum": v["is_museum"], "address": v["address"],
                    "addressDetail": v.get("address_detail"), "neighborhood": v["neighborhood"],
                    "hours": v["hours"], "phone": v.get("phone"), "website": v.get("website"),
                    "lat": v["latitude"], "lng": v["longitude"],
                    "mapX": round(map_x, 1), "mapY": round(map_y, 1),
                },
            })

    data = {
        "defaultCity": cityconfig.DEFAULT_CITY,
        "attribution": mapgen.ATTRIBUTION,
        "cities": cities,
        "shows": all_shows,
    }
    data_js = "window.DEMO_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n"
    (DIST / "data.js").write_text(data_js, encoding="utf-8")

    shutil.copy(HERE / "template.html", DIST / "index.html")
    for name in STATIC:
        shutil.copy(HERE / name, DIST / name)
    cache_bust()
    (DIST / "vercel.json").write_text(json.dumps({"trailingSlash": False}) + "\n")
    write_icons()
    (DIST / "manifest.json").write_text(json.dumps({
        "name": "Gallery Browser", "short_name": "Galleries",
        "display": "standalone", "start_url": ".",
        "background_color": "#000000", "theme_color": "#000000",
        "icons": [{"src": "icon-512.png", "sizes": "512x512", "type": "image/png"}],
    }, indent=2) + "\n")

    code_bytes = sum((DIST / n).stat().st_size for n in STATIC + ["index.html"])
    total = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file())
    print(f"\nBuild -> {DIST}")
    print(f"  cities: {len(cities)}   shows: {len(all_shows)}")
    print(f"  code {human(code_bytes)} | data.js {human((DIST / 'data.js').stat().st_size)} "
          f"| proxies {human(img_bytes)} | full-res {human(full_bytes)} ({full_count}) "
          f"| maps {human(map_bytes)} | total {human(total)}")


if __name__ == "__main__":
    main()
