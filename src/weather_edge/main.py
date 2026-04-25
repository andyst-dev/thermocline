from __future__ import annotations

import argparse
import json
from pathlib import Path

from .clients.polymarket import fetch_weather_markets
from .config import get_settings
from .db import connect, init_db, insert_forecast, insert_scan, upsert_markets
from .scanner import ScanSkip, filter_markets, scan_market


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

    results.sort(key=lambda r: (r.top_bucket_ev or -999), reverse=True)
    output = [
        {
            "market": r.question,
            "city": r.city,
            "date": r.target_date,
            "forecast_max_c": round(r.forecast_max_c, 2),
            "sigma_c": round(r.sigma_c, 2),
            "top_bucket": r.top_bucket_label,
            "top_bucket_ev": round(r.top_bucket_ev or 0.0, 4),
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
    elif args.command == "run-once":
        cmd_run_once()
    else:
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
