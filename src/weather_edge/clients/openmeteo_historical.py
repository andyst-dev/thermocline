from __future__ import annotations

from datetime import date, timedelta

from ..config import Settings
from ..http import get_json


def fetch_historical_forecast(
    settings: Settings,
    latitude: float,
    longitude: float,
    reference_date: str,
    forecast_days: int = 7,
    model: str = "gfs_seamless",
) -> dict:
    """Fetch a past forecast issued on reference_date from Open-Meteo previous-runs API.

    Returns the raw JSON payload. Hourly temperature_2m is requested.
    """
    ref = date.fromisoformat(reference_date)
    end_date = ref + timedelta(days=forecast_days - 1)
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": reference_date,
        "end_date": end_date.isoformat(),
        "models": model,
        "hourly": "temperature_2m",
        "temperature_unit": "celsius",
        "timezone": "auto",
    }
    return get_json("https://previous-runs-api.open-meteo.com/v1/forecast", params=params, timeout=45)


def fetch_archive_observed(
    settings: Settings,
    latitude: float,
    longitude: float,
    target_date: str,
) -> dict:
    """Fetch observed hourly temperatures for target_date from Open-Meteo archive API.

    Returns the raw JSON payload.
    """
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": target_date,
        "end_date": target_date,
        "hourly": "temperature_2m",
        "temperature_unit": "celsius",
        "timezone": "auto",
    }
    return get_json("https://archive-api.open-meteo.com/v1/archive", params=params, timeout=45)
