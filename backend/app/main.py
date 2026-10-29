"""HTTP API for the wind forecast evaluation snapshot."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.services.evaluation import evaluate


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
FRONTEND_DIR = ROOT / "frontend" / "dist"
UTC = timezone.utc

app = FastAPI(
    title="Wind Forecast Evaluation API",
    version="0.1.0",
    description=(
        "Evaluates Elexon WINDFOR forecast vintages against FUELHH actuals. "
        "The default is a single-day diagnostic, not a complete January backtest."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@lru_cache(maxsize=1)
def load_snapshot() -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, object]]:
    try:
        with (DATA_DIR / "forecasts.csv").open(newline="", encoding="utf-8") as handle:
            forecasts = list(csv.DictReader(handle))
        with (DATA_DIR / "actuals.csv").open(newline="", encoding="utf-8") as handle:
            actuals = list(csv.DictReader(handle))
        metadata = json.loads((DATA_DIR / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not load normalized data snapshot from {DATA_DIR}") from exc
    return forecasts, actuals, metadata


@app.get("/api/health")
def health() -> dict[str, object]:
    forecasts, actuals, metadata = load_snapshot()
    return {
        "status": "ok",
        "forecast_rows": len(forecasts),
        "actual_rows": len(actuals),
        "retrieved_at": metadata.get("retrieved_at"),
    }


@app.get("/api/metadata")
def metadata() -> dict[str, object]:
    return load_snapshot()[2]


@app.get("/api/evaluation")
def evaluation(
    delivery_from: datetime = Query(default=datetime(2026, 1, 15, tzinfo=UTC)),
    delivery_to: datetime = Query(default=datetime(2026, 1, 16, tzinfo=UTC)),
    mode: Literal["fixed_cutoff", "fixed_horizon"] = Query(default="fixed_cutoff"),
    cutoff: datetime | None = Query(default=None),
    horizon_hours: float = Query(default=24.0, gt=0, le=168),
) -> dict[str, object]:
    forecasts, actuals, _ = load_snapshot()
    effective_cutoff = cutoff
    if mode == "fixed_cutoff" and effective_cutoff is None:
        is_default_sample = (
            delivery_from == datetime(2026, 1, 15, tzinfo=UTC)
            and delivery_to == datetime(2026, 1, 16, tzinfo=UTC)
        )
        if not is_default_sample:
            raise HTTPException(status_code=422, detail="cutoff is required for a custom fixed_cutoff window")
        effective_cutoff = datetime(2026, 1, 14, 7, 30, tzinfo=UTC)
    if mode == "fixed_horizon" and cutoff is not None:
        raise HTTPException(status_code=422, detail="omit cutoff when using fixed_horizon mode")
    try:
        result = evaluate(
            forecasts,
            actuals,
            delivery_from=delivery_from,
            delivery_to=delivery_to,
            mode=mode,
            cutoff=effective_cutoff if mode == "fixed_cutoff" else None,
            horizon=timedelta(hours=horizon_hours) if mode == "fixed_horizon" else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result.to_dict()


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
