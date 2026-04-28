"""Backtesting framework: measure historical forecast residuals vs observed.

Reconstructs what Open-Meteo predicted at multiple horizons before each target
date, compares against archive ground truth, and emits per-bucket / per-season
empirical sigma estimates that can be compared to the heuristic sigma formula
used by the live scanner.
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Iterable

from .clients.openmeteo_historical import fetch_archive_observed, fetch_historical_forecast
from .config import Settings


SIGMA_CALIBRATION_FILENAME = "sigma_calibration.json"
MIN_HORIZON_SEASON_SAMPLES = 5
MIN_HORIZON_SAMPLES = 10


HORIZON_BUCKETS: list[tuple[str, float, float]] = [
    ("0-12h", 0.0, 12.0),
    ("12-24h", 12.0, 24.0),
    ("24-48h", 24.0, 48.0),
    ("48-72h", 48.0, 72.0),
    ("72-96h", 72.0, 96.0),
    ("96-120h", 96.0, 120.0),
    ("120h+", 120.0, math.inf),
]

DEFAULT_HORIZONS = [6, 12, 24, 36, 48, 72, 96, 120]


@dataclass
class BacktestRecord:
    city: str
    latitude: float
    longitude: float
    target_date: str
    reference_date: str
    horizon_hours: float
    forecast_max_c: float
    observed_max_c: float
    residual_c: float
    metric: str
    model_source: str
    fetched_at: str


def horizon_bucket(horizon_hours: float) -> str:
    for label, lo, hi in HORIZON_BUCKETS:
        if lo <= horizon_hours < hi:
            return label
    return HORIZON_BUCKETS[-1][0]


def season_from_month(month: int) -> str:
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    if month in (9, 10, 11):
        return "autumn"
    raise ValueError(f"Invalid month: {month}")


def _extract_daily_extreme(payload: dict, target_date: str, metric: str) -> float | None:
    """Pull min/max temperature for target_date out of an hourly Open-Meteo payload."""
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    if not times or not temps or len(times) != len(temps):
        return None
    day_temps: list[float] = []
    for t, v in zip(times, temps, strict=False):
        if not isinstance(t, str) or v is None:
            continue
        if t.startswith(target_date):
            try:
                day_temps.append(float(v))
            except (TypeError, ValueError):
                continue
    if not day_temps:
        return None
    return max(day_temps) if metric == "highest" else min(day_temps)


def _date_range(start_date: str, end_date: str) -> Iterable[date]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        return
    cur = start
    while cur <= end:
        yield cur
        cur = cur + timedelta(days=1)


def run_backtest_for_city(
    settings: Settings,
    city: str,
    latitude: float,
    longitude: float,
    timezone: str,
    start_date: str,
    end_date: str,
    metric: str = "highest",
    horizons: list[int] | None = None,
    model: str = "gfs_seamless",
) -> list[BacktestRecord]:
    """Run the backtest for a single city across a date range and horizon set.

    Skips silently on per-(date,horizon) failures so a transient API hiccup or
    a date past the previous-runs depth does not abort the whole sweep.
    """
    horizons = horizons or DEFAULT_HORIZONS
    records: list[BacktestRecord] = []
    observed_cache: dict[str, float | None] = {}

    for target_dt in _date_range(start_date, end_date):
        target_iso = target_dt.isoformat()

        if target_iso not in observed_cache:
            try:
                observed_payload = fetch_archive_observed(settings, latitude, longitude, target_iso)
                observed_cache[target_iso] = _extract_daily_extreme(observed_payload, target_iso, metric)
            except Exception:
                observed_cache[target_iso] = None
        observed = observed_cache[target_iso]
        if observed is None:
            continue

        for horizon_hours in horizons:
            ref_dt = datetime.combine(target_dt, datetime.min.time()) - timedelta(hours=horizon_hours)
            reference_date = ref_dt.date().isoformat()
            forecast_days = max(2, math.ceil(horizon_hours / 24.0) + 2)
            try:
                forecast_payload = fetch_historical_forecast(
                    settings,
                    latitude=latitude,
                    longitude=longitude,
                    reference_date=reference_date,
                    forecast_days=forecast_days,
                    model=model,
                )
            except Exception:
                continue
            forecast_extreme = _extract_daily_extreme(forecast_payload, target_iso, metric)
            if forecast_extreme is None:
                continue
            residual = float(forecast_extreme) - float(observed)
            records.append(
                BacktestRecord(
                    city=city,
                    latitude=latitude,
                    longitude=longitude,
                    target_date=target_iso,
                    reference_date=reference_date,
                    horizon_hours=float(horizon_hours),
                    forecast_max_c=float(forecast_extreme),
                    observed_max_c=float(observed),
                    residual_c=float(residual),
                    metric=metric,
                    model_source=f"openmeteo_{model}_historical",
                    fetched_at=datetime.now(dt_timezone.utc).isoformat(),
                )
            )

    return records


def _group_stats(residuals: list[float]) -> dict:
    n = len(residuals)
    if n == 0:
        return {"count": 0, "mean_residual_c": None, "sigma_c": None, "median_abs_error_c": None, "min_residual_c": None, "max_residual_c": None}
    mean = statistics.fmean(residuals)
    sigma = statistics.pstdev(residuals) if n >= 2 else 0.0
    mae = statistics.median([abs(r) for r in residuals])
    return {
        "count": n,
        "mean_residual_c": round(mean, 4),
        "sigma_c": round(sigma, 4),
        "median_abs_error_c": round(mae, 4),
        "min_residual_c": round(min(residuals), 4),
        "max_residual_c": round(max(residuals), 4),
    }


def aggregate_sigma(records: list[BacktestRecord]) -> dict:
    """Compute per-horizon-bucket and per-season residual stats."""
    by_horizon: dict[str, list[float]] = {label: [] for label, _, _ in HORIZON_BUCKETS}
    by_season: dict[str, list[float]] = {"winter": [], "spring": [], "summer": [], "autumn": []}
    by_horizon_season: dict[str, list[float]] = {}

    for r in records:
        bucket = horizon_bucket(r.horizon_hours)
        by_horizon[bucket].append(r.residual_c)
        try:
            month = date.fromisoformat(r.target_date).month
            season = season_from_month(month)
            by_season[season].append(r.residual_c)
            key = f"{bucket}|{season}"
            by_horizon_season.setdefault(key, []).append(r.residual_c)
        except ValueError:
            continue

    return {
        "total_records": len(records),
        "by_horizon": {label: _group_stats(vals) for label, vals in by_horizon.items()},
        "by_season": {label: _group_stats(vals) for label, vals in by_season.items()},
        "by_horizon_season": {label: _group_stats(vals) for label, vals in by_horizon_season.items()},
        "overall": _group_stats([r.residual_c for r in records]),
    }


def load_sigma_calibration(project_root: Path) -> dict | None:
    """Load the latest sigma calibration JSON if available."""
    path = project_root / "data" / SIGMA_CALIBRATION_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def sigma_for_horizon_and_season(
    horizon_hours: float,
    target_date: str,
    calibration: dict | None,
) -> float | None:
    """Pick an empirically-calibrated sigma for the given horizon and season.

    Prefers the horizon|season cross bucket once it has enough samples; falls
    back to the horizon-only bucket; returns None if neither has enough data.
    """
    if calibration is None:
        return None
    bucket = horizon_bucket(horizon_hours)
    try:
        month = date.fromisoformat(target_date).month
        season = season_from_month(month)
    except ValueError:
        season = None

    by_horizon_season = calibration.get("by_horizon_season") or {}
    by_horizon = calibration.get("by_horizon") or {}

    if season is not None:
        cross = by_horizon_season.get(f"{bucket}|{season}")
        if isinstance(cross, dict):
            count = cross.get("count") or 0
            sigma = cross.get("sigma_c")
            if count >= MIN_HORIZON_SEASON_SAMPLES and isinstance(sigma, (int, float)):
                return float(sigma)

    horizon_only = by_horizon.get(bucket)
    if isinstance(horizon_only, dict):
        count = horizon_only.get("count") or 0
        sigma = horizon_only.get("sigma_c")
        if count >= MIN_HORIZON_SAMPLES and isinstance(sigma, (int, float)):
            return float(sigma)

    return None


def recalibrate_sigma(db_path: Path, project_root: Path, lookback_days: int = 60) -> dict:
    """Rebuild sigma calibration from recent backtest_records and persist it."""
    import sqlite3

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = list(
            conn.execute(
                "SELECT * FROM backtest_records "
                "WHERE target_date >= date('now', ?) "
                "ORDER BY target_date DESC",
                (f"-{int(lookback_days)} days",),
            )
        )
    finally:
        conn.close()

    records: list[BacktestRecord] = []
    for row in rows:
        records.append(
            BacktestRecord(
                city=row["city"],
                latitude=float(row["latitude"]),
                longitude=float(row["longitude"]),
                target_date=row["target_date"],
                reference_date=row["reference_date"],
                horizon_hours=float(row["horizon_hours"]),
                forecast_max_c=float(row["forecast_max_c"]),
                observed_max_c=float(row["observed_max_c"]),
                residual_c=float(row["residual_c"]),
                metric=row["metric"],
                model_source=row["model_source"],
                fetched_at=row["fetched_at"],
            )
        )

    aggregates = aggregate_sigma(records)
    aggregates["calibrated_at"] = datetime.now(dt_timezone.utc).isoformat()
    aggregates["lookback_days"] = int(lookback_days)

    out_dir = project_root / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / SIGMA_CALIBRATION_FILENAME
    out_path.write_text(json.dumps(aggregates, indent=2), encoding="utf-8")
    return aggregates


def write_backtest_report(project_root: Path, records: list[BacktestRecord], aggregates: dict) -> tuple[Path, Path]:
    reports_dir = project_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / "backtest_report.json"
    md_path = reports_dir / "backtest_report.md"

    payload = {
        "generated_at": datetime.now(dt_timezone.utc).isoformat(),
        "aggregates": aggregates,
        "records": [asdict(r) for r in records],
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    md_lines = [
        "# Backtest report",
        "",
        f"Total records: {aggregates.get('total_records', 0)}",
        "",
        "## Empirical sigma by horizon bucket",
        "",
        "| Horizon | Count | Mean residual °C | Sigma °C | Median |residual| °C | Min | Max |",
        "|---------|------:|------------------:|---------:|--------------------:|----:|----:|",
    ]
    for label, _, _ in HORIZON_BUCKETS:
        s = aggregates["by_horizon"][label]
        md_lines.append(
            f"| {label} | {s['count']} | {s['mean_residual_c']} | {s['sigma_c']} | "
            f"{s['median_abs_error_c']} | {s['min_residual_c']} | {s['max_residual_c']} |"
        )

    md_lines += [
        "",
        "## Empirical sigma by season",
        "",
        "| Season | Count | Mean residual °C | Sigma °C | Median |residual| °C |",
        "|--------|------:|------------------:|---------:|--------------------:|",
    ]
    for season in ("winter", "spring", "summer", "autumn"):
        s = aggregates["by_season"][season]
        md_lines.append(
            f"| {season} | {s['count']} | {s['mean_residual_c']} | {s['sigma_c']} | {s['median_abs_error_c']} |"
        )

    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return json_path, md_path
