from __future__ import annotations

from weather_edge import closed_trades_audit
from weather_edge.closed_trades_audit import (
    classify_temperature_comparator,
    summarize_closed_trade_rows,
    _build_confusion_matrix,
    _brier_score_from_recomputed,
    _summarize_forecast_error_diagnostics,
    _summarize_recomputed_trade_rows,
)


def test_classify_temperature_comparator() -> None:
    assert classify_temperature_comparator("Will the highest temperature in Paris be 20°C or below on May 7?") == "or_below"
    assert classify_temperature_comparator("Will the highest temperature in Paris be 20°C or higher on May 7?") == "or_higher"
    assert classify_temperature_comparator("Will the highest temperature in Paris be between 20-21°C on May 7?") == "between"
    assert classify_temperature_comparator("Will the highest temperature in Paris be 20°C on May 7?") == "exact"
    assert classify_temperature_comparator("Will it rain in Paris?") == "unknown"


def test_summarize_closed_trade_rows_groups_recorded_outcomes() -> None:
    rows = [
        {
            "question": "Will the highest temperature in Paris be 20°C or below on May 7?",
            "side": "Yes",
            "model_prob": 0.9,
            "pnl_usd": -1.0,
            "opened_at": "2026-05-06T12:00:00+00:00",
            "notes": "official",
            "candidate": {"city": "Paris", "resolution_location": "LFPB"},
        },
        {
            "question": "Will the highest temperature in Paris be 20°C or higher on May 7?",
            "side": "No",
            "model_prob": 0.6,
            "pnl_usd": 2.0,
            "opened_at": "2026-05-06T13:00:00+00:00",
            "notes": "official",
            "candidate": {"city": "Paris", "resolution_location": "LFPB"},
        },
    ]

    summary = summarize_closed_trade_rows(rows)

    assert summary["sample_size"] == 2
    assert summary["avg_model_prob"] == 0.75
    assert summary["recorded_wins"] == 1
    assert summary["recorded_win_rate"] == 0.5
    assert summary["pnl_usd"] == 1.0
    assert summary["groups"]["by_comparator"]["or_below"]["count"] == 1
    assert summary["groups"]["by_comparator"]["or_higher"]["count"] == 1
    assert summary["groups"]["by_side"]["Yes"]["recorded_wins"] == 0
    assert summary["groups"]["by_side"]["No"]["recorded_wins"] == 1


def test_recomputed_observation_does_not_use_metar_as_official_fallback(monkeypatch) -> None:
    def fake_official(source, target_date, metric):
        return None, 3, "weather.com aggregate/sample divergence"

    def fake_metar(icao, target_date, timezone_name, metric):
        return 18.3, 27

    monkeypatch.setattr(closed_trades_audit, "official_extreme_c", fake_official)
    monkeypatch.setattr(closed_trades_audit, "observed_extreme_c", fake_metar)

    result = closed_trades_audit._recomputed_observation(
        {
            "resolution_location": "KLGA",
            "resolution_source": "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA",
            "timezone": "America/New_York",
        },
        "Will the highest temperature in New York City be 58°F or higher on April 25?",
    )

    assert result["available"] is False
    assert result["authority"] == "official_unavailable"
    assert "aggregate/sample divergence" in result["reason"]
    assert "METAR reference 18.3C" in result["reason"]


def _make_trade(*, recorded: str, recomputed: str | None, model_prob: float = 0.9) -> dict:
    trade: dict = {
        "recorded_outcome": recorded,
        "model_prob": model_prob,
    }
    if recomputed is not None:
        trade["recomputed_outcome"] = recomputed
        trade["recomputed_observation"] = {"available": True}
    else:
        trade["recomputed_observation"] = {"available": False, "reason": "no ICAO station lock"}
    return trade


def test_confusion_matrix_all_losses() -> None:
    trades = [
        _make_trade(recorded="loss", recomputed="loss"),
        _make_trade(recorded="loss", recomputed="loss"),
        _make_trade(recorded="loss", recomputed="win"),  # mismatch
        _make_trade(recorded="loss", recomputed=None),   # no obs
    ]
    cm = _build_confusion_matrix(trades)
    assert cm["recorded_win_recomputed_win"] == 0
    assert cm["recorded_win_recomputed_loss"] == 0
    assert cm["recorded_loss_recomputed_win"] == 1
    assert cm["recorded_loss_recomputed_loss"] == 2
    assert cm["no_recomputed"] == 1


def test_confusion_matrix_mixed() -> None:
    trades = [
        _make_trade(recorded="win", recomputed="win"),
        _make_trade(recorded="win", recomputed="loss"),
        _make_trade(recorded="loss", recomputed="win"),
        _make_trade(recorded="loss", recomputed="loss"),
    ]
    cm = _build_confusion_matrix(trades)
    assert cm["recorded_win_recomputed_win"] == 1
    assert cm["recorded_win_recomputed_loss"] == 1
    assert cm["recorded_loss_recomputed_win"] == 1
    assert cm["recorded_loss_recomputed_loss"] == 1
    assert cm["no_recomputed"] == 0


def test_brier_score_from_recomputed_all_wrong() -> None:
    trades = [
        _make_trade(recorded="loss", recomputed="loss", model_prob=0.9),
        _make_trade(recorded="loss", recomputed="loss", model_prob=0.8),
        _make_trade(recorded="loss", recomputed=None, model_prob=0.95),
    ]
    # Only 2 trades have recomputed observations; both are losses (outcome=0)
    # Brier = ((0.9-0)^2 + (0.8-0)^2) / 2 = (0.81 + 0.64) / 2 = 0.725
    score = _brier_score_from_recomputed(trades)
    assert score is not None
    assert abs(score - 0.725) < 1e-6


def test_brier_score_from_recomputed_empty() -> None:
    trades = [_make_trade(recorded="loss", recomputed=None)]
    score = _brier_score_from_recomputed(trades)
    assert score is None


def test_summarize_recomputed_trade_rows_groups_by_outcome_dimensions() -> None:
    trades = [
        {
            **_make_trade(recorded="loss", recomputed="loss", model_prob=0.95),
            "city": "Tokyo",
            "station": "RJTT",
            "side": "Yes",
            "comparator": "exact",
            "target_date": "2026-04-26",
            "pnl_usd": -1.0,
        },
        {
            **_make_trade(recorded="loss", recomputed="win", model_prob=0.80),
            "city": "Tokyo",
            "station": "RJTT",
            "side": "No",
            "comparator": "exact",
            "target_date": "2026-04-26",
            "pnl_usd": -1.0,
        },
        {
            **_make_trade(recorded="loss", recomputed=None, model_prob=0.70),
            "city": "Paris",
            "station": "LFPB",
            "side": "Yes",
            "comparator": "or_below",
            "target_date": "2026-04-27",
            "pnl_usd": -1.0,
        },
    ]

    summary = _summarize_recomputed_trade_rows(trades)

    assert summary["sample_size"] == 2
    assert summary["recomputed_wins"] == 1
    assert summary["recomputed_win_rate"] == 0.5
    assert summary["groups"]["by_comparator"]["exact"]["count"] == 2
    assert summary["groups"]["by_comparator"]["exact"]["recomputed_wins"] == 1
    assert summary["groups"]["by_side"]["Yes"]["recomputed_wins"] == 0
    assert summary["groups"]["by_side"]["No"]["recomputed_wins"] == 1
    assert "or_below" not in summary["groups"]["by_comparator"]


def test_summarize_forecast_error_diagnostics_groups_errors_by_outcome_dimensions() -> None:
    trades = [
        {
            **_make_trade(recorded="loss", recomputed="loss", model_prob=0.95),
            "forecast_value_c": 20.0,
            "horizon_hours": 0.4,
            "sigma_c": 0.3,
            "recomputed_observation": {"available": True, "observed_value_c": 17.0},
            "city": "Tokyo",
            "side": "Yes",
            "comparator": "exact",
            "target_date": "2026-04-26",
        },
        {
            **_make_trade(recorded="loss", recomputed="win", model_prob=0.80),
            "forecast_value_c": 10.0,
            "horizon_hours": 30.0,
            "sigma_c": 2.0,
            "recomputed_observation": {"available": True, "observed_value_c": 11.0},
            "city": "Seoul",
            "side": "No",
            "comparator": "or_below",
            "target_date": "2026-04-25",
        },
        {
            **_make_trade(recorded="loss", recomputed="loss", model_prob=0.70),
            "forecast_value_c": None,
            "recomputed_observation": {"available": True, "observed_value_c": 30.0},
            "city": "Jakarta",
            "side": "Yes",
            "comparator": "exact",
            "target_date": "2026-04-26",
        },
    ]

    summary = _summarize_forecast_error_diagnostics(trades)

    assert summary["sample_size"] == 2
    assert summary["avg_error_c"] == -1.0
    assert summary["avg_abs_error_c"] == 2.0
    assert summary["forecast_too_high_count"] == 1
    assert summary["forecast_too_low_count"] == 1
    assert summary["groups"]["by_comparator"]["exact"]["avg_error_c"] == -3.0
    assert summary["groups"]["by_side"]["No"]["avg_error_c"] == 1.0
    assert summary["groups"]["by_horizon_bucket"]["0-6h"]["count"] == 1
    assert summary["groups"]["by_horizon_bucket"]["24-48h"]["count"] == 1
    assert summary["groups"]["by_sigma_bucket"]["<=0.5C"]["count"] == 1


def test_format_closed_trades_audit_summary_includes_recomputed_breakdowns() -> None:
    report = {
        "generated_at": "2026-05-08T00:00:00+00:00",
        "read_only": True,
        "summary": {
            "sample_size": 2,
            "avg_model_prob": 0.9,
            "recorded_win_rate": 0.0,
            "pnl_usd": -2.0,
            "recomputed_observations_available": 2,
            "recorded_vs_recomputed_mismatches": 0,
            "recomputed_confusion_matrix": {
                "recorded_win_recomputed_win": 0,
                "recorded_win_recomputed_loss": 0,
                "recorded_loss_recomputed_win": 0,
                "recorded_loss_recomputed_loss": 2,
                "no_recomputed": 0,
            },
            "brier_score_recomputed": 0.81,
            "recomputed_outcome_summary": {
                "groups": {
                    "by_comparator": {
                        "exact": {"count": 2, "avg_model_prob": 0.9, "recomputed_win_rate": 0.0, "pnl_usd": -2.0}
                    },
                    "by_side": {
                        "Yes": {"count": 2, "avg_model_prob": 0.9, "recomputed_win_rate": 0.0, "pnl_usd": -2.0}
                    },
                }
            },
            "forecast_error_summary": {
                "sample_size": 2,
                "avg_error_c": -1.0,
                "avg_abs_error_c": 2.0,
                "forecast_too_high_count": 1,
                "forecast_too_low_count": 1,
                "groups": {
                    "by_comparator": {
                        "exact": {"count": 2, "avg_error_c": -1.0, "avg_abs_error_c": 2.0, "forecast_too_high_count": 1, "forecast_too_low_count": 1}
                    },
                    "by_side": {
                        "Yes": {"count": 2, "avg_error_c": -1.0, "avg_abs_error_c": 2.0, "forecast_too_high_count": 1, "forecast_too_low_count": 1}
                    },
                    "by_horizon_bucket": {
                        "0-6h": {"count": 2, "avg_error_c": -1.0, "avg_abs_error_c": 2.0, "forecast_too_high_count": 1, "forecast_too_low_count": 1}
                    },
                    "by_sigma_bucket": {
                        "<=0.5C": {"count": 2, "avg_error_c": -1.0, "avg_abs_error_c": 2.0, "forecast_too_high_count": 1, "forecast_too_low_count": 1}
                    },
                },
            },
            "groups": {
                "by_comparator": {},
                "by_side": {},
            },
        },
    }

    text = closed_trades_audit.format_closed_trades_audit_summary(report)

    assert "By recomputed comparator" in text
    assert "exact: n=2 avg_p=0.9 recomputed_win_rate=0.0 pnl=-2.0" in text
    assert "Forecast error vs observed: n=2 avg_error_c=-1.0 avg_abs_error_c=2.0 too_high=1 too_low=1" in text
    assert "By forecast-error horizon:" in text
    assert "0-6h: n=2 avg_error_c=-1.0 avg_abs_error_c=2.0 too_high=1 too_low=1" in text


def test_brier_score_from_recomputed_perfect() -> None:
    trades = [
        _make_trade(recorded="win", recomputed="win", model_prob=1.0),
        _make_trade(recorded="loss", recomputed="loss", model_prob=0.0),
    ]
    # outcome for win=1, loss=0; Brier = ((1-1)^2 + (0-0)^2) / 2 = 0
    score = _brier_score_from_recomputed(trades)
    assert score == 0.0
