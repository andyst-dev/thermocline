from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Sequence

from .candidates import Candidate
from .event_exposure import EventKey, event_key_for_candidate
from .parsing import bucket_probability, parse_temperature_contract
from .risk import horizon_scale_factor, regime_scale_factor


LADDER_ID_VERSION = "v1"


@dataclass(frozen=True)
class LadderLeg:
    market_id: str
    slug: str
    side: str | None
    token_id: str | None
    lower_c: float | None
    upper_c: float | None
    center_c: float
    ask: float
    model_prob: float
    sigma_c: float | None


@dataclass(frozen=True)
class LadderCandidate:
    ladder_id: str
    event_key: EventKey
    city: str
    target_date: str
    forecast_value_c: float
    legs: tuple[LadderLeg, ...]
    total_cost: float
    prob_hit: float
    model_prob_sum: float
    prob_method: str
    prob_source_sigma_c: float | None
    ev: float
    roi: float
    profit_if_hit: float
    max_loss: float
    horizon_hours: float
    width_c: float


def _event_key_id_part(key: EventKey) -> str:
    if key.event_id:
        return f"evt:{key.event_id}"
    return f"city:{key.city}|date:{key.target_date}|metric:{key.metric}"


def make_ladder_id(
    event_key: EventKey,
    leg_market_ids: Sequence[str],
    *,
    target_date: str,
    forecast_value_c: float,
) -> str:
    """Build a deterministic id for a ladder candidate.

    The id is stable given the same event grouping, ordered leg market ids,
    target date, and forecast (rounded to 0.01°C). Two scans that produce the
    same ladder will get the same id, allowing downstream traceability.
    """
    payload = "|".join([
        LADDER_ID_VERSION,
        _event_key_id_part(event_key),
        f"target:{target_date}",
        f"forecast:{round(float(forecast_value_c), 2):.2f}",
        "legs:" + ",".join(str(market_id) for market_id in leg_market_ids),
    ])
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"ladder_{LADDER_ID_VERSION}_{digest}"


def _candidate_dict(candidate: Candidate) -> dict:
    return candidate.as_dict()


def candidate_bucket_bounds(candidate: Candidate) -> tuple[float, float] | None:
    """Return closed Celsius bucket bounds, excluding open-ended contracts."""
    contract = parse_temperature_contract(candidate.question)
    if contract is None or contract.lower_c is None or contract.upper_c is None:
        return None
    lower = float(contract.lower_c)
    upper = float(contract.upper_c)
    if upper <= lower:
        return None
    return lower, upper


def is_exact_bucket(candidate: Candidate) -> bool:
    """Return True for exact/narrow temperature buckets eligible for ladders."""
    bounds = candidate_bucket_bounds(candidate)
    if bounds is None:
        return False
    if candidate.bucket_width_c is not None:
        return 0.0 < float(candidate.bucket_width_c) <= 1.01
    lower, upper = bounds
    return 0.0 < (upper - lower) <= 1.01


def _leg_ask(candidate: Candidate) -> float | None:
    price = candidate.fill_avg_price if candidate.fill_avg_price is not None else candidate.best_ask
    if price is None:
        return None
    price = float(price)
    if price <= 0.0 or price >= 1.0:
        return None
    return price


def _eligible(candidate: Candidate) -> bool:
    if str(candidate.side or "").lower() != "yes":
        return False
    if candidate.model_prob is None:
        return False
    if _leg_ask(candidate) is None:
        return False
    return is_exact_bucket(candidate)


def group_exact_candidates(candidates: Iterable[Candidate]) -> dict[EventKey, list[Candidate]]:
    """Group ladder-eligible exact-temperature candidates by weather event.

    This intentionally does not filter on Candidate.verdict: single-bucket REJECTs
    caused by exact/narrow bucket risk can still be valid ladder ingredients.
    """
    grouped: dict[EventKey, list[Candidate]] = {}
    for candidate in candidates:
        if not _eligible(candidate):
            continue
        key = event_key_for_candidate(_candidate_dict(candidate))
        grouped.setdefault(key, []).append(candidate)
    return grouped


def _leg_from_candidate(candidate: Candidate) -> LadderLeg | None:
    bounds = candidate_bucket_bounds(candidate)
    ask = _leg_ask(candidate)
    if bounds is None or ask is None or candidate.model_prob is None:
        return None
    lower, upper = bounds
    return LadderLeg(
        market_id=candidate.market_id,
        slug=candidate.slug,
        side=candidate.side,
        token_id=candidate.token_id,
        lower_c=lower,
        upper_c=upper,
        center_c=(lower + upper) / 2.0,
        ask=ask,
        model_prob=float(candidate.model_prob),
        sigma_c=float(candidate.sigma_c) if candidate.sigma_c is not None else None,
    )


def _adjacent_runs(legs: Sequence[LadderLeg], *, max_gap_c: float = 0.01) -> list[list[LadderLeg]]:
    if not legs:
        return []
    runs: list[list[LadderLeg]] = [[legs[0]]]
    for leg in legs[1:]:
        previous = runs[-1][-1]
        if previous.upper_c is not None and leg.lower_c is not None and abs(leg.lower_c - previous.upper_c) <= max_gap_c:
            runs[-1].append(leg)
        else:
            runs.append([leg])
    return runs


def _forecast_inside_window(legs: Sequence[LadderLeg], forecast_value_c: float) -> bool:
    lowers = [leg.lower_c for leg in legs if leg.lower_c is not None]
    uppers = [leg.upper_c for leg in legs if leg.upper_c is not None]
    if not lowers or not uppers:
        return False
    return min(lowers) <= float(forecast_value_c) <= max(uppers)


def _ladder_probability(window: Sequence[LadderLeg], *, forecast_value_c: float) -> tuple[float, float, str, float | None]:
    """Estimate probability that a mutually-exclusive exact-range ladder pays.

    Prefer one CDF over the union of continuous adjacent bounds. Summing leg
    probabilities is retained only as a fallback for malformed/missing sigma,
    because it can exceed 1.0 and overstate EV before ladder paper trading.
    """
    model_prob_sum = sum(leg.model_prob for leg in window)
    lowers = [leg.lower_c for leg in window if leg.lower_c is not None]
    uppers = [leg.upper_c for leg in window if leg.upper_c is not None]
    sigmas = [float(leg.sigma_c) for leg in window if leg.sigma_c is not None and float(leg.sigma_c) > 0.0]
    if lowers and uppers and len(sigmas) == len(window):
        sigma_c = sum(sigmas) / len(sigmas)
        prob_hit = bucket_probability(min(lowers), max(uppers), float(forecast_value_c), sigma_c)
        return prob_hit, model_prob_sum, "union_cdf", sigma_c
    capped_prob_hit = min(model_prob_sum, 1.0)
    return capped_prob_hit, model_prob_sum, "sum_leg_model_probs_fallback", None


def _build_ladder(
    window: Sequence[LadderLeg],
    *,
    event_key: EventKey,
    city: str,
    target_date: str,
    forecast_value_c: float,
    horizon_hours: float,
) -> LadderCandidate:
    total_cost = sum(leg.ask for leg in window)
    prob_hit, model_prob_sum, prob_method, prob_source_sigma_c = _ladder_probability(window, forecast_value_c=forecast_value_c)
    ev = prob_hit - total_cost
    roi = ev / total_cost if total_cost > 0 else 0.0
    lowers = [leg.lower_c for leg in window if leg.lower_c is not None]
    uppers = [leg.upper_c for leg in window if leg.upper_c is not None]
    width_c = max(uppers) - min(lowers) if lowers and uppers else 0.0
    ladder_id = make_ladder_id(
        event_key,
        [leg.market_id for leg in window],
        target_date=target_date,
        forecast_value_c=float(forecast_value_c),
    )
    return LadderCandidate(
        ladder_id=ladder_id,
        event_key=event_key,
        city=city,
        target_date=target_date,
        forecast_value_c=float(forecast_value_c),
        legs=tuple(window),
        total_cost=round(total_cost, 6),
        prob_hit=round(prob_hit, 6),
        model_prob_sum=round(model_prob_sum, 6),
        prob_method=prob_method,
        prob_source_sigma_c=round(prob_source_sigma_c, 6) if prob_source_sigma_c is not None else None,
        ev=round(ev, 6),
        roi=round(roi, 6),
        profit_if_hit=round(1.0 - total_cost, 6),
        max_loss=round(total_cost, 6),
        horizon_hours=float(horizon_hours),
        width_c=round(width_c, 6),
    )


def build_adjacent_ladders(
    group: Sequence[Candidate],
    *,
    forecast_value_c: float,
    max_legs: int = 3,
    min_legs: int = 2,
    max_total_cost: float = 1.0,
    min_ev: float = 0.0,
    min_roi: float = 0.0,
) -> list[LadderCandidate]:
    """Build pure read-only exact-temperature ladder candidates for one event group."""
    eligible = [candidate for candidate in group if _eligible(candidate)]
    if len(eligible) < min_legs:
        return []
    key = event_key_for_candidate(_candidate_dict(eligible[0]))
    legs = [_leg_from_candidate(candidate) for candidate in eligible]
    sorted_legs = sorted((leg for leg in legs if leg is not None), key=lambda leg: leg.center_c)
    ladders: list[LadderCandidate] = []
    seen: set[tuple[str, ...]] = set()
    for run in _adjacent_runs(sorted_legs):
        upper_size = min(max_legs, len(run))
        for size in range(min_legs, upper_size + 1):
            for start in range(0, len(run) - size + 1):
                window = run[start : start + size]
                ids = tuple(leg.market_id for leg in window)
                if ids in seen:
                    continue
                if not _forecast_inside_window(window, forecast_value_c):
                    continue
                ladder = _build_ladder(
                    window,
                    event_key=key,
                    city=eligible[0].city,
                    target_date=eligible[0].target_date,
                    forecast_value_c=forecast_value_c,
                    horizon_hours=eligible[0].horizon_hours,
                )
                if ladder.total_cost >= float(max_total_cost):
                    continue
                if ladder.ev < float(min_ev):
                    continue
                if ladder.roi < float(min_roi):
                    continue
                seen.add(ids)
                ladders.append(ladder)
    ladders.sort(key=score_ladder, reverse=True)
    return ladders


def score_ladder(ladder: LadderCandidate) -> float:
    """Scalar ranking score for read-only ladder candidates."""
    centers = [leg.center_c for leg in ladder.legs]
    if centers:
        midpoint = (min(centers) + max(centers)) / 2.0
        centered_bonus = max(0.0, 1.0 - abs(ladder.forecast_value_c - midpoint))
    else:
        centered_bonus = 0.0
    return round((ladder.ev * 100.0) + (ladder.roi * 10.0) + centered_bonus, 6)


def ladder_size_factors(ladder: LadderCandidate, *, regime_level: str | None) -> dict[str, float]:
    """Expose risk scale factors only; dollar sizing is decided by callers later."""
    horizon_factor = horizon_scale_factor(ladder.horizon_hours)
    regime_factor = regime_scale_factor(regime_level)
    return {
        "horizon_factor": horizon_factor,
        "regime_factor": regime_factor,
        "product": round(horizon_factor * regime_factor, 6),
    }


def build_ladders_from_candidates(
    candidates: Iterable[Candidate],
    *,
    max_legs: int = 3,
    min_legs: int = 2,
    max_total_cost: float = 1.0,
    min_ev: float = 0.0,
    min_roi: float = 0.0,
) -> list[LadderCandidate]:
    """Build all read-only exact-range ladders from a flat candidate list."""
    ladders: list[LadderCandidate] = []
    for group in group_exact_candidates(candidates).values():
        if not group:
            continue
        ladders.extend(
            build_adjacent_ladders(
                group,
                forecast_value_c=group[0].forecast_value_c,
                max_legs=max_legs,
                min_legs=min_legs,
                max_total_cost=max_total_cost,
                min_ev=min_ev,
                min_roi=min_roi,
            )
        )
    ladders.sort(key=score_ladder, reverse=True)
    return ladders


def _event_key_to_dict(key: EventKey) -> dict[str, str | None]:
    return {
        "city": key.city,
        "target_date": key.target_date,
        "metric": key.metric,
        "event_id": key.event_id,
    }


def ladder_to_dict(ladder: LadderCandidate, *, regime_level: str | None = None) -> dict:
    """Serialize a ladder candidate for reports without adding execution sizing."""
    payload = {
        "strategy": "ladder_exact_range",
        "ladder_id": ladder.ladder_id,
        "event_key": _event_key_to_dict(ladder.event_key),
        "city": ladder.city,
        "target_date": ladder.target_date,
        "forecast_value_c": round(ladder.forecast_value_c, 4),
        "leg_count": len(ladder.legs),
        "legs": [
            {
                "parent_ladder_id": ladder.ladder_id,
                "market_id": leg.market_id,
                "slug": leg.slug,
                "side": leg.side,
                "token_id": leg.token_id,
                "lower_c": leg.lower_c,
                "upper_c": leg.upper_c,
                "center_c": round(leg.center_c, 4),
                "ask": round(leg.ask, 6),
                "model_prob": round(leg.model_prob, 6),
                "sigma_c": round(leg.sigma_c, 6) if leg.sigma_c is not None else None,
            }
            for leg in ladder.legs
        ],
        "total_cost": ladder.total_cost,
        "prob_hit": ladder.prob_hit,
        "model_prob_sum": ladder.model_prob_sum,
        "prob_method": ladder.prob_method,
        "prob_source_sigma_c": ladder.prob_source_sigma_c,
        "ev": ladder.ev,
        "roi": ladder.roi,
        "profit_if_hit": ladder.profit_if_hit,
        "max_loss": ladder.max_loss,
        "horizon_hours": round(ladder.horizon_hours, 4),
        "width_c": ladder.width_c,
        "score": score_ladder(ladder),
    }
    if regime_level is not None:
        payload["size_factors"] = ladder_size_factors(ladder, regime_level=regime_level)
    return payload
