from __future__ import annotations

from datetime import datetime, timezone

from weather_edge.models import WeatherMarket
from weather_edge.scanner import filter_markets


def _market(market_id: str, *, end_date: datetime | None, active: bool = True, closed: bool = False, liquidity: float = 100.0) -> WeatherMarket:
    return WeatherMarket(
        market_id=market_id,
        slug=market_id,
        question="Will the highest temperature in Manila be 34°C on May 3?",
        end_date=end_date,
        active=active,
        closed=closed,
        liquidity=liquidity,
        volume=100.0,
        outcomes=["Yes", "No"],
        outcome_prices=[0.5, 0.5],
        raw={},
    )


def test_filter_markets_excludes_past_end_date():
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    markets = [
        _market("past", end_date=datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc)),
        _market("future", end_date=datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc)),
    ]

    filtered = filter_markets(markets, min_liquidity=50.0, now=now)

    assert [m.market_id for m in filtered] == ["future"]


def test_filter_markets_keeps_no_end_date_for_non_daily_markets():
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    market = _market("no-end", end_date=None)

    assert filter_markets([market], min_liquidity=50.0, now=now) == [market]
