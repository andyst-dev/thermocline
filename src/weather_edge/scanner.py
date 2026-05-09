from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone

from .backtest import load_sigma_calibration, sigma_for_horizon_and_season
from .clients.aviationweather import observed_extreme_c, station_coords
from .clients.clob import simulate_buy_fill
from .clients.nasa_gistemp import global_temp_baseline
from .clients.openmeteo import fetch_hourly_forecast, geocode_city
from .clients.weathercom import icao_from_wunderground_source, official_extreme_c
from .config import Settings
from .timezones import timezone_hint_for_icao
from .ensemble import ensemble_bucket_probability, fetch_gfs_ensemble
from .models import BucketProbability, MarketContext, ScanResult, WeatherMarket
from .parsing import bucket_probability, parse_bucket, parse_city_and_date, parse_global_temperature_market, parse_metric_city_and_date, parse_temperature_contract
from .weather_features import build_weather_feature_bundle


class ScanSkip(Exception):
    pass


# Calibration constants for observed-value sigma.
# These encode empirically-estimated uncertainty components when a station
# observation is available (METAR or official source).
SIGMA_STATION = 0.30       # Instrument / reading noise at the station
# NOTE (2026-04-27): Original 0.72 was inflated by a lookback-window bug in
# aviationweather.py (truncated METAR fetch for Asia/South-America legacy trades).
# After fixing the bug, empirical std-dev on clean data is ~0.35-0.40°C.
# We use 0.50 as a conservative midpoint until more post-fix data accumulates.
SIGMA_DIVERGENCE = 0.50    # Official-source vs METAR divergence (cleaned)
SIGMA_ROUNDING = 0.25      # Resolution-method rounding uncertainty (Gamma half-step)


def _effective_sigma_observed(
    sigma_station: float = SIGMA_STATION,
    sigma_divergence: float = SIGMA_DIVERGENCE,
    sigma_rounding: float = SIGMA_ROUNDING,
) -> float:
    """Effective sigma when a station observation is available.

    Combines station instrument noise, source-METAR divergence,
    and resolution-method rounding into a single standard deviation
    via quadratic sum (assuming independent errors).

    With current defaults: sqrt(0.30² + 0.50² + 0.25²) ≈ 0.63 °C.
    Only valid when the local calendar day is complete and the station
    observation is the true daily extreme. Do not use for partial-day readings.
    """
    return math.sqrt(sigma_station ** 2 + sigma_divergence ** 2 + sigma_rounding ** 2)


def _local_day_complete(target_date: datetime, timezone_name: str, buffer_hours: float = 2.0) -> bool:
    """True when less than buffer_hours remain in the local calendar day."""
    try:
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(timezone_name)
        except Exception:
            import pytz
            tz = pytz.timezone(timezone_name)
    except Exception:
        import warnings
        warnings.warn(f"_local_day_complete: timezone {timezone_name!r} could not be resolved; treating day as incomplete", stacklevel=2)
        return False
    local_end = (
        datetime(target_date.year, target_date.month, target_date.day, tzinfo=tz)
        + timedelta(days=1)
        - timedelta(hours=buffer_hours)
    )
    return datetime.now(tz) >= local_end


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


def _resolve_forecast_coords(settings: Settings, location_name: str, icao: str | None) -> tuple[float, float, str]:
    if icao:
        coords = station_coords(icao)
        if coords:
            return coords["lat"], coords["lon"], timezone_hint_for_icao(icao)
    geo = geocode_city(settings, location_name)
    if not geo:
        raise ScanSkip(f"geocode failed for {location_name}")
    return float(geo["latitude"]), float(geo["longitude"]), str(geo.get("timezone") or "auto")


def _extract_context(settings: Settings, market: WeatherMarket, icao: str | None = None) -> MarketContext:
    parsed = parse_city_and_date(market.question)
    if not parsed:
        raise ScanSkip("question pattern unsupported")
    city, target_date = parsed
    lat, lon, tz = _resolve_forecast_coords(settings, city, icao)
    return MarketContext(
        market=market,
        city=city,
        target_date=target_date,
        latitude=lat,
        longitude=lon,
        timezone=tz,
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


def _sigma_for_horizon(target_date: datetime, settings: Settings | None = None) -> tuple[float, float]:
    horizon_hours = (target_date - datetime.now(timezone.utc)).total_seconds() / 3600
    if horizon_hours < 0:
        horizon_hours = 0.0
    sigma_c: float | None = None
    if settings is not None:
        calibration = load_sigma_calibration(settings.project_root)
        if calibration is not None:
            sigma_c = sigma_for_horizon_and_season(
                horizon_hours, target_date.date().isoformat(), calibration
            )
    if sigma_c is None:
        sigma_c = min(5.0, 1.5 + (horizon_hours / 72.0) * 2.5)
    return sigma_c, horizon_hours


def _scan_city_temperature_market(settings: Settings, market: WeatherMarket) -> tuple[ScanResult, dict]:
    resolution_source = str(market.raw.get("resolutionSource") or "")
    icao = icao_from_wunderground_source(resolution_source)
    context = _extract_context(settings, market, icao)
    parsed_metric = parse_metric_city_and_date(market.question)
    metric = parsed_metric[0] if parsed_metric else "highest"
    forecast = fetch_hourly_forecast(settings, context.latitude, context.longitude, context.timezone)
    forecast_max_c = _forecast_daily_extreme(forecast, context.target_date, metric)
    sigma_c, horizon_hours = _sigma_for_horizon(context.target_date, settings)

    # Fetch GFS ensemble for mid-to-long horizons where Gaussian sigma is regime-blind
    ensemble = None
    if horizon_hours > 36:
        ensemble = fetch_gfs_ensemble(context.latitude, context.longitude, context.target_date.date(), temperature_unit="celsius")

    buckets: list[BucketProbability] = []
    for label, market_prob in zip(market.outcomes, market.outcome_prices, strict=False):
        lower, upper = parse_bucket(label)
        model_prob_gaussian = _cap_model_prob(bucket_probability(lower, upper, forecast_max_c, sigma_c))
        model_prob = model_prob_gaussian
        model_prob_ensemble = None
        if ensemble:
            member_values = ensemble["member_maxs"] if metric == "highest" else ensemble["member_mins"]
            model_prob_ensemble = ensemble_bucket_probability(member_values, lower, upper)
            model_prob = model_prob_ensemble
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
                model_prob_gaussian=model_prob_gaussian,
                model_prob_ensemble=model_prob_ensemble,
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
    # Ensemble can upgrade confidence if spread is tight even at longer horizons
    if ensemble and ensemble.get("spread_max", 999) < 1.5 and horizon_hours <= 72:
        confidence = "high"
    elif ensemble and ensemble.get("spread_max", 999) < 2.0 and horizon_hours <= 96:
        confidence = "medium"

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
    forecast_sources = {"openmeteo": {"model": "gfs_seamless"}}
    if ensemble:
        forecast_sources["ensemble"] = {
            "model": "gfs_seamless",
            "spread_c": ensemble.get("spread_max") if metric == "highest" else ensemble.get("spread_min"),
            "spread_max_c": ensemble.get("spread_max"),
            "spread_min_c": ensemble.get("spread_min"),
            "num_members": ensemble.get("num_members"),
        }
    weather_features = build_weather_feature_bundle(
        settings.db_path,
        city=context.city,
        latitude=context.latitude,
        longitude=context.longitude,
        target_date=context.target_date,
        metric=metric,
        forecast_value_c=forecast_max_c,
        forecast_sources=forecast_sources,
    )
    return result, {
        "context": {
            "city": context.city,
            "latitude": context.latitude,
            "longitude": context.longitude,
            "timezone": context.timezone,
            "metric": metric,
        },
        "forecast": forecast,
        "weather_features": weather_features,
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
    _icao_for_resolution = resolution_location if re.fullmatch(r"[A-Z]{4}", resolution_location) else None
    _lat, _lon, timezone_name = _resolve_forecast_coords(settings, resolution_location, _icao_for_resolution)
    forecast = fetch_hourly_forecast(settings, _lat, _lon, timezone_name)
    forecast_value_c = _forecast_daily_extreme(forecast, contract.target_date, contract.metric)
    openmeteo_forecast_value_c = forecast_value_c
    sigma_c, horizon_hours = _sigma_for_horizon(contract.target_date, settings)

    # Fetch GFS ensemble for mid-to-long horizons
    ensemble = None
    if horizon_hours > 36:
        ensemble = fetch_gfs_ensemble(_lat, _lon, contract.target_date.date(), temperature_unit="celsius")

    observed_count = 0
    observed_authority = None
    observed_value = None
    day_complete = False
    _partial_obs_c = None
    resolution_source = str(market.raw.get("resolutionSource") or market.raw.get("resolution_source") or "")
    if re.fullmatch(r"[A-Z]{4}", resolution_location):
        day_complete = _local_day_complete(contract.target_date, timezone_name)
        if day_complete:
            if "wunderground.com" in resolution_source.lower() or "weather.com" in resolution_source.lower():
                observed_value, observed_count, _official_note = official_extreme_c(resolution_source, contract.target_date, contract.metric)
                if observed_value is not None:
                    observed_authority = "weathercom_wunderground"
            if observed_value is None:
                observed_value, observed_count = observed_extreme_c(resolution_location, contract.target_date, timezone_name, contract.metric)
                if observed_value is not None:
                    observed_authority = "metar"
            if observed_value is not None:
                forecast_value_c = observed_value
                sigma_c = _effective_sigma_observed()
        else:
            # Enhancement 3: fetch partial METAR reading for gap logging only; never used as forecast
            _partial_obs_c, _ = observed_extreme_c(resolution_location, contract.target_date, timezone_name, contract.metric)
    model_prob_gaussian = _cap_model_prob(bucket_probability(contract.lower_c, contract.upper_c, forecast_value_c, sigma_c))
    model_prob = model_prob_gaussian
    model_prob_ensemble = None
    if ensemble:
        member_values = ensemble["member_maxs"] if contract.metric == "highest" else ensemble["member_mins"]
        model_prob_ensemble = ensemble_bucket_probability(member_values, contract.lower_c, contract.upper_c)
        model_prob = model_prob_ensemble
    bucket_prob = model_prob

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
                model_prob_gaussian=model_prob_gaussian if label.lower() == "yes" else 1.0 - model_prob_gaussian,
                model_prob_ensemble=model_prob_ensemble if label.lower() == "yes" else (1.0 - model_prob_ensemble if model_prob_ensemble is not None else None),
            )
        )

    # Enhancement 1: near-expiry scalping — only fires when the local day is complete and
    # the true daily extreme is known. Uses min(0.99, ...) cap instead of the normal 0.95
    # to allow higher confidence when the outcome is observed.
    if day_complete and observed_value is not None:
        _mp_yes = bucket_probability(contract.lower_c, contract.upper_c, observed_value, sigma_c)
        _capped_yes = min(0.99, _mp_yes)
        _capped_no = min(0.99, 1.0 - _mp_yes)
        for _b in buckets:
            if _b.label.lower() == "yes":
                if _mp_yes > 0.90 and _b.market_prob < 0.95:
                    _b.strategy = "near_expiry_scalp"
                    _b.model_prob = _capped_yes
                    _b.model_prob_gaussian = _capped_yes
                    _b.model_prob_ensemble = None
                    _b.edge = _capped_yes - _b.market_prob
                    _b.ev = _capped_yes - _b.market_prob
            else:
                if _mp_yes < 0.10 and _b.market_prob < 0.95:
                    _b.strategy = "near_expiry_scalp"
                    _b.model_prob = _capped_no
                    _b.model_prob_gaussian = _capped_no
                    _b.model_prob_ensemble = None
                    _b.edge = _capped_no - _b.market_prob
                    _b.ev = _capped_no - _b.market_prob

    _enrich_with_clob(settings, market, buckets)
    _sort_buckets(buckets)
    top = buckets[0] if buckets else None
    confidence = "low"
    if sigma_c <= 2.5:
        confidence = "medium"
    if sigma_c <= 2.0 and horizon_hours <= 36:
        confidence = "high"
    # Ensemble confidence upgrade
    if ensemble and ensemble.get("spread_max", 999) < 1.5 and horizon_hours <= 72:
        confidence = "high"
    elif ensemble and ensemble.get("spread_max", 999) < 2.0 and horizon_hours <= 96:
        confidence = "medium"
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
    _context_meta: dict = {
        "city": contract.city,
        "latitude": _lat,
        "longitude": _lon,
        "timezone": timezone_name,
        "metric": contract.metric,
        "resolution_location": resolution_location,
        "observed_metar_count": observed_count,
        "observed_authority": observed_authority,
        "bucket_label": contract.label,
        "bucket_lower_c": contract.lower_c,
        "bucket_upper_c": contract.upper_c,
    }
    if _partial_obs_c is not None:
        # Enhancement 3: log gap between partial METAR and model forecast for later analysis
        _context_meta["observation_gap_c"] = _partial_obs_c - forecast_value_c
    forecast_sources = {"openmeteo": {"model": "gfs_seamless"}}
    if ensemble:
        forecast_sources["ensemble"] = {
            "model": "gfs_seamless",
            "spread_c": ensemble.get("spread_max") if contract.metric == "highest" else ensemble.get("spread_min"),
            "spread_max_c": ensemble.get("spread_max"),
            "spread_min_c": ensemble.get("spread_min"),
            "num_members": ensemble.get("num_members"),
        }
    weather_features = build_weather_feature_bundle(
        settings.db_path,
        city=contract.city,
        latitude=_lat,
        longitude=_lon,
        target_date=contract.target_date,
        metric=contract.metric,
        forecast_value_c=openmeteo_forecast_value_c,
        forecast_sources=forecast_sources,
    )
    return result, {"context": _context_meta, "forecast": forecast, "weather_features": weather_features}


def _scan_global_temperature_market(settings: Settings, market: WeatherMarket) -> tuple[ScanResult, dict]:
    parsed = parse_global_temperature_market(market.question)
    if not parsed:
        raise ScanSkip("question pattern unsupported")
    year, month, lower, upper = parsed
    try:
        baseline = global_temp_baseline(month)
    except Exception as exc:
        raise ScanSkip(f"GISTEMP baseline unavailable: {exc}") from exc
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


def filter_markets(markets: list[WeatherMarket], min_liquidity: float, now: datetime | None = None) -> list[WeatherMarket]:
    if now is None:
        now = datetime.now(timezone.utc)
    return [
        m
        for m in markets
        if m.active
        and not m.closed
        and m.liquidity >= min_liquidity
        and (m.end_date is None or m.end_date > now)
    ]
