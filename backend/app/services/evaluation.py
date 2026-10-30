"""Evaluate selected hourly forecast vintages against half-hourly FUELHH data."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import sqrt
from typing import Iterable, Mapping

from backend.app.services.forecast_selection import ForecastPoint, SelectionMode, select_forecast


UTC = timezone.utc


def _utc(value: datetime | str, field: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class EvaluationMetrics:
    expected_intervals: int
    forecast_available: int
    actual_available: int
    matched_intervals: int
    forecast_coverage: float
    actual_coverage: float
    matched_coverage: float
    mae_mw: float | None
    rmse_mw: float | None
    bias_mw: float | None


@dataclass(frozen=True)
class EvaluationResult:
    metrics: EvaluationMetrics
    intervals: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return {"metrics": asdict(self.metrics), "intervals": self.intervals}


def hourly_actual_means(rows: Iterable[Mapping[str, object]]) -> dict[datetime, float]:
    """Average each complete UTC hour's two expected FUELHH half-hour records.

    Partial hours stay missing. This does not fill absent actuals with zero.
    """
    groups: dict[datetime, dict[datetime, float]] = defaultdict(dict)
    for row in rows:
        start = row.get("start_time", row.get("startTime"))
        generation = row.get("generation_mw", row.get("generation"))
        if start is None or generation is None:
            raise ValueError("Actual row must have start time and generation")
        instant = _utc(start, "actual start")
        hour = instant.replace(minute=0, second=0, microsecond=0)
        groups[hour][instant] = float(generation)

    result: dict[datetime, float] = {}
    for hour, observations in groups.items():
        expected = {hour, hour + timedelta(minutes=30)}
        if set(observations) == expected:
            result[hour] = (observations[hour] + observations[hour + timedelta(minutes=30)]) / 2
    return result


def evaluate(
    forecasts: Iterable[ForecastPoint | Mapping[str, object]],
    actuals: Iterable[Mapping[str, object]],
    *,
    delivery_from: datetime | str,
    delivery_to: datetime | str,
    mode: SelectionMode,
    cutoff: datetime | str | None = None,
    horizon: timedelta | None = None,
) -> EvaluationResult:
    """Score a half-open UTC delivery window, retaining missingness in coverage."""
    start, end = _utc(delivery_from, "delivery_from"), _utc(delivery_to, "delivery_to")
    if end <= start:
        raise ValueError("delivery_to must be later than delivery_from")
    if start.minute or start.second or start.microsecond or end.minute or end.second or end.microsecond:
        raise ValueError("delivery window bounds must be aligned to UTC hours")
    duration = (end - start).total_seconds()
    if duration % 3600:
        raise ValueError("delivery window must contain whole UTC hours")

    forecasts_by_delivery: dict[datetime, list[ForecastPoint]] = defaultdict(list)
    for row in forecasts:
        point = row if isinstance(row, ForecastPoint) else ForecastPoint.from_row(row)
        forecasts_by_delivery[point.delivery_start].append(point)
    actual_by_delivery = hourly_actual_means(actuals)

    expected = int(duration // 3600)
    interval_rows: list[dict[str, object]] = []
    errors: list[float] = []
    forecast_count = 0
    actual_count = 0
    matched_count = 0
    for offset in range(expected):
        delivery = start + timedelta(hours=offset)
        if mode == "fixed_horizon":
            selection = select_forecast(
                forecasts_by_delivery.get(delivery, []), delivery,
                mode=mode, horizon=horizon,
            )
        else:
            selection = select_forecast(
                forecasts_by_delivery.get(delivery, []), delivery,
                mode=mode, cutoff=cutoff,
            )
        forecast = selection.forecast
        actual = actual_by_delivery.get(delivery)
        if forecast is not None:
            forecast_count += 1
        if actual is not None:
            actual_count += 1
        error = forecast.generation_mw - actual if forecast is not None and actual is not None else None
        if error is not None:
            matched_count += 1
            errors.append(error)
        interval_rows.append({
            "delivery_start": _iso(delivery),
            "cutoff": _iso(selection.cutoff),
            "publish_time": _iso(forecast.published_at) if forecast else None,
            "forecast_mw": forecast.generation_mw if forecast else None,
            "actual_mw": actual,
            "error_mw": error,
        })

    mae = sum(abs(error) for error in errors) / matched_count if matched_count else None
    rmse = sqrt(sum(error * error for error in errors) / matched_count) if matched_count else None
    bias = sum(errors) / matched_count if matched_count else None
    metrics = EvaluationMetrics(
        expected_intervals=expected,
        forecast_available=forecast_count,
        actual_available=actual_count,
        matched_intervals=matched_count,
        forecast_coverage=forecast_count / expected,
        actual_coverage=actual_count / expected,
        matched_coverage=matched_count / expected,
        mae_mw=mae,
        rmse_mw=rmse,
        bias_mw=bias,
    )
    return EvaluationResult(metrics, interval_rows)


def evaluate_decision_period(
    forecasts: Iterable[ForecastPoint | Mapping[str, object]],
    actuals: Iterable[Mapping[str, object]],
    *,
    decision_from: date,
    decision_to: date,
    cutoff_time_utc: time,
) -> dict[str, object]:
    """Aggregate day-ahead metrics over a half-open range of decision dates.

    Each decision date uses one fixed UTC cutoff for all 24 delivery hours on
    the following date. The end date is exclusive.
    """
    if decision_to <= decision_from:
        raise ValueError("decision_to must be later than decision_from")
    if cutoff_time_utc.tzinfo is not None:
        raise ValueError("cutoff_time_utc must be a UTC wall-clock time without a timezone")

    forecast_rows = list(forecasts)
    actual_rows = list(actuals)
    daily: list[dict[str, object]] = []
    errors: list[float] = []
    expected = forecast_count = actual_count = matched_count = 0
    decision_days = (decision_to - decision_from).days

    for offset in range(decision_days):
        decision_date = decision_from + timedelta(days=offset)
        delivery_date = decision_date + timedelta(days=1)
        cutoff = datetime.combine(decision_date, cutoff_time_utc, tzinfo=UTC)
        delivery_start = datetime.combine(delivery_date, time.min, tzinfo=UTC)
        delivery_end = delivery_start + timedelta(days=1)
        result = evaluate(
            forecast_rows,
            actual_rows,
            delivery_from=delivery_start,
            delivery_to=delivery_end,
            mode="fixed_cutoff",
            cutoff=cutoff,
        )
        metrics = result.metrics
        expected += metrics.expected_intervals
        forecast_count += metrics.forecast_available
        actual_count += metrics.actual_available
        matched_count += metrics.matched_intervals
        errors.extend(
            float(row["error_mw"])
            for row in result.intervals
            if row["error_mw"] is not None
        )
        daily.append({
            "decision_date": decision_date.isoformat(),
            "delivery_date": delivery_date.isoformat(),
            "cutoff": _iso(cutoff),
            "metrics": asdict(metrics),
        })

    period_metrics = EvaluationMetrics(
        expected_intervals=expected,
        forecast_available=forecast_count,
        actual_available=actual_count,
        matched_intervals=matched_count,
        forecast_coverage=forecast_count / expected,
        actual_coverage=actual_count / expected,
        matched_coverage=matched_count / expected,
        mae_mw=sum(abs(error) for error in errors) / matched_count if matched_count else None,
        rmse_mw=sqrt(sum(error * error for error in errors) / matched_count) if matched_count else None,
        bias_mw=sum(errors) / matched_count if matched_count else None,
    )
    return {
        "decision_from": decision_from.isoformat(),
        "decision_to_exclusive": decision_to.isoformat(),
        "decision_days": decision_days,
        "delivery_from": (decision_from + timedelta(days=1)).isoformat(),
        "delivery_to_exclusive": (decision_to + timedelta(days=1)).isoformat(),
        "cutoff_time_utc": cutoff_time_utc.strftime("%H:%M"),
        "metrics": asdict(period_metrics),
        "daily": daily,
    }
