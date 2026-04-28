from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from ..http import get_json
# Public API key embedded in Weather Underground history pages. Treat this as
# best-effort read-only verification, not a guaranteed contract.
WEATHER_COM_API_KEY = "e1f10a1e78da46f5b10a1e78da96f525"


def icao_from_wunderground_source(source: str | None) -> str | None:
    if not source:
        return None
    match = re.search(r"/([A-Z]{4})(?:/date/|[/?#.]|$)", source)
    return match.group(1) if match else None


def _country_code_from_wunderground_source(source: str | None) -> str | None:
    """Return the Weather.com location country segment for a Wunderground URL.

    Wunderground history URLs usually look like:
    /history/daily/<country>/<city-or-region>/<ICAO>

    Weather.com's historical endpoint requires `<ICAO>:9:<COUNTRY>`. The old
    code hard-coded `US`, which works for US stations but returns HTTP 400 for
    OPKC/ZGSZ/EGLC/etc. For US URLs the segment after `/daily/` is `us` and the
    next segment may be a state (`ca`, `ny`), so keep it as `US`.
    """
    if not source:
        return None
    try:
        parts = [p for p in urlparse(source).path.split("/") if p]
    except Exception:
        return None
    try:
        daily_idx = parts.index("daily")
    except ValueError:
        return None
    if daily_idx + 1 >= len(parts):
        return None
    country = parts[daily_idx + 1].upper()
    return country if re.fullmatch(r"[A-Z]{2}", country) else None


def fetch_historical_observations(icao: str, target_date: datetime, units: str = "m", country: str = "US") -> dict[str, Any]:
    date = target_date.strftime("%Y%m%d")
    return get_json(
        f"https://api.weather.com/v1/location/{icao}:9:{country}/observations/historical.json",
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
    country = _country_code_from_wunderground_source(source) or "US"
    try:
        payload = fetch_historical_observations(icao, target_date, units="m", country=country)
    except Exception as exc:
        return None, 0, f"weather.com historical fetch failed for {icao}:9:{country}: {exc}"
    observations = payload.get("observations") if isinstance(payload, dict) else None
    if not isinstance(observations, list):
        return None, 0, f"weather.com historical response missing observations for {icao}"
    temps: list[float] = []
    observation_count = 0
    # Weather.com historical rows often expose `max_temp`/`min_temp` daily
    # aggregates on only one row. Using only instantaneous `temp` can miss the
    # official daily extreme badly (e.g. KLGA had max_temp=18 while hourly temp
    # samples in the returned window topped at 10). Include the aggregate field
    # for the metric when present, then fall back to instantaneous temps.
    metric_field = "min_temp" if metric == "lowest" else "max_temp"
    for row in observations:
        if not isinstance(row, dict):
            continue
        row_had_temp = False
        for field in (metric_field, "temp"):
            if row.get(field) is None:
                continue
            try:
                temps.append(float(row[field]))
                row_had_temp = True
            except (TypeError, ValueError):
                continue
        if row_had_temp:
            observation_count += 1
    if not temps:
        return None, 0, f"no weather.com historical temperatures for {icao}"
    observed = min(temps) if metric == "lowest" else max(temps)
    return observed, observation_count, f"weather.com/Wunderground historical obs at {icao}:9:{country}"
