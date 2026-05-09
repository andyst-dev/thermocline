from __future__ import annotations

import pytest

from weather_edge.candidates import Candidate
from weather_edge.event_exposure import EventExposureLimits, check_event_cap, synthetic_open_row
from weather_edge.parsing import bucket_probability
from weather_edge.ladder import (
    build_adjacent_ladders,
    build_ladders_from_candidates,
    candidate_bucket_bounds,
    group_exact_candidates,
    is_exact_bucket,
    ladder_size_factors,
    ladder_to_dict,
    make_ladder_id,
    score_ladder,
)


def _exact_candidate(
    temp_c: float,
    *,
    ask: float | None = 0.30,
    model_prob: float | None = 0.30,
    city: str = "Tokyo",
    target_date: str = "2026-05-08",
    forecast_value_c: float = 31.0,
    side: str | None = "Yes",
    verdict: str = "REJECT",
    fill_avg_price: float | None = None,
    bucket_width_c: float | None = 1.0,
    question: str | None = None,
    market_id: str | None = None,
    token_id: str | None = None,
    sigma_c: float | None = 1.5,
) -> Candidate:
    label = int(temp_c) if float(temp_c).is_integer() else temp_c
    return Candidate(
        verdict=verdict,
        reason="fixture",
        score=0.0,
        market_id=market_id or f"m-{label}",
        slug=f"tokyo-{label}",
        question=question or f"Will the highest temperature in {city} be {label}°C on May 8?",
        city=city,
        target_date=target_date,
        side=side,
        model_prob=model_prob,
        gamma_price=ask,
        best_bid=None,
        best_ask=ask,
        executable_ev=(model_prob - ask) if model_prob is not None and ask is not None else None,
        ask_capacity_usd=10.0,
        fill_avg_price=fill_avg_price,
        fill_shares=None,
        fill_cost_usd=None,
        fill_levels_json=None,
        book_fetched_at=None,
        book_snapshot_path=None,
        book_snapshot_hash=None,
        token_id=token_id,
        liquidity=500.0,
        confidence="high",
        forecast_value_c=forecast_value_c,
        sigma_c=sigma_c,
        horizon_hours=36.0,
        resolution_location="RJTT",
        observed_metar_count=8,
        observed_authority="weathercom_wunderground",
        bucket_width_c=bucket_width_c,
        resolution_source="weather.com",
        recommended_size_usd=None,
        regime_uncertainty={"level": "low"},
        tail_hedge_plan=None,
    )


def test_is_exact_bucket_accepts_unit_width_and_rejects_wide_or_open_ended() -> None:
    assert is_exact_bucket(_exact_candidate(31, bucket_width_c=1.0)) is True
    assert is_exact_bucket(_exact_candidate(31, bucket_width_c=0.5)) is True
    assert is_exact_bucket(_exact_candidate(31, bucket_width_c=3.0)) is False
    assert is_exact_bucket(_exact_candidate(31, question="Will the highest temperature in Tokyo be 31°C or higher on May 8?", bucket_width_c=None)) is False


def test_candidate_bucket_bounds_falls_back_to_question_parse() -> None:
    candidate = _exact_candidate(28, bucket_width_c=None, question="Will the highest temperature in Tokyo be 28ºC on May 8?")

    assert candidate_bucket_bounds(candidate) == pytest.approx((27.5, 28.5))


def test_group_exact_candidates_uses_event_key_and_keeps_reject_narrow_bucket_candidates() -> None:
    grouped = group_exact_candidates([
        _exact_candidate(28, market_id="m1", verdict="REJECT"),
        _exact_candidate(29, market_id="m2", verdict="REJECT"),
    ])

    assert len(grouped) == 1
    only_group = next(iter(grouped.values()))
    assert [candidate.market_id for candidate in only_group] == ["m1", "m2"]


def test_group_exact_candidates_filters_no_ask_non_yes_and_wide() -> None:
    grouped = group_exact_candidates([
        _exact_candidate(28, ask=None, market_id="no-ask"),
        _exact_candidate(29, side="No", market_id="no-side"),
        _exact_candidate(30, bucket_width_c=2.0, market_id="wide"),
        _exact_candidate(31, market_id="valid"),
    ])

    assert [candidate.market_id for group in grouped.values() for candidate in group] == ["valid"]


def test_build_ladder_three_adjacent_buckets_around_forecast() -> None:
    group = [
        _exact_candidate(30, ask=0.15, model_prob=0.30),
        _exact_candidate(31, ask=0.15, model_prob=0.40),
        _exact_candidate(32, ask=0.10, model_prob=0.20),
    ]

    ladders = build_adjacent_ladders(group, forecast_value_c=31.0, min_legs=3, max_legs=3, min_ev=0.0)

    assert len(ladders) == 1
    ladder = ladders[0]
    assert [leg.center_c for leg in ladder.legs] == [30.0, 31.0, 32.0]
    assert ladder.total_cost == pytest.approx(0.40)
    assert ladder.model_prob_sum == pytest.approx(0.90)
    assert ladder.prob_hit == pytest.approx(bucket_probability(29.5, 32.5, mean=31.0, sigma=1.5))
    assert ladder.ev == pytest.approx(ladder.prob_hit - ladder.total_cost)
    assert ladder.roi == pytest.approx(ladder.ev / ladder.total_cost, abs=1e-5)
    assert ladder.profit_if_hit == pytest.approx(0.60)
    assert ladder.max_loss == pytest.approx(0.40)


def test_build_ladder_uses_fill_avg_price_when_available() -> None:
    group = [
        _exact_candidate(30, ask=0.40, fill_avg_price=0.20, model_prob=0.30),
        _exact_candidate(31, ask=0.40, fill_avg_price=0.20, model_prob=0.30),
    ]

    ladder = build_adjacent_ladders(group, forecast_value_c=30.5, min_legs=2, max_legs=2)[0]

    assert ladder.total_cost == pytest.approx(0.40)


def test_ladder_prob_hit_uses_union_cdf_not_leg_probability_sum() -> None:
    group = [
        _exact_candidate(30, ask=0.20, model_prob=0.35, sigma_c=1.0),
        _exact_candidate(31, ask=0.20, model_prob=0.40, sigma_c=1.0),
        _exact_candidate(32, ask=0.20, model_prob=0.35, sigma_c=1.0),
    ]

    ladder = build_adjacent_ladders(group, forecast_value_c=31.0, min_legs=3, max_legs=3)[0]

    assert ladder.model_prob_sum == pytest.approx(1.10)
    assert ladder.prob_hit == pytest.approx(bucket_probability(29.5, 32.5, mean=31.0, sigma=1.0), abs=1e-6)
    assert ladder.prob_hit < ladder.model_prob_sum
    assert ladder.prob_method == "union_cdf"


def test_ladder_prob_hit_falls_back_to_sum_when_sigma_invalid() -> None:
    group = [
        _exact_candidate(30, ask=0.20, model_prob=0.30, sigma_c=0.0),
        _exact_candidate(31, ask=0.20, model_prob=0.40, sigma_c=0.0),
    ]

    ladder = build_adjacent_ladders(group, forecast_value_c=30.5, min_legs=2, max_legs=2)[0]
    payload = ladder_to_dict(ladder)

    assert ladder.prob_hit == pytest.approx(0.70)
    assert ladder.model_prob_sum == pytest.approx(0.70)
    assert ladder.prob_method == "sum_leg_model_probs_fallback"
    assert payload["prob_method"] == "sum_leg_model_probs_fallback"
    assert payload["model_prob_sum"] == pytest.approx(0.70)


def test_ladder_probability_fallback_caps_prob_hit_at_one() -> None:
    group = [
        _exact_candidate(30, ask=0.20, model_prob=0.40, sigma_c=0.0),
        _exact_candidate(31, ask=0.20, model_prob=0.40, sigma_c=0.0),
        _exact_candidate(32, ask=0.20, model_prob=0.40, sigma_c=0.0),
    ]

    ladder = build_adjacent_ladders(group, forecast_value_c=31.0, min_legs=3, max_legs=3)[0]

    assert ladder.model_prob_sum == pytest.approx(1.20)
    assert ladder.prob_hit == pytest.approx(1.0)
    assert ladder.ev == pytest.approx(0.40)


def test_build_ladder_filters_cost_negative_ev_non_adjacent_and_forecast_outside_range() -> None:
    assert build_adjacent_ladders([
        _exact_candidate(30, ask=0.60, model_prob=0.50),
        _exact_candidate(31, ask=0.50, model_prob=0.40),
    ], forecast_value_c=30.5) == []

    assert build_adjacent_ladders([
        _exact_candidate(30, ask=0.30, model_prob=0.10),
        _exact_candidate(31, ask=0.30, model_prob=0.10),
    ], forecast_value_c=30.5, min_ev=0.0) == []

    assert build_adjacent_ladders([
        _exact_candidate(28, ask=0.20, model_prob=0.30),
        _exact_candidate(29, ask=0.20, model_prob=0.30),
        _exact_candidate(31, ask=0.20, model_prob=0.30),
    ], forecast_value_c=30.0, min_legs=3, max_legs=3) == []

    assert build_adjacent_ladders([
        _exact_candidate(28, ask=0.20, model_prob=0.30),
        _exact_candidate(29, ask=0.20, model_prob=0.30),
    ], forecast_value_c=35.0) == []


def test_build_ladder_requires_continuous_bounds_not_just_center_gap() -> None:
    group = [
        _exact_candidate(30.25, ask=0.20, model_prob=0.40, bucket_width_c=0.5, question="Will the highest temperature in Tokyo be between 30°C and 30.5°C on May 8?"),
        _exact_candidate(31.25, ask=0.20, model_prob=0.40, bucket_width_c=0.5, question="Will the highest temperature in Tokyo be between 31°C and 31.5°C on May 8?"),
    ]

    assert build_adjacent_ladders(group, forecast_value_c=30.75) == []


def test_build_ladder_requires_minimum_two_legs_and_scores_by_ev() -> None:
    assert build_adjacent_ladders([_exact_candidate(31, ask=0.20, model_prob=0.40)], forecast_value_c=31.0) == []

    low = build_adjacent_ladders([
        _exact_candidate(30, ask=0.20, model_prob=0.35),
        _exact_candidate(31, ask=0.20, model_prob=0.35),
    ], forecast_value_c=30.5)[0]
    high = build_adjacent_ladders([
        _exact_candidate(30, ask=0.10, model_prob=0.40),
        _exact_candidate(31, ask=0.10, model_prob=0.40),
    ], forecast_value_c=30.5)[0]

    assert score_ladder(high) > score_ladder(low)


def test_ladder_size_factors_exposes_horizon_and_regime_without_usd_sizing() -> None:
    ladder = build_adjacent_ladders([
        _exact_candidate(30, ask=0.20, model_prob=0.40),
        _exact_candidate(31, ask=0.20, model_prob=0.40),
    ], forecast_value_c=30.5)[0]

    factors = ladder_size_factors(ladder, regime_level="high")

    assert factors == pytest.approx({"horizon_factor": 0.60, "regime_factor": 0.40, "product": 0.24})


def test_event_cap_would_block_third_ladder_leg_by_default() -> None:
    legs = [candidate.as_dict() for candidate in [
        _exact_candidate(30, ask=0.20, model_prob=0.40),
        _exact_candidate(31, ask=0.20, model_prob=0.40),
        _exact_candidate(32, ask=0.20, model_prob=0.40),
    ]]
    open_rows = [synthetic_open_row(legs[0], 1.0), synthetic_open_row(legs[1], 1.0)]

    allowed, reason = check_event_cap(legs[2], open_rows, proposed_size_usd=1.0, limits=EventExposureLimits(max_legs_per_event=2, max_usd_per_event=5.0))

    assert allowed is False
    assert "max_legs_per_event=2" in str(reason)


def test_build_ladders_from_candidates_and_json_report_shape() -> None:
    candidates = [
        _exact_candidate(30, ask=0.20, model_prob=0.40, forecast_value_c=30.5),
        _exact_candidate(31, ask=0.20, model_prob=0.40, forecast_value_c=30.5),
        _exact_candidate(35, ask=0.20, model_prob=0.40, target_date="2026-05-09", question="Will the highest temperature in Tokyo be 35°C on May 9?"),
    ]

    ladders = build_ladders_from_candidates(candidates)
    payload = ladder_to_dict(ladders[0], regime_level="elevated")

    assert len(ladders) == 1
    assert payload["strategy"] == "ladder_exact_range"
    assert payload["ladder_id"] == ladders[0].ladder_id
    assert payload["ladder_id"] == make_ladder_id(
        ladders[0].event_key,
        [leg.market_id for leg in ladders[0].legs],
        target_date=ladders[0].target_date,
        forecast_value_c=ladders[0].forecast_value_c,
    )
    assert all(leg["parent_ladder_id"] == payload["ladder_id"] for leg in payload["legs"])
    assert all("token_id" in leg for leg in payload["legs"])
    assert payload["event_key"]["city"] == "tokyo"
    assert payload["leg_count"] == 2
    assert payload["total_cost"] == pytest.approx(0.40)
    assert payload["prob_hit"] == pytest.approx(bucket_probability(29.5, 31.5, mean=30.5, sigma=1.5))
    assert payload["model_prob_sum"] == pytest.approx(0.80)
    assert payload["prob_method"] == "union_cdf"
    assert payload["size_factors"] == pytest.approx({"horizon_factor": 0.60, "regime_factor": 0.70, "product": 0.42})
    assert "recommended_size_usd" not in payload
