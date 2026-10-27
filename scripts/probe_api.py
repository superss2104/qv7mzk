
import requests
from collections import Counter
from datetime import datetime

BASE = "https://data.elexon.co.uk/bmrs/api/v1"

def fetch(path, params):
    response = requests.get(
        f"{BASE}/{path}",
        params=params,
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else data.get("data", [])

# Actual wind generation for January 15.
actuals = fetch("datasets/FUELHH/stream", {
    "fuelType": "WIND",
    "settlementDateFrom": "2026-01-15",
    "settlementDateTo": "2026-01-15",
})

# All forecast publications on January 15.
forecasts = fetch("datasets/WINDFOR/stream", {
    "publishDateTimeFrom": "2026-01-15T00:00:00Z",
    "publishDateTimeTo": "2026-01-16T00:00:00Z",
})

def parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

actual_starts = {parse(r["startTime"]) for r in actuals}
forecast_starts = {parse(r["startTime"]) for r in forecasts}

print("ACTUALS")
print("Rows:", len(actuals))
print("Unique startTimes:", len(actual_starts))

actual_deltas = Counter(
    (b - a).total_seconds() / 60
    for a, b in zip(sorted(actual_starts), sorted(actual_starts)[1:])
)
print("Actual interval gaps (minutes):", actual_deltas)

print("\nFORECASTS")
print("Rows:", len(forecasts))
print("Unique delivery startTimes:", len(forecast_starts))
print("Publication counts:", Counter(
    r["publishTime"] for r in forecasts
))
forecast_deltas = Counter(
    (b - a).total_seconds() / 60
    for a, b in zip(sorted(forecast_starts), sorted(forecast_starts)[1:])
)
print("Forecast interval gaps (minutes):", forecast_deltas)

print("\nOVERLAP")
print("Exact matching startTimes:", len(actual_starts & forecast_starts))
print("Forecast times without exact actual match:",
      len(forecast_starts - actual_starts))

print("\nSample actuals:")
for row in actuals[:3]:
    print(row)

print("\nSample forecasts:")
for row in forecasts[:3]:
    print(row)
