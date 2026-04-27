"""Unit tests for the multi-horizon backtest framework."""
from __future__ import annotations

import math

import pytest

from weather_edge.backtest import (
    BacktestRecord,
    HORIZON_BUCKETS,
    aggregate_sigma,
    horizon_bucket,
    load_sigma_calibration,
    recalibrate_sigma,
    season_from_month,
    sigma_for_horizon_and_season,
)


class TestHorizonBucket:
    @pytest.mark.parametrize(
        "horizon,expected",
        [
            (0.0, "0-12h"),
            (6.0, "0-12h"),
            (11.999, "0-12h"),
            (12.0, "12-24h"),
            (23.5, "12-24h"),
            (24.0, "24-48h"),
            (36.0, "24-48h"),
            (48.0, "48-72h"),
            (72.0, "72-96h"),
            (96.0, "96-120h"),
            (120.0, "120h+"),
            (130.0, "120h+"),
            (240.0, "120h+"),
        ],
    )
    def test_buckets(self, horizon, expected):
        assert horizon_bucket(horizon) == expected


class TestSeasonFromMonth:
    @pytest.mark.parametrize(
        "month,expected",
        [
            (12, "winter"),
            (1, "winter"),
            (2, "winter"),
            (3, "spring"),
            (4, "spring"),
            (5, "spring"),
            (6, "summer"),
            (7, "summer"),
            (8, "summer"),
            (9, "autumn"),
            (10, "autumn"),
            (11, "autumn"),
        ],
    )
    def test_each_month(self, month, expected):
        assert season_from_month(month) == expected

    def test_invalid_month_raises(self):
        with pytest.raises(ValueError):
            season_from_month(0)
        with pytest.raises(ValueError):
            season_from_month(13)


def _make_record(horizon: float, residual: float, target_date: str = "2026-04-15") -> BacktestRecord:
    return BacktestRecord(
        city="Test",
        latitude=0.0,
        longitude=0.0,
        target_date=target_date,
        reference_date="2026-04-14",
        horizon_hours=horizon,
        forecast_max_c=20.0 + residual,
        observed_max_c=20.0,
        residual_c=residual,
        metric="highest",
        model_source="openmeteo_gfs_historical",
        fetched_at="2026-04-27T00:00:00+00:00",
    )


class TestAggregateSigma:
    def test_empty_records_returns_empty_aggregates(self):
        agg = aggregate_sigma([])
        assert agg["total_records"] == 0
        assert agg["overall"]["count"] == 0
        assert agg["overall"]["sigma_c"] is None
        for label, _, _ in HORIZON_BUCKETS:
            assert agg["by_horizon"][label]["count"] == 0
        for season in ("winter", "spring", "summer", "autumn"):
            assert agg["by_season"][season]["count"] == 0
        assert agg["by_horizon_season"] == {}

    def test_sigma_per_bucket_with_synthetic_records(self):
        records = [
            _make_record(24, 1.0),
            _make_record(36, -1.0),
            _make_record(72, 5.0),
            _make_record(96, -5.0),
        ]
        agg = aggregate_sigma(records)
        assert agg["total_records"] == 4
        bucket_24_48 = agg["by_horizon"]["24-48h"]
        assert bucket_24_48["count"] == 2
        assert bucket_24_48["sigma_c"] == pytest.approx(1.0, rel=1e-6)
        assert bucket_24_48["mean_residual_c"] == pytest.approx(0.0, abs=1e-9)
        bucket_72_96 = agg["by_horizon"]["72-96h"]
        assert bucket_72_96["count"] == 1
        assert bucket_72_96["sigma_c"] == pytest.approx(0.0)
        bucket_96_120 = agg["by_horizon"]["96-120h"]
        assert bucket_96_120["count"] == 1

    def test_season_grouping(self):
        records = [
            _make_record(24, 1.0, target_date="2026-01-15"),
            _make_record(24, -1.0, target_date="2026-02-10"),
            _make_record(24, 2.0, target_date="2026-07-01"),
        ]
        agg = aggregate_sigma(records)
        assert agg["by_season"]["winter"]["count"] == 2
        assert agg["by_season"]["winter"]["sigma_c"] == pytest.approx(1.0, rel=1e-6)
        assert agg["by_season"]["summer"]["count"] == 1
        assert agg["by_season"]["spring"]["count"] == 0
        assert agg["by_season"]["autumn"]["count"] == 0

    def test_horizon_season_cross_grouping_keys(self):
        records = [
            _make_record(24, 1.0, target_date="2026-01-15"),
            _make_record(96, 2.0, target_date="2026-07-01"),
        ]
        agg = aggregate_sigma(records)
        assert "24-48h|winter" in agg["by_horizon_season"]
        assert agg["by_horizon_season"]["24-48h|winter"]["count"] == 1
        assert "96-120h|summer" in agg["by_horizon_season"]
        assert agg["by_horizon_season"]["96-120h|summer"]["count"] == 1

    def test_median_abs_error(self):
        records = [
            _make_record(24, 3.0),
            _make_record(36, -3.0),
            _make_record(36, 7.0),
        ]
        agg = aggregate_sigma(records)
        bucket = agg["by_horizon"]["24-48h"]
        assert bucket["count"] == 3
        assert bucket["median_abs_error_c"] == pytest.approx(3.0)

    def test_overall_includes_all_records(self):
        residuals = [0.5, -0.5, 2.0, -2.0]
        records = [_make_record(24 + i * 24, r) for i, r in enumerate(residuals)]
        agg = aggregate_sigma(records)
        assert agg["overall"]["count"] == 4
        assert agg["overall"]["mean_residual_c"] == pytest.approx(0.0, abs=1e-9)
        assert agg["overall"]["min_residual_c"] == pytest.approx(-2.0)
        assert agg["overall"]["max_residual_c"] == pytest.approx(2.0)


class TestSigmaForHorizonAndSeason:
    def test_uses_calibration_when_enough_samples(self):
        calibration = {
            "by_horizon_season": {
                "24-48h|spring": {"count": 5, "sigma_c": 2.3},
            },
            "by_horizon": {},
        }
        result = sigma_for_horizon_and_season(36.0, "2026-04-15", calibration)
        assert result == pytest.approx(2.3)

    def test_falls_back_to_horizon_only(self):
        calibration = {
            "by_horizon_season": {
                "24-48h|spring": {"count": 2, "sigma_c": 1.5},
            },
            "by_horizon": {
                "24-48h": {"count": 12, "sigma_c": 2.5},
            },
        }
        result = sigma_for_horizon_and_season(36.0, "2026-04-15", calibration)
        assert result == pytest.approx(2.5)

    def test_returns_none_when_insufficient_data(self):
        calibration = {
            "by_horizon_season": {},
            "by_horizon": {
                "24-48h": {"count": 3, "sigma_c": 2.0},
            },
        }
        result = sigma_for_horizon_and_season(36.0, "2026-04-15", calibration)
        assert result is None

    def test_returns_none_when_calibration_is_none(self):
        assert sigma_for_horizon_and_season(36.0, "2026-04-15", None) is None


class TestRecalibrateSigma:
    def test_creates_json_file(self, tmp_path):
        db_path = tmp_path / "test.db"
        import sqlite3

        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE backtest_records (
                id INTEGER PRIMARY KEY,
                city TEXT, latitude REAL, longitude REAL,
                target_date TEXT, reference_date TEXT,
                horizon_hours REAL, forecast_max_c REAL,
                observed_max_c REAL, residual_c REAL,
                metric TEXT, model_source TEXT,
                fetched_at TEXT, created_at TEXT
            );
        """)
        conn.execute(
            "INSERT INTO backtest_records (city, latitude, longitude, target_date, reference_date, "
            "horizon_hours, forecast_max_c, observed_max_c, residual_c, metric, model_source, fetched_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("TestCity", 0.0, 0.0, "2026-04-20", "2026-04-19", 24.0, 22.0, 20.0, 2.0, "highest", "live_scanner", "2026-04-19T00:00:00", "2026-04-19T00:00:00"),
        )
        conn.execute(
            "INSERT INTO backtest_records (city, latitude, longitude, target_date, reference_date, "
            "horizon_hours, forecast_max_c, observed_max_c, residual_c, metric, model_source, fetched_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("TestCity", 0.0, 0.0, "2026-04-21", "2026-04-20", 48.0, 19.0, 20.0, -1.0, "highest", "live_scanner", "2026-04-20T00:00:00", "2026-04-20T00:00:00"),
        )
        conn.commit()
        conn.close()

        aggregates = recalibrate_sigma(db_path, tmp_path, lookback_days=60)
        calibration_path = tmp_path / "data" / "sigma_calibration.json"
        assert calibration_path.exists()
        loaded = load_sigma_calibration(tmp_path)
        assert loaded is not None
        assert loaded["total_records"] == 2
        assert "calibrated_at" in loaded
        assert loaded["lookback_days"] == 60
        assert loaded["by_horizon"]["24-48h"]["count"] == 1
        assert loaded["by_horizon"]["48-72h"]["count"] == 1
