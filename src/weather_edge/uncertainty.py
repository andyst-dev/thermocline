from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import BucketProbability


@dataclass(frozen=True)
class RegimeUncertainty:
    level: str
    score: int
    reasons: list[str]
    metrics: dict[str, float | int | None]

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "score": self.score,
            "reasons": list(self.reasons),
            "metrics": dict(self.metrics),
        }


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _discussion_text(features: dict[str, Any]) -> str:
    discussion = features.get("nws_discussion")
    if isinstance(discussion, dict):
        text = discussion.get("text") or discussion.get("discussion") or discussion.get("headline")
        return str(text or "")
    if isinstance(discussion, str):
        return discussion
    return ""


def evaluate_regime_uncertainty(
    weather_features: dict[str, Any] | None,
    *,
    forecast_value_c: float,
    sigma_c: float,
    horizon_hours: float,
) -> RegimeUncertainty:
    """Score regime uncertainty from side-channel weather features.

    This is deliberately a logging/risk overlay signal, not a probability override.
    """
    features = weather_features or {}
    reasons: list[str] = []
    score = 0

    sources = features.get("forecast_sources") if isinstance(features.get("forecast_sources"), dict) else {}
    ensemble = sources.get("ensemble") if isinstance(sources, dict) and isinstance(sources.get("ensemble"), dict) else {}
    ensemble_spread_c = _as_float(ensemble.get("spread_c") or ensemble.get("spread_max") or ensemble.get("spread_min"))
    ensemble_members = _as_float(ensemble.get("num_members"))
    if ensemble_spread_c is not None and ensemble_spread_c >= 2.5:
        score += 2
        reasons.append(f"ensemble spread high: {ensemble_spread_c:.2f}C")
    elif ensemble_spread_c is not None and ensemble_spread_c >= 1.8:
        score += 1
        reasons.append(f"ensemble spread elevated: {ensemble_spread_c:.2f}C")

    recent_bias = features.get("recent_bias_14d") if isinstance(features.get("recent_bias_14d"), dict) else {}
    mean_residual_c = _as_float(recent_bias.get("mean_residual_c"))
    if mean_residual_c is not None and abs(mean_residual_c) >= 2.0:
        score += 1
        reasons.append(f"recent forecast bias large: {mean_residual_c:+.2f}C")

    climatology = features.get("station_climatology") if isinstance(features.get("station_climatology"), dict) else {}
    clim_mean = _as_float(climatology.get("mean_observed_c"))
    clim_std = _as_float(climatology.get("std_observed_c"))
    if clim_mean is not None and clim_std is not None and clim_std > 0:
        z = abs(float(forecast_value_c) - clim_mean) / max(clim_std, 0.1)
        if z >= 2.0:
            score += 1
            reasons.append(f"forecast far from climatology: z={z:.2f}")
    else:
        z = None

    text = _discussion_text(features).lower()
    uncertainty_keywords = ("uncertain", "uncertainty", "front", "storm", "storms", "convective", "timing")
    if text and any(keyword in text for keyword in uncertainty_keywords):
        score += 1
        reasons.append("forecast discussion flags uncertainty/front/storm risk")

    if sigma_c >= 3.0:
        score += 1
        reasons.append(f"scanner sigma high: {sigma_c:.2f}C")
    if horizon_hours >= 72:
        score += 1
        reasons.append(f"long horizon: {horizon_hours:.0f}h")

    if score >= 3:
        level = "high"
    elif score >= 1:
        level = "elevated"
    else:
        level = "low"

    return RegimeUncertainty(
        level=level,
        score=score,
        reasons=reasons,
        metrics={
            "ensemble_spread_c": ensemble_spread_c,
            "ensemble_members": int(ensemble_members) if ensemble_members is not None else None,
            "recent_mean_residual_c": mean_residual_c,
            "climatology_z": z,
            "sigma_c": float(sigma_c),
            "horizon_hours": float(horizon_hours),
        },
    )


def _is_tail_bucket(bucket: BucketProbability, forecast_value_c: float, min_distance_c: float) -> bool:
    if bucket.upper is not None and bucket.upper <= forecast_value_c - min_distance_c:
        return True
    if bucket.lower is not None and bucket.lower >= forecast_value_c + min_distance_c:
        return True
    return False


def build_tail_hedge_plan(
    buckets: list[BucketProbability],
    *,
    forecast_value_c: float,
    regime: RegimeUncertainty | dict[str, Any],
    max_ask: float = 0.10,
    min_tail_distance_c: float = 3.0,
    max_legs: int = 2,
) -> dict[str, Any] | None:
    """Select cheap tail buckets as hedge candidates on high-uncertainty regimes."""
    level = regime.level if isinstance(regime, RegimeUncertainty) else str(regime.get("level", "low"))
    if level != "high":
        return None

    eligible: list[BucketProbability] = []
    for bucket in buckets:
        if bucket.best_ask is None or bucket.best_ask > max_ask:
            continue
        if not _is_tail_bucket(bucket, forecast_value_c, min_tail_distance_c):
            continue
        eligible.append(bucket)

    if not eligible:
        return None

    eligible.sort(key=lambda b: (b.best_ask if b.best_ask is not None else 1.0, -abs((b.model_prob or 0.0) - (b.market_prob or 0.0))))
    legs = []
    for bucket in eligible[:max_legs]:
        legs.append(
            {
                "side": bucket.label,
                "lower": bucket.lower,
                "upper": bucket.upper,
                "best_ask": bucket.best_ask,
                "market_prob": bucket.market_prob,
                "model_prob": bucket.model_prob,
                "reason": "cheap tail hedge on high uncertainty regime",
            }
        )

    return {
        "enabled": True,
        "strategy": "cheap_tail_hedge",
        "max_ask": max_ask,
        "min_tail_distance_c": min_tail_distance_c,
        "legs": legs,
    }
