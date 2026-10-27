import math
import unittest
from datetime import datetime, timedelta, timezone

from backend.app.services.evaluation import evaluate, hourly_actual_means


def forecast(start, published, value):
    return {"start_time": start, "publish_time": published, "generation_mw": value}


def actual(start, value):
    return {"start_time": start, "generation_mw": value}


class HourlyActualTests(unittest.TestCase):
    def test_averages_two_half_hours_and_drops_incomplete_hour(self):
        means = hourly_actual_means([
            actual("2026-01-15T08:00:00Z", 120),
            actual("2026-01-15T08:30:00Z", 100),
            actual("2026-01-15T09:00:00Z", 80),
        ])
        self.assertEqual(means, {datetime(2026, 1, 15, 8, tzinfo=timezone.utc): 110})


class EvaluationTests(unittest.TestCase):
    def test_metrics_use_only_cutoff_eligible_forecasts_and_complete_actual_hours(self):
        result = evaluate(
            [
                forecast("2026-01-15T08:00:00Z", "2026-01-15T07:30:00Z", 12253),
                forecast("2026-01-15T08:00:00Z", "2026-01-15T08:00:00Z", 15000),
            ],
            [actual("2026-01-15T08:00:00Z", 12719), actual("2026-01-15T08:30:00Z", 12302)],
            delivery_from="2026-01-15T08:00:00Z",
            delivery_to="2026-01-15T09:00:00Z",
            mode="fixed_cutoff",
            cutoff="2026-01-15T07:30:00Z",
        )
        self.assertEqual(result.metrics.expected_intervals, 1)
        self.assertEqual(result.metrics.matched_intervals, 1)
        self.assertEqual(result.metrics.matched_coverage, 1.0)
        self.assertEqual(result.intervals[0]["publish_time"], "2026-01-15T07:30:00Z")
        self.assertEqual(result.metrics.mae_mw, 257.5)
        self.assertEqual(result.metrics.rmse_mw, 257.5)
        self.assertEqual(result.metrics.bias_mw, -257.5)

    def test_coverage_separates_missing_forecasts_and_actuals(self):
        result = evaluate(
            [forecast("2026-01-15T08:00:00Z", "2026-01-15T07:00:00Z", 100)],
            [actual("2026-01-15T08:00:00Z", 90)],  # missing the second half-hour
            delivery_from="2026-01-15T08:00:00Z",
            delivery_to="2026-01-15T10:00:00Z",
            mode="fixed_cutoff",
            cutoff="2026-01-15T08:00:00Z",
        )
        self.assertEqual(result.metrics.forecast_coverage, 0.5)
        self.assertEqual(result.metrics.actual_coverage, 0.0)
        self.assertEqual(result.metrics.matched_coverage, 0.0)
        self.assertIsNone(result.metrics.mae_mw)
        self.assertIsNone(result.intervals[0]["actual_mw"])
        self.assertIsNone(result.intervals[1]["forecast_mw"])

    def test_calculates_metrics_only_over_matched_intervals(self):
        result = evaluate(
            [
                forecast("2026-01-15T08:00:00Z", "2026-01-15T07:00:00Z", 102),
                forecast("2026-01-15T09:00:00Z", "2026-01-15T08:00:00Z", 96),
            ],
            [
                actual("2026-01-15T08:00:00Z", 100), actual("2026-01-15T08:30:00Z", 100),
                actual("2026-01-15T09:00:00Z", 100), actual("2026-01-15T09:30:00Z", 100),
            ],
            delivery_from="2026-01-15T08:00:00Z",
            delivery_to="2026-01-15T10:00:00Z",
            mode="fixed_cutoff",
            cutoff="2026-01-15T08:00:00Z",
        )
        self.assertEqual(result.metrics.mae_mw, 3.0)
        self.assertAlmostEqual(result.metrics.rmse_mw, math.sqrt(10))
        self.assertEqual(result.metrics.bias_mw, -1.0)

    def test_fixed_horizon_uses_interval_specific_cutoffs(self):
        result = evaluate(
            [
                forecast("2026-01-15T08:00:00Z", "2026-01-15T06:00:00Z", 100),
                forecast("2026-01-15T08:00:00Z", "2026-01-15T07:00:00Z", 200),
            ],
            [],
            delivery_from="2026-01-15T08:00:00Z",
            delivery_to="2026-01-15T09:00:00Z",
            mode="fixed_horizon",
            horizon=timedelta(hours=2),
        )
        self.assertEqual(result.intervals[0]["cutoff"], "2026-01-15T06:00:00Z")
        self.assertEqual(result.intervals[0]["forecast_mw"], 100)

    def test_rejects_non_hour_aligned_window(self):
        with self.assertRaisesRegex(ValueError, "aligned to UTC hours"):
            evaluate([], [], delivery_from="2026-01-15T08:30:00Z", delivery_to="2026-01-15T09:30:00Z", mode="fixed_cutoff", cutoff="2026-01-15T08:00:00Z")


if __name__ == "__main__":
    unittest.main()
