from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from ..config import Settings
from ..fixtures import sample_weather_markets
from ..http import get_json
from ..models import WeatherMarket

# Keep this deliberately strict. Broad words like "weather" or "storm" create
# bad matches (e.g. Carolina Hurricanes, geopolitical descriptions, etc.).
SCANNABLE_CITY_TEMP_RE = re.compile(
    r"(?:highest|lowest) temperature in\s+.+?\s+be\s+.+?\s+on\s+[A-Za-z]+\s+\d{1,2}",
    flags=re.IGNORECASE,
)
GLOBAL_TEMP_RE = re.compile(
    r"global temperature increase .* in [A-Za-z]+ \d{4}",
    flags=re.IGNORECASE,
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


def _parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _is_weather_market(raw: dict[str, Any]) -> bool:
    question = (raw.get("question") or raw.get("title") or "").strip()
    return bool(SCANNABLE_CITY_TEMP_RE.search(question) or GLOBAL_TEMP_RE.search(question))


def _normalize_market(raw: dict[str, Any]) -> WeatherMarket | None:
    question = (raw.get("question") or raw.get("title") or "").strip()
    if not question or not _is_weather_market(raw):
        return None

    outcomes = [str(x).strip() for x in _parse_json_list(raw.get("outcomes"))]
    outcome_prices = [_to_float(x) for x in _parse_json_list(raw.get("outcomePrices"))]

    if not outcomes or not outcome_prices or len(outcomes) != len(outcome_prices):
        return None

    market_id = str(raw.get("id") or raw.get("conditionId") or raw.get("slug") or question)
    normalized_raw = dict(raw)
    normalized_raw.setdefault("source", "polymarket_gamma")
    return WeatherMarket(
        market_id=market_id,
        slug=str(raw.get("slug") or market_id),
        question=question,
        end_date=_parse_dt(raw.get("endDate") or raw.get("end_date_iso") or raw.get("endDateIso")),
        active=bool(raw.get("active", False)),
        closed=bool(raw.get("closed", False)),
        liquidity=_to_float(raw.get("liquidity") or raw.get("liquidityNum")),
        volume=_to_float(raw.get("volume") or raw.get("volumeNum")),
        outcomes=outcomes,
        outcome_prices=outcome_prices,
        raw=normalized_raw,
    )


def _fetch_gamma_page(settings: Settings, offset: int) -> list[dict[str, Any]]:
    payload = get_json(
        f"{settings.polymarket_gamma_url}/markets",
        params={
            "limit": settings.market_limit,
            "offset": offset,
            "closed": "false",
            "active": "true",
        },
    )
    return payload if isinstance(payload, list) else []


def fetch_market_by_id(settings: Settings, market_id: str) -> WeatherMarket | None:
    if settings.use_fixtures:
        return None
    payload = get_json(f"{settings.polymarket_gamma_url}/markets/{market_id}", timeout=30)
    if not isinstance(payload, dict):
        return None
    return _normalize_market(payload)


def fetch_weather_markets(settings: Settings) -> list[WeatherMarket]:
    if settings.use_fixtures:
        return sample_weather_markets()

    markets_by_id: dict[str, WeatherMarket] = {}
    try:
        for page in range(settings.market_scan_pages):
            offset = page * settings.market_limit
            payload = _fetch_gamma_page(settings, offset)
            if not payload:
                break
            for raw in payload:
                market = _normalize_market(raw)
                if market is not None:
                    markets_by_id[market.market_id] = market
    except Exception as exc:
        # Network/API failure: keep local development usable, but make it visible.
        print(f"WARNING: Polymarket API failure ({exc}); using fixtures", flush=True)
        return sample_weather_markets()

    return list(markets_by_id.values())
