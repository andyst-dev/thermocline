from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import BucketProbability, ScanResult, WeatherMarket


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
    liquidity: float
    confidence: str
    forecast_value_c: float
    sigma_c: float
    horizon_hours: float
    resolution_location: str | None
    observed_metar_count: int
    resolution_source: str | None

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
            "liquidity": self.liquidity,
            "confidence": self.confidence,
            "forecast_value_c": round(self.forecast_value_c, 2),
            "sigma_c": round(self.sigma_c, 2),
            "horizon_hours": round(self.horizon_hours, 2),
            "resolution_location": self.resolution_location,
            "observed_metar_count": self.observed_metar_count,
            "resolution_source": self.resolution_source,
        }


def _top_bucket(result: ScanResult) -> BucketProbability | None:
    with_exec = [b for b in result.buckets if b.executable_ev is not None]
    if with_exec:
        return max(with_exec, key=lambda b: b.executable_ev or -999)
    return max(result.buckets, key=lambda b: b.ev, default=None)


def build_candidate(market: WeatherMarket, result: ScanResult, forecast_meta: dict[str, Any]) -> Candidate:
    top = _top_bucket(result)
    context = forecast_meta.get("context") or {}
    resolution_location = context.get("resolution_location")
    observed_count = int(context.get("observed_metar_count") or 0)
    resolution_source = market.raw.get("resolutionSource") or market.raw.get("resolution_source")

    blockers: list[str] = []
    cautions: list[str] = []
    if top is None:
        blockers.append("no priced bucket")
    if result.city == "Global":
        cautions.append("global climate market, not target city weather niche")
    if top and top.best_ask is None:
        blockers.append("no executable ask found")
    if top and (top.ask_capacity_usd is None or top.ask_capacity_usd < 1.0):
        blockers.append("insufficient ask depth for $1 paper fill")
    if top and top.best_ask is not None and top.best_ask > 0.10:
        cautions.append("ask above cheap-tail threshold")
    if top and top.executable_ev is not None and top.executable_ev < 0.15:
        blockers.append("executable EV below threshold")
    if result.liquidity < 250:
        blockers.append("low liquidity")
    if result.confidence != "high":
        cautions.append("model confidence not high")
    if resolution_location and isinstance(resolution_location, str) and len(resolution_location) == 4:
        if observed_count < 6 and result.horizon_hours <= 24:
            cautions.append("few/no same-day METAR observations")
    else:
        cautions.append("no ICAO station lock")
    if not resolution_source:
        cautions.append("missing resolution source")

    exec_ev = top.executable_ev if top else None
    ask = top.best_ask if top else None
    model_prob = top.model_prob if top else None
    gamma_price = top.market_prob if top else None
    ask_capacity = top.ask_capacity_usd if top else None

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
        liquidity=result.liquidity,
        confidence=result.confidence,
        forecast_value_c=result.forecast_max_c,
        sigma_c=result.sigma_c,
        horizon_hours=result.horizon_hours,
        resolution_location=resolution_location,
        observed_metar_count=observed_count,
        resolution_source=str(resolution_source) if resolution_source else None,
    )
