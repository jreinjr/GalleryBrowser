"""Venue identity guards added after the Kotaro Nukaga / Taka Ishii incident
(2026-09-03): a Places neighbour merged into the wrong record as an alias, a
show saved under a spelling variant routed to that record and renamed it, the
same show then saved twice under two venue ids, and a Places crosscheck stored
somebody else's listing. Stdlib unittest.

    scraper/.venv/bin/python -m unittest discover -s scraper/tests
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import seed_venues as sv  # noqa: E402
import tools  # noqa: E402
import venues  # noqa: E402
from test_dates import TempContent, show  # noqa: E402
from test_scheduling import RegistryBase  # noqa: E402

NUKAGA_ALIAS = "KOTARO NUKAGA（六本木 | Roppongi）"
TAKA = {"name": "Taka Ishii Gallery Roppongi", "website": "https://www.takaishiigallery.com/en/",
        "address": "complex665, 6-5-24 Roppongi", "latitude": 35.6615289, "longitude": 139.7310734}
NUKAGA = {"name": "Kotaro Nukaga", "website": "https://kotaronukaga.com/about/",
          "address": "6-6-9 Roppongi, Minato-ku", "latitude": 35.6618412, "longitude": 139.7315535}


def record(name, website, slug="nukaga-original-copy", title="Original Copy", **venue_extra):
    venue = {"name": name, "website": website, "is_museum": False, "neighborhood": "Roppongi",
             "address": "6-6-9 Roppongi, Minato City", "address_detail": "Piramide 2F",
             "hours": ["Tue - Sat 11:30am to 6:00pm"], "phone": "03-6721-1180",
             "latitude": 35.6618412, "longitude": 139.7315535}
    venue.update(venue_extra)
    return {"slug": slug, "title": title, "artist": "Oriol Vilanova",
            "start_date": "2026-08-01", "end_date": "2026-09-12",
            "source_urls": ["https://kotaronukaga.com/exhibition/oriol_vilanova_original_copy/"],
            "venue": venue}


class TokyoRegistry(RegistryBase):
    """Taka Ishii (carrying the wrong Nukaga alias, as the seed stage left it)
    and Kotaro Nukaga, both in Roppongi."""

    def setUp(self):
        super().setUp()
        self.taka = self.venue("taka-ishii-gallery-roppongi", website=TAKA["website"])
        self.taka.update({k: TAKA[k] for k in ("name", "address", "latitude", "longitude")})
        self.taka["aliases"] = [NUKAGA_ALIAS]
        self.nukaga = self.venue("kotaro-nukaga", website=NUKAGA["website"])
        self.nukaga.update({k: NUKAGA[k] for k in ("name", "address", "latitude", "longitude")})
        self.write(self.taka, self.nukaga)

    def reg(self):
        return venues.index_by_id(venues.load_registry("los-angeles"))


class FindVenueTests(TokyoRegistry):
    def test_alias_id_collision_is_real(self):
        self.assertEqual(venues.venue_id("Kotaro Nukaga (Roppongi)"), venues.venue_id(NUKAGA_ALIAS))

    def test_alias_hit_needs_domain_agreement(self):
        reg = venues.load_registry("los-angeles")
        # no site given: the alias resolves (nothing contradicts it)
        v = venues.find_venue(reg, "Kotaro Nukaga (Roppongi)", None)
        self.assertEqual(v["id"], "taka-ishii-gallery-roppongi")
        # the alias sits on Taka Ishii, but the site is Nukaga's -> Nukaga
        v = venues.find_venue(reg, "Kotaro Nukaga (Roppongi)", "https://kotaronukaga.com/")
        self.assertEqual(v["id"], "kotaro-nukaga")

    def test_domain_hit_prefers_best_named_branch(self):
        reg = venues.load_registry("los-angeles")
        tennoz = self.venue("kotaro-nukaga-tennoz", website="https://kotaronukaga.com/")
        tennoz["name"] = "KOTARO NUKAGA（天王洲 | Tennoz）"
        reg["venues"].insert(0, tennoz)   # registry order must not decide
        v = venues.find_venue(reg, "Kotaro Nukaga Roppongi", "https://kotaronukaga.com/", city="tokyo")
        self.assertEqual(v["id"], "kotaro-nukaga")
        # a branch spelling (district word) is not recorded as an alias of the parent
        self.assertNotIn("Kotaro Nukaga Roppongi", v["aliases"])
        v = venues.find_venue(reg, "KOTARO NUKAGA Contemporary", "https://kotaronukaga.com/", city="tokyo")
        self.assertEqual(v["id"], "kotaro-nukaga")
        self.assertIn("KOTARO NUKAGA Contemporary", v["aliases"])

    def test_same_site_ignores_host_prefix(self):
        self.assertTrue(venues.same_site("https://en.gallery-momo.com/x", "http://gallery-momo.com/"))
        self.assertFalse(venues.same_site("https://kotaronukaga.com/", "https://takaishiigallery.com/"))
        self.assertIsNone(venues.same_site(None, "https://takaishiigallery.com/"))
        self.assertEqual(venues.root_domain("https://www.shop.example.co.jp/"), "example.co.jp")


class SaveRoutingTests(TokyoRegistry):
    def test_variant_spelling_routes_by_site_not_alias(self):
        rec = record("Kotaro Nukaga (Roppongi)", "https://kotaronukaga.com/")
        vid = venues.on_save_show(rec, "los-angeles", "s1", None)
        self.assertEqual(vid, "kotaro-nukaga")
        reg = self.reg()
        self.assertEqual(reg["taka-ishii-gallery-roppongi"]["name"], TAKA["name"])
        self.assertEqual(reg["taka-ishii-gallery-roppongi"]["website"], TAKA["website"])
        self.assertEqual(reg["taka-ishii-gallery-roppongi"]["sources"]["shows"], [])
        self.assertIn("nukaga-original-copy", reg["kotaro-nukaga"]["sources"]["shows"])

    def test_todo_venue_wins(self):
        trace = venues.SessionTrace("s1", "tokyo")
        trace.todo_venues = [{"name": "Kotaro Nukaga", "key": tools._norm_venue("Kotaro Nukaga"),
                              "venue_id": "kotaro-nukaga"}]
        rec = record("Kotaro Nukaga (Roppongi)", None)   # agent gave no site at all
        vid = venues.on_save_show(rec, "los-angeles", "s1", trace)
        self.assertEqual(vid, "kotaro-nukaga")
        self.assertEqual(self.reg()["taka-ishii-gallery-roppongi"]["sources"]["shows"], [])

    def test_todo_route_still_needs_domain_agreement(self):
        trace = venues.SessionTrace("s1", "tokyo")
        trace.todo_venues = [{"name": "Kotaro Nukaga", "key": tools._norm_venue("Kotaro Nukaga"),
                              "venue_id": "kotaro-nukaga"}]
        rec = record("Kotaro Nukaga Editions", "https://other-gallery.example/", slug="x")
        vid = venues.on_save_show(rec, "los-angeles", "s1", trace)
        self.assertEqual(vid, "kotaro-nukaga-editions")   # a new record, not the TODO venue

    def test_resolve_id_uses_trace(self):
        trace = venues.SessionTrace("s1", "tokyo")
        trace.todo_venues = [{"name": "Kotaro Nukaga", "key": tools._norm_venue("Kotaro Nukaga"),
                              "venue_id": "kotaro-nukaga"}]
        self.assertEqual(venues.resolve_id("los-angeles", "Kotaro Nukaga (Roppongi)", None, trace),
                         "kotaro-nukaga")
        self.assertEqual(venues.resolve_id("los-angeles", "Kotaro Nukaga (Roppongi)", None),
                         "taka-ishii-gallery-roppongi")   # without a trace the alias still wins


class IdentityFillOnlyTests(TokyoRegistry):
    def test_show_block_never_renames_an_existing_venue(self):
        rec = record("Kotaro Nukaga (Roppongi)", "https://kotaronukaga.com/")
        venues.on_save_show(rec, "los-angeles", "s1", None)
        v = self.reg()["kotaro-nukaga"]
        self.assertEqual(v["name"], "Kotaro Nukaga")
        self.assertIn("Kotaro Nukaga (Roppongi)", v["aliases"])          # same site: spelling kept as alias
        self.assertEqual(v["address"], NUKAGA["address"])                 # existing address kept
        self.assertEqual(v["address_detail"], "Piramide 2F")              # gap filled
        self.assertEqual(v["phone"], "03-6721-1180")                      # same site refreshes phone
        self.assertEqual(v["coords_source"], "show:nukaga-original-copy")

    def test_other_site_only_attaches_the_show(self):
        # defence in depth: a mis-routed save from another site must not touch identity
        rec = record("Kotaro Nukaga (Roppongi)", "https://kotaronukaga.com/", latitude=1.0, longitude=2.0)
        patch = venues._venue_patch_from_show(rec, "pending")
        out = venues._identity_fill_only(self.taka, patch, "https://kotaronukaga.com/")
        for k in ("name", "website", "address", "address_detail", "phone", "hours",
                  "latitude", "longitude", "coords_source"):
            self.assertNotIn(k, out, k)
        self.assertNotIn("aliases", out)
        self.assertIn("last_known_shows", out)

    def test_confirm_show_uses_stored_venue_id(self):
        rec = record("Kotaro Nukaga (Roppongi)", None)
        rec["venue_id"] = "kotaro-nukaga"
        venues.on_confirm_show(rec, "los-angeles", "published")
        reg = self.reg()
        self.assertEqual(reg["kotaro-nukaga"]["last_known_shows"][0]["slug"], "nukaga-original-copy")
        self.assertEqual(reg["taka-ishii-gallery-roppongi"]["last_known_shows"], [])


class DuplicateShowTests(TempContent):
    def save(self, **kw):
        rec = show(**kw)
        rec["images"] = [self.image(rec["slug"])]
        return json.loads(tools.save_show(rec, "los-angeles", ["Hollywood"]))

    def save_at(self, slug, venue, website, title, source_urls):
        rec = show(slug=slug, venue=venue, title=title, source_urls=source_urls)
        rec["venue"]["website"] = website
        rec["images"] = [self.image(rec["slug"])]
        return json.loads(tools.save_show(rec, "los-angeles", ["Hollywood"]))

    def test_spelling_variant_same_site_is_the_same_show(self):
        self.save_at("nukaga-original-copy", "Kotaro Nukaga", "https://kotaronukaga.com/about/",
                     "Original Copy", ["https://kotaronukaga.com/about/"])
        with self.assertRaises(ValueError) as cm:
            self.save_at("nukaga-oriol-vilanova-original-copy", "Kotaro Nukaga (Roppongi)",
                         "https://kotaronukaga.com/", "Original Copy", ["https://kotaronukaga.com/about/"])
        self.assertIn("nukaga-original-copy", str(cm.exception))
        # a same-title show at an unrelated gallery that happens to share a
        # web host (shared platform) is not the same show
        self.save_at("other", "Sofa Projects", "https://kotaronukaga.com/", "Original Copy",
                     ["https://kotaronukaga.com/sofa"])

    def test_shared_exhibition_page_is_the_same_show(self):
        page = "https://kotaronukaga.com/exhibition/points-of-entry/"
        self.save(slug="nukaga-points-of-entry", venue="Kotaro Nukaga", title="Points of Entry",
                  source_urls=[page])
        with self.assertRaises(ValueError):
            self.save(slug="pour-points", venue="Nukaga Roppongi Space", title="Points of Entry",
                      source_urls=[page])
        # a listing page shared by two different shows of one venue is not a duplicate
        self.save(slug="other-show", venue="Kotaro Nukaga", title="After Understanding",
                  source_urls=["https://kotaronukaga.com/exhibition/"])

    def test_same_title_different_galleries_both_save(self):
        self.save(slug="a", venue="Gallery A", title="Summer Group Show")
        out = self.save(slug="b", venue="Gallery B", title="Summer Group Show")
        self.assertEqual(out["result"], "saved")


class SeedProximityTests(unittest.TestCase):
    def _venue(self, vid, name, website, lat, lng):
        v = venues.empty_venue(vid, name)
        v.update({"website": website, "latitude": lat, "longitude": lng, "neighborhood": "Roppongi"})
        return v

    def test_district_word_is_not_a_shared_name(self):
        reg = {"venues": [self._venue("taka-ishii-gallery-roppongi", TAKA["name"], TAKA["website"],
                                      TAKA["latitude"], TAKA["longitude"])]}
        v, how = sv.match_registry(reg, NUKAGA_ALIAS, None, NUKAGA["latitude"], NUKAGA["longitude"],
                                   city="tokyo")   # ~55 m apart, share only "roppongi"
        self.assertIsNone(v)

    def test_different_sites_never_match(self):
        reg = {"venues": [self._venue("blum", "Blum Roppongi", "https://blum-gallery.com/", 34.029, -118.386)]}
        v, how = sv.match_registry(reg, "BLUM Annex", "https://other.example/", 34.0291, -118.3861, city="tokyo")
        self.assertIsNone(v)
        v, how = sv.match_registry(reg, "BLUM Annex", None, 34.0291, -118.3861, city="tokyo")
        self.assertEqual((v["id"], how), ("blum", "proximity"))


class CrosscheckGuardTests(TokyoRegistry):
    def listing(self, name, address, website):
        return {"found": True, "name": name, "address": address, "website": website,
                "status": "OPERATIONAL", "lat": 35.66, "lng": 139.73, "hours": None, "phone": "03"}

    def test_foreign_listing_is_not_stored(self):
        venues.on_crosscheck("los-angeles", TAKA["name"],
                             self.listing("KOTARO NUKAGA（六本木 | Roppongi）",
                                          "6-chōme-6-9 Roppongi, Minato City", "https://kotaronukaga.com/"))
        v = self.reg()["taka-ishii-gallery-roppongi"]
        self.assertIsNone(v.get("google"))
        self.assertEqual(v["sources"]["crosscheck_mismatch"]["name"], "KOTARO NUKAGA（六本木 | Roppongi）")

    def test_matching_listing_is_stored_with_its_name(self):
        venues.on_crosscheck("los-angeles", TAKA["name"],
                             self.listing("Taka Ishii Gallery", "6-chōme-5-24 complex665 3F, Roppongi, Minato City",
                                          "https://www.takaishiigallery.com/"))
        v = self.reg()["taka-ishii-gallery-roppongi"]
        self.assertEqual(v["google"]["name"], "Taka Ishii Gallery")
        self.assertIn("crosscheck_ts", v["sources"])

    def test_listing_matches_relaxations(self):
        v = {"name": "ギャラリー小柳", "address": None}
        self.assertTrue(venues.listing_matches(v, self.listing("Gallery Koyanagi", "1-7-5 Ginza", None)))
        v = {"name": "Taka Ishii Gallery", "address": None}
        self.assertFalse(venues.listing_matches(v, self.listing("Kotaro Nukaga", "6-6-9 Roppongi", None)))
        self.assertFalse(venues.listing_matches(v, {"found": False}))


class SeedBlockTests(unittest.TestCase):
    def test_llm_seed_keeps_places_seed(self):
        v = {"sources": {"seed": {"places": {"place_id": "abc"}}}}
        patch = {"sources": sv.seed_block(v, "llm", {"ts": 1})}
        venues.merge_patch(v, patch)
        self.assertEqual(v["sources"]["seed"]["places"]["place_id"], "abc")
        self.assertEqual(v["sources"]["seed"]["llm"], {"ts": 1})


class AuditTests(TokyoRegistry):
    def test_audit_reports_the_incident_shapes(self):
        # the alias on Taka Ishii collides with nothing yet (no record has that id)
        # but is unrelated to the record's name; add a same-title show under two ids
        tools.CONTENT_DIR.mkdir(parents=True, exist_ok=True)
        (tools.CONTENT_DIR / "los-angeles.json").write_text(json.dumps({"shows": [
            {"slug": "a", "title": "Original Copy", "venue_id": "kotaro-nukaga",
             "venue": {"name": "Kotaro Nukaga", "website": "https://kotaronukaga.com/"},
             "source_urls": ["https://kotaronukaga.com/exhibition/oriol_vilanova_original_copy/"]},
            {"slug": "b", "title": "Original Copy", "venue_id": "taka-ishii-gallery-roppongi",
             "venue": {"name": "Kotaro Nukaga (Roppongi)", "website": "https://kotaronukaga.com/"},
             "source_urls": ["https://kotaronukaga.com/exhibition/oriol_vilanova_original_copy/"]}]}))
        (tools.CONTENT_DIR / "pending").mkdir(exist_ok=True)
        (tools.CONTENT_DIR / "pending" / "los-angeles.json").write_text(json.dumps({"shows": []}))
        with venues.locked_registry("los-angeles") as reg:
            taka = venues.index_by_id(reg)["taka-ishii-gallery-roppongi"]
            taka["google"] = {"website": "https://kotaronukaga.com/about/"}
        with mock.patch.object(venues, "_district_tokens", return_value={"roppongi"}):
            out = venues.audit("los-angeles")
        self.assertEqual([o["alias"] for o in out["orphan_aliases"]], [NUKAGA_ALIAS])
        self.assertEqual(out["duplicate_shows"][0]["slugs"], ["a", "b"])
        self.assertEqual(out["domain_conflicts"][0]["venue"], "taka-ishii-gallery-roppongi")
        self.assertEqual(out["alias_collisions"], [])
        # an alias that resolves to another live record is a collision
        with venues.locked_registry("los-angeles") as reg:
            venues.index_by_id(reg)["kotaro-nukaga"]["aliases"] = ["Taka Ishii Gallery Roppongi"]
        out = venues.audit("los-angeles")
        self.assertEqual(out["alias_collisions"][0]["resolves_to"], "taka-ishii-gallery-roppongi")


if __name__ == "__main__":
    unittest.main()
