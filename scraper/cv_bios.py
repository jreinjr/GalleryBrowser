"""Artist CVs: bring researched CVs into content/artists/cvs/ and give each
show artist that has a CV but no bio card a bio derived from it.

    scraper/.venv/bin/python scraper/cv_bios.py --import DIR          # DIR/*.json -> content/artists/cvs/
    scraper/.venv/bin/python scraper/cv_bios.py --city los-angeles --report R.json [R2.json ...]

A CV file (content/artists/cvs/<artist-slug>.json) holds the artist's whole
CV: born/died, birthplace, lives_works, cv_kind ("published" when transcribed
from the artist's or gallery's CV, "rebuilt" when assembled from biographical
pages), cv_url, sections [{kind, title, items}] in the CV's own order, and
sources. build.py ships it for the CV page the bio card opens.

A report maps show slugs to their artists ({"shows": {slug: [name, ...]}}).
For each artist with a CV and a birth year but no bio on that show, a bio is
added to content/artists/bios/<city>.json (education, latest museum shows,
latest solo gallery shows); the slugs that got one are printed for
bio_cards.py --slug.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from bio_cards import slugify

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
CVS = CONTENT / "artists" / "cvs"
KINDS = {"education", "solo", "group", "collections", "awards", "residencies", "commissions", "bibliography", "other"}


def valid(d: dict) -> list[str]:
    errs = [f"missing {k}" for k in ("artist", "cv_kind", "sections", "sources") if not d.get(k)]
    errs += [f"section kind {s.get('kind')}" for s in d.get("sections") or [] if s.get("kind") not in KINDS]
    return errs


def year(it: dict) -> int | None:
    m = re.match(r"\d{4}", str(it.get("year") or ""))
    return int(m.group()) if m else None


def bio_from_cv(d: dict) -> dict:
    items = lambda kind: [it for s in d["sections"] if s["kind"] == kind for it in s.get("items") or []]
    latest = lambda xs: sorted(xs, key=lambda it: -(year(it) or 0))[:4]
    row = lambda it, kind: {k: v for k, v in {"year": year(it), "title": it.get("title") or it.get("text"),
                                              "venue": it.get("venue"), "kind": kind}.items() if v}
    museum = [row(it, "solo") for it in items("solo") if it.get("museum")]
    if len(museum) < 2:
        museum += [row(it, "group") for it in items("group") if it.get("museum")]
    bio = {
        "artist": d["artist"], "born": d.get("born"), "died": d.get("died"), "birthplace": d.get("birthplace"),
        "education": [", ".join(x for x in (it.get("text"), it.get("year")) if x) for it in items("education")][:3],
        "museum_shows": latest(museum),
        "solo_gallery_shows": [{k: v for k, v in r.items() if k != "kind"}
                               for r in latest([row(it, "solo") for it in items("solo") if not it.get("museum")])],
        "sources": d["sources"],
        "notes": "Derived from the artist's CV (content/artists/cvs).",
    }
    return {k: v for k, v in bio.items() if v}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--import", dest="imp", type=Path)
    ap.add_argument("--city")
    ap.add_argument("--report", nargs="*", type=Path, default=[])
    args = ap.parse_args()

    if args.imp:
        CVS.mkdir(parents=True, exist_ok=True)
        n = 0
        for f in sorted(args.imp.glob("*.json")):
            d = json.loads(f.read_text())
            if errs := valid(d):
                print(f"  skip {f.name}: {'; '.join(errs)}")
                continue
            dest = CVS / f"{slugify(d['artist'])}.json"
            dest.write_text(json.dumps(d, indent=1, ensure_ascii=False) + "\n")
            n += 1
        print(f"  imported {n} CV(s) into {CVS.relative_to(ROOT)}")

    if args.city and args.report:
        path = CONTENT / "artists" / "bios" / f"{args.city}.json"
        bios = json.loads(path.read_text()) if path.exists() else {"_note": "Artist bio cards (scraper/bio_cards.py).", "shows": {}}
        slugs = {s["slug"] for s in json.loads((CONTENT / f"{args.city}.json").read_text())["shows"]}
        added: list[str] = []
        for r in args.report:
            for slug, names in (json.loads(r.read_text()).get("shows") or {}).items():
                if slug not in slugs:
                    continue
                have = {slugify(a["artist"]) for a in bios["shows"].get(slug, [])}
                for name in names:
                    f = CVS / f"{slugify(name)}.json"
                    if slugify(name) in have or not f.exists():
                        continue
                    d = json.loads(f.read_text())
                    if not d.get("born"):
                        print(f"  {slug}: {name} has no birth year — no bio card")
                        continue
                    bios["shows"].setdefault(slug, []).append(bio_from_cv(d))
                    if slug not in added:
                        added.append(slug)
        path.write_text(json.dumps(bios, indent=1, ensure_ascii=False) + "\n")
        print("  bios added for: " + " ".join(added))


if __name__ == "__main__":
    main()
