"""Tests unitaires pour weather_edge.parsing."""
from __future__ import annotations

import math

import pytest

from weather_edge.parsing import (
    TemperatureContract,
    bucket_probability,
    normal_cdf,
    parse_bucket,
    parse_temperature_contract,
)


class TestParseBucket:
    """Tests de parse_bucket() uniquement (pas de conversion °F→°C ici)."""

    def test_range_celsius(self):
        assert parse_bucket("20-22") == (20.0, 22.0)

    def test_range_fahrenheit(self):
        # parse_bucket ne convertit pas F→C, juste extrait les nombres
        assert parse_bucket("86-88F") == (86.0, 88.0)
        assert parse_bucket("86-88°F") == (86.0, 88.0)

    def test_open_lower_under(self):
        assert parse_bucket("<10") == (None, 10.0)
        assert parse_bucket("under 10") == (None, 10.0)

    def test_open_upper_over(self):
        assert parse_bucket(">30") == (30.0, None)
        assert parse_bucket("above 30") == (30.0, None)

    def test_single_integer(self):
        assert parse_bucket("20") == (20.0, 20.0)

    def test_or_above(self):
        assert parse_bucket("25+") == (25.0, None)
        assert parse_bucket("25 or above") == (25.0, None)

    def test_or_below(self):
        # "or lower" n'est pas supporté par parse_bucket (seul "or above" l'est)
        # Ce test documente la limitation actuelle.
        assert parse_bucket("10 or lower") == (None, None)

    def test_between(self):
        # "between X and Y" n'est pas supporté par parse_bucket
        # (traité uniquement dans parse_temperature_contract)
        assert parse_bucket("between 15 and 20") == (None, None)

    def test_no_match(self):
        assert parse_bucket("no numbers here") == (None, None)


class TestBucketProbability:
    """Tests de bucket_probability() avec différents sigmas."""

    def test_open_tail(self):
        # lower=None, upper=20, mean=15, sigma=1.0  → très probable
        prob = bucket_probability(None, 20.0, 15.0, 1.0)
        assert 0.99 < prob < 1.0

    def test_exact_centered_sigma_0_3(self):
        # lower=upper=20, mean=20, sigma=0.3, half_width=0.5
        # P = CDF(20.5) - CDF(19.5) ≈ 0.904
        prob = bucket_probability(20.0, 20.0, 20.0, 0.3)
        assert 0.90 < prob < 0.91

    def test_exact_off_center_sigma_0_3(self):
        # lower=upper=20, mean=20.8 (décalé 0.8°C), sigma=0.3
        # P = CDF(20.5) - CDF(19.5) avec mean=20.8  → ~0.16
        prob = bucket_probability(20.0, 20.0, 20.8, 0.3)
        assert 0.10 < prob < 0.20
        # Vérifie qu'on a bien une chute drastique vs centré
        centered = bucket_probability(20.0, 20.0, 20.0, 0.3)
        assert prob < centered * 0.3  # au moins 70% de chute

    def test_wide_bucket_stable(self):
        # lower=17.5, upper=22.5 (width=5°C), mean=20, sigma=0.3
        # Très large par rapport au sigma → proche de 1.0
        prob = bucket_probability(17.5, 22.5, 20.0, 0.3)
        assert prob == pytest.approx(1.0, abs=1e-3)

    def test_range_width_2(self):
        # lower=20, upper=22 (width=2°C), mean=21, sigma=1.0
        # P = CDF(22) - CDF(20) ≈ 0.683
        prob = bucket_probability(20.0, 22.0, 21.0, 1.0)
        assert 0.68 < prob < 0.69


class TestNormalCDF:
    """Tests de la fonction de répartition normale."""

    def test_mean(self):
        assert normal_cdf(5.0, 5.0, 1.0) == pytest.approx(0.5, abs=1e-6)

    def test_far_above(self):
        assert normal_cdf(10.0, 5.0, 1.0) > 0.999

    def test_far_below(self):
        assert normal_cdf(0.0, 5.0, 1.0) < 0.001

    def test_sigma_zero(self):
        assert normal_cdf(5.0, 5.0, 0.0) == 1.0
        assert normal_cdf(4.9, 5.0, 0.0) == 0.0


class TestParseTemperatureContract:
    """Tests du parsing de contrats complets avec conversion °F→°C."""

    def test_celsius_bucket(self):
        q = "Will the highest temperature in Paris be 20°C on May 5?"
        tc = parse_temperature_contract(q)
        assert tc is not None
        assert tc.city == "Paris"
        assert tc.metric == "highest"
        assert tc.lower_c == 19.5
        assert tc.upper_c == 20.5

    def test_fahrenheit_bucket(self):
        q = "Will the highest temperature in New York be 86°F on April 25?"
        tc = parse_temperature_contract(q)
        assert tc is not None
        assert tc.city == "New York"
        # 86°F → (86-32)*5/9 = 30.0°C exact  →  lower=29.5, upper=30.5
        assert tc.lower_c == pytest.approx(29.5, abs=0.01)
        assert tc.upper_c == pytest.approx(30.5, abs=0.01)

    def test_open_below(self):
        q = "Will the highest temperature in London be below 10°C on April 10?"
        tc = parse_temperature_contract(q)
        assert tc is not None
        assert tc.lower_c is None
        assert tc.upper_c == 10.0

    def test_open_above(self):
        q = "Will the highest temperature in Tokyo be 25°C or above on April 20?"
        tc = parse_temperature_contract(q)
        assert tc is not None
        assert tc.lower_c == 25.0
        assert tc.upper_c is None

    def test_between(self):
        q = "Will the highest temperature in Berlin be between 15°C and 20°C on April 15?"
        tc = parse_temperature_contract(q)
        assert tc is not None
        assert tc.lower_c == 15.0
        assert tc.upper_c == 20.0

    def test_no_match(self):
        assert parse_temperature_contract("completely unrelated question") is None
