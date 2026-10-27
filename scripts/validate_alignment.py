
import requests
import pandas as pd

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

# Actuals for the delivery date January 15.
actual_rows = fetch("datasets/FUELHH/stream", {
    "fuelType": "WIND",
    "settlementDateFrom": "2026-01-15",
    "settlementDateTo": "2026-01-15",
})

# Narrow publication window around the chosen vintage.
forecast_rows = fetch("datasets/WINDFOR/stream", {
    "publishDateTimeFrom": "2026-01-15T07:00:00Z",
    "publishDateTimeTo": "2026-01-15T08:00:00Z",
})

actuals = pd.DataFrame(actual_rows)
forecasts = pd.DataFrame(forecast_rows)

if actuals.empty or forecasts.empty:
    raise RuntimeError("Actuals or forecasts are empty.")

actuals["startTime"] = pd.to_datetime(
    actuals["startTime"], utc=True
)
forecasts["startTime"] = pd.to_datetime(
    forecasts["startTime"], utc=True
)
forecasts["publishTime"] = pd.to_datetime(
    forecasts["publishTime"], utc=True
)

print("Forecast publication times:")
print(sorted(forecasts["publishTime"].unique()))

# Actual generation is sampled every 30 minutes.
# Average the two half-hour observations into hourly values.
actuals = actuals.sort_values("startTime").set_index("startTime")

hourly_actuals = actuals["generation"].resample(
    "1h", label="left", closed="left"
).mean()

hourly_actuals.name = "actual_mw"

# Retain one forecast vintage only.
vintages = forecasts["publishTime"].unique()
if len(vintages) != 1:
    raise RuntimeError(
        f"Expected one vintage, found {len(vintages)}. "
        "Narrow the publication filter."
    )

forecasts = forecasts[
    ["startTime", "publishTime", "generation"]
].rename(columns={"generation": "forecast_mw"})

forecasts = forecasts.set_index("startTime")

# Align forecasts to hourly actual averages.
matched = forecasts.join(hourly_actuals, how="inner")
matched = matched.dropna(subset=["forecast_mw", "actual_mw"])

matched["error_mw"] = (
    matched["forecast_mw"] - matched["actual_mw"]
)
matched["absolute_error_mw"] = matched["error_mw"].abs()

print("\nMatched rows:", len(matched))
print(matched.head(10).to_string())

if not matched.empty:
    print("\nMAE (MW):", matched["absolute_error_mw"].mean())
    print("Bias (MW):", matched["error_mw"].mean())

matched.to_csv("data/alignment_check.csv")
print("\nSaved data/alignment_check.csv")
