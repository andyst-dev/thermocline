from __future__ import annotations

import argparse
import json
from pathlib import Path

from .candidates import build_candidate
from .clients.polymarket import fetch_weather_markets
from .config import get_settings
from .db import close_paper_trade, connect, init_db, insert_forecast, insert_paper_trade, insert_scan, list_paper_trades, upsert_markets
from .scanner import ScanSkip, filter_markets, scan_market
from .settlement import settle_candidate


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
            candidates.append(build_candidate(market, result, forecast_meta))
    candidates.sort(key=lambda c: (c.verdict == "PASS", c.verdict == "PAPER", c.score), reverse=True)
    return candidates, skipped


def cmd_verify_candidates() -> None:
    settings = get_settings()
    candidates, skipped = _generate_candidates(settings)
    output = [candidate.as_dict() for candidate in candidates[: settings.report_limit]]
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


def cmd_paper_open(limit: int, size_usd: float, include_paper: bool = False) -> None:
    settings = get_settings()
    candidates, _skipped = _generate_candidates(settings)
    allowed = {"PASS", "PAPER"} if include_paper else {"PASS"}
    selected = [c.as_dict() for c in candidates if c.verdict in allowed and c.best_ask is not None and c.side][:limit]
    opened = []
    with connect(settings.db_path) as conn:
        existing = {(row["market_id"], row["side"]) for row in list_paper_trades(conn, "open")}
        for candidate in selected:
            key = (candidate["market_id"], candidate["side"])
            if key in existing:
                continue
            insert_paper_trade(conn, candidate=candidate, size_usd=size_usd, notes="auto paper-open from verified candidates")
            opened.append(candidate)
    report_path = settings.project_root / "reports" / "paper_opened.json"
    report_path.write_text(json.dumps({"opened": opened, "size_usd": size_usd}, indent=2), encoding="utf-8")
    print(json.dumps({"opened": opened, "size_usd": size_usd}, indent=2))
    print(f"Saved report: {report_path}")


def cmd_paper_settle() -> None:
    settings = get_settings()
    init_db(settings.db_path)
    settled = []
    pending = []
    with connect(settings.db_path) as conn:
        rows = list_paper_trades(conn, "open")
        for row in rows:
            candidate = json.loads(row["candidate_json"])
            result = settle_candidate(candidate, row["question"], row["side"])
            if not result.can_settle or result.outcome_price is None:
                pending.append({
                    "id": row["id"],
                    "question": row["question"],
                    "side": row["side"],
                    "observed_value_c": result.observed_value_c,
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
                "notes": result.notes,
            })
    report = {"settled": settled, "pending": pending, "summary": {"settled": len(settled), "pending": len(pending), "pnl_usd": round(sum(x["pnl_usd"] for x in settled), 4)}}
    report_path = settings.project_root / "reports" / "paper_settlement.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Saved report: {report_path}")


def cmd_paper_report() -> None:
    settings = get_settings()
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        rows = list_paper_trades(conn)
    trades = []
    total_at_risk = 0.0
    for row in rows:
        candidate = json.loads(row["candidate_json"])
        shares = candidate.get("paper_shares")
        total_at_risk += float(row["size_usd"])
        trades.append({
            "id": row["id"],
            "status": row["status"],
            "question": row["question"],
            "side": row["side"],
            "entry_price": row["entry_price"],
            "size_usd": row["size_usd"],
            "paper_shares": round(float(shares or 0.0), 2),
            "model_prob": row["model_prob"],
            "executable_ev": row["executable_ev"],
            "score": row["score"],
            "opened_at": row["opened_at"],
            "resolution_source": candidate.get("resolution_source"),
            "resolution_location": candidate.get("resolution_location"),
        })
    report = {"summary": {"trades": len(trades), "total_at_risk_usd": round(total_at_risk, 2)}, "trades": trades}
    report_path = settings.project_root / "reports" / "paper_trades.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Saved report: {report_path}")


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
    paper_open.add_argument("--size-usd", type=float, default=1.0)
    paper_open.add_argument("--include-paper", action="store_true")
    sub.add_parser("paper-report")
    sub.add_parser("paper-settle")
    sub.add_parser("run-once")
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
    elif args.command == "run-once":
        cmd_run_once()
    else:
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
