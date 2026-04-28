"""Tests for Kelly-scaled position sizing."""
from __future__ import annotations

import pytest

from weather_edge.candidates import compute_kelly_size


class TestComputeKellySize:
    def test_model_prob_none_returns_min(self):
        assert compute_kelly_size(model_prob=None, price=0.05, min_size_usd=2.0) == 2.0

    def test_price_none_returns_min(self):
        assert compute_kelly_size(model_prob=0.8, price=None, min_size_usd=2.0) == 2.0

    def test_price_zero_returns_min(self):
        assert compute_kelly_size(model_prob=0.8, price=0.0, min_size_usd=2.0) == 2.0

    def test_no_edge_returns_zero(self):
        # model_prob == price → kelly_f = 0
        assert compute_kelly_size(model_prob=0.5, price=0.5) == 0.0
        # model_prob < price → negative edge
        assert compute_kelly_size(model_prob=0.3, price=0.5) == 0.0

    def test_typical_low_price_high_prob(self):
        size = compute_kelly_size(
            model_prob=0.95,
            price=0.001,
            bankroll_usd=100.0,
            kelly_fraction=0.25,
        )
        assert size == pytest.approx(23.75, abs=0.01)

    def test_cap_at_max_size(self):
        size = compute_kelly_size(
            model_prob=0.99,
            price=0.001,
            max_size_usd=10.0,
            bankroll_usd=100.0,
            kelly_fraction=0.25,
        )
        assert size == 10.0

    def test_floor_at_min_size(self):
        size = compute_kelly_size(
            model_prob=0.55,
            price=0.50,
            min_size_usd=5.0,
            max_size_usd=50.0,
            bankroll_usd=100.0,
            kelly_fraction=0.25,
        )
        assert size == 5.0
