from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from datetime import date, timedelta

from .audit import write_json_gz
from .backtest import (
    DEFAULT_HORIZONS,
    HORIZON_BUCKETS,
    BacktestRecord,
    aggregate_sigma,
    recalibrate_sigma,
    run_backtest_for_city,
    write_backtest_report,
)
from .candidates import build_candidate, compute_kelly_size
from .clients.aviationweather import observed_extreme_c
from .clients.clob import simulate_buy_fill
from .clients.openmeteo import geocode_city
from .clients.polymarket import fetch_market_by_id, fetch_weather_markets
from .clients.weathercom import icao_from_wunderground_source, official_extreme_c
from .config import get_settings
from .db import close_paper_trade, connect, init_db, insert_backtest_record, insert_forecast, insert_paper_trade, insert_scan, list_paper_trades, upsert_markets
from .parsing import parse_temperature_contract
from .scanner import ScanSkip, filter_markets, scan_market, _effective_sigma_observed
from .settlement import settle_candidate
from .timezones import timezone_hint_for_icao


def cmd_init_db() -> None:
    settings = get_settings()
    init_db(settings.db_path)
    print(f"DB initialized: {settings.db_path}")


def cmd_fetch_markets() -> None:
    settings = get_settings()
    init_db(settings.db_path)
    markets = fetch_weather_markets(settings)
    with connect(settings.db_path) as conn:
        upsert_markets(conn, markets)
    print(f"Fetched {len(markets)} weather markets")


def cmd_scan() -> None:
    settings = get_settings()
    init_db(settings.db_path)
    markets = filter_markets(fetch_weather_markets(settings), settings.min_liquidity)
    results = []
    skipped: list[tuple[str, str]] = []
    with connect(settings.db_path) as conn:
        upsert_markets(conn, markets)
        for market in markets:
            try:
                result, forecast_meta = scan_market(settings, market)
            except ScanSkip as exc:
                skipped.append((market.slug, str(exc)))
                continue
            insert_forecast(
                conn,
                market_id=result.market_id,
                city=result.city,
                target_date=result.target_date,
                latitude=forecast_meta["context"]["latitude"],
                longitude=forecast_meta["context"]["longitude"],
                timezone_name=forecast_meta["context"]["timezone"],
                forecast_max_c=result.forecast_max_c,
                sigma_c=result.sigma_c,
                horizon_hours=result.horizon_hours,
                raw=forecast_meta,
            )
            insert_scan(conn, result)
            results.append(result)

    results.sort(key=lambda r: max((b.executable_ev for b in r.buckets if b.executable_ev is not None), default=-999), reverse=True)
    output = [
        {
            "market": r.question,
            "city": r.city,
            "date": r.target_date,
            "forecast_max_c": round(r.forecast_max_c, 2),
            "sigma_c": round(r.sigma_c, 2),
            "top_bucket": r.top_bucket_label,
            "top_bucket_ev": round(r.top_bucket_ev or 0.0, 4),
            "top_executable_ev": round(max((b.executable_ev for b in r.buckets if b.executable_ev is not None), default=0.0), 4),
            "top_best_ask": next((b.best_ask for b in r.buckets if b.label == r.top_bucket_label), None),
            "confidence": r.confidence,
            "liquidity": r.liquidity,
        }
        for r in results[: settings.report_limit]
    ]

    report_path = settings.project_root / "reports" / "latest_scan.json"
    report_path.write_text(json.dumps({"results": output, "skipped": skipped}, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))
    print(f"Saved report: {report_path}")
    if skipped:
        print(f"Skipped: {len(skipped)} markets")


def _generate_candidates(settings):
    init_db(settings.db_path)
    markets = filter_markets(fetch_weather_markets(settings), settings.min_liquidity)
    candidates = []
    skipped: list[tuple[str, str]] = []
    with connect(settings.db_path) as conn:
        upsert_markets(conn, markets)
        for market in markets:
            try:
                result, forecast_meta = scan_market(settings, market)
            except ScanSkip as exc:
                skipped.append((market.slug, str(exc)))
                continue
            insert_forecast(
                conn,
                market_id=result.market_id,
                city=result.city,
                target_date=result.target_date,
                latitude=forecast_meta["context"]["latitude"],
                longitude=forecast_meta["context"]["longitude"],
                timezone_name=forecast_meta["context"]["timezone"],
                forecast_max_c=result.forecast_max_c,
                sigma_c=result.sigma_c,
                horizon_hours=result.horizon_hours,
                raw=forecast_meta,
            )
            insert_scan(conn, result)
            candidates.append(build_candidate(market, result, forecast_meta, settings))
    candidates.sort(key=lambda c: (c.verdict == "PASS", c.verdict == "PAPER", c.score), reverse=True)
    return candidates, skipped


def cmd_verify_candidates() -> None:
    settings = get_settings()
    candidates, skipped = _generate_candidates(settings)
    output = []
    for candidate in candidates[: settings.report_limit]:
        candidate_dict = candidate.as_dict()
        # Verification reports are used for manual review. Keep a raw CLOB book
        # snapshot for every displayed candidate with a token, even when paper
        # opening is disabled, so future audits can reproduce the quoted fill.
        refreshed = _refresh_candidate_fill_for_open(settings, candidate_dict, size_usd=1.0)
        if refreshed is not None and refreshed.get("fill_avg_price") is not None:
            refreshed["recommended_size_usd"] = compute_kelly_size(
                model_prob=refreshed.get("model_prob"),
                price=refreshed.get("fill_avg_price"),
                kelly_fraction=settings.kelly_fraction,
                max_size_usd=settings.max_position_size_usd,
                min_size_usd=settings.min_position_size_usd,
                bankroll_usd=settings.kelly_bankroll_usd,
            )
        output.append(refreshed or candidate_dict)
    report = {
        "policy": {
            "meaning": "PASS is a candidate for manual verification, not permission to trade automatically.",
            "no_auto_trade": True,
            "min_executable_ev": 0.15,
            "max_preferred_ask": 0.10,
        },
        "candidates": output,
        "counts": {
            "pass": sum(1 for c in candidates if c.verdict == "PASS"),
            "paper": sum(1 for c in candidates if c.verdict == "PAPER"),
            "reject": sum(1 for c in candidates if c.verdict == "REJECT"),
            "skipped": len(skipped),
        },
        "skipped": skipped[:50],
    }
    report_path = settings.project_root / "reports" / "verified_candidates.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Saved report: {report_path}")


def _refresh_candidate_fill_for_open(settings, candidate: dict, size_usd: float) -> dict | None:
    token_id = candidate.get("token_id")
    model_prob = candidate.get("model_prob")
    if not token_id or model_prob is None:
        return None
    fill = simulate_buy_fill(settings, str(token_id), usd_size=size_usd)
    if not fill.filled or fill.avg_price is None or fill.cost_usd < size_usd * 0.999:
        return None
    path, digest = write_json_gz(
        settings.project_root,
        kind="books",
        name=str(token_id)[:16],
        payload=fill.book_payload,
        fetched_at=fill.book_fetched_at,
    )
    refreshed = dict(candidate)
    refreshed.update(
        {
            "best_bid": fill.best_bid,
            "best_ask": fill.best_ask,
            "ask_capacity_usd": fill.capacity_usd_at_best_ask,
            "fill_avg_price": fill.avg_price,
            "fill_shares": fill.shares,
            "fill_cost_usd": fill.cost_usd,
            "fill_levels_json": json.dumps(fill.levels_used),
            "book_fetched_at": fill.book_fetched_at,
            "book_snapshot_path": path,
            "book_snapshot_hash": digest,
            "executable_ev": float(model_prob) - fill.avg_price,
        }
    )
    return refreshed


def cmd_paper_open(limit: int, size_usd: float | None = None, include_paper: bool = False) -> None:
    settings = get_settings()
    candidates, _skipped = _generate_candidates(settings)
    allowed = {"PASS", "PAPER"} if include_paper else {"PASS"}
    selected = [c.as_dict() for c in candidates if c.verdict in allowed and c.best_ask is not None and c.side]
    opened = []
    with connect(settings.db_path) as conn:
        # Do not open multiple sides/buckets for the same market in paper. They are
        # correlated/contradictory and can inflate apparent edge.
        all_rows = list_paper_trades(conn)
        existing = {row["market_id"] for row in all_rows}
        open_count = sum(1 for row in all_rows if row["status"] == "open")
        if open_count >= settings.max_open_positions:
            selected = []
        for candidate in selected:
            if open_count + len(opened) >= settings.max_open_positions:
                break
            if len(opened) >= limit:
                break
            key = candidate["market_id"]
            if key in existing:
                continue
            if size_usd is None:
                # Kelly dynamic sizing
                refresh_1 = _refresh_candidate_fill_for_open(settings, candidate, size_usd=1.0)
                if refresh_1 is None:
                    continue
                price_for_kelly = refresh_1.get("fill_avg_price") if refresh_1.get("fill_avg_price") is not None else refresh_1.get("best_ask")
                model_prob = refresh_1.get("model_prob")
                kelly_size = compute_kelly_size(
                    model_prob=model_prob,
                    price=price_for_kelly,
                    kelly_fraction=settings.kelly_fraction,
                    max_size_usd=settings.max_position_size_usd,
                    min_size_usd=settings.min_position_size_usd,
                    bankroll_usd=settings.kelly_bankroll_usd,
                )
                ask_capacity_usd = refresh_1.get("ask_capacity_usd")
                if ask_capacity_usd is not None and ask_capacity_usd < settings.min_position_size_usd:
                    continue
                if ask_capacity_usd is not None:
                    kelly_size = min(kelly_size, float(ask_capacity_usd))
                # Re-run refresh with the actual kelly size
                refreshed = _refresh_candidate_fill_for_open(settings, refresh_1, size_usd=kelly_size)
                if refreshed is None:
                    continue
                insert_paper_trade(conn, candidate=refreshed, size_usd=kelly_size, notes="auto paper-open from verified candidates")
                opened.append(refreshed)
                existing.add(key)
            else:
                refreshed = _refresh_candidate_fill_for_open(settings, candidate, size_usd)
                if refreshed is None:
                    continue
                insert_paper_trade(conn, candidate=refreshed, size_usd=size_usd, notes="auto paper-open from verified candidates")
                opened.append(refreshed)
                existing.add(key)
    report_path = settings.project_root / "reports" / "paper_opened.json"
    report_path.write_text(json.dumps({"opened": opened, "size_usd": size_usd}, indent=2), encoding="utf-8")
    print(json.dumps({"opened": opened, "size_usd": size_usd}, indent=2))
    print(f"Saved report: {report_path}")


def _gamma_official_settlement(settings, row, candidate: dict) -> dict | None:
    try:
        market = fetch_market_by_id(settings, str(row["market_id"]))
    except Exception as exc:
        print(f"WARNING: Gamma settlement fetch failed for market {row['market_id']}: {exc}", flush=True)
        market = None
    if market is None or not market.closed:
        return None
    side = str(row["side"])
    try:
        price_by_side = {str(label): float(price) for label, price in zip(market.outcomes, market.outcome_prices, strict=False)}
    except (TypeError, ValueError):
        return None
    if side not in price_by_side:
        return None
    outcome_price = price_by_side[side]
    if outcome_price < 0.99 and outcome_price > 0.01:
        return None
    shares = float(candidate.get("paper_shares") or 0.0)
    size_usd = float(row["size_usd"])
    entry_price = float(row["entry_price"])
    if shares <= 0 or size_usd <= 0 or entry_price <= 0:
        print(f"WARNING: invalid paper trade economics for trade {row['id']}", flush=True)
        return None
    pnl = shares * outcome_price - size_usd
    max_pnl = size_usd * (1.0 / entry_price - 1.0)
    if pnl < -size_usd - 1e-9 or pnl > max_pnl + 1e-9:
        print(f"WARNING: implausible paper PnL for trade {row['id']}: {pnl}", flush=True)
        return None
    return {
        "outcome_price": 1.0 if outcome_price >= 0.99 else 0.0,
        "pnl_usd": pnl,
        "notes": "settled from official Polymarket Gamma closed outcome",
        "authority": "official_gamma",
        "observation_count": 0,
    }


def _maybe_record_live_backtest(conn, row, candidate: dict, result) -> None:
    """Insert a BacktestRecord for resolved paper trades to feed sigma calibration.

    Skips silently for non-temperature markets, missing observed values, or when
    the (city, target_date, horizon) tuple is already recorded. Failures must
    never break the settle loop.
    """
    try:
        if result.observed_value_c is None:
            return
        is_temperature = (
            "observed_authority" in candidate
            or "forecast_max_c" in candidate
            or "forecast_value_c" in candidate
        )
        if not is_temperature:
            return
        target_date = candidate.get("target_date")
        if not isinstance(target_date, str) or len(target_date) != 10:
            return
        forecast_value = candidate.get("forecast_max_c")
        if forecast_value is None:
            forecast_value = candidate.get("forecast_value_c")
        if forecast_value is None:
            return
        horizon_hours = candidate.get("horizon_hours")
        if horizon_hours is None:
            return
        forecast_value = float(forecast_value)
        horizon_hours = float(horizon_hours)
        observed = float(result.observed_value_c)
        city = str(candidate.get("city") or "")
        latitude = float(candidate.get("latitude") or 0.0)
        longitude = float(candidate.get("longitude") or 0.0)

        existing = conn.execute(
            "SELECT 1 FROM backtest_records "
            "WHERE city = ? AND target_date = ? AND horizon_hours = ? LIMIT 1",
            (city, target_date, horizon_hours),
        ).fetchone()
        if existing:
            return

        opened_at = row["opened_at"]
        try:
            reference_date = str(opened_at).split("T", 1)[0]
        except Exception:
            reference_date = target_date

        metric = "highest"
        ctx = candidate.get("context")
        if isinstance(ctx, dict) and ctx.get("metric"):
            metric = str(ctx["metric"])
        elif candidate.get("metric"):
            metric = str(candidate["metric"])

        record = BacktestRecord(
            city=city,
            latitude=latitude,
            longitude=longitude,
            target_date=target_date,
            reference_date=reference_date,
            horizon_hours=horizon_hours,
            forecast_max_c=forecast_value,
            observed_max_c=observed,
            residual_c=forecast_value - observed,
            metric=metric,
            model_source="live_scanner",
            fetched_at=str(opened_at),
        )
        insert_backtest_record(conn, record)
    except Exception as exc:
        print(f"WARNING: failed to record live backtest for trade {row['id']}: {exc}", flush=True)


def cmd_paper_settle() -> None:
    settings = get_settings()
    init_db(settings.db_path)
    settled = []
    pending = []
    with connect(settings.db_path) as conn:
        rows = list_paper_trades(conn, "open")
        for row in rows:
            candidate = json.loads(row["candidate_json"])
            gamma_result = _gamma_official_settlement(settings, row, candidate)
            if gamma_result is not None:
                close_paper_trade(
                    conn,
                    trade_id=int(row["id"]),
                    exit_price=gamma_result["outcome_price"],
                    pnl_usd=gamma_result["pnl_usd"],
                    notes=gamma_result["notes"],
                )
                settled.append({
                    "id": row["id"],
                    "question": row["question"],
                    "side": row["side"],
                    "entry_price": row["entry_price"],
                    "exit_price": gamma_result["outcome_price"],
                    "size_usd": row["size_usd"],
                    "paper_shares": round(float(candidate.get("paper_shares") or 0.0), 2),
                    "pnl_usd": round(gamma_result["pnl_usd"], 4),
                    "observed_value_c": None,
                    "authority": gamma_result["authority"],
                    "observation_count": gamma_result["observation_count"],
                    "notes": gamma_result["notes"],
                })
                continue
            result = settle_candidate(candidate, row["question"], row["side"])
            if not result.can_settle or result.outcome_price is None:
                pending.append({
                    "id": row["id"],
                    "question": row["question"],
                    "side": row["side"],
                    "observed_value_c": result.observed_value_c,
                    "authority": result.authority,
                    "observation_count": result.observation_count,
                    "notes": result.notes,
                })
                continue
            shares = float(candidate.get("paper_shares") or 0.0)
            exit_value = shares * result.outcome_price
            pnl = exit_value - float(row["size_usd"])
            close_paper_trade(
                conn,
                trade_id=int(row["id"]),
                exit_price=result.outcome_price,
                pnl_usd=pnl,
                notes=result.notes,
            )
            _maybe_record_live_backtest(conn, row, candidate, result)
            settled.append({
                "id": row["id"],
                "question": row["question"],
                "side": row["side"],
                "entry_price": row["entry_price"],
                "exit_price": result.outcome_price,
                "size_usd": row["size_usd"],
                "paper_shares": round(shares, 2),
                "pnl_usd": round(pnl, 4),
                "observed_value_c": result.observed_value_c,
                "authority": result.authority,
                "observation_count": result.observation_count,
                "notes": result.notes,
            })
    report = {"settled": settled, "pending": pending, "summary": {"settled": len(settled), "pending": len(pending), "pnl_usd": round(sum(x["pnl_usd"] for x in settled), 4)}}
    report_path = settings.project_root / "reports" / "paper_settlement.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Saved report: {report_path}")


def _gamma_price_for_side(settings, row) -> tuple[bool, float | None, str]:
    try:
        market = fetch_market_by_id(settings, str(row["market_id"]))
    except Exception as exc:
        return False, None, f"gamma_fetch_failed: {exc}"
    if market is None:
        return False, None, "gamma_missing"
    if not market.closed:
        return False, None, "gamma_open"
    try:
        price_by_side = {str(label): float(price) for label, price in zip(market.outcomes, market.outcome_prices, strict=False)}
    except (TypeError, ValueError):
        return True, None, "gamma_bad_prices"
    return True, price_by_side.get(str(row["side"])), "gamma_closed"


def cmd_reconcile_sources() -> None:
    settings = get_settings()
    init_db(settings.db_path)
    rows_out = []
    with connect(settings.db_path) as conn:
        rows = list_paper_trades(conn)
    for row in rows:
        if row["status"] == "duplicate":
            continue
        candidate = json.loads(row["candidate_json"])
        contract = parse_temperature_contract(row["question"])
        source = candidate.get("resolution_source")
        icao = candidate.get("resolution_location") or icao_from_wunderground_source(source)
        if isinstance(icao, str) and len(icao) != 4:
            icao = icao_from_wunderground_source(source)
        gamma_closed, gamma_price, gamma_note = _gamma_price_for_side(settings, row)
        official_value = None
        official_count = 0
        official_note = "unsupported_contract_or_source"
        metar_value = None
        metar_count = 0
        if contract:
            official_value, official_count, official_note = official_extreme_c(source, contract.target_date, contract.metric)
            if isinstance(icao, str) and len(icao) == 4:
                metar_value, metar_count = observed_extreme_c(icao, contract.target_date, timezone_hint_for_icao(icao), contract.metric)
        diff = None
        if official_value is not None and metar_value is not None:
            diff = round(float(official_value) - float(metar_value), 3)
        flags = []
        if gamma_closed:
            flags.append("gamma_official")
        if official_value is None:
            flags.append("official_source_missing")
        if diff is not None and abs(diff) > 1.0:
            flags.append("source_metar_diff_gt_1c")
        if candidate.get("fill_avg_price") is None or candidate.get("fill_cost_usd") is None:
            flags.append("legacy_no_simulated_fill")
        rows_out.append({
            "id": row["id"],
            "status": row["status"],
            "question": row["question"],
            "side": row["side"],
            "entry_price": row["entry_price"],
            "pnl_usd": row["pnl_usd"],
            "gamma_closed": gamma_closed,
            "gamma_side_price": gamma_price,
            "gamma_note": gamma_note,
            "resolution_source": source,
            "resolution_location": icao,
            "official_value_c": official_value,
            "official_count": official_count,
            "official_note": official_note,
            "metar_value_c": metar_value,
            "metar_count": metar_count,
            "official_minus_metar_c": diff,
            "flags": flags,
        })
    summary = {
        "rows": len(rows_out),
        "gamma_closed": sum(1 for r in rows_out if r["gamma_closed"]),
        "official_source_missing": sum(1 for r in rows_out if "official_source_missing" in r["flags"]),
        "source_metar_diff_gt_1c": sum(1 for r in rows_out if "source_metar_diff_gt_1c" in r["flags"]),
        "legacy_no_simulated_fill": sum(1 for r in rows_out if "legacy_no_simulated_fill" in r["flags"]),
    }
    report = {"summary": summary, "rows": rows_out}
    json_path = settings.project_root / "reports" / "source_reconciliation.json"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_lines = [
        "# Source reconciliation",
        "",
        f"Rows: {summary['rows']}",
        f"Gamma closed: {summary['gamma_closed']}",
        f"Official source missing: {summary['official_source_missing']}",
        f"Official/METAR diff >1°C: {summary['source_metar_diff_gt_1c']}",
        f"Legacy no simulated fill: {summary['legacy_no_simulated_fill']}",
        "",
        "## Flagged rows",
    ]
    for r in rows_out:
        if r["flags"]:
            md_lines.append(f"- #{r['id']} {r['side']} — {r['question']} — flags={','.join(r['flags'])} — official={r['official_value_c']} metar={r['metar_value_c']} gamma={r['gamma_side_price']}")
    md_path = settings.project_root / "reports" / "source_reconciliation.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Saved report: {json_path}")
    print(f"Saved report: {md_path}")


def cmd_paper_report() -> None:
    settings = get_settings()
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        rows = list_paper_trades(conn)
    active_rows = [row for row in rows if row["status"] != "duplicate"]
    duplicate_rows = [row for row in rows if row["status"] == "duplicate"]
    trades = []
    total_at_risk = 0.0
    for row in active_rows:
        candidate = json.loads(row["candidate_json"])
        shares = candidate.get("paper_shares")
        total_at_risk += float(row["size_usd"])
        trades.append({
            "id": row["id"],
            "status": row["status"],
            "question": row["question"],
            "side": row["side"],
            "entry_price": row["entry_price"],
            "fill_avg_price": candidate.get("fill_avg_price"),
            "fill_cost_usd": candidate.get("fill_cost_usd"),
            "size_usd": row["size_usd"],
            "paper_shares": round(float(shares or 0.0), 2),
            "model_prob": row["model_prob"],
            "executable_ev": row["executable_ev"],
            "score": row["score"],
            "opened_at": row["opened_at"],
            "resolution_source": candidate.get("resolution_source"),
            "resolution_location": candidate.get("resolution_location"),
        })
    closed_rows = [row for row in active_rows if row["status"] == "closed"]
    filled_closed_rows = []
    unfilled_closed_rows = []
    for row in closed_rows:
        candidate = json.loads(row["candidate_json"])
        if candidate.get("fill_avg_price") is None or candidate.get("fill_cost_usd") is None:
            unfilled_closed_rows.append(row)
        else:
            filled_closed_rows.append(row)
    closed_pnl = sum(float(row["pnl_usd"] or 0.0) for row in closed_rows)
    closed_pnl_filled = sum(float(row["pnl_usd"] or 0.0) for row in filled_closed_rows)
    report = {
        "summary": {
            "trades": len(trades),
            "closed": len(closed_rows),
            "open": sum(1 for row in active_rows if row["status"] == "open"),
            "duplicates_excluded": len(duplicate_rows),
            "closed_with_simulated_fill": len(filled_closed_rows),
            "closed_without_simulated_fill": len(unfilled_closed_rows),
            "total_at_risk_usd": round(total_at_risk, 2),
            "closed_pnl_usd": round(closed_pnl, 4),
            "closed_pnl_with_simulated_fill_usd": round(closed_pnl_filled, 4),
            "pnl_note": "closed_pnl_usd includes legacy paper trades without simulated fills; use closed_pnl_with_simulated_fill_usd for cleaner post-fill accounting",
        },
        "trades": trades,
    }
    report_path = settings.project_root / "reports" / "paper_trades.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Saved report: {report_path}")


def cmd_calibration_snapshot() -> None:
    """Write a calibration snapshot for model validation.

    Collects per-candidate metrics (bucket width, sigma, model prob, verdict)
    and aggregate stats used to validate sigma calibration and gate logic.
    This runs on every paper-cycle so we accumulate empirical data even when
    paper opening is disabled.
    """
    from .parsing import bucket_probability

    settings = get_settings()
    candidates, skipped = _generate_candidates(settings)
    sigma_eff = _effective_sigma_observed()

    records = []
    narrow_rejects = []
    for c in candidates:
        d = c.as_dict()
        records.append({
            "market_id": d["market_id"],
            "slug": d["slug"],
            "verdict": d["verdict"],
            "score": d["score"],
            "bucket_width_c": d.get("bucket_width_c"),
            "sigma_c": d["sigma_c"],
            "model_prob": d["model_prob"],
            "executable_ev": d["executable_ev"],
            "confidence": d["confidence"],
            "observed_metar_count": d["observed_metar_count"],
            "observed_authority": d["observed_authority"],
            "resolution_location": d["resolution_location"],
            "horizon_hours": d["horizon_hours"],
            "liquidity": d["liquidity"],
            "reason": d["reason"],
        })
        if d["verdict"] == "REJECT" and "exact/narrow temperature bucket" in d.get("reason", ""):
            narrow_rejects.append(d["market_id"])

    # Aggregate stats by bucket-width bins
    def _bin_width(w):
        if w is None:
            return "unknown"
        if w <= 1.01:
            return "narrow_<=1.01"
        if w <= 2.0:
            return "medium_1.01_2.0"
        return "wide_>2.0"

    by_width = {}
    for r in records:
        b = _bin_width(r["bucket_width_c"])
        by_width.setdefault(b, {"count": 0, "pass": 0, "paper": 0, "reject": 0, "avg_score": 0.0, "avg_model_prob": 0.0})
        by_width[b]["count"] += 1
        by_width[b][r["verdict"].lower()] += 1
        by_width[b]["avg_score"] += r["score"] or 0.0
        by_width[b]["avg_model_prob"] += r["model_prob"] or 0.0

    for b in by_width:
        n = by_width[b]["count"]
        by_width[b]["avg_score"] = round(by_width[b]["avg_score"] / n, 3) if n else 0.0
        by_width[b]["avg_model_prob"] = round(by_width[b]["avg_model_prob"] / n, 4) if n else 0.0

    report = {
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "sigma_eff_observed": round(sigma_eff, 4),
        "total_scanned": len(records),
        "skipped": len(skipped),
        "verdict_counts": {
            "pass": sum(1 for r in records if r["verdict"] == "PASS"),
            "paper": sum(1 for r in records if r["verdict"] == "PAPER"),
            "reject": sum(1 for r in records if r["verdict"] == "REJECT"),
        },
        "narrow_bucket_hard_rejects": len(narrow_rejects),
        "by_bucket_width": by_width,
        "top_5_pass": [r for r in records if r["verdict"] == "PASS"][:5],
        "top_5_paper": [r for r in records if r["verdict"] == "PAPER"][:5],
    }
    report_path = settings.project_root / "reports" / "calibration_snapshot.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Calibration snapshot saved: {report_path}")


def cmd_paper_cycle() -> None:
    # Conservative unattended loop: refresh candidates, optionally open a few
    # PASS-only paper positions, settle irreversible/open positions, then write
    # the current report. Opening can be paused while source/model reliability is
    # under investigation without stopping settlement/reporting.
    cmd_verify_candidates()
    cmd_calibration_snapshot()
    if os.environ.get("WEATHER_EDGE_DISABLE_PAPER_OPEN") == "1":
        print("Paper opening disabled by WEATHER_EDGE_DISABLE_PAPER_OPEN=1")
    else:
        cmd_paper_open(limit=5, size_usd=None, include_paper=False)
    cmd_paper_settle()
    cmd_paper_report()


DEFAULT_BACKTEST_CITIES = [
    "New York",
    "London",
    "Tokyo",
    "Seoul",
    "Sao Paulo",
    "Mexico City",
    "Buenos Aires",
    "Dallas",
    "Miami",
    "Moscow",
    "Wellington",
]


def cmd_backtest(
    cities: list[str] | None,
    start_date: str,
    end_date: str,
    horizons: list[int] | None = None,
    metric: str = "highest",
) -> None:
    settings = get_settings()
    init_db(settings.db_path)
    horizons = horizons or list(DEFAULT_HORIZONS)
    cities = cities or list(DEFAULT_BACKTEST_CITIES)

    try:
        start_dt = date.fromisoformat(start_date)
    except ValueError as exc:
        raise SystemExit(f"Invalid --start-date: {exc}") from exc
    today = date.today()
    if (today - start_dt) > timedelta(days=90):
        print(
            f"WARNING: start-date {start_date} is more than ~3 months back; "
            "Open-Meteo previous-runs API has limited depth and may return no data.",
            flush=True,
        )

    all_records = []
    per_city_counts: dict[str, int] = {}
    for city in cities:
        try:
            geo = geocode_city(settings, city)
        except Exception as exc:
            print(f"WARNING: geocoding failed for {city}: {exc}", flush=True)
            continue
        if not geo:
            print(f"WARNING: no geocode result for {city}", flush=True)
            continue
        latitude = float(geo["latitude"])
        longitude = float(geo["longitude"])
        timezone_name = str(geo.get("timezone") or "auto")
        print(f"Backtesting {city} ({latitude:.3f},{longitude:.3f}) {start_date}..{end_date}", flush=True)
        records = run_backtest_for_city(
            settings,
            city=city,
            latitude=latitude,
            longitude=longitude,
            timezone=timezone_name,
            start_date=start_date,
            end_date=end_date,
            metric=metric,
            horizons=horizons,
        )
        per_city_counts[city] = len(records)
        all_records.extend(records)

    aggregates = aggregate_sigma(all_records)

    with connect(settings.db_path) as conn:
        for record in all_records:
            insert_backtest_record(conn, record)

    json_path, md_path = write_backtest_report(settings.project_root, all_records, aggregates)

    print("")
    print(f"Total records: {aggregates['total_records']}")
    print("Per-city counts:")
    for city, count in per_city_counts.items():
        print(f"  {city}: {count}")
    print("")
    print("Empirical sigma by horizon bucket:")
    for label, _, _ in HORIZON_BUCKETS:
        stats = aggregates["by_horizon"][label]
        print(
            f"  {label}: count={stats['count']} sigma={stats['sigma_c']} "
            f"mean={stats['mean_residual_c']} mae={stats['median_abs_error_c']}"
        )
    print("")
    print(f"Saved report: {json_path}")
    print(f"Saved report: {md_path}")


def cmd_run_once() -> None:
    cmd_init_db()
    cmd_fetch_markets()
    cmd_scan()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="weather-edge")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    sub.add_parser("fetch-markets")
    sub.add_parser("scan")
    sub.add_parser("verify-candidates")
    paper_open = sub.add_parser("paper-open")
    paper_open.add_argument("--limit", type=int, default=5)
    paper_open.add_argument("--size-usd", type=float, default=None, help="Flat position size in USD. Omit to enable Kelly dynamic sizing.")
    paper_open.add_argument("--include-paper", action="store_true")
    sub.add_parser("paper-report")
    sub.add_parser("paper-settle")
    sub.add_parser("reconcile-sources")
    sub.add_parser("paper-cycle")
    sub.add_parser("run-once")
    backtest = sub.add_parser("backtest")
    backtest.add_argument("--cities", type=str, default=None, help="Comma-separated city list")
    backtest.add_argument("--start-date", type=str, required=True, help="YYYY-MM-DD")
    backtest.add_argument("--end-date", type=str, required=True, help="YYYY-MM-DD")
    backtest.add_argument("--horizons", type=str, default="24,48,72,96,120", help="Comma-separated hours")
    backtest.add_argument("--metric", type=str, default="highest", choices=["highest", "lowest"])
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "init-db":
        cmd_init_db()
    elif args.command == "fetch-markets":
        cmd_fetch_markets()
    elif args.command == "scan":
        cmd_scan()
    elif args.command == "verify-candidates":
        cmd_verify_candidates()
    elif args.command == "paper-open":
        cmd_paper_open(limit=args.limit, size_usd=args.size_usd, include_paper=args.include_paper)
    elif args.command == "paper-report":
        cmd_paper_report()
    elif args.command == "paper-settle":
        cmd_paper_settle()
    elif args.command == "reconcile-sources":
        cmd_reconcile_sources()
    elif args.command == "paper-cycle":
        cmd_paper_cycle()
    elif args.command == "run-once":
        cmd_run_once()
    elif args.command == "backtest":
        cities = [c.strip() for c in args.cities.split(",") if c.strip()] if args.cities else None
        horizons = [int(h.strip()) for h in args.horizons.split(",") if h.strip()] if args.horizons else None
        cmd_backtest(
            cities=cities,
            start_date=args.start_date,
            end_date=args.end_date,
            horizons=horizons,
            metric=args.metric,
        )
    else:
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
