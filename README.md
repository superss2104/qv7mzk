# GridBricks Wind Forecast Desk

A small decision-support dashboard for comparing Elexon wind-generation forecasts with realized output. It preserves forecast publication timestamps so the selected forecast can be restricted to information available at a configured decision cutoff or lead time.

## Current status

The repository contains a normalized January 2026 publication-window snapshot and a working local dashboard/API. It summarizes **Jan 1–31 decision dates** at the configurable 07:30 UTC previous-day cutoff, covering delivery dates Jan 2–Feb 1, and opens with a **Jan 15, 2026 daily diagnostic**. The brief does not specify a cutoff time; 07:30 UTC is the documented default assumption. No procurement cost or savings are estimated because demand, procurement positions, and imbalance prices are not included.

## Project structure

```text
backend/app/main.py                    FastAPI API and static frontend serving
backend/app/services/forecast_selection.py
                                        Fixed-cutoff and fixed-horizon selection
backend/app/services/evaluation.py     Daily and decision-period error metrics
backend/app/services/forecast_analysis.py
                                        Lead-time, revision, and empirical error analysis
frontend/src/App.tsx                   React overview and interval explorer
scripts/ingest_data.py                 Reproducible Elexon data ingestion
data/actuals.csv                       Normalized FUELHH actuals
data/forecasts.csv                     Normalized WINDFOR vintage rows
data/metadata.json                     Retrieval window, counts, coverage, checksums
tests/                                 Ingestion, selection, and evaluation tests
investigation.md                       API query semantics and data findings
```

## Run locally

Use Python 3.11+ and Node.js 20+.

### API

From the repository root, create and activate a virtual environment, then run:

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn backend.app.main:app --reload --port 8000
```

The API is available at `http://localhost:8000`; interactive API docs are at `http://localhost:8000/docs`.

### React app

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. Vite proxies `/api` calls to the local FastAPI server.

### Docker

From the repository root:

```bash
docker compose up --build
```

Open `http://localhost:8000`. The container serves the built React app and API from one origin.

## Refresh the data snapshot

The default ingestion window covers forecast publication dates from Jan 1 through Jan 31, 2026 (exclusive end: Feb 1). This means January is interpreted as the decision/publication window; delivery timestamps extend into February.

```bash
python scripts/ingest_data.py
```

Choose a different half-open publication window with `--start-date`, `--end-date`, and optionally `--output-dir`. The pipeline queries FUELHH settlement dates for every date touched by the returned forecast deliveries. It records both actual and forecast publication times, normalizes to UTC, writes SHA-256 checksums, removes identical duplicates deterministically, and fails on conflicting duplicate values.

## API routes

- `GET /api/health` — loaded snapshot status and row counts.
- `GET /api/metadata` — retrieval window, coverage, counts, and checksums.
- `GET /api/january-evaluation` — aggregated metrics and daily results for January decision dates; accepts `cutoff_time_utc` (default `07:30`).
- `GET /api/evaluation` — selected forecast series, actual series, errors, and summary metrics.
- `GET /api/forecast-analytics` — lead-time buckets, matched-interval revision comparison, and January empirical error quantiles for the selected evaluation rule.

The default daily evaluation is for Jan 15 delivery at a 07:30 UTC cutoff on Jan 14. The January summary applies one shared cutoff to all 24 delivery hours for each decision day from Jan 1 through Jan 31; deliveries run Jan 2 through Feb 1. Custom daily requests can set `delivery_from`, `delivery_to`, and either `mode=fixed_cutoff&cutoff=<UTC timestamp>` or `mode=fixed_horizon&horizon_hours=24`.

## Method and limitations

- A forecast is eligible only when `publish_time <= cutoff`; a later publication cannot alter a decision-time result.
- WINDFOR values are hourly and FUELHH reports average MW for each half-hour. Because the two half-hours have equal duration, the arithmetic mean of a complete pair gives the UTC-hour average MW. Incomplete pairs remain missing; they are not filled with zero. See Elexon’s [generation by fuel type data notes](https://bmrs.elexon.co.uk/generation-by-fuel-type).
- The January summary covers 31 decision days and up to 744 hourly intervals; the selected-day view shows that date's 24 hourly rows.
- January MAE, RMSE, and bias are calculated from all matched hourly observations together, not by averaging daily metrics. Expected, forecast-available, actual-available, and matched counts are tracked separately.
- Elexon wind outturn reflects operational-metered generation available to the system operator; embedded or unmetered wind may not be represented in the source total.
- Positive signed error (`forecast - actual`) is overforecasting; negative signed error is underforecasting. Which direction costs more depends on market prices and the buyer's position.
- Lead-time accuracy counts every matched forecast-vintage/delivery observation published no later than delivery; only non-empty buckets are returned.
- Revision impact compares the selected decision-time forecast with the latest vintage published by delivery, using only intervals matched in both. It is retrospective and is not a financial-savings estimate.
- P10/P50/P90 use linearly interpolated empirical quantiles of January decision-time errors. They are shown only with at least 30 matched observations and are not guaranteed prediction intervals.
- Dashboard timestamps are shown in `Europe/London`; timestamps in the saved data are UTC.
- The snapshot covers Jan 2026 publication vintages, with delivery starts from Dec 31 through Feb 2. The exact retrieval timestamp and checksums are in `data/metadata.json`.

## Tests

```bash
python -m unittest discover -s tests -v
```

The suite covers query-row normalization, duplicate handling, publication-time eligibility, fixed-horizon selection, missing coverage, daily and decision-period metric calculations, and a manually checked Elexon data example.

## Deployment

Target: one-container deployment using the included Dockerfile. **Public deployment URL: pending.**

## AI-tool usage

OpenAI Codex assisted with repository inspection, Elexon query investigation, implementation, and test writing. API query semantics and the saved sample were checked against live Elexon responses.
