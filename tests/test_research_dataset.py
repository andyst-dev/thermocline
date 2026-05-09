from __future__ import annotations

import json
from datetime import datetime, timezone

from weather_edge.db import connect, init_db, insert_backtest_record
from weather_edge.models import WeatherMarket
from weather_edge.backtest import BacktestRecord
from weather_edge.research_dataset import export_research_dataset, outcome_for_temperature, read_jsonl


def _seed_market_scan_and_actual(db_path):
    init_db(db_path)
    market = WeatherMarket(
        market_id="m1",
        slug="nyc-high-temp-may-3",
        question="Will the highest temperature in New York be 20°C or above on May 3?",
        end_date=datetime(2026, 5, 4, tzinfo=timezone.utc),
        active=False,
        closed=True,
        liquidity=1234.0,
        volume=5678.0,
        outcomes=["20°C or above", "Below 20°C"],
        outcome_prices=[0.42, 0.58],
        raw={"event_slug": "new-york-temperature-may-3", "secret": "must-not-appear"},
    )
    buckets = [
        {
            "label": "20°C or above",
            "lower": 20.0,
            "upper": None,
            "market_prob": 0.42,
            "model_prob": 0.61,
            "model_prob_gaussian": 0.59,
            "model_prob_ensemble": 0.64,
            "edge": 0.19,
            "ev": 0.19,
            "best_bid": 0.40,
            "best_ask": 0.44,
            "executable_ev": 0.17,
            "token_id": "tok_yes",
        },
        {
            "label": "Below 20°C",
            "lower": None,
            "upper": 20.0,
            "market_prob": 0.58,
            "model_prob": 0.39,
            "best_bid": 0.56,
            "best_ask": 0.60,
            "executable_ev": -0.21,
            "token_id": "tok_no",
        },
    ]
    with connect(db_path) as conn:
        from weather_edge.db import upsert_markets

        upsert_markets(conn, [market])
        conn.execute(
            """
            INSERT INTO scans(
                market_id, slug, question, city, target_date,
                forecast_max_c, sigma_c, horizon_hours, liquidity,
                top_bucket_label, top_bucket_ev, confidence,
                buckets_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "m1",
                market.slug,
                market.question,
                "New York",
                "2026-05-03",
                21.2,
                1.7,
                48.0,
                market.liquidity,
                "20°C or above",
                0.19,
                "medium",
                json.dumps(buckets),
                "2026-05-01T12:00:00+00:00",
            ),
        )
        insert_backtest_record(
            conn,
            BacktestRecord(
                city="New York",
                latitude=40.7128,
                longitude=-74.0060,
                target_date="2026-05-03",
                reference_date="2026-05-01",
                horizon_hours=48.0,
                forecast_max_c=21.2,
                observed_max_c=22.0,
                residual_c=-0.8,
                metric="highest",
                model_source="openmeteo_gfs_historical",
                fetched_at="2026-05-04T01:00:00+00:00",
            ),
        )


def test_outcome_for_temperature_handles_open_and_closed_bins():
    assert outcome_for_temperature(22.0, lower=20.0, upper=None) == 1
    assert outcome_for_temperature(19.9, lower=20.0, upper=None) == 0
    assert outcome_for_temperature(20.0, lower=20.0, upper=None) == 1
    assert outcome_for_temperature(18.0, lower=None, upper=20.0) == 1
    assert outcome_for_temperature(21.0, lower=None, upper=20.0) == 0
    assert outcome_for_temperature(20.0, lower=None, upper=20.0) == 0
    assert outcome_for_temperature(20.5, lower=20.0, upper=21.0) == 1
    assert outcome_for_temperature(21.0, lower=20.0, upper=21.0) == 0
    assert outcome_for_temperature(None, lower=20.0, upper=None) is None


def test_export_research_dataset_writes_one_reproducible_jsonl_row_per_bucket(tmp_path):
    db_path = tmp_path / "weather_edge.db"
    output_path = tmp_path / "research_dataset.jsonl"
    _seed_market_scan_and_actual(db_path)

    summary = export_research_dataset(db_path, output_path)
    rows = read_jsonl(output_path)

    assert summary["rows"] == 2
    assert summary["markets"] == 1
    assert output_path.exists()
    assert [row["bucket_label"] for row in rows] == ["20°C or above", "Below 20°C"]

    yes = rows[0]
    assert yes["market_id"] == "m1"
    assert yes["city"] == "New York"
    assert yes["target_date"] == "2026-05-03"
    assert yes["forecast_max_c"] == 21.2
    assert yes["observed_max_c"] == 22.0
    assert yes["resolved_outcome"] == 1
    assert yes["market_prob"] == 0.42
    assert yes["model_prob"] == 0.61
    assert yes["model_prob_gaussian"] == 0.59
    assert yes["model_prob_ensemble"] == 0.64
    assert yes["edge"] == 0.19
    assert yes["ev"] == 0.19
    assert yes["best_ask"] == 0.44
    assert yes["event_slug"] == "new-york-temperature-may-3"
    assert "secret" not in yes
    assert "raw_json" not in yes


def test_export_research_dataset_is_stable_when_run_twice(tmp_path):
    db_path = tmp_path / "weather_edge.db"
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    _seed_market_scan_and_actual(db_path)

    export_research_dataset(db_path, first)
    export_research_dataset(db_path, second)

    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")
