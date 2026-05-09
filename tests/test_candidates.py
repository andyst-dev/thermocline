"""Tests unitaires pour weather_edge.candidates."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from weather_edge.candidates import Candidate, build_candidate
from weather_edge.config import Settings
from weather_edge.models import BucketProbability, ScanResult, WeatherMarket


def _make_market(question: str = "Will the highest temperature in Paris be 20°C on May 5?", **kwargs) -> WeatherMarket:
    """Helper pour créer un WeatherMarket minimal."""
    defaults = {
        "market_id": "test-id-1",
        "slug": "test-slug",
        "question": question,
        "end_date": datetime(2026, 5, 5, 23, 59, tzinfo=timezone.utc),
        "active": True,
        "closed": False,
        "liquidity": 1000.0,
        "volume": 500.0,
        "outcomes": ["Yes", "No"],
        "outcome_prices": [0.50, 0.50],
        "raw": {
            "resolutionSource": "https://www.wunderground.com/history/daily/KJFK/date/2026-05-05",
        },
    }
    defaults.update(kwargs)
    return WeatherMarket(**defaults)


def _make_settings() -> Settings:
    return Settings(
        project_root=Path("/tmp/weather-edge-test"),
        db_path=Path("/tmp/weather-edge-test.db"),
        kelly_fraction=0.25,
        kelly_bankroll_usd=100.0,
        min_position_size_usd=1.0,
        max_position_size_usd=50.0,
    )


def _make_scan_result(
    buckets: list[BucketProbability] | None = None,
    confidence: str = "high",
    horizon_hours: float = 24.0,
    sigma_c: float = 1.8,
    city: str = "Paris",
    **kwargs,
) -> ScanResult:
    defaults = {
        "market_id": "test-id-1",
        "slug": "test-slug",
        "question": "Will the highest temperature in Paris be 20°C on May 5?",
        "city": city,
        "target_date": "2026-05-05",
        "forecast_max_c": 20.0,
        "sigma_c": sigma_c,
        "horizon_hours": horizon_hours,
        "liquidity": 1000.0,
        "buckets": buckets or [],
        "top_bucket_label": None,
        "top_bucket_ev": None,
        "confidence": confidence,
    }
    defaults.update(kwargs)
    return ScanResult(**defaults)


def _make_bucket(
    label: str = "Yes",
    lower: float | None = 19.5,
    upper: float | None = 20.5,
    market_prob: float = 0.50,
    model_prob: float = 0.80,
    executable_ev: float | None = 0.30,
    best_ask: float | None = 0.05,
    fill_cost_usd: float | None = 1.50,
    **kwargs,
) -> BucketProbability:
    defaults = {
        "label": label,
        "lower": lower,
        "upper": upper,
        "market_prob": market_prob,
        "model_prob": model_prob,
        "edge": model_prob - market_prob,
        "ev": model_prob - market_prob,
    }
    if executable_ev is not None:
        defaults["executable_ev"] = executable_ev
    if best_ask is not None:
        defaults["best_ask"] = best_ask
    if fill_cost_usd is not None:
        defaults["fill_cost_usd"] = fill_cost_usd
    defaults.update(kwargs)
    return BucketProbability(**defaults)


class TestBuildCandidate:
    """Tests de build_candidate() avec différentes configurations."""

    def test_wide_bucket_pass(self):
        """Bucket large (width=3.0), EV OK, tout vert → PASS."""
        bucket = _make_bucket(lower=17.5, upper=20.5)  # width = 3.0
        scan = _make_scan_result(buckets=[bucket, _make_bucket(label="No", lower=None, upper=None, model_prob=0.20, executable_ev=None, best_ask=None)])
        meta = {"context": {"bucket_lower_c": 17.5, "bucket_upper_c": 20.5, "observed_metar_count": 6, "observed_authority": "weathercom_wunderground", "resolution_location": "KJFK"}}
        candidate = build_candidate(_make_market(), scan, meta)
        assert candidate.verdict == "PASS"
        assert candidate.bucket_width_c == pytest.approx(3.0, abs=0.01)
        assert candidate.score > 0

    def test_calibration_gate_blocks_pass_candidates(self):
        """Une calibration non fiable bloque le PASS pré-trade."""
        bucket = _make_bucket(lower=17.5, upper=20.5)
        scan = _make_scan_result(buckets=[bucket, _make_bucket(label="No", lower=None, upper=None, model_prob=0.20, executable_ev=None, best_ask=None)])
        meta = {"context": {"bucket_lower_c": 17.5, "bucket_upper_c": 20.5, "observed_metar_count": 6, "observed_authority": "weathercom_wunderground", "resolution_location": "KJFK"}}
        gate = {"allowed": False, "reasons": ["Brier score too high: 0.8604 > 0.3000"]}

        candidate = build_candidate(_make_market(), scan, meta, calibration_gate=gate)

        assert candidate.verdict == "REJECT"
        assert "calibration gate blocked" in candidate.reason
        assert "Brier score too high" in candidate.reason

    def test_recommended_size_uses_horizon_and_uncertainty_scale(self):
        """Le sizing applique le scale-in temporel + régime météo, pas juste Kelly brut."""
        bucket = _make_bucket(lower=17.5, upper=20.5, market_prob=0.50, model_prob=0.80, best_ask=0.50, fill_avg_price=0.50)
        scan = _make_scan_result(buckets=[bucket], horizon_hours=72, sigma_c=3.2)
        meta = {
            "context": {"bucket_lower_c": 17.5, "bucket_upper_c": 20.5, "observed_metar_count": 6, "observed_authority": "weathercom_wunderground", "resolution_location": "KJFK"},
            "weather_features": {"forecast_sources": {"ensemble": {"spread_c": 3.0, "num_members": 31}}},
        }

        candidate = build_candidate(_make_market(), scan, meta, settings=_make_settings())

        # Kelly brut: p=0.80, price=0.50 => $15. PolyDekos scale: 72h=0.30, high regime=0.40 => $1.80.
        assert candidate.recommended_size_usd == pytest.approx(1.8)
        assert candidate.regime_uncertainty["level"] == "high"

    def test_forecast_drift_blocks_full_size_scale_in(self):
        """Un drift forecast >1°C empêche le passage full-size près de l'expiry."""
        bucket = _make_bucket(lower=17.5, upper=20.5, market_prob=0.50, model_prob=0.80, best_ask=0.50, fill_avg_price=0.50)
        scan = _make_scan_result(buckets=[bucket], horizon_hours=4, sigma_c=1.2, forecast_max_c=21.3)
        meta = {
            "context": {
                "bucket_lower_c": 17.5,
                "bucket_upper_c": 20.5,
                "observed_metar_count": 6,
                "observed_authority": "weathercom_wunderground",
                "resolution_location": "KJFK",
                "initial_forecast_c": 20.0,
            },
            "weather_features": {"forecast_sources": {"ensemble": {"spread_c": 0.4, "num_members": 31}}},
        }

        candidate = build_candidate(_make_market(), scan, meta, settings=_make_settings())

        # Sans gate stabilité: 4h => horizon scale 1.0, low regime 1.0, donc $15.
        # Avec drift 1.3°C: reste starter à 30%, donc $4.50 et pas de PASS nu.
        assert candidate.recommended_size_usd == pytest.approx(4.5)
        assert candidate.verdict == "PAPER"
        assert "forecast drift" in candidate.reason

    def test_high_uncertainty_downgrades_pass_and_logs_tail_hedge_plan(self):
        """Régime météo incertain: pas de PASS nu, et tail hedge cheap loggé."""
        bucket = _make_bucket(label="18-22", lower=18.0, upper=22.0, model_prob=0.80, market_prob=0.50, executable_ev=0.30, best_ask=0.05)
        low_tail = _make_bucket(label="Under 15", lower=None, upper=15.0, model_prob=0.04, market_prob=0.03, executable_ev=0.00, best_ask=0.04)
        high_tail = _make_bucket(label="Above 27", lower=27.0, upper=None, model_prob=0.05, market_prob=0.04, executable_ev=0.00, best_ask=0.06)
        scan = _make_scan_result(buckets=[bucket, low_tail, high_tail], horizon_hours=96, sigma_c=3.2)
        meta = {
            "context": {"bucket_lower_c": 18.0, "bucket_upper_c": 22.0, "observed_metar_count": 6, "observed_authority": "weathercom_wunderground", "resolution_location": "KJFK"},
            "weather_features": {"forecast_sources": {"ensemble": {"spread_c": 3.0, "num_members": 31}}},
        }

        candidate = build_candidate(_make_market(), scan, meta)

        assert candidate.verdict == "PAPER"
        assert "high uncertainty regime" in candidate.reason
        payload = candidate.as_dict()
        assert payload["regime_uncertainty"]["level"] == "high"
        assert [leg["side"] for leg in payload["tail_hedge_plan"]["legs"]] == ["Under 15", "Above 27"]

    def test_arbitrage_candidate_bypasses_uncertainty_overlay(self):
        """L'arbitrage pur reste séparé de l'overlay météo directionnel."""
        scan = _make_scan_result(buckets=[_make_bucket()], horizon_hours=96, sigma_c=3.2)
        meta = {
            "context": {"bucket_lower_c": 18.0, "bucket_upper_c": 22.0},
            "weather_features": {"forecast_sources": {"ensemble": {"spread_c": 3.0, "num_members": 31}}},
        }
        market = _make_market(outcome_prices=[0.40, 0.40])

        candidate = build_candidate(market, scan, meta)

        assert candidate.verdict == "ARBITRAGE"
        assert candidate.as_dict()["regime_uncertainty"] is None
        assert candidate.as_dict()["tail_hedge_plan"] is None

    def test_narrow_bucket_reject(self):
        """Bucket étroit (width=1.0) → hard block REJECT actuel."""
        bucket = _make_bucket(lower=19.5, upper=20.5)  # width = 1.0
        scan = _make_scan_result(buckets=[bucket])
        meta = {"context": {"bucket_lower_c": 19.5, "bucket_upper_c": 20.5, "observed_metar_count": 6}}
        candidate = build_candidate(_make_market(), scan, meta)
        assert candidate.verdict == "REJECT"
        assert "exact/narrow temperature bucket" in candidate.reason

    def test_no_executable_ev_reject(self):
        """Top sans executable_ev → REJECT."""
        bucket = _make_bucket(executable_ev=None, best_ask=None)
        scan = _make_scan_result(buckets=[bucket])
        meta = {"context": {"bucket_lower_c": 17.5, "bucket_upper_c": 20.5, "observed_metar_count": 6}}
        candidate = build_candidate(_make_market(), scan, meta)
        assert candidate.verdict == "REJECT"
        assert "no executable ask" in candidate.reason

    def test_low_liquidity_reject(self):
        """Liquidité < 250 → REJECT."""
        bucket = _make_bucket()
        scan = _make_scan_result(buckets=[bucket], liquidity=100.0)
        meta = {"context": {"bucket_lower_c": 17.5, "bucket_upper_c": 20.5, "observed_metar_count": 6}}
        candidate = build_candidate(_make_market(liquidity=100.0), scan, meta)
        assert candidate.verdict == "REJECT"
        assert "low liquidity" in candidate.reason

    def test_insufficient_depth_reject(self):
        """fill_cost_usd < 0.999 → REJECT (walk-the-book échoue)."""
        bucket = _make_bucket(fill_cost_usd=0.50)
        scan = _make_scan_result(buckets=[bucket])
        meta = {"context": {"bucket_lower_c": 17.5, "bucket_upper_c": 20.5, "observed_metar_count": 6}}
        candidate = build_candidate(_make_market(), scan, meta)
        assert candidate.verdict == "REJECT"
        assert "insufficient depth" in candidate.reason

    def test_global_market_paper(self):
        """Marché global → au moins PAPER (caution)."""
        bucket = _make_bucket()
        scan = _make_scan_result(buckets=[bucket], city="Global")
        meta = {"context": {"bucket_lower_c": 17.5, "bucket_upper_c": 20.5, "observed_metar_count": 6}}
        candidate = build_candidate(_make_market(), scan, meta)
        assert candidate.verdict in ("PAPER", "REJECT")
        assert "global climate market" in candidate.reason

    def test_no_station_lock_paper(self):
        """Pas de station ICAO 4 lettres → PAPER (caution)."""
        bucket = _make_bucket()
        scan = _make_scan_result(buckets=[bucket])
        meta = {"context": {"bucket_lower_c": 17.5, "bucket_upper_c": 20.5, "observed_metar_count": 6}}
        market = _make_market(raw={})
        candidate = build_candidate(market, scan, meta)
        assert candidate.verdict in ("PAPER", "REJECT")
        assert "no ICAO station lock" in candidate.reason

    def test_score_composition(self):
        """Vérifie que le score est bien composé EV*100 + bonuses - penalties."""
        bucket = _make_bucket(executable_ev=0.30, best_ask=0.05)
        scan = _make_scan_result(buckets=[bucket], confidence="high", liquidity=1000.0)
        meta = {"context": {"bucket_lower_c": 17.5, "bucket_upper_c": 20.5, "observed_metar_count": 6, "observed_authority": "weathercom_wunderground", "resolution_location": "KJFK"}}
        candidate = build_candidate(_make_market(), scan, meta)
        # Score attendu:
        # EV*100 = 0.30 * 100 = 30
        # ask_bonus = max(0, 0.10 - 0.05) * 50 = 2.5
        # confidence_bonus (high) = 10
        # obs_bonus (>=6) = 15
        # liquidity_bonus (>=500) = 5
        # Total = 62.5
        assert candidate.score == pytest.approx(62.5, abs=0.1)

    def test_score_with_blockers(self):
        """Vérifie que les blockers pénalisent fortement le score."""
        bucket = _make_bucket(executable_ev=0.30, best_ask=0.05, fill_cost_usd=0.50)
        scan = _make_scan_result(buckets=[bucket], confidence="high", liquidity=1000.0)
        meta = {"context": {"bucket_lower_c": 17.5, "bucket_upper_c": 20.5, "observed_metar_count": 6, "observed_authority": "weathercom_wunderground", "resolution_location": "KJFK"}}
        candidate = build_candidate(_make_market(), scan, meta)
        # Score = 62.5 - 20 (1 blocker: insufficient depth)
        assert candidate.score == pytest.approx(42.5, abs=0.1)

    def test_ev_below_threshold_reject(self):
        """executable_ev < 0.15 → REJECT."""
        bucket = _make_bucket(executable_ev=0.10)
        scan = _make_scan_result(buckets=[bucket])
        meta = {"context": {"bucket_lower_c": 17.5, "bucket_upper_c": 20.5, "observed_metar_count": 6}}
        candidate = build_candidate(_make_market(), scan, meta)
        assert candidate.verdict == "REJECT"
        assert "executable EV below threshold" in candidate.reason

    def test_absolute_edge_below_threshold_reject(self):
        """model_prob trop proche du marché (<10pp) → REJECT même si executable_ev est haut."""
        bucket = _make_bucket(market_prob=0.50, model_prob=0.59, executable_ev=0.30)
        scan = _make_scan_result(buckets=[bucket])
        meta = {"context": {"bucket_lower_c": 17.5, "bucket_upper_c": 20.5, "observed_metar_count": 6}}
        candidate = build_candidate(_make_market(), scan, meta)
        assert candidate.verdict == "REJECT"
        assert "edge below 10pp absolute threshold" in candidate.reason
