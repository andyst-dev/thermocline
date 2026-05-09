from __future__ import annotations

from datetime import datetime, timezone

from weather_edge.clients import weathercom


def test_official_extreme_uses_hourly_samples_when_aggregate_matches(monkeypatch) -> None:
    def fake_fetch(icao, target_date, units="m", country="US"):
        return {
            "observations": [
                {"temp": 10, "max_temp": 14},
                {"temp": 14},
                {"temp": 12},
            ]
        }

    monkeypatch.setattr(weathercom, "fetch_historical_observations", fake_fetch)

    observed, count, note = weathercom.official_extreme_c(
        "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA",
        datetime(2026, 4, 25, tzinfo=timezone.utc),
        "highest",
    )

    assert observed == 14
    assert count == 3
    assert "hourly obs" in note


def test_official_extreme_rejects_divergent_daily_aggregate(monkeypatch) -> None:
    def fake_fetch(icao, target_date, units="m", country="US"):
        return {
            "observations": [
                {"temp": 10, "max_temp": 18},
                {"temp": 9},
                {"temp": 8},
            ]
        }

    monkeypatch.setattr(weathercom, "fetch_historical_observations", fake_fetch)

    observed, count, note = weathercom.official_extreme_c(
        "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA",
        datetime(2026, 4, 25, tzinfo=timezone.utc),
        "highest",
    )

    assert observed is None
    assert count == 3
    assert "aggregate/sample divergence" in note
    assert "aggregate=18.0C samples=10.0C" in note


def test_official_extreme_rejects_divergent_min_aggregate(monkeypatch) -> None:
    def fake_fetch(icao, target_date, units="m", country="KR"):
        return {
            "observations": [
                {"temp": 8, "min_temp": 4},
                {"temp": 9},
                {"temp": 11},
            ]
        }

    monkeypatch.setattr(weathercom, "fetch_historical_observations", fake_fetch)

    observed, count, note = weathercom.official_extreme_c(
        "https://www.wunderground.com/history/daily/kr/incheon/RKSI",
        datetime(2026, 4, 25, tzinfo=timezone.utc),
        "lowest",
    )

    assert observed is None
    assert count == 3
    assert "aggregate/sample divergence" in note
    assert "RKSI:9:KR" in note
