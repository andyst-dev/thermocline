from __future__ import annotations

from datetime import datetime
from typing import Any

from ..config import Settings
from ..fixtures import sample_weather_markets
from ..http import get_json
from ..models import WeatherMarket

WEATHER_KEYWORDS = (
    "highest temperature",
    "temperature in",
    "weather",
    "temp in",
)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_market(raw: dict[str, Any]) -> WeatherMarket | None:
    question = (raw.get("question") or raw.get("title") or "").strip()
    question_lc = question.lower()
    if not any(keyword in question_lc for keyword in WEATHER_KEYWORDS):
        return None

    outcomes_raw = raw.get("outcomes") or []
    prices_raw = raw.get("outcomePrices") or []

    if isinstance(outcomes_raw, str):
        import json
        outcomes_raw = json.loads(outcomes_raw)
    if isinstance(prices_raw, str):
        import json
        prices_raw = json.loads(prices_raw)

    outcomes = [str(x).strip() for x in outcomes_raw]
    outcome_prices = [_to_float(x) for x in prices_raw]

    if not outcomes or not outcome_prices or len(outcomes) != len(outcome_prices):
        return None

    market_id = str(raw.get("id") or raw.get("conditionId") or raw.get("slug") or question)
    return WeatherMarket(
        market_id=market_id,
        slug=str(raw.get("slug") or market_id),
        question=question,
        end_date=_parse_dt(raw.get("endDate") or raw.get("end_date_iso")),
        active=bool(raw.get("active", False)),
        closed=bool(raw.get("closed", False)),
        liquidity=_to_float(raw.get("liquidity")),
        volume=_to_float(raw.get("volume") or raw.get("volumeNum")),
        outcomes=outcomes,
        outcome_prices=outcome_prices,
        raw=raw,
    )


def fetch_weather_markets(settings: Settings) -> list[WeatherMarket]:
    try:
        payload = get_json(
            f"{settings.polymarket_gamma_url}/markets",
            params={
                "limit": settings.market_limit,
                "closed": "false",
                "active": "true",
            },
        )
    except Exception:
        return sample_weather_markets()

    if not isinstance(payload, list):
        return sample_weather_markets()
    markets: list[WeatherMarket] = []
    for raw in payload:
        market = _normalize_market(raw)
        if market is not None:
            markets.append(market)
    return markets or sample_weather_markets()
