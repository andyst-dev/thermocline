"""Tests unitaires pour weather_edge.candidates."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from weather_edge.candidates import Candidate, build_candidate
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
