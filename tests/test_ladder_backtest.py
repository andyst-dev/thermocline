from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from weather_edge.db import connect, init_db
from weather_edge.ladder_backtest import (
    build_ladder_backtest_report,
    format_ladder_backtest_summary,
    write_ladder_backtest_report,
)


def _contains_key(payload: Any, key: str) -> bool:
    if isinstance(payload, dict):
        return key in payload or any(_contains_key(value, key) for value in payload.values())
    if isinstance(payload, list):
        return any(_contains_key(item, key) for item in payload)
    return False


def _insert_backtest_record(db_path: Path, *, city: str, forecast: float, observed: float, horizon: float = 24.0) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO backtest_records(
                city, latitude, longitude, target_date, reference_date,
                horizon_hours, forecast_max_c, observed_max_c, residual_c,
                metric, model_source, fetched_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                city,
                0.0,
                0.0,
                "2026-05-08",
                "2026-05-07",
                horizon,
                forecast,
                observed,
                observed - forecast,
                "highest",
                "fixture",
                "2026-05-07T00:00:00+00:00",
                "2026-05-07T00:00:00+00:00",
            ),
        )


def test_ladder_backtest_report_empty_is_safe_and_explicit(tmp_path: Path) -> None:
    db_path = tmp_path / "weather-edge.sqlite3"
    init_db(db_path)

    payload = build_ladder_backtest_report(db_path, generated_at="2026-05-09T00:00:00+00:00")

    assert payload["safety"] == {
        "report_only": True,
        "no_orders_placed": True,
        "no_paper_open_integration": True,
        "no_recommended_size_usd": True,
    }
    assert payload["decision"] == "insufficient_data_for_paper_ladder_decision"
    assert payload["sufficient_data"] is False
    assert payload["input_summary"]["backtest_records"] == 0
    assert payload["no_go_reasons"]
    assert any("insufficient backtest_records" in reason for reason in payload["no_go_reasons"])
    assert not _contains_key(payload, "recommended_size_usd")

    for strategy in payload["strategies"]:
        for field in ["trade_count", "hit_rate", "avg_cost", "pnl", "roi", "max_drawdown", "brier", "calibration", "by_city", "by_horizon"]:
            assert field in strategy
        assert strategy["trade_count"] == 0
        assert strategy["hit_rate"] is None
        assert strategy["avg_cost"] is None
        assert strategy["pnl"] is None
        assert strategy["roi"] is None

    summary = format_ladder_backtest_summary(payload)
    assert "read-only" in summary
    assert "No-go" in summary


def test_ladder_backtest_report_hit_rates_expand_with_wider_ladders(tmp_path: Path) -> None:
    db_path = tmp_path / "weather-edge.sqlite3"
    init_db(db_path)
    _insert_backtest_record(db_path, city="Tokyo", forecast=30.2, observed=30.1, horizon=24.0)  # single, pm1, pm2 hit
    _insert_backtest_record(db_path, city="Tokyo", forecast=30.2, observed=31.0, horizon=48.0)  # pm1, pm2 hit
    _insert_backtest_record(db_path, city="Paris", forecast=30.2, observed=32.0, horizon=72.0)  # pm2 hit
    _insert_backtest_record(db_path, city="Paris", forecast=30.2, observed=34.0, horizon=96.0)  # miss all

    output_path = tmp_path / "reports" / "ladder_backtest_report.json"
    payload = write_ladder_backtest_report(db_path, output_path, generated_at="2026-05-09T00:00:00+00:00")

    assert output_path.exists()
    assert json.loads(output_path.read_text(encoding="utf-8"))["report_version"] == payload["report_version"]
    by_name = {strategy["name"]: strategy for strategy in payload["strategies"]}

    assert by_name["single_best_bucket"]["trade_count"] == 4
    assert by_name["single_best_bucket"]["hit_rate"] == 0.25
    assert by_name["ladder_pm_1c"]["hit_rate"] == 0.50
    assert by_name["ladder_pm_2c"]["hit_rate"] == 0.75
    assert by_name["single_best_bucket"]["hit_rate"] <= by_name["ladder_pm_1c"]["hit_rate"] <= by_name["ladder_pm_2c"]["hit_rate"]
    assert "Tokyo" in by_name["ladder_pm_1c"]["by_city"]
    assert by_name["ladder_pm_1c"]["by_horizon"]
    assert payload["no_go_reasons"]
    assert payload["sufficient_data"] is False
    assert payload["hit_rate_sample_sufficient"] is False
    assert not _contains_key(payload, "recommended_size_usd")
