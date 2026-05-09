from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from ..http import get_json

# Weather.com / Wunderground history endpoints require an API key. Do not commit
# credentials; provide this from the runtime environment when official
# Weather.com verification is enabled.
WEATHER_COM_API_KEY = os.getenv("WEATHER_COM_API_KEY")


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
    if not WEATHER_COM_API_KEY:
        raise RuntimeError("WEATHER_COM_API_KEY is not configured")
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
    instant_temps: list[float] = []
    aggregate_temps: list[float] = []
    observation_count = 0
    # Weather.com historical rows expose both instantaneous `temp` samples and
    # occasional daily aggregate fields (`max_temp`/`min_temp`). Do not merge the
    # aggregate with samples and then take the extreme: stale aggregate values can
    # fabricate a daily high/low that is inconsistent with the hourly table shown
    # on Wunderground. Prefer the instantaneous samples; if an aggregate diverges
    # materially, mark the official observation unavailable instead of feeding a
    # suspect value into settlement/calibration diagnostics.
    metric_field = "min_temp" if metric == "lowest" else "max_temp"
    for row in observations:
        if not isinstance(row, dict):
            continue
        if row.get("temp") is not None:
            try:
                instant_temps.append(float(row["temp"]))
                observation_count += 1
            except (TypeError, ValueError):
                pass
        if row.get(metric_field) is not None:
            try:
                aggregate_temps.append(float(row[metric_field]))
            except (TypeError, ValueError):
                pass
    if not instant_temps:
        return None, 0, f"no weather.com historical instantaneous temperatures for {icao}"
    observed = min(instant_temps) if metric == "lowest" else max(instant_temps)
    if aggregate_temps:
        aggregate_observed = min(aggregate_temps) if metric == "lowest" else max(aggregate_temps)
        if abs(aggregate_observed - observed) > 2.0:
            return (
                None,
                observation_count,
                f"weather.com aggregate/sample divergence at {icao}:9:{country}: "
                f"aggregate={aggregate_observed}C samples={observed}C",
            )
    return observed, observation_count, f"weather.com/Wunderground historical hourly obs at {icao}:9:{country}"
