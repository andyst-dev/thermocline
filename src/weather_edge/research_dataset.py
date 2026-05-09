from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class ResearchDatasetRow:
    """One reproducible training/backtest row for one market bucket."""

    market_id: str
    slug: str
    question: str
    event_slug: str | None
    event_title: str | None
    city: str
    target_date: str
    scan_created_at: str
    end_date: str | None
    active: bool
    closed: bool
    liquidity: float
    volume: float
    bucket_label: str
    lower: float | None
    upper: float | None
    token_id: str | None
    market_prob: float | None
    model_prob: float | None
    model_prob_gaussian: float | None
    model_prob_ensemble: float | None
    edge: float | None
    ev: float | None
    best_bid: float | None
    best_ask: float | None
    executable_ev: float | None
    forecast_max_c: float
    sigma_c: float
    horizon_hours: float
    confidence: str
    observed_max_c: float | None
    residual_c: float | None
    resolved_outcome: int | None
    actual_source: str | None


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_json_loads(value: str | bytes | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def outcome_for_temperature(observed_c: float | None, *, lower: float | None, upper: float | None) -> int | None:
    """Return 1 if observed_c settles inside a bucket, 0 if outside, None if unresolved.

    Buckets use half-open intervals: lower is inclusive, upper is exclusive.
    This prevents boundary temperatures from settling as a win in two adjacent
    buckets in the exported backtest labels.
    """
    if observed_c is None:
        return None
    observed = float(observed_c)
    if lower is not None and observed < float(lower):
        return 0
    if upper is not None and observed >= float(upper):
        return 0
    return 1


def _latest_actuals(conn: sqlite3.Connection) -> dict[tuple[str, str], sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT * FROM backtest_records
        ORDER BY target_date ASC, city ASC, fetched_at DESC, id DESC
        """
    ).fetchall()
    actuals: dict[tuple[str, str], sqlite3.Row] = {}
    for row in rows:
        key = (str(row["city"]), str(row["target_date"]))
        if key not in actuals:
            actuals[key] = row
    return actuals


def _iter_dataset_rows(conn: sqlite3.Connection) -> Iterable[ResearchDatasetRow]:
    actuals = _latest_actuals(conn)
    scan_rows = conn.execute(
        """
        SELECT
            s.*,
            m.end_date AS market_end_date,
            m.active AS market_active,
            m.closed AS market_closed,
            m.volume AS market_volume,
            m.raw_json AS market_raw_json
        FROM scans s
        LEFT JOIN markets m ON m.market_id = s.market_id
        ORDER BY s.target_date ASC, s.market_id ASC, s.created_at ASC, s.id ASC
        """
    ).fetchall()

    for scan in scan_rows:
        raw = _safe_json_loads(scan["market_raw_json"], {})
        if not isinstance(raw, dict):
            raw = {}
        buckets = _safe_json_loads(scan["buckets_json"], [])
        if not isinstance(buckets, list):
            continue
        actual = actuals.get((str(scan["city"]), str(scan["target_date"])))
        observed = _maybe_float(actual["observed_max_c"]) if actual is not None else None
        residual = _maybe_float(actual["residual_c"]) if actual is not None else None
        source = str(actual["model_source"]) if actual is not None else None

        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            lower = _maybe_float(bucket.get("lower"))
            upper = _maybe_float(bucket.get("upper"))
            yield ResearchDatasetRow(
                market_id=str(scan["market_id"]),
                slug=str(scan["slug"]),
                question=str(scan["question"]),
                event_slug=raw.get("event_slug") if isinstance(raw.get("event_slug"), str) else raw.get("eventSlug") if isinstance(raw.get("eventSlug"), str) else None,
                event_title=raw.get("event_title") if isinstance(raw.get("event_title"), str) else raw.get("eventTitle") if isinstance(raw.get("eventTitle"), str) else None,
                city=str(scan["city"]),
                target_date=str(scan["target_date"]),
                scan_created_at=str(scan["created_at"]),
                end_date=str(scan["market_end_date"]) if scan["market_end_date"] is not None else None,
                active=bool(scan["market_active"]) if scan["market_active"] is not None else False,
                closed=bool(scan["market_closed"]) if scan["market_closed"] is not None else False,
                liquidity=float(scan["liquidity"]),
                volume=float(scan["market_volume"]) if scan["market_volume"] is not None else 0.0,
                bucket_label=str(bucket.get("label") or ""),
                lower=lower,
                upper=upper,
                token_id=str(bucket["token_id"]) if bucket.get("token_id") is not None else None,
                market_prob=_maybe_float(bucket.get("market_prob")),
                model_prob=_maybe_float(bucket.get("model_prob")),
                model_prob_gaussian=_maybe_float(bucket.get("model_prob_gaussian")),
                model_prob_ensemble=_maybe_float(bucket.get("model_prob_ensemble")),
                edge=_maybe_float(bucket.get("edge")),
                ev=_maybe_float(bucket.get("ev")),
                best_bid=_maybe_float(bucket.get("best_bid")),
                best_ask=_maybe_float(bucket.get("best_ask")),
                executable_ev=_maybe_float(bucket.get("executable_ev")),
                forecast_max_c=float(scan["forecast_max_c"]),
                sigma_c=float(scan["sigma_c"]),
                horizon_hours=float(scan["horizon_hours"]),
                confidence=str(scan["confidence"]),
                observed_max_c=observed,
                residual_c=residual,
                resolved_outcome=outcome_for_temperature(observed, lower=lower, upper=upper),
                actual_source=source,
            )


def export_research_dataset(db_path: Path, output_path: Path) -> dict[str, int | str]:
    """Export scans + bucket prices + latest station actuals to deterministic JSONL."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = list(_iter_dataset_rows(conn))
    finally:
        conn.close()

    with output_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(asdict(row), sort_keys=True, separators=(",", ":")) + "\n")

    return {
        "path": str(output_path),
        "rows": len(rows),
        "markets": len({row.market_id for row in rows}),
        "resolved_rows": sum(1 for row in rows if row.resolved_outcome is not None),
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows
