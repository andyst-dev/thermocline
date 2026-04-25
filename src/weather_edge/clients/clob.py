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


def best_bid_ask(settings: Settings, token_id: str) -> tuple[float | None, float | None]:
    book = fetch_book(settings, token_id)
    bids = [_price(level) for level in book.get("bids", []) if _has_min_size(level) and _price(level) is not None]
    asks = [_price(level) for level in book.get("asks", []) if _has_min_size(level) and _price(level) is not None]
    # Buy liquidity is the cheapest ask; sell liquidity is the highest bid.
    return (max(bids) if bids else None, min(asks) if asks else None)
