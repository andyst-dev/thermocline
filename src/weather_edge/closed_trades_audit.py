from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .clients.aviationweather import observed_extreme_c
from .clients.weathercom import official_extreme_c
from .db import connect
from .parsing import parse_temperature_contract
from .settlement import _evaluate_contract
from .timezones import timezone_hint_for_icao


def classify_temperature_comparator(question: str) -> str:
    """Classify a Polymarket temperature question by payoff shape."""
    text = question.lower()
    if " or higher" in text:
        return "or_higher"
    if " or below" in text:
        return "or_below"
    if "between" in text:
        return "between"
    if re.search(r"\d+(?:\.\d+)?\s*°\s*[cf]\b", text):
        return "exact"
    return "unknown"


def _icao_from_candidate(candidate: dict[str, Any]) -> str | None:
    loc = candidate.get("resolution_location")
    if isinstance(loc, str) and re.fullmatch(r"[A-Z]{4}", loc):
        return loc
    return None


def _new_group() -> dict[str, Any]:
    return {
        "count": 0,
        "model_prob_sum": 0.0,
        "recorded_wins": 0,
        "pnl_usd": 0.0,
    }


def _add_group(groups: dict[str, dict[str, Any]], key: str, *, model_prob: float | None, pnl_usd: float | None) -> None:
    group = groups[key]
    group["count"] += 1
    group["model_prob_sum"] += float(model_prob or 0.0)
    group["recorded_wins"] += 1 if float(pnl_usd or 0.0) > 0 else 0
    group["pnl_usd"] += float(pnl_usd or 0.0)


def _finalize_groups(groups: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    finalized: dict[str, dict[str, Any]] = {}
    for key, group in sorted(groups.items(), key=lambda item: (-item[1]["count"], item[0])):
        count = int(group["count"])
        avg_model_prob = group["model_prob_sum"] / count if count else None
        recorded_win_rate = group["recorded_wins"] / count if count else None
        finalized[key] = {
            "count": count,
            "avg_model_prob": round(avg_model_prob, 6) if avg_model_prob is not None else None,
            "recorded_wins": int(group["recorded_wins"]),
            "recorded_win_rate": round(recorded_win_rate, 6) if recorded_win_rate is not None else None,
            "pnl_usd": round(float(group["pnl_usd"]), 6),
        }
    return finalized


def _new_recomputed_group() -> dict[str, Any]:
    return {
        "count": 0,
        "model_prob_sum": 0.0,
        "recomputed_wins": 0,
        "pnl_usd": 0.0,
    }


def _add_recomputed_group(
    groups: dict[str, dict[str, Any]],
    key: str,
    *,
    model_prob: float | None,
    pnl_usd: float | None,
    recomputed_outcome: str,
) -> None:
    group = groups[key]
    group["count"] += 1
    group["model_prob_sum"] += float(model_prob or 0.0)
    group["recomputed_wins"] += 1 if recomputed_outcome == "win" else 0
    group["pnl_usd"] += float(pnl_usd or 0.0)


def _finalize_recomputed_groups(groups: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    finalized: dict[str, dict[str, Any]] = {}
    for key, group in sorted(groups.items(), key=lambda item: (-item[1]["count"], item[0])):
        count = int(group["count"])
        avg_model_prob = group["model_prob_sum"] / count if count else None
        recomputed_win_rate = group["recomputed_wins"] / count if count else None
        finalized[key] = {
            "count": count,
            "avg_model_prob": round(avg_model_prob, 6) if avg_model_prob is not None else None,
            "recomputed_wins": int(group["recomputed_wins"]),
            "recomputed_win_rate": round(recomputed_win_rate, 6) if recomputed_win_rate is not None else None,
            "pnl_usd": round(float(group["pnl_usd"]), 6),
        }
    return finalized


def _summarize_recomputed_trade_rows(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize only trades whose outcomes were recomputed from observations."""
    groups = {
        "by_side": defaultdict(_new_recomputed_group),
        "by_comparator": defaultdict(_new_recomputed_group),
        "by_city": defaultdict(_new_recomputed_group),
        "by_station": defaultdict(_new_recomputed_group),
        "by_target_date": defaultdict(_new_recomputed_group),
    }
    recomputed = [trade for trade in trades if trade.get("recomputed_outcome") is not None]
    model_prob_sum = 0.0
    recomputed_wins = 0
    total_pnl = 0.0
    for trade in recomputed:
        outcome = str(trade.get("recomputed_outcome") or "loss")
        model_prob = trade.get("model_prob")
        pnl_usd = trade.get("pnl_usd")
        model_prob_sum += float(model_prob or 0.0)
        recomputed_wins += 1 if outcome == "win" else 0
        total_pnl += float(pnl_usd or 0.0)
        for group_name, key in (
            ("by_side", str(trade.get("side") or "unknown")),
            ("by_comparator", str(trade.get("comparator") or "unknown")),
            ("by_city", str(trade.get("city") or "unknown")),
            ("by_station", str(trade.get("station") or "unknown")),
            ("by_target_date", str(trade.get("target_date") or "unknown")),
        ):
            _add_recomputed_group(groups[group_name], key, model_prob=model_prob, pnl_usd=pnl_usd, recomputed_outcome=outcome)

    count = len(recomputed)
    return {
        "sample_size": count,
        "avg_model_prob": round(model_prob_sum / count, 6) if count else None,
        "recomputed_wins": recomputed_wins,
        "recomputed_win_rate": round(recomputed_wins / count, 6) if count else None,
        "pnl_usd": round(total_pnl, 6),
        "groups": {name: _finalize_recomputed_groups(group) for name, group in groups.items()},
    }


def _new_forecast_error_group() -> dict[str, Any]:
    return {
        "count": 0,
        "error_sum_c": 0.0,
        "abs_error_sum_c": 0.0,
        "forecast_too_high_count": 0,
        "forecast_too_low_count": 0,
    }


def _add_forecast_error_group(groups: dict[str, dict[str, Any]], key: str, *, error_c: float) -> None:
    group = groups[key]
    group["count"] += 1
    group["error_sum_c"] += error_c
    group["abs_error_sum_c"] += abs(error_c)
    if error_c < 0:
        group["forecast_too_high_count"] += 1
    elif error_c > 0:
        group["forecast_too_low_count"] += 1


def _finalize_forecast_error_groups(groups: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    finalized: dict[str, dict[str, Any]] = {}
    for key, group in sorted(groups.items(), key=lambda item: (-item[1]["count"], item[0])):
        count = int(group["count"])
        avg_error = group["error_sum_c"] / count if count else None
        avg_abs_error = group["abs_error_sum_c"] / count if count else None
        finalized[key] = {
            "count": count,
            "avg_error_c": round(avg_error, 6) if avg_error is not None else None,
            "avg_abs_error_c": round(avg_abs_error, 6) if avg_abs_error is not None else None,
            "forecast_too_high_count": int(group["forecast_too_high_count"]),
            "forecast_too_low_count": int(group["forecast_too_low_count"]),
        }
    return finalized


def _forecast_horizon_bucket(hours: Any) -> str:
    if hours is None:
        return "unknown"
    value = float(hours)
    if value < 6:
        return "0-6h"
    if value < 24:
        return "6-24h"
    if value < 48:
        return "24-48h"
    if value < 72:
        return "48-72h"
    return "72h+"


def _forecast_sigma_bucket(sigma_c: Any) -> str:
    if sigma_c is None:
        return "unknown"
    value = float(sigma_c)
    if value <= 0.5:
        return "<=0.5C"
    if value <= 1.0:
        return "0.5-1.0C"
    if value <= 2.0:
        return "1.0-2.0C"
    return ">2.0C"


def _summarize_forecast_error_diagnostics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize observed minus forecast errors for recomputed closed trades."""
    groups = {
        "by_side": defaultdict(_new_forecast_error_group),
        "by_comparator": defaultdict(_new_forecast_error_group),
        "by_city": defaultdict(_new_forecast_error_group),
        "by_target_date": defaultdict(_new_forecast_error_group),
        "by_horizon_bucket": defaultdict(_new_forecast_error_group),
        "by_sigma_bucket": defaultdict(_new_forecast_error_group),
    }
    errors: list[float] = []
    for trade in trades:
        observation = trade.get("recomputed_observation") or {}
        forecast_value = trade.get("forecast_value_c")
        observed_value = observation.get("observed_value_c")
        if forecast_value is None or observed_value is None:
            continue
        error_c = float(observed_value) - float(forecast_value)
        trade["forecast_error_c"] = round(error_c, 6)
        trade["forecast_abs_error_c"] = round(abs(error_c), 6)
        errors.append(error_c)
        for group_name, key in (
            ("by_side", str(trade.get("side") or "unknown")),
            ("by_comparator", str(trade.get("comparator") or "unknown")),
            ("by_city", str(trade.get("city") or "unknown")),
            ("by_target_date", str(trade.get("target_date") or "unknown")),
            ("by_horizon_bucket", _forecast_horizon_bucket(trade.get("horizon_hours"))),
            ("by_sigma_bucket", _forecast_sigma_bucket(trade.get("sigma_c"))),
        ):
            _add_forecast_error_group(groups[group_name], key, error_c=error_c)

    count = len(errors)
    return {
        "sample_size": count,
        "avg_error_c": round(sum(errors) / count, 6) if count else None,
        "avg_abs_error_c": round(sum(abs(error) for error in errors) / count, 6) if count else None,
        "forecast_too_high_count": sum(1 for error in errors if error < 0),
        "forecast_too_low_count": sum(1 for error in errors if error > 0),
        "groups": {name: _finalize_forecast_error_groups(group) for name, group in groups.items()},
    }


def summarize_closed_trade_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize already-loaded closed paper trades without doing network I/O."""
    groups = {
        "by_side": defaultdict(_new_group),
        "by_comparator": defaultdict(_new_group),
        "by_city": defaultdict(_new_group),
        "by_station": defaultdict(_new_group),
        "by_opened_date": defaultdict(_new_group),
        "by_notes": defaultdict(_new_group),
    }
    total_pnl = 0.0
    model_prob_sum = 0.0
    recorded_wins = 0

    for row in rows:
        candidate = row.get("candidate") or {}
        question = str(row.get("question") or "")
        model_prob = row.get("model_prob")
        pnl_usd = row.get("pnl_usd")
        comparator = str(row.get("comparator") or classify_temperature_comparator(question))
        city = str(candidate.get("city") or "unknown")
        station = str(candidate.get("resolution_location") or "unknown")
        opened_date = str(row.get("opened_at") or "")[:10] or "unknown"
        notes = str(row.get("notes") or "") or "none"
        side = str(row.get("side") or "unknown")

        total_pnl += float(pnl_usd or 0.0)
        model_prob_sum += float(model_prob or 0.0)
        recorded_wins += 1 if float(pnl_usd or 0.0) > 0 else 0
        for group_name, key in (
            ("by_side", side),
            ("by_comparator", comparator),
            ("by_city", city),
            ("by_station", station),
            ("by_opened_date", opened_date),
            ("by_notes", notes),
        ):
            _add_group(groups[group_name], key, model_prob=model_prob, pnl_usd=pnl_usd)

    count = len(rows)
    return {
        "sample_size": count,
        "avg_model_prob": round(model_prob_sum / count, 6) if count else None,
        "recorded_wins": recorded_wins,
        "recorded_win_rate": round(recorded_wins / count, 6) if count else None,
        "pnl_usd": round(total_pnl, 6),
        "groups": {name: _finalize_groups(group) for name, group in groups.items()},
    }


def _recomputed_observation(candidate: dict[str, Any], question: str) -> dict[str, Any]:
    contract = parse_temperature_contract(question)
    if not contract:
        return {"available": False, "reason": "unsupported question pattern"}
    icao = _icao_from_candidate(candidate)
    if not icao:
        return {"available": False, "reason": "no ICAO station lock"}
    timezone_name = str(candidate.get("timezone") or timezone_hint_for_icao(icao))
    resolution_source = str(candidate.get("resolution_source") or "")
    observed = None
    count = 0
    authority = "none"
    note = ""
    try:
        if "wunderground.com" in resolution_source.lower() or "weather.com" in resolution_source.lower():
            observed, count, note = official_extreme_c(resolution_source, contract.target_date, contract.metric)
            if observed is not None:
                authority = "official_weathercom_wunderground"
            else:
                metar_observed, metar_count = observed_extreme_c(icao, contract.target_date, timezone_name, contract.metric)
                hint = (
                    f"; METAR reference {round(float(metar_observed), 4)}C from {metar_count} obs at {icao}"
                    if metar_observed is not None
                    else ""
                )
                return {
                    "available": False,
                    "reason": f"official source unavailable: {note}{hint}",
                    "authority": "official_unavailable",
                    "observation_count": int(count or 0),
                }
        else:
            observed, count = observed_extreme_c(icao, contract.target_date, timezone_name, contract.metric)
            authority = "metar" if observed is not None else authority
    except Exception as exc:  # pragma: no cover - defensive around external fetchers
        return {"available": False, "reason": f"observation fetch failed: {exc}"}
    if observed is None:
        return {"available": False, "reason": note or f"no observation for {icao}", "authority": authority}
    yes_wins, irreversible = _evaluate_contract(contract, float(observed), True)
    return {
        "available": True,
        "authority": authority,
        "observed_value_c": round(float(observed), 4),
        "observation_count": int(count or 0),
        "yes_wins": bool(yes_wins),
        "irreversible": bool(irreversible),
    }


def _load_closed_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    query = """
    SELECT id, market_id, slug, question, side, entry_price, size_usd,
           model_prob, executable_ev, score, verdict, status, exit_price,
           pnl_usd, notes, candidate_json, opened_at, closed_at
    FROM paper_trades
    WHERE status = 'closed'
    ORDER BY opened_at, id
    """
    rows: list[dict[str, Any]] = []
    for row in conn.execute(query):
        item = dict(row)
        try:
            candidate = json.loads(item.get("candidate_json") or "{}")
        except json.JSONDecodeError:
            candidate = {}
        item["candidate"] = candidate
        item["comparator"] = classify_temperature_comparator(str(item.get("question") or ""))
        item["recorded_outcome"] = "win" if float(item.get("pnl_usd") or 0.0) > 0 else "loss"
        rows.append(item)
    return rows


def _build_confusion_matrix(trades: list[dict[str, Any]]) -> dict[str, int]:
    """Count recorded-vs-recomputed outcome cells for trades that have observations."""
    cm: dict[str, int] = {
        "recorded_win_recomputed_win": 0,
        "recorded_win_recomputed_loss": 0,
        "recorded_loss_recomputed_win": 0,
        "recorded_loss_recomputed_loss": 0,
        "no_recomputed": 0,
    }
    for trade in trades:
        recomputed = trade.get("recomputed_outcome")
        if recomputed is None:
            cm["no_recomputed"] += 1
            continue
        recorded = str(trade.get("recorded_outcome") or "loss")
        key = f"recorded_{recorded}_recomputed_{recomputed}"
        if key in cm:
            cm[key] += 1
        else:
            cm["no_recomputed"] += 1
    return cm


def _brier_score_from_recomputed(trades: list[dict[str, Any]]) -> float | None:
    """Brier score using recomputed outcomes as ground truth (where available)."""
    items = [
        (float(t["model_prob"]), 1.0 if t["recomputed_outcome"] == "win" else 0.0)
        for t in trades
        if t.get("recomputed_outcome") is not None and t.get("model_prob") is not None
    ]
    if not items:
        return None
    return sum((p - o) ** 2 for p, o in items) / len(items)


def build_closed_trades_audit(db_path: Path, *, include_observations: bool = False) -> dict[str, Any]:
    with connect(db_path) as conn:
        rows = _load_closed_rows(conn)

    trades: list[dict[str, Any]] = []
    mismatches = 0
    recomputed_available = 0
    for row in rows:
        candidate = row["candidate"]
        trade = {
            "id": row["id"],
            "market_id": row["market_id"],
            "question": row["question"],
            "side": row["side"],
            "comparator": row["comparator"],
            "city": candidate.get("city"),
            "station": candidate.get("resolution_location"),
            "target_date": candidate.get("target_date"),
            "forecast_value_c": candidate.get("forecast_value_c"),
            "horizon_hours": candidate.get("horizon_hours"),
            "sigma_c": candidate.get("sigma_c"),
            "opened_at": row["opened_at"],
            "closed_at": row["closed_at"],
            "entry_price": row["entry_price"],
            "size_usd": row["size_usd"],
            "model_prob": row["model_prob"],
            "pnl_usd": row["pnl_usd"],
            "recorded_outcome": row["recorded_outcome"],
            "notes": row["notes"],
        }
        if include_observations:
            recomputed = _recomputed_observation(candidate, str(row["question"] or ""))
            trade["recomputed_observation"] = recomputed
            if recomputed.get("available"):
                recomputed_available += 1
                yes_wins = bool(recomputed["yes_wins"])
                side = str(row.get("side") or "").lower()
                recomputed_trade_wins = yes_wins if side == "yes" else not yes_wins if side == "no" else None
                trade["recomputed_outcome"] = "win" if recomputed_trade_wins else "loss"
                if recomputed_trade_wins is not None and trade["recomputed_outcome"] != row["recorded_outcome"]:
                    mismatches += 1
                    trade["recorded_vs_recomputed"] = "mismatch"
                else:
                    trade["recorded_vs_recomputed"] = "match"
        trades.append(trade)

    summary_input = [{**row, "candidate": row["candidate"]} for row in rows]
    summary = summarize_closed_trade_rows(summary_input)
    summary["recomputed_observations_available"] = recomputed_available
    summary["recorded_vs_recomputed_mismatches"] = mismatches
    if include_observations:
        summary["recomputed_confusion_matrix"] = _build_confusion_matrix(trades)
        summary["recomputed_outcome_summary"] = _summarize_recomputed_trade_rows(trades)
        summary["forecast_error_summary"] = _summarize_forecast_error_diagnostics(trades)
        brier = _brier_score_from_recomputed(trades)
        summary["brier_score_recomputed"] = round(brier, 6) if brier is not None else None
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "read_only": True,
        "include_observations": include_observations,
        "summary": summary,
        "trades": trades,
    }


def write_closed_trades_audit_report(
    db_path: Path,
    output_path: Path,
    *,
    include_observations: bool = False,
) -> dict[str, Any]:
    report = build_closed_trades_audit(db_path, include_observations=include_observations)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def format_closed_trades_audit_summary(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "WEATHER EDGE — CLOSED TRADES AUDIT",
        f"Generated: {report['generated_at']}",
        f"Read-only: {report['read_only']}",
        f"Closed trades: {summary['sample_size']}",
        f"Avg model prob: {summary['avg_model_prob']}",
        f"Recorded win rate: {summary['recorded_win_rate']}",
        f"Recorded PnL: {summary['pnl_usd']}",
        f"Recomputed observations available: {summary['recomputed_observations_available']}",
        f"Recorded/recomputed mismatches: {summary['recorded_vs_recomputed_mismatches']}",
    ]
    if "recomputed_confusion_matrix" in summary:
        cm = summary["recomputed_confusion_matrix"]
        lines += [
            f"Confusion matrix (recorded vs recomputed):",
            f"  win /win : {cm['recorded_win_recomputed_win']}",
            f"  win /loss: {cm['recorded_win_recomputed_loss']}",
            f"  loss/win : {cm['recorded_loss_recomputed_win']}  <- settlement mismatches",
            f"  loss/loss: {cm['recorded_loss_recomputed_loss']}  <- model wrong",
            f"  no obs   : {cm['no_recomputed']}",
        ]
        brier = summary.get("brier_score_recomputed")
        if brier is not None:
            lines.append(f"Brier score (recomputed ground truth): {brier}")
    recomputed_summary = summary.get("recomputed_outcome_summary")
    if recomputed_summary:
        lines += [
            "",
            "By recomputed comparator:",
        ]
        for key, group in recomputed_summary["groups"]["by_comparator"].items():
            lines.append(
                f"  {key}: n={group['count']} avg_p={group['avg_model_prob']} "
                f"recomputed_win_rate={group['recomputed_win_rate']} pnl={group['pnl_usd']}"
            )
        lines.append("By recomputed side:")
        for key, group in recomputed_summary["groups"]["by_side"].items():
            lines.append(
                f"  {key}: n={group['count']} avg_p={group['avg_model_prob']} "
                f"recomputed_win_rate={group['recomputed_win_rate']} pnl={group['pnl_usd']}"
            )
    forecast_error_summary = summary.get("forecast_error_summary")
    if forecast_error_summary:
        lines += [
            "",
            f"Forecast error vs observed: n={forecast_error_summary['sample_size']} "
            f"avg_error_c={forecast_error_summary['avg_error_c']} "
            f"avg_abs_error_c={forecast_error_summary['avg_abs_error_c']} "
            f"too_high={forecast_error_summary['forecast_too_high_count']} "
            f"too_low={forecast_error_summary['forecast_too_low_count']}",
            "By forecast-error comparator:",
        ]
        for key, group in forecast_error_summary["groups"]["by_comparator"].items():
            lines.append(
                f"  {key}: n={group['count']} avg_error_c={group['avg_error_c']} "
                f"avg_abs_error_c={group['avg_abs_error_c']} "
                f"too_high={group['forecast_too_high_count']} too_low={group['forecast_too_low_count']}"
            )
        lines.append("By forecast-error side:")
        for key, group in forecast_error_summary["groups"]["by_side"].items():
            lines.append(
                f"  {key}: n={group['count']} avg_error_c={group['avg_error_c']} "
                f"avg_abs_error_c={group['avg_abs_error_c']} "
                f"too_high={group['forecast_too_high_count']} too_low={group['forecast_too_low_count']}"
            )
        lines.append("By forecast-error horizon:")
        for key, group in forecast_error_summary["groups"].get("by_horizon_bucket", {}).items():
            lines.append(
                f"  {key}: n={group['count']} avg_error_c={group['avg_error_c']} "
                f"avg_abs_error_c={group['avg_abs_error_c']} "
                f"too_high={group['forecast_too_high_count']} too_low={group['forecast_too_low_count']}"
            )
        lines.append("By forecast-error sigma:")
        for key, group in forecast_error_summary["groups"].get("by_sigma_bucket", {}).items():
            lines.append(
                f"  {key}: n={group['count']} avg_error_c={group['avg_error_c']} "
                f"avg_abs_error_c={group['avg_abs_error_c']} "
                f"too_high={group['forecast_too_high_count']} too_low={group['forecast_too_low_count']}"
            )
    lines += [
        "",
        "By comparator:",
    ]
    for key, group in summary["groups"]["by_comparator"].items():
        lines.append(
            f"  {key}: n={group['count']} avg_p={group['avg_model_prob']} "
            f"win_rate={group['recorded_win_rate']} pnl={group['pnl_usd']}"
        )
    lines.append("By side:")
    for key, group in summary["groups"]["by_side"].items():
        lines.append(
            f"  {key}: n={group['count']} avg_p={group['avg_model_prob']} "
            f"win_rate={group['recorded_win_rate']} pnl={group['pnl_usd']}"
        )
    return "\n".join(lines)
