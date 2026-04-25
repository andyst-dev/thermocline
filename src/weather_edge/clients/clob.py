from __future__ import annotations

from typing import Any

from ..config import Settings
from ..http import get_json

_BOOK_CACHE: dict[str, dict[str, Any]] = {}


def fetch_book(settings: Settings, token_id: str) -> dict[str, Any]:
    if token_id in _BOOK_CACHE:
        return _BOOK_CACHE[token_id]
    try:
        payload = get_json(f"{settings.polymarket_clob_url}/book", params={"token_id": token_id}, timeout=20)
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    _BOOK_CACHE[token_id] = payload
    return payload


def _price(level: dict[str, Any]) -> float | None:
    try:
        return float(level["price"])
    except (KeyError, TypeError, ValueError):
        return None


def _has_min_size(level: dict[str, Any], min_size: float = 10.0) -> bool:
    try:
        return float(level.get("size", 0.0)) >= min_size
    except (TypeError, ValueError):
        return False


def _size(level: dict[str, Any]) -> float:
    try:
        return float(level.get("size", 0.0))
    except (TypeError, ValueError):
        return 0.0


def best_bid_ask(settings: Settings, token_id: str) -> tuple[float | None, float | None]:
    bid, ask, _capacity = best_bid_ask_capacity(settings, token_id)
    return bid, ask


def best_bid_ask_capacity(settings: Settings, token_id: str, max_price: float | None = None) -> tuple[float | None, float | None, float | None]:
    book = fetch_book(settings, token_id)
    bids = [_price(level) for level in book.get("bids", []) if _has_min_size(level) and _price(level) is not None]
    ask_levels = [level for level in book.get("asks", []) if _has_min_size(level) and _price(level) is not None]
    asks = [_price(level) for level in ask_levels]
    best_ask = min(asks) if asks else None
    if best_ask is None:
        return (max(bids) if bids else None, None, None)
    limit = best_ask if max_price is None else max_price
    capacity = 0.0
    for level in ask_levels:
        price = _price(level)
        if price is not None and price <= limit:
            capacity += price * _size(level)
    # Buy liquidity is the cheapest ask; sell liquidity is the highest bid.
    return (max(bids) if bids else None, best_ask, capacity)
