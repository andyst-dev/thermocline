"""Unit tests for the multi-horizon backtest framework."""
from __future__ import annotations

import math

import pytest

from weather_edge.backtest import (
    BacktestRecord,
    HORIZON_BUCKETS,
    aggregate_sigma,
    horizon_bucket,
    season_from_month,
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
