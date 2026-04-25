from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .clients.aviationweather import observed_extreme_c
from .clients.clob import simulate_buy_fill
from .clients.nasa_gistemp import global_temp_baseline
from .clients.openmeteo import fetch_hourly_forecast, geocode_city
from .config import Settings
from .models import BucketProbability, MarketContext, ScanResult, WeatherMarket
from .parsing import bucket_probability, parse_bucket, parse_city_and_date, parse_global_temperature_market, parse_temperature_contract


class ScanSkip(Exception):
    pass


def _cap_model_prob(prob: float) -> float:
    # Until sigma is empirically calibrated, avoid fake certainties that make
    # dust-priced contracts look like infinite money.
    return max(0.05, min(0.95, prob))


def _enrich_with_clob(settings: Settings, market: WeatherMarket, buckets: list[BucketProbability]) -> None:
    token_ids = market.raw.get("clobTokenIds") or []
    if isinstance(token_ids, str):
        import json
        try:
            token_ids = json.loads(token_ids)
        except json.JSONDecodeError:
            token_ids = []
    if not isinstance(token_ids, list) or len(token_ids) != len(market.outcomes):
        return
    token_by_label = {str(label): str(token_id) for label, token_id in zip(market.outcomes, token_ids, strict=False)}
    for bucket in buckets:
        token_id = token_by_label.get(bucket.label)
        if not token_id:
            continue
        fill = simulate_buy_fill(settings, token_id, usd_size=1.0)
        bucket.token_id = token_id
        bucket.best_bid = fill.best_bid
        bucket.best_ask = fill.best_ask
        bucket.ask_capacity_usd = fill.capacity_usd_at_best_ask
        bucket.fill_avg_price = fill.avg_price
        bucket.fill_shares = fill.shares
        bucket.fill_cost_usd = fill.cost_usd
        bucket.fill_levels_json = json.dumps(fill.levels_used)
        bucket.book_fetched_at = fill.book_fetched_at
        if fill.filled and fill.avg_price is not None:
            bucket.executable_ev = bucket.model_prob - fill.avg_price


def _sort_buckets(buckets: list[BucketProbability]) -> None:
    buckets.sort(key=lambda x: x.executable_ev if x.executable_ev is not None else -999.0, reverse=True)


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


def _forecast_daily_extreme(forecast: dict, target_date: datetime, metric: str = "highest") -> float:
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
    return min(values) if metric == "lowest" else max(values)


def _sigma_for_horizon(target_date: datetime) -> tuple[float, float]:
    horizon_hours = (target_date - datetime.now(timezone.utc)).total_seconds() / 3600
    if horizon_hours < 0:
        horizon_hours = 0.0
    sigma_c = min(5.0, 1.5 + (horizon_hours / 72.0) * 2.5)
    return sigma_c, horizon_hours


def _scan_city_temperature_market(settings: Settings, market: WeatherMarket) -> tuple[ScanResult, dict]:
    context = _extract_context(settings, market)
    forecast = fetch_hourly_forecast(settings, context.latitude, context.longitude, context.timezone)
    forecast_max_c = _forecast_daily_extreme(forecast, context.target_date, "highest")
    sigma_c, horizon_hours = _sigma_for_horizon(context.target_date)

    buckets: list[BucketProbability] = []
    for label, market_prob in zip(market.outcomes, market.outcome_prices, strict=False):
        lower, upper = parse_bucket(label)
        model_prob = _cap_model_prob(bucket_probability(lower, upper, forecast_max_c, sigma_c))
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

    _enrich_with_clob(settings, market, buckets)
    _sort_buckets(buckets)
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


def _resolution_location(market: WeatherMarket, fallback_city: str) -> str:
    source = str(market.raw.get("resolutionSource") or "")
    match = re.search(r"/([A-Z]{4})(?:[/?#.]|$)", source)
    if match:
        return match.group(1)
    description = str(market.raw.get("description") or "")
    match = re.search(r"recorded at the (.+?) Station", description, flags=re.IGNORECASE)
    if match:
        station = match.group(1).replace("Intl", "International")
        return station
    return fallback_city


def _scan_temperature_contract(settings: Settings, market: WeatherMarket) -> tuple[ScanResult, dict]:
    contract = parse_temperature_contract(market.question)
    if not contract:
        raise ScanSkip("question pattern unsupported")
    resolution_location = _resolution_location(market, contract.city)
    geo = geocode_city(settings, resolution_location)
    if not geo:
        raise ScanSkip(f"geocode failed for {resolution_location}")
    timezone_name = str(geo.get("timezone") or "auto")
    forecast = fetch_hourly_forecast(settings, float(geo["latitude"]), float(geo["longitude"]), timezone_name)
    forecast_value_c = _forecast_daily_extreme(forecast, contract.target_date, contract.metric)
    sigma_c, horizon_hours = _sigma_for_horizon(contract.target_date)
    observed_count = 0
    if re.fullmatch(r"[A-Z]{4}", resolution_location):
        observed_value, observed_count = observed_extreme_c(resolution_location, contract.target_date, timezone_name, contract.metric)
        if observed_value is not None:
            forecast_value_c = observed_value
            sigma_c = 0.3
    bucket_prob = _cap_model_prob(bucket_probability(contract.lower_c, contract.upper_c, forecast_value_c, sigma_c))

    buckets: list[BucketProbability] = []
    for label, market_prob in zip(market.outcomes, market.outcome_prices, strict=False):
        model_prob = bucket_prob if label.lower() == "yes" else 1.0 - bucket_prob
        buckets.append(
            BucketProbability(
                label=label,
                lower=contract.lower_c if label.lower() == "yes" else None,
                upper=contract.upper_c if label.lower() == "yes" else None,
                market_prob=market_prob,
                model_prob=model_prob,
                edge=model_prob - market_prob,
                ev=model_prob - market_prob,
            )
        )

    _enrich_with_clob(settings, market, buckets)
    _sort_buckets(buckets)
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
        city=contract.city,
        target_date=contract.target_date.date().isoformat(),
        forecast_max_c=forecast_value_c,
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
            "city": contract.city,
            "latitude": float(geo["latitude"]),
            "longitude": float(geo["longitude"]),
            "timezone": timezone_name,
            "metric": contract.metric,
            "resolution_location": resolution_location,
            "observed_metar_count": observed_count,
            "bucket_label": contract.label,
            "bucket_lower_c": contract.lower_c,
            "bucket_upper_c": contract.upper_c,
        },
        "forecast": forecast,
    }


def _scan_global_temperature_market(settings: Settings, market: WeatherMarket) -> tuple[ScanResult, dict]:
    parsed = parse_global_temperature_market(market.question)
    if not parsed:
        raise ScanSkip("question pattern unsupported")
    year, month, lower, upper = parsed
    baseline = global_temp_baseline(month)
    bracket_prob = _cap_model_prob(bucket_probability(lower, upper, baseline.mean_c, baseline.sigma_c))

    buckets: list[BucketProbability] = []
    for label, market_prob in zip(market.outcomes, market.outcome_prices, strict=False):
        model_prob = bracket_prob if label.lower() == "yes" else 1.0 - bracket_prob
        buckets.append(
            BucketProbability(
                label=label,
                lower=lower if label.lower() == "yes" else None,
                upper=upper if label.lower() == "yes" else None,
                market_prob=market_prob,
                model_prob=model_prob,
                edge=model_prob - market_prob,
                ev=model_prob - market_prob,
            )
        )

    _enrich_with_clob(settings, market, buckets)
    _sort_buckets(buckets)
    top = buckets[0] if buckets else None
    result = ScanResult(
        market_id=market.market_id,
        slug=market.slug,
        question=market.question,
        city="Global",
        target_date=f"{year}-{month:02d}",
        forecast_max_c=baseline.mean_c,
        sigma_c=baseline.sigma_c,
        horizon_hours=0.0,
        liquidity=market.liquidity,
        buckets=buckets,
        top_bucket_label=top.label if top else None,
        top_bucket_ev=top.ev if top else None,
        confidence="low",
    )
    return result, {
        "context": {"city": "Global", "latitude": 0.0, "longitude": 0.0, "timezone": "UTC"},
        "forecast": {
            "kind": "nasa_gistemp_baseline",
            "target_month": month,
            "mean_c": baseline.mean_c,
            "sigma_c": baseline.sigma_c,
            "samples": baseline.samples,
            "source_url": baseline.source_url,
        },
    }


def scan_market(settings: Settings, market: WeatherMarket) -> tuple[ScanResult, dict]:
    if parse_temperature_contract(market.question):
        return _scan_temperature_contract(settings, market)
    if parse_city_and_date(market.question):
        return _scan_city_temperature_market(settings, market)
    return _scan_global_temperature_market(settings, market)


def filter_markets(markets: list[WeatherMarket], min_liquidity: float) -> list[WeatherMarket]:
    return [m for m in markets if m.active and not m.closed and m.liquidity >= min_liquidity]
