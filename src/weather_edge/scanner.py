from __future__ import annotations

from datetime import datetime, timezone

from .clients.openmeteo import fetch_hourly_forecast, geocode_city
from .config import Settings
from .models import BucketProbability, MarketContext, ScanResult, WeatherMarket
from .parsing import bucket_probability, parse_bucket, parse_city_and_date


class ScanSkip(Exception):
    pass


def _extract_context(settings: Settings, market: WeatherMarket) -> MarketContext:
    parsed = parse_city_and_date(market.question)
    if not parsed:
        raise ScanSkip("question pattern unsupported")
    city, target_date = parsed
    geo = geocode_city(settings, city)
    if not geo:
        raise ScanSkip(f"geocode failed for {city}")
    return MarketContext(
        market=market,
        city=city,
        target_date=target_date,
        latitude=float(geo["latitude"]),
        longitude=float(geo["longitude"]),
        timezone=str(geo.get("timezone") or "auto"),
    )


def _forecast_daily_max(forecast: dict, target_date: datetime) -> float:
    hourly = forecast.get("hourly") or {}
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    if not times or not temps:
        raise ScanSkip("missing hourly forecast")
    values = []
    target_prefix = target_date.strftime("%Y-%m-%d")
    for t, temp in zip(times, temps, strict=False):
        if str(t).startswith(target_prefix):
            values.append(float(temp))
    if not values:
        raise ScanSkip(f"no hourly values for {target_prefix}")
    return max(values)


def _sigma_for_horizon(target_date: datetime) -> tuple[float, float]:
    horizon_hours = (target_date - datetime.now(timezone.utc)).total_seconds() / 3600
    if horizon_hours < 0:
        horizon_hours = 0.0
    sigma_c = min(5.0, 1.5 + (horizon_hours / 72.0) * 2.5)
    return sigma_c, horizon_hours


def scan_market(settings: Settings, market: WeatherMarket) -> tuple[ScanResult, dict]:
    context = _extract_context(settings, market)
    forecast = fetch_hourly_forecast(settings, context.latitude, context.longitude, context.timezone)
    forecast_max_c = _forecast_daily_max(forecast, context.target_date)
    sigma_c, horizon_hours = _sigma_for_horizon(context.target_date)

    buckets: list[BucketProbability] = []
    for label, market_prob in zip(market.outcomes, market.outcome_prices, strict=False):
        lower, upper = parse_bucket(label)
        model_prob = bucket_probability(lower, upper, forecast_max_c, sigma_c)
        edge = model_prob - market_prob
        ev = (model_prob * 1.0) - market_prob
        buckets.append(
            BucketProbability(
                label=label,
                lower=lower,
                upper=upper,
                market_prob=market_prob,
                model_prob=model_prob,
                edge=edge,
                ev=ev,
            )
        )

    buckets.sort(key=lambda x: x.ev, reverse=True)
    top = buckets[0] if buckets else None
    confidence = "low"
    if sigma_c <= 2.5:
        confidence = "medium"
    if sigma_c <= 2.0 and horizon_hours <= 36:
        confidence = "high"

    result = ScanResult(
        market_id=market.market_id,
        slug=market.slug,
        question=market.question,
        city=context.city,
        target_date=context.target_date.date().isoformat(),
        forecast_max_c=forecast_max_c,
        sigma_c=sigma_c,
        horizon_hours=horizon_hours,
        liquidity=market.liquidity,
        buckets=buckets,
        top_bucket_label=top.label if top else None,
        top_bucket_ev=top.ev if top else None,
        confidence=confidence,
    )
    return result, {
        "context": {
            "city": context.city,
            "latitude": context.latitude,
            "longitude": context.longitude,
            "timezone": context.timezone,
        },
        "forecast": forecast,
    }


def filter_markets(markets: list[WeatherMarket], min_liquidity: float) -> list[WeatherMarket]:
    return [m for m in markets if m.active and not m.closed and m.liquidity >= min_liquidity]
