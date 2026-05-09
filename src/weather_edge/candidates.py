from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import BucketProbability, ScanResult, WeatherMarket
from .risk import RiskSizingConfig, apply_sizing_scales, compute_position_size
from .uncertainty import build_tail_hedge_plan, evaluate_regime_uncertainty

FORECAST_STABILITY_DRIFT_THRESHOLD_C = 1.0
FORECAST_STABILITY_SCALE = 0.30


def compute_kelly_size(
    model_prob: float | None,
    price: float | None,
    kelly_fraction: float = 0.25,
    max_size_usd: float = 50.0,
    min_size_usd: float = 1.0,
    bankroll_usd: float = 100.0,
) -> float:
    return compute_position_size(
        model_prob=model_prob,
        price=price,
        config=RiskSizingConfig(
            kelly_fraction=kelly_fraction,
            bankroll_usd=bankroll_usd,
            min_position_size_usd=min_size_usd,
            max_position_size_usd=max_size_usd,
        ),
    )


@dataclass(frozen=True)
class Candidate:
    verdict: str
    reason: str
    score: float
    market_id: str
    slug: str
    question: str
    city: str
    target_date: str
    side: str | None
    model_prob: float | None
    gamma_price: float | None
    best_bid: float | None
    best_ask: float | None
    executable_ev: float | None
    ask_capacity_usd: float | None
    fill_avg_price: float | None
    fill_shares: float | None
    fill_cost_usd: float | None
    fill_levels_json: str | None
    book_fetched_at: str | None
    book_snapshot_path: str | None
    book_snapshot_hash: str | None
    token_id: str | None
    liquidity: float
    confidence: str
    forecast_value_c: float
    sigma_c: float
    horizon_hours: float
    resolution_location: str | None
    observed_metar_count: int
    observed_authority: str | None
    bucket_width_c: float | None
    resolution_source: str | None
    model_prob_gaussian: float | None = None
    model_prob_ensemble: float | None = None
    recommended_size_usd: float | None = None
    regime_uncertainty: dict[str, Any] | None = None
    tail_hedge_plan: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "score": round(self.score, 4),
            "market_id": self.market_id,
            "slug": self.slug,
            "question": self.question,
            "city": self.city,
            "target_date": self.target_date,
            "side": self.side,
            "model_prob": round(self.model_prob, 4) if self.model_prob is not None else None,
            "gamma_price": self.gamma_price,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "executable_ev": round(self.executable_ev, 4) if self.executable_ev is not None else None,
            "ask_capacity_usd": round(self.ask_capacity_usd, 4) if self.ask_capacity_usd is not None else None,
            "fill_avg_price": round(self.fill_avg_price, 6) if self.fill_avg_price is not None else None,
            "fill_shares": round(self.fill_shares, 4) if self.fill_shares is not None else None,
            "fill_cost_usd": round(self.fill_cost_usd, 4) if self.fill_cost_usd is not None else None,
            "fill_levels_json": self.fill_levels_json,
            "book_fetched_at": self.book_fetched_at,
            "book_snapshot_path": self.book_snapshot_path,
            "book_snapshot_hash": self.book_snapshot_hash,
            "token_id": self.token_id,
            "liquidity": self.liquidity,
            "confidence": self.confidence,
            "forecast_value_c": round(self.forecast_value_c, 2),
            "sigma_c": round(self.sigma_c, 2),
            "horizon_hours": round(self.horizon_hours, 2),
            "resolution_location": self.resolution_location,
            "observed_metar_count": self.observed_metar_count,
            "observed_authority": self.observed_authority,
            "bucket_width_c": round(self.bucket_width_c, 3) if self.bucket_width_c is not None else None,
            "resolution_source": self.resolution_source,
            "model_prob_gaussian": round(self.model_prob_gaussian, 4) if self.model_prob_gaussian is not None else None,
            "model_prob_ensemble": round(self.model_prob_ensemble, 4) if self.model_prob_ensemble is not None else None,
            "recommended_size_usd": round(self.recommended_size_usd, 4) if self.recommended_size_usd is not None else None,
            "regime_uncertainty": self.regime_uncertainty,
            "tail_hedge_plan": self.tail_hedge_plan,
        }


def _top_bucket(result: ScanResult) -> BucketProbability | None:
    with_exec = [b for b in result.buckets if b.executable_ev is not None]
    if with_exec:
        return max(with_exec, key=lambda b: b.executable_ev or -999)
    return max(result.buckets, key=lambda b: b.ev, default=None)


def _forecast_drift_c(context: dict[str, Any], current_forecast_c: float) -> float | None:
    """Return abs forecast drift since initial/opened forecast if metadata exists."""
    for key in ("initial_forecast_c", "opened_forecast_c", "previous_forecast_c"):
        baseline = context.get(key)
        if isinstance(baseline, (int, float)):
            return abs(float(current_forecast_c) - float(baseline))
    return None


def _apply_forecast_stability_scale(
    size_usd: float | None,
    *,
    drift_c: float | None,
    min_position_size_usd: float,
) -> float:
    if size_usd is None or size_usd <= 0.0:
        return 0.0
    if drift_c is None or drift_c <= FORECAST_STABILITY_DRIFT_THRESHOLD_C:
        return float(size_usd)
    scaled = float(size_usd) * FORECAST_STABILITY_SCALE
    if scaled < float(min_position_size_usd):
        return 0.0
    return float(round(scaled, 6))


def build_candidate(
    market: WeatherMarket,
    result: ScanResult,
    forecast_meta: dict[str, Any],
    settings=None,
    calibration_gate: Any | None = None,
) -> Candidate:
    top = _top_bucket(result)
    context = forecast_meta.get("context") or {}
    resolution_location = context.get("resolution_location")
    observed_count = int(context.get("observed_metar_count") or 0)
    observed_authority = context.get("observed_authority")
    resolution_source = market.raw.get("resolutionSource") or market.raw.get("resolution_source")
    lower = context.get("bucket_lower_c")
    upper = context.get("bucket_upper_c")
    bucket_width_c = None
    if isinstance(lower, (int, float)) and isinstance(upper, (int, float)):
        bucket_width_c = float(upper) - float(lower)

    # Enhancement 2: intra-market arbitrage — buying both sides costs < $1, guaranteeing profit
    if len(market.outcomes) == 2 and len(market.outcome_prices) == 2 and result.liquidity >= 250:
        _price_sum = sum(market.outcome_prices)
        if _price_sum < 0.99:
            _profit = 1.0 - _price_sum
            return Candidate(
                verdict="ARBITRAGE",
                reason="intra-market arbitrage: yes + no = {:.3f}".format(_price_sum),
                score=200,
                market_id=result.market_id,
                slug=result.slug,
                question=result.question,
                city=result.city,
                target_date=result.target_date,
                side=None,
                model_prob=None,
                gamma_price=_price_sum,
                best_bid=None,
                best_ask=_price_sum,
                executable_ev=_profit,
                ask_capacity_usd=None,
                fill_avg_price=_price_sum,
                fill_shares=None,
                fill_cost_usd=None,
                fill_levels_json=None,
                book_fetched_at=None,
                book_snapshot_path=None,
                book_snapshot_hash=None,
                token_id=None,
                liquidity=result.liquidity,
                confidence=result.confidence,
                forecast_value_c=result.forecast_max_c,
                sigma_c=result.sigma_c,
                horizon_hours=result.horizon_hours,
                resolution_location=resolution_location,
                observed_metar_count=observed_count,
                observed_authority=str(observed_authority) if observed_authority else None,
                bucket_width_c=bucket_width_c,
                resolution_source=str(resolution_source) if resolution_source else None,
                recommended_size_usd=min(settings.max_position_size_usd, result.liquidity * 0.1) if settings is not None else 50.0,
            )

    blockers: list[str] = []
    cautions: list[str] = []
    if top is None:
        blockers.append("no priced bucket")
    if result.city == "Global":
        cautions.append("global climate market, not target city weather niche")
    if top and top.best_ask is None:
        blockers.append("no executable ask found")
    if top and (top.fill_cost_usd is None or top.fill_cost_usd < 0.999):
        blockers.append("insufficient depth for $1 walk-the-book fill")
    if top and top.best_ask is not None and top.best_ask > 0.10:
        cautions.append("ask above cheap-tail threshold")
    if top and top.executable_ev is not None and top.executable_ev < 0.15:
        blockers.append("executable EV below threshold")
    if top and top.market_prob is not None and top.model_prob is not None and abs(top.model_prob - top.market_prob) < 0.10:
        blockers.append("edge below 10pp absolute threshold")
    if result.liquidity < 250:
        blockers.append("low liquidity")
    if result.confidence != "high":
        cautions.append("model confidence not high")
    if result.horizon_hours <= 0:
        blockers.append("same-day/provisional observation: local day may not be complete")
    if bucket_width_c is not None and bucket_width_c <= 1.01:
        blockers.append("exact/narrow temperature bucket requires calibration before PASS")
    if resolution_location and isinstance(resolution_location, str) and len(resolution_location) == 4:
        if observed_count < 6 and result.horizon_hours <= 24:
            cautions.append("few/no same-day official observations")
    else:
        cautions.append("no ICAO station lock")
    if not resolution_source:
        cautions.append("missing resolution source")
    elif ("wunderground.com" in str(resolution_source).lower() or "weather.com" in str(resolution_source).lower()) and observed_authority != "weathercom_wunderground" and result.horizon_hours > 0:
        blockers.append("official Wunderground/weather.com source unavailable")
    if calibration_gate is not None:
        if isinstance(calibration_gate, dict):
            gate_allowed = bool(calibration_gate.get("allowed"))
            gate_reasons = list(calibration_gate.get("reasons") or [])
        else:
            gate_allowed = bool(getattr(calibration_gate, "allowed", False))
            gate_reasons = list(getattr(calibration_gate, "reasons", []) or [])
        if not gate_allowed:
            detail = "; ".join(str(reason) for reason in gate_reasons[:3]) or "calibration unavailable"
            blockers.append(f"calibration gate blocked: {detail}")

    forecast_drift_c = _forecast_drift_c(context, result.forecast_max_c)
    if forecast_drift_c is not None and forecast_drift_c > FORECAST_STABILITY_DRIFT_THRESHOLD_C:
        cautions.append(
            f"forecast drift {forecast_drift_c:.2f}°C exceeds {FORECAST_STABILITY_DRIFT_THRESHOLD_C:.1f}°C stability gate"
        )

    regime = evaluate_regime_uncertainty(
        forecast_meta.get("weather_features"),
        forecast_value_c=result.forecast_max_c,
        sigma_c=result.sigma_c,
        horizon_hours=result.horizon_hours,
    )
    tail_hedge_plan = build_tail_hedge_plan(
        result.buckets,
        forecast_value_c=result.forecast_max_c,
        regime=regime,
    )
    if regime.level == "high":
        cautions.append("high uncertainty regime: use tail hedge plan or stay paper")

    exec_ev = top.executable_ev if top else None
    ask = top.best_ask if top else None
    model_prob = top.model_prob if top else None
    model_prob_gaussian = top.model_prob_gaussian if top else None
    model_prob_ensemble = top.model_prob_ensemble if top else None
    gamma_price = top.market_prob if top else None
    ask_capacity = top.ask_capacity_usd if top else None
    fill_avg_price = top.fill_avg_price if top else None
    fill_shares = top.fill_shares if top else None
    fill_cost_usd = top.fill_cost_usd if top else None
    fill_levels_json = top.fill_levels_json if top else None
    book_fetched_at = top.book_fetched_at if top else None
    book_snapshot_path = top.book_snapshot_path if top else None
    book_snapshot_hash = top.book_snapshot_hash if top else None
    token_id = top.token_id if top else None

    price_for_kelly = fill_avg_price if fill_avg_price is not None else (ask if ask is not None else gamma_price)
    recommended_size_usd = None
    if settings is not None:
        base_size_usd = compute_kelly_size(
            model_prob=model_prob,
            price=price_for_kelly,
            kelly_fraction=settings.kelly_fraction,
            max_size_usd=settings.max_position_size_usd,
            min_size_usd=settings.min_position_size_usd,
            bankroll_usd=settings.kelly_bankroll_usd,
        )
        recommended_size_usd = apply_sizing_scales(
            base_size_usd,
            horizon_hours=result.horizon_hours,
            regime_level=regime.level,
            min_position_size_usd=settings.min_position_size_usd,
            max_position_size_usd=settings.max_position_size_usd,
        )
        recommended_size_usd = _apply_forecast_stability_scale(
            recommended_size_usd,
            drift_c=forecast_drift_c,
            min_position_size_usd=settings.min_position_size_usd,
        )

    score = 0.0
    if exec_ev is not None:
        score += max(0.0, exec_ev) * 100
    if ask is not None:
        score += max(0.0, 0.10 - ask) * 50
    if result.confidence == "high":
        score += 10
    if observed_count >= 6:
        score += 15
    if result.liquidity >= 500:
        score += 5
    score -= 20 * len(blockers)
    score -= 5 * len(cautions)

    if blockers:
        verdict = "REJECT"
        reason = "; ".join(blockers + cautions)
    elif cautions:
        verdict = "PAPER"
        reason = "; ".join(cautions)
    else:
        verdict = "PASS"
        reason = "meets executable EV, liquidity, confidence, station and observation checks"

    return Candidate(
        verdict=verdict,
        reason=reason,
        score=score,
        market_id=result.market_id,
        slug=result.slug,
        question=result.question,
        city=result.city,
        target_date=result.target_date,
        side=top.label if top else None,
        model_prob=model_prob,
        gamma_price=gamma_price,
        best_bid=top.best_bid if top else None,
        best_ask=ask,
        executable_ev=exec_ev,
        ask_capacity_usd=ask_capacity,
        fill_avg_price=fill_avg_price,
        fill_shares=fill_shares,
        fill_cost_usd=fill_cost_usd,
        fill_levels_json=fill_levels_json,
        book_fetched_at=book_fetched_at,
        book_snapshot_path=book_snapshot_path,
        book_snapshot_hash=book_snapshot_hash,
        token_id=token_id,
        liquidity=result.liquidity,
        confidence=result.confidence,
        forecast_value_c=result.forecast_max_c,
        sigma_c=result.sigma_c,
        horizon_hours=result.horizon_hours,
        resolution_location=resolution_location,
        observed_metar_count=observed_count,
        observed_authority=str(observed_authority) if observed_authority else None,
        bucket_width_c=bucket_width_c,
        resolution_source=str(resolution_source) if resolution_source else None,
        model_prob_gaussian=model_prob_gaussian,
        model_prob_ensemble=model_prob_ensemble,
        recommended_size_usd=recommended_size_usd,
        regime_uncertainty=regime.as_dict(),
        tail_hedge_plan=tail_hedge_plan,
    )
