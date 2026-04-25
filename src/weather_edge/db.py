from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Iterator

from .models import ScanResult, WeatherMarket

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS markets (
    market_id TEXT PRIMARY KEY,
    slug TEXT,
    question TEXT,
    end_date TEXT,
    active INTEGER NOT NULL,
    closed INTEGER NOT NULL,
    liquidity REAL NOT NULL DEFAULT 0,
    volume REAL NOT NULL DEFAULT 0,
    outcomes_json TEXT NOT NULL,
    outcome_prices_json TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS forecasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    timezone TEXT NOT NULL,
    forecast_max_c REAL NOT NULL,
    sigma_c REAL NOT NULL,
    horizon_hours REAL NOT NULL,
    raw_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    question TEXT NOT NULL,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    forecast_max_c REAL NOT NULL,
    sigma_c REAL NOT NULL,
    horizon_hours REAL NOT NULL,
    liquidity REAL NOT NULL,
    top_bucket_label TEXT,
    top_bucket_ev REAL,
    confidence TEXT NOT NULL,
    buckets_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS book_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    side TEXT NOT NULL,
    token_id TEXT,
    fetched_at TEXT,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_trade_id INTEGER NOT NULL,
    requested_usd REAL NOT NULL,
    avg_price REAL,
    shares REAL,
    cost_usd REAL,
    levels_used_json TEXT,
    book_snapshot_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    question TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    size_usd REAL NOT NULL,
    model_prob REAL,
    executable_ev REAL,
    score REAL,
    verdict TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    exit_price REAL,
    pnl_usd REAL,
    notes TEXT,
    candidate_json TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    UNIQUE(market_id, side, opened_at)
);

CREATE INDEX IF NOT EXISTS idx_scans_market_created ON scans(market_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_forecasts_market_created ON forecasts(market_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades(status, opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_book_snapshots_market ON book_snapshots(market_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_fills_trade ON fills(paper_trade_id);
"""


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


def upsert_markets(conn: sqlite3.Connection, markets: list[WeatherMarket]) -> None:
    now = datetime.now(dt_timezone.utc).isoformat()
    conn.executemany(
        """
        INSERT INTO markets(
            market_id, slug, question, end_date, active, closed,
            liquidity, volume, outcomes_json, outcome_prices_json, raw_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(market_id) DO UPDATE SET
            slug=excluded.slug,
            question=excluded.question,
            end_date=excluded.end_date,
            active=excluded.active,
            closed=excluded.closed,
            liquidity=excluded.liquidity,
            volume=excluded.volume,
            outcomes_json=excluded.outcomes_json,
            outcome_prices_json=excluded.outcome_prices_json,
            raw_json=excluded.raw_json,
            updated_at=excluded.updated_at
        """,
        [
            (
                m.market_id,
                m.slug,
                m.question,
                m.end_date.isoformat() if m.end_date else None,
                int(m.active),
                int(m.closed),
                m.liquidity,
                m.volume,
                json.dumps(m.outcomes),
                json.dumps(m.outcome_prices),
                json.dumps(m.raw),
                now,
            )
            for m in markets
        ],
    )


def insert_forecast(
    conn: sqlite3.Connection,
    *,
    market_id: str,
    city: str,
    target_date: str,
    latitude: float,
    longitude: float,
    timezone_name: str,
    forecast_max_c: float,
    sigma_c: float,
    horizon_hours: float,
    raw: dict,
) -> None:
    conn.execute(
        """
        INSERT INTO forecasts(
            market_id, city, target_date, latitude, longitude, timezone,
            forecast_max_c, sigma_c, horizon_hours, raw_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            market_id,
            city,
            target_date,
            latitude,
            longitude,
            timezone_name,
            forecast_max_c,
            sigma_c,
            horizon_hours,
            json.dumps(raw),
            datetime.now(dt_timezone.utc).isoformat(),
        ),
    )


def _insert_book_snapshot(conn: sqlite3.Connection, candidate: dict) -> int | None:
    path = candidate.get("book_snapshot_path")
    digest = candidate.get("book_snapshot_hash")
    if not path or not digest:
        return None
    cur = conn.execute(
        """
        INSERT INTO book_snapshots(market_id, side, token_id, fetched_at, path, sha256, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate["market_id"],
            candidate["side"],
            candidate.get("token_id"),
            candidate.get("book_fetched_at"),
            path,
            digest,
            datetime.now(dt_timezone.utc).isoformat(),
        ),
    )
    return int(cur.lastrowid)


def insert_paper_trade(
    conn: sqlite3.Connection,
    *,
    candidate: dict,
    size_usd: float,
    notes: str = "",
) -> None:
    now = datetime.now(dt_timezone.utc).isoformat()
    entry_price = float(candidate.get("fill_avg_price") or candidate["best_ask"])
    shares = size_usd / entry_price if entry_price > 0 else 0.0
    enriched = dict(candidate)
    enriched["paper_shares"] = shares
    book_snapshot_id = _insert_book_snapshot(conn, candidate)
    cur = conn.execute(
        """
        INSERT INTO paper_trades(
            market_id, slug, question, side, entry_price, size_usd,
            model_prob, executable_ev, score, verdict, status,
            notes, candidate_json, opened_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)
        """,
        (
            candidate["market_id"],
            candidate["slug"],
            candidate["question"],
            candidate["side"],
            entry_price,
            size_usd,
            candidate.get("model_prob"),
            candidate.get("executable_ev"),
            candidate.get("score"),
            candidate.get("verdict"),
            notes,
            json.dumps(enriched),
            now,
        ),
    )
    paper_trade_id = int(cur.lastrowid)
    conn.execute(
        """
        INSERT INTO fills(
            paper_trade_id, requested_usd, avg_price, shares, cost_usd,
            levels_used_json, book_snapshot_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            paper_trade_id,
            size_usd,
            candidate.get("fill_avg_price"),
            candidate.get("fill_shares"),
            candidate.get("fill_cost_usd"),
            candidate.get("fill_levels_json"),
            book_snapshot_id,
            now,
        ),
    )


def list_paper_trades(conn: sqlite3.Connection, status: str | None = None) -> list[sqlite3.Row]:
    if status:
        return list(conn.execute("SELECT * FROM paper_trades WHERE status = ? ORDER BY opened_at DESC", (status,)))
    return list(conn.execute("SELECT * FROM paper_trades ORDER BY opened_at DESC"))


def close_paper_trade(
    conn: sqlite3.Connection,
    *,
    trade_id: int,
    exit_price: float,
    pnl_usd: float,
    notes: str,
) -> None:
    conn.execute(
        """
        UPDATE paper_trades
        SET status = 'closed', exit_price = ?, pnl_usd = ?, notes = ?, closed_at = ?
        WHERE id = ? AND status = 'open'
        """,
        (exit_price, pnl_usd, notes, datetime.now(dt_timezone.utc).isoformat(), trade_id),
    )


def insert_scan(conn: sqlite3.Connection, scan: ScanResult) -> None:
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
            scan.market_id,
            scan.slug,
            scan.question,
            scan.city,
            scan.target_date,
            scan.forecast_max_c,
            scan.sigma_c,
            scan.horizon_hours,
            scan.liquidity,
            scan.top_bucket_label,
            scan.top_bucket_ev,
            scan.confidence,
            json.dumps([bucket.__dict__ for bucket in scan.buckets]),
            datetime.now(dt_timezone.utc).isoformat(),
        ),
    )
