from __future__ import annotations

import math
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


def compute_recent_bias_14d(
    db_path: Path,
    city: str,
    target_date: datetime | str,
    metric: str,
    window_days: int = 14,
    min_samples: int = 3,
) -> dict | None:
    if isinstance(target_date, datetime):
        target_str = target_date.date().isoformat()
    else:
        target_str = target_date
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        rows = conn.execute(
            """
            SELECT residual_c FROM backtest_records
            WHERE city = ?
              AND metric = ?
              AND target_date >= date(?, ?)
              AND target_date < ?
            """,
            (city, metric, target_str, f"-{window_days} days", target_str),
        ).fetchall()
    finally:
        conn.close()
    if len(rows) < min_samples:
        return None
    mean_r = sum(r[0] for r in rows) / len(rows)
    return {
        "count": len(rows),
        "mean_residual_c": mean_r,
        "window_days": window_days,
        "source": "backtest_records",
    }


def compute_station_climatology(
    db_path: Path,
    city: str,
    target_date: datetime | str,
    metric: str,
    day_window: int = 7,
    min_samples: int = 5,
) -> dict | None:
    if isinstance(target_date, datetime):
        dt = target_date.date()
    else:
        from datetime import date
        dt = date.fromisoformat(target_date)
    target_doy = int(dt.strftime("%j"))
    with sqlite3.connect(db_path, timeout=30) as conn:
        rows = conn.execute(
            """
            SELECT observed_max_c, target_date FROM backtest_records
            WHERE city = ? AND metric = ? AND target_date < ?
            """,
            (city, metric, dt.isoformat()),
        ).fetchall()
    values: list[float] = []
    for obs_c, tdate_str in rows:
        if obs_c is None or tdate_str is None:
            continue
        try:
            row_dt = datetime.strptime(tdate_str[:10], "%Y-%m-%d")
        except ValueError:
            continue
        row_doy = int(row_dt.strftime("%j"))
        dist = abs(row_doy - target_doy)
        dist = min(dist, 365 - dist)
        if dist <= day_window:
            values.append(float(obs_c))
    if len(values) < min_samples:
        return None
    mean_v = sum(values) / len(values)
    variance = sum((v - mean_v) ** 2 for v in values) / len(values)
    std_v = math.sqrt(variance)
    return {
        "count": len(values),
        "mean_observed_c": mean_v,
        "std_observed_c": std_v,
        "day_window": day_window,
        "source": "backtest_records",
    }


_NWS_HEADERS = {
    "User-Agent": "weather-edge/1.0",
    "Accept": "application/ld+json",
}


def fetch_nws_discussion(latitude: float, longitude: float, timeout: int = 10) -> dict | None:
    # weather.gov/NWS only covers US territories. Avoid slow failures for the
    # mostly international Polymarket weather universe; this is a hook, not a
    # mandatory dependency.
    if not (18.0 <= latitude <= 72.0 and -170.0 <= longitude <= -60.0):
        return None
    try:
        points_url = f"https://api.weather.gov/points/{latitude:.4f},{longitude:.4f}"
        req = Request(points_url, headers=_NWS_HEADERS)
        with urlopen(req, timeout=timeout) as resp:
            points_data = json.loads(resp.read().decode())
        office = points_data.get("properties", {}).get("cwa") or points_data.get("properties", {}).get("gridId")
        if not office:
            return None
        disc_url = f"https://api.weather.gov/products/types/AFD/locations/{office}"
        req2 = Request(disc_url, headers=_NWS_HEADERS)
        with urlopen(req2, timeout=timeout) as resp2:
            disc_list = json.loads(resp2.read().decode())
        items = disc_list.get("@graph") or disc_list.get("productList") or []
        if not items:
            return None
        latest_id = items[0].get("id") or items[0].get("productId")
        if not latest_id:
            return None
        prod_url = f"https://api.weather.gov/products/{latest_id}"
        req3 = Request(prod_url, headers=_NWS_HEADERS)
        with urlopen(req3, timeout=timeout) as resp3:
            prod_data = json.loads(resp3.read().decode())
        text = prod_data.get("productText") or ""
        issued_at = prod_data.get("issuanceTime") or ""
        return {"office": office, "issued_at": issued_at, "text": text}
    except Exception:
        return None


def build_weather_feature_bundle(
    db_path: Path,
    city: str,
    latitude: float,
    longitude: float,
    target_date: datetime | str,
    metric: str,
    forecast_value_c: float,
    forecast_sources: dict[str, Any] | None = None,
) -> dict:
    recent_bias = compute_recent_bias_14d(db_path, city, target_date, metric, min_samples=2)
    clim = compute_station_climatology(db_path, city, target_date, metric, min_samples=2)
    nws = fetch_nws_discussion(latitude, longitude)

    if recent_bias is not None:
        bias_corrected = forecast_value_c - recent_bias["mean_residual_c"]
    else:
        bias_corrected = forecast_value_c

    sources: dict[str, Any] = dict(forecast_sources) if forecast_sources else {}
    sources["nws_discussion"] = {"available": nws is not None}

    return {
        "recent_bias_14d": recent_bias,
        "station_climatology": clim,
        "nws_discussion": nws,
        "forecast_sources": sources,
        "bias_corrected_forecast_c": bias_corrected,
    }
