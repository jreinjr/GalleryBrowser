"""Registry scheduling after the four-miss fixes: current vs upcoming shows,
the priority rework (Places-only capped, ageing, retry/curated bonuses),
future/past path scoring, requeue cohorts, protected upserts, and the
same-session skip guard in on_save_show. Stdlib unittest.

    scraper/.venv/bin/python -m unittest discover -s scraper/tests
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_deep  # noqa: E402
import tools  # noqa: E402
import venues  # noqa: E402

TODAY = date(2026, 9, 1)
UPCOMING = ("2026-09-25", "2026-11-07")
CURRENT = ("2026-08-01", "2026-10-15")


def ts(d: date) -> int:
    return int(time.mktime(d.timetuple()))


class RegistryBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._vd, self._cd = venues.VENUES_DIR, tools.CONTENT_DIR
        venues.VENUES_DIR = self.tmp / "venues"
        tools.CONTENT_DIR = self.tmp

    def tearDown(self):
        venues.VENUES_DIR, tools.CONTENT_DIR = self._vd, self._cd

    def venue(self, vid, shows=(), next_check=None, status="active", kind="gallery",
              scraped_days_ago=3, outcome="saved", sources=None, website=None):
        v = venues.empty_venue(vid, vid.replace("-", " ").title())
        v.update({"status": status, "kind": kind, "neighborhood": "Hollywood",
                  "website": website,
                  "last_scraped": ts(TODAY - timedelta(days=scraped_days_ago)) if scraped_days_ago is not None else None,
                  "last_outcome": outcome if scraped_days_ago is not None else None,
                  "next_check": next_check,
                  "last_known_shows": [{"slug": f"{vid}-{i}", "title": f"T{i}", "start": s, "end": e,
                                        "placement": "published"} for i, (s, e) in enumerate(shows)]})
        if sources:
            v["sources"].update(sources)
        return v

    def write(self, *vs):
        with venues.locked_registry("los-angeles") as reg:
            reg["venues"] = list(vs)

    def due(self, **kw):
        return {r["id"]: r for r in venues.due_venues("los-angeles", "Hollywood", TODAY, **kw)}


class CurrentVsUpcomingTests(RegistryBase):
    def test_split_and_future_only_flag(self):
        v = self.venue("1301-pe", shows=(UPCOMING,))
        self.assertEqual(venues.current_shows(v, TODAY), [])
        self.assertEqual(len(venues.upcoming_shows(v, TODAY)), 1)
        self.assertEqual(len(venues.active_shows(v, TODAY)), 1)
        self.assertTrue(venues.future_only_saved(v, TODAY))
        v["last_outcome"] = "skipped:closed_or_between_shows"    # the agent said what's on now
        self.assertFalse(venues.future_only_saved(v, TODAY))
        both = self.venue("both", shows=(CURRENT, UPCOMING))
        self.assertEqual(len(venues.current_shows(both, TODAY)), 1)
        self.assertFalse(venues.future_only_saved(both, TODAY))

    def test_future_only_venue_is_due_and_rechecked_soon(self):
        v = self.venue("1301-pe", shows=(UPCOMING,), next_check=(TODAY + timedelta(days=20)).isoformat())
        self.write(v)
        due = self.due()
        self.assertIn("1301-pe", due)
        self.assertIn("future_only_saved", due["1301-pe"]["_reasons"])
        self.assertGreaterEqual(due["1301-pe"]["_priority"], 25)
        # compute_next_check: base (last_scraped, 3 days ago) + 2 would be yesterday ->
        # clamped to tomorrow (never today/past), and certainly not +21
        self.assertEqual(venues.compute_next_check(v, TODAY), TODAY + timedelta(days=1))
        resolved = self.venue("resolved", shows=(UPCOMING,), outcome="skipped:closed_or_between_shows",
                              next_check=(TODAY + timedelta(days=20)).isoformat())
        self.write(v, resolved)
        self.assertNotIn("resolved", self.due())


class PriorityTests(RegistryBase):
    def places_only(self, vid, name="Foo Gallery"):
        v = self.venue(vid, scraped_days_ago=None, status="unknown", website="https://foo.example",
                       sources={"seed": {"places": {"ts": 1, "type": "art_gallery"}}})
        v["name"] = name
        return v

    def test_places_only_never_outranks_a_vouched_venue(self):
        junk = self.places_only("museum-of-failure", name="Museum of Failure")
        real = self.venue("fernberger", next_check=TODAY.isoformat(),
                          outcome="skipped:unverifiable",
                          sources={"seed": {"carla": {"ts": 1}}})
        real["last_skip"] = {"reason": "unverifiable",
                             "detail": "own site confirms the current on-view show; date badge "
                                       "renders via JavaScript"}
        self.write(junk, real)
        due = self.due()
        self.assertLessEqual(due["museum-of-failure"]["_priority"], 10)
        self.assertIn("places_only", due["museum-of-failure"]["_reasons"])
        self.assertIn("retry_confirmed_show", due["fernberger"]["_reasons"])
        self.assertIn("curated_source", due["fernberger"]["_reasons"])
        order = [r["id"] for r in venues.due_venues("los-angeles", "Hollywood", TODAY)]
        self.assertEqual(order[0], "fernberger")
        # --exclude-places-only drops the junk entirely
        self.assertNotIn("museum-of-failure", self.due(include_places_only=False))

    def test_implausible_places_only_stays_parked_until_vouched(self):
        parked = self.places_only("advocartsy-dtla", name="ADVOCARTSY DTLA")
        self.write(parked)
        self.assertNotIn("advocartsy-dtla", self.due())
        parked["sources"]["site"] = {"ts": 1, "url": "https://advocartsy.com/exhibitions/"}
        self.assertFalse(venues.seed_only_places(parked))
        self.write(parked)
        due = self.due()
        self.assertIn("advocartsy-dtla", due)
        self.assertIn("never_scraped", due["advocartsy-dtla"]["_reasons"])
        self.assertNotIn("places_only", due["advocartsy-dtla"]["_reasons"])
        # still not "curated" — that stays for GPLA/Carla/enumeration
        self.assertNotIn("curated_source", due["advocartsy-dtla"]["_reasons"])

    def test_overdue_venues_age_upward(self):
        fresh = self.venue("fresh", next_check=TODAY.isoformat())
        stale = self.venue("stale", next_check=(TODAY - timedelta(days=10)).isoformat())
        self.write(fresh, stale)
        due = self.due()
        self.assertEqual(due["stale"]["_priority"] - due["fresh"]["_priority"], 15)  # cap +15

    def test_requeued_venue_is_due_with_bonus(self):
        v = self.venue("art-practice", kind="nonprofit", shows=(CURRENT,),
                       next_check=(TODAY + timedelta(days=20)).isoformat())
        self.write(v)
        self.assertNotIn("art-practice", self.due())
        v["requeue_reasons"] = ["single_save_backfill"]
        self.write(v)
        due = self.due()
        self.assertIn("requeued", due["art-practice"]["_reasons"])
        self.assertGreaterEqual(due["art-practice"]["_priority"], 20)


class SkipRuleTests(RegistryBase):
    def test_confirmed_show_unverifiable_retries_in_a_week(self):
        v = self.venue("js")
        venues.apply_skip(v, {"reason": "unverifiable", "ts": 1,
                              "detail": "site confirms the show is current; dates render via JavaScript"}, TODAY)
        self.assertEqual(v["next_check"], (TODAY + timedelta(days=7)).isoformat())
        self.assertEqual(v["page"]["fetch_mode"], "js")
        w = self.venue("dead")
        venues.apply_skip(w, {"reason": "unverifiable", "detail": "site unreachable", "ts": 1}, TODAY)
        self.assertEqual(w["next_check"], (TODAY + timedelta(days=30)).isoformat())


class NeverDueTodayTests(RegistryBase):
    """A skip or save today must never schedule the venue for today/past —
    that re-queued Vincent Price, Craig Krull and Copro every session."""

    def test_reopen_date_already_past_falls_back(self):
        v = self.venue("vpam", kind="museum")
        venues.apply_skip(v, {"reason": "closed_or_between_shows", "ts": 1,
                              "detail": "closed for summer; reopened Tue Sept 1, 2026 with only the "
                                        "permanent collection"}, date(2026, 9, 2))
        self.assertEqual(v["next_check"], "2026-09-23")

    def test_reopen_within_three_days_looks_after_opening(self):
        v = self.venue("krull")
        venues.apply_skip(v, {"reason": "closed_or_between_shows", "ts": 1,
                              "detail": "only the saved shows, opening Sept 5"}, date(2026, 9, 2))
        self.assertEqual(v["next_check"], "2026-09-06")
        w = self.venue("later")
        venues.apply_skip(w, {"reason": "closed_or_between_shows", "ts": 1,
                              "detail": "next show opens Sept 20"}, date(2026, 9, 2))
        self.assertEqual(w["next_check"], "2026-09-17")

    def test_unchanged_near_turnover_checks_after_the_close(self):
        v = self.venue("copro", shows=(("2026-08-15", "2026-09-04"), ("2026-09-12", "2026-10-16")))
        venues.apply_skip(v, {"reason": "unchanged", "ts": 1, "detail": "same"}, date(2026, 9, 2))
        self.assertEqual(v["next_check"], "2026-09-05")
        far = self.venue("far", shows=(("2026-08-15", "2026-10-15"),))
        venues.apply_skip(far, {"reason": "unchanged", "ts": 1, "detail": "same"}, date(2026, 9, 2))
        self.assertEqual(far["next_check"], "2026-10-13")

    def test_compute_next_check_never_today(self):
        v = self.venue("closing", shows=(("2026-08-01", "2026-09-03"),), scraped_days_ago=0)
        self.assertEqual(venues.compute_next_check(v, TODAY), date(2026, 9, 4))
        w = self.venue("normal", shows=(("2026-08-01", "2026-10-15"),), scraped_days_ago=0)
        self.assertEqual(venues.compute_next_check(w, TODAY), date(2026, 9, 22))   # cadence 21


class PathScoreTests(unittest.TestCase):
    def test_future_and_past_pages_never_become_exhibitions_url(self):
        self.assertEqual(venues._path_score("https://www.1301pe.com/future-exhibitions"), (1, None))
        self.assertEqual(venues._path_score("https://x.com/exhibitions/upcoming"), (1, None))
        self.assertEqual(venues._path_score("https://x.com/exhibitions/past"), (1, None))
        self.assertEqual(venues._path_score("https://x.com/exhibitions")[0], 3)
        venue = {"website": "https://www.1301pe.com"}
        best, cands = venues.attribute_urls(venue, [
            (0, "https://www.1301pe.com/future-exhibitions", "web_fetch"),
            (1, "https://www.1301pe.com/current-exhibition", "web_fetch")])
        self.assertEqual(best, "https://www.1301pe.com/current-exhibition")
        self.assertIn("https://www.1301pe.com/future-exhibitions", cands)
        best_only_future, _ = venues.attribute_urls(venue, [
            (0, "https://www.1301pe.com/future-exhibitions", "web_fetch")])
        self.assertIsNone(best_only_future)


class BackfillKindTests(RegistryBase):
    def test_backfill_includes_every_kind_and_needs_a_current_show(self):
        npo = self.venue("art-practice", kind="nonprofit", shows=(CURRENT,))
        uni = self.venue("new-wight", kind="university", shows=(CURRENT,))
        fut = self.venue("1301-pe", shows=(UPCOMING,))            # nothing on view yet
        two = self.venue("two", shows=(CURRENT, ("2026-08-10", "2026-11-01")))
        self.write(npo, uni, fut, two)
        todo = run_deep.zone_todo("los-angeles", "Hollywood", only_with_saves=True)
        self.assertEqual([v["id"] for v in todo], ["art-practice", "new-wight"])


class RequeueTests(RegistryBase):
    def test_cohorts_and_apply(self):
        fut = self.venue("1301-pe", shows=(UPCOMING,))
        unv = self.venue("fernberger", outcome="skipped:unverifiable")
        unv["last_skip"] = {"reason": "unverifiable",
                            "detail": "own site confirms current show; dates render via JavaScript"}
        dead = self.venue("dead", outcome="skipped:unverifiable")
        dead["last_skip"] = {"reason": "unverifiable", "detail": "site unreachable"}
        npo = self.venue("art-practice", kind="nonprofit", shows=(CURRENT,))
        gal = self.venue("karma", shows=(CURRENT,))                 # galleries: not the backfill cohort
        rec = self.venue("recent", kind="nonprofit", shows=(CURRENT,), outcome="skipped:unchanged",
                         scraped_days_ago=1)
        self.write(fut, unv, dead, npo, gal, rec)
        cohorts = venues.requeue_cohorts("los-angeles", TODAY)
        self.assertEqual(cohorts["future_only_saved"], ["1301-pe"])
        self.assertEqual(cohorts["retry_confirmed_show"], ["fernberger"])
        self.assertEqual(cohorts["single_save_backfill"], ["art-practice"])
        n = venues.apply_requeue("los-angeles", cohorts, TODAY)
        self.assertEqual(n, 3)
        reg = venues.index_by_id(venues.load_registry("los-angeles"))
        self.assertEqual(reg["fernberger"]["next_check"], TODAY.isoformat())
        self.assertEqual(reg["fernberger"]["requeue_reasons"], ["retry_confirmed_show"])
        self.assertIsNone(reg["karma"].get("requeue_reasons"))
        due = self.due()
        self.assertIn("requeued", due["art-practice"]["_reasons"])
        # the next skip hook clears the tag
        venues.on_log_skip({"venue": "Fernberger", "neighborhood": "Hollywood", "reason": "unchanged",
                            "detail": "same show", "ts": 1, "session": "s2", "url": None},
                           "los-angeles", None)
        reg = venues.index_by_id(venues.load_registry("los-angeles"))
        self.assertNotIn("requeue_reasons", reg["fernberger"])


class UpsertProtectionTests(RegistryBase):
    def test_bulk_upsert_protects_existing_schedule(self):
        v = self.venue("fernberger", next_check="2026-10-01", website="https://fernbergergallery.com")
        self.write(v)
        res = venues.bulk_upsert("los-angeles", [
            {"name": "Fernberger Gallery", "website": "https://fernbergergallery.com",
             "patch": {"next_check": "2026-09-01", "status": "unknown", "address": "747 N Western Ave",
                       "website": "https://fernbergergallery.com"}, "source": "seed-carla"},
            {"name": "Brand New Space", "patch": {"next_check": "2026-09-01", "status": "unknown",
                                                  "website": "https://brandnew.example"},
             "source": "seed-carla"},
        ], protect_existing=("status", "neighborhood", "next_check"))
        self.assertEqual(res["updated"], ["fernberger"])
        self.assertEqual(res["new"], ["brand-new-space"])
        reg = venues.index_by_id(venues.load_registry("los-angeles"))
        self.assertEqual(reg["fernberger"]["next_check"], "2026-10-01")   # protected
        self.assertEqual(reg["fernberger"]["status"], "active")
        self.assertEqual(reg["fernberger"]["address"], "747 N Western Ave")  # gap filled
        self.assertEqual(reg["brand-new-space"]["next_check"], "2026-09-01")  # new venue: full patch


class SkipAttributionTests(RegistryBase):
    def test_shortened_skip_name_lands_on_the_todo_venue(self):
        cand = self.venue("art-one-gallery-s-official-website", status="candidate", scraped_days_ago=None)
        cand["name"] = "Art One Gallery's Official Website"
        self.write(cand)
        trace = venues.SessionTrace("s1", "los-angeles")
        trace.todo_venues = [{"name": cand["name"], "key": tools._norm_venue(cand["name"]),
                              "venue_id": cand["id"]}]
        self.assertEqual(trace.todo_venue_id("Art One Gallery"), cand["id"])
        self.assertIsNone(trace.todo_venue_id("Art"))              # too short to trust
        self.assertIsNone(trace.todo_venue_id("Some Other Space"))
        vid = venues.on_log_skip({"venue": "Art One Gallery", "neighborhood": "Beverly Hills",
                                  "reason": "out_of_scope", "detail": "framing shop", "ts": 1,
                                  "session": "s1", "url": "https://www.yelp.com/biz/x"},
                                 "los-angeles", trace)
        self.assertEqual(vid, cand["id"])
        reg = venues.index_by_id(venues.load_registry("los-angeles"))
        self.assertEqual(len(reg), 1)                                 # no stray "art-one" record
        self.assertEqual(reg[cand["id"]]["status"], "out_of_scope")
        self.assertIn("Art One Gallery", reg[cand["id"]]["aliases"])


class SameSessionSkipTests(RegistryBase):
    def test_upcoming_save_after_between_shows_skip_keeps_the_skip_schedule(self):
        v = self.venue("1301-pe", website="https://www.1301pe.com", shows=())
        v["last_skip"] = {"ts": 1, "reason": "closed_or_between_shows", "detail": "reopens Sept 25",
                          "url": None, "session": "s1"}
        v["last_outcome"], v["next_check"] = "skipped:closed_or_between_shows", "2026-09-22"
        self.write(v)
        record = {"slug": "1301pe-moonbird", "title": "As the Moonbird Flies", "artist": None,
                  "start_date": UPCOMING[0], "end_date": UPCOMING[1], "source_urls": [],
                  "venue": {"name": "1301PE", "website": "https://www.1301pe.com",
                            "neighborhood": "Hollywood", "address": "6150 Wilshire Blvd",
                            "is_museum": False, "latitude": None, "longitude": None}}
        venues.on_save_show(record, "los-angeles", "s1", None)
        reg = venues.index_by_id(venues.load_registry("los-angeles"))
        self.assertEqual(reg["1301-pe"]["next_check"], "2026-09-22")
        self.assertEqual(reg["1301-pe"]["last_outcome"], "skipped:closed_or_between_shows")
        # a different session saving only an upcoming show is re-checked in 2 days
        venues.on_save_show(record, "los-angeles", "s2", None)
        reg = venues.index_by_id(venues.load_registry("los-angeles"))
        self.assertEqual(reg["1301-pe"]["last_outcome"], "saved")
        self.assertEqual(reg["1301-pe"]["next_check"], (date.today() + timedelta(days=2)).isoformat())


if __name__ == "__main__":
    unittest.main()


class GalleriesFirstCutoffTests(RegistryBase):
    """docs/GALLERIES.md stage S1: due_venues cutoffs + rank weighting."""

    def ranked(self, vid, tier, score, verified=True, **kw):
        v = self.venue(vid, scraped_days_ago=None, **kw)
        v["tier"], v["score"] = tier, score
        v["verification"] = {"status": "verified" if verified else "unverified",
                             "ts": ts(TODAY), "checks": {}}
        return v

    def test_require_verified_and_cutoffs(self):
        top = self.ranked("regen", 1, 0.9)
        mid = self.ranked("small-space", 3, 0.2)
        unranked = self.venue("nobody", scraped_days_ago=None)
        unverified = self.ranked("ghost", 1, 0.95, verified=False)
        self.write(top, mid, unranked, unverified)
        plain = self.due()
        self.assertEqual(set(plain), {"regen", "small-space", "nobody", "ghost"})
        self.assertEqual(set(self.due(require_verified=True)), {"regen", "small-space"})
        # cutoffs alone do not imply verification (ghost is tier 1 but unverified)
        self.assertEqual(set(self.due(min_tier=2)), {"regen", "ghost"})
        self.assertEqual(set(self.due(min_tier=2, require_verified=True)), {"regen"})
        self.assertEqual(set(self.due(min_score=0.5, require_verified=True)), {"regen"})
        self.assertNotIn("nobody", self.due(min_score=0.0))   # unranked drop under any cutoff

    def test_rank_weight_orders_the_queue(self):
        top = self.ranked("regen", 1, 0.9)
        mid = self.ranked("small-space", 3, 0.2)
        self.write(mid, top)
        base = [r["id"] for r in venues.due_venues("los-angeles", "Hollywood", TODAY)]
        self.assertEqual(base, ["regen", "small-space"])   # equal priority -> id order
        weighted = venues.due_venues("los-angeles", "Hollywood", TODAY, rank_weight=30)
        self.assertEqual([r["id"] for r in weighted], ["regen", "small-space"])
        self.assertIn("ranked", weighted[0]["_reasons"])
        # tier 1 already carries +10 over tier 3; the rank term adds 30 * (0.9 - 0.2)
        self.assertAlmostEqual(weighted[0]["_priority"] - weighted[1]["_priority"], 10 + 30 * 0.7)
