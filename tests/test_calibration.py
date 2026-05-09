"""Unit tests for weather_edge.calibration."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from weather_edge.calibration import (
    CalibrationGateResult,
    build_calibration_report,
    evaluate_calibration_gate,
    run_calibration,
)
from weather_edge.db import connect, init_db


def _insert_trade(
    db_path: Path,
    *,
    status: str = "closed",
    pnl_usd: float | None = None,
    model_prob: float | None = 0.95,
    candidate_json: dict | None = None,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO paper_trades(
                market_id, slug, question, side, entry_price, size_usd,
                model_prob, executable_ev, score, verdict, status,
                exit_price, pnl_usd, notes, candidate_json, opened_at, closed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"m-{model_prob}-{pnl_usd}-{status}-{json.dumps(candidate_json or {}, sort_keys=True)}",
                "slug",
                "Question?",
                "Yes",
                0.01,
                1.0,
                model_prob,
                0.5,
                100.0,
                "PASS",
                status,
                0.0,
                pnl_usd,
                "test",
                json.dumps(candidate_json or {}),
                "2026-04-27T00:00:00+00:00",
                "2026-04-28T00:00:00+00:00",
            ),
        )


def test_calibration_report_uses_real_average_probability_and_boundaries(tmp_path: Path) -> None:
    db_path = tmp_path / "weather_edge.db"
    init_db(db_path)
    _insert_trade(db_path, model_prob=0.10, pnl_usd=1.0)
    _insert_trade(db_path, model_prob=0.15, pnl_usd=-1.0)
    _insert_trade(db_path, model_prob=1.00, pnl_usd=1.0)

    with connect(db_path) as conn:
        report = build_calibration_report(conn)

    assert report.total_trades == 3
    assert report.brier_score == pytest.approx(((0.10 - 1) ** 2 + (0.15 - 0) ** 2 + (1.0 - 1) ** 2) / 3)

    # 10% is placed in the 10-20% bucket and the bucket average is based on
    # actual predictions, not the midpoint 15%.
    bucket_10_20 = report.buckets[1]
    assert bucket_10_20.predicted_count == 2
    assert bucket_10_20.yes_count == 1
    assert bucket_10_20.no_count == 1
    assert bucket_10_20.avg_predicted == pytest.approx(0.125)
    assert bucket_10_20.actual_frequency == pytest.approx(0.5)
    assert bucket_10_20.error == pytest.approx(-0.375)

    bucket_90_100 = report.buckets[9]
    assert bucket_90_100.predicted_count == 1
    assert bucket_90_100.avg_predicted == pytest.approx(1.0)


def test_calibration_ignores_unresolved_closed_trades(tmp_path: Path) -> None:
    db_path = tmp_path / "weather_edge.db"
    init_db(db_path)
    _insert_trade(db_path, model_prob=0.80, pnl_usd=None)
    _insert_trade(db_path, model_prob=0.70, pnl_usd=-1.0)

    with connect(db_path) as conn:
        report = build_calibration_report(conn)

    assert report.total_trades == 1
    assert report.buckets[7].predicted_count == 1


def test_run_calibration_writes_report(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "weather_edge.db"
    init_db(db_path)
    _insert_trade(db_path, model_prob=0.95, pnl_usd=-1.0)

    report, gate, path = run_calibration(db_path, output_dir=tmp_path / "reports")

    assert report.total_trades == 1
    assert gate.allowed is False
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "WEATHER EDGE — CALIBRATION REPORT" in text
    assert "Total resolved trades: 1" in text
    assert "Calibration gate:" in text


def test_calibration_gate_blocks_small_samples(tmp_path: Path) -> None:
    db_path = tmp_path / "weather_edge.db"
    init_db(db_path)
    _insert_trade(db_path, model_prob=0.60, pnl_usd=1.0)

    with connect(db_path) as conn:
        report = build_calibration_report(conn)
    gate = evaluate_calibration_gate(report, min_trades=10)

    assert gate.allowed is False
    assert gate.sample_size == 1
    assert any("sample too small" in reason for reason in gate.reasons)


def test_calibration_gate_blocks_bad_brier_score(tmp_path: Path) -> None:
    db_path = tmp_path / "weather_edge.db"
    init_db(db_path)
    for i in range(12):
        _insert_trade(db_path, model_prob=0.95, pnl_usd=-1.0, candidate_json={"i": i})

    with connect(db_path) as conn:
        report = build_calibration_report(conn)
    gate = evaluate_calibration_gate(report, min_trades=10, max_brier=0.25)

    assert gate.allowed is False
    assert gate.brier_score == pytest.approx(0.9025)
    assert any("Brier" in reason for reason in gate.reasons)


def test_calibration_gate_blocks_large_bucket_error(tmp_path: Path) -> None:
    db_path = tmp_path / "weather_edge.db"
    init_db(db_path)
    for i in range(10):
        _insert_trade(db_path, model_prob=0.80, pnl_usd=-1.0, candidate_json={"i": i})
    for i in range(10):
        _insert_trade(db_path, model_prob=0.20, pnl_usd=-1.0, candidate_json={"j": i})

    with connect(db_path) as conn:
        report = build_calibration_report(conn)
    gate = evaluate_calibration_gate(
        report,
        min_trades=10,
        max_brier=0.50,
        max_abs_bucket_error=0.20,
        min_trades_per_bucket=5,
    )

    assert gate.allowed is False
    assert gate.max_abs_bucket_error == pytest.approx(0.80)
    assert any("bucket calibration error" in reason for reason in gate.reasons)


def test_calibration_gate_allows_sufficient_well_calibrated_sample(tmp_path: Path) -> None:
    db_path = tmp_path / "weather_edge.db"
    init_db(db_path)
    # 70% bucket: 7 wins / 10 predictions around 70%.
    for i in range(7):
        _insert_trade(db_path, model_prob=0.70, pnl_usd=1.0, candidate_json={"win": i})
    for i in range(3):
        _insert_trade(db_path, model_prob=0.70, pnl_usd=-1.0, candidate_json={"loss": i})

    with connect(db_path) as conn:
        report = build_calibration_report(conn)
    gate = evaluate_calibration_gate(
        report,
        min_trades=10,
        max_brier=0.25,
        max_abs_bucket_error=0.10,
        min_trades_per_bucket=5,
    )

    assert gate.allowed is True
    assert gate.reasons == []


def test_calibration_bucket_boundaries_include_zero_and_one(tmp_path: Path) -> None:
    db_path = tmp_path / "weather_edge.db"
    init_db(db_path)
    _insert_trade(db_path, model_prob=0.0, pnl_usd=-1.0, candidate_json={"p": 0.0})
    _insert_trade(db_path, model_prob=0.20, pnl_usd=-1.0, candidate_json={"p": 0.20})
    _insert_trade(db_path, model_prob=1.0, pnl_usd=1.0, candidate_json={"p": 1.0})

    with connect(db_path) as conn:
        report = build_calibration_report(conn)

    assert report.buckets[0].predicted_count == 1
    assert report.buckets[2].predicted_count == 1
    assert report.buckets[9].predicted_count == 1


def test_won_and_lost_status_rows_do_not_need_pnl(tmp_path: Path) -> None:
    db_path = tmp_path / "weather_edge.db"
    init_db(db_path)
    _insert_trade(db_path, status="won", pnl_usd=None, model_prob=0.80, candidate_json={"status": "won"})
    _insert_trade(db_path, status="lost", pnl_usd=None, model_prob=0.20, candidate_json={"status": "lost"})

    with connect(db_path) as conn:
        report = build_calibration_report(conn)

    assert report.total_trades == 2
    assert report.buckets[8].yes_count == 1
    assert report.buckets[2].no_count == 1


def test_gate_as_dict_handles_no_eligible_bucket_error() -> None:
    gate = CalibrationGateResult(
        allowed=False,
        reasons=["sample too small: 0 resolved trades < 30"],
        sample_size=0,
        brier_score=0.0,
        max_abs_bucket_error=None,
        min_trades=30,
        max_brier=0.30,
        max_allowed_abs_bucket_error=0.25,
        min_trades_per_bucket=5,
    )

    data = gate.as_dict()

    assert data["max_abs_bucket_error"] is None
    assert data["allowed"] is False
