from __future__ import annotations

from pathlib import Path

from weather_edge.clients import polymarket
from weather_edge.config import Settings


def _settings() -> Settings:
    return Settings(
        project_root=Path("."),
        db_path=Path(":memory:"),
        market_limit=2,
        market_scan_pages=2,
        use_fixtures=False,
    )


def _city_market(market_id: str, question_date: str = "May 3", end_date: str = "2026-05-03T12:00:00Z") -> dict:
    return {
        "id": market_id,
        "question": f"Will the highest temperature in Manila be 34°C on {question_date}?",
        "slug": f"highest-temperature-in-manila-{market_id}",
        "resolutionSource": "https://www.wunderground.com/history/daily/ph/manila/RPLL",
        "endDate": end_date,
        "active": True,
        "closed": False,
        "liquidity": "1200",
        "volume": "100",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.42", "0.58"]',
    }


def test_fetch_weather_markets_reads_recent_weather_events_before_deep_market_pages(monkeypatch):
    """Daily temperature markets live in recent event pages, not shallow /markets pages."""
    calls: list[tuple[str, dict]] = []

    def fake_get_json(url, params=None, **kwargs):
        calls.append((url, dict(params or {})))
        if url.endswith("/events"):
            return [
                {
                    "id": "event-1",
                    "slug": "highest-temperature-in-manila-on-may-3-2026",
                    "title": "Highest temperature in Manila on May 3?",
                    "markets": [_city_market("m-event")],
                }
            ]
        if url.endswith("/markets"):
            return []
        raise AssertionError(url)

    monkeypatch.setattr(polymarket, "get_json", fake_get_json)

    markets = polymarket.fetch_weather_markets(_settings())

    assert [m.market_id for m in markets] == ["m-event"]
    assert markets[0].raw["source"] == "polymarket_gamma_event"
    assert markets[0].raw["event_slug"] == "highest-temperature-in-manila-on-may-3-2026"
    event_calls = [params for url, params in calls if url.endswith("/events")]
    assert event_calls
    assert event_calls[0]["order"] == "createdAt"
    assert event_calls[0]["ascending"] == "false"
    assert event_calls[0]["active"] == "true"
    assert event_calls[0]["closed"] == "false"


def test_fetch_weather_markets_deduplicates_event_and_market_results(monkeypatch):
    def fake_get_json(url, params=None, **kwargs):
        if url.endswith("/events"):
            return [{"id": "event-1", "slug": "event", "title": "Event", "markets": [_city_market("same")]}]
        if url.endswith("/markets"):
            return [_city_market("same"), _city_market("other")]
        raise AssertionError(url)

    monkeypatch.setattr(polymarket, "get_json", fake_get_json)

    markets = polymarket.fetch_weather_markets(_settings())

    assert sorted(m.market_id for m in markets) == ["other", "same"]


def test_normalize_market_rejects_closed_or_inactive_weather_markets():
    closed = _city_market("closed") | {"closed": True}
    inactive = _city_market("inactive") | {"active": False}

    assert polymarket._normalize_market(closed) is None
    assert polymarket._normalize_market(inactive) is None


def test_fetch_market_by_id_allows_closed_weather_markets_for_settlement(monkeypatch):
    closed = _city_market("closed") | {"closed": True, "active": False, "outcomePrices": '["1", "0"]'}

    def fake_get_json(url, **kwargs):
        assert url.endswith("/markets/closed")
        return closed

    monkeypatch.setattr(polymarket, "get_json", fake_get_json)

    market = polymarket.fetch_market_by_id(_settings(), "closed")

    assert market is not None
    assert market.market_id == "closed"
    assert market.closed is True
    assert market.outcome_prices == [1.0, 0.0]


def test_parse_dt_makes_naive_gamma_dates_utc_aware():
    parsed = polymarket._parse_dt("2026-05-03T12:00:00")

    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.isoformat() == "2026-05-03T12:00:00+00:00"
