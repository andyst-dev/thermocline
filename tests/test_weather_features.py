from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from weather_edge.config import Settings
from weather_edge.db import init_db
from weather_edge.models import WeatherMarket
from weather_edge.scanner import scan_market
from weather_edge.weather_features import (
    build_weather_feature_bundle,
    compute_recent_bias_14d,
    compute_station_climatology,
    fetch_nws_discussion,
)


def _insert_backtest(
    db_path: Path,
    *,
    city: str = "Paris",
    target_date: str,
    residual_c: float,
    observed_max_c: float,
    metric: str = "highest",
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO backtest_records(
                city, latitude, longitude, target_date, reference_date, horizon_hours,
                forecast_max_c, observed_max_c, residual_c, metric, model_source, fetched_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                city,
                46.2,
                6.1,
                target_date,
                target_date,
                24.0,
                observed_max_c + residual_c,
                observed_max_c,
                residual_c,
                metric,
                "test_model",
                "2026-05-01T00:00:00+00:00",
                "2026-05-01T00:00:00+00:00",
            ),
        )


def test_recent_bias_14d_uses_only_recent_same_city_metric_records(tmp_path: Path) -> None:
    db_path = tmp_path / "weather_edge.db"
    init_db(db_path)
    _insert_backtest(db_path, city="Paris", target_date="2026-04-20", residual_c=1.0, observed_max_c=18.0)
    _insert_backtest(db_path, city="Paris", target_date="2026-04-25", residual_c=2.0, observed_max_c=20.0)
    _insert_backtest(db_path, city="Paris", target_date="2026-04-01", residual_c=99.0, observed_max_c=20.0)
    _insert_backtest(db_path, city="Paris", target_date="2026-04-26", residual_c=99.0, observed_max_c=20.0, metric="lowest")
    _insert_backtest(db_path, city="London", target_date="2026-04-26", residual_c=99.0, observed_max_c=20.0)

    bias = compute_recent_bias_14d(db_path, city="Paris", target_date="2026-05-01", metric="highest", min_samples=2)

    assert bias == {
        "count": 2,
        "mean_residual_c": pytest.approx(1.5),
        "window_days": 14,
        "source": "backtest_records",
    }


def test_recent_bias_14d_returns_none_when_sample_too_small(tmp_path: Path) -> None:
    db_path = tmp_path / "weather_edge.db"
    init_db(db_path)
    _insert_backtest(db_path, target_date="2026-04-25", residual_c=1.0, observed_max_c=18.0)

    assert compute_recent_bias_14d(db_path, city="Paris", target_date="2026-05-01", metric="highest", min_samples=2) is None


def test_station_climatology_uses_same_city_metric_and_day_of_year_window(tmp_path: Path) -> None:
    db_path = tmp_path / "weather_edge.db"
    init_db(db_path)
    _insert_backtest(db_path, target_date="2024-04-30", residual_c=0.0, observed_max_c=19.0)
    _insert_backtest(db_path, target_date="2025-05-02", residual_c=0.0, observed_max_c=21.0)
    _insert_backtest(db_path, target_date="2026-05-01", residual_c=0.0, observed_max_c=99.0)
    _insert_backtest(db_path, target_date="2025-07-01", residual_c=0.0, observed_max_c=99.0)

    clim = compute_station_climatology(db_path, city="Paris", target_date="2026-05-01", metric="highest", day_window=3, min_samples=2)

    assert clim is not None
    assert clim["count"] == 2
    assert clim["mean_observed_c"] == pytest.approx(20.0)
    assert clim["std_observed_c"] == pytest.approx(1.0)
    assert clim["day_window"] == 3


def test_feature_bundle_applies_recent_bias_and_logs_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "weather_edge.db"
    init_db(db_path)
    _insert_backtest(db_path, target_date="2026-04-20", residual_c=1.0, observed_max_c=18.0)
    _insert_backtest(db_path, target_date="2026-04-25", residual_c=2.0, observed_max_c=20.0)
    _insert_backtest(db_path, target_date="2025-05-01", residual_c=0.0, observed_max_c=21.0)
    _insert_backtest(db_path, target_date="2024-05-01", residual_c=0.0, observed_max_c=19.0)

    monkeypatch.setattr(
        "weather_edge.weather_features.fetch_nws_discussion",
        lambda latitude, longitude, timeout=10: {"office": "PHI", "text": "Cold front timing uncertain.", "issued_at": "2026-05-01T00:00:00+00:00"},
    )

    bundle = build_weather_feature_bundle(
        db_path,
        city="Paris",
        latitude=46.2,
        longitude=6.1,
        target_date=datetime(2026, 5, 1, tzinfo=timezone.utc),
        metric="highest",
        forecast_value_c=25.0,
        forecast_sources={"openmeteo": {"model": "gfs_seamless"}},
    )

    assert bundle["recent_bias_14d"]["mean_residual_c"] == pytest.approx(1.5)
    assert bundle["bias_corrected_forecast_c"] == pytest.approx(23.5)
    assert bundle["station_climatology"]["mean_observed_c"] == pytest.approx(20.0)
    assert bundle["nws_discussion"]["text"] == "Cold front timing uncertain."
    assert bundle["forecast_sources"]["openmeteo"]["model"] == "gfs_seamless"
    assert bundle["forecast_sources"]["nws_discussion"]["available"] is True


def test_nws_discussion_short_circuits_non_us_coordinates(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_urlopen(*args, **kwargs):
        raise AssertionError("non-US coordinates should not call weather.gov")

    monkeypatch.setattr("weather_edge.weather_features.urlopen", fail_urlopen)

    assert fetch_nws_discussion(48.8566, 2.3522) is None


def test_scan_city_temperature_market_logs_weather_features(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "weather_edge.db"
    init_db(db_path)
    settings = Settings(project_root=tmp_path, db_path=db_path)
    market = WeatherMarket(
        market_id="m-city",
        slug="paris-daily-range",
        question="What will be the highest temperature in Paris on May 15?",
        end_date=datetime(2026, 5, 15, 23, 59, tzinfo=timezone.utc),
        active=True,
        closed=False,
        liquidity=1000.0,
        volume=100.0,
        outcomes=["18-22", "23-27"],
        outcome_prices=[0.5, 0.5],
        raw={},
    )

    monkeypatch.setattr("weather_edge.scanner.geocode_city", lambda *args, **kwargs: {"latitude": 48.8566, "longitude": 2.3522, "timezone": "Europe/Paris"})
    monkeypatch.setattr("weather_edge.scanner.fetch_hourly_forecast", lambda *args, **kwargs: {
        "hourly": {
            "time": ["2026-05-15T00:00", "2026-05-15T12:00"],
            "temperature_2m": [18.0, 22.0],
        }
    })
    monkeypatch.setattr("weather_edge.scanner.fetch_gfs_ensemble", lambda *args, **kwargs: {"spread_max": 3.0, "spread_min": 2.5, "num_members": 31, "member_maxs": [22.0, 25.0], "member_mins": [18.0, 17.0]})
    monkeypatch.setattr("weather_edge.scanner.simulate_buy_fill", lambda *args, **kwargs: type("Fill", (), {
        "best_bid": None,
        "best_ask": None,
        "capacity_usd_at_best_ask": None,
        "avg_price": None,
        "shares": None,
        "cost_usd": None,
        "levels_used": [],
        "book_fetched_at": None,
        "filled": False,
    })())
    captured_sources = {}

    def fake_weather_bundle(*args, **kwargs):
        captured_sources.update(kwargs.get("forecast_sources") or {})
        return {"forecast_sources": kwargs.get("forecast_sources") or {}}

    monkeypatch.setattr("weather_edge.scanner.build_weather_feature_bundle", fake_weather_bundle)

    result, meta = scan_market(settings, market)

    assert result.forecast_max_c == pytest.approx(22.0)
    assert meta["weather_features"]["forecast_sources"]["openmeteo"]["model"] == "gfs_seamless"
    assert captured_sources["ensemble"]["spread_c"] == pytest.approx(3.0)
    assert captured_sources["ensemble"]["num_members"] == 31


def test_scan_market_logs_weather_features_without_using_bias_for_probability(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "weather_edge.db"
    init_db(db_path)
    settings = Settings(project_root=tmp_path, db_path=db_path)
    market = WeatherMarket(
        market_id="m-1",
        slug="paris-temp",
        question="Will the highest temperature in Paris be 20°C on May 15?",
        end_date=datetime(2026, 5, 15, 23, 59, tzinfo=timezone.utc),
        active=True,
        closed=False,
        liquidity=1000.0,
        volume=100.0,
        outcomes=["Yes", "No"],
        outcome_prices=[0.5, 0.5],
        raw={"resolutionSource": "https://example.com/KJFK"},
    )

    monkeypatch.setattr("weather_edge.scanner.station_coords", lambda icao: {"lat": 40.64, "lon": -73.78})
    monkeypatch.setattr("weather_edge.scanner.fetch_hourly_forecast", lambda *args, **kwargs: {
        "hourly": {
                "time": ["2026-05-15T00:00", "2026-05-15T12:00"],
            "temperature_2m": [18.0, 22.0],
        }
    })
    monkeypatch.setattr("weather_edge.scanner.fetch_gfs_ensemble", lambda *args, **kwargs: None)
    monkeypatch.setattr("weather_edge.scanner.simulate_buy_fill", lambda *args, **kwargs: type("Fill", (), {
        "best_bid": None,
        "best_ask": None,
        "capacity_usd_at_best_ask": None,
        "avg_price": None,
        "shares": None,
        "cost_usd": None,
        "levels_used": [],
        "book_fetched_at": None,
        "filled": False,
    })())
    monkeypatch.setattr("weather_edge.scanner.build_weather_feature_bundle", lambda *args, **kwargs: {
        "recent_bias_14d": {"mean_residual_c": 2.0, "count": 4},
        "bias_corrected_forecast_c": 20.0,
        "forecast_sources": {"openmeteo": {"model": "gfs_seamless"}},
    })

    result, meta = scan_market(settings, market)

    assert result.forecast_max_c == pytest.approx(22.0)
    assert meta["weather_features"]["bias_corrected_forecast_c"] == pytest.approx(20.0)
    assert meta["weather_features"]["recent_bias_14d"]["mean_residual_c"] == pytest.approx(2.0)
