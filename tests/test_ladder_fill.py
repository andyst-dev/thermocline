from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from weather_edge.candidates import Candidate
from weather_edge.config import Settings
from weather_edge.ladder import build_adjacent_ladders
from weather_edge.ladder_fill import simulate_ladder_fill


@dataclass(frozen=True)
class _Fill:
    best_bid: float | None
    best_ask: float | None
    avg_price: float | None
    shares: float
    cost_usd: float
    requested_usd: float
    filled: bool
    capacity_usd_at_best_ask: float | None
    levels_used: list[dict[str, float]]
    book_fetched_at: str
    book_payload: dict[str, Any]


def _settings(tmp_path: Path) -> Settings:
    return Settings(project_root=tmp_path, db_path=tmp_path / "weather_edge.db")


def _exact_candidate(
    temp_c: float,
    *,
    ask: float = 0.10,
    model_prob: float = 0.30,
    market_id: str,
    token_id: str | None,
    forecast_value_c: float = 30.5,
) -> Candidate:
    label = int(temp_c) if float(temp_c).is_integer() else temp_c
    return Candidate(
        verdict="REJECT",
        reason="fixture",
        score=0.0,
        market_id=market_id,
        slug=f"tokyo-{label}",
        question=f"Will the highest temperature in Tokyo be {label}°C on May 8?",
        city="Tokyo",
        target_date="2026-05-08",
        side="Yes",
        model_prob=model_prob,
        gamma_price=ask,
        best_bid=None,
        best_ask=ask,
        executable_ev=model_prob - ask,
        ask_capacity_usd=10.0,
        fill_avg_price=None,
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
        sigma_c=1.5,
        horizon_hours=36.0,
        resolution_location="RJTT",
        observed_metar_count=8,
        observed_authority="weathercom_wunderground",
        bucket_width_c=1.0,
        resolution_source="weather.com",
        recommended_size_usd=None,
        regime_uncertainty={"level": "low"},
        tail_hedge_plan=None,
    )


def test_simulate_ladder_fill_all_legs_filled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, float, float]] = []

    def fake_simulate(settings: Settings, token_id: str, usd_size: float, *, max_avg_price: float = 0.10) -> _Fill:
        calls.append((token_id, usd_size, max_avg_price))
        return _Fill(
            best_bid=0.09,
            best_ask=0.10,
            avg_price=0.10,
            shares=usd_size / 0.10,
            cost_usd=usd_size,
            requested_usd=usd_size,
            filled=True,
            capacity_usd_at_best_ask=25.0,
            levels_used=[{"price": 0.10, "shares": usd_size / 0.10, "cost_usd": usd_size}],
            book_fetched_at="2026-05-09T00:00:00+00:00",
            book_payload={"token_id": token_id, "asks": [{"price": "0.10", "size": "100"}]},
        )

    monkeypatch.setattr("weather_edge.clients.clob.simulate_buy_fill", fake_simulate)
    ladder = build_adjacent_ladders(
        [
            _exact_candidate(30, market_id="m30", token_id="t30", ask=0.10, model_prob=0.30),
            _exact_candidate(31, market_id="m31", token_id="t31", ask=0.10, model_prob=0.30),
        ],
        forecast_value_c=30.5,
    )[0]

    payload = simulate_ladder_fill(_settings(tmp_path), ladder, requested_usd_per_leg=1.0, max_avg_price=0.20)

    assert calls == [("t30", 1.0, 0.20), ("t31", 1.0, 0.20)]
    assert payload["read_only"] is True
    assert payload["no_orders_placed"] is True
    assert payload["executable"] is True
    assert payload["all_legs_filled"] is True
    assert payload["total_requested_usd"] == pytest.approx(2.0)
    assert payload["total_cost_usd"] == pytest.approx(2.0)
    assert payload["total_shares"] == pytest.approx(20.0)
    assert payload["avg_price_weighted"] == pytest.approx(0.10)
    assert payload["max_leg_avg_price"] == pytest.approx(0.10)
    assert payload["rejection_reasons"] == []
    assert all(leg["parent_ladder_id"] == ladder.ladder_id for leg in payload["legs"])
    assert all(leg["book_snapshot_path"] for leg in payload["legs"])


def test_simulate_ladder_fill_blocks_missing_token_or_failed_leg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_simulate(settings: Settings, token_id: str, usd_size: float, *, max_avg_price: float = 0.10) -> _Fill:
        return _Fill(
            best_bid=None,
            best_ask=0.20,
            avg_price=0.20,
            shares=usd_size / 0.20,
            cost_usd=usd_size,
            requested_usd=usd_size,
            filled=False,
            capacity_usd_at_best_ask=0.50,
            levels_used=[],
            book_fetched_at="2026-05-09T00:00:00+00:00",
            book_payload={"token_id": token_id},
        )

    monkeypatch.setattr("weather_edge.clients.clob.simulate_buy_fill", fake_simulate)
    ladder = build_adjacent_ladders(
        [
            _exact_candidate(30, market_id="m30", token_id=None, ask=0.10, model_prob=0.30),
            _exact_candidate(31, market_id="m31", token_id="t31", ask=0.10, model_prob=0.30),
        ],
        forecast_value_c=30.5,
    )[0]

    payload = simulate_ladder_fill(_settings(tmp_path), ladder, requested_usd_per_leg=1.0, max_avg_price=0.10)

    assert payload["executable"] is False
    assert payload["all_legs_filled"] is False
    assert any("missing token_id" in reason for reason in payload["rejection_reasons"])
    assert any("not fully fillable" in reason for reason in payload["rejection_reasons"])
    assert payload["legs"][0]["book_snapshot_path"] is None
    assert payload["legs"][1]["book_snapshot_path"]
