import csv
import unittest
from pathlib import Path

from backend.app.services.forecast_selection import select_forecast


ROOT = Path(__file__).resolve().parents[1]


def read_rows(name):
    with (ROOT / "data" / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class GoldenSnapshotTests(unittest.TestCase):
    def test_jan_15_0730_vintage_matches_manually_checked_api_rows(self):
        delivery = "2026-01-15T08:00:00Z"
        forecast_rows = [row for row in read_rows("forecasts.csv") if row["start_time"] == delivery]
        selection = select_forecast(
            forecast_rows,
            delivery,
            mode="fixed_cutoff",
            cutoff="2026-01-15T07:30:00Z",
        )
        self.assertEqual(selection.forecast.published_at.isoformat(), "2026-01-15T07:30:00+00:00")
        self.assertEqual(selection.forecast.generation_mw, 12253.0)

        actual_rows = [row for row in read_rows("actuals.csv") if row["start_time"] in {delivery, "2026-01-15T08:30:00Z"}]
        actual_by_start = {row["start_time"]: float(row["generation_mw"]) for row in actual_rows}
        hourly_actual = (actual_by_start[delivery] + actual_by_start["2026-01-15T08:30:00Z"]) / 2
        self.assertEqual(actual_by_start, {delivery: 12719.0, "2026-01-15T08:30:00Z": 12302.0})
        self.assertEqual(hourly_actual, 12510.5)
        self.assertEqual(selection.forecast.generation_mw - hourly_actual, -257.5)


if __name__ == "__main__":
    unittest.main()
