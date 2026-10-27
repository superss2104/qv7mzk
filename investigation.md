# Elexon API investigation

Checked against the live Insights API on 2026-10-27. The current scripts are probes, not production ingestion: `from`/`to` on the WINDFOR stream returned the current October 2026 vintage when asked for a January delivery time, and the alignment script's single-vintage metric is not a backtest.

## Query semantics verified

- `datasets/FUELHH/stream` with `fuelType=WIND` and `settlementDateFrom`/`settlementDateTo` is the actual wind source. One date returned 48 half-hour rows; January 1–31 returned 1,488 rows and 1,488 unique `startTime` values. In this query, settlement-date filters select delivery dates as intended.
- `datasets/WINDFOR/stream` with `publishDateTimeFrom`/`publishDateTimeTo` selects forecast publication time. Jan 15 returned eight publications and 584 rows (73 deliveries per publication). Jan 1–Feb 1 returned 18,104 rows, 248 publications, and 73 records in each publication. The union of 31 daily queries exactly matched the monthly response as a multiset; neither responses showed pagination headers or a pagination envelope.
- The WINDFOR stream's `from`/`to` parameters select delivery time but returned the latest, October 2026 vintage for a historical January delivery-time query. Do not use it to reconstruct historical forecasts.
- `/forecast/generation/wind/history?publishTime=...` returned 73 rows with the exact `publishTime` for an exact 07:30Z query. Nearby non-exact query values returned an earlier publication (07:29:59Z -> 04:30Z) or the previous publication as expected from a coarse/nearest lookup (with the observed 07:30:01Z query returning 07:30Z). Use the WINDFOR stream's returned publication timestamps for collection and validate them against the requested window; do not apply an hour correction.
- A January publication window contains delivery starts from 2025-12-31T21:00Z through 2026-02-02T21:00Z. Delivery timestamps are hourly in the forecast data; actuals are half-hourly. These are different resolutions and should not be equated row-for-row. The exploratory 1-hour mean of each pair of half-hours is used as an explicit comparison assumption: it keeps each full UTC hour only when both expected half-hour actual records exist. The tested evaluation service reproduced the handoff's Jan 15 07:30Z single-vintage diagnostic over 24 intervals: MAE 902.7917 MW, RMSE 1062.0486 MW, bias +817.4167 MW. This remains a one-day diagnostic, not a January-wide backtest or a procurement-cost estimate.

## Ingestion decisions

`scripts/ingest_data.py` treats the configured window as UTC forecast publication/decision dates, inclusive at the start and exclusive at the end. It queries FUELHH for every settlement date touched by the returned forecast deliveries, keeps the original actual and forecast `publishTime`, normalizes timestamps to UTC, sorts and deduplicates deterministically, and fails on conflicting duplicate values. It writes normalized CSV snapshots and metadata with retrieval time, query bounds, counts, coverage, interval resolution, and SHA-256 checksums.

The default publication window is January 2026 (`2026-01-01` through but excluding `2026-02-01`). January decisions can produce February delivery forecasts; the script derives actual settlement-date coverage from the actual forecast delivery timestamps rather than assuming both date windows are January.

## Sources

- [FUELHH stream documentation](https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/FUELHH/stream)
- [WINDFOR stream documentation](https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/WINDFOR/stream)
- [Historical wind forecast documentation](https://bmrs.elexon.co.uk/api-documentation/endpoint/forecast/generation/wind/history)
