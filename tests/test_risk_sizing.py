from __future__ import annotations

import json

import pytest

from weather_edge.risk import (
    RiskSizingConfig,
    apply_sizing_scales,
    build_sensitivity_grid,
    compute_position_size,
    horizon_scale_factor,
    regime_scale_factor,
    summarize_risk_metrics,
    write_sensitivity_report,
)


def test_fractional_kelly_size_obeys_fraction_bankroll_and_caps() -> None:
    config = RiskSizingConfig(
        kelly_fraction=0.25,
        bankroll_usd=100.0,
        min_position_size_usd=1.0,
        max_position_size_usd=20.0,
    )

    # p=0.80, price=0.50 -> full Kelly fraction = 0.60; quarter Kelly => $15 on $100 bankroll.
    size = compute_position_size(model_prob=0.80, price=0.50, config=config)

    assert size == pytest.approx(15.0)


def test_position_size_returns_zero_for_no_edge_not_minimum_bet() -> None:
    config = RiskSizingConfig(min_position_size_usd=1.0)

    size = compute_position_size(model_prob=0.49, price=0.50, config=config)

    assert size == 0.0


def test_position_size_respects_max_cap_min_floor_and_capacity() -> None:
    config = RiskSizingConfig(
        kelly_fraction=0.25,
        bankroll_usd=1_000.0,
        min_position_size_usd=1.0,
        max_position_size_usd=20.0,
    )

    assert compute_position_size(model_prob=0.90, price=0.10, config=config) == pytest.approx(20.0)
    assert compute_position_size(model_prob=0.56, price=0.50, config=config) == pytest.approx(20.0)
    assert compute_position_size(model_prob=0.56, price=0.50, config=config, ask_capacity_usd=7.0) == pytest.approx(7.0)
    assert compute_position_size(model_prob=0.505, price=0.50, config=config) == pytest.approx(2.5)


def test_polydekos_horizon_and_regime_scales_reduce_exposure() -> None:
    assert horizon_scale_factor(72) == pytest.approx(0.30)
    assert horizon_scale_factor(36) == pytest.approx(0.60)
    assert horizon_scale_factor(12) == pytest.approx(0.80)
    assert horizon_scale_factor(4) == pytest.approx(1.00)
    assert horizon_scale_factor(0) == pytest.approx(0.0)

    assert regime_scale_factor("low") == pytest.approx(1.0)
    assert regime_scale_factor("elevated") == pytest.approx(0.70)
    assert regime_scale_factor("high") == pytest.approx(0.40)
    assert regime_scale_factor(None) == pytest.approx(0.70)

    assert apply_sizing_scales(10.0, horizon_hours=72, regime_level="low") == pytest.approx(3.0)
    assert apply_sizing_scales(10.0, horizon_hours=24, regime_level="elevated") == pytest.approx(4.2)
    assert apply_sizing_scales(10.0, horizon_hours=4, regime_level="high") == pytest.approx(4.0)
    assert apply_sizing_scales(2.0, horizon_hours=72, regime_level="high", min_position_size_usd=1.0) == 0.0


def test_risk_metrics_include_drawdown_profit_factor_and_sharpe() -> None:
    metrics = summarize_risk_metrics([2.0, -1.0, 3.0, -4.0, 5.0], initial_equity_usd=100.0)

    assert metrics["trade_count"] == 5
    assert metrics["total_pnl_usd"] == pytest.approx(5.0)
    assert metrics["win_rate"] == pytest.approx(0.60)
    assert metrics["profit_factor"] == pytest.approx(10.0 / 5.0)
    # Equity path: 100 -> 102 -> 101 -> 104 -> 100 -> 105, max drawdown = 4 from peak 104.
    assert metrics["max_drawdown_usd"] == pytest.approx(4.0)
    assert metrics["max_drawdown_pct"] == pytest.approx(4.0 / 104.0)
    assert metrics["sharpe_per_trade"] > 0


def test_sensitivity_grid_shows_lower_sizing_after_negative_probability_shock() -> None:
    config = RiskSizingConfig(
        kelly_fraction=0.25,
        bankroll_usd=100.0,
        min_position_size_usd=1.0,
        max_position_size_usd=20.0,
    )

    rows = build_sensitivity_grid(
        model_prob=0.70,
        price=0.50,
        config=config,
        probability_shocks=(-0.10, 0.0, 0.10),
        price_shocks=(0.0,),
        kelly_fractions=(0.10, 0.25),
    )

    assert len(rows) == 6
    base = next(row for row in rows if row["probability_shock"] == 0.0 and row["kelly_fraction"] == 0.25)
    stressed = next(row for row in rows if row["probability_shock"] == -0.10 and row["kelly_fraction"] == 0.25)
    smaller_fraction = next(row for row in rows if row["probability_shock"] == 0.0 and row["kelly_fraction"] == 0.10)
    assert stressed["recommended_size_usd"] < base["recommended_size_usd"]
    assert smaller_fraction["recommended_size_usd"] < base["recommended_size_usd"]
    assert base["expected_value_usd"] > 0


def test_write_sensitivity_report_creates_json_without_opening_trades(tmp_path) -> None:
    config = RiskSizingConfig(kelly_fraction=0.25, bankroll_usd=100.0, max_position_size_usd=20.0)

    report_path = write_sensitivity_report(
        tmp_path,
        model_prob=0.70,
        price=0.50,
        config=config,
        candidate_ref={"market_id": "m1", "side": "Yes"},
    )

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["policy"]["no_order_placement"] is True
    assert payload["candidate_ref"] == {"market_id": "m1", "side": "Yes"}
    assert payload["grid"]
    assert payload["risk_metrics"]["trade_count"] == len(payload["grid"])
    assert payload["risk_metrics"]["total_pnl_usd"] < 0
    assert payload["risk_metrics"]["max_drawdown_usd"] > 0
    assert payload["expected_value_metrics"]["total_pnl_usd"] > 0
