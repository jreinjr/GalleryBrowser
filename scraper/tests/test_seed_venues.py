"""Unit tests for seed_venues + the registry primitives it relies on (stdlib unittest).

    scraper/.venv/bin/python -m unittest discover -s scraper/tests

No network: HTML/JSON fixtures are inline, the Google calls are monkeypatched,
and every registry / evidence / cache path is redirected to a temp directory.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import seed_venues as sv  # noqa: E402
import tools  # noqa: E402
import venues  # noqa: E402
from cities import CITIES  # noqa: E402

CITY = "los-angeles"
CFG = CITIES[CITY]
TODAY = date(2026, 9, 1)

GPLA_LIST_HTML = """
<div class="grid"><div class="col-span-3 font-bold">
  <a href="https://galleryplatform.la/galleries/1301-pe" data-uuid="x">1301 PE</a></div>
  <div class="col-span-1"><span class="text-xs ui-pill">Central</span></div>
  <div><span><a href="https://galleryplatform.la/galleries/1301-pe/exhibitions/as-the-moonbird-flies">As The Moonbird Flies</a></span></div>
</div>
<div class="grid"><div class="col-span-3 font-bold">
  <a href="https://galleryplatform.la/galleries/no-pill" data-uuid="y">No Pill &amp; Co</a></div>
  <div class="col-span-1"></div>
</div>
<div class="grid"><div class="col-span-3 font-bold">
  <a href="https://galleryplatform.la/galleries/the-pit" data-uuid="z">The Pit</a></div>
  <div class="col-span-1"><span class="text-xs ui-pill">East</span></div>
</div>
"""

GPLA_DETAIL_HTML = """
<h1>1301 PE</h1><div class="details-table ">
<div class="details-table--row flex"><div class="details-table--row-title">
    Links  </div><div class="flex-1">
    <a href="https://www.instagram.com/1301pe">@1301pe</a>
    <a href="https://www.1301pe.com">1301pe.com</a></div></div>
<div class="details-table--row flex"><div class="details-table--row-title">
    Contact  </div><div class="flex-1">
    <a href="tel:3239385822">323 938 5822</a> <a href="mailto:x">x</a></div></div>
<div class="details-table--row flex"><div class="details-table--row-title">
    Address  </div><div class="flex-1">
    <a href="https://goo.gl/maps/abc">6150 Wilshire Blvd, Los Angeles, CA 90048</a></div></div>
<div class="details-table--row flex"><div class="details-table--row-title">
    Hours  </div><div class="flex-1">
      <div class="space-x-5"><span>Tuesday–Saturday</span><span>11am–6pm</span></div>
      <div class="space-x-5"><span>Sunday</span><span>By appointment</span></div>
    </div></div></div>
"""

CARLA_HTML = """
<html><body><section id="other">nope</section>
<section id="distribution" class="cd-section"><div class="title">Distribution</div>
<div class="sub-title ">Central</div><div class="element">
<a href="https://www.1301pe.com/">1301 PE</a><br />
<a href=“https://www.7811gallery.com/”>7811 Gallery</a><br />
<a href="https://anatebgi.com/">Anat Ebgi (Wilshire) </a><br />
<a href="https://paradiseframingla.com/">Paradise Framing</a><br />
<a href="https://www.arcanabooks.com/">Arcana Books</a><br />
<a href= "https://www.montevistaprojects.com/">Monte Vista Projects</a></div>
<div class="sub-title type-2">East</div><div class="element">
<a href="https://www.the-pit.la/">The Pit Los Angeles</a><br />
<a href="https://www.thisisumi.co/">Umico Printing and Framing</a></div>
<div class="sub-title ">Outside L.A.</div><div class="element">
<a href="https://beverlys.nyc/">Beverly's (New York, NY)</a></div>
</section><section id="after"><a href="https://x.com/">X</a></section></body></html>
"""

VENUE_PAGE = ("---\ntitle: Exhibitions | Monte Vista Projects\nmeta-og:site_name: Monte Vista Projects\n---\n\n"
              "Monte Vista Projects is an artist-run gallery in Highland Park, Los Angeles.\n"
              "Exhibitions\nCurrent exhibitions on view through October. Opening reception Saturday.\n"
              "5442 Monte Vista St, Los Angeles, CA 90042\n" + "Gallery hours Saturday and Sunday 12-5pm.\n" * 20)


def _venue(vid, name, zone=None, lat=None, lng=None, status="active", website=None, **kw):
    v = venues.empty_venue(vid, name)
    v.update({"neighborhood": zone, "latitude": lat, "longitude": lng, "status": status,
              "website": website, **kw})
    return v


class TempEnv(unittest.TestCase):
    """Registry, evidence and Places cache redirected to a temp dir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._saved = (tools.CONTENT_DIR, venues.VENUES_DIR, venues.EVIDENCE_DIR,
                       venues.EVIDENCE_INDEX, sv.PLACES_CACHE_DIR)
        tools.CONTENT_DIR = root / "content"
        venues.VENUES_DIR = root / "content" / "venues"
        venues.EVIDENCE_DIR = root / "evidence"
        venues.EVIDENCE_INDEX = root / "evidence" / "index.jsonl"
        sv.PLACES_CACHE_DIR = root / "places"
        (root / "content").mkdir()

    def tearDown(self):
        (tools.CONTENT_DIR, venues.VENUES_DIR, venues.EVIDENCE_DIR,
         venues.EVIDENCE_INDEX, sv.PLACES_CACHE_DIR) = self._saved
        self.tmp.cleanup()

    def write_registry(self, vs):
        venues.save_registry(CITY, {"schema": 1, "city": CITY, "updated": None, "venues": vs})


class ParserTests(unittest.TestCase):
    def test_parse_gpla_list(self):
        rows = sv.parse_gpla_list(GPLA_LIST_HTML)
        self.assertEqual([r["slug"] for r in rows], ["1301-pe", "no-pill", "the-pit"])
        self.assertEqual(rows[0], {"slug": "1301-pe", "name": "1301 PE", "region": "Central"})
        self.assertEqual(rows[1]["name"], "No Pill & Co")
        self.assertIsNone(rows[1]["region"])            # never borrows the next row's pill
        self.assertEqual(rows[2]["region"], "East")

    def test_parse_gpla_detail(self):
        d = sv.parse_gpla_detail(GPLA_DETAIL_HTML)
        self.assertEqual(d["website"], "https://www.1301pe.com")   # instagram skipped
        self.assertEqual(d["phone"], "323 938 5822")
        self.assertEqual(d["address"], "6150 Wilshire Blvd, Los Angeles, CA 90048")
        self.assertEqual(d["hours"], ["Tuesday–Saturday 11am–6pm", "Sunday By appointment"])
        self.assertEqual(sv.parse_gpla_detail("<p>nothing</p>"),
                         {"website": None, "phone": None, "address": None, "hours": []})

    def test_parse_carla(self):
        rows = sv.parse_carla(CARLA_HTML)
        names = [r["name"] for r in rows]
        self.assertEqual(names, ["1301 PE", "7811 Gallery", "Anat Ebgi", "Monte Vista Projects",
                                 "The Pit Los Angeles"])
        self.assertEqual(rows[1]["website"], "https://www.7811gallery.com/")   # curly-quote href
        self.assertEqual(rows[3]["website"], "https://www.montevistaprojects.com/")
        self.assertEqual({r["group"] for r in rows}, {"Central", "East"})
        self.assertNotIn("Beverly's", names)                                   # Outside L.A. dropped
        raw = sv.parse_carla(CARLA_HTML, deny=False)
        self.assertEqual(len(raw), 8)
        denied = {r["name"]: sv.carla_deny_reason(r) for r in raw if sv.carla_deny_reason(r)}
        self.assertEqual(denied, {"Paradise Framing": "name:framing", "Arcana Books": "name:books",
                                  "Umico Printing and Framing": "name:printing"})
        self.assertEqual(sv.parse_carla("<html>no section</html>"), [])


class PlacesFilterTests(unittest.TestCase):
    def place(self, name, types=("art_gallery", "point_of_interest"), status="OPERATIONAL"):
        return {"place_id": "p", "name": name, "types": list(types), "status": status,
                "lat": 34.0, "lng": -118.0}

    def test_filter_and_kind(self):
        self.assertIsNone(sv.place_skip_reason(self.place("Night Gallery")))
        self.assertEqual(sv.place_skip_reason(self.place("Paradise Framing")), "name:framing")
        self.assertEqual(sv.place_skip_reason(self.place("Gone Gallery", status="CLOSED_PERMANENTLY")),
                         "closed_permanently")
        self.assertEqual(sv.place_skip_reason(self.place("Sofa Palace", types=("furniture_store", "store"))),
                         "type:furniture_store")
        self.assertIsNone(sv.place_skip_reason(self.place("Arcana", types=("art_gallery", "store"))))
        self.assertEqual(sv.place_skip_reason(self.place("La Brea Tar Pits Museum", types=("museum",))),
                         "name:tar pits")
        self.assertEqual(sv.place_skip_reason(self.place("Wrigley Mansion", types=("museum",))), "name:mansion")
        self.assertEqual(sv._addr_key("1151 Oxford Road, San Marino"), "1151 oxford road")
        self.assertEqual(sv._addr_key("300 East Colorado Boulevard Suite 170, Pasadena"), "300 east colorado boulevard")
        self.assertIsNone(sv._addr_key(None))
        self.assertEqual(sv.place_kind(self.place("The Broad", types=("museum", "tourist_attraction"))), "museum")
        self.assertEqual(sv.place_kind(self.place("Blum")), "gallery")

    def test_normalise_new_and_legacy(self):
        p = sv._norm_new({"id": "abc", "displayName": {"text": "X"}, "formattedAddress": "1 A St, Los Angeles, CA 90001, USA",
                          "location": {"latitude": 34.1, "longitude": -118.2}, "types": ["art_gallery"],
                          "businessStatus": "OPERATIONAL", "websiteUri": "https://x.la/",
                          "regularOpeningHours": {"weekdayDescriptions": ["Mon: Closed"]}})
        self.assertEqual((p["place_id"], p["name"], p["lat"], p["website"], p["hours"]),
                         ("abc", "X", 34.1, "https://x.la/", ["Mon: Closed"]))
        self.assertEqual(sv._clean_address(p["address"]), "1 A St, Los Angeles, CA 90001")
        q = sv._norm_legacy({"place_id": "l", "name": "Y", "vicinity": "2 B St", "types": ["museum"],
                             "geometry": {"location": {"lat": 1.0, "lng": 2.0}}, "business_status": "OPERATIONAL"})
        self.assertEqual((q["place_id"], q["address"], q["lat"], q["website"]), ("l", "2 B St", 1.0, None))


class ZoneTests(unittest.TestCase):
    def test_assign_zone(self):
        labeled = [{"lat": 34.0000, "lng": -118.0000, "zone": "A", "via": "venue:a"},
                   {"lat": 34.0020, "lng": -118.0000, "zone": "B", "via": "area:b"}]   # ~222 m north
        zone, via, dist, amb = sv.assign_zone(34.0001, -118.0000, labeled)
        self.assertEqual((zone, via), ("A", "venue:a"))
        self.assertLess(dist, 20)
        self.assertTrue(amb)                       # B's nearest point within 300 m of A's
        far = [{"lat": 34.0000, "lng": -118.0000, "zone": "A", "via": "venue:a"},
               {"lat": 34.0500, "lng": -118.0000, "zone": "B", "via": "area:b"}]
        zone, _, _, amb = sv.assign_zone(34.0001, -118.0000, far)
        self.assertEqual((zone, amb), ("A", False))
        zone, via, dist, amb = sv.assign_zone(34.0400, -118.0000, [far[0]])   # ~4.4 km
        self.assertIsNone(zone)
        self.assertGreater(dist, sv.ZONE_MAX_M)
        self.assertEqual(sv.assign_zone(0, 0, []), (None, None, None, False))

    def test_labeled_points_skip_out_of_scope(self):
        reg = {"venues": [_venue("a", "A", "Z1", 1.0, 2.0),
                          _venue("b", "B", "Z2", 1.0, 2.0, status="out_of_scope"),
                          _venue("c", "C", None, 1.0, 2.0),
                          _venue("d", "D", "Z3")]}
        pts = sv.labeled_points(reg, {"Z9": {"Area": {"lat": 3.0, "lng": 4.0, "radius_m": 900}, "Nope": None}})
        self.assertEqual([p["via"] for p in pts], ["venue:a", "area:Area"])

    def test_session_zone(self):
        self.assertEqual(sv.session_zone("deep-los-angeles-pasadena-san-gabriel-1788311438", CFG),
                         "Pasadena/San Gabriel")
        self.assertEqual(sv.session_zone("enum-los-angeles-west-hollywood-fairfax-17", CFG),
                         "West Hollywood/Fairfax")            # not plain "Hollywood"
        self.assertEqual(sv.session_zone("deep-los-angeles-santa-monica-venice-datesmoke-1788", CFG),
                         "Santa Monica/Venice")
        self.assertIsNone(sv.session_zone("verify-los-angeles-1788320609", CFG))
        self.assertIsNone(sv.session_zone(None, CFG))


class PatchTests(unittest.TestCase):
    def test_patch_for_existing_keeps_status_and_zone(self):
        v = _venue("x", "X", "Hollywood", 1.0, 2.0, status="active", website="https://x.la/", hours=["Mon"])
        full = {"kind": "gallery", "status": "unknown", "neighborhood": "Beverly Hills", "address": "1 A St",
                "latitude": 9.0, "longitude": 9.0, "coords_source": "geocode", "website": "https://other/",
                "phone": "1", "hours": ["Tue"], "next_check": "2026-09-01",
                "sources": {"seed": {"gpla": {"slug": "x"}}}, "google": None}
        p = sv.patch_for(v, full)
        self.assertEqual(p, {"address": "1 A St", "phone": "1", "sources": {"seed": {"gpla": {"slug": "x"}}}})
        self.assertEqual(sv.patch_for(None, full)["status"], "unknown")
        self.assertEqual(sv.patch_for(v, {"is_museum": False, "kind": "museum"}), {})   # False never fills
        self.assertNotIn("google", sv.patch_for(None, full))

    def test_seed_block_merges_other_sources(self):
        v = _venue("x", "X")
        v["sources"]["seed"] = {"gpla": {"slug": "x"}}
        self.assertEqual(sv.seed_block(v, "carla", {"group": "East"}),
                         {"seed": {"gpla": {"slug": "x"}, "carla": {"group": "East"}}})

    def test_id_collision(self):
        v = _venue("karma", "Karma", website="https://karmakarma.org/")
        self.assertIsNone(sv.id_collision(v, "Karma", "https://www.karmakarma.org/"))
        self.assertIn("karma:", sv.id_collision(v, "Karma", "https://karma-gallery.com/") or "")


class RegistryTests(TempEnv):
    def test_bulk_upsert_idempotent_and_alias_domain_merge(self):
        rows = [{"name": "1301 PE", "source": "seed-gpla",
                 "patch": {"website": "https://www.1301pe.com", "neighborhood": "Mid-Wilshire/Koreatown"}},
                {"name": "Monte Vista Projects", "source": "seed-carla",
                 "patch": {"website": "https://www.montevistaprojects.com/"}}]
        r1 = venues.bulk_upsert(CITY, rows)
        self.assertEqual(r1, {"new": ["1301-pe", "monte-vista-projects"], "updated": []})
        r2 = venues.bulk_upsert(CITY, rows)
        self.assertEqual(r2["new"], [])
        self.assertEqual(r2["updated"], ["1301-pe", "monte-vista-projects"])
        # different spelling, same domain -> same venue, alias recorded, None never overwrites
        r3 = venues.bulk_upsert(CITY, [{"name": "1301PE Gallery", "source": "seed-carla",
                                        "website": "https://1301pe.com/",
                                        "patch": {"neighborhood": None, "phone": "323"}}])
        self.assertEqual(r3, {"new": [], "updated": ["1301-pe"]})
        reg = venues.load_registry(CITY)
        v = venues.index_by_id(reg)["1301-pe"]
        self.assertIn("1301PE Gallery", v["aliases"])
        self.assertEqual(v["neighborhood"], "Mid-Wilshire/Koreatown")
        self.assertEqual(v["phone"], "323")
        self.assertEqual(sorted(v["sources"]["touched_by"]), ["seed-carla", "seed-gpla"])
        self.assertEqual(len(reg["venues"]), 2)

    def test_match_registry_proximity(self):
        reg = {"venues": [_venue("blum", "Blum", "Culver City/West Adams", 34.02900, -118.38600,
                                 website="https://blum-gallery.com/")]}
        v, how = sv.match_registry(reg, "BLUM Los Angeles", None, 34.02910, -118.38610)   # ~15 m
        self.assertEqual((v["id"], how), ("blum", "proximity"))
        v, how = sv.match_registry(reg, "Sofa Gallery", None, 34.02910, -118.38610)          # no shared word
        self.assertIsNone(v)
        v, how = sv.match_registry(reg, "Blum", "https://blum-gallery.com/")
        self.assertEqual(how, "registry")

    def test_due_venues_candidate_priority(self):
        self.write_registry([_venue("cand", "Cand Space", "Hollywood", status="candidate"),
                             _venue("unk", "Unknown Space", "Hollywood", status="unknown")])
        due = {d["id"]: d for d in venues.due_venues(CITY, "Hollywood", TODAY)}
        self.assertIn("candidate", due["cand"]["_reasons"])
        self.assertEqual(due["cand"]["_priority"], due["unk"]["_priority"] + 20)

    def test_zone_coverage_anchor_alias(self):
        reg = {"venues": [_venue("los-angeles-county-museum-of-art-lacma",
                                 "Los Angeles County Museum of Art (LACMA)", "Mid-Wilshire/Koreatown",
                                 sources={"directory_ts": 1, "directory_session": "enum-x", "shows": [],
                                          "crosscheck_ts": None})]}
        cfg = {"zones": {"Mid-Wilshire/Koreatown": {"anchors": ["LACMA", "1301 PE"]}}}
        cov = venues.zone_coverage(CITY, "Mid-Wilshire/Koreatown", cfg, min_enumerated=1, reg=reg)
        self.assertEqual(cov["missing_anchors"], ["1301 PE"])
        self.assertEqual((cov["registry"], cov["enumerated"], cov["seeded"]), (1, 1, 0))
        self.assertFalse(cov["ok"])
        reg["venues"].append(_venue("1301-pe", "1301 PE", "Mid-Wilshire/Koreatown",
                                    sources={"seed": {"gpla": {"slug": "1301-pe"}}}))
        cov = venues.zone_coverage(CITY, "Mid-Wilshire/Koreatown", cfg, min_enumerated=1, reg=reg)
        self.assertTrue(cov["ok"])
        self.assertEqual(cov["seeded"], 1)


class CandidateTests(unittest.TestCase):
    def test_candidate_from_page(self):
        deny = set(venues.CANDIDATE_DENY)
        c = venues.candidate_from_page("https://www.montevistaprojects.com/exhibitions", VENUE_PAGE, CFG, deny)
        self.assertEqual((c["name"], c["website"], c["domain"]),
                         ("Monte Vista Projects", "https://montevistaprojects.com/", "montevistaprojects.com"))
        # publication domain
        self.assertIsNone(venues.candidate_from_page("https://contemporaryartreview.la/x", VENUE_PAGE, CFG, deny))
        self.assertIsNone(venues.candidate_from_page("https://hyperallergic.com/x", VENUE_PAGE, CFG, deny))
        # no address / postal code
        no_addr = VENUE_PAGE.replace("5442 Monte Vista St, Los Angeles, CA 90042\n", "")
        self.assertIsNone(venues.candidate_from_page("https://www.montevistaprojects.com/", no_addr, CFG, deny))
        # too short
        self.assertIsNone(venues.candidate_from_page("https://www.montevistaprojects.com/", VENUE_PAGE[:150], CFG, deny))


class EvidenceBackfillTests(TempEnv):
    def _index(self, rows):
        venues.EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        lines = []
        for i, (url, session, text) in enumerate(rows):
            f = venues.EVIDENCE_DIR / f"{i}.txt"
            f.write_text(text)
            lines.append(json.dumps({"ts": 100 + i, "session": session, "city": CITY, "url": url,
                                     "kind": "web_fetch", "bytes": len(text), "path": str(f), "venue_id": None}))
        venues.EVIDENCE_INDEX.write_text("\n".join(lines) + "\n")

    def test_backfill_dry_run_then_apply(self):
        self.write_registry([_venue("various-small-fires-vsf", "Various Small Fires (VSF)", "Hollywood")])
        vsf_page = ("---\ntitle: Jessie Homer French | Various Small Fires\nmeta-og:site_name: Various Small Fires\n---\n"
                    "Menu Exhibitions Artists Fairs. Various Small Fires is a contemporary art gallery in Los Angeles.\n"
                    * 6)
        self._index([
            ("https://santamonica.com/", "verify-los-angeles-1788320654",
             VENUE_PAGE.replace("Monte Vista Projects", "Visit Santa Monica")),
            ("https://www.montevistaprojects.com/exhibitions", "deep-los-angeles-los-feliz-nela-1788312264", VENUE_PAGE),
            ("https://www.montevistaprojects.com/", "deep-los-angeles-los-feliz-nela-1788312264", VENUE_PAGE),
            ("https://vsf.la/exhibitions", "deep-los-angeles-hollywood-1788317202", vsf_page),
            ("https://hyperallergic.com/review", "deep-los-angeles-hollywood-1788317202", VENUE_PAGE),
        ])
        ctx = sv.Ctx(CITY, apply=False, today=TODAY, key="")
        rep = sv.new_report("evidence")
        sv.seed_evidence(ctx, rep)
        self.assertEqual([e["name"] for e in rep["new"]], ["Monte Vista Projects"])
        self.assertEqual(rep["new"][0]["zone"], "Los Feliz/NELA")
        self.assertEqual([(s["name"], s["reason"]) for s in rep["skipped"]],
                         [("Visit Santa Monica", "generic page name / civic site")])
        self.assertEqual([(e["id"], e["action"], e["matched_via"]) for e in rep["updated"]],
                         [("various-small-fires-vsf", "attach_website", "containment")])
        self.assertTrue(rep["updated"][0]["weak"])           # no address on the page: registry-backed rule
        self.assertEqual(venues.load_registry(CITY)["venues"][0].get("website"), None)   # dry-run wrote nothing
        # apply
        ctx = sv.Ctx(CITY, apply=True, today=TODAY, key="")
        rep = sv.new_report("evidence")
        sv.seed_evidence(ctx, rep)
        reg = venues.index_by_id(venues.load_registry(CITY))
        self.assertEqual(reg["various-small-fires-vsf"]["website"], "https://vsf.la/")
        self.assertIn("Various Small Fires", reg["various-small-fires-vsf"]["aliases"])
        mv = reg["monte-vista-projects"]
        self.assertEqual((mv["status"], mv["neighborhood"], mv["website"]),
                         ("candidate", "Los Feliz/NELA", "https://montevistaprojects.com/"))
        self.assertEqual(mv["sources"]["candidate"]["session"], "deep-los-angeles-los-feliz-nela-1788312264")
        self.assertEqual(rep["new"][0]["result"], "candidate:monte-vista-projects")
        self.assertIn("https://www.montevistaprojects.com/exhibitions", mv["exhibitions_url_candidates"])
        # candidates are due with the +20 bump
        due = {d["id"]: d for d in venues.due_venues(CITY, "Los Feliz/NELA", TODAY)}
        self.assertIn("candidate", due["monte-vista-projects"]["_reasons"])
        # idempotent: a second apply creates nothing
        rep = sv.new_report("evidence")
        sv.seed_evidence(sv.Ctx(CITY, apply=True, today=TODAY, key=""), rep)
        self.assertEqual(rep["new"], [])
        self.assertEqual(len(venues.load_registry(CITY)["venues"]), 2)


class SeedFlowTests(TempEnv):
    """GPLA / Carla / Places seeding end to end with the network stubbed out."""

    def setUp(self):
        super().setUp()
        self._orig = (sv.fetch_html, sv.geocode_address, sv.geocode_area, sv.places_nearby)
        pages = {sv.GPLA_LIST_URL: GPLA_LIST_HTML,
                 sv.GPLA_DETAIL_URL.format(slug="1301-pe"): GPLA_DETAIL_HTML,
                 sv.GPLA_DETAIL_URL.format(slug="no-pill"): "<p>nothing</p>",
                 sv.GPLA_DETAIL_URL.format(slug="the-pit"): GPLA_DETAIL_HTML.replace(
                     "6150 Wilshire Blvd, Los Angeles, CA 90048", "918 Ruberta Ave, Glendale, CA 91201"
                 ).replace("https://www.1301pe.com", "https://www.the-pit.la"),
                 sv.CARLA_URL: CARLA_HTML}
        sv.fetch_html = lambda ctx, url: pages.get(url)
        geo = {"6150 Wilshire Blvd, Los Angeles, CA 90048": {"lat": 34.0630, "lng": -118.3610},
               "918 Ruberta Ave, Glendale, CA 91201": {"lat": 34.1600, "lng": -118.2800}}
        sv.geocode_address = lambda key, address, city_name, city=None, stats=None: geo.get(address)
        sv.geocode_area = lambda key, name, city_name, city=None, stats=None: None
        self.write_registry([
            _venue("los-angeles-county-museum-of-art-lacma", "Los Angeles County Museum of Art (LACMA)",
                   "Mid-Wilshire/Koreatown", 34.0639, -118.3592, website="https://www.lacma.org"),
            _venue("gattopardo", "Gattopardo", "Los Feliz/NELA", 34.1110, -118.1950),
            _venue("anat-ebgi", "Anat Ebgi", "Culver City/West Adams", status="out_of_scope",
                   website="https://anatebgi.com/"),
        ])

    def tearDown(self):
        sv.fetch_html, sv.geocode_address, sv.geocode_area, sv.places_nearby = self._orig
        super().tearDown()

    def test_gpla_then_carla(self):
        ctx = sv.Ctx(CITY, apply=True, today=TODAY, key="k")
        rep = sv.new_report("gpla")
        sv.seed_gpla(ctx, rep)
        self.assertEqual([e["id"] for e in rep["new"]], ["1301-pe", "no-pill-and-co", "pit"])
        by = {e["id"]: e for e in rep["new"]}
        self.assertEqual(by["1301-pe"]["zone"], "Mid-Wilshire/Koreatown")
        self.assertEqual(by["1301-pe"]["via"], "venue:los-angeles-county-museum-of-art-lacma")
        self.assertIsNone(by["no-pill-and-co"]["zone"])
        self.assertEqual([e["id"] for e in rep["zone_unresolved"]], ["no-pill-and-co", "pit"])
        reg = venues.index_by_id(venues.load_registry(CITY))
        v = reg["1301-pe"]
        self.assertEqual((v["status"], v["neighborhood"], v["website"], v["phone"], v["coords_source"]),
                         ("unknown", "Mid-Wilshire/Koreatown", "https://www.1301pe.com", "323 938 5822", "geocode"))
        self.assertEqual(v["hours"], ["Tuesday–Saturday 11am–6pm", "Sunday By appointment"])
        self.assertEqual(v["next_check"], "2026-09-01")
        self.assertEqual(v["sources"]["seed"]["gpla"]["region"], "Central")
        self.assertEqual(v["notes"], "GPLA region: Central")
        self.assertIsNone(reg["pit"]["neighborhood"])              # 6+ km from Gattopardo
        # Carla: 1301 PE updated (domain/id), Anat Ebgi (existing, out_of_scope) keeps status + zone,
        # Monte Vista Projects new with no zone, Paradise Framing denied
        rep = sv.new_report("carla")
        sv.seed_carla(ctx, rep)
        self.assertEqual(sorted(e["id"] for e in rep["new"]), ["7811", "monte-vista-projects"])
        self.assertEqual(sorted(e["id"] for e in rep["updated"]), ["1301-pe", "anat-ebgi", "pit"])
        self.assertIn("Paradise Framing", [s["name"] for s in rep["skipped"]])
        reg = venues.index_by_id(venues.load_registry(CITY))
        self.assertEqual(reg["anat-ebgi"]["status"], "out_of_scope")
        self.assertEqual(reg["anat-ebgi"]["neighborhood"], "Culver City/West Adams")
        self.assertEqual(set(reg["1301-pe"]["sources"]["seed"]), {"gpla", "carla"})
        self.assertEqual(reg["monte-vista-projects"]["neighborhood"], None)
        self.assertEqual(reg["monte-vista-projects"]["sources"]["seed"]["carla"]["group"], "Central")
        self.assertEqual(reg["pit"]["notes"], "GPLA region: East")      # existing notes not overwritten
        # idempotent
        rep = sv.new_report("gpla")
        sv.seed_gpla(ctx, rep)
        self.assertEqual(rep["new"], [])
        self.assertEqual(len(rep["updated"]), 3)

    def test_batch_internal_domain_merge_keeps_new_only_fields(self):
        ctx = sv.Ctx(CITY, apply=True, today=TODAY, key="k", quiet=True)
        rep = sv.new_report("x")
        entries = [{"name": "Jurassic Magic Mid-City", "website": "https://www.jurassicmagic.xyz/", "existing": False,
                    "zone": "Mid-Wilshire/Koreatown",
                    "patch": {"status": "unknown", "neighborhood": "Mid-Wilshire/Koreatown",
                              "website": "https://www.jurassicmagic.xyz/", "next_check": "2026-09-01"}},
                   {"name": "Jurassic Magic MacArthur Park", "website": "https://www.jurassicmagic.xyz/", "existing": False,
                    "zone": "Downtown/Arts District",
                    "patch": {"status": "unknown", "neighborhood": "Downtown/Arts District",
                              "website": "https://www.jurassicmagic.xyz/", "next_check": "2026-09-01"}}]
        sv.commit(ctx, rep, entries, "seed-carla")
        self.assertEqual([e["id"] for e in rep["new"]], ["jurassic-magic-mid-city"])
        self.assertEqual([(e["id"], e["how"]) for e in rep["updated"]],
                         [("jurassic-magic-mid-city", "batch:jurassic-magic-mid-city")])
        v = venues.index_by_id(venues.load_registry(CITY))["jurassic-magic-mid-city"]
        self.assertEqual(v["neighborhood"], "Mid-Wilshire/Koreatown")      # first row's zone survives
        self.assertIn("Jurassic Magic MacArthur Park", v["aliases"])

    def test_places_plan_only_then_execute(self):
        calls = []

        def fake_nearby(key, lat, lng, radius_m, ptype, api="auto", city=None, stats=None, out=None,
                        max_requests=None):
            calls.append((round(lat, 4), ptype))
            if out is not None:
                out.update({"api": "new", "cached": False, "saturated": False, "n": 3, "paid": 1})
            return [{"place_id": "p1", "name": "Aabee Bleue Project", "address": "1 A St, Los Angeles, CA 90048, USA",
                     "lat": 34.0632, "lng": -118.3600, "types": ["art_gallery"], "status": "OPERATIONAL",
                     "website": "https://aabee.la/", "phone": "1", "hours": ["Mon: Closed"]},
                    {"place_id": "p2", "name": "LACMA Los Angeles County Museum of Art", "address": "5905 Wilshire Blvd",
                     "lat": 34.0639, "lng": -118.3592, "types": ["museum"], "status": "OPERATIONAL",
                     "website": "https://www.lacma.org/", "phone": None, "hours": None},
                    {"place_id": "p3", "name": "Wilshire Framing", "address": "2 B St", "lat": 34.0633,
                     "lng": -118.3601, "types": ["art_gallery", "store"], "status": "OPERATIONAL",
                     "website": None, "phone": None, "hours": None},
                    {"place_id": "p4", "name": "Old Gallery", "address": "3 C St", "lat": 34.0634,
                     "lng": -118.3602, "types": ["art_gallery"], "status": "CLOSED_PERMANENTLY",
                     "website": None, "phone": None, "hours": None},
                    {"place_id": "p5", "name": "Resnick Pavilion", "address": "5905 Wilshire Blvd, Los Angeles",
                     "lat": 34.0640, "lng": -118.3590, "types": ["art_gallery"], "status": "OPERATIONAL",
                     "website": None, "phone": None, "hours": None}]

        sv.places_nearby = fake_nearby
        sv.geocode_area = lambda key, name, city_name, city=None, stats=None: (
            {"lat": 34.0630, "lng": -118.3610, "radius_m": 1200} if name == "Miracle Mile" else None)
        # plan only: no nearby calls in a plain dry-run
        ctx = sv.Ctx(CITY, apply=False, zones=["Mid-Wilshire/Koreatown"], today=TODAY, key="k", quiet=True)
        rep = sv.new_report("places")
        sv.seed_places(ctx, rep)
        self.assertEqual(calls, [])
        self.assertEqual(rep["plan"]["requests"], 2)           # one area geocoded x 2 types
        self.assertEqual(rep["plan"]["est_new_usd"], round(2 * sv.COST_USD["nearby_new"], 2))
        self.assertEqual(rep["new"], [])
        # explicit budget: executes, filters, matches, zones
        ctx = sv.Ctx(CITY, apply=False, zones=["Mid-Wilshire/Koreatown"], today=TODAY, key="k",
                     max_places_requests=1, quiet=True)
        rep = sv.new_report("places")
        sv.seed_places(ctx, rep)
        self.assertEqual(len(calls), 1)
        self.assertEqual([e["id"] for e in rep["new"]], ["aabee-bleue-project"])
        self.assertEqual(rep["new"][0]["zone"], "Mid-Wilshire/Koreatown")
        self.assertEqual(rep["new"][0]["kind"], "gallery")
        self.assertEqual([(e["id"], e["how"]) for e in rep["updated"]],
                         [("los-angeles-county-museum-of-art-lacma", "registry")])
        self.assertEqual({s["name"]: s["reason"] for s in rep["skipped"]},
                         {"Wilshire Framing": "name:framing", "Old Gallery": "closed_permanently",
                          "Resnick Pavilion": "inside museum: LACMA Los Angeles County Museum of Art"})
        self.assertIs(rep["updated"][0]["patch"]["is_museum"], True)    # museum listing upgrades the record
        self.assertTrue(any("1 reached" in n for n in rep["notes"]))
        # the working copy got the venue, the registry file did not
        self.assertIn("aabee-bleue-project", venues.index_by_id(ctx.reg))
        self.assertNotIn("aabee-bleue-project", venues.index_by_id(venues.load_registry(CITY)))
        patch = rep["new"][0]["patch"]
        self.assertEqual(patch["address"], "1 A St, Los Angeles, CA 90048")
        self.assertEqual(patch["coords_source"], "places")
        self.assertEqual(patch["sources"]["seed"]["places"]["place_id"], "p1")
        self.assertEqual(patch["google"]["website"], "https://aabee.la/")


if __name__ == "__main__":
    unittest.main()
