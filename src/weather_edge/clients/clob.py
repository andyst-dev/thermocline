from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..config import Settings
from ..http import get_json

_BOOK_CACHE: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True)
class FillSimulation:
    best_bid: float | None
    best_ask: float | None
    avg_price: float | None
    shares: float
    cost_usd: float
    requested_usd: float
    filled: bool
    capacity_usd_at_best_ask: float | None
    levels_used: list[dict[str, float]]
    book_fetched_at: str
    book_payload: dict[str, Any]


def fetch_book(settings: Settings, token_id: str, *, use_cache: bool = False) -> dict[str, Any]:
    if use_cache and token_id in _BOOK_CACHE:
        return _BOOK_CACHE[token_id]
    try:
        payload = get_json(f"{settings.polymarket_clob_url}/book", params={"token_id": token_id}, timeout=20)
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload["_fetched_at"] = datetime.now(timezone.utc).isoformat()
    _BOOK_CACHE[token_id] = payload
    return payload


def _price(level: dict[str, Any]) -> float | None:
    try:
        return float(level["price"])
    except (KeyError, TypeError, ValueError):
        return None


def _size(level: dict[str, Any]) -> float:
    try:
        return float(level.get("size", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _has_min_size(level: dict[str, Any], min_size: float = 10.0) -> bool:
    return _size(level) >= min_size


def _book_levels(book: dict[str, Any], side: str) -> list[dict[str, Any]]:
    levels = [level for level in book.get(side, []) if _has_min_size(level) and _price(level) is not None]
    reverse = side == "bids"
    return sorted(levels, key=lambda level: float(level["price"]), reverse=reverse)


def best_bid_ask(settings: Settings, token_id: str) -> tuple[float | None, float | None]:
    sim = simulate_buy_fill(settings, token_id, usd_size=1.0)
    return sim.best_bid, sim.best_ask


def best_bid_ask_capacity(settings: Settings, token_id: str, max_price: float | None = None) -> tuple[float | None, float | None, float | None]:
    sim = simulate_buy_fill(settings, token_id, usd_size=1.0)
    return sim.best_bid, sim.best_ask, sim.capacity_usd_at_best_ask


def simulate_buy_fill(settings: Settings, token_id: str, usd_size: float = 1.0, *, max_avg_price: float = 0.10) -> FillSimulation:
    book = fetch_book(settings, token_id)
    bid_levels = _book_levels(book, "bids")
    ask_levels = _book_levels(book, "asks")
    bids = [_price(level) for level in bid_levels if _price(level) is not None]
    asks = [_price(level) for level in ask_levels if _price(level) is not None]
    best_bid = max(bids) if bids else None
    best_ask = min(asks) if asks else None
    fetched_at = str(book.get("_fetched_at") or datetime.now(timezone.utc).isoformat())
    if best_ask is None:
        return FillSimulation(best_bid, None, None, 0.0, 0.0, usd_size, False, None, [], fetched_at, book)

    capacity_at_best = sum(_size(level) * float(level["price"]) for level in ask_levels if float(level["price"]) <= best_ask)
    remaining = usd_size
    shares = 0.0
    cost = 0.0
    levels_used: list[dict[str, float]] = []
    for level in ask_levels:
        price = float(level["price"])
        size = _size(level)
        if price <= 0:
            continue
        max_level_cost = price * size
        take_cost = min(remaining, max_level_cost)
        take_shares = take_cost / price
        if take_cost > 0:
            levels_used.append({"price": price, "shares": take_shares, "cost_usd": take_cost})
            shares += take_shares
            cost += take_cost
            remaining -= take_cost
        if remaining <= 1e-9:
            break
    avg = cost / shares if shares > 0 else None
    filled = remaining <= 1e-9 and avg is not None and avg <= max_avg_price
    return FillSimulation(best_bid, best_ask, avg, shares, cost, usd_size, filled, capacity_at_best, levels_used, fetched_at, book)
