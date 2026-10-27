# GridBricks Wind Forecast Desk

A small decision-support dashboard for comparing Elexon wind-generation forecasts with realized output. It preserves forecast publication timestamps so the selected forecast can be restricted to information available at a configured decision cutoff or lead time.

## Current status

The repository contains a normalized January 2026 publication-window snapshot and a working local dashboard/API. The default page shows a **single-day diagnostic for Jan 15, 2026 at a 07:30 UTC cutoff**. This is a representative sample, not a January-wide backtest. No procurement cost or savings are estimated because demand, procurement positions, and imbalance prices are not included.

## Project structure

```text
backend/app/main.py                    FastAPI API and static frontend serving
backend/app/services/forecast_selection.py
                                        Fixed-cutoff and fixed-horizon selection
backend/app/services/evaluation.py     Coverage and forecast error metrics
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
- `GET /api/evaluation` — selected forecast series, actual series, errors, and summary metrics.

The default evaluation route is the Jan 15 diagnostic at a 07:30 UTC fixed cutoff. Custom requests can set `delivery_from`, `delivery_to`, and either `mode=fixed_cutoff&cutoff=<UTC timestamp>` or `mode=fixed_horizon&horizon_hours=24`.

## Method and limitations

- A forecast is eligible only when `publish_time <= cutoff`; a later publication cannot alter a decision-time result.
- WINDFOR forecast deliveries are hourly while FUELHH actuals are half-hourly. The current comparison uses the arithmetic mean of the two expected half-hour actuals for each complete UTC hour. Incomplete actual hours remain missing; they are not filled with zero.
- The default view contains 24 matched hourly intervals from one day. It is not a full January decision-date backtest.
- Positive signed error (`forecast - actual`) is overforecasting; negative signed error is underforecasting. Which direction costs more depends on market prices and the buyer's position.
- Dashboard timestamps are shown in `Europe/London`; timestamps in the saved data are UTC.
- The snapshot covers Jan 2026 publication vintages, with delivery starts from Dec 31 through Feb 2. The exact retrieval timestamp and checksums are in `data/metadata.json`.

## Tests

```bash
python -m unittest discover -s tests -v
```

The suite covers query-row normalization, duplicate handling, publication-time eligibility, fixed-horizon selection, missing coverage, metric calculations, and a manually checked Elexon data example.

## Deployment

Target: one-container deployment using the included Dockerfile. **Public deployment URL: pending.**

## AI-tool usage

OpenAI Codex assisted with repository inspection, Elexon query investigation, implementation, and test writing. API query semantics and the saved sample were checked against live Elexon responses.
