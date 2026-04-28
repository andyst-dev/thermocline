from __future__ import annotations

from datetime import datetime, date, time as dtime, timezone
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
    # Ensure the METAR lookback window covers the full target local day.
    # The day ends at 23:59 local time; compute how many hours ago that was in UTC.
    end_of_day_local = datetime.combine(target_local_date, dtime(23, 59), tzinfo=tz)
    end_of_day_utc = end_of_day_local.astimezone(timezone.utc)
    hours_needed = max(48, int((datetime.now(timezone.utc) - end_of_day_utc).total_seconds() / 3600) + 24)
    temps: list[float] = []
    for row in fetch_metars(icao, hours=hours_needed):
        if row.get("temp") is None or row.get("obsTime") is None:
            continue
        obs_dt = datetime.fromtimestamp(int(row["obsTime"]), tz=timezone.utc).astimezone(tz)
        if obs_dt.date() == target_local_date:
            temps.append(float(row["temp"]))
    if not temps:
        return None, 0
    return (min(temps) if metric == "lowest" else max(temps)), len(temps)


_STATION_COORDS_CACHE: dict[str, dict | None] = {}


def station_coords(icao: str) -> dict | None:
    """Fetch lat/lon for an ICAO station from AviationWeather METAR API.

    The METAR endpoint returns station metadata (including lat/lon) alongside
    observations. Returns {"lat": float, "lon": float} or None if not found.
    """
    icao = icao.upper()
    if icao in _STATION_COORDS_CACHE:
        return _STATION_COORDS_CACHE[icao]
    try:
        rows = fetch_metars(icao, hours=1)
        if isinstance(rows, list) and rows:
            result = {"lat": float(rows[0]["lat"]), "lon": float(rows[0]["lon"])}
            _STATION_COORDS_CACHE[icao] = result
            return result
    except Exception:
        pass
    _STATION_COORDS_CACHE[icao] = None
    return None
