"""Tests unitaires pour weather_edge.ensemble."""
from __future__ import annotations

import pytest

from weather_edge.ensemble import ensemble_bucket_probability, ensemble_probability_above, ensemble_probability_below


class TestEnsembleBucketProbability:
    """Tests de ensemble_bucket_probability() — comptage fractionnaire."""

    def test_all_inside(self):
        """Tous les membres dans le bucket → 1.0."""
        prob = ensemble_bucket_probability([10.0, 11.0, 12.0, 13.0], lower=9.0, upper=14.0)
        assert prob == pytest.approx(1.0, abs=0.001)

    def test_none_inside(self):
        """Aucun membre dans le bucket → 0.0."""
        prob = ensemble_bucket_probability([5.0, 6.0, 7.0], lower=10.0, upper=15.0)
        assert prob == pytest.approx(0.0, abs=0.001)

    def test_half_inside(self):
        """50% des membres dans le bucket."""
        prob = ensemble_bucket_probability([10.0, 11.0, 20.0, 21.0], lower=9.0, upper=15.0)
        assert prob == pytest.approx(0.5, abs=0.001)

    def test_open_lower_tail(self):
        """Bucket ouvert en bas (-inf, upper]."""
        prob = ensemble_bucket_probability([5.0, 10.0, 15.0], lower=None, upper=12.0)
        assert prob == pytest.approx(2 / 3, abs=0.001)

    def test_open_upper_tail(self):
        """Bucket ouvert en haut [lower, +inf)."""
        prob = ensemble_bucket_probability([5.0, 10.0, 15.0], lower=8.0, upper=None)
        assert prob == pytest.approx(2 / 3, abs=0.001)

    def test_empty_members(self):
        """Liste vide → 0.5 (neutral)."""
        prob = ensemble_bucket_probability([], lower=0.0, upper=10.0)
        assert prob == pytest.approx(0.5, abs=0.001)

    def test_single_member_inside(self):
        """Un seul membre, dans le bucket → 1.0."""
        prob = ensemble_bucket_probability([12.0], lower=10.0, upper=15.0)
        assert prob == pytest.approx(1.0, abs=0.001)

    def test_exact_boundary(self):
        """Membre exactement sur la borne → inclus."""
        prob = ensemble_bucket_probability([10.0, 15.0], lower=10.0, upper=15.0)
        assert prob == pytest.approx(1.0, abs=0.001)


class TestEnsembleProbabilityAbove:
    """Tests de ensemble_probability_above()."""

    def test_all_above(self):
        prob = ensemble_probability_above([15.0, 16.0, 17.0], 10.0)
        assert prob == pytest.approx(1.0, abs=0.001)

    def test_none_above(self):
        prob = ensemble_probability_above([5.0, 6.0, 7.0], 10.0)
        assert prob == pytest.approx(0.0, abs=0.001)

    def test_empty(self):
        prob = ensemble_probability_above([], 10.0)
        assert prob == pytest.approx(0.5, abs=0.001)


class TestEnsembleProbabilityBelow:
    """Tests de ensemble_probability_below()."""

    def test_all_below(self):
        prob = ensemble_probability_below([5.0, 6.0, 7.0], 10.0)
        assert prob == pytest.approx(1.0, abs=0.001)

    def test_none_below(self):
        prob = ensemble_probability_below([15.0, 16.0, 17.0], 10.0)
        assert prob == pytest.approx(0.0, abs=0.001)
