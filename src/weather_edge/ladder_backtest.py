"""Read-only ladder backtest reporting.

The single-bucket-vs-ladder comparison the readiness guide expects requires
historical per-leg price snapshots over time. Until we capture those, this
module produces a deterministic skeleton report that:

- compares hit rates of synthetic strategies on the data we *do* have
  (`backtest_records` forecast/observed residuals), so adjacency width can be
  evaluated qualitatively;
- leaves cost/PnL/ROI/drawdown fields explicitly null with a `no_go_reasons`
  list naming the missing inputs, instead of fabricating values from prices we
  never recorded;
- never opens, sizes, or recommends a paper trade.

The CLI entry point is ``weather-edge ladder-backtest-report``.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Any

from .backtest import horizon_bucket
from .db import connect


REPORT_VERSION = "v1"
DEFAULT_REPORT_FILENAME = "ladder_backtest_report.json"
MIN_TRADES_FOR_VALID_BACKTEST = 30


@dataclass(frozen=True)
class LadderBacktestStrategy:
    """Synthetic ladder strategy: hits if |observed_int - forecast_int| <= half_width."""

    name: str
    half_width_c: int
    description: str


STRATEGIES: tuple[LadderBacktestStrategy, ...] = (
    LadderBacktestStrategy(
        name="single_best_bucket",
        half_width_c=0,
        description="Single 1°C bucket centered on round(forecast). Hits iff round(observed)==round(forecast).",
    ),
    LadderBacktestStrategy(
        name="ladder_pm_1c",
        half_width_c=1,
        description="3-leg adjacent ladder at round(forecast) ± 1°C. Hits iff |round(observed)-round(forecast)|<=1.",
    ),
    LadderBacktestStrategy(
        name="ladder_pm_2c",
        half_width_c=2,
        description="5-leg adjacent ladder at round(forecast) ± 2°C. Hits iff |round(observed)-round(forecast)|<=2.",
    ),
)


def _empty_metrics() -> dict[str, Any]:
    """Fields the readiness guide expects, all unavailable until fills are stored."""
    return {
        "trade_count": 0,
        "hit_rate": None,
        "avg_cost": None,
        "avg_payout": None,
        "pnl": None,
        "roi": None,
        "max_drawdown": None,
        "brier": None,
        "calibration": None,
    }


def _fetch_backtest_records(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = list(conn.execute(
        "SELECT city, target_date, horizon_hours, forecast_max_c, observed_max_c, residual_c, metric "
        "FROM backtest_records ORDER BY target_date DESC, horizon_hours ASC"
    ))
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            out.append({
                "city": str(row["city"]),
                "target_date": str(row["target_date"]),
                "horizon_hours": float(row["horizon_hours"]),
                "forecast_max_c": float(row["forecast_max_c"]),
                "observed_max_c": float(row["observed_max_c"]),
                "residual_c": float(row["residual_c"]),
                "metric": str(row["metric"]),
            })
        except (TypeError, ValueError):
            continue
    return out


def _strategy_hit(record: dict[str, Any], strategy: LadderBacktestStrategy) -> bool:
    forecast_int = round(record["forecast_max_c"])
    observed_int = round(record["observed_max_c"])
    return abs(observed_int - forecast_int) <= strategy.half_width_c


def _stats_from_hits(hits: list[bool]) -> dict[str, Any]:
    total = len(hits)
    metrics = _empty_metrics()
    metrics["trade_count"] = total
    if total > 0:
        metrics["hit_rate"] = round(sum(1 for hit in hits if hit) / total, 6)
    return metrics


def _by_group_stats(
    records: list[dict[str, Any]],
    strategy: LadderBacktestStrategy,
    group_key: str,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[bool]] = defaultdict(list)
    for record in records:
        if group_key == "horizon":
            key = horizon_bucket(record["horizon_hours"])
        else:
            key = str(record.get(group_key) or "unknown")
        grouped[key].append(_strategy_hit(record, strategy))
    return {key: _stats_from_hits(hits) for key, hits in sorted(grouped.items())}


def _strategy_block(records: list[dict[str, Any]], strategy: LadderBacktestStrategy) -> dict[str, Any]:
    hits = [_strategy_hit(record, strategy) for record in records]
    block = _stats_from_hits(hits)
    block["name"] = strategy.name
    block["half_width_c"] = strategy.half_width_c
    block["description"] = strategy.description
    block["by_horizon"] = _by_group_stats(records, strategy, "horizon")
    block["by_city"] = _by_group_stats(records, strategy, "city")
    return block


def _no_go_reasons(records: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    if len(records) < MIN_TRADES_FOR_VALID_BACKTEST:
        reasons.append(
            f"insufficient backtest_records: {len(records)} < {MIN_TRADES_FOR_VALID_BACKTEST}"
        )
    reasons.extend([
        "no historical per-leg ask/bid snapshots stored: avg_cost/pnl/roi/max_drawdown unavailable",
        "current per-leg ladder fill simulation exists for observation; historical fill-level replay still unavailable until ladder snapshots accumulate and resolve",
        "no calibration gate aggregation linked to ladder strategy: brier/calibration not computed",
        "no event-key + ladder_id replay across past scans: ladder PnL cannot be aggregated yet",
    ])
    return reasons


def _safety_block() -> dict[str, Any]:
    return {
        "report_only": True,
        "no_orders_placed": True,
        "no_paper_open_integration": True,
        "no_recommended_size_usd": True,
    }


def build_ladder_backtest_report(
    db_path: Path,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic ladder backtest report payload.

    The function never opens, sizes, or recommends a trade. It only reads
    historical residual records and reports synthetic strategy hit rates with
    explicit ``no_go_reasons`` whenever inputs are insufficient for a real
    fill-level backtest.
    """
    with connect(db_path) as conn:
        records = _fetch_backtest_records(conn)

    strategies = [_strategy_block(records, strategy) for strategy in STRATEGIES]
    no_go_reasons = _no_go_reasons(records)
    hit_rate_sample_sufficient = len(records) >= MIN_TRADES_FOR_VALID_BACKTEST
    sufficient_for_paper_decision = False

    return {
        "report_version": REPORT_VERSION,
        "generated_at": generated_at or datetime.now(dt_timezone.utc).isoformat(),
        "safety": _safety_block(),
        "input_summary": {
            "backtest_records": len(records),
            "min_records_for_valid_backtest": MIN_TRADES_FOR_VALID_BACKTEST,
            "distinct_cities": len({record["city"] for record in records}),
            "distinct_target_dates": len({record["target_date"] for record in records}),
        },
        "strategies": strategies,
        "no_go_reasons": no_go_reasons,
        "hit_rate_sample_sufficient": hit_rate_sample_sufficient,
        "sufficient_data": sufficient_for_paper_decision,
        "decision": (
            "qualitative_hit_rate_only_fill_level_backtest_unavailable"
            if hit_rate_sample_sufficient
            else "insufficient_data_for_paper_ladder_decision"
        ),
    }


def write_ladder_backtest_report(
    db_path: Path,
    output_path: Path,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Write the ladder backtest report to disk and return the payload."""
    payload = build_ladder_backtest_report(db_path, generated_at=generated_at)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def format_ladder_backtest_summary(payload: dict[str, Any]) -> str:
    """One-line-per-strategy human summary, safe to print after writing JSON."""
    lines = [
        "Ladder backtest report (read-only, no execution)",
        f"Generated: {payload.get('generated_at')}",
        f"Records: {payload['input_summary']['backtest_records']} "
        f"(cities={payload['input_summary']['distinct_cities']}, "
        f"dates={payload['input_summary']['distinct_target_dates']})",
        f"Decision: {payload['decision']}",
        "",
        "Strategies:",
    ]
    for strat in payload["strategies"]:
        hit_rate = strat.get("hit_rate")
        hit_rate_str = f"{hit_rate:.4f}" if isinstance(hit_rate, (int, float)) else "n/a"
        lines.append(
            f"  - {strat['name']}: n={strat['trade_count']} "
            f"hit_rate={hit_rate_str} (cost/pnl unavailable)"
        )
    if payload["no_go_reasons"]:
        lines.append("")
        lines.append("No-go / data-gap reasons:")
        lines.extend(f"  - {reason}" for reason in payload["no_go_reasons"])
    return "\n".join(lines)
