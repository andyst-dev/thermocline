from __future__ import annotations

import pytest

from weather_edge.models import BucketProbability
from weather_edge.uncertainty import build_tail_hedge_plan, evaluate_regime_uncertainty


def _bucket(
    label: str,
    lower: float | None,
    upper: float | None,
    *,
    market_prob: float = 0.05,
    model_prob: float = 0.03,
    best_ask: float | None = 0.04,
) -> BucketProbability:
    return BucketProbability(
        label=label,
        lower=lower,
        upper=upper,
        market_prob=market_prob,
        model_prob=model_prob,
        edge=model_prob - market_prob,
        ev=model_prob - market_prob,
        best_ask=best_ask,
        executable_ev=(model_prob - best_ask) if best_ask is not None else None,
    )


def test_regime_uncertainty_high_from_ensemble_spread_bias_and_discussion() -> None:
    features = {
        "recent_bias_14d": {"mean_residual_c": -2.4, "count": 8},
        "station_climatology": {"mean_observed_c": 20.0, "std_observed_c": 1.0, "count": 20},
        "nws_discussion": {"text": "Cold front timing remains highly uncertain with storms possible."},
        "forecast_sources": {"ensemble": {"spread_c": 2.8, "num_members": 31}},
    }

    regime = evaluate_regime_uncertainty(features, forecast_value_c=23.0, sigma_c=3.2, horizon_hours=96)

    assert regime.level == "high"
    assert regime.score >= 3
    assert "ensemble spread" in "; ".join(regime.reasons)
    assert regime.as_dict()["metrics"]["ensemble_spread_c"] == pytest.approx(2.8)


def test_regime_uncertainty_low_when_sources_are_calm() -> None:
    features = {
        "recent_bias_14d": {"mean_residual_c": 0.2, "count": 8},
        "station_climatology": {"mean_observed_c": 20.0, "std_observed_c": 2.0, "count": 20},
        "forecast_sources": {"ensemble": {"spread_c": 0.8, "num_members": 31}},
    }

    regime = evaluate_regime_uncertainty(features, forecast_value_c=20.5, sigma_c=1.4, horizon_hours=24)

    assert regime.level == "low"
    assert regime.score == 0


def test_tail_hedge_plan_only_for_high_uncertainty_and_cheap_tails() -> None:
    regime = evaluate_regime_uncertainty(
        {"forecast_sources": {"ensemble": {"spread_c": 3.0, "num_members": 31}}},
        forecast_value_c=20.0,
        sigma_c=3.0,
        horizon_hours=72,
    )
    buckets = [
        _bucket("Under 15", None, 15.0, best_ask=0.04),
        _bucket("18-22", 18.0, 22.0, best_ask=0.20),
        _bucket("Above 27", 27.0, None, best_ask=0.07),
        _bucket("Above 30 expensive", 30.0, None, best_ask=0.18),
    ]

    plan = build_tail_hedge_plan(buckets, forecast_value_c=20.0, regime=regime, max_ask=0.10)

    assert plan is not None
    assert plan["enabled"] is True
    assert [leg["side"] for leg in plan["legs"]] == ["Under 15", "Above 27"]
    assert all(leg["best_ask"] <= 0.10 for leg in plan["legs"])


def test_tail_hedge_plan_disabled_when_uncertainty_not_high() -> None:
    regime = evaluate_regime_uncertainty({}, forecast_value_c=20.0, sigma_c=1.5, horizon_hours=24)

    assert build_tail_hedge_plan([_bucket("Under 15", None, 15.0)], forecast_value_c=20.0, regime=regime) is None


def test_tail_hedge_plan_none_when_high_uncertainty_has_no_cheap_tails() -> None:
    regime = evaluate_regime_uncertainty(
        {"forecast_sources": {"ensemble": {"spread_c": 3.0, "num_members": 31}}},
        forecast_value_c=20.0,
        sigma_c=3.0,
        horizon_hours=72,
    )
    buckets = [
        _bucket("18-22", 18.0, 22.0, best_ask=0.04),
        _bucket("Above 27 expensive", 27.0, None, best_ask=0.20),
    ]

    assert build_tail_hedge_plan(buckets, forecast_value_c=20.0, regime=regime, max_ask=0.10) is None
