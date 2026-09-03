"""city_priors (stage G0): list_member signal kind, proposal merge, stdlib
list parsers, candidate resolution. No network, no content/ writes."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import city_priors as cpri  # noqa: E402
import curation_prompts as cp  # noqa: E402
import curation_store as store  # noqa: E402
import venues  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


class SignalKind(unittest.TestCase):
    def test_list_member_is_a_signal_kind(self):
        self.assertIn("list_member", store.SIGNAL_KINDS)
        self.assertIn("list_member", store.RECORD_SIGNAL_SCHEMA["properties"]["kind"]["enum"])

    def test_lists_variant_registered(self):
        self.assertEqual(cp.VARIANTS["lists_v1"].unit, "list")
        first = cp.VARIANTS["lists_v1"].first_message({"items": [{"id": "adaa", "name": "ADAA", "kind": "curated_list"}]})
        self.assertIn("adaa", first)


class ProposalMerge(unittest.TestCase):
    def setUp(self):
        self.existing = [
            {"id": "frieze-la-exhibitors", "kind": "fair", "urls": ["https://www.frieze.com/fairs/frieze-los-angeles"]},
            {"id": "carla", "kind": "review_outlet", "urls": ["https://contemporaryartreview.la/"]},
        ]
        self.data = {
            "fairs": [
                {"id": "frieze-los-angeles", "name": "Frieze LA", "url": "https://frieze.com/fairs/frieze-los-angeles",
                 "exhibitor_list_url": None, "prestige": 3, "region": "local", "query_hints": [], "notes": None},
                {"id": "felix-art-fair", "name": "Felix", "url": "https://www.felixfair.com/",
                 "exhibitor_list_url": "https://www.felixfair.com/exhibitors", "prestige": 2, "region": "local",
                 "query_hints": ["Felix 2026 exhibitors"], "notes": None},
            ],
            "lists": [
                {"id": "adaa", "name": "ADAA", "url": "https://www.artdealers.org/member-galleries",
                 "entry_urls": [], "list_kind": "association", "weight": 0.9, "query_hints": [], "notes": None},
                {"id": "carla-venues", "name": "Carla venue list", "url": "https://contemporaryartreview.la/venues",
                 "entry_urls": [], "list_kind": "editorial", "weight": 0.6, "query_hints": [], "notes": None},
            ],
            "publications": [
                {"id": "carla", "name": "Carla", "url": "https://contemporaryartreview.la/", "kind": "review_outlet",
                 "weight": 1, "query_hints": [], "notes": None},
            ],
        }

    def test_merge_skips_same_id_and_same_domain_same_kind(self):
        entries = cpri.entries_from_proposal("los-angeles", self.data, self.existing, today="2026-09-03")
        ids = [e["id"] for e in entries]
        self.assertNotIn("frieze-los-angeles", ids)      # same fair domain as frieze-la-exhibitors
        self.assertNotIn("carla", ids)                   # same id
        self.assertIn("felix-art-fair", ids)
        self.assertIn("adaa", ids)
        self.assertIn("carla-venues", ids)               # same domain but a different kind
        adaa = next(e for e in entries if e["id"] == "adaa")
        self.assertEqual(adaa["kind"], "curated_list")
        self.assertEqual(adaa["parser"], "adaa")
        self.assertEqual(adaa["weight"], 0.9)
        felix = next(e for e in entries if e["id"] == "felix-art-fair")
        self.assertEqual(felix["kind"], "fair")
        self.assertEqual(felix["urls"][0], "https://www.felixfair.com/exhibitors")
        self.assertEqual(felix["weight"], 0)

    def test_second_merge_is_idempotent(self):
        entries = cpri.entries_from_proposal("los-angeles", self.data, self.existing)
        again = cpri.entries_from_proposal("los-angeles", self.data, self.existing + entries)
        self.assertEqual(again, [])


class Parsers(unittest.TestCase):
    def test_adaa_filters_to_city(self):
        rows = cpri.parse_adaa((FIX / "adaa_members.html").read_text(), "los-angeles")
        self.assertEqual([r["name"] for r in rows], ["Karma", "Marc Selwyn Fine Art"])
        self.assertEqual(rows[0]["website"], "https://karmakarma.org")

    def test_nada_name_before_first_comma(self):
        html = (FIX / "nada_members.html").read_text()
        la = cpri.parse_nada(html, "los-angeles")
        self.assertEqual([r["name"] for r in la], ["Anat Ebgi", "Long Story Short"])
        self.assertEqual(la[0]["website"], "https://anatebgi.com/")
        tk = cpri.parse_nada(html, "tokyo")
        self.assertEqual([r["name"] for r in tk], ["MISAKO & ROSEN"])

    def test_parse_list_source_dedupes(self):
        src = {"id": "adaa", "parser": "adaa", "entry_urls": ["u1", "u2"]}
        html = (FIX / "adaa_members.html").read_text()
        rows = cpri.parse_list_source(src, "los-angeles", {"u1": html, "u2": html})
        self.assertEqual(len(rows), 2)


def _reg(*names):
    reg = {"schema": 2, "city": "los-angeles", "updated": 0, "venues": []}
    for n in names:
        v = venues.empty_venue(venues.venue_id(n), n)
        reg["venues"].append(v)
    return reg


def _sig(venue, source_id, kind="list_member", snippet=""):
    return {"id": f"{source_id}-{venue}", "kind": kind, "source": {"id": source_id},
            "show_ref": {"venue": venue, "artist": None, "title": None}, "snippet": snippet}


class Candidates(unittest.TestCase):
    def test_resolution_and_unresolved(self):
        reg = _reg("Vielmetter Los Angeles", "Karma", "Marc Selwyn Fine Art")
        reg["venues"][1]["aliases"].append("Karma Los Angeles")
        sigs = [_sig("Vielmetter", "adaa"), _sig("Karma Los Angeles", "nada"),
                _sig("Marc Selwyn Fine Art (Camden Annex)", "adaa"),
                _sig("Brand New Space", "adaa", snippet="Member of ADAA; site: brandnew.gallery"),
                _sig("Brand New Space", "nada")]
        events = [{"event": "seen", "venue_in_pool": False,
                   "show_ref": {"venue": "Press Only Room", "artist": "X", "title": None}, "run_id": "r1"}]
        new, tagged = cpri.unresolved_candidates("los-angeles", reg, sigs, events)
        self.assertEqual({t["id"] for t in tagged}, {"vielmetter-los-angeles", "karma", "marc-selwyn"})
        self.assertEqual([r["name"] for r in new], ["Brand New Space", "Press Only Room"])
        self.assertEqual(new[0]["sources"], ["adaa", "nada"])
        self.assertEqual(new[0]["website"], "https://brandnew.gallery")
        self.assertEqual(new[1]["sources"], ["press:r1"])


if __name__ == "__main__":
    unittest.main()
