import unittest
from datetime import datetime, timezone

from scripts.ingest_data import normalize_actuals, normalize_forecasts


def actual(publish_time, generation):
    return {
        "publishTime": publish_time,
        "startTime": "2026-01-15T00:00:00Z",
        "settlementDate": "2026-01-15",
        "settlementPeriod": 1,
        "generation": generation,
    }


def forecast(publish_time, start_time="2026-01-15T01:00:00Z", generation=100):
    return {"publishTime": publish_time, "startTime": start_time, "generation": generation}


class NormalizeActualsTests(unittest.TestCase):
    def test_keeps_latest_revision_and_preserves_publication_time(self):
        rows, duplicates = normalize_actuals([
            actual("2026-01-15T00:30:00Z", 50),
            actual("2026-01-15T02:30:00Z", 60),
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["generation_mw"], 60)
        self.assertEqual(rows[0]["publish_time"], "2026-01-15T02:30:00Z")
        self.assertEqual(duplicates, 1)

    def test_rejects_conflicting_rows_with_same_publication_time(self):
        with self.assertRaisesRegex(ValueError, "Conflicting actual duplicate"):
            normalize_actuals([actual("2026-01-15T00:30:00Z", 50), actual("2026-01-15T00:30:00Z", 60)])


class NormalizeForecastsTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 1, 15, tzinfo=timezone.utc)
        self.end = datetime(2026, 1, 16, tzinfo=timezone.utc)

    def test_preserves_distinct_publication_vintages(self):
        rows, duplicates = normalize_forecasts([
            forecast("2026-01-15T07:30:00Z", generation=100),
            forecast("2026-01-15T09:30:00Z", generation=120),
        ], self.start, self.end)
        self.assertEqual(len(rows), 2)
        self.assertEqual([row["publish_time"] for row in rows], ["2026-01-15T07:30:00Z", "2026-01-15T09:30:00Z"])
        self.assertEqual(duplicates, 0)

    def test_rejects_publication_outside_requested_half_open_window(self):
        with self.assertRaisesRegex(ValueError, "outside requested window"):
            normalize_forecasts([forecast("2026-01-16T00:00:00Z")], self.start, self.end)

    def test_collapses_only_identical_duplicate_rows(self):
        row = forecast("2026-01-15T07:30:00Z")
        rows, duplicates = normalize_forecasts([row, row], self.start, self.end)
        self.assertEqual(len(rows), 1)
        self.assertEqual(duplicates, 1)

    def test_rejects_conflicting_duplicate_vintage_delivery(self):
        with self.assertRaisesRegex(ValueError, "Conflicting forecast duplicate"):
            normalize_forecasts([
                forecast("2026-01-15T07:30:00Z", generation=100),
                forecast("2026-01-15T07:30:00Z", generation=101),
            ], self.start, self.end)


if __name__ == "__main__":
    unittest.main()
