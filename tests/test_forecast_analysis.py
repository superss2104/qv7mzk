import unittest
from datetime import datetime, timedelta, timezone

from backend.app.services.forecast_analysis import analyze_forecast_window


UTC = timezone.utc


def forecast(start, published, value):
    return {"start_time": start, "publish_time": published, "generation_mw": value}


def actual(start, value):
    return {"start_time": start, "generation_mw": value}


class ForecastAnalysisTests(unittest.TestCase):
    def test_lead_bucket_boundaries_are_lower_inclusive_and_upper_exclusive(self):
        delivery_from = datetime(2026, 1, 15, tzinfo=UTC)
        leads = (0, 1, 3, 6, 12, 24, 36, 48, 72)
        forecasts = []
        actuals = []
        for offset, lead in enumerate(leads):
            delivery = delivery_from + timedelta(hours=offset)
            delivery_text = delivery.isoformat().replace("+00:00", "Z")
            published_text = (delivery - timedelta(hours=lead)).isoformat().replace("+00:00", "Z")
            forecasts.append(forecast(delivery_text, published_text, 1))
            actuals.extend([
                actual(delivery_text, 0),
                actual((delivery + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"), 0),
            ])

        result = analyze_forecast_window(
            forecasts,
            actuals,
            delivery_from=delivery_from,
            delivery_to=delivery_from + timedelta(hours=len(leads)),
            mode="fixed_cutoff",
            cutoff=delivery_from - timedelta(hours=1),
        )
        self.assertEqual([bucket["label"] for bucket in result["lead_time"]["buckets"]], [
            "0–1 h", "1–3 h", "3–6 h", "6–12 h", "12–24 h", "24–36 h", "36–48 h", "48–72 h", "72+ h",
        ])
        self.assertTrue(all(bucket["sample_count"] == 1 for bucket in result["lead_time"]["buckets"]))
        self.assertEqual(result["lead_time"]["observation_count"], len(leads))

    def test_lead_buckets_and_revision_use_only_eligible_same_interval_vintages(self):
        delivery = "2026-01-15T12:00:00Z"
        forecasts = [
            forecast(delivery, "2026-01-15T08:00:00Z", 130),
            forecast(delivery, "2026-01-15T10:00:00Z", 120),
            forecast(delivery, delivery, 95),
            forecast(delivery, "2026-01-15T12:01:00Z", 999),
        ]
        actuals = [actual(delivery, 100), actual("2026-01-15T12:30:00Z", 100)]

        result = analyze_forecast_window(
            forecasts,
            actuals,
            delivery_from=datetime(2026, 1, 15, tzinfo=UTC),
            delivery_to=datetime(2026, 1, 16, tzinfo=UTC),
            mode="fixed_cutoff",
            cutoff=datetime(2026, 1, 15, 8, tzinfo=UTC),
        )

        buckets = {item["label"]: item for item in result["lead_time"]["buckets"]}
        self.assertEqual(set(buckets), {"0–1 h", "1–3 h", "3–6 h"})
        self.assertEqual([buckets[label]["sample_count"] for label in ("0–1 h", "1–3 h", "3–6 h")], [1, 1, 1])
        self.assertEqual(result["lead_time"]["observation_count"], 3)
        self.assertEqual(result["revision"]["matched_intervals"], 1)
        self.assertEqual(result["revision"]["decision_time"]["mae_mw"], 30)
        self.assertEqual(result["revision"]["later_vintage"]["mae_mw"], 5)
        self.assertEqual(result["revision"]["mae_improvement_mw"], 25)
        row = result["revision"]["intervals"][0]
        self.assertEqual(row["decision_publish_time"], "2026-01-15T08:00:00Z")
        self.assertEqual(row["later_publish_time"], delivery)
        self.assertEqual(row["later_lead_hours"], 0)
        self.assertIsNone(result["error_distribution"]["p10_mw"])
        self.assertEqual(result["error_distribution"]["sample_count"], 0)
        self.assertEqual(result["error_distribution"]["bands"], [])

    def test_error_quantiles_and_bands_require_minimum_sample(self):
        forecasts = []
        actuals = []
        for offset in range(30):
            decision_day = 1 if offset < 24 else 2
            hour = offset if offset < 24 else offset - 24
            delivery_day = decision_day + 1
            delivery = datetime(2026, 1, delivery_day, hour, tzinfo=UTC)
            cutoff = datetime(2026, 1, decision_day, 7, 30, tzinfo=UTC)
            start = delivery.isoformat().replace("+00:00", "Z")
            pub = cutoff.isoformat().replace("+00:00", "Z")
            forecasts.append(forecast(start, pub, float(offset)))
            actuals.extend([
                actual(start, 0),
                actual((delivery.replace(minute=30)).isoformat().replace("+00:00", "Z"), 0),
            ])

        result = analyze_forecast_window(
            forecasts,
            actuals,
            delivery_from=datetime(2026, 1, 2, tzinfo=UTC),
            delivery_to=datetime(2026, 1, 3, tzinfo=UTC),
            mode="fixed_cutoff",
            cutoff=datetime(2026, 1, 1, 7, 30, tzinfo=UTC),
        )
        distribution = result["error_distribution"]
        self.assertEqual(distribution["sample_count"], 30)
        self.assertAlmostEqual(distribution["p10_mw"], 2.9)
        self.assertAlmostEqual(distribution["p50_mw"], 14.5)
        self.assertAlmostEqual(distribution["p90_mw"], 26.1)
        self.assertEqual(len(distribution["bands"]), 24)


if __name__ == "__main__":
    unittest.main()
