from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .db import connect


@dataclass
class CalibrationBucket:
    bucket_min: float
    bucket_max: float
    predicted_count: int = 0
    yes_count: int = 0
    no_count: int = 0
    probability_sum: float = 0.0
    avg_predicted: float | None = None
    actual_frequency: float | None = None
    error: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "bucket_range": f"{self.bucket_min:.0%}-{self.bucket_max:.0%}",
            "predicted_count": self.predicted_count,
            "yes_count": self.yes_count,
            "no_count": self.no_count,
            "avg_predicted": round(self.avg_predicted, 4) if self.avg_predicted is not None else None,
            "actual_frequency": round(self.actual_frequency, 4) if self.actual_frequency is not None else None,
            "error": round(self.error, 4) if self.error is not None else None,
        }


@dataclass
class CalibrationReport:
    total_trades: int
    brier_score: float
    buckets: list[CalibrationBucket] = field(default_factory=list)
    generated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "total_trades": self.total_trades,
            "brier_score": round(self.brier_score, 6),
            "buckets": [b.as_dict() for b in self.buckets],
        }


@dataclass(frozen=True)
class CalibrationGateResult:
    allowed: bool
    reasons: list[str]
    sample_size: int
    brier_score: float
    max_abs_bucket_error: float | None
    min_trades: int
    max_brier: float
    max_allowed_abs_bucket_error: float
    min_trades_per_bucket: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reasons": self.reasons,
            "sample_size": self.sample_size,
            "brier_score": round(self.brier_score, 6),
            "max_abs_bucket_error": round(self.max_abs_bucket_error, 6) if self.max_abs_bucket_error is not None else None,
            "thresholds": {
                "min_trades": self.min_trades,
                "max_brier": self.max_brier,
                "max_abs_bucket_error": self.max_allowed_abs_bucket_error,
                "min_trades_per_bucket": self.min_trades_per_bucket,
            },
        }


def _load_resolved_trades(conn: sqlite3.Connection) -> list[sqlite3.Row]:

    """Return resolved paper trades with an inferable win/loss outcome."""
    query = "SELECT * FROM paper_trades WHERE status IN ('won', 'lost', 'closed') ORDER BY opened_at DESC"
    rows = list(conn.execute(query))
    # For 'closed', we need pnl to know won/lost
    result: list[sqlite3.Row] = []
    for row in rows:
        if row["status"] in ("won", "lost"):
            result.append(row)
        elif row["status"] == "closed" and row["pnl_usd"] is not None:
            result.append(row)
    return result


def _compute_outcome(row: sqlite3.Row) -> bool | None:
    """Return True if trade paid out (YES won), False if lost."""
    status = str(row["status"]).lower()
    if status == "won":
        return True
    if status == "lost":
        return False
    # For closed trades, infer from PnL
    pnl = row["pnl_usd"]
    if pnl is not None:
        return float(pnl) > 0
    # Fallback: parse candidate_json for settlement result
    candidate_json = dict(row).get("candidate_json") or "{}"
    try:
        data = json.loads(candidate_json)
        notes = str(data.get("notes") or "")
        if "won" in notes.lower() or "profit" in notes.lower():
            return True
        if "lost" in notes.lower():
            return False
    except Exception:
        pass
    return None


def _brier_score(trades_data: list[tuple[float, bool]]) -> float:
    """Compute Brier score: mean squared error of probability forecasts."""
    if not trades_data:
        return 0.0
    total = 0.0
    for prob, outcome in trades_data:
        outcome_f = 1.0 if outcome else 0.0
        total += (prob - outcome_f) ** 2
    return total / len(trades_data)


def evaluate_calibration_gate(
    report: CalibrationReport,
    *,
    min_trades: int = 30,
    max_brier: float = 0.30,
    max_abs_bucket_error: float = 0.25,
    min_trades_per_bucket: int = 5,
) -> CalibrationGateResult:
    """Decide whether calibration is good enough to allow pre-trade PASS decisions.

    The gate is deliberately conservative: it blocks small samples, bad Brier
    score, and reliability buckets whose empirical outcome frequency is too far
    from the model's actual average predicted probability.
    """
    reasons: list[str] = []
    if report.total_trades < min_trades:
        reasons.append(f"sample too small: {report.total_trades} resolved trades < {min_trades}")
    if report.brier_score > max_brier:
        reasons.append(f"Brier score too high: {report.brier_score:.4f} > {max_brier:.4f}")

    eligible_errors = [
        abs(bucket.error)
        for bucket in report.buckets
        if bucket.error is not None and bucket.predicted_count >= min_trades_per_bucket
    ]
    observed_max_error = max(eligible_errors) if eligible_errors else None
    if observed_max_error is not None and observed_max_error > max_abs_bucket_error:
        reasons.append(
            f"bucket calibration error too high: {observed_max_error:.4f} > {max_abs_bucket_error:.4f}"
        )

    return CalibrationGateResult(
        allowed=not reasons,
        reasons=reasons,
        sample_size=report.total_trades,
        brier_score=report.brier_score,
        max_abs_bucket_error=observed_max_error,
        min_trades=min_trades,
        max_brier=max_brier,
        max_allowed_abs_bucket_error=max_abs_bucket_error,
        min_trades_per_bucket=min_trades_per_bucket,
    )


def build_calibration_report(
    conn: sqlite3.Connection,
    bucket_count: int = 10,
) -> CalibrationReport:
    """Build calibration report from resolved paper trades."""
    rows = _load_resolved_trades(conn)
    trades_data: list[tuple[float, bool]] = []

    for row in rows:
        model_prob = row["model_prob"]
        if model_prob is None:
            continue
        outcome = _compute_outcome(row)
        if outcome is None:
            continue
        trades_data.append((float(model_prob), outcome))

    total_trades = len(trades_data)
    brier = _brier_score(trades_data)

    # Create buckets (0-10%, 10-20%, ..., 90-100%). A probability that
    # lands exactly on a boundary goes into the upper bucket, except 100%.
    buckets: list[CalibrationBucket] = []
    for i in range(bucket_count):
        bucket_min = i / bucket_count
        bucket_max = (i + 1) / bucket_count
        buckets.append(CalibrationBucket(bucket_min=bucket_min, bucket_max=bucket_max))

    for prob, outcome in trades_data:
        bucket_index = min(int(prob * bucket_count), bucket_count - 1)
        bucket = buckets[bucket_index]
        bucket.predicted_count += 1
        bucket.probability_sum += prob
        if outcome:
            bucket.yes_count += 1
        else:
            bucket.no_count += 1

    for bucket in buckets:
        if bucket.predicted_count > 0:
            bucket.avg_predicted = bucket.probability_sum / bucket.predicted_count
            bucket.actual_frequency = bucket.yes_count / bucket.predicted_count
            bucket.error = bucket.avg_predicted - bucket.actual_frequency

    report = CalibrationReport(
        total_trades=total_trades,
        brier_score=brier,
        buckets=buckets,
        generated_at=datetime.utcnow().isoformat() + "Z",
    )
    return report


def write_calibration_report(
    report: CalibrationReport,
    output_dir: Path,
    gate: CalibrationGateResult | None = None,
) -> Path:
    """Write calibration report as text and return path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y-%m-%d")
    report_path = output_dir / f"calibration_{timestamp}.txt"

    lines: list[str] = [
        "=" * 60,
        "WEATHER EDGE — CALIBRATION REPORT",
        f"Generated: {report.generated_at}",
        "=" * 60,
        "",
        f"Total resolved trades: {report.total_trades}",
        f"Brier score: {report.brier_score:.6f}",
        "",
        "Calibration by probability bucket:",
        "-" * 60,
    ]

    for bucket in report.buckets:
        if bucket.predicted_count == 0:
            lines.append(
                f"  {bucket.bucket_min:.0%}-{bucket.bucket_max:.0%}: "
                f"N=0 (no data)"
            )
        else:
            lines.append(
                f"  {bucket.bucket_min:.0%}-{bucket.bucket_max:.0%}: "
                f"N={bucket.predicted_count}, "
                f"predicted ~{bucket.avg_predicted:.0%}, "
                f"actual {bucket.actual_frequency:.1%}, "
                f"error={bucket.error:+.1%}"
            )

    if gate is not None:
        lines.extend([
            "",
            "Calibration gate:",
            "-" * 60,
            f"  allowed: {gate.allowed}",
            f"  sample_size: {gate.sample_size} / min {gate.min_trades}",
            f"  brier_score: {gate.brier_score:.6f} / max {gate.max_brier:.6f}",
            "  max_abs_bucket_error: "
            + (
                f"{gate.max_abs_bucket_error:.4f} / max {gate.max_allowed_abs_bucket_error:.4f}"
                if gate.max_abs_bucket_error is not None
                else "n/a"
            ),
        ])
        if gate.reasons:
            lines.append("  blockers:")
            lines.extend(f"    - {reason}" for reason in gate.reasons)
        else:
            lines.append("  blockers: none")

    lines.extend(["", "=" * 60])

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def run_calibration(db_path: Path, output_dir: Path | None = None) -> tuple[CalibrationReport, CalibrationGateResult, Path]:
    """Run full calibration analysis and write report."""
    if output_dir is None:
        output_dir = db_path.parents[1] / "reports"

    with connect(db_path) as conn:
        report = build_calibration_report(conn)

    gate = evaluate_calibration_gate(report)
    report_path = write_calibration_report(report, output_dir, gate=gate)
    return report, gate, report_path
