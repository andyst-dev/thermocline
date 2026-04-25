from __future__ import annotations

from ..config import Settings
from ..http import get_json


def geocode_city(settings: Settings, city: str) -> dict | None:
    payload = get_json(
        settings.openmeteo_geocode_url,
        params={"name": city, "count": 1, "language": "en", "format": "json"},
    )
    results = payload.get("results") or []
    return results[0] if results else None


def fetch_hourly_forecast(settings: Settings, latitude: float, longitude: float, timezone: str = "auto") -> dict:
    return get_json(
        settings.openmeteo_forecast_url,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "hourly": ["temperature_2m", "precipitation_probability"],
            "temperature_unit": "celsius",
            "timezone": timezone,
            "forecast_days": 16,
        },
    )
