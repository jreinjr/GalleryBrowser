"""hours.parse_hours / breadth_value on the three real registry formats. Stdlib unittest, no network.

    PYTHONPATH=scraper/tests:scraper scraper/.venv/bin/python -m unittest discover -s scraper/tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hours  # noqa: E402

GOOGLE = ["Monday: Closed", "Tuesday: 11:00 AM – 6:00 PM",
          "Wednesday: 11:00 AM – 6:00 PM", "Thursday: 11:00 AM – 6:00 PM",
          "Friday: 11:00 AM – 6:00 PM", "Saturday: 11:00 AM – 6:00 PM",
          "Sunday: Closed"]


class ParseHours(unittest.TestCase):
    def test_range_line(self):
        p = hours.parse_hours(["Tue - Sat 10:00am to 6:00pm"])
        self.assertEqual((p["days_open"], p["hours_per_week"], p["by_appointment"], p["parsed"]),
                         (5, 40.0, False, True))

    def test_google_per_day_with_thin_spaces(self):
        p = hours.parse_hours(GOOGLE)
        self.assertEqual((p["days_open"], p["hours_per_week"]), (5, 35.0))
        self.assertFalse(p["by_appointment"])

    def test_google_alternating_days(self):
        p = hours.parse_hours(["Monday: Closed", "Tuesday: 12:00 – 5:00 PM", "Wednesday: Closed",
                               "Thursday: 12:00 – 5:00 PM", "Friday: Closed",
                               "Saturday: 12:00 – 5:00 PM", "Sunday: 12:00 – 5:00 PM"])
        self.assertEqual((p["days_open"], p["hours_per_week"]), (4, 20.0))

    def test_mixed_days_plus_appointment(self):
        p = hours.parse_hours(["Fri 12pm to 5pm", "Sun 12pm to 5pm", "Other days by appointment"])
        self.assertEqual((p["days_open"], p["hours_per_week"], p["by_appointment"]), (2, 10.0, True))

    def test_appointment_only(self):
        p = hours.parse_hours(["By appointment only"])
        self.assertEqual((p["days_open"], p["by_appointment"], p["parsed"]), (0, True, True))
        self.assertAlmostEqual(hours.breadth_value(p), hours.APPOINTMENT_ONLY_VALUE)

    def test_or_by_appointment_keeps_public_hours(self):
        p = hours.parse_hours(["Wed - Sun 12pm to 6pm", "or by appointment"])
        self.assertEqual((p["days_open"], p["hours_per_week"], p["by_appointment"]), (5, 30.0, True))

    def test_en_dash_range_without_ampm(self):
        p = hours.parse_hours(["Open Tue–Sat 11–6"])
        self.assertEqual((p["days_open"], p["hours_per_week"]), (5, 35.0))

    def test_string_input_and_empty(self):
        self.assertEqual(hours.parse_hours("Mon - Fri 9am to 5pm; Sat 10am to 2pm")["days_open"], 6)
        self.assertFalse(hours.parse_hours([])["parsed"])
        self.assertFalse(hours.parse_hours(["Check site for current gallery hours"])["parsed"])
        self.assertEqual(hours.breadth_value(hours.parse_hours([])), 0.0)

    def test_breadth_monotone(self):
        one = hours.breadth_value(hours.parse_hours(["Sat 12pm to 5pm"]))
        three = hours.breadth_value(hours.parse_hours(["Thu - Sat 1pm to 5pm"]))
        five = hours.breadth_value(hours.parse_hours(["Tue - Sat 10am to 6pm"]))
        six = hours.breadth_value(hours.parse_hours(["Mon - Sat 11am to 6pm"]))
        self.assertTrue(hours.APPOINTMENT_ONLY_VALUE < one < three < five <= six <= 1.0)

    def test_hours_breadth_prefers_google(self):
        v = {"google": {"hours": GOOGLE}, "hours": ["By appointment only"], "status": "active"}
        val, basis = hours.hours_breadth(v)
        self.assertTrue(basis.startswith("google"))
        self.assertGreater(val, 0.5)
        val2, basis2 = hours.hours_breadth({"hours": [], "google": None, "status": "appointment_only"})
        self.assertEqual((val2, basis2), (hours.APPOINTMENT_ONLY_VALUE, "status: appointment_only"))
        self.assertEqual(hours.hours_breadth({"status": "active"}), (0.0, "no hours on record"))


if __name__ == "__main__":
    unittest.main()
