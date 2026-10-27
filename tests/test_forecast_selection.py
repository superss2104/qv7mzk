import unittest
from datetime import datetime, timedelta, timezone

from backend.app.services.forecast_selection import select_forecast


UTC = timezone.utc
DELIVERY = "2026-01-16T12:00:00Z"


def point(published_at, generation=100, delivery=DELIVERY):
    return {"start_time": delivery, "publish_time": published_at, "generation_mw": generation}


class ForecastSelectionTests(unittest.TestCase):
    def test_selects_latest_forecast_published_at_or_before_cutoff(self):
        result = select_forecast([
            point("2026-01-16T06:00:00Z", 90),
            point("2026-01-16T09:00:00Z", 100),
            point("2026-01-16T10:01:00Z", 110),
        ], DELIVERY, mode="fixed_cutoff", cutoff="2026-01-16T10:00:00Z")
        self.assertEqual(result.forecast.generation_mw, 100)
        self.assertEqual(result.forecast.published_at.isoformat(), "2026-01-16T09:00:00+00:00")
        self.assertEqual(result.lead_time, timedelta(hours=3))

    def test_publication_exactly_at_cutoff_is_eligible(self):
        result = select_forecast([point("2026-01-16T10:00:00Z")], DELIVERY, mode="fixed_cutoff", cutoff="2026-01-16T10:00:00Z")
        self.assertIsNotNone(result.forecast)

    def test_no_eligible_forecast_is_explicit(self):
        result = select_forecast([point("2026-01-16T10:01:00Z")], DELIVERY, mode="fixed_cutoff", cutoff="2026-01-16T10:00:00Z")
        self.assertIsNone(result.forecast)
        self.assertIsNone(result.lead_time)

    def test_adding_forecasts_after_cutoff_cannot_change_selection(self):
        earlier = [point("2026-01-16T06:00:00Z", 90)]
        later = earlier + [point("2026-01-16T10:01:00Z", 110), point("2026-01-16T11:00:00Z", 120)]
        kwargs = {"mode": "fixed_cutoff", "cutoff": "2026-01-16T10:00:00Z"}
        before = select_forecast(earlier, DELIVERY, **kwargs)
        after = select_forecast(later, DELIVERY, **kwargs)
        self.assertEqual(before, after)

    def test_fixed_horizon_derives_delivery_specific_cutoff(self):
        result = select_forecast([
            point("2026-01-16T09:00:00Z", 100),
            point("2026-01-16T10:00:00Z", 110),
        ], DELIVERY, mode="fixed_horizon", horizon=timedelta(hours=2))
        self.assertEqual(result.cutoff, datetime(2026, 1, 16, 10, tzinfo=UTC))
        self.assertEqual(result.forecast.generation_mw, 110)

    def test_duplicate_handling_is_deterministic(self):
        row = point("2026-01-16T09:00:00Z", 100)
        result = select_forecast([row, row], DELIVERY, mode="fixed_cutoff", cutoff="2026-01-16T10:00:00Z")
        self.assertEqual(result.forecast.generation_mw, 100)
        with self.assertRaisesRegex(ValueError, "Conflicting forecast duplicate"):
            select_forecast([row, point("2026-01-16T09:00:00Z", 101)], DELIVERY, mode="fixed_cutoff", cutoff="2026-01-16T10:00:00Z")

    def test_fixed_cutoff_requires_timezone_and_cutoff(self):
        with self.assertRaisesRegex(ValueError, "requires cutoff"):
            select_forecast([], DELIVERY, mode="fixed_cutoff")
        with self.assertRaisesRegex(ValueError, "must include a timezone"):
            select_forecast([], "2026-01-16T12:00:00", mode="fixed_cutoff", cutoff="2026-01-16T10:00:00Z")


if __name__ == "__main__":
    unittest.main()
