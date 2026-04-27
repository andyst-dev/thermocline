"""Ensemble forecast fetcher using Open-Meteo GFS ensemble (31 members).

Provides data-driven probability estimates by counting ensemble members
within a temperature bucket, replacing Gaussian assumptions for mid-to-long
horizons (>36h) where forecast spread is regime-dependent.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any

from .http import get_json


def fetch_gfs_ensemble(
    latitude: float,
    longitude: float,
    target_date: date,
    temperature_unit: str = "celsius",
) -> dict[str, Any] | None:
    """Fetch GFS ensemble forecast from Open-Meteo Ensemble API.

    Returns a dict with:
      - member_maxs: list of daily max temps (one per member, 31 items)
      - member_mins: list of daily min temps (one per member, 31 items)
      - control_max: daily max from the control run
      - control_min: daily min from the control run
      - spread_max: empirical std-dev of member_maxs
      - spread_min: empirical std-dev of member_mins
      - num_members: count of valid members (should be 31)
    """
    try:
        payload = get_json(
            "https://ensemble-api.open-meteo.com/v1/ensemble",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "hourly": "temperature_2m",
                "models": "gfs_seamless",
                "temperature_unit": temperature_unit,
                "start_date": target_date.isoformat(),
                "end_date": target_date.isoformat(),
            },
            timeout=30,
        )
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        return None

    # Collect all member keys: control + member01..member30
    member_keys = ["temperature_2m"]  # control run
    for i in range(1, 31):
        member_keys.append(f"temperature_2m_member{i:02d}")

    member_maxs: list[float] = []
    member_mins: list[float] = []

    for key in member_keys:
        values = hourly.get(key)
        if not isinstance(values, list) or not values:
            continue
        # Filter out None values
        clean = [float(v) for v in values if v is not None]
        if not clean:
            continue
        member_maxs.append(max(clean))
        member_mins.append(min(clean))

    if not member_maxs:
        return None

    def _std(vals: list[float]) -> float:
        n = len(vals)
        if n < 2:
            return 0.0
        mean = sum(vals) / n
        variance = sum((v - mean) ** 2 for v in vals) / n
        return math.sqrt(variance)

    return {
        "member_maxs": member_maxs,
        "member_mins": member_mins,
        "control_max": member_maxs[0] if member_maxs else None,
        "control_min": member_mins[0] if member_mins else None,
        "spread_max": _std(member_maxs),
        "spread_min": _std(member_mins),
        "num_members": len(member_maxs),
    }


def ensemble_bucket_probability(
    member_values: list[float],
    lower: float | None,
    upper: float | None,
) -> float:
    """Fraction of ensemble members whose value falls inside [lower, upper].

    Open bounds (lower=None or upper=None) are treated as tails.
    """
    if not member_values:
        return 0.5

    def _in_bucket(v: float) -> bool:
        if lower is not None and v < lower:
            return False
        if upper is not None and v > upper:
            return False
        return True

    count = sum(1 for v in member_values if _in_bucket(v))
    return count / len(member_values)


def ensemble_probability_above(member_values: list[float], threshold: float) -> float:
    """Fraction of ensemble members strictly above threshold."""
    if not member_values:
        return 0.5
    count = sum(1 for v in member_values if v > threshold)
    return count / len(member_values)


def ensemble_probability_below(member_values: list[float], threshold: float) -> float:
    """Fraction of ensemble members strictly below threshold."""
    if not member_values:
        return 0.5
    count = sum(1 for v in member_values if v < threshold)
    return count / len(member_values)