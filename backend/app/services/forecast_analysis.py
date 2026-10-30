"""Retrospective analyses of forecast vintages for the wind forecast desk."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from math import sqrt
from typing import Iterable, Mapping

from backend.app.services.evaluation import hourly_actual_means
from backend.app.services.forecast_selection import ForecastPoint, SelectionMode, select_forecast


UTC = timezone.utc
LEAD_BUCKETS = (
    (0.0, 1.0, "0–1 h"),
    (1.0, 3.0, "1–3 h"),
    (3.0, 6.0, "3–6 h"),
    (6.0, 12.0, "6–12 h"),
    (12.0, 24.0, "12–24 h"),
    (24.0, 36.0, "24–36 h"),
    (36.0, 48.0, "36–48 h"),
    (48.0, 72.0, "48–72 h"),
    (72.0, float("inf"), "72+ h"),
)
MIN_QUANTILE_SAMPLE = 30


def _metrics(errors: list[float]) -> dict[str, float | int | None]:
    count = len(errors)
    return {
        "sample_count": count,
        "mae_mw": sum(abs(error) for error in errors) / count if count else None,
        "rmse_mw": sqrt(sum(error * error for error in errors) / count) if count else None,
        "bias_mw": sum(errors) / count if count else None,
    }


def _quantile(sorted_values: list[float], probability: float) -> float:
    """Linear-interpolated empirical quantile (same convention as NumPy default)."""
    position = (len(sorted_values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def analyze_forecast_window(
    forecasts: Iterable[ForecastPoint | Mapping[str, object]],
    actuals: Iterable[Mapping[str, object]],
    *,
    delivery_from: datetime,
    delivery_to: datetime,
    mode: SelectionMode,
    cutoff: datetime | None = None,
    horizon: timedelta | None = None,
) -> dict[str, object]:
    """Build lead-time, revision, and January error-distribution analyses.

    Lead-time and revision results are filtered to the requested delivery
    window. The uncertainty distribution uses January 2026 decision dates and
    the active decision rule, giving the selected daily forecast an empirical
    historical error range. Only forecasts published by delivery are eligible
    for retrospective comparisons.
    """
    if delivery_from.tzinfo is None or delivery_to.tzinfo is None:
        raise ValueError("delivery window bounds must include a timezone")
    start = delivery_from.astimezone(UTC)
    end = delivery_to.astimezone(UTC)
    if end <= start or start.minute or start.second or start.microsecond or end.minute or end.second or end.microsecond:
        raise ValueError("delivery window must be a positive whole-hour UTC window")
    if mode == "fixed_cutoff" and (cutoff is None or cutoff.tzinfo is None):
        raise ValueError("fixed_cutoff analysis requires a timezone-aware cutoff")
    if mode == "fixed_horizon" and horizon is None:
        raise ValueError("fixed_horizon analysis requires a horizon")

    grouped: dict[datetime, list[ForecastPoint]] = defaultdict(list)
    unique: dict[tuple[datetime, datetime], ForecastPoint] = {}
    for row in forecasts:
        point = row if isinstance(row, ForecastPoint) else ForecastPoint.from_row(row)
        key = (point.delivery_start, point.published_at)
        previous = unique.get(key)
        if previous and previous.generation_mw != point.generation_mw:
            raise ValueError(f"Conflicting forecast duplicate at {point.published_at.isoformat()}")
        unique[key] = point
    for point in unique.values():
        grouped[point.delivery_start].append(point)
    actual_by_hour = hourly_actual_means(actuals)

    bucket_errors: list[list[float]] = [[] for _ in LEAD_BUCKETS]
    candidate_forecasts = 0
    excluded_incomplete_actual_count = 0
    excluded_post_delivery_count = 0
    observation_count = 0
    for delivery, points in grouped.items():
        if not start <= delivery < end:
            continue
        candidate_forecasts += len(points)
        actual = actual_by_hour.get(delivery)
        if actual is None:
            excluded_incomplete_actual_count += len(points)
            continue
        for point in points:
            if point.published_at > delivery:
                excluded_post_delivery_count += 1
                continue
            observation_count += 1
            lead = (delivery - point.published_at).total_seconds() / 3600
            for index, (lower, upper, _label) in enumerate(LEAD_BUCKETS):
                if lower <= lead < upper:
                    bucket_errors[index].append(point.generation_mw - actual)
                    break

    buckets = [
        {
            "label": label,
            "lower_hours": lower,
            "upper_hours": upper if upper != float("inf") else None,
            **_metrics(errors),
        }
        for (lower, upper, label), errors in zip(LEAD_BUCKETS, bucket_errors)
        if errors
    ]
    bucketed_observations = sum(len(errors) for errors in bucket_errors)
    unbucketed_observations = observation_count - bucketed_observations

    revision_errors: list[tuple[datetime, float, float, ForecastPoint, ForecastPoint, float]] = []
    for delivery in (start + timedelta(hours=offset) for offset in range(int((end - start).total_seconds() // 3600))):
        actual = actual_by_hour.get(delivery)
        if actual is None:
            continue
        points = grouped.get(delivery, [])
        if mode == "fixed_cutoff":
            decision = select_forecast(points, delivery, mode=mode, cutoff=cutoff)
        else:
            decision = select_forecast(points, delivery, mode=mode, horizon=horizon)
        if decision.forecast is None:
            continue
        later = [point for point in points if decision.forecast.published_at < point.published_at <= delivery]
        if not later:
            continue
        revised = max(later, key=lambda point: point.published_at)
        revision_errors.append((
            delivery,
            actual,
            decision.forecast.generation_mw,
            decision.forecast,
            revised,
            revised.generation_mw,
        ))

    decision_errors: list[float] = []
    revised_errors: list[float] = []
    revision_rows: list[dict[str, object]] = []
    for delivery, actual, decision_value, decision_point, revised_point, revised_value in revision_errors:
        decision_error = decision_value - actual
        revised_error = revised_value - actual
        decision_errors.append(decision_error)
        revised_errors.append(revised_error)
        revision_rows.append({
            "delivery_start": delivery.isoformat().replace("+00:00", "Z"),
            "actual_mw": actual,
            "decision_forecast_mw": decision_value,
            "decision_publish_time": decision_point.published_at.isoformat().replace("+00:00", "Z"),
            "decision_lead_hours": (delivery - decision_point.published_at).total_seconds() / 3600,
            "decision_error_mw": decision_error,
            "later_forecast_mw": revised_value,
            "later_publish_time": revised_point.published_at.isoformat().replace("+00:00", "Z"),
            "later_lead_hours": (delivery - revised_point.published_at).total_seconds() / 3600,
            "later_error_mw": revised_error,
        })

    # Keep the hindsight distribution tied to the same January decision rule.
    all_points: dict[datetime, list[ForecastPoint]] = defaultdict(list)
    for point in unique.values():
        all_points[point.delivery_start].append(point)
    decision_time_errors: list[float] = []
    if mode == "fixed_cutoff":
        if cutoff is None or cutoff.tzinfo is None:
            raise ValueError("fixed_cutoff analysis requires a timezone-aware cutoff")
        cutoff_clock = cutoff.astimezone(UTC).timetz().replace(tzinfo=None)
    else:
        cutoff_clock = None
    for day in range(1, 32):
        decision_date = date(2026, 1, day)
        delivery_date = decision_date + timedelta(days=1)
        daily_cutoff = datetime.combine(decision_date, cutoff_clock, tzinfo=UTC) if cutoff_clock is not None else None
        for hour in range(24):
            delivery = datetime.combine(delivery_date, time.min, tzinfo=UTC) + timedelta(hours=hour)
            actual = actual_by_hour.get(delivery)
            if actual is None:
                continue
            points = all_points.get(delivery, [])
            if mode == "fixed_cutoff":
                selection = select_forecast(points, delivery, mode=mode, cutoff=daily_cutoff)
            else:
                selection = select_forecast(points, delivery, mode=mode, horizon=horizon)
            if selection.forecast is not None:
                decision_time_errors.append(selection.forecast.generation_mw - actual)

    sorted_errors = sorted(decision_time_errors)
    enough_data = len(sorted_errors) >= MIN_QUANTILE_SAMPLE
    quantiles = {
        "p10_mw": _quantile(sorted_errors, 0.10) if enough_data else None,
        "p50_mw": _quantile(sorted_errors, 0.50) if enough_data else None,
        "p90_mw": _quantile(sorted_errors, 0.90) if enough_data else None,
    }
    bands: list[dict[str, object]] = []
    if enough_data:
        for delivery in (start + timedelta(hours=offset) for offset in range(int((end - start).total_seconds() // 3600))):
            points = grouped.get(delivery, [])
            if mode == "fixed_cutoff":
                decision = select_forecast(points, delivery, mode=mode, cutoff=cutoff)
            else:
                decision = select_forecast(points, delivery, mode=mode, horizon=horizon)
            if decision.forecast is None:
                continue
            value = decision.forecast.generation_mw
            bands.append({
                "delivery_start": delivery.isoformat().replace("+00:00", "Z"),
                "forecast_mw": value,
                "p10_mw": value + quantiles["p10_mw"],
                "p50_mw": value + quantiles["p50_mw"],
                "p90_mw": value + quantiles["p90_mw"],
            })

    n_decision = len(decision_errors)
    n_later = len(revised_errors)
    decision_metrics = _metrics(decision_errors)
    later_metrics = _metrics(revised_errors)
    return {
        "lead_time": {
            "buckets": buckets,
            "candidate_forecasts": candidate_forecasts,
            "observation_count": observation_count,
            "bucketed_observations": bucketed_observations,
            "unbucketed_observations": unbucketed_observations,
            "excluded_post_delivery_count": excluded_post_delivery_count,
            "excluded_incomplete_actual_count": excluded_incomplete_actual_count,
        },
        "revision": {
            "decision_time": decision_metrics,
            "later_vintage": later_metrics,
            "mae_improvement_mw": (decision_metrics["mae_mw"] - later_metrics["mae_mw"]) if n_decision and n_later else None,
            "rmse_improvement_mw": (decision_metrics["rmse_mw"] - later_metrics["rmse_mw"]) if n_decision and n_later else None,
            "matched_intervals": len(revision_rows),
            "intervals": revision_rows,
        },
        "error_distribution": {
            **quantiles,
            "sample_count": len(sorted_errors),
            "minimum_sample_count": MIN_QUANTILE_SAMPLE,
            "observation_window": "January 1–31, 2026 decision dates",
            "selection_rule": mode,
            "cutoff_time_utc": cutoff_clock.strftime("%H:%M") if cutoff_clock is not None else None,
            "bands": bands,
        },
    }
