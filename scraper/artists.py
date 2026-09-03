"""Artists dataset (backend only, no UI): who shows where, now and historically.

    python artists.py build  --city los-angeles [--apply] [--today YYYY-MM-DD]
    python artists.py report --city los-angeles

Sources merged by ``build`` (docs/GALLERIES.md, "Artists"):
  (a) GalleryReport rosters + exhibition archives, content/venues/reports/<city>/*.json
      (relation represented|exhibited|estate; shows_history from the archive),
  (b) the show pool (content/<city>.json + content/pending/<city>.json), relation
      "showed" + shows_current / shows_upcoming from the show dates,
  (c) curation signals of kind artist_activity / award (aliases + activity only).

Dedup: ``artist_id`` is the slug of the NFKC-normalized, diacritic-stripped, lowercased
name with punctuation collapsed. Two-token Latin names whose swapped order also appears
in the sources are merged and both orders kept as aliases. When one id is claimed by
what looks like two different people (see ``looks_like_collision``) the record is kept
as two entries and the pair is written to content/artists/merge_review.json instead of
being merged. content/artists/merges.json ``{"merge": [[keep, drop], ...], "split":
[...]}`` is applied last and wins. $0, no network.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import curation_store as store  # noqa: E402
import tools  # noqa: E402
import venues  # noqa: E402

ARTISTS_DIR = tools.CONTENT_DIR / "artists"
REPORTS_DIR = tools.CONTENT_DIR / "venues" / "reports"
SPEND_REPORTS = tools.CONTENT_DIR / "spend" / "reports"
SCHEMA = 1

GROUP_LABEL_RE = re.compile(
    r"^(various( artists)?|group( show| exhibition)?|multiple artists|artists?|"
    r"collective|selected artists|tba|tbd|n/?a|unknown|anonymous)$", re.I)
PAREN_RE = re.compile(r"\s*\([^)]*\)")
SPLIT_RE = re.compile(r"\s*(?:,|&|/|;|\+|\band\b|\bwith\b)\s*", re.I)
TRAIL_RE = re.compile(r"\b(the\s+)?(great wall of .*|collective|studio)$", re.I)
CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
RELATION_RANK = {"represented": 3, "estate": 3, "exhibited": 2, "showed": 1}


# --- names ----------------------------------------------------------------------

def norm_name(name: str | None) -> str:
    """NFKC + strip diacritics + lowercase + collapse punctuation to single spaces."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", str(name))
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^\w]+", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def artist_id(name: str | None) -> str:
    n = norm_name(name)
    if not n:
        return ""
    if CJK_RE.search(n):
        # keep the script; ids must be stable and non-empty for non-Latin names
        return "a-" + re.sub(r"\s+", "-", n)
    return re.sub(r"\s+", "-", n)


def split_artists(raw: str | None) -> list[str]:
    """'A, B and C (with X)' -> ['A', 'B', 'C']; group-show labels -> []."""
    if not raw or not str(raw).strip():
        return []
    s = PAREN_RE.sub("", str(raw)).strip()
    out: list[str] = []
    for part in SPLIT_RE.split(s):
        p = part.strip(" .,-–—")
        if not p or GROUP_LABEL_RE.match(p) or TRAIL_RE.search(p):
            continue
        if len(norm_name(p)) < 2:
            continue
        out.append(p)
    return out


def swapped(name: str) -> str | None:
    """'Family Given' <-> 'Given Family' for two-token Latin names only."""
    toks = name.split()
    if len(toks) != 2 or CJK_RE.search(name):
        return None
    return f"{toks[1]} {toks[0]}"


def looks_like_collision(a: dict, b: dict) -> bool:
    """Same id, but plausibly two people: disjoint gallery sets in different
    cities AND different first-name initials among their raw aliases, or a
    source that explicitly disambiguates ('(b. 19xx)' / '(painter)')."""
    ga = {g["venue_id"] for g in a.get("galleries", [])}
    gb = {g["venue_id"] for g in b.get("galleries", [])}
    ca = set(a.get("_cities", [])) or {a.get("city")}
    cb = set(b.get("_cities", [])) or {b.get("city")}
    if ga and gb and not (ga & gb) and not (ca & cb):
        ia = {n.split()[0][0] for n in a.get("aliases", []) if n.split()}
        ib = {n.split()[0][0] for n in b.get("aliases", []) if n.split()}
        if ia and ib and not (ia & ib):
            return True
    for rec in (a, b):
        for al in rec.get("_raw", []):
            if re.search(r"\((b\.|born|painter|sculptor|photographer|\d{4}[–-]\d{4})", al, re.I):
                return True
    return False


# --- sources ------------------------------------------------------------------------

def _iso(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def load_reports(city: str) -> list[dict]:
    d = REPORTS_DIR / city
    out = []
    if not d.exists():
        return out
    for f in sorted(d.glob("*.json")):
        try:
            r = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(r, dict) and r.get("venue_id"):
            r["_path"] = str(f.relative_to(tools.CONTENT_DIR.parent))
            out.append(r)
    return out


class Builder:
    def __init__(self, city: str, today: date | None = None, registry: dict | None = None):
        self.city = city
        self.today = today or date.today()
        reg = registry if registry is not None else venues.load_registry(city)
        self.registry = venues.index_by_id(reg)
        self.records: dict[str, dict] = {}
        self.review: list[dict] = []

    # -- record access ----------------------------------------------------------
    def _rec(self, name: str) -> dict | None:
        aid = artist_id(name)
        if not aid:
            return None
        rec = self.records.get(aid)
        if rec is None:
            rec = {"artist_id": aid, "name": name.strip(), "aliases": [], "_raw": [],
                   "galleries": [], "shows_current": [], "shows_upcoming": [],
                   "shows_history": [], "activity": [], "wiki": None, "_cities": [self.city]}
            self.records[aid] = rec
        raw = name.strip()
        if raw not in rec["_raw"]:
            rec["_raw"].append(raw)
        if raw != rec["name"] and raw not in rec["aliases"]:
            rec["aliases"].append(raw)
        return rec

    def _venue_status(self, vid: str | None) -> str | None:
        v = self.registry.get(vid or "")
        return ((v or {}).get("verification") or {}).get("status")

    def add_gallery(self, rec: dict, venue_id: str | None, relation: str,
                    since: int | None = None, source_url: str | None = None) -> None:
        if not venue_id:
            return
        for g in rec["galleries"]:
            if g["venue_id"] == venue_id:
                if RELATION_RANK.get(relation, 0) > RELATION_RANK.get(g["relation"], 0):
                    g["relation"] = relation
                    g["source_url"] = source_url or g.get("source_url")
                if since and (g.get("since") is None or since < g["since"]):
                    g["since"] = since
                return
        rec["galleries"].append({"venue_id": venue_id, "relation": relation, "since": since,
                                 "source_url": source_url,
                                 "venue_verification": self._venue_status(venue_id)})

    # -- (a) reports -------------------------------------------------------------
    def add_reports(self, reports: list[dict]) -> int:
        n = 0
        for r in reports:
            vid = r["venue_id"]
            for a in r.get("roster") or []:
                rec = self._rec(a.get("name") or "")
                if not rec:
                    continue
                rel = a.get("status") if a.get("status") in RELATION_RANK else "exhibited"
                self.add_gallery(rec, vid, rel, source_url=a.get("source_url"))
                n += 1
            for ex in r.get("exhibitions") or []:
                names = ex.get("artists") or []
                if isinstance(names, str):
                    names = split_artists(names)
                for nm in names:
                    rec = self._rec(nm)
                    if not rec:
                        continue
                    year = ex.get("year") or (int(ex["start"][:4]) if ex.get("start") else None)
                    self.add_gallery(rec, vid, "exhibited", since=year, source_url=ex.get("source_url"))
                    row = {"venue_id": vid, "title": ex.get("title"), "start": ex.get("start"),
                           "end": ex.get("end"), "year": year, "kind": ex.get("kind"),
                           "source_url": ex.get("source_url")}
                    if row not in rec["shows_history"]:
                        rec["shows_history"].append(row)
                    n += 1
        return n

    # -- (b) show pool -------------------------------------------------------------
    def add_shows(self, shows: list[dict]) -> int:
        n = 0
        for s in shows:
            vid = s.get("venue_id") or venues.venue_id((s.get("venue") or {}).get("name") or "")
            start, end = _iso(s.get("start_date")), _iso(s.get("end_date"))
            if end and end < self.today:
                bucket = "shows_history"
            elif start and start > self.today:
                bucket = "shows_upcoming"
            else:
                bucket = "shows_current"
            for nm in split_artists(s.get("artist")):
                rec = self._rec(nm)
                if not rec:
                    continue
                self.add_gallery(rec, vid, "showed", since=start.year if start else None,
                                 source_url=(s.get("source_urls") or [None])[0])
                if bucket == "shows_history":
                    row = {"venue_id": vid, "title": s.get("title"), "start": s.get("start_date"),
                           "end": s.get("end_date"), "year": end.year if end else None,
                           "kind": "pool", "source_url": (s.get("source_urls") or [None])[0]}
                else:
                    row = {"slug": s.get("slug"), "venue_id": vid, "title": s.get("title"),
                           "start": s.get("start_date"), "end": s.get("end_date")}
                if row not in rec[bucket]:
                    rec[bucket].append(row)
                n += 1
        return n

    # -- (c) signals ---------------------------------------------------------------
    def add_signals(self, rows: list[dict]) -> int:
        n = 0
        for s in rows:
            if s.get("kind") not in ("artist_activity", "award"):
                continue
            ref = s.get("show_ref") or {}
            for nm in split_artists(ref.get("artist")):
                aid = artist_id(nm)
                rec = self.records.get(aid)
                if not rec:
                    continue   # activity alone does not create an artist
                if nm.strip() not in rec["_raw"]:
                    rec["_raw"].append(nm.strip())
                    if nm.strip() != rec["name"]:
                        rec["aliases"].append(nm.strip())
                item = {"kind": s["kind"], "source": (s.get("source") or {}).get("id"),
                        "url": s.get("source_url"), "snippet": (s.get("snippet") or "")[:200],
                        "date": s.get("published_at")}
                if item not in rec["activity"]:
                    rec["activity"].append(item)
                n += 1
        return n

    # -- dedup ----------------------------------------------------------------------
    def merge_into(self, keep: dict, drop: dict) -> None:
        for al in [drop["name"]] + drop["aliases"]:
            if al != keep["name"] and al not in keep["aliases"]:
                keep["aliases"].append(al)
        keep["_raw"] = list(dict.fromkeys(keep["_raw"] + drop["_raw"]))
        for g in drop["galleries"]:
            self.add_gallery(keep, g["venue_id"], g["relation"], g.get("since"), g.get("source_url"))
        for k in ("shows_current", "shows_upcoming", "shows_history", "activity"):
            for row in drop[k]:
                if row not in keep[k]:
                    keep[k].append(row)
        keep["_cities"] = sorted(set(keep["_cities"]) | set(drop["_cities"]))

    def dedup_name_order(self) -> int:
        """Merge 'Given Family' with 'Family Given' when both orders appear."""
        merged = 0
        for aid in sorted(self.records):
            rec = self.records.get(aid)
            if not rec:
                continue
            sw = swapped(rec["name"])
            if not sw:
                continue
            other = self.records.get(artist_id(sw))
            if other and other is not rec and not looks_like_collision(rec, other):
                self.merge_into(rec, other)
                del self.records[other["artist_id"]]
                merged += 1
        return merged

    def apply_merges(self, merges: dict | None) -> int:
        n = 0
        for pair in (merges or {}).get("merge", []):
            if len(pair) != 2:
                continue
            keep, drop = self.records.get(pair[0]), self.records.get(pair[1])
            if keep and drop and keep is not drop:
                self.merge_into(keep, drop)
                del self.records[pair[1]]
                n += 1
        for aid in (merges or {}).get("split", []):
            # a split just flags for review; the data cannot separate people on its own
            if aid in self.records:
                self.review.append({"artist_id": aid, "reason": "split requested in merges.json",
                                    "aliases": self.records[aid]["aliases"]})
        return n

    def flag_collisions(self) -> None:
        for rec in self.records.values():
            # collisions inside one id show up as galleries in different cities; the
            # dataset is per city so this is mostly aliases disagreeing on the initial.
            raws = rec["_raw"]
            initials = {norm_name(r).split()[0] for r in raws if norm_name(r).split()}
            firsts = {norm_name(r).split()[0][0] for r in raws if norm_name(r).split()}
            if len(firsts) > 1 and len(initials) > 1 and len(rec["galleries"]) > 1:
                self.review.append({"artist_id": rec["artist_id"], "reason": "aliases differ in first name",
                                    "aliases": raws,
                                    "galleries": [g["venue_id"] for g in rec["galleries"]]})

    def result(self) -> dict:
        arts = []
        for rec in self.records.values():
            r = {k: v for k, v in rec.items() if not k.startswith("_")}
            r["galleries"].sort(key=lambda g: (-RELATION_RANK.get(g["relation"], 0), g["venue_id"]))
            r["shows_history"].sort(key=lambda s: (s.get("start") or s.get("year") and str(s["year"]) or ""), reverse=True)
            arts.append(r)
        arts.sort(key=lambda a: (-len(a["galleries"]), a["artist_id"]))
        return {"schema": SCHEMA, "city": self.city, "updated": int(time.time()),
                "today": self.today.isoformat(), "artists": arts, "review": self.review}


def build(city: str, today: date | None = None, reports: list[dict] | None = None,
          shows: list[dict] | None = None, signals: list[dict] | None = None,
          merges: dict | None = None, registry: dict | None = None) -> dict:
    b = Builder(city, today, registry)
    b.add_reports(load_reports(city) if reports is None else reports)
    b.add_shows(tools.all_city_shows(city) if shows is None else shows)
    b.add_signals(store.load_signals(city) if signals is None else signals)
    b.dedup_name_order()
    if merges is None:
        mf = ARTISTS_DIR / "merges.json"
        merges = json.loads(mf.read_text()) if mf.exists() else {}
    b.apply_merges(merges)
    b.flag_collisions()
    return b.result()


def write_dataset(city: str, data: dict) -> Path:
    ARTISTS_DIR.mkdir(parents=True, exist_ok=True)
    out = ARTISTS_DIR / f"{city}.json"
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    (ARTISTS_DIR / "merge_review.json").write_text(json.dumps(
        {**_load_json(ARTISTS_DIR / "merge_review.json"), city: data["review"]}, indent=1, ensure_ascii=False))
    rebuild_index()
    return out


def _load_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except json.JSONDecodeError:
        return {}


def rebuild_index() -> Path:
    idx: dict[str, dict] = {}
    for f in sorted(ARTISTS_DIR.glob("*.json")):
        if f.name in ("index.json", "merges.json", "merge_review.json"):
            continue
        d = _load_json(f)
        for a in d.get("artists", []):
            e = idx.setdefault(a["artist_id"], {"name": a["name"], "cities": [], "galleries": 0})
            e["cities"].append(d.get("city") or f.stem)
            e["galleries"] += len(a.get("galleries", []))
    out = ARTISTS_DIR / "index.json"
    out.write_text(json.dumps({"schema": SCHEMA, "updated": int(time.time()), "artists": idx},
                              indent=1, ensure_ascii=False))
    return out


# --- report ---------------------------------------------------------------------------

def _vname(reg: dict[str, dict], vid: str | None) -> str:
    v = reg.get(vid or "")
    return v["name"] if v else (vid or "?")


def render_report(data: dict, reg: dict[str, dict]) -> tuple[str, dict]:
    city = data["city"]
    rows = []
    md = [f"# Artists — {city}", "",
          f"Generated {data.get('today')} from content/artists/{city}.json "
          f"({len(data['artists'])} artists, {len(data.get('review') or [])} in merge review).", "",
          "| artist | galleries | current shows | history |", "|---|---|---|---|"]
    for a in data["artists"]:
        gal = "; ".join(f"{_vname(reg, g['venue_id'])} ({g['relation']})" for g in a["galleries"])
        cur = "; ".join(f"{s.get('title') or '—'} @ {_vname(reg, s['venue_id'])} {s.get('start') or '?'}→{s.get('end') or '?'}"
                        for s in a["shows_current"] + a["shows_upcoming"])
        hist = a["shows_history"]
        hist_s = f"{len(hist)}" + ("" if not hist else ": " + "; ".join(
            f"{h.get('title') or '—'} @ {_vname(reg, h['venue_id'])} {h.get('year') or (h.get('start') or '')[:4]}"
            for h in hist[:5]))
        md.append(f"| {a['name']} | {gal} | {cur} | {hist_s} |")
        rows.append({"artist_id": a["artist_id"], "name": a["name"], "aliases": a["aliases"],
                     "galleries": [{**g, "venue": _vname(reg, g["venue_id"])} for g in a["galleries"]],
                     "shows_current": a["shows_current"] + a["shows_upcoming"],
                     "shows_history_count": len(hist), "shows_history_latest": hist[:5]})
    md.append("")
    return "\n".join(md), {"city": city, "today": data.get("today"), "artists": rows}


def cmd_build(args) -> int:
    today = date.fromisoformat(args.today) if args.today else None
    data = build(args.city, today)
    arts = data["artists"]
    multi = sum(1 for a in arts if len(a["galleries"]) > 1)
    print(f"{args.city}: {len(arts)} artists, {multi} with >1 gallery, "
          f"{sum(len(a['shows_current']) for a in arts)} current show links, "
          f"{sum(len(a['shows_history']) for a in arts)} history rows, "
          f"{len(data['review'])} in merge review")
    if args.apply:
        print(f"wrote {write_dataset(args.city, data)}")
    else:
        print("dry run — pass --apply to write content/artists/")
    return 0


def cmd_report(args) -> int:
    f = ARTISTS_DIR / f"{args.city}.json"
    if not f.exists():
        print(f"no dataset at {f}; run build --apply first")
        return 1
    data = json.loads(f.read_text())
    reg = venues.index_by_id(venues.load_registry(args.city))
    md, js = render_report(data, reg)
    SPEND_REPORTS.mkdir(parents=True, exist_ok=True)
    (SPEND_REPORTS / f"artists-{args.city}.md").write_text(md)
    (SPEND_REPORTS / f"artists-{args.city}.json").write_text(json.dumps(js, indent=1, ensure_ascii=False))
    print(f"wrote {SPEND_REPORTS / f'artists-{args.city}.md'} ({len(js['artists'])} artists)")
    return 0


def main(argv: list[str] | None = None) -> int:
    from cities import CITIES
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--city", required=True, choices=sorted(CITIES))
    b.add_argument("--apply", action="store_true")
    b.add_argument("--today")
    b.set_defaults(fn=cmd_build)
    r = sub.add_parser("report")
    r.add_argument("--city", required=True, choices=sorted(CITIES))
    r.set_defaults(fn=cmd_report)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
