from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..http import get_json
# Public API key embedded in Weather Underground history pages. Treat this as
# best-effort read-only verification, not a guaranteed contract.
WEATHER_COM_API_KEY = "e1f10a1e78da46f5b10a1e78da96f525"


def icao_from_wunderground_source(source: str | None) -> str | None:
    if not source:
        return None
    match = re.search(r"/([A-Z]{4})(?:/date/|[/?#.]|$)", source)
    return match.group(1) if match else None


def fetch_historical_observations(icao: str, target_date: datetime, units: str = "m") -> dict[str, Any]:
    date = target_date.strftime("%Y%m%d")
    return get_json(
        f"https://api.weather.com/v1/location/{icao}:9:US/observations/historical.json",
        params={
            "apiKey": WEATHER_COM_API_KEY,
            "units": units,
            "startDate": date,
            "endDate": date,
        },
        timeout=30,
    )


def official_extreme_c(source: str | None, target_date: datetime, metric: str) -> tuple[float | None, int, str]:
    icao = icao_from_wunderground_source(source)
    if not icao:
        return None, 0, "no Wunderground ICAO in resolution source"
    try:
        payload = fetch_historical_observations(icao, target_date, units="m")
    except Exception as exc:
        return None, 0, f"weather.com historical fetch failed for {icao}: {exc}"
    observations = payload.get("observations") if isinstance(payload, dict) else None
    if not isinstance(observations, list):
        return None, 0, f"weather.com historical response missing observations for {icao}"
    temps: list[float] = []
    for row in observations:
        if not isinstance(row, dict) or row.get("temp") is None:
            continue
        try:
            temps.append(float(row["temp"]))
        except (TypeError, ValueError):
            continue
    if not temps:
        return None, 0, f"no weather.com historical temperatures for {icao}"
    observed = min(temps) if metric == "lowest" else max(temps)
    return observed, len(temps), f"weather.com/Wunderground historical obs at {icao}"
