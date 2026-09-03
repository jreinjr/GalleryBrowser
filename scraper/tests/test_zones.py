"""City zone configuration: every city's guidance/zones/aliases agree with its
neighborhood list, and tools.normalize_zone resolves the district names an
agent is likely to write. Stdlib unittest, no network.

    scraper/.venv/bin/python -m unittest discover -s scraper/tests
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools  # noqa: E402
from cities import CITIES  # noqa: E402


class ConfigConsistency(unittest.TestCase):
    def test_zone_keys_match_neighborhoods(self):
        for key, cfg in CITIES.items():
            hoods = set(cfg["neighborhoods"])
            with self.subTest(city=key):
                self.assertEqual(len(hoods), len(cfg["neighborhoods"]), "duplicate zone label")
                if isinstance(cfg["guidance"], dict):
                    self.assertLessEqual(set(cfg["guidance"]) - {"*"}, hoods)
                self.assertLessEqual(set(cfg.get("zones") or {}), hoods)
                self.assertLessEqual(set((cfg.get("zone_aliases") or {}).values()), hoods)

    def test_seeding_zones_have_areas_and_anchors(self):
        for key, cfg in CITIES.items():
            for zone, z in (cfg.get("zones") or {}).items():
                with self.subTest(city=key, zone=zone):
                    self.assertTrue(z.get("areas"), "areas drive the Places sweep")
                    self.assertIsInstance(z.get("anchors", []), list)

    def test_aliases_do_not_shadow_a_zone_label(self):
        """An alias that is already a zone part would never be consulted."""
        for key, cfg in CITIES.items():
            hoods = cfg["neighborhoods"]
            for alias in (cfg.get("zone_aliases") or {}):
                with self.subTest(city=key, alias=alias):
                    self.assertIsNone(
                        tools.normalize_zone(alias, hoods),
                        f"'{alias}' already resolves without the alias map")


class TokyoZones(unittest.TestCase):
    cfg = CITIES["tokyo"]

    def hood(self, name):
        return tools.normalize_zone(name, self.cfg["neighborhoods"], "tokyo")

    def test_original_labels_survive(self):
        """Existing tokyo.json records carry these — renaming one orphans them."""
        for zone in ("Roppongi", "Ginza/Kyobashi", "Shibuya/Omotesando",
                     "Ebisu/Meguro", "Kiyosumi-Shirakawa", "Tennozu"):
            self.assertIn(zone, self.cfg["neighborhoods"])

    def test_districts_resolve(self):
        cases = {
            "Shinagawa": "Tennozu", "Tennozu Isle": "Tennozu", "Osaki": "Tennozu",
            "Harajuku": "Shibuya/Omotesando", "Aoyama": "Shibuya/Omotesando",
            "Daikanyama": "Ebisu/Meguro", "Nakameguro": "Ebisu/Meguro",
            "Azabudai Hills": "Roppongi", "Nishi-Azabu": "Roppongi",
            "Kayabacho": "Nihonbashi/Bakurocho", "Jimbocho": "Nihonbashi/Bakurocho",
            "Hatsudai": "Shinjuku/Kagurazaka", "Toyosu": "Kiyosumi-Shirakawa",
            "Asakusa": "Ueno/Yanaka", "Kichijoji": "Setagaya/West Tokyo",
        }
        for name, zone in cases.items():
            with self.subTest(district=name):
                self.assertEqual(self.hood(name), zone)

    def test_straddling_ward_names_stay_ambiguous(self):
        for name in ("Minato", "Chuo", "Kanagawa"):
            self.assertIsNone(self.hood(name))

    def test_postal_re_matches_23_wards_only(self):
        pat = re.compile(self.cfg["postal_re"])
        for good in ("〒106-0032", "Tokyo 150-0001", "104-0031 Chuo-ku"):
            self.assertTrue(pat.search(good), good)
        for bad in ("03-1234-5678", "220-0012 Yokohama", "2026-09-03"):
            self.assertIsNone(pat.search(bad), bad)


class AliasesAreOptional(unittest.TestCase):
    def test_city_without_aliases(self):
        self.assertEqual(tools.zone_aliases("los-angeles"), {})
        self.assertEqual(tools.zone_aliases(None), {})
        self.assertEqual(
            tools.normalize_zone("Pasadena", CITIES["los-angeles"]["neighborhoods"], "los-angeles"),
            "Pasadena/San Gabriel")


if __name__ == "__main__":
    unittest.main()


class NonLatinVenueNames(unittest.TestCase):
    """Tokyo's Google Places results include local-script names. They must not
    all collapse onto one registry id, and they must not become venues."""

    def test_norm_venue_keeps_cjk(self):
        import tools as t
        self.assertEqual(t._norm_venue("彫刻広場"), "彫刻広場")
        self.assertEqual(t._norm_venue("Mori Art Museum"), "mori art museum")

    def test_ids_stay_distinct(self):
        import venues
        names = ["手帳類図書室", "ギャラリー樋口文庫", "崔如琢陽光美術館", "彫刻広場"]
        ids = [venues.venue_id(n) for n in names]
        self.assertEqual(len(set(ids)), len(names), ids)
        for i in ids:
            self.assertTrue(i.startswith("venue-"), i)
        self.assertEqual(ids, [venues.venue_id(n) for n in names], "ids must be stable")

    def test_ascii_ids_unchanged(self):
        import venues
        self.assertEqual(venues.venue_id("Hammer Museum"), "hammer-museum")
        self.assertEqual(venues.venue_id("Taka Ishii Gallery"), "taka-ishii")

    def test_places_skips_script_only_names(self):
        import seed_venues
        self.assertEqual(
            seed_venues.place_skip_reason({"name": "ギャラリー樋口文庫", "types": []}),
            "name_not_latin")
        self.assertIsNone(
            seed_venues.place_skip_reason({"name": "Gallery Koyanagi", "types": []}))

    def test_places_skips_civic_museums_but_keeps_art_museums(self):
        import seed_venues
        for junk in ("Minato Science Museum", "NHK Museum of Broadcasting",
                     "Water Supply Pipe Inlet Museum",
                     "Japan Sake and Shochu Information Center"):
            with self.subTest(name=junk):
                self.assertIsNotNone(seed_venues.place_skip_reason({"name": junk, "types": []}))
        for real in ("Mori Art Museum", "Artizon Museum", "Okura Museum of Art",
                     "Museum of Contemporary Art Tokyo", "Sen-oku Hakukokan Museum Tokyo",
                     "Kikuchi Kanjitsu Memorial Tomo Museum", "Norton Simon Museum",
                     "The Broad", "LACMA"):
            with self.subTest(name=real):
                self.assertIsNone(seed_venues.place_skip_reason({"name": real, "types": []}))


class DetailsGate(unittest.TestCase):
    """A Places `details` call costs $0.02 and is the only source of a seeded
    venue's website. The gate must keep real galleries whose names carry no
    venue word, and drop the malls/tunnels/temple halls a type sweep returns."""

    cfg = CITIES["tokyo"]

    def test_keeps_anchor_venues_without_a_venue_word(self):
        import seed_venues
        for name in ("Perrotin", "NANZUKA", "ANOMALY", "KOSAKU KANECHIKA", "TARO NASU",
                     "SCAI The Bathhouse", "WAKO WORKS OF ART", "ShugoArts",
                     "21_21 Design Sight", "PARCEL"):
            with self.subTest(name=name):
                self.assertTrue(seed_venues.details_worth_paying(name, self.cfg))

    def test_keeps_obvious_venue_names(self):
        import seed_venues
        for name in ("Gallery Koyanagi", "Mori Art Museum", "Shiseido Gallery",
                     "Museum of Contemporary Art Tokyo"):
            with self.subTest(name=name):
                self.assertTrue(seed_venues.details_worth_paying(name, self.cfg))

    def test_drops_type_sweep_noise(self):
        import seed_venues
        for name in ("Azabudai hills", "Roppongi Tunnel Wallart", "Theater 360",
                     "Treasure Hall of Nogi Jinja", "Sumi.studio(UENO)", ""):
            with self.subTest(name=name):
                self.assertFalse(seed_venues.details_worth_paying(name, self.cfg))

    def test_city_without_anchors_falls_back_to_the_name_test(self):
        import seed_venues
        self.assertTrue(seed_venues.details_worth_paying("Some Gallery", {}))
        self.assertFalse(seed_venues.details_worth_paying("Some Mall", {}))
