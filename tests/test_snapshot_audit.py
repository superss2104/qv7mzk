"""Independent checks of the bundled snapshot and its analytical outputs."""

import csv
import math
import unittest
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from backend.app.services.evaluation import evaluate_decision_period
from backend.app.services.forecast_analysis import analyze_forecast_window
from backend.app.services.forecast_selection import select_forecast


ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def rows(filename):
    with (ROOT / "data" / filename).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def empirical_quantile(values, probability):
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def metrics(errors):
    return {
        "sample_count": len(errors),
        "mae_mw": sum(abs(error) for error in errors) / len(errors),
        "rmse_mw": math.sqrt(sum(error * error for error in errors) / len(errors)),
        "bias_mw": sum(errors) / len(errors),
    }


class SnapshotAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.forecast_rows = rows("forecasts.csv")
        cls.actual_rows = rows("actuals.csv")
        cls.forecasts = defaultdict(list)
        for row in cls.forecast_rows:
            cls.forecasts[timestamp(row["start_time"])].append(row)
        cls.actual_points = {
            timestamp(row["start_time"]): float(row["generation_mw"])
            for row in cls.actual_rows
        }
        cls.hourly_actuals = {}
        for instant, value in cls.actual_points.items():
            hour = instant.replace(minute=0, second=0, microsecond=0)
            half_hour = hour + timedelta(minutes=30)
            if hour in cls.actual_points and half_hour in cls.actual_points:
                cls.hourly_actuals[hour] = (cls.actual_points[hour] + cls.actual_points[half_hour]) / 2

    def test_actual_rows_cover_complete_adjacent_settlement_dates(self):
        counts = Counter(row["settlement_date"] for row in self.actual_rows)
        dates = sorted(date.fromisoformat(day) for day in counts)
        self.assertEqual(dates, [dates[0] + timedelta(days=offset) for offset in range(len(dates))])
        self.assertTrue(all(count == 48 for count in counts.values()))

        decision_days = (date(2026, 2, 1) - date(2026, 1, 1)).days
        january_delivery_start = datetime(2026, 1, 2, tzinfo=UTC)
        january_delivery_end = january_delivery_start + timedelta(days=decision_days)
        january_hours = sum(
            january_delivery_start <= delivery < january_delivery_end
            for delivery in self.hourly_actuals
        )
        self.assertEqual(january_hours, decision_days * 24)

    def test_lead_buckets_account_for_all_eligible_repeated_vintages(self):
        delivery_from = datetime(2026, 1, 15, tzinfo=UTC)
        delivery_to = delivery_from + timedelta(days=1)
        bounds = ((0, 1), (1, 3), (3, 6), (6, 12), (12, 24), (24, 36), (36, 48), (48, 72), (72, math.inf))
        expected_counts = [0] * len(bounds)
        raw_observation_count = 0
        candidate_count = 0
        post_delivery_count = 0
        incomplete_actual_count = 0
        for delivery, points in self.forecasts.items():
            if not delivery_from <= delivery < delivery_to:
                continue
            unique = {(row["publish_time"], row["generation_mw"]) for row in points}
            candidate_count += len(unique)
            if delivery not in self.hourly_actuals:
                incomplete_actual_count += len(unique)
                continue
            for published_text, _value in unique:
                published = timestamp(published_text)
                if published > delivery:
                    post_delivery_count += 1
                    continue
                lead = (delivery - published).total_seconds() / 3600
                for index, (lower, upper) in enumerate(bounds):
                    if lower <= lead < upper:
                        expected_counts[index] += 1
                        raw_observation_count += 1
                        break

        result = analyze_forecast_window(
            self.forecast_rows,
            self.actual_rows,
            delivery_from=delivery_from,
            delivery_to=delivery_to,
            mode="fixed_cutoff",
            cutoff=datetime(2026, 1, 14, 7, 30, tzinfo=UTC),
        )
        buckets = result["lead_time"]["buckets"]
        self.assertEqual([bucket["sample_count"] for bucket in buckets], [count for count in expected_counts if count])
        self.assertEqual(sum(bucket["sample_count"] for bucket in buckets), raw_observation_count)
        self.assertEqual(result["lead_time"]["observation_count"], raw_observation_count)
        lead_time = result["lead_time"]
        self.assertEqual(lead_time["candidate_forecasts"], candidate_count)
        self.assertEqual(lead_time["bucketed_observations"], raw_observation_count)
        self.assertEqual(lead_time["unbucketed_observations"], raw_observation_count - sum(bucket["sample_count"] for bucket in buckets))
        self.assertEqual(lead_time["excluded_post_delivery_count"], post_delivery_count)
        self.assertEqual(lead_time["excluded_incomplete_actual_count"], incomplete_actual_count)
        self.assertEqual(candidate_count, raw_observation_count + post_delivery_count + incomplete_actual_count)
        self.assertGreater(raw_observation_count, 24)

    def test_revision_metrics_and_historical_quantiles_match_independent_rows(self):
        delivery_from = datetime(2026, 1, 15, tzinfo=UTC)
        cutoff = datetime(2026, 1, 14, 7, 30, tzinfo=UTC)
        result = analyze_forecast_window(
            self.forecast_rows,
            self.actual_rows,
            delivery_from=delivery_from,
            delivery_to=delivery_from + timedelta(days=1),
            mode="fixed_cutoff",
            cutoff=cutoff,
        )

        decision_errors = []
        later_errors = []
        for delivery in (delivery_from + timedelta(hours=hour) for hour in range(24)):
            actual = self.hourly_actuals[delivery]
            points = self.forecasts[delivery]
            eligible = [row for row in points if timestamp(row["publish_time"]) <= cutoff]
            later = [row for row in points if cutoff < timestamp(row["publish_time"]) <= delivery]
            if not eligible or not later:
                continue
            selected = max(eligible, key=lambda row: timestamp(row["publish_time"]))
            revised = max(later, key=lambda row: timestamp(row["publish_time"]))
            selected_error = float(selected["generation_mw"]) - actual
            revised_error = float(revised["generation_mw"]) - actual
            decision_errors.append(selected_error)
            later_errors.append(revised_error)
            output = next(row for row in result["revision"]["intervals"] if timestamp(row["delivery_start"]) == delivery)
            self.assertEqual(output["actual_mw"], actual)
            self.assertEqual(output["decision_forecast_mw"], float(selected["generation_mw"]))
            self.assertEqual(output["decision_publish_time"], timestamp(selected["publish_time"]).isoformat().replace("+00:00", "Z"))
            self.assertAlmostEqual(output["decision_error_mw"], selected_error)
            self.assertEqual(output["later_forecast_mw"], float(revised["generation_mw"]))
            self.assertEqual(output["later_publish_time"], timestamp(revised["publish_time"]).isoformat().replace("+00:00", "Z"))
            self.assertAlmostEqual(output["later_error_mw"], revised_error)

        expected_decision = metrics(decision_errors)
        expected_later = metrics(later_errors)
        revision = result["revision"]
        self.assertEqual(revision["matched_intervals"], len(decision_errors))
        self.assertEqual(revision["decision_time"]["sample_count"], len(decision_errors))
        self.assertEqual(revision["later_vintage"]["sample_count"], len(later_errors))
        for field in ("mae_mw", "rmse_mw", "bias_mw"):
            self.assertAlmostEqual(revision["decision_time"][field], expected_decision[field])
            self.assertAlmostEqual(revision["later_vintage"][field], expected_later[field])
        self.assertAlmostEqual(revision["mae_improvement_mw"], expected_decision["mae_mw"] - expected_later["mae_mw"])
        self.assertAlmostEqual(revision["rmse_improvement_mw"], expected_decision["rmse_mw"] - expected_later["rmse_mw"])

        historical_errors = []
        for day in range(1, 32):
            decision_date = date(2026, 1, day)
            delivery_date = decision_date + timedelta(days=1)
            daily_cutoff = datetime.combine(decision_date, time(7, 30), UTC)
            for hour in range(24):
                delivery = datetime.combine(delivery_date, time(hour), UTC)
                candidates = [row for row in self.forecasts[delivery] if timestamp(row["publish_time"]) <= daily_cutoff]
                if candidates and delivery in self.hourly_actuals:
                    selected = max(candidates, key=lambda row: timestamp(row["publish_time"]))
                    historical_errors.append(float(selected["generation_mw"]) - self.hourly_actuals[delivery])
        distribution = result["error_distribution"]
        self.assertEqual(distribution["sample_count"], len(historical_errors))
        self.assertAlmostEqual(distribution["p10_mw"], empirical_quantile(historical_errors, 0.10))
        self.assertAlmostEqual(distribution["p50_mw"], empirical_quantile(historical_errors, 0.50))
        self.assertAlmostEqual(distribution["p90_mw"], empirical_quantile(historical_errors, 0.90))
        self.assertTrue(all(
            band["p10_mw"] <= band["p50_mw"] <= band["p90_mw"]
            and math.isclose(band["p10_mw"] - band["forecast_mw"], distribution["p10_mw"])
            and math.isclose(band["p50_mw"] - band["forecast_mw"], distribution["p50_mw"])
            and math.isclose(band["p90_mw"] - band["forecast_mw"], distribution["p90_mw"])
            for band in distribution["bands"]
        ))

    def test_fixed_horizon_historical_sample_uses_the_same_horizon_rule(self):
        delivery_from = datetime(2026, 1, 15, tzinfo=UTC)
        for horizon_hours in (12, 24, 36, 48):
            horizon = timedelta(hours=horizon_hours)
            result = analyze_forecast_window(
                self.forecast_rows,
                self.actual_rows,
                delivery_from=delivery_from,
                delivery_to=delivery_from + timedelta(days=1),
                mode="fixed_horizon",
                horizon=horizon,
            )
            expected_errors = []
            for day in range(1, 32):
                delivery_date = date(2026, 1, day) + timedelta(days=1)
                for hour in range(24):
                    delivery = datetime.combine(delivery_date, time(hour), UTC)
                    candidates = [
                        row for row in self.forecasts[delivery]
                        if timestamp(row["publish_time"]) <= delivery - horizon
                    ]
                    if candidates and delivery in self.hourly_actuals:
                        selected = max(candidates, key=lambda row: timestamp(row["publish_time"]))
                        expected_errors.append(float(selected["generation_mw"]) - self.hourly_actuals[delivery])

            distribution = result["error_distribution"]
            self.assertIsNone(distribution["cutoff_time_utc"])
            self.assertEqual(distribution["selection_rule"], "fixed_horizon")
            self.assertEqual(distribution["sample_count"], len(expected_errors))
            self.assertAlmostEqual(distribution["p10_mw"], empirical_quantile(expected_errors, 0.10))
            self.assertAlmostEqual(distribution["p50_mw"], empirical_quantile(expected_errors, 0.50))
            self.assertAlmostEqual(distribution["p90_mw"], empirical_quantile(expected_errors, 0.90))

    def test_january_monthly_metrics_pool_errors_and_coverage(self):
        summary = evaluate_decision_period(
            self.forecast_rows,
            self.actual_rows,
            decision_from=date(2026, 1, 1),
            decision_to=date(2026, 2, 1),
            cutoff_time_utc=time(7, 30),
        )
        independently_scored = []
        for day in range(1, 32):
            decision_date = date(2026, 1, day)
            delivery_date = decision_date + timedelta(days=1)
            cutoff = datetime.combine(decision_date, time(7, 30), UTC)
            daily_errors = []
            for hour in range(24):
                delivery = datetime.combine(delivery_date, time(hour), UTC)
                candidates = [row for row in self.forecasts[delivery] if timestamp(row["publish_time"]) <= cutoff]
                if candidates and delivery in self.hourly_actuals:
                    selected = max(candidates, key=lambda row: timestamp(row["publish_time"]))
                    daily_errors.append(float(selected["generation_mw"]) - self.hourly_actuals[delivery])
            independently_scored.extend(daily_errors)
            daily_report = summary["daily"][day - 1]["metrics"]
            self.assertEqual(daily_report["matched_intervals"], len(daily_errors))
            self.assertAlmostEqual(daily_report["mae_mw"], metrics(daily_errors)["mae_mw"])
            self.assertAlmostEqual(daily_report["rmse_mw"], metrics(daily_errors)["rmse_mw"])
            self.assertAlmostEqual(daily_report["bias_mw"], metrics(daily_errors)["bias_mw"])

        overall = summary["metrics"]
        expected_metrics = metrics(independently_scored)
        self.assertEqual(overall["expected_intervals"], (date(2026, 2, 1) - date(2026, 1, 1)).days * 24)
        self.assertEqual(overall["forecast_available"], overall["actual_available"])
        self.assertEqual(overall["matched_intervals"], len(independently_scored))
        self.assertEqual(overall["matched_intervals"], overall["forecast_available"])
        self.assertAlmostEqual(overall["matched_coverage"], overall["matched_intervals"] / overall["expected_intervals"])
        for field in ("mae_mw", "rmse_mw", "bias_mw"):
            self.assertAlmostEqual(overall[field], expected_metrics[field])
        self.assertNotAlmostEqual(overall["rmse_mw"], sum(day["metrics"]["rmse_mw"] for day in summary["daily"]) / len(summary["daily"]))

    def test_every_snapshot_interval_obeys_cutoff_and_horizon_selection(self):
        for delivery, points in self.forecasts.items():
            day_before = delivery.date() - timedelta(days=1)
            cutoff = datetime.combine(day_before, time(7, 30), UTC)
            modes = [("fixed_cutoff", cutoff, None)] + [
                ("fixed_horizon", None, timedelta(hours=value)) for value in (12, 24, 36, 48)
            ]
            for mode, fixed_cutoff, horizon in modes:
                effective_cutoff = fixed_cutoff if fixed_cutoff is not None else delivery - horizon
                expected = max(
                    (row for row in points if timestamp(row["publish_time"]) <= effective_cutoff),
                    key=lambda row: timestamp(row["publish_time"]),
                    default=None,
                )
                selected = select_forecast(
                    points,
                    delivery,
                    mode=mode,
                    cutoff=fixed_cutoff,
                    horizon=horizon,
                ).forecast
                self.assertEqual(selected is None, expected is None)
                if selected is not None:
                    self.assertLessEqual(selected.published_at, effective_cutoff)
                    self.assertLessEqual(selected.published_at, delivery)
                    self.assertEqual(selected.generation_mw, float(expected["generation_mw"]))

                as_of_cutoff = [row for row in points if timestamp(row["publish_time"]) <= effective_cutoff]
                as_of_selection = select_forecast(
                    as_of_cutoff,
                    delivery,
                    mode=mode,
                    cutoff=fixed_cutoff,
                    horizon=horizon,
                ).forecast
                self.assertEqual(selected, as_of_selection)


if __name__ == "__main__":
    unittest.main()
