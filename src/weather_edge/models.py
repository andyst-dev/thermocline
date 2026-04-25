from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class WeatherMarket:
    market_id: str
    slug: str
    question: str
    end_date: datetime | None
    active: bool
    closed: bool
    liquidity: float
    volume: float
    outcomes: list[str]
    outcome_prices: list[float]
    raw: dict[str, Any]


@dataclass
class MarketContext:
    market: WeatherMarket
    city: str
    target_date: datetime
    latitude: float
    longitude: float
    timezone: str


@dataclass
class BucketProbability:
    label: str
    lower: float | None
    upper: float | None
    market_prob: float
    model_prob: float
    edge: float
    ev: float
    best_bid: float | None = None
    best_ask: float | None = None
    executable_ev: float | None = None
    ask_capacity_usd: float | None = None
    fill_avg_price: float | None = None
    fill_shares: float | None = None
    fill_cost_usd: float | None = None
    fill_levels_json: str | None = None
    book_fetched_at: str | None = None
    book_snapshot_path: str | None = None
    book_snapshot_hash: str | None = None
    token_id: str | None = None


@dataclass
class ScanResult:
    market_id: str
    slug: str
    question: str
    city: str
    target_date: str
    forecast_max_c: float
    sigma_c: float
    horizon_hours: float
    liquidity: float
    buckets: list[BucketProbability]
    top_bucket_label: str | None
    top_bucket_ev: float | None
    confidence: str
