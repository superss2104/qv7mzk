"""Select a historically available forecast for a delivery interval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Literal, Mapping


SelectionMode = Literal["fixed_cutoff", "fixed_horizon"]
UTC = timezone.utc


def _utc(value: datetime | str, field: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class ForecastPoint:
    delivery_start: datetime
    published_at: datetime
    generation_mw: float

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> "ForecastPoint":
        delivery = row.get("start_time", row.get("startTime"))
        published = row.get("publish_time", row.get("publishTime"))
        generation = row.get("generation_mw", row.get("generation"))
        if delivery is None or published is None or generation is None:
            raise ValueError("Forecast row must have start time, publication time, and generation")
        return cls(_utc(delivery, "delivery start"), _utc(published, "publication time"), float(generation))


@dataclass(frozen=True)
class ForecastSelection:
    delivery_start: datetime
    cutoff: datetime
    forecast: ForecastPoint | None

    @property
    def lead_time(self) -> timedelta | None:
        if self.forecast is None:
            return None
        return self.delivery_start - self.forecast.published_at


def select_forecast(
    rows: Iterable[ForecastPoint | Mapping[str, object]],
    delivery_start: datetime | str,
    *,
    mode: SelectionMode,
    cutoff: datetime | str | None = None,
    horizon: timedelta | None = None,
) -> ForecastSelection:
    """Return the latest eligible forecast, or an explicit missing selection.

    `fixed_cutoff` uses the supplied cutoff. `fixed_horizon` sets the cutoff to
    delivery_start minus horizon. Publication at the cutoff is eligible.
    """
    delivery = _utc(delivery_start, "delivery start")
    if mode == "fixed_cutoff":
        if cutoff is None:
            raise ValueError("fixed_cutoff mode requires cutoff")
        effective_cutoff = _utc(cutoff, "cutoff")
    elif mode == "fixed_horizon":
        if horizon is None or horizon < timedelta(0):
            raise ValueError("fixed_horizon mode requires a non-negative horizon")
        if cutoff is not None:
            raise ValueError("cutoff cannot be supplied with fixed_horizon mode")
        effective_cutoff = delivery - horizon
    else:
        raise ValueError(f"Unsupported selection mode: {mode}")

    unique: dict[tuple[datetime, datetime], ForecastPoint] = {}
    for value in rows:
        point = value if isinstance(value, ForecastPoint) else ForecastPoint.from_row(value)
        if point.delivery_start != delivery:
            continue
        key = (point.published_at, point.delivery_start)
        previous = unique.get(key)
        if previous is not None and previous.generation_mw != point.generation_mw:
            raise ValueError(f"Conflicting forecast duplicate at {point.published_at.isoformat()}")
        unique[key] = point

    eligible = [point for point in unique.values() if point.published_at <= effective_cutoff]
    selected = max(eligible, key=lambda point: point.published_at) if eligible else None
    return ForecastSelection(delivery, effective_cutoff, selected)
