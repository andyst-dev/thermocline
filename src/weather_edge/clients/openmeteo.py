from __future__ import annotations

from ..config import Settings
from ..http import get_json

_GEOCODE_CACHE: dict[str, dict | None] = {}
_FORECAST_CACHE: dict[tuple[float, float, str], dict] = {}


def geocode_city(settings: Settings, city: str) -> dict | None:
    key = city.strip().lower()
    if key in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[key]
    payload = get_json(
        settings.openmeteo_geocode_url,
        params={"name": city, "count": 1, "language": "en", "format": "json"},
    )
    results = payload.get("results") or []
    result = results[0] if results else None
    _GEOCODE_CACHE[key] = result
    return result


def fetch_hourly_forecast(settings: Settings, latitude: float, longitude: float, timezone: str = "auto") -> dict:
    key = (round(latitude, 4), round(longitude, 4), timezone)
    if key in _FORECAST_CACHE:
        return _FORECAST_CACHE[key]
    payload = get_json(
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
    _FORECAST_CACHE[key] = payload
    return payload
