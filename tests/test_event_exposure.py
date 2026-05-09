from __future__ import annotations

import json

import pytest

from weather_edge.event_exposure import (
    EventExposureLimits,
    check_event_cap,
    current_exposure,
    current_open_event_count,
    event_key_for_candidate,
)


def _candidate(**overrides):
    data = {
        "city": "Tokyo",
        "target_date": "2026-05-08",
        "question": "Will the highest temperature in Tokyo be 28°C on May 8?",
        "market_id": "m1",
        "recommended_size_usd": 2.0,
    }
    data.update(overrides)
    return data


def _row(candidate: dict, *, status: str = "open", size_usd: float = 1.0):
    return {
        "status": status,
        "size_usd": size_usd,
        "candidate_json": json.dumps(candidate),
        "question": candidate.get("question", ""),
    }


def test_event_key_groups_same_city_date_metric_across_markets() -> None:
    first = event_key_for_candidate(_candidate(market_id="m1", question="Will the highest temperature in Tokyo be 28°C on May 8?"))
    second = event_key_for_candidate(_candidate(market_id="m2", question="Will the highest temperature in Tokyo be 29°C on May 8?"))

    assert second == first


def test_event_key_distinguishes_metric_and_date() -> None:
    highest = event_key_for_candidate(_candidate(question="Will the highest temperature in Tokyo be 28°C on May 8?"))
    lowest = event_key_for_candidate(_candidate(question="Will the lowest temperature in Tokyo be 18°C on May 8?"))
    next_day = event_key_for_candidate(_candidate(target_date="2026-05-09", question="Will the highest temperature in Tokyo be 28°C on May 9?"))

    assert lowest != highest
    assert next_day != highest


def test_event_key_uses_event_id_when_present() -> None:
    with_event = event_key_for_candidate(_candidate(event_id="evt-123", question="Will the highest temperature in Tokyo be 28°C on May 8?"))
    same_event_different_city_text = event_key_for_candidate(_candidate(city="Tokio", event_id="evt-123", question="Will the highest temperature in Tokio be 29°C on May 8?"))

    assert same_event_different_city_text == with_event


def test_current_exposure_counts_open_only_for_matching_event() -> None:
    key = event_key_for_candidate(_candidate(question="Will the highest temperature in Tokyo be 28°C on May 8?"))
    rows = [
        _row(_candidate(market_id="m1", question="Will the highest temperature in Tokyo be 28°C on May 8?"), status="open", size_usd=2.0),
        _row(_candidate(market_id="m2", question="Will the highest temperature in Tokyo be 29°C on May 8?"), status="open", size_usd=1.5),
        _row(_candidate(market_id="m3", question="Will the highest temperature in Tokyo be 30°C on May 8?"), status="closed", size_usd=99.0),
        _row(_candidate(market_id="m4", target_date="2026-05-09", question="Will the highest temperature in Tokyo be 28°C on May 9?"), status="open", size_usd=7.0),
    ]

    count, usd = current_exposure(rows, key)

    assert count == 2
    assert usd == pytest.approx(3.5)


def test_check_event_cap_blocks_third_leg_by_count() -> None:
    candidate = _candidate(market_id="m3", question="Will the highest temperature in Tokyo be 30°C on May 8?")
    rows = [
        _row(_candidate(market_id="m1", question="Will the highest temperature in Tokyo be 28°C on May 8?"), size_usd=1.0),
        _row(_candidate(market_id="m2", question="Will the highest temperature in Tokyo be 29°C on May 8?"), size_usd=1.0),
    ]

    allowed, reason = check_event_cap(candidate, rows, proposed_size_usd=1.0, limits=EventExposureLimits(max_legs_per_event=2, max_usd_per_event=5.0))

    assert allowed is False
    assert "max_legs_per_event=2" in str(reason)


def test_check_event_cap_blocks_when_usd_would_exceed() -> None:
    rows = [_row(_candidate(market_id="m1"), size_usd=4.0)]

    allowed, reason = check_event_cap(_candidate(market_id="m2", question="Will the highest temperature in Tokyo be 29°C on May 8?"), rows, proposed_size_usd=2.0, limits=EventExposureLimits(max_legs_per_event=3, max_usd_per_event=5.0))

    assert allowed is False
    assert "max_usd_per_event=5.00" in str(reason)
    assert "4.00 + 2.00" in str(reason)


def test_check_event_cap_allows_distinct_event() -> None:
    rows = [_row(_candidate(market_id="m1"), size_usd=5.0)]

    allowed, reason = check_event_cap(_candidate(market_id="m2", target_date="2026-05-09", question="Will the highest temperature in Tokyo be 29°C on May 9?"), rows, proposed_size_usd=2.0, limits=EventExposureLimits(max_legs_per_event=1, max_usd_per_event=5.0))

    assert allowed is True
    assert reason is None


def test_current_open_event_count_counts_distinct_open_events_only() -> None:
    rows = [
        _row(_candidate(market_id="m1", question="Will the highest temperature in Tokyo be 28°C on May 8?"), status="open"),
        _row(_candidate(market_id="m2", question="Will the highest temperature in Tokyo be 29°C on May 8?"), status="open"),
        _row(_candidate(market_id="m3", target_date="2026-05-09", question="Will the highest temperature in Tokyo be 28°C on May 9?"), status="open"),
        _row(_candidate(market_id="m4", target_date="2026-05-10", question="Will the highest temperature in Tokyo be 28°C on May 10?"), status="closed"),
    ]

    assert current_open_event_count(rows) == 2


def test_check_event_cap_blocks_new_event_when_open_event_cap_reached() -> None:
    rows = [_row(_candidate(market_id="m1"), size_usd=1.0)]
    new_event = _candidate(market_id="m2", target_date="2026-05-09", question="Will the highest temperature in Tokyo be 29°C on May 9?")

    allowed, reason = check_event_cap(new_event, rows, proposed_size_usd=1.0, limits=EventExposureLimits(max_legs_per_event=2, max_usd_per_event=5.0, max_open_events=1))

    assert allowed is False
    assert "max_open_events=1" in str(reason)


def test_check_event_cap_allows_existing_event_when_open_event_cap_reached() -> None:
    rows = [_row(_candidate(market_id="m1", question="Will the highest temperature in Tokyo be 28°C on May 8?"), size_usd=1.0)]
    same_event = _candidate(market_id="m2", question="Will the highest temperature in Tokyo be 29°C on May 8?")

    allowed, reason = check_event_cap(same_event, rows, proposed_size_usd=1.0, limits=EventExposureLimits(max_legs_per_event=2, max_usd_per_event=5.0, max_open_events=1))

    assert allowed is True
    assert reason is None
