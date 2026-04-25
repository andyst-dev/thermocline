from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ..http import get_json

_METAR_CACHE: dict[str, list[dict[str, Any]]] = {}


def fetch_metars(icao: str, hours: int = 48) -> list[dict[str, Any]]:
    key = f"{icao}:{hours}"
    if key in _METAR_CACHE:
        return _METAR_CACHE[key]
    try:
        payload = get_json(
            "https://aviationweather.gov/api/data/metar",
            params={"ids": icao, "format": "json", "taf": "false", "hours": hours},
            timeout=30,
        )
    except Exception:
        payload = []
    if not isinstance(payload, list):
        payload = []
    _METAR_CACHE[key] = payload
    return payload


def observed_extreme_c(icao: str, target_date: datetime, timezone_name: str, metric: str) -> tuple[float | None, int]:
    tz = ZoneInfo(timezone_name) if timezone_name and timezone_name != "auto" else timezone.utc
    target_local_date = target_date.astimezone(tz).date()
    temps: list[float] = []
    for row in fetch_metars(icao):
        if row.get("temp") is None or row.get("obsTime") is None:
            continue
        obs_dt = datetime.fromtimestamp(int(row["obsTime"]), tz=timezone.utc).astimezone(tz)
        if obs_dt.date() == target_local_date:
            temps.append(float(row["temp"]))
    if not temps:
        return None, 0
    return (min(temps) if metric == "lowest" else max(temps)), len(temps)
