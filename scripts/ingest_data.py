"""Download reproducible FUELHH actuals and WINDFOR publication vintages."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import sys
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


API_BASE = "https://data.elexon.co.uk/bmrs/api/v1"
UTC = timezone.utc


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp has no timezone: {value!r}")
    return parsed.astimezone(UTC)


def format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def fetch_rows(session: requests.Session, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
    response = session.get(f"{API_BASE}/{path}", params=params, timeout=90)
    response.raise_for_status()
    payload = response.json()
    rows = payload if isinstance(payload, list) else payload.get("data")
    if not isinstance(rows, list):
        raise ValueError(f"Unexpected {path} response shape: {type(payload).__name__}")
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Unexpected non-object row in {path} response")
    return rows


def _generation(row: dict[str, Any]) -> float:
    value = row.get("generation")
    if value is None:
        raise ValueError("API row is missing generation")
    return float(value)


def normalize_forecasts(rows: list[dict[str, Any]], start: datetime, end: datetime) -> tuple[list[dict[str, Any]], int]:
    selected: dict[tuple[datetime, datetime], dict[str, Any]] = {}
    duplicates = 0
    for row in rows:
        published = parse_timestamp(row["publishTime"])
        delivery = parse_timestamp(row["startTime"])
        if not start <= published < end:
            raise ValueError(f"Forecast publication {row['publishTime']} is outside requested window")
        record = {
            "start_time": format_timestamp(delivery),
            "publish_time": format_timestamp(published),
            "generation_mw": _generation(row),
        }
        key = (published, delivery)
        prior = selected.get(key)
        if prior is not None:
            if prior["generation_mw"] != record["generation_mw"]:
                raise ValueError(f"Conflicting forecast duplicate for {key}")
            duplicates += 1
            continue
        selected[key] = record
    return sorted(selected.values(), key=lambda row: (row["publish_time"], row["start_time"])), duplicates


def normalize_actuals(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    selected: dict[datetime, tuple[datetime, dict[str, Any]]] = {}
    duplicates = 0
    for row in rows:
        delivery = parse_timestamp(row["startTime"])
        published = parse_timestamp(row["publishTime"])
        record = {
            "start_time": format_timestamp(delivery),
            "publish_time": format_timestamp(published),
            "settlement_date": row["settlementDate"],
            "settlement_period": int(row["settlementPeriod"]),
            "generation_mw": _generation(row),
        }
        prior = selected.get(delivery)
        if prior is None or published > prior[0]:
            if prior is not None:
                duplicates += 1
            selected[delivery] = (published, record)
        elif published == prior[0]:
            if prior[1]["generation_mw"] != record["generation_mw"]:
                raise ValueError(f"Conflicting actual duplicate for {record['start_time']}")
            duplicates += 1
        else:
            duplicates += 1
    records = [record for _, record in sorted(selected.values(), key=lambda pair: pair[1]["start_time"])]
    return records, duplicates


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_metadata(
    actuals: list[dict[str, Any]],
    forecasts: list[dict[str, Any]],
    actual_hash: str,
    forecast_hash: str,
    requested_start: datetime,
    requested_end: datetime,
    actual_settlement_start: date,
    actual_settlement_end: date,
    retrieved_at: datetime,
    duplicate_counts: dict[str, int],
    raw_counts: dict[str, int],
) -> dict[str, Any]:
    starts_by_vintage: dict[str, list[datetime]] = {}
    for row in forecasts:
        starts_by_vintage.setdefault(row["publish_time"], []).append(parse_timestamp(row["start_time"]))
    records_per_vintage = Counter(len(rows) for rows in starts_by_vintage.values())
    all_delivery = sorted({parse_timestamp(row["start_time"]) for row in forecasts})
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(all_delivery, all_delivery[1:])]
    interval = statistics.mode(gaps) if gaps else None
    return {
        "retrieved_at": format_timestamp(retrieved_at),
        "source": API_BASE,
        "query": {
            "forecast_publication_from": format_timestamp(requested_start),
            "forecast_publication_to_exclusive": format_timestamp(requested_end),
            "actual_settlement_date_from": actual_settlement_start.isoformat(),
            "actual_settlement_date_to": actual_settlement_end.isoformat(),
            "actual_fuel_type": "WIND",
        },
        "interpretation": "January window refers to forecast publication/decision dates; delivery dates may extend into adjacent months.",
        "records": {
            "actuals_raw": raw_counts["actuals"],
            "actuals_normalized": len(actuals),
            "actuals_duplicate_rows_removed": duplicate_counts["actuals"],
            "forecasts_raw": raw_counts["forecasts"],
            "forecasts_normalized": len(forecasts),
            "forecasts_duplicate_rows_removed": duplicate_counts["forecasts"],
            "forecast_vintages": len(starts_by_vintage),
            "forecast_records_per_vintage": {str(count): number for count, number in sorted(records_per_vintage.items())},
        },
        "coverage": {
            "actual_start_time_from": actuals[0]["start_time"] if actuals else None,
            "actual_start_time_to": actuals[-1]["start_time"] if actuals else None,
            "actual_settlement_dates": sorted({row["settlement_date"] for row in actuals}),
            "forecast_publish_time_from": min(starts_by_vintage) if starts_by_vintage else None,
            "forecast_publish_time_to": max(starts_by_vintage) if starts_by_vintage else None,
            "forecast_delivery_start_from": format_timestamp(all_delivery[0]) if all_delivery else None,
            "forecast_delivery_start_to": format_timestamp(all_delivery[-1]) if all_delivery else None,
            "forecast_delivery_interval_minutes_mode": interval,
        },
        "checksums_sha256": {"actuals.csv": actual_hash, "forecasts.csv": forecast_hash},
    }


def ingest(start_date: date, end_date_exclusive: date, output_dir: Path, session: requests.Session | None = None) -> dict[str, Any]:
    if end_date_exclusive <= start_date:
        raise ValueError("end date must be later than start date")
    session = session or requests.Session()
    start = datetime.combine(start_date, time.min, tzinfo=UTC)
    end = datetime.combine(end_date_exclusive, time.min, tzinfo=UTC)
    # Elexon filters are inclusive; use the preceding whole second for an exclusive end.
    forecast_params = {
        "publishDateTimeFrom": format_timestamp(start),
        "publishDateTimeTo": format_timestamp(end - timedelta(seconds=1)),
    }
    forecast_raw = fetch_rows(session, "datasets/WINDFOR/stream", forecast_params)
    forecasts, forecast_duplicates = normalize_forecasts(forecast_raw, start, end)
    if not forecasts:
        raise RuntimeError("No forecast vintages returned for the requested publication window")

    # Fetch every actual settlement date touched by the forecast delivery coverage.
    delivery_dates = [parse_timestamp(row["start_time"]).date() for row in forecasts]
    actual_start_date, actual_end_date = min(delivery_dates), max(delivery_dates)
    actual_params = {
        "fuelType": "WIND",
        "settlementDateFrom": actual_start_date.isoformat(),
        "settlementDateTo": actual_end_date.isoformat(),
    }
    actual_raw = fetch_rows(session, "datasets/FUELHH/stream", actual_params)
    actuals, actual_duplicates = normalize_actuals(actual_raw)

    actual_hash = write_csv(output_dir / "actuals.csv", actuals, ["start_time", "publish_time", "settlement_date", "settlement_period", "generation_mw"])
    forecast_hash = write_csv(output_dir / "forecasts.csv", forecasts, ["start_time", "publish_time", "generation_mw"])
    metadata = build_metadata(
        actuals, forecasts, actual_hash, forecast_hash, start, end, actual_start_date, actual_end_date,
        datetime.now(UTC), {"actuals": actual_duplicates, "forecasts": forecast_duplicates},
        {"actuals": len(actual_raw), "forecasts": len(forecast_raw)},
    )
    metadata_path = output_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2026, 1, 1), help="Inclusive forecast publication date (UTC)")
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2026, 2, 1), help="Exclusive forecast publication date (UTC)")
    parser.add_argument("--output-dir", type=Path, default=Path("data"))
    args = parser.parse_args(argv)
    try:
        print(json.dumps(ingest(args.start_date, args.end_date, args.output_dir), indent=2))
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        print(f"Ingestion failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
