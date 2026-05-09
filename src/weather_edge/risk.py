from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


@dataclass(frozen=True)
class RiskSizingConfig:
    kelly_fraction: float = 0.25
    bankroll_usd: float = 100.0
    min_position_size_usd: float = 1.0
    max_position_size_usd: float = 20.0


def full_kelly_fraction(model_prob: float | None, price: float | None) -> float:
    """Return uncapped full-Kelly bankroll fraction for a binary $1 payout contract."""
    if model_prob is None or price is None:
        return 0.0
    p = float(model_prob)
    c = float(price)
    if p <= 0.0 or p >= 1.0 or c <= 0.0 or c >= 1.0:
        return 0.0
    b = (1.0 / c) - 1.0
    if b <= 0.0:
        return 0.0
    q = 1.0 - p
    edge_fraction = (b * p - q) / b
    return max(0.0, edge_fraction)


def compute_position_size(
    model_prob: float | None,
    price: float | None,
    config: RiskSizingConfig | None = None,
    *,
    ask_capacity_usd: float | None = None,
) -> float:
    """Fractional-Kelly size with hard caps and optional book-capacity limit.

    No-edge or invalid inputs return 0.0, not the min position. The min floor only
    applies once Kelly says the trade is worth taking.
    """
    cfg = config or RiskSizingConfig()
    kelly = full_kelly_fraction(model_prob, price)
    if kelly <= 0.0:
        return 0.0
    raw_size = cfg.kelly_fraction * kelly * cfg.bankroll_usd
    if raw_size <= 0.0:
        return 0.0
    capped = min(cfg.max_position_size_usd, max(cfg.min_position_size_usd, raw_size))
    if ask_capacity_usd is not None:
        capacity = max(0.0, float(ask_capacity_usd))
        if capacity < cfg.min_position_size_usd:
            return 0.0
        capped = min(capped, capacity)
    return float(round(capped, 6))


def expected_value_usd(model_prob: float, price: float, size_usd: float) -> float:
    if price <= 0.0 or price >= 1.0 or size_usd <= 0.0:
        return 0.0
    shares = size_usd / price
    return float(model_prob * (shares - size_usd) - (1.0 - model_prob) * size_usd)


def horizon_scale_factor(horizon_hours: float | int | None) -> float:
    """PolyDekos-style scale-in by time-to-resolution.

    Far from settlement, size only a starter. Increase exposure as forecasts
    become less noisy. Same-day/past horizons are not eligible for new sizing.
    """
    if horizon_hours is None:
        return 0.30
    horizon = float(horizon_hours)
    if horizon <= 0.0:
        return 0.0
    if horizon >= 48.0:
        return 0.30
    if horizon >= 24.0:
        return 0.60
    if horizon >= 8.0:
        return 0.80
    return 1.00


def regime_scale_factor(regime_level: str | None) -> float:
    """Reduce sizing when weather regime uncertainty is elevated."""
    level = str(regime_level or "unknown").lower()
    if level == "low":
        return 1.0
    if level == "elevated":
        return 0.70
    if level == "high":
        return 0.40
    return 0.70


def apply_sizing_scales(
    base_size_usd: float | None,
    *,
    horizon_hours: float | int | None,
    regime_level: str | None,
    min_position_size_usd: float = 1.0,
    max_position_size_usd: float | None = None,
) -> float:
    """Apply horizon and uncertainty scales to an already Kelly-capped size.

    If scaling would leave less than the configured minimum trade size, return
    0.0 rather than rounding back up and undoing the risk reduction.
    """
    if base_size_usd is None or base_size_usd <= 0.0:
        return 0.0
    scaled = float(base_size_usd) * horizon_scale_factor(horizon_hours) * regime_scale_factor(regime_level)
    if max_position_size_usd is not None:
        scaled = min(float(max_position_size_usd), scaled)
    if scaled < float(min_position_size_usd):
        return 0.0
    return float(round(scaled, 6))


def summarize_risk_metrics(pnls_usd: Iterable[float], *, initial_equity_usd: float = 100.0) -> dict[str, float | int | None]:
    pnls = [float(x) for x in pnls_usd]
    trade_count = len(pnls)
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    total_pnl = sum(pnls)

    equity = float(initial_equity_usd)
    peak = equity
    max_drawdown_usd = 0.0
    max_drawdown_pct = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = peak - equity
        if drawdown > max_drawdown_usd:
            max_drawdown_usd = drawdown
            max_drawdown_pct = drawdown / peak if peak > 0 else 0.0

    std = statistics.pstdev(pnls) if trade_count > 1 else 0.0
    mean = statistics.fmean(pnls) if trade_count else 0.0
    sharpe = (mean / std * math.sqrt(trade_count)) if std > 0.0 else None
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0.0 else None

    return {
        "trade_count": trade_count,
        "total_pnl_usd": round(total_pnl, 6),
        "mean_pnl_usd": round(mean, 6) if trade_count else 0.0,
        "win_rate": round(len(wins) / trade_count, 6) if trade_count else None,
        "profit_factor": round(profit_factor, 6) if isinstance(profit_factor, float) and math.isfinite(profit_factor) else profit_factor,
        "max_drawdown_usd": round(max_drawdown_usd, 6),
        "max_drawdown_pct": max_drawdown_pct,
        "sharpe_per_trade": round(sharpe, 6) if sharpe is not None else None,
        "worst_loss_usd": round(min(pnls), 6) if pnls else None,
        "best_win_usd": round(max(pnls), 6) if pnls else None,
        "ending_equity_usd": round(equity, 6),
    }


def _clamp_probability(value: float) -> float:
    return max(0.001, min(0.999, float(value)))


def _clamp_price(value: float) -> float:
    return max(0.001, min(0.999, float(value)))


def build_sensitivity_grid(
    *,
    model_prob: float,
    price: float,
    config: RiskSizingConfig | None = None,
    probability_shocks: Sequence[float] = (-0.10, -0.05, 0.0, 0.05, 0.10),
    price_shocks: Sequence[float] = (-0.05, 0.0, 0.05),
    kelly_fractions: Sequence[float] = (0.10, 0.25, 0.50),
    ask_capacity_usd: float | None = None,
) -> list[dict[str, float]]:
    cfg = config or RiskSizingConfig()
    rows: list[dict[str, float]] = []
    for kelly_fraction in kelly_fractions:
        scenario_cfg = RiskSizingConfig(
            kelly_fraction=float(kelly_fraction),
            bankroll_usd=cfg.bankroll_usd,
            min_position_size_usd=cfg.min_position_size_usd,
            max_position_size_usd=cfg.max_position_size_usd,
        )
        for prob_shock in probability_shocks:
            scenario_prob = _clamp_probability(model_prob + float(prob_shock))
            for price_shock in price_shocks:
                scenario_price = _clamp_price(price + float(price_shock))
                size = compute_position_size(
                    scenario_prob,
                    scenario_price,
                    scenario_cfg,
                    ask_capacity_usd=ask_capacity_usd,
                )
                rows.append(
                    {
                        "kelly_fraction": round(float(kelly_fraction), 6),
                        "probability_shock": round(float(prob_shock), 6),
                        "price_shock": round(float(price_shock), 6),
                        "model_prob": round(scenario_prob, 6),
                        "price": round(scenario_price, 6),
                        "full_kelly_fraction": round(full_kelly_fraction(scenario_prob, scenario_price), 6),
                        "recommended_size_usd": round(size, 6),
                        "expected_value_usd": round(expected_value_usd(scenario_prob, scenario_price, size), 6),
                        "loss_if_wrong_usd": round(-size, 6),
                        "profit_if_right_usd": round((size / scenario_price) - size, 6) if size > 0 else 0.0,
                    }
                )
    return rows


def write_sensitivity_report(
    output_dir: str | Path,
    *,
    model_prob: float,
    price: float,
    config: RiskSizingConfig | None = None,
    candidate_ref: dict[str, Any] | None = None,
    ask_capacity_usd: float | None = None,
    filename: str = "risk_sizing_grid.json",
) -> Path:
    cfg = config or RiskSizingConfig()
    grid = build_sensitivity_grid(
        model_prob=model_prob,
        price=price,
        config=cfg,
        ask_capacity_usd=ask_capacity_usd,
    )
    report_dir = Path(output_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / filename
    payload = {
        "policy": {
            "no_order_placement": True,
            "purpose": "offline sensitivity validation for fractional Kelly sizing before paper-open enablement",
        },
        "config": asdict(cfg),
        "candidate_ref": candidate_ref or {},
        "base": {
            "model_prob": model_prob,
            "price": price,
            "ask_capacity_usd": ask_capacity_usd,
            "recommended_size_usd": compute_position_size(model_prob, price, cfg, ask_capacity_usd=ask_capacity_usd),
        },
        "grid": grid,
        "risk_metrics": summarize_risk_metrics((row["loss_if_wrong_usd"] for row in grid), initial_equity_usd=cfg.bankroll_usd),
        "expected_value_metrics": summarize_risk_metrics((row["expected_value_usd"] for row in grid), initial_equity_usd=cfg.bankroll_usd),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
