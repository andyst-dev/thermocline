"""Tests unitaires pour weather_edge.scanner."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from weather_edge.scanner import _effective_sigma_observed, _sigma_for_horizon


class TestEffectiveSigmaObserved:
    """Tests de _effective_sigma_observed() — calibration multi-composantes."""

    def test_default_value(self):
        """Valeur par défaut: sqrt(0.30² + 0.50² + 0.25²) ≈ 0.634"""
        sigma = _effective_sigma_observed()
        assert sigma == pytest.approx(0.634, abs=0.001)

    def test_with_zero_divergence(self):
        """Si divergence = 0, sigma plus faible."""
        sigma = _effective_sigma_observed(sigma_divergence=0.0)
        assert sigma == pytest.approx(0.391, abs=0.001)

    def test_single_component(self):
        """Une seule composante → sigma = cette composante."""
        sigma = _effective_sigma_observed(sigma_station=1.0, sigma_divergence=0.0, sigma_rounding=0.0)
        assert sigma == pytest.approx(1.0, abs=0.001)

    def test_larger_than_any_component(self):
        """Le sigma effectif est toujours >= chaque composante individuelle."""
        sigma = _effective_sigma_observed()
        assert sigma >= 0.30
        assert sigma >= 0.50
        assert sigma >= 0.25


class TestSigmaForHorizon:
    """Tests de _sigma_for_horizon() avec différents horizons temporels."""

    def test_zero_hours(self):
        now = datetime.now(timezone.utc)
        sigma, horizon = _sigma_for_horizon(now)
        assert sigma == pytest.approx(1.5, abs=0.01)
        assert horizon == pytest.approx(0.0, abs=0.01)

    def test_14_4_hours(self):
        now = datetime.now(timezone.utc)
        target = now.replace(microsecond=0) + __import__("datetime").timedelta(hours=14.4)
        sigma, horizon = _sigma_for_horizon(target)
        assert sigma == pytest.approx(2.0, abs=0.05)
        assert pytest.approx(14.4, abs=0.2) == horizon

    def test_36_hours(self):
        now = datetime.now(timezone.utc)
        target = now.replace(microsecond=0) + __import__("datetime").timedelta(hours=36)
        sigma, horizon = _sigma_for_horizon(target)
        assert sigma == pytest.approx(2.75, abs=0.05)
        assert pytest.approx(36.0, abs=0.2) == horizon

    def test_72_hours(self):
        now = datetime.now(timezone.utc)
        target = now.replace(microsecond=0) + __import__("datetime").timedelta(hours=72)
        sigma, horizon = _sigma_for_horizon(target)
        assert sigma == pytest.approx(4.0, abs=0.05)
        assert pytest.approx(72.0, abs=0.2) == horizon

    def test_144_hours_capped(self):
        now = datetime.now(timezone.utc)
        target = now.replace(microsecond=0) + __import__("datetime").timedelta(hours=144)
        sigma, horizon = _sigma_for_horizon(target)
        assert sigma == pytest.approx(5.0, abs=0.05)  # capped at 5.0
        assert horizon == pytest.approx(144.0, abs=0.2)

    def test_past_date_clamped(self):
        now = datetime.now(timezone.utc)
        target = now.replace(microsecond=0) - __import__("datetime").timedelta(hours=10)
        sigma, horizon = _sigma_for_horizon(target)
        assert sigma == pytest.approx(1.5, abs=0.01)  # clamped to 0h
        assert horizon == pytest.approx(0.0, abs=0.01)

    def test_monotonic_increase(self):
        """Vérifie que sigma augmente toujours avec l'horizon."""
        now = datetime.now(timezone.utc)
        sigmas = []
        for h in [0, 6, 12, 18, 24, 36, 48, 60, 72, 96, 120, 144]:
            target = now + __import__("datetime").timedelta(hours=h)
            sigma, _ = _sigma_for_horizon(target)
            sigmas.append(sigma)
        # Sigma doit être strictement croissant ou stable (pas de décroissance)
        for i in range(1, len(sigmas)):
            assert sigmas[i] >= sigmas[i - 1] - 0.01
